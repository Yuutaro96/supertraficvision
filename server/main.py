"""
main.py (servidor central)
Recibe los conteos y el estado de cámaras que cada mini PC sincroniza, y
expone un panel mínimo (un solo usuario) para ver el estado actual y
descargar el reporte acumulado en CSV.

No sirve video, no hace detección: eso vive en el mini PC. Este servicio
solo agrega datos livianos (JSON) para poder revisarlos desde cualquier
lugar sin depender de estar en la misma red que las cámaras.
"""

import asyncio
import csv
import io
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

import database
import templates

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "")
PURGE_INTERVAL_SECONDS = 24 * 3600

security = HTTPBasic()


def require_login(credentials: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(500, "ADMIN_PASSWORD no configurado en el servidor.")
    user_ok = secrets.compare_digest(credentials.username, ADMIN_USER)
    pass_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(401, "Credenciales inválidas", headers={"WWW-Authenticate": "Basic"})


def require_api_key(x_api_key: Optional[str] = Header(None)):
    if not INGEST_API_KEY:
        raise HTTPException(500, "INGEST_API_KEY no configurado en el servidor.")
    if not x_api_key or not secrets.compare_digest(x_api_key, INGEST_API_KEY):
        raise HTTPException(401, "API key inválida")


class CameraIn(BaseModel):
    name: str
    connected: bool = False
    fps: float = 0.0
    last_error: str = ""


class CountIn(BaseModel):
    timestamp: str
    camera_name: str = ""
    line_name: str = ""
    movement: str = ""
    class_name: str
    direction: str
    count: int = 1


class LineMirrorIn(BaseModel):
    """Espejo liviano de una línea configurada en el mini PC (no es la
    fuente de verdad, solo para que el panel sepa qué ya existe)."""
    camera_name: str
    line_id: int
    name: str
    movement: str
    x1: int
    y1: int
    x2: int
    y2: int


class IngestIn(BaseModel):
    site_id: str
    cameras: List[CameraIn] = []
    counts: List[CountIn] = []
    lines: List[LineMirrorIn] = []


# Debe coincidir con config.MOVEMENT_LABELS del mini PC — este servidor no
# importa el código del mini PC (son proyectos independientes), así que se
# repite la lista aquí a propósito.
MOVEMENT_LABELS = [
    "NORTE", "SUR", "ESTE", "OESTE", "GIRO_IZQ", "GIRO_DER",
    "RECTO", "ENTRADA", "SALIDA",
]


class LineCommandIn(BaseModel):
    camera_name: str
    command_type: str  # "create" | "update" | "delete"
    name: Optional[str] = None
    movement: Optional[str] = None
    x1: Optional[int] = None
    y1: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    line_id: Optional[int] = None  # requerido para "update"/"delete"


class LineCommandResultIn(BaseModel):
    command_id: int
    ok: bool
    error: str = ""


async def purge_loop():
    while True:
        try:
            removed = database.purge_old()
            if removed:
                print(f"[server] Purgados {removed} conteos con más de {database.RETENTION_DAYS} días.")
        except Exception as exc:  # pragma: no cover
            print(f"[server] Error purgando datos antiguos: {exc}")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    task = asyncio.create_task(purge_loop())
    yield
    task.cancel()


app = FastAPI(title="Aforo Visión - Servidor Central", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/ingest")
def ingest(payload: IngestIn, _=Depends(require_api_key)):
    database.upsert_camera_status(payload.site_id, [c.dict() for c in payload.cameras])
    inserted = database.insert_counts(payload.site_id, [c.dict() for c in payload.counts])
    if payload.lines:
        # Se agrupan por cámara porque upsert_line_mirror reemplaza el
        # conjunto completo de líneas reportadas para esa cámara (para que
        # una línea borrada localmente también desaparezca del espejo).
        by_camera: Dict[str, list] = {}
        for l in payload.lines:
            by_camera.setdefault(l.camera_name, []).append(l.dict())
        for camera_name, lines in by_camera.items():
            database.upsert_line_mirror(payload.site_id, camera_name, lines)
    # Cámaras con una captura pendiente de enviar (botón "Solicitar captura"
    # del panel) y comandos de líneas pendientes de aplicar; el mini PC
    # revisa estas listas y actúa en su próximo ciclo, sin conexión aparte.
    snapshot_requests = database.get_pending_snapshot_requests(payload.site_id)
    line_commands = database.get_pending_line_commands(payload.site_id)
    return {
        "ok": True, "inserted": inserted,
        "snapshot_requests": snapshot_requests,
        "line_commands": line_commands,
    }


MAX_SNAPSHOT_BYTES = 500 * 1024  # 500 KB: de sobra para validar encuadre/detección


@app.post("/api/snapshot")
async def upload_snapshot(
    site_id: str = Form(...),
    camera_name: str = Form(...),
    image: UploadFile = File(...),
    snapshot_type: str = Form("raw"),
    _=Depends(require_api_key),
):
    data = await image.read()
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(413, "Captura demasiado grande")
    saved = database.save_snapshot(site_id, camera_name, data, snapshot_type)
    if not saved:
        raise HTTPException(404, "Cámara no reconocida (aún no reportó estado)")
    return {"ok": True}


@app.post("/api/snapshot/request")
def request_snapshot(site_id: str, camera_name: str, snapshot_type: str = "raw",
                      _=Depends(require_login)):
    ok = database.request_snapshot(site_id, camera_name, snapshot_type)
    if not ok:
        raise HTTPException(404, "Cámara no encontrada")
    return {"ok": True}


@app.get("/api/snapshot/{site_id}/{camera_name}")
def view_snapshot(site_id: str, camera_name: str, _=Depends(require_login)):
    data = database.get_snapshot(site_id, camera_name)
    if not data:
        raise HTTPException(404, "Todavía no hay captura para esta cámara")
    return Response(content=data, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Comandos de líneas (crear/editar/borrar) — cola aplicada por el mini PC
# ---------------------------------------------------------------------------
@app.post("/api/lines/command")
def queue_line_command(site_id: str, cmd: LineCommandIn, _=Depends(require_login)):
    if cmd.command_type not in ("create", "update", "delete"):
        raise HTTPException(400, "command_type debe ser create, update o delete")
    if cmd.command_type in ("update", "delete") and cmd.line_id is None:
        raise HTTPException(400, "line_id es obligatorio para update/delete")
    if cmd.command_type in ("create", "update"):
        if cmd.movement and cmd.movement not in MOVEMENT_LABELS:
            raise HTTPException(400, f"movement debe ser uno de: {', '.join(MOVEMENT_LABELS)}")
        if cmd.command_type == "create" and not cmd.name:
            raise HTTPException(400, "name es obligatorio para crear una línea")
    payload = {
        "line_id": cmd.line_id, "name": cmd.name, "movement": cmd.movement,
        "x1": cmd.x1, "y1": cmd.y1, "x2": cmd.x2, "y2": cmd.y2,
    }
    command_id = database.queue_line_command(site_id, cmd.camera_name, cmd.command_type, payload)
    return {"ok": True, "command_id": command_id}


@app.post("/api/lines/command/result")
def report_line_command_result(result: LineCommandResultIn, _=Depends(require_api_key)):
    database.mark_line_command_result(result.command_id, result.ok, result.error)
    return {"ok": True}


@app.get("/api/lines/mirror/{site_id}")
def get_line_mirror(site_id: str, _=Depends(require_login)):
    return database.get_line_mirror(site_id)




@app.get("/api/export/csv")
def export_csv(site_id: Optional[str] = None, _=Depends(require_login)):
    rows = database.get_all_counts(site_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "sitio", "timestamp", "camara", "linea", "movimiento",
                      "clase", "direccion", "conteo"])
    for r in rows:
        writer.writerow([r["id"], r["site_id"], r["timestamp"], r["camera_name"],
                          r["line_name"], r["movement"], r["class_name"],
                          r["direction"], r["count"]])
    output.seek(0)
    filename = f"aforo_reporte_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Reinicio manual de reportes (además de la purga automática por
# RETENTION_DAYS) — botón en el panel, y también llamable directo desde el
# mini PC (misma idea que en la app local: administración de datos propia).
# ---------------------------------------------------------------------------
@app.post("/api/admin/reset-counts")
def admin_reset_counts(site_id: Optional[str] = None, _=Depends(require_login)):
    removed = database.reset_counts(site_id)
    return {"ok": True, "removed": removed}


@app.post("/api/reset-counts")
def edge_reset_counts(site_id: str, _=Depends(require_api_key)):
    """Para que el mini PC pueda reiniciar SUS PROPIOS reportes centrales
    directamente (ej. un botón en la app local), sin pasar por el panel
    admin. Siempre requiere site_id explícito — nunca borra otros sitios."""
    removed = database.reset_counts(site_id)
    return {"ok": True, "removed": removed}


@app.get("/", response_class=HTMLResponse)
def dashboard(_=Depends(require_login)):
    cameras = database.get_camera_statuses()
    totals = database.totals_by_class()
    return HTMLResponse(templates.dashboard_html(cameras, totals))


@app.get("/reports", response_class=HTMLResponse)
def reports_page(site_id: str = "", hours: int = 24, _=Depends(require_login)):
    cameras = database.get_camera_statuses()
    site_ids = sorted({c["site_id"] for c in cameras})
    by_hour = database.counts_by_hour(site_id or None, hours)
    totals = database.totals_by_class(site_id or None)
    recent = database.get_all_counts(site_id or None)[:100]
    return HTMLResponse(templates.reports_html(site_ids, site_id, hours, by_hour, totals, recent))


@app.get("/lines", response_class=HTMLResponse)
def lines_page(site_id: str = "", camera_name: str = "", _=Depends(require_login)):
    cameras = database.get_camera_statuses()
    site_ids = sorted({c["site_id"] for c in cameras})
    if not site_id and site_ids:
        site_id = site_ids[0]
    cameras_by_site: Dict[str, list] = {}
    for c in cameras:
        cameras_by_site.setdefault(c["site_id"], []).append(c["name"])
    cam_names = cameras_by_site.get(site_id, [])
    if not camera_name and cam_names:
        camera_name = cam_names[0]
    mirror_lines = database.get_line_mirror(site_id) if site_id else []
    recent_commands = database.get_line_commands(site_id) if site_id else []
    return HTMLResponse(templates.lines_html(
        site_ids, cameras_by_site, site_id, camera_name,
        mirror_lines, MOVEMENT_LABELS, recent_commands,
    ))
