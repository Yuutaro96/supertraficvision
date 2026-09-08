/* settings.js - lógica de la página de Configuración (Fase 1) */

// Estado local de la configuración (se sincroniza con el backend)
let CFG = {
  confidence: 0.5,
  model_size: "n",
  target_fps: 15,
  device: "auto",
  iou: 0.5,
  imgsz: 640,
  class_confidence: {},
  track_activation_threshold: 0.25,
  lost_track_buffer: 30,
  minimum_consecutive_frames: 1,
  display_fps: 25,
  stream_quality: 80,
  stream_resolution: "original",
  reconnect_delay: 5,
  data_retention_days: 90,
  active_classes: [0, 1, 2, 3, 5, 7],
};

const CLASS_NAMES = {
  0: "🚶 Persona",
  1: "🚲 Bicicleta",
  2: "🚗 Auto",
  3: "🏍️ Moto",
  5: "🚌 Bus",
  7: "🚛 Camión",
};

let _statusTimer = null;

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------
function $(id) { return document.getElementById(id); }

function setActiveInGroup(container, matchFn) {
  container.querySelectorAll(".opt-btn").forEach((b) => {
    b.classList.toggle("active", matchFn(b));
  });
}

// ---------------------------------------------------------------------------
// Pestañas
// ---------------------------------------------------------------------------
function initTabs() {
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      tabs.forEach((t) => t.classList.toggle("active", t === tab));
      document.querySelectorAll(".tab-panel").forEach((p) => {
        p.classList.toggle("active", p.id === "panel-" + name);
      });
      // Refrescar datos al entrar a ciertas pestañas
      if (name === "db") loadDbStats();
      if (name === "sistema") refreshStatus();
    });
  });
}

// ---------------------------------------------------------------------------
// Cargar configuración inicial
// ---------------------------------------------------------------------------
async function loadSettings() {
  try {
    const s = await API.get("/api/settings");
    CFG = Object.assign(CFG, s);
    applyCfgToUI();
  } catch (e) {
    toast("Error al cargar la configuración: " + e.message, true);
  }
}

function renderClassConfidenceGrid() {
  const wrap = $("classConfidenceGrid");
  wrap.innerHTML = Object.entries(CLASS_NAMES).map(([id, label]) => {
    const val = CFG.class_confidence[id] != null ? CFG.class_confidence[id] : CFG.confidence;
    return `<div class="slider-row">
      <label>${label}: <span class="slider-val" data-conf-val="${id}">${Number(val).toFixed(2)}</span></label>
      <input type="range" class="class-conf-slider" data-class-id="${id}"
             min="0.1" max="0.95" step="0.05" value="${val}">
    </div>`;
  }).join("");
  wrap.querySelectorAll(".class-conf-slider").forEach((el) => {
    el.addEventListener("input", (e) => {
      document.querySelector(`[data-conf-val="${e.target.dataset.classId}"]`).textContent =
        Number(e.target.value).toFixed(2);
    });
  });
}

function applyCfgToUI() {
  // Modelo
  document.querySelectorAll("#modelGrid .model-card").forEach((c) => {
    c.classList.toggle("active", c.dataset.model === CFG.model_size);
  });

  // Sliders
  $("confidence").value = CFG.confidence;
  $("confVal").textContent = Number(CFG.confidence).toFixed(2);
  $("iou").value = CFG.iou;
  $("iouVal").textContent = Number(CFG.iou).toFixed(2);
  $("imgsz").value = CFG.imgsz;
  $("imgszVal").textContent = CFG.imgsz;
  $("track_activation_threshold").value = CFG.track_activation_threshold;
  $("trackActivationVal").textContent = Number(CFG.track_activation_threshold).toFixed(2);
  $("lost_track_buffer").value = CFG.lost_track_buffer;
  $("lostBufferVal").textContent = CFG.lost_track_buffer;
  $("minimum_consecutive_frames").value = CFG.minimum_consecutive_frames;
  $("minFramesVal").textContent = CFG.minimum_consecutive_frames;
  renderClassConfidenceGrid();
  $("target_fps").value = CFG.target_fps;
  $("targetFpsVal").textContent = CFG.target_fps;
  $("display_fps").value = CFG.display_fps;
  $("displayFpsVal").textContent = CFG.display_fps;
  $("stream_quality").value = CFG.stream_quality;
  $("qualityVal").textContent = CFG.stream_quality + "%";
  $("reconnect_delay").value = CFG.reconnect_delay;
  $("reconnectVal").textContent = CFG.reconnect_delay + " seg";

  // Dispositivo
  setActiveInGroup($("deviceGroup"), (b) => b.dataset.device === CFG.device);

  // Clases
  document.querySelectorAll("#classGrid input[type=checkbox]").forEach((chk) => {
    chk.checked = CFG.active_classes.includes(parseInt(chk.value, 10));
  });

  // Resolución
  setActiveInGroup($("resGroup"), (b) => b.dataset.res === CFG.stream_resolution);

  // Retención
  setActiveInGroup($("retentionGroup"), (b) => parseInt(b.dataset.days, 10) === CFG.data_retention_days);
  $("retentionWarn").classList.toggle("hidden", CFG.data_retention_days !== 0);
}

