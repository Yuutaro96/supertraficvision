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
from typing import List, Optional

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import (
    StreamingResponse, HTMLResponse, JSONResponse, Response, FileResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import database
from camera_manager import manager, mjpeg_generator, EVENT_QUEUE


# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------
class CameraIn(BaseModel):
    name: str
    url: str
    active: bool = True


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    active: Optional[bool] = None


class LineIn(BaseModel):
    camera_id: int
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    movement: str = "RECTO"


class LineUpdate(BaseModel):
    name: Optional[str] = None
    x1: Optional[int] = None
    y1: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    movement: Optional[str] = None


class SettingsIn(BaseModel):
    confidence: Optional[float] = None
    model_size: Optional[str] = None
    target_fps: Optional[int] = None
    device: Optional[str] = None
    iou: Optional[float] = None


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
    pump_task = asyncio.create_task(event_pump())
    print("[main] Aplicación iniciada.")
    yield
    pump_task.cancel()
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
    created = database.create_camera(cam.name, cam.url, cam.active)
    if created and created["active"]:
        manager.start_camera(created)
    return created


@app.put("/api/cameras/{camera_id}")
def update_camera(camera_id: int, cam: CameraUpdate):
    updated = database.update_camera(camera_id, cam.name, cam.url, cam.active)
    if not updated:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    # Reiniciar el stream para aplicar cambios
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
@app.get("/api/status")
def get_status():
    return {
        "cameras": manager.statuses(),
        "settings": config.settings.to_dict(),
        "totals_today": database.totals_today(),
    }


@app.get("/api/settings")
def get_settings():
    return config.settings.to_dict()


@app.post("/api/settings")
def update_settings(s: SettingsIn):
    config.settings.update(**s.dict(exclude_none=True))
    database.save_settings(config.settings.to_dict())
    return config.settings.to_dict()


@app.get("/api/meta")
def get_meta():
    """Metadatos para el frontend: clases y etiquetas de movimiento."""
    return {
        "classes": [
            {"id": cid, "name": info["name"], "emoji": info["emoji"]}
            for cid, info in config.VEHICLE_CLASSES.items()
        ],
        "movements": config.MOVEMENT_LABELS,
    }


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
