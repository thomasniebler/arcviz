// Status line and the busy lock shared by every feature.
import { READ_ONLY_HINT, canEdit, canOwn, editLock } from "./access.js";
import { $, el } from "./dom.js";
import { t } from "./i18n.js";
import { state } from "./state.js";
import { typeConfig, unsupportedTitle } from "./types.js";

export function setStatus(text) { el.status.textContent = text || ""; }

export function setBusy(busy) {
  state.busy = busy;
  el.send.disabled = busy;
  el.input.disabled = busy;
  // Lock everything that would change the model or the shown diagram mid-operation; zoom stays usable.
  document.querySelectorAll(".topbar button, .topbar select").forEach((b) => (b.disabled = busy));
  const navigator = $("navigator");
  if (navigator) navigator.inert = busy; // inert keeps the navigator's own disabled states (ends of lists, delete)
  $("btn-undo").disabled = busy || state.undoTarget == null;
  $("btn-redo").disabled = busy || state.redoTarget == null;
  applyFeatures();
  el.focusUp.disabled = busy || !state.focus;
  el.editFocus.disabled = busy || !state.focus;
  el.inspector.querySelectorAll("input, select, textarea, button").forEach((b) => (b.disabled = busy));
  document.querySelectorAll("#review .review-actions button").forEach((b) => (b.disabled = busy || b.dataset.running === "true"));
  document.querySelectorAll("#review .finding-apply").forEach((b) => (b.disabled = busy || b.dataset.stale === "true"));
  applyReadOnly();
}

const chatPlaceholder = { text: null };

/** The caller's role on the open project changed (a diagram or project view was shown): recompute the controls. */
export function setRole(role) {
  if (role === state.role) return;
  state.role = role ?? null;
  setBusy(state.busy);
}

/**
 * Read-only mode for viewers (spec: workspaces-and-sharing.md): controls marked `data-edit` are disabled (`setBusy`
 * recomputes them before; those marked `data-edit-restore` too, which nobody recomputes, are given back by
 * `editLock` when the role allows editing again), the
 * editing zones (`data-edit-zone`: the inspector's forms, the model list) are inert, edit-only and owner-only
 * actions are hidden by `body.readonly` / `body.not-owner`, and the chat input explains why it is disabled.
 */
export function applyReadOnly() {
  const readOnly = !canEdit(state.role);
  document.body.classList.toggle("readonly", readOnly);
  document.body.classList.toggle("not-owner", !canOwn(state.role));
  document.querySelectorAll("[data-edit]").forEach((b) => {
    if (!("editRestore" in b.dataset)) {
      if (readOnly) b.disabled = true;
      return;
    }
    const next = editLock(readOnly, b.disabled, b.dataset.editLocked === "true");
    b.disabled = next.disabled;
    if (next.locked) b.dataset.editLocked = "true";
    else delete b.dataset.editLocked;
  });
  document.querySelectorAll("[data-edit-zone]").forEach((zone) => (zone.inert = readOnly));
  if (chatPlaceholder.text === null) chatPlaceholder.text = el.input.placeholder;
  el.input.placeholder = readOnly ? READ_ONLY_HINT : chatPlaceholder.text;
  el.input.disabled = state.busy || readOnly;
  el.send.disabled = state.busy || readOnly;
  el.send.title = readOnly ? READ_ONLY_HINT : "";
}

/** Disable the controls of features the shown diagram's type does not support (or everything while busy). */
export function applyFeatures() {
  const features = typeConfig(state.config, state.type).features || {};
  const toggle = (node, supported, title) => {
    if (!node) return;
    node.disabled = state.busy || !supported;
    if (!("title" in node.dataset)) node.dataset.title = node.title || ""; // the original tooltip
    node.title = supported ? node.dataset.title : title;
  };
  const why = unsupportedTitle(state.type);
  toggle(el.focusSelect, features.focus, why);
  toggle(el.direction, features.direction, why);
  toggle($("btn-compare"), features.compare, why);
  toggle($("exp-puml"), features.plantuml, why);
  toggle($("exp-mmd"), typeConfig(state.config, state.type).renderer === "mermaid", t("Dieses Diagramm wird nicht mit Mermaid gezeichnet"));
}