// ---------------------------------------------------------------------------
// Recolectar valores de la UI -> CFG
// ---------------------------------------------------------------------------
function collectCfg() {
  const activeModel = document.querySelector("#modelGrid .model-card.active");
  const activeDevice = $("deviceGroup").querySelector(".opt-btn.active");
  const activeRes = $("resGroup").querySelector(".opt-btn.active");
  const activeRet = $("retentionGroup").querySelector(".opt-btn.active");

  const classes = [];
  document.querySelectorAll("#classGrid input[type=checkbox]").forEach((chk) => {
    if (chk.checked) classes.push(parseInt(chk.value, 10));
  });

  const classConfidence = {};
  document.querySelectorAll(".class-conf-slider").forEach((el) => {
    classConfidence[el.dataset.classId] = parseFloat(el.value);
  });

  return {
    model_size: activeModel ? activeModel.dataset.model : CFG.model_size,
    confidence: parseFloat($("confidence").value),
    iou: parseFloat($("iou").value),
    imgsz: parseInt($("imgsz").value, 10),
    class_confidence: classConfidence,
    track_activation_threshold: parseFloat($("track_activation_threshold").value),
    lost_track_buffer: parseInt($("lost_track_buffer").value, 10),
    minimum_consecutive_frames: parseInt($("minimum_consecutive_frames").value, 10),
    target_fps: parseInt($("target_fps").value, 10),
    display_fps: parseInt($("display_fps").value, 10),
    stream_quality: parseInt($("stream_quality").value, 10),
    stream_resolution: activeRes ? activeRes.dataset.res : CFG.stream_resolution,
    reconnect_delay: parseInt($("reconnect_delay").value, 10),
    device: activeDevice ? activeDevice.dataset.device : CFG.device,
    data_retention_days: activeRet ? parseInt(activeRet.dataset.days, 10) : CFG.data_retention_days,
    active_classes: classes.length ? classes : CFG.active_classes,
  };
}

async function saveSettings() {
  const payload = collectCfg();
  try {
    const s = await API.post("/api/settings", payload);
    CFG = Object.assign(CFG, s);
    applyCfgToUI();
    toast("✅ Configuración guardada");
  } catch (e) {
    toast("Error al guardar: " + e.message, true);
  }
}

