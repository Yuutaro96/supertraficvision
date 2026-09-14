"""
templates.py (servidor central)
Construcción del HTML del panel — separado de main.py para no amontonar
todo el ruteo con strings gigantes. Son funciones puras (reciben datos ya
consultados de la base, devuelven un string HTML completo); no importan
FastAPI ni tocan la base de datos directamente.
"""

import json
from urllib.parse import quote

# Debe coincidir con config.MOVEMENT_LABELS del mini PC — este servidor no
# importa el código del mini PC (son proyectos independientes), así que se
# repite la lista aquí a propósito. (Definido también en main.py; se pasa
# como parámetro a las funciones que lo necesitan para no duplicar el import.)

PAGE_CSS = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #0f1115; color: #e6edf3;
         margin: 0; padding: 0 16px 32px; }
  h1 { font-size: 18px; margin: 0; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .05em;
       color: #8b949e; margin: 24px 0 8px; }
  nav { display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
        padding: 14px 0; border-bottom: 1px solid #2b3444; margin-bottom: 18px;
        position: sticky; top: 0; background: #0f1115; z-index: 10; }
  nav a { color: #8b949e; text-decoration: none; padding: 8px 12px; border-radius: 6px;
          font-size: 13.5px; font-weight: 600; }
  nav a.active { color: #e6edf3; background: #21262d; }
  nav a:hover { color: #e6edf3; }
  .table-wrap { overflow-x: auto; border-radius: 8px; border: 1px solid #2b3444; }
  table { width: 100%; border-collapse: collapse; min-width: 560px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #2b3444; font-size: 13px; }
  th { color: #8b949e; font-weight: 500; background: #161b22; }
  tr:last-child td { border-bottom: none; }
  a.button, button.button { display: inline-block; padding: 10px 16px; background: #2f81f7;
              color: white; border-radius: 6px; text-decoration: none; font-weight: 600;
              border: none; cursor: pointer; font-size: 13.5px; }
  button.button.danger { background: #da3633; }
  .btn-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
  .thumb { max-width: 140px; max-height: 90px; display: block; border-radius: 4px; border: 1px solid #2b3444; }
  .muted { color: #8b949e; font-size: 11px; margin-top: 4px; }
  .snap-btn { margin-top: 6px; padding: 6px 10px; font-size: 12px; background: #21262d;
              color: #e6edf3; border: 1px solid #30363d; border-radius: 5px; cursor: pointer; }
  .snap-btn:hover { background: #30363d; }
  .cam-cards { display: none; }
  .filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin: 10px 0 18px; }
  .filters .field { display: flex; flex-direction: column; gap: 4px; }
  .filters label { font-size: 11px; color: #8b949e; }
  .filters select, .filters input { padding: 7px 9px; background: #0d1117; color: #e6edf3;
                                     border: 1px solid #30363d; border-radius: 5px; font-size: 13px; }
  .chart-box { background: #161b22; border: 1px solid #2b3444; border-radius: 8px; padding: 14px; margin-bottom: 20px; }
  .chart-box canvas { max-width: 100%; }
  .tag-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px;
               font-weight: 600; }
  .tag-badge.pending { background: #3d2f00; color: #e3b341; }
  .tag-badge.applied { background: #0d2818; color: #3fb950; }
  .tag-badge.failed { background: #3d1418; color: #f85149; }
  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6);
             display: none; align-items: center; justify-content: center; z-index: 1000; padding: 12px; }
  .overlay.show { display: flex; }
  .modal { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 18px;
           width: 640px; max-width: 100%; max-height: 92vh; overflow-y: auto; }
  .modal h3 { margin: 0 0 12px; font-size: 15px; }
  .modal canvas { width: 100%; max-width: 600px; background: #000; border-radius: 4px; cursor: crosshair; display: block; }
  .modal .field { margin-top: 10px; }
  .modal label { font-size: 12px; color: #8b949e; display: block; margin-bottom: 4px; }
  .modal input, .modal select { width: 100%; padding: 7px 9px; background: #0d1117; color: #e6edf3;
                                 border: 1px solid #30363d; border-radius: 5px; font-size: 13.5px; }
  .close-x { float: right; background: none; border: none; color: #8b949e; cursor: pointer; font-size: 18px; }
  .existing-lines { margin-top: 16px; border-top: 1px solid #2b3444; padding-top: 10px; }
  .existing-lines div { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; font-size: 12.5px; gap: 8px; }
  .hidden { display: none !important; }

  /* --- Mobile: tablas anchas -> tarjetas apiladas --- */
  @media (max-width: 640px) {
    body { padding: 0 10px 28px; }
    .table-wrap.responsive { display: none; }
    .cam-cards { display: grid; gap: 10px; }
    .cam-card { background: #161b22; border: 1px solid #2b3444; border-radius: 8px; padding: 12px; }
    .cam-card .row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 13px; }
    .cam-card .row span:first-child { color: #8b949e; }
    .filters { flex-direction: column; align-items: stretch; }
    .modal { padding: 14px; }
  }
"""


def nav_html(active: str) -> str:
    items = [("/", "Estado"), ("/reports", "Reportes"), ("/lines", "Líneas")]
    links = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for key, (href, label) in zip(["status", "reports", "lines"], items)
    )
    return f'<nav><span style="font-weight:700;margin-right:10px">🚦 Aforo Visión</span>{links}</nav>'


def page_shell(title: str, active: str, body: str, extra_head: str = "") -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} · Aforo Visión</title>
  <style>{PAGE_CSS}</style>
  {extra_head}
</head>
<body>
  {nav_html(active)}
  {body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Página: Estado (dashboard principal)
# ---------------------------------------------------------------------------
def dashboard_html(cameras: list, totals: dict) -> str:
    def _snapshot_cell(c: dict) -> str:
        site_q, name_q = quote(c["site_id"]), quote(c["name"])
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
            pending = f"<div class='muted'>⏳ Solicitada ({type_txt})…</div>"
        else:
            pending = ""
        return (
            f"{thumb}{pending}"
            f"<div class='btn-row'>"
            f"<button class='snap-btn' onclick=\"requestSnapshot('{site_q}','{name_q}','raw')\">📷 Cruda</button>"
            f"<button class='snap-btn' onclick=\"requestSnapshot('{site_q}','{name_q}','annotated')\">🎯 Detecciones</button>"
            f"</div>"
        )

    def _lines_btn(c: dict) -> str:
        site_q, name_q = quote(c["site_id"]), quote(c["name"])
        return f"<a class='snap-btn' style='text-decoration:none;display:inline-block' href=\"/lines?site_id={site_q}&camera_name={name_q}\">✏️ Configurar</a>"

    rows_html = "".join(
        f"<tr><td>{c['site_id']}</td><td>{c['name']}</td>"
        f"<td>{'🟢 Conectada' if c['connected'] else '🔴 Sin señal'}</td>"
        f"<td>{c['fps']:.1f}</td><td>{c['updated_at'] or ''}</td>"
        f"<td>{_snapshot_cell(c)}</td><td>{_lines_btn(c)}</td></tr>"
        for c in cameras
    ) or "<tr><td colspan='7'>Sin cámaras reportadas todavía.</td></tr>"

    cards_html = "".join(f"""
      <div class="cam-card">
        <div class="row"><span>Sitio</span><span>{c['site_id']}</span></div>
        <div class="row"><span>Cámara</span><span>{c['name']}</span></div>
        <div class="row"><span>Estado</span><span>{'🟢 Conectada' if c['connected'] else '🔴 Sin señal'}</span></div>
        <div class="row"><span>FPS</span><span>{c['fps']:.1f}</span></div>
        <div class="row"><span>Actualizado</span><span>{c['updated_at'] or '—'}</span></div>
        <div style="margin-top:8px">{_snapshot_cell(c)}</div>
        <div style="margin-top:8px">{_lines_btn(c)}</div>
      </div>
    """ for c in cameras) or "<p class='muted'>Sin cámaras reportadas todavía.</p>"

    total_rows = "".join(
        f"<tr><td>{cls}</td><td>{n}</td></tr>" for cls, n in sorted(totals.items())
    ) or "<tr><td colspan='2'>Sin datos todavía.</td></tr>"

    body = f"""
      <h1>Estado de cámaras</h1>
      <div class="table-wrap responsive">
        <table>
          <tr><th>Sitio</th><th>Cámara</th><th>Estado</th><th>FPS</th><th>Actualizado</th><th>Captura</th><th>Líneas</th></tr>
          {rows_html}
        </table>
      </div>
      <div class="cam-cards">{cards_html}</div>

      <h2>Totales acumulados (desde el último reinicio/purga)</h2>
      <div class="table-wrap">
        <table><tr><th>Clase</th><th>Total</th></tr>{total_rows}</table>
      </div>
      <div class="btn-row">
        <a class="button" href="/api/export/csv">⬇️ Descargar CSV</a>
        <a class="button" href="/reports">📊 Ver reportes con gráficas</a>
      </div>

      <script>
        async function requestSnapshot(siteId, cameraName, snapshotType) {{
          const url = `/api/snapshot/request?site_id=${{siteId}}&camera_name=${{cameraName}}&snapshot_type=${{snapshotType}}`;
          const resp = await fetch(url, {{ method: "POST" }});
          if (resp.ok) {{
            alert("Captura solicitada. El mini PC la envía en su próximo ciclo de sincronización.");
            location.reload();
          }} else {{
            alert("No se pudo solicitar la captura.");
          }}
        }}
      </script>
    """
    return page_shell("Estado", "status", body)


# ---------------------------------------------------------------------------
# Página: Reportes (gráficas)
# ---------------------------------------------------------------------------
def reports_html(site_ids: list, selected_site: str, hours: int,
                  by_hour: list, totals: dict, recent_counts: list) -> str:
    site_options = "<option value=''>Todos los sitios</option>" + "".join(
        f"<option value='{s}' {'selected' if s == selected_site else ''}>{s}</option>" for s in site_ids
    )
    hour_options = "".join(
        f"<option value='{h}' {'selected' if h == hours else ''}>Últimas {h} horas</option>"
        for h in (6, 24, 48, 72, 168)
    )

    classes = sorted({row["class_name"] for row in by_hour}) or []
    hour_labels = sorted({row["hour"] for row in by_hour})
    series = {
        cls: [next((r["total"] for r in by_hour if r["hour"] == h and r["class_name"] == cls), 0)
              for h in hour_labels]
        for cls in classes
    }

    rows_html = "".join(
        f"<tr><td>{r['timestamp']}</td><td>{r['site_id']}</td><td>{r['camera_name']}</td>"
        f"<td>{r['line_name']}</td><td>{r['movement']}</td><td>{r['class_name']}</td>"
        f"<td>{r['direction']}</td><td>{r['count']}</td></tr>"
        for r in recent_counts
    ) or "<tr><td colspan='8'>Sin conteos todavía.</td></tr>"

    site_qs = f"&site_id={quote(selected_site)}" if selected_site else ""

    body = f"""
      <h1>Reportes</h1>
      <form class="filters" method="get" action="/reports">
        <div class="field"><label>Sitio</label><select name="site_id" onchange="this.form.submit()">{site_options}</select></div>
        <div class="field"><label>Ventana de tiempo</label><select name="hours" onchange="this.form.submit()">{hour_options}</select></div>
      </form>

      <div class="chart-box">
        <h2 style="margin-top:0">Conteos por hora y clase</h2>
        <canvas id="hourChart" height="220"></canvas>
      </div>
      <div class="chart-box">
        <h2 style="margin-top:0">Distribución por clase</h2>
        <canvas id="classChart" height="220"></canvas>
      </div>

      <div class="btn-row">
        <a class="button" href="/api/export/csv?{site_qs.lstrip('&')}">⬇️ Descargar CSV{' (' + selected_site + ')' if selected_site else ''}</a>
        <button class="button danger" onclick="resetReports()">🔄 Reiniciar reportes{' de ' + selected_site if selected_site else ''}</button>
      </div>

      <h2>Últimos conteos</h2>
      <div class="table-wrap">
        <table>
          <tr><th>Timestamp</th><th>Sitio</th><th>Cámara</th><th>Línea</th><th>Movimiento</th><th>Clase</th><th>Dir.</th><th>Cant.</th></tr>
          {rows_html}
        </table>
      </div>

      <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
      <script>
        const hourLabels = {json.dumps(hour_labels)};
        const series = {json.dumps(series)};
        const totals = {json.dumps(totals)};
        const palette = ["#2f81f7","#3fb950","#e3b341","#f85149","#a371f7","#39c5cf","#ff7b72","#79c0ff"];

        new Chart(document.getElementById("hourChart"), {{
          type: "bar",
          data: {{
            labels: hourLabels,
            datasets: Object.keys(series).map((cls, i) => ({{
              label: cls, data: series[cls], backgroundColor: palette[i % palette.length],
            }})),
          }},
          options: {{
            responsive: true,
            scales: {{ x: {{ stacked: true, ticks: {{ color: "#8b949e" }} }},
                       y: {{ stacked: true, ticks: {{ color: "#8b949e" }} }} }},
            plugins: {{ legend: {{ labels: {{ color: "#e6edf3" }} }} }},
          }},
        }});

        new Chart(document.getElementById("classChart"), {{
          type: "doughnut",
          data: {{
            labels: Object.keys(totals),
            datasets: [{{ data: Object.values(totals), backgroundColor: palette }}],
          }},
          options: {{ responsive: true, plugins: {{ legend: {{ labels: {{ color: "#e6edf3" }} }} }} }},
        }});

        async function resetReports() {{
          const siteId = {json.dumps(selected_site)};
          const msg = siteId ? `¿Reiniciar los reportes acumulados del sitio "${{siteId}}"?` : "¿Reiniciar TODOS los reportes acumulados (todos los sitios)?";
          if (!confirm(msg)) return;
          const url = "/api/admin/reset-counts" + (siteId ? `?site_id=${{encodeURIComponent(siteId)}}` : "");
          const resp = await fetch(url, {{ method: "POST" }});
          if (resp.ok) {{
            const data = await resp.json();
            alert(`Se eliminaron ${{data.removed}} registros.`);
            location.reload();
          }} else {{
            alert("No se pudo reiniciar los reportes.");
          }}
        }}
      </script>
    """
    return page_shell("Reportes", "reports", body)


# ---------------------------------------------------------------------------
# Página: Líneas (editor completo)
# ---------------------------------------------------------------------------
def lines_html(site_ids: list, cameras_by_site: dict, selected_site: str,
               selected_camera: str, mirror_lines: list, movement_labels: list,
               recent_commands: list) -> str:
    site_options = "".join(
        f"<option value='{s}' {'selected' if s == selected_site else ''}>{s}</option>" for s in site_ids
    )
    cam_names = cameras_by_site.get(selected_site, [])
    camera_options = "".join(
        f"<option value='{c}' {'selected' if c == selected_camera else ''}>{c}</option>" for c in cam_names
    )
    movement_options = "".join(f"<option value='{m}'>{m}</option>" for m in movement_labels)

    lines_for_camera = [l for l in mirror_lines if l["camera_name"] == selected_camera]
    existing_html = "".join(
        f"<div><span>{l['name']} — {l['movement']}</span>"
        f"<button class='snap-btn' onclick=\"sendLineDelete({l['line_id']})\">🗑 Borrar</button></div>"
        for l in lines_for_camera
    ) or "<p class='muted'>Sin líneas configuradas en esta cámara todavía.</p>"

    def _status_badge(s):
        return f"<span class='tag-badge {s}'>{s}</span>"

    cmd_rows = "".join(
        f"<tr><td>{c['created_at'] or ''}</td><td>{c['camera_name']}</td><td>{c['command_type']}</td>"
        f"<td>{_status_badge(c['status'])}</td><td>{c['error'] or ''}</td></tr>"
        for c in recent_commands
    ) or "<tr><td colspan='5'>Sin comandos todavía.</td></tr>"

    body = f"""
      <h1>Configurar líneas</h1>
      <form class="filters" method="get" action="/lines">
        <div class="field"><label>Sitio</label><select name="site_id" onchange="this.form.submit()">{site_options}</select></div>
        <div class="field"><label>Cámara</label><select name="camera_name" onchange="this.form.submit()">{camera_options}</select></div>
      </form>

      {"<p class='muted'>Elige un sitio y una cámara para empezar.</p>" if not selected_camera else f'''
      <p class="muted" id="linesNoImage">
        Todavía no hay una captura de esta cámara para dibujar sobre ella —
        ve a <a href="/" style="color:#2f81f7">Estado</a> y pide una "📷 Cruda" de <b>{selected_camera}</b>, luego vuelve aquí.
      </p>
      <canvas id="linesCanvas" width="600" height="340" class="hidden"></canvas>
      <p class="muted" id="linesCoords">Coordenadas: —</p>
      <div class="field">
        <label>Nombre de la línea nueva</label>
        <input id="lineNameInput" placeholder="Ej. Carril Norte">
      </div>
      <div class="field">
        <label>Movimiento / Dirección</label>
        <select id="lineMovementInput">{movement_options}</select>
      </div>
      <div class="btn-row">
        <button class="button" id="sendLineBtn" onclick="sendLineCreate()" disabled>Enviar comando (crear)</button>
        <button class="snap-btn" onclick="resetLineDraw()">Reiniciar dibujo</button>
      </div>

      <h2>Líneas ya configuradas (editar = borrar y crear de nuevo)</h2>
      <div class="existing-lines" id="existingLinesList">{existing_html}</div>
      '''}

      <h2>Historial de comandos de este sitio</h2>
      <div class="table-wrap">
        <table>
          <tr><th>Fecha</th><th>Cámara</th><th>Tipo</th><th>Estado</th><th>Error</th></tr>
          {cmd_rows}
        </table>
      </div>

      <script>
        const curSite = {json.dumps(selected_site)};
        const curCamera = {json.dumps(selected_camera)};
        let imgW = 0, imgH = 0, pointA = null, pointB = null, bgImg = null;
        const canvas = document.getElementById("linesCanvas");
        const ctx = canvas ? canvas.getContext("2d") : null;

        function scaleFactors() {{ return {{ sx: canvas.width / imgW, sy: canvas.height / imgH }}; }}
        function canvasToImage(cx, cy) {{
          const {{ sx, sy }} = scaleFactors();
          return {{ x: Math.round(cx / sx), y: Math.round(cy / sy) }};
        }}
        function imageToCanvas(ix, iy) {{
          const {{ sx, sy }} = scaleFactors();
          return {{ x: ix * sx, y: iy * sy }};
        }}

        function redraw() {{
          if (!ctx) return;
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          if (bgImg) ctx.drawImage(bgImg, 0, 0, canvas.width, canvas.height);
          if (pointA) {{
            const a = imageToCanvas(pointA.x, pointA.y);
            ctx.fillStyle = "#3fb950";
            ctx.beginPath(); ctx.arc(a.x, a.y, 5, 0, Math.PI * 2); ctx.fill();
          }}
          if (pointA && pointB) {{
            const a = imageToCanvas(pointA.x, pointA.y), b = imageToCanvas(pointB.x, pointB.y);
            ctx.strokeStyle = "#3fb950"; ctx.lineWidth = 3;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
            ctx.fillStyle = "#3fb950";
            ctx.beginPath(); ctx.arc(b.x, b.y, 5, 0, Math.PI * 2); ctx.fill();
          }}
        }}

        if (canvas) {{
          const img = new Image();
          img.onload = () => {{
            bgImg = img; imgW = img.naturalWidth; imgH = img.naturalHeight;
            canvas.classList.remove("hidden");
            document.getElementById("linesNoImage").classList.add("hidden");
            redraw();
          }};
          img.onerror = () => {{ canvas.classList.add("hidden"); }};
          img.src = `/api/snapshot/${{curSite}}/${{curCamera}}?t=${{Date.now()}}`;

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
            redraw();
          }});
        }}

        function resetLineDraw() {{
          pointA = pointB = null;
          document.getElementById("linesCoords").textContent = "Coordenadas: —";
          document.getElementById("sendLineBtn").disabled = true;
          redraw();
        }}

        async function sendLineCreate() {{
          const name = document.getElementById("lineNameInput").value.trim();
          const movement = document.getElementById("lineMovementInput").value;
          if (!name || !pointA || !pointB) {{ alert("Falta el nombre o los dos puntos de la línea"); return; }}
          const body = {{ camera_name: curCamera, command_type: "create", name, movement,
                          x1: pointA.x, y1: pointA.y, x2: pointB.x, y2: pointB.y }};
          const resp = await fetch(`/api/lines/command?site_id=${{curSite}}`, {{
            method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(body),
          }});
          if (resp.ok) {{ alert("Comando encolado. Se aplica en el próximo ciclo de sync del mini PC."); location.reload(); }}
          else {{ alert("No se pudo encolar el comando: " + (await resp.text())); }}
        }}

        async function sendLineDelete(lineId) {{
          if (!confirm("¿Borrar esta línea? Se aplica en el próximo ciclo de sync del mini PC.")) return;
          const body = {{ camera_name: curCamera, command_type: "delete", line_id: lineId }};
          const resp = await fetch(`/api/lines/command?site_id=${{curSite}}`, {{
            method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(body),
          }});
          if (resp.ok) {{ alert("Comando de borrado encolado."); location.reload(); }}
          else {{ alert("No se pudo encolar el comando: " + (await resp.text())); }}
        }}
      </script>
    """
    return page_shell("Líneas", "lines", body)
