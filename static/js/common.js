/* common.js - utilidades compartidas por todas las páginas */

const API = {
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async put(url, body) {
    const r = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async del(url) {
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
};

let _toastTimer = null;
function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = "toast show" + (isError ? " err" : "");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = "toast"; }, 3000);
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("es-EC", { hour12: false });
}

function fmtTimeShort(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleTimeString("es-EC", { hour12: false });
}

// Cache de metadatos (clases + movimientos)
let META = null;
async function loadMeta() {
  if (META) return META;
  META = await API.get("/api/meta");
  return META;
}

function classEmoji(name) {
  if (!META) return "";
  const c = META.classes.find((x) => x.name === name);
  return c ? c.emoji : "";
}