// ---------------------------------------------------------------------------
// Listeners de controles
// ---------------------------------------------------------------------------
function initControls() {
  // Modelo
  document.querySelectorAll("#modelGrid .model-card").forEach((c) => {
    c.addEventListener("click", () => {
      document.querySelectorAll("#modelGrid .model-card").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
    });
  });

  // Sliders con display en vivo
  $("confidence").addEventListener("input", (e) => { $("confVal").textContent = Number(e.target.value).toFixed(2); });
  $("iou").addEventListener("input", (e) => { $("iouVal").textContent = Number(e.target.value).toFixed(2); });
  $("imgsz").addEventListener("input", (e) => { $("imgszVal").textContent = e.target.value; });
  $("track_activation_threshold").addEventListener("input", (e) => {
    $("trackActivationVal").textContent = Number(e.target.value).toFixed(2);
  });
  $("lost_track_buffer").addEventListener("input", (e) => { $("lostBufferVal").textContent = e.target.value; });
  $("minimum_consecutive_frames").addEventListener("input", (e) => { $("minFramesVal").textContent = e.target.value; });
  $("target_fps").addEventListener("input", (e) => { $("targetFpsVal").textContent = e.target.value; });
  $("display_fps").addEventListener("input", (e) => { $("displayFpsVal").textContent = e.target.value; });
  $("stream_quality").addEventListener("input", (e) => { $("qualityVal").textContent = e.target.value + "%"; });
  $("reconnect_delay").addEventListener("input", (e) => { $("reconnectVal").textContent = e.target.value + " seg"; });

  // Dispositivo (grupo de botones)
  $("deviceGroup").querySelectorAll(".opt-btn").forEach((b) => {
    b.addEventListener("click", () => {
      setActiveInGroup($("deviceGroup"), (x) => x === b);
      updateDeviceBadge();
    });
  });

  // Resolución
  $("resGroup").querySelectorAll(".opt-btn").forEach((b) => {
    b.addEventListener("click", () => setActiveInGroup($("resGroup"), (x) => x === b));
  });

  // Retención
  $("retentionGroup").querySelectorAll(".opt-btn").forEach((b) => {
    b.addEventListener("click", () => {
      setActiveInGroup($("retentionGroup"), (x) => x === b);
      const days = parseInt(b.dataset.days, 10);
      $("retentionWarn").classList.toggle("hidden", days !== 0);
    });
  });

  // Guardar
  $("saveBtn").addEventListener("click", saveSettings);

  // Limpieza de datos
  $("cleanBtn").addEventListener("click", () => {
    openModal(
      "Limpiar datos antiguos",
      "Se eliminarán permanentemente los registros más antiguos que el periodo de retención seleccionado. ¿Deseas continuar?",
      doCleanData
    );
  });

  // Probar / recargar modelo
  $("testModelBtn").addEventListener("click", doTestModel);
  $("reloadModelBtn").addEventListener("click", doReloadModel);
}

// ---------------------------------------------------------------------------
// GPU / dispositivo
// ---------------------------------------------------------------------------
let _gpu = null;
async function loadGpuInfo() {
  try {
    _gpu = await API.get("/api/settings/gpu-info");
  } catch (e) {
    _gpu = null;
  }
  updateDeviceBadge();
}

function updateDeviceBadge() {
  const badge = $("deviceBadge");
  if (!_gpu) { badge.textContent = "No se pudo comprobar el dispositivo."; badge.className = "device-badge"; return; }
  if (_gpu.cuda_available) {
    let txt = "✅ GPU activa: " + (_gpu.gpu_name || "NVIDIA CUDA");
    if (_gpu.vram_total_mb) txt += ` (${(_gpu.vram_total_mb / 1024).toFixed(1)} GB)`;
    badge.textContent = txt;
    badge.className = "device-badge ok";
  } else {
    badge.textContent = "⚠️ GPU no disponible, usando CPU. Para usar GPU instala PyTorch con CUDA.";
    badge.className = "device-badge warn";
  }
}

// ---------------------------------------------------------------------------
// Base de datos
// ---------------------------------------------------------------------------
async function loadDbStats() {
  try {
    const s = await API.get("/api/settings/db-stats");
    $("dbTotal").textContent = (s.total_records || 0).toLocaleString("es-EC");
    $("dbSize").textContent = (s.size_mb != null ? s.size_mb : 0) + " MB";
    $("dbOldest").textContent = s.oldest ? fmtTime(s.oldest) : "—";
    $("dbNewest").textContent = s.newest ? fmtTime(s.newest) : "—";
    if (s.retention_days && s.retention_days > 0) {
      $("removableInfo").textContent =
        `Con retención de ${s.retention_days} días se eliminarían ${(s.removable_records || 0).toLocaleString("es-EC")} registros.`;
    } else {
      $("removableInfo").textContent = "Retención indefinida: no se eliminará ningún registro.";
    }
  } catch (e) {
    toast("Error al cargar estadísticas: " + e.message, true);
  }
}

