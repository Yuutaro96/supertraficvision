/* cameras.js - CRUD de cámaras */

let editingId = null;

async function loadCameras() {
  const [cams, status] = await Promise.all([
    API.get("/api/cameras"),
    API.get("/api/status").catch(() => ({ cameras: [] })),
  ]);
  const statusMap = {};
  (status.cameras || []).forEach((s) => { statusMap[s.id] = s; });

  const tbody = document.getElementById("camTable");
  if (cams.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted">No hay cámaras registradas.</td></tr>`;
    return;
  }
  tbody.innerHTML = cams.map((c) => {
    const st = statusMap[c.id];
    const conn = st && st.connected;
    const connBadge = c.active
      ? `<span class="badge-conn ${conn ? "on" : "off"}"><span class="status-dot ${conn ? "on" : ""}"></span>${conn ? "Conectada" : "Desconectada"}</span>`
      : `<span class="muted">—</span>`;
    return `<tr>
      <td>${c.id}</td>
      <td>${escapeHtml(c.name)}</td>
      <td class="muted" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(c.url)}</td>
      <td><span class="tag ${c.active ? "in" : "out"}">${c.active ? "Activa" : "Inactiva"}</span></td>
      <td>${connBadge}</td>
      <td>${st ? st.fps : 0}</td>
      <td class="right">
        ${c.ptz_capable ? `<button class="icon-btn" title="Control PTZ" onclick="openPtz(${c.id})">🕹️</button>` : ""}
        <button class="icon-btn" title="Editar" onclick="editCam(${c.id})">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20l1-4.2L15.8 5 19 8.2 8.2 19 4 20Z"/></svg>
        </button>
        <button class="icon-btn red" title="Eliminar" onclick="delCam(${c.id})">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13h10l1-13"/></svg>
        </button>
      </td>
    </tr>`;
  }).join("");
  window._cams = cams;
}

function resetForm() {
  editingId = null;
  document.getElementById("camId").value = "";
  document.getElementById("camName").value = "";
  document.getElementById("camUrl").value = "";
  document.getElementById("camActive").checked = true;
  document.getElementById("camPtz").checked = false;
  document.getElementById("camProtocol").value = "ezviz_cloud";
  document.getElementById("camDeviceSerial").value = "";
  document.getElementById("ptzConfigFields").classList.add("hidden");
  document.getElementById("formTitle").textContent = "Nueva cámara";
  document.getElementById("cancelEdit").classList.add("hidden");
}

function editCam(id) {
  const cam = (window._cams || []).find((c) => c.id === id);
  if (!cam) return;
  editingId = id;
  document.getElementById("camName").value = cam.name;
  document.getElementById("camUrl").value = cam.url;
  document.getElementById("camActive").checked = !!cam.active;
  document.getElementById("camPtz").checked = !!cam.ptz_capable;
  document.getElementById("ptzConfigFields").classList.toggle("hidden", !cam.ptz_capable);
  document.getElementById("camProtocol").value = cam.control_protocol || "ezviz_cloud";
  let cfg = {};
  try { cfg = cam.control_config ? JSON.parse(cam.control_config) : {}; } catch (e) { cfg = {}; }
  document.getElementById("camDeviceSerial").value = cfg.device_serial || "";
  document.getElementById("formTitle").textContent = `Editar cámara #${id}`;
  document.getElementById("cancelEdit").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.getElementById("camPtz").addEventListener("change", (e) => {
  document.getElementById("ptzConfigFields").classList.toggle("hidden", !e.target.checked);
});

async function delCam(id) {
  if (!confirm("¿Eliminar esta cámara y sus líneas configuradas?")) return;
  try {
    await API.del(`/api/cameras/${id}`);
    toast("Cámara eliminada");
    loadCameras();
  } catch (e) { toast(e.message, true); }
}

async function saveCam() {
  const name = document.getElementById("camName").value.trim();
  const url = document.getElementById("camUrl").value.trim();
  const active = document.getElementById("camActive").checked;
  const ptz_capable = document.getElementById("camPtz").checked;
  const control_protocol = ptz_capable ? document.getElementById("camProtocol").value : "none";
  const deviceSerial = document.getElementById("camDeviceSerial").value.trim();
  const control_config = ptz_capable ? JSON.stringify({ device_serial: deviceSerial }) : null;
  if (!name || !url) { toast("Nombre y URL son obligatorios", true); return; }
  if (ptz_capable && control_protocol === "ezviz_cloud" && !deviceSerial) {
    toast("Falta el número de serie EZVIZ", true); return;
  }
  const payload = { name, url, active, ptz_capable, control_protocol, control_config };
  try {
    if (editingId) {
      await API.put(`/api/cameras/${editingId}`, payload);
      toast("Cámara actualizada");
    } else {
      await API.post("/api/cameras", payload);
      toast("Cámara creada");
    }
    resetForm();
    loadCameras();
  } catch (e) { toast(e.message, true); }
}

document.getElementById("saveCam").addEventListener("click", saveCam);
document.getElementById("cancelEdit").addEventListener("click", resetForm);

loadCameras();
setInterval(loadCameras, 5000);

/* ---- Control PTZ ---- */
let ptzCameraId = null;

function openPtz(id) {
  const cam = (window._cams || []).find((c) => c.id === id);
  if (!cam) return;
  ptzCameraId = id;
  document.getElementById("ptzModalTitle").textContent = `Control PTZ · ${cam.name}`;
  document.getElementById("ptzOverlay").classList.remove("hidden");
}

function closePtz() {
  document.getElementById("ptzOverlay").classList.add("hidden");
  ptzCameraId = null;
}

async function ptzMove(direction) {
  if (!ptzCameraId) return;
  try {
    await API.post(`/api/cameras/${ptzCameraId}/ptz/move`, { direction, speed: 5 });
  } catch (e) { toast(e.message, true); }
}

async function ptzStop(direction) {
  if (!ptzCameraId) return;
  try {
    await API.post(`/api/cameras/${ptzCameraId}/ptz/stop`, { direction, speed: 5 });
  } catch (e) { /* silencioso: el usuario ya soltó el botón */ }
}

async function ptzZoom(direction) {
  if (!ptzCameraId) return;
  try {
    await API.post(`/api/cameras/${ptzCameraId}/ptz/zoom`, { direction });
  } catch (e) { toast(e.message, true); }
}

document.getElementById("ptzClose").addEventListener("click", closePtz);
document.getElementById("ptzOverlay").addEventListener("click", (e) => {
  if (e.target.id === "ptzOverlay") closePtz();
});
document.getElementById("ptzZoomIn").addEventListener("click", () => ptzZoom("in"));
document.getElementById("ptzZoomOut").addEventListener("click", () => ptzZoom("out"));

document.querySelectorAll(".ptz-btn").forEach((btn) => {
  const dir = btn.dataset.dir;
  const start = (e) => { e.preventDefault(); btn.classList.add("pressed"); ptzMove(dir); };
  const stop = (e) => { e.preventDefault(); btn.classList.remove("pressed"); ptzStop(dir); };
  btn.addEventListener("mousedown", start);
  btn.addEventListener("touchstart", start);
  btn.addEventListener("mouseup", stop);
  btn.addEventListener("mouseleave", stop);
  btn.addEventListener("touchend", stop);
});
