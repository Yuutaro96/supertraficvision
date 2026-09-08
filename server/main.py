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
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

import database

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


class IngestIn(BaseModel):
    site_id: str
    cameras: List[CameraIn] = []
    counts: List[CountIn] = []


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
    return {"ok": True, "inserted": inserted}


@app.get("/api/export/csv")
def export_csv(_=Depends(require_login)):
    rows = database.get_all_counts()
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


@app.get("/", response_class=HTMLResponse)
def dashboard(_=Depends(require_login)):
    cameras = database.get_camera_statuses()
    totals = database.totals_by_class()

    cam_rows = "".join(
        f"<tr><td>{c['site_id']}</td><td>{c['name']}</td>"
        f"<td>{'🟢 Conectada' if c['connected'] else '🔴 Sin señal'}</td>"
        f"<td>{c['fps']:.1f}</td><td>{c['updated_at'] or ''}</td></tr>"
        for c in cameras
    ) or "<tr><td colspan='5'>Sin cámaras reportadas todavía.</td></tr>"

    total_rows = "".join(
        f"<tr><td>{cls}</td><td>{n}</td></tr>" for cls, n in sorted(totals.items())
    ) or "<tr><td colspan='2'>Sin datos todavía.</td></tr>"

    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Aforo Visión - Panel Central</title>
      <style>
        body {{ font-family: system-ui, sans-serif; background: #0f1115; color: #e6edf3;
               margin: 0; padding: 32px; }}
        h1 {{ font-size: 20px; }}
        h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: .05em;
              color: #8b949e; margin-top: 32px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #2b3444;
                  font-size: 13px; }}
        th {{ color: #8b949e; font-weight: 500; }}
        a.button {{ display: inline-block; margin-top: 16px; padding: 10px 16px;
                    background: #2f81f7; color: white; border-radius: 6px;
                    text-decoration: none; font-weight: 600; }}
      </style>
    </head>
    <body>
      <h1>Aforo Visión &middot; Panel Central</h1>

      <h2>Estado de cámaras</h2>
      <table>
        <tr><th>Sitio</th><th>Cámara</th><th>Estado</th><th>FPS</th><th>Última actualización</th></tr>
        {cam_rows}
      </table>

      <h2>Totales acumulados (desde la última purga/descarga)</h2>
      <table>
        <tr><th>Clase</th><th>Total</th></tr>
        {total_rows}
      </table>

      <a class="button" href="/api/export/csv">Descargar reporte (CSV)</a>
    </body>
    </html>
    """
    return HTMLResponse(html)
