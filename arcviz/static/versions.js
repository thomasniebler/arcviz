// Versions: the version list, preview, restore, undo/redo (buttons and shortcuts), snapshots and the
// visual comparison of two versions (GET /compare: one merged render with marks).
import { canEdit } from "./access.js";
import { api } from "./api.js";
import { renderView } from "./canvas.js";
import { addMessage } from "./chatlog.js";
import { onDiagramShown, setFocus, showDiagram } from "./diagram.js";
import { $, el, option } from "./dom.js";
import { compareSummary, versionLabel, versionOptionLabel, versionTitle } from "./format.js";
import { refreshDiagramListLabel } from "./navigator.js";
import { focusQuery, state } from "./state.js";
import { viewOf } from "./types.js";
import { setStatus } from "./ui.js";
import { t } from "./i18n.js";

let compareEl = null;
const MORE = "more"; // the entry that loads the next page of older versions (spec: hardening.md, #114)

/** The selector is narrow in the top bar (its text ends with "…"): its tooltip names the selected version in full. */
function showSelectedTitle() {
  el.versionSelect.title = el.versionSelect.selectedOptions[0]?.title || t("Versionen");
}

function fillVersions(versions, undoTarget, redoTarget, total = versions.length) {
  el.versionSelect.innerHTML = "";
  [...versions].reverse().forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.version;
    opt.textContent = versionOptionLabel(v);
    opt.title = versionTitle(v);
    el.versionSelect.appendChild(opt);
  });
  state.versionsTotal = total;
  if (versions.length < total) el.versionSelect.appendChild(option(MORE, t("Ältere Versionen laden … ({count})", { count: total - versions.length })));
  showSelectedTitle();
  state.undoTarget = undoTarget ?? null;
  state.redoTarget = redoTarget ?? null;
  const readOnly = !canEdit(state.role); // viewers: the version list only (spec: workspaces-and-sharing.md)
  $("btn-undo").disabled = state.busy || readOnly || state.undoTarget == null;
  $("btn-redo").disabled = state.busy || readOnly || state.redoTarget == null;
  $("btn-restore").disabled = state.busy || readOnly || versions.length < 2;
  state.versions = versions;
}

/** The next page of older versions, prepended to the list (the newest page comes with every diagram response). A
 * failed request puts the list back on the current state and shows the error (the entry stays, to try again); an
 * empty page means there is nothing older any more (pruned meanwhile): the entry goes. */
async function loadOlderVersions() {
  const id = state.diagramId;
  const oldest = state.versions?.[0]?.version;
  const reset = () => { if (state.versions?.length) el.versionSelect.value = String(state.versions.at(-1).version); };
  if (oldest == null) { reset(); return; }
  let older;
  try {
    older = await api(`/api/diagrams/${id}/versions?before=${oldest}`);
  } catch (err) {
    reset();
    setStatus(t("Ältere Versionen konnten nicht geladen werden: {error}", { error: err.message }));
    return;
  }
  if (state.diagramId !== id) return; // switched diagrams meanwhile
  const versions = [...(older || []), ...state.versions];
  // nothing older came back: the history is shorter than announced (pruned meanwhile), so no entry any more
  const total = older?.length ? state.versionsTotal : versions.length;
  fillVersions(versions, state.undoTarget, state.redoTarget, total);
}

async function showCompare(base, target) {
  const id = state.diagramId;
  const query = `base=${base}${target != null ? `&target=${target}` : ""}${focusQuery("&")}`;
  let data;
  try { data = await api(`/api/diagrams/${id}/compare?${query}`); } catch (err) { setStatus(t("Vergleich fehlgeschlagen: {error}", { error: err.message })); return; }
  if (state.diagramId !== id) return; // switched diagrams meanwhile
  state.previewVersion = null;
  el.preview.classList.add("hidden");
  state.compare = { base, target };
  state.focus = data.focus;
  compareEl.text.textContent = compareSummary(base, target, data.diff);
  compareEl.banner.classList.remove("hidden");
  setStatus("");
  renderView(viewOf(data), { fit: true });
}

export function endCompare() {
  state.compare = null;
  compareEl.banner.classList.add("hidden");
}

