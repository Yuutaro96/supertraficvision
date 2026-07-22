/* dashboard.js - vista en tiempo real de cámaras y conteos */

let crossings = [];

async function renderTotals() {
  const status = await API.get("/api/status");
  await loadMeta();
  const totals = status.totals_today || {};
  const wrap = document.getElementById("totals");
  wrap.innerHTML = "";
  // Mostrar todas las clases conocidas, incluso con 0
  META.classes.forEach((c) => {
    const val = totals[c.name] || 0;
    const div = document.createElement("div");
    div.className = "stat";
    div.innerHTML = `<div class="emoji">${c.emoji}</div>
      <div class="value">${val}</div>
      <div class="label">${c.name}</div>`;
    wrap.appendChild(div);
  });
  return status;
}

function cameraTile(cam) {
  const connClass = cam.connected ? "on" : "off";
  const connText = cam.connected ? "Conectada" : "Desconectada";
  const chips = (cam.lines || []).map((l) =>
    `<span class="chip">${l.name}: <b>IN ${l.in_count}</b> / OUT ${l.out_count}</span>`
  ).join("");
  return `<div class="card cam-tile" data-cam="${cam.id}">
    <div class="cam-head">
      <span class="cam-name">${cam.name}</span>
      <span class="badge-conn ${connClass}">
        <span class="status-dot ${cam.connected ? "on" : ""}"></span>${connText} · ${cam.fps} FPS
      </span>
    </div>
    <img class="cam-video" src="/stream/${cam.id}" alt="${cam.name}"
         onerror="this.style.opacity=0.3">
    <div class="cam-counters">${chips || '<span class="muted">Sin líneas configuradas</span>'}</div>
  </div>`;
}

async function renderCameras(status) {
  const cams = status.cameras || [];
  const wrap = document.getElementById("cameras");
  const noCams = document.getElementById("noCams");
  if (cams.length === 0) {
    wrap.innerHTML = "";
    noCams.classList.remove("hidden");
    return;
  }
  noCams.classList.add("hidden");
  // Reconstruir solo si cambia el conjunto de cámaras (evita recargar <img>)
  const existing = new Set([...wrap.querySelectorAll(".cam-tile")].map(t => t.dataset.cam));
  const incoming = new Set(cams.map(c => String(c.id)));
  const sameSet = existing.size === incoming.size && [...existing].every(id => incoming.has(id));
  if (!sameSet) {
    wrap.innerHTML = cams.map(cameraTile).join("");
  } else {
    // Actualizar sólo textos (conexión, fps, contadores) sin tocar el <img>
    cams.forEach((cam) => {
      const tile = wrap.querySelector(`.cam-tile[data-cam="${cam.id}"]`);
      if (!tile) return;
      const badge = tile.querySelector(".badge-conn");
      badge.className = "badge-conn " + (cam.connected ? "on" : "off");
      badge.innerHTML = `<span class="status-dot ${cam.connected ? "on" : ""}"></span>${cam.connected ? "Conectada" : "Desconectada"} · ${cam.fps} FPS`;
      const counters = tile.querySelector(".cam-counters");
      counters.innerHTML = (cam.lines || []).map((l) =>
        `<span class="chip">${l.name}: <b>IN ${l.in_count}</b> / OUT ${l.out_count}</span>`
      ).join("") || '<span class="muted">Sin líneas configuradas</span>';
    });
  }
}

function renderCrossings() {
  const tbody = document.getElementById("crossings");
  tbody.innerHTML = crossings.slice(0, 50).map((c) =>
    `<tr>
      <td>${fmtTimeShort(c.timestamp)}</td>
      <td>${c.camera_name || c.camera_id}</td>
      <td>${c.line_name || ""}</td>
      <td>${c.movement || ""}</td>
      <td>${c.emoji || ""} ${c.class_name}</td>
      <td><span class="tag ${c.direction === "IN" ? "in" : "out"}">${c.direction}</span></td>
    </tr>`
  ).join("");
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/counts`);
  const dot = document.getElementById("wsDot");
  ws.onopen = () => { dot.classList.add("on"); };
  ws.onclose = () => {
    dot.classList.remove("on");
    setTimeout(connectWS, 3000); // reconexión
  };
  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "count") {
        crossings.unshift(msg.data);
        if (crossings.length > 100) crossings.pop();
        renderCrossings();
      }
    } catch (e) {}
  };
  // ping periódico para mantener viva la conexión
  setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 25000);
}

async function loadInitialCrossings() {
  try {
    const rows = await API.get("/api/counts?limit=50");
    crossings = rows.map((r) => ({
      timestamp: r.timestamp,
      camera_name: r.camera_name,
      camera_id: r.camera_id,
      line_name: r.line_name,
      movement: r.movement,
      class_name: r.class_name,
      emoji: classEmoji(r.class_name),
      direction: r.direction,
    }));
    renderCrossings();
  } catch (e) {}
}

async function refresh() {
  try {
    const status = await renderTotals();
    await renderCameras(status);
  } catch (e) {
    console.error(e);
  }
}

(async function init() {
  await loadMeta();
  await refresh();
  await loadInitialCrossings();
  connectWS();
  setInterval(refresh, 3000);
})();
