// The shown diagram: detail rendering, focus/drill-down, opening a diagram, clearing its chat. Hub of the
// navigator and the feature modules: they import from here; reactions to a new state go through the hooks.
// The list of diagrams (projects, sections) is the navigator's (navigator.js).
import { api } from "./api.js";
import { renderView } from "./canvas.js";
import { showChat } from "./chatlog.js";
import { $, el, option } from "./dom.js";
import { diagramStats } from "./format.js";
import { containerOptions, parentFocus, staleFocus } from "./model.js";
import { focusQuery, state } from "./state.js";
import { typeConfig, viewOf } from "./types.js";
import { applyFeatures, setRole, setStatus } from "./ui.js";
import { t } from "./i18n.js";

const shownHooks = [], switchedHooks = [], selectionHooks = [];

/** `fn(detail, {remote})` runs at the end of every `showDiagram`; `remote` ({author_name, origin}) when the new state
 * came from someone else's change (live stream, spec: collaboration.md). */
export function onDiagramShown(fn) { shownHooks.push(fn); }

/** `fn(id)` runs when the element the user looks at (in the inspector) changes; null: none. */
export function onSelection(fn) { selectionHooks.push(fn); }

/** The inspector reports the element it shows (presence, spec: collaboration.md). */
export function notifySelection(id) { for (const fn of selectionHooks) fn(id ?? null); }

/** `fn()` runs when `openDiagram` switches to a different diagram, before it is loaded. */
export function onDiagramSwitched(fn) { switchedHooks.push(fn); }

let focusSeq = 0; // ignore responses of superseded focus requests

export async function setFocus(id) {
  if (!state.diagramId) return;
  state.focus = id && state.nodes[id] ? id : null;
  await showFocused({ fit: true }, { user: true });
}

let fitWanted = false; // a superseded focus change asked for a fit: the request that superseded it keeps it

/** Fetch and show the current focus; answers of superseded requests are dropped (their `fit` carries over). A failed
 * request the user made (`user`) is reported in the status line, a background refetch stays silent (a 404 after
 * `gone` above all); the newest request clears the pending fit, so no later refetch inherits it. */
async function showFocused(options, { user = false } = {}) {
  const seq = ++focusSeq;
  if (options.fit) fitWanted = true;
  try {
    const detail = await api(`/api/diagrams/${state.diagramId}${focusQuery()}`);
    if (seq !== focusSeq) return; // a newer focus request has been issued meanwhile
    showDiagram(detail, { ...options, fit: fitWanted });
  } catch (err) {
    if (seq === focusSeq && user) setStatus(t("Ansicht konnte nicht geladen werden: {error}", { error: err.message || err }));
  } finally {
    if (seq === focusSeq) fitWanted = false;
  }
}

/** Whether a rendering answer (a detail, a chat event) shows another focus than the one the user looks at now: it was
 * requested before the focus changed (#216) and must not take the view back. */
export function isStaleView(answer) {
  if (answer?.focus === undefined || !answer.diagram) return false;
  const features = typeConfig(state.config, answer.diagram.type || "deployment").features;
  return staleFocus(answer.focus, state.focus, answer.diagram.nodes, features?.focus);
}

function showFocus(detail) {
  state.nodes = detail.diagram.nodes;
  state.edges = detail.diagram.edges || [];
  state.mermaidIds = detail.mermaid_ids || {};
  if (detail.focus !== undefined) state.focus = detail.focus;
  // breadcrumb
  el.breadcrumb.innerHTML = "";
  const crumb = (label, id, current) => {
    const node = document.createElement(current ? "span" : "button");
    node.textContent = label;
    if (current) node.className = "current";
    else { node.type = "button"; node.addEventListener("click", () => setFocus(id)); }
    el.breadcrumb.appendChild(node);
  };
  const path = detail.breadcrumb || [];
  crumb(t("Ganzes Diagramm"), null, path.length === 0);
  path.forEach((b, i) => {
    const sep = document.createElement("span"); sep.className = "sep"; sep.textContent = "›";
    el.breadcrumb.appendChild(sep);
    crumb(b.name, b.id, i === path.length - 1);
  });
  // view selector
  el.focusSelect.innerHTML = "";
  const all = document.createElement("option"); all.value = ""; all.textContent = t("Ganzes Diagramm");
  el.focusSelect.appendChild(all);
  for (const c of containerOptions(state.nodes, typeConfig(state.config, state.type).container_kinds)) {
    const opt = document.createElement("option"); opt.value = c.id; opt.textContent = c.label.replace(/ /g, " ");
    el.focusSelect.appendChild(opt);
  }
  el.focusSelect.value = state.focus ?? "";
  el.focusUp.disabled = state.busy || !state.focus;
  el.editFocus.disabled = state.busy || !state.focus;
}

