"""
sync_client.py
Sincronización periódica (opcional) de conteos y estado de cámaras hacia un
servidor central en la nube.

Solo se activa si config.SYNC_SERVER_URL está configurado (variable de
entorno SYNC_SERVER_URL). Si no está configurado, el hilo no hace nada.

No envía video ni imágenes: solo los registros de conteo (JSON pequeño) y el
estado de conexión/FPS de cada cámara, pensado para funcionar con un chip de
datos móviles de consumo mínimo.
"""

import threading
import time

import cv2
import requests

import config
import database
from camera_manager import manager

# Ancho máximo y calidad JPEG de las capturas bajo demanda: solo sirven para
# validar que la cámara apunte a donde debe, no hace falta resolución/calidad
# alta, así que se mantienen livianas (unos 15-30 KB por captura).
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
        }

        resp = requests.post(
            f"{config.SYNC_SERVER_URL}/api/ingest",
            json=payload,
            headers={"X-API-Key": config.SYNC_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()

        if pending:
            database.mark_synced([c["id"] for c in pending])
            print(f"[sync] {len(pending)} conteos sincronizados con el servidor.")

        # Capturas pendientes: el panel central marca una cámara cuando el
        # usuario le da "Solicitar captura"; solo entonces se sube una
        # imagen, para no gastar datos móviles con envíos periódicos.
        requested = (resp.json() or {}).get("snapshot_requests") or []
        for camera_name in requested:
            self._send_snapshot(camera_name)

    def _send_snapshot(self, camera_name: str):
        stream = next((s for s in manager.streams.values() if s.name == camera_name), None)
        if stream is None:
            return
        frame = stream.get_raw_frame()
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
                data={"site_id": config.SYNC_SITE_ID, "camera_name": camera_name},
                files={"image": (f"{camera_name}.jpg", buf.tobytes(), "image/jpeg")},
                headers={"X-API-Key": config.SYNC_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            print(f"[sync] Captura de '{camera_name}' enviada ({len(buf)} bytes).")
        except Exception as exc:  # pragma: no cover
            print(f"[sync] Error enviando captura de '{camera_name}': {exc}")


sync_client = SyncClient()
