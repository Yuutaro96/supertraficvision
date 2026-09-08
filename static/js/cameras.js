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
  document.getElementById("formTitle").textContent = `Editar cámara #${id}`;
  document.getElementById("cancelEdit").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

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
  if (!name || !url) { toast("Nombre y URL son obligatorios", true); return; }
  try {
    if (editingId) {
      await API.put(`/api/cameras/${editingId}`, { name, url, active });
      toast("Cámara actualizada");
    } else {
      await API.post("/api/cameras", { name, url, active });
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