export function initVersions() {
  compareEl = { dialog: $("compare-dialog"), base: $("compare-base"), target: $("compare-target"), banner: $("compare-banner"), text: $("compare-text") };
  // Every newly shown state refreshes the list and replaces a comparison.
  onDiagramShown((detail) => {
    if (detail.versions) fillVersions(detail.versions, detail.undo_target, detail.redo_target, detail.versions_total ?? detail.versions.length);
    endCompare();
  });

  $("btn-undo").addEventListener("click", async () => {
    try {
      const detail = await api(`/api/diagrams/${state.diagramId}/undo${focusQuery()}`, { method: "POST" });
      showDiagram(detail);
      addMessage("assistant", t("Rückgängig gemacht: Stand von v{from} wiederhergestellt (als v{to} gesichert).", { from: detail.version.restored_from, to: detail.version.version }));
      await refreshDiagramListLabel();
    } catch (err) { alert(err.message); }
  });
  $("btn-redo").addEventListener("click", async () => {
    try {
      const detail = await api(`/api/diagrams/${state.diagramId}/redo${focusQuery()}`, { method: "POST" });
      showDiagram(detail);
      addMessage("assistant", t("Wiederholt: Stand von v{from} wiederhergestellt (als v{to} gesichert).", { from: detail.version.restored_from, to: detail.version.version }));
      await refreshDiagramListLabel();
    } catch (err) { alert(err.message); }
  });
  // Ctrl/Cmd+Z = Undo, Ctrl/Cmd+Shift+Z or Ctrl+Y = Redo; never inside text fields (their own undo wins).
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const target = e.target;
    if (target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
    const key = e.key.toLowerCase();
    const button = key === "z" ? $(e.shiftKey ? "btn-redo" : "btn-undo") : key === "y" && !e.shiftKey ? $("btn-redo") : null;
    if (!button) return;
    e.preventDefault();
    if (!button.disabled && !document.querySelector("dialog[open]")) button.click();
  });
  $("btn-snapshot").addEventListener("click", async () => {
    const label = prompt(t("Bezeichnung der Version:"), t("Manueller Stand"));
    if (label == null) return;
    const detail = await api(`/api/diagrams/${state.diagramId}/versions${focusQuery()}`, { method: "POST", body: JSON.stringify({ label }) });
    showDiagram(detail);
    await refreshDiagramListLabel();
  });
  el.versionSelect.addEventListener("change", async () => {
    showSelectedTitle();
    if (el.versionSelect.value === MORE) { await loadOlderVersions(); return; }
    const v = Number(el.versionSelect.value);
    const data = await api(`/api/diagrams/${state.diagramId}/versions/${v}${focusQuery()}`);
    endCompare();
    state.previewVersion = v;
    renderView(viewOf(data));
    el.preview.textContent = t("Vorschau v{version} – „Wiederherstellen“ macht sie zum aktuellen Stand", { version: v });
    el.preview.classList.remove("hidden");
  });
  $("btn-restore").addEventListener("click", async () => {
    const v = Number(el.versionSelect.value);
    if (!v) return;
    const detail = await api(`/api/diagrams/${state.diagramId}/versions/${v}/restore${focusQuery()}`, { method: "POST" });
    showDiagram(detail);
    addMessage("assistant", t("Version v{from} wiederhergestellt (als v{to} gesichert).", { from: v, to: detail.version.version }));
    await refreshDiagramListLabel();
  });

  $("btn-compare").addEventListener("click", () => {
    const versions = [...(state.versions || [])].reverse();
    if (!versions.length) return;
    compareEl.base.innerHTML = ""; compareEl.target.innerHTML = "";
    compareEl.target.appendChild(option("", t("Aktueller Stand")));
    for (const v of versions) {
      compareEl.base.appendChild(option(String(v.version), versionLabel(v)));
      compareEl.target.appendChild(option(String(v.version), versionLabel(v)));
    }
    // default: the version picked in the selector (or the previous one) against the current state
    const selected = Number(el.versionSelect.value);
    const latest = versions[0].version;
    compareEl.base.value = String(selected && selected !== latest ? selected : (versions[1] ?? versions[0]).version);
    compareEl.target.value = "";
    compareEl.dialog.showModal();
  });
  $("compare-cancel").addEventListener("click", () => compareEl.dialog.close());
  $("compare-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    compareEl.dialog.close();
    const base = Number(compareEl.base.value);
    const target = compareEl.target.value ? Number(compareEl.target.value) : null;
    await showCompare(base, target);
  });
  $("btn-compare-end").addEventListener("click", () => setFocus(state.focus));
}