async function doCleanData() {
  try {
    const r = await API.post("/api/settings/clean-data", {});
    toast("🗑️ " + (r.message || "Limpieza completada"));
    loadDbStats();
  } catch (e) {
    toast("Error al limpiar: " + e.message, true);
  }
}

// ---------------------------------------------------------------------------
// Modelo (probar / recargar)
// ---------------------------------------------------------------------------
async function doTestModel() {
  const btn = $("testModelBtn");
  const prev = btn.textContent;
  btn.disabled = true; btn.textContent = "⏳ Probando…";
  try {
    const r = await API.post("/api/settings/test-model", {});
    toast((r.ok ? "✅ " : "⚠️ ") + (r.message || ""), !r.ok);
  } catch (e) {
    toast("Error al probar el modelo: " + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = prev;
  }
}

async function doReloadModel() {
  const btn = $("reloadModelBtn");
  const prev = btn.textContent;
  btn.disabled = true; btn.textContent = "⏳ Recargando…";
  try {
    const r = await API.post("/api/settings/reload-model", {});
    toast((r.ok ? "🔄 " : "⚠️ ") + (r.message || ""), !r.ok);
    refreshStatus();
  } catch (e) {
    toast("Error al recargar el modelo: " + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = prev;
  }
}

// ---------------------------------------------------------------------------
// Estado del sistema (Sistema tab)
// ---------------------------------------------------------------------------
async function refreshStatus() {
  try {
    const st = await API.get("/api/status");
    const cams = st.cameras || [];
    const connected = cams.filter((c) => c.connected).length;
    const compute = st.compute || {};
    const devLabel = compute.cuda_available
      ? "⚡ GPU: " + (compute.gpu_name || "CUDA")
      : "💻 CPU";

    $("sysDevice").textContent = devLabel;
    $("sysCams").textContent = `${connected} de ${cams.length}`;

    const fpsList = cams.filter((c) => c.connected).map((c) => c.fps || 0);
    const avgFps = fpsList.length ? (fpsList.reduce((a, b) => a + b, 0) / fpsList.length) : 0;
    $("sysFps").textContent = avgFps.toFixed(1);

    let health = "✅ Operativo";
    if (cams.length === 0) health = "➖ Sin cámaras";
    else if (connected === 0) health = "⚠️ Sin conexión";
    else if (connected < cams.length) health = "⚠️ Parcial";
    $("sysHealth").textContent = health;

    // Info del modelo
    const s = st.settings || CFG;
    $("infoModel").textContent = "YOLOv8" + (s.model_size || "n");
    const classes = (s.active_classes || CFG.active_classes).map((c) => CLASS_NAMES[c] || c).join(", ");
    $("infoClasses").textContent = classes || "—";
    $("infoDevice").textContent = compute.device || s.device || "—";
    $("infoGpu").textContent = compute.cuda_available
      ? (compute.gpu_name || "Sí")
      : "No detectada";
  } catch (e) {
    // silencioso en polling
  }
}

// ---------------------------------------------------------------------------
// Modal de confirmación
// ---------------------------------------------------------------------------
let _modalAction = null;
function openModal(title, msg, action) {
  $("modalTitle").textContent = title;
  $("modalMsg").textContent = msg;
  _modalAction = action;
  $("modal").classList.remove("hidden");
}
function closeModal() {
  $("modal").classList.add("hidden");
  _modalAction = null;
}
function initModal() {
  $("modalCancel").addEventListener("click", closeModal);
  $("modalOk").addEventListener("click", () => {
    const act = _modalAction;
    closeModal();
    if (act) act();
  });
  $("modal").addEventListener("click", (e) => {
    if (e.target === $("modal")) closeModal();
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
  initTabs();
  initControls();
  initModal();
  await loadSettings();
  await loadGpuInfo();
  await loadDbStats();
  await refreshStatus();
  _statusTimer = setInterval(refreshStatus, 3000);
}

document.addEventListener("DOMContentLoaded", init);
