// Export of the current view (SVG, PNG, Mermaid source; SVG-rendered types export their SVG directly), download of the whole model (JSON, PlantUML)
// and import of an exported JSON document as a new diagram.
import { api, apiFetch } from "./api.js";
import { renderOffscreen } from "./canvas.js";
import { addMessage } from "./chatlog.js";
import { $ } from "./dom.js";
import { currentProject, loadDiagramList } from "./navigator.js";
import { exportName } from "./format.js";
import { carriedText } from "./sharedmodel.js";
import { focusQuery, state } from "./state.js";
import { viewOf } from "./types.js";
import { setStatus } from "./ui.js";
import { sanitizeSvg } from "./svgsafe.js";
import { t } from "./i18n.js";

let exportMenu = null;
let exportSeq = 0;

/** The view to export: the shown one (with highlights) or a clean re-render, as `{renderer, source}`. */
async function exportView() {
  if ($("exp-highlight").checked || !state.diagramId || state.compare) return state.view; // a comparison is its marks
  if (state.previewVersion != null) {
    return viewOf(await api(`/api/diagrams/${state.diagramId}/versions/${state.previewVersion}${focusQuery()}`));
  }
  return viewOf(await api(`/api/diagrams/${state.diagramId}/render${focusQuery()}`));
}

/** A standalone SVG string with explicit size from an SVG document rendered by the server. */
function standaloneSvg(source) {
  const node = sanitizeSvg(new DOMParser().parseFromString(source, "image/svg+xml").documentElement);
  const vb = (node.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  const width = vb[2] || Number(node.getAttribute("width")) || 800;
  const height = vb[3] || Number(node.getAttribute("height")) || 600;
  node.setAttribute("width", String(width));
  node.setAttribute("height", String(height));
  const text = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(node);
  return { text, width, height };
}

/** Render Mermaid source off-screen and return a standalone SVG string with explicit size. */
async function exportSvg(src) {
  // Plain SVG text labels instead of HTML (foreignObject): foreignObject taints the canvas used
  // for PNG export and is not understood by most vector tools. Mermaid turns <br/> into lines.
  // Both keys are needed: node labels follow flowchart.htmlLabels, edge labels the top-level one.
  const directive = '%%{init: {"htmlLabels": false, "flowchart": {"htmlLabels": false}}}%%\n';
  // Front matter (e.g. the swimlane layout of activity diagrams) must stay first, so the directive goes after it.
  const front = src.match(/^---\n[\s\S]*?\n---\n/)?.[0] || "";
  // Parsed (HTML parser, inert) and sanitized like the canvas (#197); XMLSerializer then writes a well-formed document.
  const node = await renderOffscreen(`arcviz-export-${++exportSeq}`, front + directive + src.slice(front.length));
  const vb = (node.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  const width = vb[2] || Number(node.getAttribute("width")) || 800;
  const height = vb[3] || Number(node.getAttribute("height")) || 600;
  node.setAttribute("width", String(width));
  node.setAttribute("height", String(height));
  node.removeAttribute("style");
  node.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  node.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
  const text = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(node);
  return { text, width, height };
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.rel = "noopener";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function svgToPng(svgText, width, height, scale = 2) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(new Blob([svgText], { type: "image/svg+xml;charset=utf-8" }));
    img.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = Math.ceil(width * scale); canvas.height = Math.ceil(height * scale);
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = getComputedStyle(document.body).backgroundColor || "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error(t("PNG konnte nicht erzeugt werden.")))), "image/png");
      } catch (err) { reject(err); } finally { URL.revokeObjectURL(url); }
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(t("SVG konnte nicht gerastert werden."))); };
    img.src = url;
  });
}

/** Copy text to the clipboard; falls back to a hidden textarea + execCommand where the async
 * Clipboard API is unavailable (plain-HTTP deployments are not a secure context). */
async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return; } catch { /* fall through */ }
  }
  const area = document.createElement("textarea");
  area.value = text; area.setAttribute("readonly", "");
  area.style.position = "fixed"; area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } finally { area.remove(); }
  if (!ok) throw new Error(t("Zwischenablage nicht verfügbar – Quelltext über „Quelltext“ anzeigen und manuell kopieren."));
}

async function runExport(kind) {
  exportMenu.open = false;
  if (!state.view) return;
  setStatus(t("Exportiere …"));
  // Snapshot the names now: the view (diagram, focus, preview) may change during the awaits below.
  const names = { svg: exportName(state, "svg"), png: exportName(state, "png") };
  try {
    const view = await exportView();
    if (kind === "mmd") {
      if (view.renderer !== "mermaid") throw new Error(t("Dieses Diagramm wird nicht mit Mermaid gezeichnet."));
      await copyText(view.source);
      setStatus(t("Mermaid-Quelltext kopiert."));
      return;
    }
    const { text, width, height } = view.renderer === "svg" ? standaloneSvg(view.source) : await exportSvg(view.source);
    if (kind === "svg") download(new Blob([text], { type: "image/svg+xml;charset=utf-8" }), names.svg);
    else download(await svgToPng(text, width, height, 2), names.png);
    setStatus("");
  } catch (err) {
    setStatus(t("Export fehlgeschlagen: {error}", { error: err.message || err }));
  }
}

/** Download the whole model (JSON document or PlantUML) of the shown state from GET /export. */
async function downloadModel(format) {
  exportMenu.open = false;
  if (!state.diagramId) return;
  const version = state.compare ? state.compare.target : state.previewVersion; // a comparison exports its newer side
  const query = `format=${format}${version != null ? `&version=${version}` : ""}`;
  try {
    const res = await apiFetch(`/api/diagrams/${encodeURIComponent(state.diagramId)}/export?${query}`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `${state.diagramId}.${format}`;
    download(await res.blob(), name);
    setStatus("");
  } catch (err) {
    setStatus(t("Export fehlgeschlagen: {error}", { error: err.message || err }));
  }
}

export function initExport() {
  exportMenu = $("export-menu");
  $("exp-json").addEventListener("click", () => downloadModel("json"));
  $("exp-puml").addEventListener("click", () => downloadModel("plantuml"));

  // Import of an exported JSON document as a new diagram.
  const importFile = $("import-file");
  $("btn-import").addEventListener("click", () => { importFile.value = ""; importFile.click(); });
  importFile.addEventListener("change", async () => {
    const file = importFile.files?.[0];
    if (!file) return;
    let document_;
    try { document_ = JSON.parse(await file.text()); } catch (err) { alert(t("„{file}“ ist keine gültige JSON-Datei: {error}", { file: file.name, error: err.message })); return; }
    try {
      const created = await api("/api/diagrams/import", { method: "POST", body: JSON.stringify({ document: document_, project: currentProject() }) });
      await loadDiagramList(created.diagram.id);
      const carried = carriedText(created.carried); // the linked elements that came along, or were dropped (#153)
      addMessage("system", t("„{file}“ als neues Diagramm „{title}“ ({id}) importiert.", { file: file.name, title: created.diagram.title, id: created.diagram.id }) + (carried ? ` ${carried}.` : ""));
    } catch (err) { alert(t("Import fehlgeschlagen: {error}", { error: err.message })); }
  });

  $("exp-svg").addEventListener("click", () => runExport("svg"));
  $("exp-png").addEventListener("click", () => runExport("png"));
  $("exp-mmd").addEventListener("click", () => runExport("mmd"));
  document.addEventListener("pointerdown", (e) => { if (exportMenu.open && !exportMenu.contains(e.target)) exportMenu.open = false; });
}
