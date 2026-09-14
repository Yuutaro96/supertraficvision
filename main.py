"""
main.py
Aplicación FastAPI principal del sistema de aforo vehicular.

Sirve el frontend estático, expone la API REST, el streaming MJPEG por cámara
y un WebSocket para difundir los cruces en tiempo real.

Ejecutar:
    python main.py
    # o
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import io
import csv
import queue
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import cv2
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import (
    StreamingResponse, HTMLResponse, JSONResponse, Response, FileResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import config
import database
import ptz_controller
from camera_manager import manager, mjpeg_generator, EVENT_QUEUE
from sync_client import sync_client


# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------
class CameraIn(BaseModel):
    name: str
    url: str
    active: bool = True
    ptz_capable: bool = False
    control_protocol: str = "none"
    control_config: Optional[str] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    active: Optional[bool] = None
    ptz_capable: Optional[bool] = None
    control_protocol: Optional[str] = None
    control_config: Optional[str] = None


class PTZMoveIn(BaseModel):
    direction: str  # "up" | "down" | "left" | "right"
    speed: int = 5


class PTZZoomIn(BaseModel):
    direction: str  # "in" | "out"


class LineIn(BaseModel):
    camera_id: int
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    movement: str = "RECTO"

    @field_validator("movement")
    @classmethod
    def _validate_movement(cls, v):
        if v not in config.MOVEMENT_LABELS:
            raise ValueError(f"movement debe ser uno de: {', '.join(config.MOVEMENT_LABELS)}")
        return v


class LineUpdate(BaseModel):
    name: Optional[str] = None
    x1: Optional[int] = None
    y1: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    movement: Optional[str] = None

    @field_validator("movement")
    @classmethod
    def _validate_movement(cls, v):
        if v is not None and v not in config.MOVEMENT_LABELS:
            raise ValueError(f"movement debe ser uno de: {', '.join(config.MOVEMENT_LABELS)}")
        return v


class SettingsIn(BaseModel):
    confidence: Optional[float] = None
    model_size: Optional[str] = None
    target_fps: Optional[int] = None
    device: Optional[str] = None
    iou: Optional[float] = None
    imgsz: Optional[int] = None
    class_confidence: Optional[Dict[str, float]] = None
    track_activation_threshold: Optional[float] = None
    lost_track_buffer: Optional[int] = None
    minimum_consecutive_frames: Optional[int] = None
    display_fps: Optional[int] = None
    stream_quality: Optional[int] = None
    stream_resolution: Optional[str] = None
    reconnect_delay: Optional[int] = None
    data_retention_days: Optional[int] = None
    active_classes: Optional[List[int]] = None


class RoiIn(BaseModel):
    points: List[List[int]] = []


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------
class WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, message: dict):
        async with self._lock:
            targets = list(self.active)
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                await self.disconnect(ws)


ws_manager = WSManager()


async def event_pump():
    """Drena EVENT_QUEUE (hilos de cámara) y difunde por WebSocket."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            event = await loop.run_in_executor(None, EVENT_QUEUE.get, True, 1.0)
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue
        except Exception:
            await asyncio.sleep(0.1)
            continue
        if event:
            await ws_manager.broadcast({"type": "count", "data": event})


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    manager.start_all()
    sync_client.start()
    pump_task = asyncio.create_task(event_pump())
    print("[main] Aplicación iniciada.")
    yield
    pump_task.cancel()
    sync_client.stop()
    manager.stop_all()
    print("[main] Aplicación detenida.")


