/* lines.js - configuración interactiva de líneas virtuales sobre canvas */

const canvas = document.getElementById("lineCanvas");
const ctx = canvas.getContext("2d");

let currentCam = null;
let frameImg = null;      // Image() cargada
let imgW = 0, imgH = 0;   // dimensiones reales del frame
let pointA = null, pointB = null; // en coordenadas de imagen
let existingLines = [];

let mode = "line";        // "line" | "roi"
let existingRoi = [];     // puntos guardados en el servidor
let roiPoints = [];       // puntos en edición (antes de guardar)

function scaleFactors() {
  return { sx: canvas.width / imgW, sy: canvas.height / imgH };
}

function canvasToImage(cx, cy) {
  const { sx, sy } = scaleFactors();
  return { x: Math.round(cx / sx), y: Math.round(cy / sy) };
}

function imageToCanvas(ix, iy) {
  const { sx, sy } = scaleFactors();
  return { x: ix * sx, y: iy * sy };
}

function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (frameImg) {
    ctx.drawImage(frameImg, 0, 0, canvas.width, canvas.height);
  } else {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#8b949e";
    ctx.font = "16px sans-serif";
    ctx.fillText("Carga el frame de una cámara para empezar", 210, 225);
  }

  // Dibujar líneas existentes (azul)
  ctx.lineWidth = 3;
  existingLines.forEach((l) => {
    const a = imageToCanvas(l.x1, l.y1), b = imageToCanvas(l.x2, l.y2);
    ctx.strokeStyle = "#2f81f7";
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    ctx.fillStyle = "#2f81f7";
    ctx.font = "13px sans-serif";
    ctx.fillText(`${l.name} (${l.movement})`, a.x + 4, a.y - 6);
  });

  // ROI guardado (naranja, semitransparente)
  if (existingRoi.length >= 3) {
    ctx.beginPath();
    existingRoi.forEach((p, i) => {
      const c = imageToCanvas(p[0], p[1]);
      if (i === 0) ctx.moveTo(c.x, c.y); else ctx.lineTo(c.x, c.y);
    });
    ctx.closePath();
    ctx.fillStyle = "rgba(240,136,0,0.15)";
    ctx.fill();
    ctx.strokeStyle = "#f08800";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  // ROI en edición (amarillo)
  if (roiPoints.length > 0) {
    ctx.strokeStyle = "#e3b341";
    ctx.fillStyle = "#e3b341";
    ctx.lineWidth = 2;
    ctx.beginPath();
    roiPoints.forEach((p, i) => {
      const c = imageToCanvas(p[0], p[1]);
      if (i === 0) ctx.moveTo(c.x, c.y); else ctx.lineTo(c.x, c.y);
    });
    if (roiPoints.length >= 3) {
      const first = imageToCanvas(roiPoints[0][0], roiPoints[0][1]);
      ctx.setLineDash([5, 5]);
      ctx.lineTo(first.x, first.y);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    roiPoints.forEach((p) => {
      const c = imageToCanvas(p[0], p[1]);
      ctx.beginPath(); ctx.arc(c.x, c.y, 4, 0, Math.PI * 2); ctx.fill();
    });
  }

  // Línea en edición (verde)
  if (mode === "line" && pointA) {
    const a = imageToCanvas(pointA.x, pointA.y);
    ctx.fillStyle = "#3fb950";
    ctx.beginPath(); ctx.arc(a.x, a.y, 5, 0, Math.PI * 2); ctx.fill();
  }
  if (pointA && pointB) {
    const a = imageToCanvas(pointA.x, pointA.y), b = imageToCanvas(pointB.x, pointB.y);
    ctx.strokeStyle = "#3fb950";
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    ctx.fillStyle = "#3fb950";
    ctx.beginPath(); ctx.arc(b.x, b.y, 5, 0, Math.PI * 2); ctx.fill();
  }
}

canvas.addEventListener("click", (e) => {
  if (!frameImg) { toast("Primero carga el frame de una cámara", true); return; }
  const rect = canvas.getBoundingClientRect();
  const cx = (e.clientX - rect.left) * (canvas.width / rect.width);
  const cy = (e.clientY - rect.top) * (canvas.height / rect.height);
  const p = canvasToImage(cx, cy);

  if (mode === "roi") {
    roiPoints.push([p.x, p.y]);
    document.getElementById("saveRoi").disabled = roiPoints.length < 3;
    redraw();
    return;
  }

  if (!pointA || (pointA && pointB)) {
    pointA = p; pointB = null;
  } else {
    pointB = p;
  }
  updateCoords();
  redraw();
});

function updateCoords() {
  const el = document.getElementById("coords");
  const saveBtn = document.getElementById("saveLine");
  if (pointA && pointB) {
    el.textContent = `A(${pointA.x}, ${pointA.y}) → B(${pointB.x}, ${pointB.y})`;
    saveBtn.disabled = false;
  } else if (pointA) {
    el.textContent = `A(${pointA.x}, ${pointA.y}) → marca el punto B`;
    saveBtn.disabled = true;
  } else {
    el.textContent = "Coordenadas: —";
    saveBtn.disabled = true;
  }
}

async function loadCamsSelect() {
  const cams = await API.get("/api/cameras");
  const sel = document.getElementById("camSelect");
  sel.innerHTML = cams.map((c) => `<option value="${c.id}">${escapeHtml(c.name)} (#${c.id})</option>`).join("");
  if (cams.length) { currentCam = parseInt(sel.value); await loadLines(); }
}

async function loadMovements() {
  await loadMeta();
  const sel = document.getElementById("lineMovement");
  sel.innerHTML = META.movements.map((m) => `<option value="${m}">${m}</option>`).join("");
}

async function loadFrame() {
  if (!currentCam) return;
  const img = new Image();
  img.onload = () => {
    frameImg = img;
    imgW = img.naturalWidth;
    imgH = img.naturalHeight;
    // Ajustar el canvas a proporción de la imagen (ancho fijo 800)
    canvas.width = 800;
    canvas.height = Math.round(800 * imgH / imgW);
    redraw();
    toast("Frame cargado");
  };
  img.onerror = () => toast("No se pudo obtener el frame (¿cámara conectada?)", true);
  img.src = `/api/frame/${currentCam}?t=${Date.now()}`;
}

async function loadLines() {
  if (!currentCam) return;
  existingLines = await API.get(`/api/lines/${currentCam}`);
  renderLineTable();
  redraw();
}

async function loadRoi() {
  if (!currentCam) return;
  const r = await API.get(`/api/cameras/${currentCam}/roi`);
  existingRoi = r.points || [];
  roiPoints = [];
  document.getElementById("saveRoi").disabled = true;
  redraw();
}

async function saveRoi() {
  if (!currentCam || roiPoints.length < 3) return;
  try {
    await API.put(`/api/cameras/${currentCam}/roi`, { points: roiPoints });
    toast("ROI guardado");
    await loadRoi();
  } catch (e) { toast(e.message, true); }
}

async function clearRoi() {
  if (!currentCam) return;
  if (!confirm("¿Borrar la zona de interés de esta cámara? Volverá a detectar en todo el frame.")) return;
  try {
    await API.del(`/api/cameras/${currentCam}/roi`);
    toast("ROI eliminado");
    await loadRoi();
  } catch (e) { toast(e.message, true); }
}

function setMode(newMode) {
  mode = newMode;
  document.getElementById("modeGroup").querySelectorAll(".opt-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
  document.getElementById("lineForm").classList.toggle("hidden", mode !== "line");
  document.getElementById("roiForm").classList.toggle("hidden", mode !== "roi");
  document.getElementById("hint").textContent = mode === "roi"
    ? "Haz clic para agregar cada punto del polígono ROI."
    : "Haz clic para marcar el punto A, luego el punto B.";
  pointA = pointB = null;
  roiPoints = [];
  updateCoords();
  redraw();
}

function renderLineTable() {
  const tbody = document.getElementById("lineTable");
  if (existingLines.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">Sin líneas.</td></tr>`;
    return;
  }
  tbody.innerHTML = existingLines.map((l) =>
    `<tr>
      <td>${escapeHtml(l.name)}</td>
      <td>${escapeHtml(l.movement)}</td>
      <td class="muted">${l.x1},${l.y1} → ${l.x2},${l.y2}</td>
      <td class="right"><button class="btn sm red" onclick="delLine(${l.id})">Eliminar</button></td>
    </tr>`
  ).join("");
}

async function saveLine() {
  if (!currentCam || !pointA || !pointB) return;
  const name = document.getElementById("lineName").value.trim();
  const movement = document.getElementById("lineMovement").value;
  if (!name) { toast("Ingresa un nombre para la línea", true); return; }
  try {
    await API.post("/api/lines", {
      camera_id: currentCam, name,
      x1: pointA.x, y1: pointA.y, x2: pointB.x, y2: pointB.y, movement,
    });
    toast("Línea guardada");
    pointA = pointB = null;
    document.getElementById("lineName").value = "";
    updateCoords();
    await loadLines();
  } catch (e) { toast(e.message, true); }
}

async function delLine(id) {
  if (!confirm("¿Eliminar esta línea?")) return;
  try {
    await API.del(`/api/lines/${id}`);
    toast("Línea eliminada");
    await loadLines();
  } catch (e) { toast(e.message, true); }
}

document.getElementById("camSelect").addEventListener("change", async (e) => {
  currentCam = parseInt(e.target.value);
  frameImg = null; pointA = pointB = null; roiPoints = [];
  updateCoords();
  await loadLines();
  await loadRoi();
});
document.getElementById("loadFrame").addEventListener("click", loadFrame);
document.getElementById("saveLine").addEventListener("click", saveLine);
document.getElementById("resetDraw").addEventListener("click", () => {
  pointA = pointB = null; updateCoords(); redraw();
});

document.getElementById("modeGroup").querySelectorAll(".opt-btn").forEach((b) => {
  b.addEventListener("click", () => setMode(b.dataset.mode));
});
document.getElementById("saveRoi").addEventListener("click", saveRoi);
document.getElementById("clearRoi").addEventListener("click", clearRoi);
document.getElementById("undoRoiPoint").addEventListener("click", () => {
  roiPoints.pop();
  document.getElementById("saveRoi").disabled = roiPoints.length < 3;
  redraw();
});

(async function init() {
  await loadMovements();
  await loadCamsSelect();
  await loadRoi();
  redraw();
})();
