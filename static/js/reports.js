/* reports.js - filtros, gráficas Chart.js y exportación CSV */

let hourChart = null, classChart = null;

function buildQuery() {
  const params = new URLSearchParams();
  const cam = document.getElementById("fCamera").value;
  const cls = document.getElementById("fClass").value;
  const from = document.getElementById("fFrom").value;
  const to = document.getElementById("fTo").value;
  if (cam) params.set("camera_id", cam);
  if (cls) params.set("class_name", cls);
  if (from) params.set("date_from", from);
  if (to) params.set("date_to", to);
  return params;
}

async function loadFilters() {
  const [cams] = await Promise.all([API.get("/api/cameras"), loadMeta()]);
  const camSel = document.getElementById("fCamera");
  camSel.innerHTML = `<option value="">Todas</option>` +
    cams.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  const clsSel = document.getElementById("fClass");
  clsSel.innerHTML = `<option value="">Todas</option>` +
    META.classes.map((c) => `<option value="${c.name}">${c.emoji} ${c.name}</option>`).join("");
}

async function loadReport() {
  const params = buildQuery();
  params.set("limit", "1000");
  const rows = await API.get("/api/counts?" + params.toString());
  document.getElementById("rowCount").textContent = rows.length;
  const tbody = document.getElementById("reportTable");
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted">Sin registros para los filtros seleccionados.</td></tr>`;
  } else {
    tbody.innerHTML = rows.map((r) =>
      `<tr>
        <td>${fmtTime(r.timestamp)}</td>
        <td>${escapeHtml(r.camera_name || r.camera_id)}</td>
        <td>${escapeHtml(r.line_name || "")}</td>
        <td>${escapeHtml(r.movement || "")}</td>
        <td>${classEmoji(r.class_name)} ${escapeHtml(r.class_name)}</td>
        <td><span class="tag ${r.direction === "IN" ? "in" : "out"}">${escapeHtml(r.direction)}</span></td>
        <td>${r.count}</td>
      </tr>`
    ).join("");
  }
  await loadCharts(rows);
}

async function loadCharts(rows) {
  const sumParams = buildQuery();
  const summary = await API.get("/api/counts/summary?" + sumParams.toString());

  // ---- Histograma por hora (apilado por clase) ----
  const byHour = summary.by_hour || [];
  const hours = [...new Set(byHour.map((r) => r.hour))].sort();
  const classes = [...new Set(byHour.map((r) => r.class_name))];
  const palette = ["#2f81f7", "#3fb950", "#d29922", "#f85149", "#a371f7", "#56d364"];
  const datasets = classes.map((cls, i) => ({
    label: cls,
    data: hours.map((h) => {
      const rec = byHour.find((r) => r.hour === h && r.class_name === cls);
      return rec ? rec.total : 0;
    }),
    backgroundColor: palette[i % palette.length],
  }));
  const labels = hours.map((h) => h.replace("T", " ") + ":00");

  if (hourChart) hourChart.destroy();
  hourChart = new Chart(document.getElementById("hourChart"), {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#e6edf3" } } },
      scales: {
        x: { stacked: true, ticks: { color: "#8b949e" }, grid: { color: "#2b3444" } },
        y: { stacked: true, ticks: { color: "#8b949e" }, grid: { color: "#2b3444" } },
      },
    },
  });

  // ---- Distribución por clase (dona) ----
  const classTotals = {};
  rows.forEach((r) => { classTotals[r.class_name] = (classTotals[r.class_name] || 0) + r.count; });
  const clsLabels = Object.keys(classTotals);
  if (classChart) classChart.destroy();
  classChart = new Chart(document.getElementById("classChart"), {
    type: "doughnut",
    data: {
      labels: clsLabels,
      datasets: [{
        data: clsLabels.map((c) => classTotals[c]),
        backgroundColor: palette,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "right", labels: { color: "#e6edf3" } } },
    },
  });
}

function exportCsv() {
  const params = buildQuery();
  window.location = "/api/counts/export/csv?" + params.toString();
}

document.getElementById("applyFilter").addEventListener("click", loadReport);
document.getElementById("exportCsv").addEventListener("click", exportCsv);

(async function init() {
  await loadFilters();
  await loadReport();
})();
