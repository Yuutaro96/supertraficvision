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

import requests

import config
import database
from camera_manager import manager


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


sync_client = SyncClient()