app = FastAPI(title="Aforo Vehicular", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Páginas HTML
# ---------------------------------------------------------------------------
def _page(name: str) -> FileResponse:
    return FileResponse(f"{config.STATIC_DIR}/{name}")


@app.get("/", response_class=HTMLResponse)
def page_index():
    return _page("index.html")


@app.get("/cameras", response_class=HTMLResponse)
def page_cameras():
    return _page("cameras.html")


@app.get("/lines", response_class=HTMLResponse)
def page_lines():
    return _page("lines.html")


@app.get("/reports", response_class=HTMLResponse)
def page_reports():
    return _page("reports.html")


@app.get("/settings", response_class=HTMLResponse)
def page_settings():
    return _page("settings.html")


# ---------------------------------------------------------------------------
# Streaming MJPEG y frame estático
# ---------------------------------------------------------------------------
@app.get("/stream/{camera_id}")
def stream(camera_id: int):
    return StreamingResponse(
        mjpeg_generator(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/frame/{camera_id}")
def api_frame(camera_id: int):
    frame = manager.snapshot(camera_id)
    if frame is None:
        raise HTTPException(status_code=503, detail="No se pudo obtener el frame de la cámara")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(status_code=500, detail="Error codificando el frame")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# API Cámaras
# ---------------------------------------------------------------------------
@app.get("/api/cameras")
def list_cameras():
    return database.get_cameras()


@app.post("/api/cameras")
def create_camera(cam: CameraIn):
    created = database.create_camera(
        cam.name, cam.url, cam.active,
        cam.ptz_capable, cam.control_protocol, cam.control_config,
    )
    if created and created["active"]:
        manager.start_camera(created)
    return created


@app.put("/api/cameras/{camera_id}")
def update_camera(camera_id: int, cam: CameraUpdate):
    before = database.get_camera(camera_id)
    if not before:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    updated = database.update_camera(
        camera_id, cam.name, cam.url, cam.active,
        cam.ptz_capable, cam.control_protocol, cam.control_config,
    )

    # Solo reiniciar el stream si cambió la URL o el estado activo/inactivo;
    # un cambio de nombre no requiere cortar el video en curso.
    needs_restart = (
        (cam.url is not None and cam.url != before["url"])
        or (cam.active is not None and cam.active != bool(before["active"]))
    )
    if needs_restart:
        manager.stop_camera(camera_id)
        if updated["active"]:
            manager.start_camera(updated)
    return updated


@app.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: int):
    manager.stop_camera(camera_id)
    ok = database.delete_camera(camera_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API ROI (zona de interés) por cámara
# ---------------------------------------------------------------------------
@app.get("/api/cameras/{camera_id}/roi")
def get_camera_roi(camera_id: int):
    if not database.get_camera(camera_id):
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    return {"points": database.get_roi(camera_id)}


@app.put("/api/cameras/{camera_id}/roi")
def set_camera_roi(camera_id: int, roi: RoiIn):
    if not database.get_camera(camera_id):
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    if roi.points and len(roi.points) < 3:
        raise HTTPException(status_code=400, detail="Un ROI necesita al menos 3 puntos")
    database.set_roi(camera_id, roi.points)
    manager.reload_roi(camera_id)
    return {"points": roi.points}


@app.delete("/api/cameras/{camera_id}/roi")
def delete_camera_roi(camera_id: int):
    if not database.get_camera(camera_id):
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    database.set_roi(camera_id, [])
    manager.reload_roi(camera_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# API PTZ (pan/tilt/zoom) por cámara
# ---------------------------------------------------------------------------
def _get_ptz_camera_or_404(camera_id: int) -> dict:
    cam = database.get_camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    if not cam["ptz_capable"]:
        raise HTTPException(status_code=400, detail="Esta cámara no está marcada como PTZ")
    return cam


@app.post("/api/cameras/{camera_id}/ptz/move")
def ptz_move(camera_id: int, body: PTZMoveIn):
    cam = _get_ptz_camera_or_404(camera_id)
    try:
        controller = ptz_controller.get_ptz_controller(cam)
        controller.move(body.direction, body.speed)
    except ptz_controller.PTZError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/ptz/stop")
def ptz_stop(camera_id: int, body: PTZMoveIn):
    cam = _get_ptz_camera_or_404(camera_id)
    try:
        controller = ptz_controller.get_ptz_controller(cam)
        controller.stop(body.direction)
    except ptz_controller.PTZError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/ptz/zoom")
def ptz_zoom(camera_id: int, body: PTZZoomIn):
    cam = _get_ptz_camera_or_404(camera_id)
    try:
        controller = ptz_controller.get_ptz_controller(cam)
        controller.zoom(body.direction)
    except ptz_controller.PTZError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


# ---------------------------------------------------------------------------
# API Líneas
# ---------------------------------------------------------------------------
@app.get("/api/lines/{camera_id}")
def list_lines(camera_id: int):
    return database.get_lines(camera_id)


@app.post("/api/lines")
def create_line(line: LineIn):
    created = database.create_line(
        line.camera_id, line.name, line.x1, line.y1, line.x2, line.y2, line.movement
    )
    manager.reload_lines(line.camera_id)
    return created


@app.put("/api/lines/{line_id}")
def update_line(line_id: int, line: LineUpdate):
    updated = database.update_line(
        line_id, name=line.name, x1=line.x1, y1=line.y1,
        x2=line.x2, y2=line.y2, movement=line.movement,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Línea no encontrada")
    manager.reload_lines(updated["camera_id"])
    return updated


@app.delete("/api/lines/{line_id}")
def delete_line(line_id: int):
    line = database.get_line(line_id)
    if not line:
        raise HTTPException(status_code=404, detail="Línea no encontrada")
    database.delete_line(line_id)
    manager.reload_lines(line["camera_id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# API Conteos y reportes
# ---------------------------------------------------------------------------
@app.get("/api/counts")
def get_counts(camera_id: Optional[int] = None, line_id: Optional[int] = None,
               class_name: Optional[str] = None, date_from: Optional[str] = None,
               date_to: Optional[str] = None, limit: int = 200):
    return database.query_counts(camera_id, line_id, class_name,
                                 date_from, date_to, limit)


@app.get("/api/counts/summary")
def get_summary(camera_id: Optional[int] = None, date_from: Optional[str] = None,
                date_to: Optional[str] = None):
    return {
        "by_hour": database.summary_by_hour(camera_id, date_from, date_to),
        "totals_today": database.totals_today(camera_id),
    }


@app.get("/api/counts/export/csv")
def export_csv(camera_id: Optional[int] = None, line_id: Optional[int] = None,
               class_name: Optional[str] = None, date_from: Optional[str] = None,
               date_to: Optional[str] = None):
    rows = database.query_counts(camera_id, line_id, class_name,
                                 date_from, date_to, limit=None)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp", "camara", "linea", "movimiento",
                     "clase", "direccion", "conteo"])
    for r in rows:
        writer.writerow([r["id"], r["timestamp"], r.get("camera_name", ""),
                         r.get("line_name", ""), r.get("movement", ""),
                         r["class_name"], r["direction"], r["count"]])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aforo_export.csv"},
    )


# ---------------------------------------------------------------------------
# API Estado y configuración
# ---------------------------------------------------------------------------
def _gpu_info() -> dict:
    """Información del dispositivo de cómputo (GPU/CPU)."""
    info = {"device": config.settings.resolved_device, "cuda_available": False, "gpu_name": None}
    try:
        import torch
        if torch.cuda.is_available():
            info["cuda_available"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return info


@app.get("/api/status")
def get_status():
    return {
        "cameras": manager.statuses(),
        "settings": config.settings.to_dict(),
        "totals_today": database.totals_today(),
        "compute": _gpu_info(),
    }


@app.get("/api/settings")
def get_settings():
    return config.settings.to_dict()


@app.post("/api/settings")
def update_settings(s: SettingsIn):
    config.settings.update(**s.dict(exclude_none=True))
    database.save_settings(config.settings.to_dict())
    # Si el modelo ya estaba cargado (alguna cámara activa lo usó, o se
    # probó/recargó antes) y cambió tamaño/dispositivo, recargarlo ahora
    # mismo. Si nunca se cargó, no forzarlo aquí (evita descargar el modelo
    # solo por guardar Ajustes sin cámaras todavía).
    from detector import VehicleDetector, get_detector
    if VehicleDetector.instance_exists():
        get_detector().ensure_model()
    return config.settings.to_dict()


@app.get("/api/settings/gpu-info")
def gpu_info_endpoint():
    """Información detallada del dispositivo de cómputo (GPU/CPU)."""
    info = {
        "device": config.settings.resolved_device,
        "preference": config.settings.device,
        "cuda_available": False,
        "gpu_name": None,
        "vram_total_mb": None,
        "vram_free_mb": None,
    }
    try:
        import torch
        if torch.cuda.is_available():
            info["cuda_available"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
            try:
                free, total = torch.cuda.mem_get_info(0)
                info["vram_total_mb"] = round(total / (1024 * 1024))
                info["vram_free_mb"] = round(free / (1024 * 1024))
            except Exception:
                props = torch.cuda.get_device_properties(0)
                info["vram_total_mb"] = round(props.total_memory / (1024 * 1024))
    except Exception:
        pass
    return info


@app.get("/api/settings/db-stats")
def db_stats_endpoint():
    """Estadísticas de la base de datos + registros que se limpiarían."""
    stats = database.db_stats()
    days = config.settings.data_retention_days
    stats["retention_days"] = days
    stats["removable_records"] = database.count_old_records(days) if days and days > 0 else 0
    return stats


@app.post("/api/settings/clean-data")
def clean_old_data():
    """Elimina registros más antiguos que data_retention_days."""
    days = config.settings.data_retention_days
    if not days or days <= 0:
        return {"removed": 0, "message": "Retención indefinida: no se eliminó nada."}
    removed = database.clean_old_counts(days)
    return {"removed": removed, "message": f"Se eliminaron {removed} registros con más de {days} días."}


@app.post("/api/settings/test-model")
def test_model():
    """Prueba rápida de que el modelo YOLO carga correctamente."""
    import numpy as np
    try:
        from detector import get_detector
        det = get_detector()
        det.ensure_model()
        if det.model is None:
            return {"ok": False, "message": "El modelo no se pudo cargar."}
        frame = np.zeros((320, 320, 3), dtype=np.uint8)
        det.detect(frame)
        return {
            "ok": True,
            "message": "Modelo cargado y operativo.",
            "model": config.settings.model_name,
            "device": config.settings.resolved_device,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Error al probar el modelo: {exc}"}


@app.post("/api/settings/reload-model")
def reload_model():
    """Fuerza la recarga del modelo YOLO (aplica cambios de tamaño/dispositivo)."""
    try:
        from detector import get_detector
        det = get_detector()
        det._load_model()
        ok = det.model is not None
        return {
            "ok": ok,
            "message": "Modelo recargado." if ok else "No se pudo recargar el modelo.",
            "model": config.settings.model_name,
            "device": config.settings.resolved_device,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Error al recargar: {exc}"}


@app.get("/api/meta")
def get_meta():
    """Metadatos para el frontend: clases y etiquetas de movimiento."""
    return {
        "classes": [
            {"id": cid, "name": info["name"], "emoji": info["emoji"]}
            for cid, info in config.VEHICLE_CLASSES.items()
        ],
        "movements": config.MOVEMENT_LABELS,
        "sync_enabled": bool(config.SYNC_SERVER_URL),
        "intersection_url": (f"{config.SYNC_SERVER_URL}/intersections/{config.SYNC_SITE_ID}"
                              if config.SYNC_SERVER_URL else None),
    }


def _require_sync_configured():
    if not config.SYNC_SERVER_URL:
        raise HTTPException(status_code=400, detail="SYNC_SERVER_URL no configurado; no hay panel central conectado.")


@app.get("/api/sync/intersection-info")
def get_intersection_info():
    """Trae los datos de la ficha de intersección desde el panel central,
    para precargar el formulario local — no se guarda copia local."""
    _require_sync_configured()
    import requests
    try:
        resp = requests.get(
            f"{config.SYNC_SERVER_URL}/api/intersections/{config.SYNC_SITE_ID}",
            headers={"X-API-Key": config.SYNC_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo contactar al panel central: {exc}")


@app.post("/api/sync/intersection-info")
def update_intersection_info(payload: dict):
    """Envía nombre/código/notas de la intersección al panel central."""
    _require_sync_configured()
    import requests
    try:
        resp = requests.post(
            f"{config.SYNC_SERVER_URL}/api/intersections/{config.SYNC_SITE_ID}",
            json={
                "display_name": payload.get("display_name"),
                "mts_code": payload.get("mts_code"),
                "notes": payload.get("notes"),
            },
            headers={"X-API-Key": config.SYNC_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo contactar al panel central: {exc}")


@app.post("/api/sync/upload-schematic")
async def upload_intersection_schematic(image: UploadFile = File(...)):
    """Sube el esquema de señalización directo al panel central (documento
    subido una sola vez por un humano, no pasa por el ciclo de sync)."""
    _require_sync_configured()
    import requests
    data = await image.read()
    try:
        resp = requests.post(
            f"{config.SYNC_SERVER_URL}/api/intersections/{config.SYNC_SITE_ID}/schematic",
            files={"image": (image.filename, data, image.content_type)},
            headers={"X-API-Key": config.SYNC_API_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=exc.response.status_code if exc.response is not None else 502, detail=detail)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo contactar al panel central: {exc}")


@app.post("/api/sync/reset-remote")
def reset_remote_reports():
    """Reinicia los reportes acumulados de ESTE sitio en el panel central
    (Railway), llamado directamente desde la app local — no pasa por el
    ciclo normal de sync porque es una acción deliberada de un humano, no
    algo que necesite reintentos automáticos."""
    if not config.SYNC_SERVER_URL:
        raise HTTPException(status_code=400, detail="SYNC_SERVER_URL no configurado; no hay panel central conectado.")
    import requests
    try:
        resp = requests.post(
            f"{config.SYNC_SERVER_URL}/api/reset-counts",
            params={"site_id": config.SYNC_SITE_ID},
            headers={"X-API-Key": config.SYNC_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo contactar al panel central: {exc}")


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws/counts")
async def ws_counts(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # Mantener viva la conexión; el cliente puede enviar pings
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)
    except Exception:
        await ws_manager.disconnect(ws)


# Montar estáticos (js, css)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    database.init_db()
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