/** Show a diagram detail; `highlight` is a view (types.js) to render instead of the detail's own. */
export function showDiagram(detail, { highlight, fit = false, remote = null } = {}) {
  const d = detail.diagram;
  if (isStaleView(detail) && detail.diagram.id === state.diagramId) {
    // requested for the focus shown before the user changed it (#216): keep the revision, show the current focus
    if (detail.revision !== undefined) state.revision = detail.revision;
    showFocused({ remote }).catch(() => {});
    return;
  }
  if (detail.revision !== undefined) state.revision = detail.revision;
  if (detail.access) setRole(detail.access.role); // viewers see the diagram read-only (spec: workspaces-and-sharing.md)
  state.type = d.type || "deployment";
  state.interaction = d.interaction || null;
  state.complete = d.complete || [];
  state.hiddenRelations = d.hidden_relations || [];
  el.title.textContent = d.title;
  el.stats.textContent = diagramStats(d);
  showFocus(detail);
  el.direction.value = d.direction || "TB";
  applyFeatures();
  renderView(highlight || viewOf(detail), { fit });
  state.previewVersion = null;
  el.preview.classList.add("hidden");
  for (const fn of shownHooks) fn(detail, { remote });
}

// ---------- layout direction selector (its changes are model edits, see inspector.js) ----------

const DIRECTION_LABELS = { TB: "TB ↓", BT: "BT ↑", LR: "LR →", RL: "RL ←" };

export function fillDirections(directions) {
  el.direction.innerHTML = "";
  for (const d of directions || ["TB"]) el.direction.appendChild(option(d, DIRECTION_LABELS[d] || d));
}

// ---------- opening a diagram ----------------------------------------------------

export async function openDiagram(id) {
  if (id !== state.diagramId) { state.focus = null; state.revision = null; state.lastChange = null; for (const fn of switchedHooks) fn(); }
  state.diagramId = id;
  try { localStorage.setItem("arcviz.diagram", id); } catch {}
  const detail = await api(`/api/diagrams/${id}${focusQuery()}`);
  showDiagram(detail, { fit: true });
  if (state.chatScope === "diagram") showChat(detail.chat); // the project chat keeps its transcript
}

export function initDiagram() {
  el.focusSelect.addEventListener("change", () => setFocus(el.focusSelect.value || null));
  el.focusUp.addEventListener("click", () => setFocus(parentFocus(state.nodes, state.focus)));
  document.addEventListener("keydown", (e) => {
    if (e.altKey && e.key === "ArrowUp" && state.focus) { e.preventDefault(); el.focusUp.click(); }
  });

  $("btn-clear-chat").addEventListener("click", async () => {
    if (state.busy) return;
    if (state.chatScope === "project") { // the transcript of the current scope (spec: project-chat.md)
      const projectId = state.projectId;
      if (!projectId || !confirm(t("Chat-Verlauf des Projekts löschen? Die Diagramme und ihre Versionen bleiben erhalten."))) return;
      await api(`/api/projects/${encodeURIComponent(projectId)}/chat`, { method: "DELETE" });
      if (state.chatScope === "project" && state.projectId === projectId) showChat([], { scope: "project" });
      return;
    }
    if (!state.diagramId) return;
    if (!confirm(t("Chat-Verlauf dieses Diagramms löschen? Die Versionen bleiben erhalten."))) return;
    const id = state.diagramId;
    await api(`/api/diagrams/${id}/chat`, { method: "DELETE" });
    if (state.diagramId === id && state.chatScope === "diagram") showChat([]); // the user may have switched meanwhile
  });
}
