// Element lookup shared by the DOM modules. Nothing here runs at import time: `bindElements()` fills
// `el` once the page is parsed (called first by app.js).
import { t } from "./i18n.js";

export const $ = (id) => document.getElementById(id);

/** Frequently used elements, filled by `bindElements()`. */
export const el = {};

export function bindElements() {
  Object.assign(el, {
    versionSelect: $("version-select"),
    messages: $("messages"), form: $("chat-form"), input: $("chat-input"), send: $("btn-send"),
    status: $("status"), diagram: $("diagram"), viewport: $("viewport"), source: $("source"), title: $("diagram-title"),
    stats: $("diagram-stats"), provider: $("provider-info"), preview: $("preview-banner"),
    breadcrumb: $("breadcrumb"), focusSelect: $("focus-select"), focusUp: $("btn-focus-up"), editFocus: $("btn-edit-focus"),
    inspector: $("inspector"), direction: $("direction-select"),
  });
}

export function option(value, label, selected = false) {
  const o = document.createElement("option"); o.value = value; o.textContent = label; o.selected = selected; return o;
}

/** A placeholder `<div class="placeholder">` showing `text` literally: error messages may quote diagram source or
 * server text, so they are text, never markup (#119). */
export function placeholder(text) {
  const d = document.createElement("div"); d.className = "placeholder"; d.textContent = text; return d;
}

/** Point the location hash at `hash` ("" = no fragment) without a hashchange event; a new history entry unless `replace`. */
export function setHash(hash, { replace = false } = {}) {
  const current = location.hash === "#" ? "" : location.hash;
  if (current === hash) return;
  const url = location.pathname + location.search + hash;
  if (replace) history.replaceState(null, "", url); else history.pushState(null, "", url);
}

/** Copy text to the clipboard; falls back to a hidden textarea + execCommand where the async
 * Clipboard API is unavailable (plain-HTTP deployments are not a secure context). */
export async function copyText(text) {
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
