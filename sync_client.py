"""
sync_client.py
Sincronización periódica (opcional) de conteos y estado de cámaras hacia un
servidor central en la nube.

Solo se activa si config.SYNC_SERVER_URL está configurado (variable de
entorno SYNC_SERVER_URL). Si no está configurado, el hilo no hace nada.

No envía video ni imágenes de forma continua: solo los registros de conteo
(JSON pequeño) y el estado de conexión/FPS de cada cámara. Las capturas de
imagen y los comandos de configuración de líneas se piden/encolan desde el
panel central y solo se atienden bajo demanda, en el ciclo normal de sync —
nunca hay una conexión aparte hacia el mini PC (está detrás de CGNAT/4G).
"""

import threading
import time

import cv2
import requests

import config
import database
from camera_manager import manager

# Ancho máximo y calidad JPEG de las capturas bajo demanda: solo sirven para
# validar que la cámara apunte a donde debe (o que detecte bien), no hace
# falta resolución/calidad alta, así que se mantienen livianas (unos
# 15-30 KB por captura).
SNAPSHOT_MAX_WIDTH = 640
SNAPSHOT_JPEG_QUALITY = 55


class SyncClient:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread = None

    def start(self):
        if not config.SYNC_SERVER_URL:
            print("[sync] SYNC_SERVER_URL no configurado, sincronización desactivada.")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="sync-client")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            try:
                self._sync_once()
            except Exception as exc:  # pragma: no cover
                print(f"[sync] Error sincronizando: {exc}")
            self._stop.wait(config.SYNC_INTERVAL_SECONDS)

    def _sync_once(self):
        pending = database.get_unsynced_counts(limit=500)
        cameras = database.get_cameras()
        name_to_id = {c["name"]: c["id"] for c in cameras}

        lines_payload = []
        for cam in cameras:
            for line in database.get_lines(cam["id"]):
                lines_payload.append({
                    "camera_name": cam["name"],
                    "line_id": line["id"],
                    "name": line["name"],
                    "movement": line["movement"],
                    "x1": line["x1"], "y1": line["y1"],
                    "x2": line["x2"], "y2": line["y2"],
                })

        payload = {
            "site_id": config.SYNC_SITE_ID,
            "cameras": [
                {
                    "name": s["name"],
                    "connected": s["connected"],
                    "fps": s["fps"],
                    "last_error": s["last_error"],
                }
                for s in manager.statuses()
            ],
            "counts": [
                {
                    "timestamp": c["timestamp"],
                    "camera_name": c.get("camera_name") or "",
                    "line_name": c.get("line_name") or "",
                    "movement": c.get("movement") or "",
                    "class_name": c["class_name"],
                    "direction": c["direction"],
                    "count": c["count"],
                }
                for c in pending
            ],
            "lines": lines_payload,
        }

        resp = requests.post(
            f"{config.SYNC_SERVER_URL}/api/ingest",
            json=payload,
            headers={"X-API-Key": config.SYNC_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json() or {}

        if pending:
            database.mark_synced([c["id"] for c in pending])
            print(f"[sync] {len(pending)} conteos sincronizados con el servidor.")

        # Capturas pendientes: el panel central marca una cámara cuando el
        # usuario pide "Cruda" o "Con detecciones"; solo entonces se sube
        # una imagen, para no gastar datos móviles con envíos periódicos.
        for req in data.get("snapshot_requests") or []:
            self._send_snapshot(req.get("camera_name"), req.get("type") or "raw")

        # Comandos de líneas pendientes (crear/editar/borrar), encolados
        # desde el panel central.
        for cmd in data.get("line_commands") or []:
            self._apply_line_command(cmd, name_to_id)

    def _send_snapshot(self, camera_name: str, snapshot_type: str = "raw"):
        stream = next((s for s in manager.streams.values() if s.name == camera_name), None)
        if stream is None:
            return
        frame = stream.get_frame(timeout=5.0) if snapshot_type == "annotated" else stream.get_raw_frame()
        if frame is None:
            return

        h, w = frame.shape[:2]
        if w > SNAPSHOT_MAX_WIDTH:
            new_h = int(round(h * (SNAPSHOT_MAX_WIDTH / float(w))))
            frame = cv2.resize(frame, (SNAPSHOT_MAX_WIDTH, new_h), interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, SNAPSHOT_JPEG_QUALITY])
        if not ok:
            return

        try:
            resp = requests.post(
                f"{config.SYNC_SERVER_URL}/api/snapshot",
                data={
                    "site_id": config.SYNC_SITE_ID,
                    "camera_name": camera_name,
                    "snapshot_type": snapshot_type,
                },
                files={"image": (f"{camera_name}.jpg", buf.tobytes(), "image/jpeg")},
                headers={"X-API-Key": config.SYNC_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            print(f"[sync] Captura ({snapshot_type}) de '{camera_name}' enviada ({len(buf)} bytes).")
        except Exception as exc:  # pragma: no cover
            print(f"[sync] Error enviando captura de '{camera_name}': {exc}")

    def _apply_line_command(self, cmd: dict, name_to_id: dict):
        command_id = cmd.get("id")
        camera_name = cmd.get("camera_name")
        command_type = cmd.get("command_type")
        payload = cmd.get("payload") or {}
        ok, error = self._do_apply_line_command(camera_name, command_type, payload, name_to_id)
        try:
            requests.post(
                f"{config.SYNC_SERVER_URL}/api/lines/command/result",
                json={"command_id": command_id, "ok": ok, "error": error or ""},
                headers={"X-API-Key": config.SYNC_API_KEY},
                timeout=15,
            )
        except Exception as exc:  # pragma: no cover
            print(f"[sync] Error reportando resultado del comando {command_id}: {exc}")
        if ok:
            print(f"[sync] Comando de línea '{command_type}' aplicado en '{camera_name}'.")
        else:
            print(f"[sync] Comando de línea '{command_type}' en '{camera_name}' falló: {error}")

    @staticmethod
    def _do_apply_line_command(camera_name, command_type, payload, name_to_id):
        """Devuelve (ok, error). Valida antes de tocar la base local para no
        aplicar datos corruptos que vengan mal formados del panel central."""
        camera_id = name_to_id.get(camera_name)
        if camera_id is None:
            return False, f"cámara '{camera_name}' no existe en este mini PC"

        if command_type == "delete":
            line_id = payload.get("line_id")
            if line_id is None:
                return False, "falta line_id"
            line = database.get_line(int(line_id))
            if not line or line["camera_id"] != camera_id:
                return False, f"línea {line_id} no existe en esta cámara"
            database.delete_line(int(line_id))
            manager.reload_lines(camera_id)
            return True, ""

        movement = payload.get("movement")
        if movement is not None and movement not in config.MOVEMENT_LABELS:
            return False, f"movimiento inválido: {movement!r}"

        if command_type == "create":
            name = payload.get("name")
            x1, y1, x2, y2 = payload.get("x1"), payload.get("y1"), payload.get("x2"), payload.get("y2")
            if not name or None in (x1, y1, x2, y2):
                return False, "faltan campos obligatorios (name, x1, y1, x2, y2)"
            database.create_line(camera_id, name, int(x1), int(y1), int(x2), int(y2),
                                  movement or "RECTO")
            manager.reload_lines(camera_id)
            return True, ""

        if command_type == "update":
            line_id = payload.get("line_id")
            if line_id is None:
                return False, "falta line_id"
            line = database.get_line(int(line_id))
            if not line or line["camera_id"] != camera_id:
                return False, f"línea {line_id} no existe en esta cámara"
            database.update_line(
                int(line_id), name=payload.get("name"),
                x1=payload.get("x1"), y1=payload.get("y1"),
                x2=payload.get("x2"), y2=payload.get("y2"),
                movement=movement,
            )
            manager.reload_lines(camera_id)
            return True, ""

        return False, f"command_type desconocido: {command_type!r}"


sync_client = SyncClient()
