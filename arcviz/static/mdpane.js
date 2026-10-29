// View helpers of the two-pane lists on the Architektur page (spec: docs/architecture/specs/architecture-workspace.md,
// ADR-0196): the card of a list entry, chips, the selection mark, the keyboard step and the discard question.
// Everything is built with DOM APIs: titles and descriptions are free text and must never reach innerHTML.
import { t } from "./i18n.js";
import { fitHeight } from "./readability.js";

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

/** A small label; `tone` is one of ok, warn, danger, accent or empty. */
export function chip(text, tone = "") {
  return node("span", `chip${tone ? ` chip-${tone}` : ""}`, text);
}

/** The list button of one item: `lead` (a node or text, e.g. a priority badge), `title`, `chips` (nodes) and `meta`. */
export function card({ id, lead = null, title, chips = [], meta = "" }) {
  const b = node("button", "md-card");
  b.type = "button";
  b.dataset.id = id;
  if (lead !== null) b.appendChild(typeof lead === "string" ? node("span", "md-lead", lead) : lead);
  const body = node("span", "md-body");
  body.appendChild(node("span", "md-title", title));
  if (chips.length) {
    const row = node("span", "md-chips");
    row.append(...chips);
    body.appendChild(row);
  }
  if (meta) body.appendChild(node("span", "md-meta muted", meta));
  b.appendChild(body);
  return b;
}

/** Mark the card of `id` inside `list` as the current one (and no other). */
export function markSelected(list, id) {
  for (const c of list.querySelectorAll(".md-card")) {
    if (c.dataset.id === id) c.setAttribute("aria-current", "true"); else c.removeAttribute("aria-current");
  }
}

/** ↑/↓ call `onStep(±1)`, Enter on a focused card calls `onOpen(id)`; the keys are handled on `list`. */
export function bindListKeys(list, onStep, onOpen) {
  list.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      onStep(ev.key === "ArrowDown" ? 1 : -1);
    } else if (ev.key === "Enter") {
      const c = ev.target.closest?.(".md-card");
      if (c) { ev.preventDefault(); onOpen(c.dataset.id); }
    }
  });
}

/** Whether the user may leave the editor: always when it is clean, else after one question. */
export function confirmDiscard(dirty) {
  return !dirty || window.confirm(t("Ungespeicherte Änderungen verwerfen?"));
}

/** Hide the label around a multi-select that has nothing to choose from (no empty box in the editor). */
export function hideIfEmpty(select) {
  select.closest("label")?.classList.toggle("hidden", select.options.length === 0);
}

/** Text areas of the editors below `root` grow with their text, up to 60 % of the window. Browsers with CSS
 * `field-sizing: content` do it themselves; elsewhere the height is set after every edit, click and DOM change. */
export function initAutosize(root) {
  if (typeof CSS !== "undefined" && CSS.supports?.("field-sizing", "content")) return;
  let queued = false;
  const fit = () => {
    queued = false;
    for (const area of root.querySelectorAll(".md-detail textarea")) {
      if (!area.offsetParent) continue; // hidden: no size to measure
      area.style.height = "auto";
      const { height, overflow } = fitHeight(area.scrollHeight + 2, 88, Math.round(window.innerHeight * 0.6));
      area.style.height = `${height}px`;
      area.style.overflowY = overflow ? "auto" : "hidden";
    }
  };
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(fit); } };
  for (const type of ["input", "click", "change", "focusin"]) root.addEventListener(type, queue);
  new MutationObserver(queue).observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "hidden"] });
}
