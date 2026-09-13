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
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
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
    import json as _json

    cameras = database.get_camera_statuses()
    totals = database.totals_by_class()
    site_ids = sorted({c["site_id"] for c in cameras})
    mirror_by_site: Dict[str, list] = {sid: database.get_line_mirror(sid) for sid in site_ids}

    def _snapshot_cell(c: dict) -> str:
        site_q = quote(c["site_id"])
        name_q = quote(c["name"])
        if c["has_snapshot"]:
            type_label = "con detecciones" if c.get("snapshot_image_type") == "annotated" else "cruda"
            thumb = (
                f"<a href='/api/snapshot/{site_q}/{name_q}' target='_blank'>"
                f"<img class='thumb' src='/api/snapshot/{site_q}/{name_q}'></a>"
                f"<div class='muted'>{type_label} · {c['snapshot_captured_at'] or ''}</div>"
            )
        else:
            thumb = "<span class='muted'>Sin captura todavía</span>"
        if c["snapshot_requested"]:
            type_txt = "con detecciones" if c.get("requested_snapshot_type") == "annotated" else "cruda"
            pending = f"<div class='muted'>⏳ Solicitada ({type_txt}), esperando al mini PC…</div>"
        else:
            pending = ""
        return (
            f"{thumb}{pending}"
            f"<div class='btn-row'>"
            f"<button class='snap-btn' onclick=\"requestSnapshot('{site_q}','{name_q}','raw')\">📷 Cruda</button>"
            f"<button class='snap-btn' onclick=\"requestSnapshot('{site_q}','{name_q}','annotated')\">🎯 Con detecciones</button>"
            f"</div>"
        )

    def _lines_cell(c: dict) -> str:
        site_q = quote(c["site_id"])
        name_q = quote(c["name"])
        return f"<button class='snap-btn' onclick=\"openLinesEditor('{site_q}','{name_q}')\">✏️ Configurar líneas</button>"

    cam_rows = "".join(
        f"<tr><td>{c['site_id']}</td><td>{c['name']}</td>"
        f"<td>{'🟢 Conectada' if c['connected'] else '🔴 Sin señal'}</td>"
        f"<td>{c['fps']:.1f}</td><td>{c['updated_at'] or ''}</td>"
        f"<td>{_snapshot_cell(c)}</td><td>{_lines_cell(c)}</td></tr>"
        for c in cameras
    ) or "<tr><td colspan='7'>Sin cámaras reportadas todavía.</td></tr>"

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
        .thumb {{ max-width: 140px; max-height: 90px; display: block;
                   border-radius: 4px; border: 1px solid #2b3444; }}
        .muted {{ color: #8b949e; font-size: 11px; margin-top: 4px; }}
        .snap-btn {{ margin-top: 6px; padding: 4px 10px; font-size: 12px;
                      background: #21262d; color: #e6edf3; border: 1px solid #30363d;
                      border-radius: 5px; cursor: pointer; }}
        .snap-btn:hover {{ background: #30363d; }}
        .btn-row {{ display: flex; gap: 6px; flex-wrap: wrap; }}
        .overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.6);
                     display: none; align-items: center; justify-content: center; z-index: 1000; }}
        .overlay.show {{ display: flex; }}
        .modal {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                   padding: 20px; width: 640px; max-width: 95vw; max-height: 90vh; overflow-y: auto; }}
        .modal h3 {{ margin: 0 0 12px; font-size: 15px; }}
        .modal canvas {{ width: 100%; max-width: 600px; background: #000; border-radius: 4px;
                          cursor: crosshair; display: block; }}
        .modal .field {{ margin-top: 10px; }}
        .modal label {{ font-size: 12px; color: #8b949e; display: block; margin-bottom: 4px; }}
        .modal input, .modal select {{ width: 100%; padding: 6px 8px; background: #0d1117;
                                        color: #e6edf3; border: 1px solid #30363d; border-radius: 5px; }}
        .modal-actions {{ margin-top: 12px; display: flex; gap: 8px; }}
        .existing-lines {{ margin-top: 16px; border-top: 1px solid #2b3444; padding-top: 10px; }}
        .existing-lines div {{ display: flex; justify-content: space-between; align-items: center;
                                 padding: 4px 0; font-size: 12.5px; }}
        .close-x {{ float: right; background: none; border: none; color: #8b949e; cursor: pointer; font-size: 16px; }}
        .hidden {{ display: none !important; }}
      </style>
    </head>
    <body>
      <h1>Aforo Visión &middot; Panel Central</h1>

      <h2>Estado de cámaras</h2>
      <table>
        <tr><th>Sitio</th><th>Cámara</th><th>Estado</th><th>FPS</th><th>Última actualización</th><th>Captura</th><th>Líneas</th></tr>
        {cam_rows}
      </table>

      <h2>Totales acumulados (desde la última purga/descarga)</h2>
      <table>
        <tr><th>Clase</th><th>Total</th></tr>
        {total_rows}
      </table>

      <a class="button" href="/api/export/csv">Descargar reporte (CSV)</a>

      <div class="overlay" id="linesOverlay">
        <div class="modal">
          <button class="close-x" onclick="closeLinesEditor()">✕</button>
          <h3 id="linesModalTitle">Configurar líneas</h3>
          <p class="muted" id="linesNoImage">
            Todavía no hay una captura de esta cámara para dibujar sobre ella —
            pide una "📷 Cruda" en la fila de la cámara y vuelve a abrir este editor.
          </p>
          <canvas id="linesCanvas" width="600" height="340" class="hidden"></canvas>
          <p class="muted" id="linesCoords">Coordenadas: —</p>
          <div class="field">
            <label>Nombre de la línea nueva</label>
            <input id="lineNameInput" placeholder="Ej. Carril Norte">
          </div>
          <div class="field">
            <label>Movimiento / Dirección</label>
            <select id="lineMovementInput">
              {"".join(f"<option value='{m}'>{m}</option>" for m in MOVEMENT_LABELS)}
            </select>
          </div>
          <div class="modal-actions">
            <button class="button" style="margin:0" id="sendLineBtn" onclick="sendLineCreate()" disabled>Enviar comando (crear)</button>
            <button class="snap-btn" onclick="resetLineDraw()">Reiniciar dibujo</button>
          </div>
          <div class="existing-lines" id="existingLinesList"></div>
        </div>
      </div>

      <script>
        const LINE_MIRROR = {_json.dumps(mirror_by_site)};

        async function requestSnapshot(siteId, cameraName, snapshotType) {{
          const url = `/api/snapshot/request?site_id=${{siteId}}&camera_name=${{cameraName}}&snapshot_type=${{snapshotType}}`;
          const resp = await fetch(url, {{ method: "POST" }});
          if (resp.ok) {{
            alert("Captura solicitada. El mini PC la envía en su próximo ciclo de sincronización (puede tardar unos minutos).");
            location.reload();
          }} else {{
            alert("No se pudo solicitar la captura.");
          }}
        }}

        let curSite = null, curCamera = null, imgW = 0, imgH = 0;
        let pointA = null, pointB = null;
        const canvas = document.getElementById("linesCanvas");
        const ctx = canvas.getContext("2d");
        let bgImg = null;

        function scaleFactors() {{ return {{ sx: canvas.width / imgW, sy: canvas.height / imgH }}; }}
        function canvasToImage(cx, cy) {{
          const {{ sx, sy }} = scaleFactors();
          return {{ x: Math.round(cx / sx), y: Math.round(cy / sy) }};
        }}
        function imageToCanvas(ix, iy) {{
          const {{ sx, sy }} = scaleFactors();
          return {{ x: ix * sx, y: iy * sy }};
        }}

        function redrawLinesCanvas() {{
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          if (bgImg) ctx.drawImage(bgImg, 0, 0, canvas.width, canvas.height);
          const lines = (LINE_MIRROR[curSite] || []).filter(l => l.camera_name === curCamera);
          ctx.lineWidth = 2;
          lines.forEach(l => {{
            const a = imageToCanvas(l.x1, l.y1), b = imageToCanvas(l.x2, l.y2);
            ctx.strokeStyle = "#2f81f7";
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
            ctx.fillStyle = "#2f81f7"; ctx.font = "12px sans-serif";
            ctx.fillText(`${{l.name}} (${{l.movement}})`, a.x + 4, a.y - 6);
          }});
          if (pointA) {{
            const a = imageToCanvas(pointA.x, pointA.y);
            ctx.fillStyle = "#3fb950";
            ctx.beginPath(); ctx.arc(a.x, a.y, 5, 0, Math.PI * 2); ctx.fill();
          }}
          if (pointA && pointB) {{
            const a = imageToCanvas(pointA.x, pointA.y), b = imageToCanvas(pointB.x, pointB.y);
            ctx.strokeStyle = "#3fb950";
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
            ctx.fillStyle = "#3fb950";
            ctx.beginPath(); ctx.arc(b.x, b.y, 5, 0, Math.PI * 2); ctx.fill();
          }}
        }}

        function renderExistingLines() {{
          const lines = (LINE_MIRROR[curSite] || []).filter(l => l.camera_name === curCamera);
          const box = document.getElementById("existingLinesList");
          if (lines.length === 0) {{
            box.innerHTML = "<p class='muted'>Sin líneas configuradas en esta cámara.</p>";
            return;
          }}
          box.innerHTML = "<p class='muted' style='margin:0 0 6px'>Líneas ya configuradas (editar = borrar y crear de nuevo):</p>" +
            lines.map(l => `<div><span>${{l.name}} — ${{l.movement}}</span>
              <button class="snap-btn" onclick="sendLineDelete(${{l.line_id}})">🗑 Borrar</button></div>`).join("");
        }}

        function openLinesEditor(siteId, cameraName) {{
          curSite = siteId; curCamera = cameraName;
          pointA = pointB = null;
          document.getElementById("linesModalTitle").textContent = `Configurar líneas · ${{cameraName}} (${{siteId}})`;
          document.getElementById("lineNameInput").value = "";
          document.getElementById("sendLineBtn").disabled = true;
          document.getElementById("linesCoords").textContent = "Coordenadas: —";
          renderExistingLines();

          const img = new Image();
          img.onload = () => {{
            bgImg = img; imgW = img.naturalWidth; imgH = img.naturalHeight;
            canvas.classList.remove("hidden");
            document.getElementById("linesNoImage").classList.add("hidden");
            redrawLinesCanvas();
          }};
          img.onerror = () => {{
            bgImg = null;
            canvas.classList.add("hidden");
            document.getElementById("linesNoImage").classList.remove("hidden");
          }};
          img.src = `/api/snapshot/${{siteId}}/${{cameraName}}?t=${{Date.now()}}`;
          document.getElementById("linesOverlay").classList.add("show");
        }}

        function closeLinesEditor() {{
          document.getElementById("linesOverlay").classList.remove("show");
        }}

        function resetLineDraw() {{
          pointA = pointB = null;
          document.getElementById("linesCoords").textContent = "Coordenadas: —";
          document.getElementById("sendLineBtn").disabled = true;
          redrawLinesCanvas();
        }}

        canvas.addEventListener("click", (e) => {{
          if (!bgImg) return;
          const rect = canvas.getBoundingClientRect();
          const cx = (e.clientX - rect.left) * (canvas.width / rect.width);
          const cy = (e.clientY - rect.top) * (canvas.height / rect.height);
          const p = canvasToImage(cx, cy);
          if (!pointA || (pointA && pointB)) {{ pointA = p; pointB = null; }}
          else {{ pointB = p; }}
          document.getElementById("linesCoords").textContent = pointA && pointB
            ? `A(${{pointA.x}}, ${{pointA.y}}) -> B(${{pointB.x}}, ${{pointB.y}})`
            : `A(${{pointA.x}}, ${{pointA.y}}) -> marca el punto B`;
          document.getElementById("sendLineBtn").disabled = !(pointA && pointB);
          redrawLinesCanvas();
        }});

        async function sendLineCreate() {{
          const name = document.getElementById("lineNameInput").value.trim();
          const movement = document.getElementById("lineMovementInput").value;
          if (!name || !pointA || !pointB) {{ alert("Falta el nombre o los dos puntos de la línea"); return; }}
          const body = {{
            camera_name: curCamera, command_type: "create", name, movement,
            x1: pointA.x, y1: pointA.y, x2: pointB.x, y2: pointB.y,
          }};
          const resp = await fetch(`/api/lines/command?site_id=${{curSite}}`, {{
            method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(body),
          }});
          if (resp.ok) {{
            alert("Comando encolado. Se aplica en el próximo ciclo de sync del mini PC.");
            closeLinesEditor();
          }} else {{
            alert("No se pudo encolar el comando: " + (await resp.text()));
          }}
        }}

        async function sendLineDelete(lineId) {{
          if (!confirm("¿Borrar esta línea? Se aplica en el próximo ciclo de sync del mini PC.")) return;
          const body = {{ camera_name: curCamera, command_type: "delete", line_id: lineId }};
          const resp = await fetch(`/api/lines/command?site_id=${{curSite}}`, {{
            method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(body),
          }});
          if (resp.ok) {{
            alert("Comando de borrado encolado.");
          }} else {{
            alert("No se pudo encolar el comando: " + (await resp.text()));
          }}
        }}
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
