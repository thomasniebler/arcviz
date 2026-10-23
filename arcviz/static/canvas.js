// Canvas: theme, rendering into #viewport (Mermaid source or an SVG document, see types.js), pan & zoom,
// clicks on the SVG.
import { $, el, placeholder } from "./dom.js";
import { ZOOM_STEP, fitTransform, viewTransform, wheelFactor, zoomAt as zoomedView, zoomLabel } from "./geometry.js";
import { state, view } from "./state.js";
import { sanitizeSvg } from "./svgsafe.js";
import { t } from "./i18n.js";

// Mermaid is served from /static/vendor (pinned build, see scripts/vendor_mermaid.py) so the UI
// works offline and in restricted networks.
const mermaid = () => window.mermaid;

// ---------- theme (follows the operating system; Mermaid is re-initialised to match) ----------

let darkQuery = null;

function applyTheme() {
  const dark = darkQuery.matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  mermaid().initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: dark ? "dark" : "neutral",
    flowchart: { htmlLabels: true, curve: "basis" },
  });
  if (state.view?.renderer === "mermaid") renderView(state.view);
}

// ---------- rendering -------------------------------------------------------

const renderedHooks = [];

/** `fn()` runs after every successful render, once the SVG is in the page (e.g. to re-apply a highlight). */
export function onRendered(fn) { renderedHooks.push(fn); }

/** Parse an SVG document from the server and drop anything active (second line of defence: the server
 * escapes all text). Returns the root element, or throws. */
export function parseSvg(text) {
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const root = doc.documentElement;
  if (root.nodeName !== "svg" || doc.querySelector("parsererror")) throw new Error(t("ungültiges SVG"));
  sanitizeSvg(root); // allow-list: no scripts, links, animations, foreign content or unsafe CSS (svgsafe.js)
  return document.importNode(root, true);
}

/** Parse Mermaid's SVG output and run it through the allow-list (#197): the HTML parser (Mermaid writes labels as
 * HTML, e.g. `<br>`, which XML would reject) fills an inert `<template>` (no script runs, nothing loads, no handler
 * fires there); only the sanitized `<svg>` is adopted into the page. Returns the root element, or throws. */
export function parseMermaidSvg(text) {
  const template = document.createElement("template");
  template.innerHTML = text; // inert-html: Mermaid output, parsed into an inert template and sanitized before adoption
  const roots = template.content.children;
  if (roots.length !== 1 || roots[0].localName !== "svg") throw new Error(t("ungültiges SVG"));
  sanitizeSvg(roots[0], { htmlLabels: true }); // labels: only Mermaid's div/span/p/br with class and safe style
  return document.importNode(roots[0], true);
}

/** Render a view `{renderer, source}` (see `viewOf` in types.js) into the canvas. */
export async function renderView(rendering, { fit = false } = {}) {
  if (!rendering) return;
  state.view = rendering;
  el.source.textContent = rendering.source;
  if (fit) state.fitPending = true;
  const seq = ++state.renderSeq;
  el.diagram.setAttribute("aria-busy", "true"); // until the newest render is in the page (the e2e test waits on it)
  try {
    let node;
    if (rendering.renderer === "svg") {
      node = parseSvg(rendering.source);
      el.viewport.replaceChildren(node);
    } else {
      const { svg } = await mermaid().render(`arcviz-${seq}`, rendering.source);
      if (seq !== state.renderSeq) return;
      node = parseMermaidSvg(svg); // through the allow-list like every other SVG (#197)
      el.viewport.replaceChildren(node);
    }
    // Give the SVG its natural size so the viewport maths below is exact.
    const vb = node.viewBox.baseVal;
    if (vb && vb.width && vb.height) {
      node.setAttribute("width", vb.width);
      node.setAttribute("height", vb.height);
      node.style.maxWidth = "";
    }
    if (state.fitPending) fitView(); else applyView();
    el.diagram.setAttribute("aria-busy", "false");
    for (const fn of renderedHooks) fn();
  } catch (err) {
    if (seq !== state.renderSeq) return;
    el.diagram.setAttribute("aria-busy", "false");
    el.viewport.replaceChildren(placeholder(t("Rendering fehlgeschlagen: {error}", { error: err.message || err })));
    view.scale = 1; view.x = 0; view.y = 0; applyView();
    // mermaid leaves a broken temp element behind on error
    document.querySelectorAll(`#darcviz-${seq}, #arcviz-${seq}`).forEach((n) => n.remove());
  }
}

/** Register the vendored icon packs (spec: docs/architecture/specs/cloud-icons.md). The loaders run only when
 * a diagram uses an icon of the pack, and fetch it from our own /static, never from a CDN. */
export function registerIcons(packs) {
  if (!packs?.length || !mermaid().registerIconPacks) return;
  mermaid().registerIconPacks(packs.map((name) => ({
    name,
    loader: () => fetch(`/static/vendor/icons/${encodeURIComponent(name)}.json`).then((r) => r.json()),
  })));
}

/** Render Mermaid source off-screen (for exports); returns the sanitized `<svg>` element (detached, #197). */
export async function renderOffscreen(id, src) { return parseMermaidSvg((await mermaid().render(id, src)).svg); }

// ---------- pan & zoom ------------------------------------------------------

function svgSize() {
  const svg = el.viewport.querySelector("svg");
  if (!svg) return null;
  const vb = svg.viewBox.baseVal;
  return vb && vb.width ? { width: vb.width, height: vb.height } : { width: svg.clientWidth, height: svg.clientHeight };
}

export function applyView() {
  el.viewport.style.transform = viewTransform(view);
  $("btn-zoom-reset").textContent = zoomLabel(view.scale);
}

export function fitView() {
  const fit = fitTransform(svgSize(), { width: el.diagram.clientWidth, height: el.diagram.clientHeight });
  if (!fit) return;
  Object.assign(view, fit);
  state.fitPending = false;
  view.fitted = true;
  applyView();
}

/** Zoom by `factor` keeping the diagram point under (cx, cy) (container coordinates) fixed. */
function zoomAt(factor, cx, cy) {
  Object.assign(view, zoomedView(view, factor, cx, cy));
  view.fitted = false;
  applyView();
}

export function zoomCenter(factor) { zoomAt(factor, el.diagram.clientWidth / 2, el.diagram.clientHeight / 2); }

export function resetView() {
  view.scale = 1; view.x = 24; view.y = 24; view.fitted = false;
  applyView();
}

/** Theme, wheel zoom, drag to pan, click detection and refit on resize. `onClick(target)` receives the
 * element pressed when a pointer is released without moving (≤ 4 px). */
export function initCanvas({ onClick }) {
  darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  applyTheme();
  darkQuery.addEventListener("change", applyTheme);

  el.diagram.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = el.diagram.getBoundingClientRect();
    zoomAt(wheelFactor(e, el.diagram.clientHeight), e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });

  let pan = null;
  el.diagram.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    // pointer capture makes later events target #diagram, so remember what was pressed
    pan = { id: e.pointerId, startX: e.clientX, startY: e.clientY, x: view.x, y: view.y, moved: false, target: e.target };
    el.diagram.setPointerCapture(e.pointerId);
    el.diagram.classList.add("panning");
  });
  el.diagram.addEventListener("pointermove", (e) => {
    if (!pan || e.pointerId !== pan.id) return;
    view.x = pan.x + (e.clientX - pan.startX);
    view.y = pan.y + (e.clientY - pan.startY);
    view.fitted = false;
    if (Math.abs(e.clientX - pan.startX) + Math.abs(e.clientY - pan.startY) > 4) pan.moved = true;
    applyView();
  });
  function endPan(e) {
    if (!pan || e.pointerId !== pan.id) return;
    const clicked = !pan.moved && e.type === "pointerup" ? pan.target : null;
    pan = null;
    el.diagram.classList.remove("panning");
    if (clicked) onClick(clicked);
  }
  el.diagram.addEventListener("pointerup", endPan);
  el.diagram.addEventListener("pointercancel", endPan);
  el.diagram.addEventListener("dblclick", (e) => { e.preventDefault(); fitView(); });
  // The canvas changes size on window resize and on splitter drags; a fitted diagram stays fitted.
  new ResizeObserver(() => { if (state.fitPending || view.fitted) fitView(); }).observe(el.diagram);

  $("btn-zoom-in").addEventListener("click", () => zoomCenter(ZOOM_STEP));
  $("btn-zoom-out").addEventListener("click", () => zoomCenter(1 / ZOOM_STEP));
  $("btn-zoom-reset").addEventListener("click", resetView);
  $("btn-fit").addEventListener("click", fitView);
  $("btn-source").addEventListener("click", () => el.source.classList.toggle("hidden"));
}
