// The allow-list sanitizer for every SVG the canvas shows or exports (second line of defence: the server escapes all
// text; spec: docs/architecture/specs/hardening.md, docs/architecture/specs/follow-ups.md). Pure: it walks a DOM-like
// tree it is given and touches no globals, so tests/js/svgsafe.test.mjs runs it on a tiny fake DOM. The own renderers'
// SVG and Mermaid's output (#197, `htmlLabels`: its labels are HTML in a foreignObject) pass through it, so nothing
// outside what they produce needs to survive.
import { t } from "./i18n.js";

export const SVG_NS = "http://www.w3.org/2000/svg";
export const XHTML_NS = "http://www.w3.org/1999/xhtml";

/** The HTML Mermaid writes for a label (htmlLabels: a `div` > `span.nodeLabel|edgeLabel` > `p` with text and `br`).
 * Only these stay, only in the XHTML namespace and only inside a `foreignObject`. */
export const LABEL_ELEMENTS = new Set(["div", "span", "p", "br"]);
/** The only attributes of label elements: `class`, a safe `style`, and `xmlns` declaring XHTML. */
export const LABEL_ATTRIBUTES = new Set(["class", "style", "xmlns"]);

/** The only elements that stay (in the SVG namespace). Everything else, and every element of another namespace
 * (XHTML `img`, `form`, `meta`, …), is dropped with its subtree. */
export const ALLOWED_ELEMENTS = new Set([
  "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title", "desc",
  "defs", "marker", "clippath", "lineargradient", "radialgradient", "stop", "use", "style", "mask",
]);

/** The only attributes that stay (plus `aria-*` and `data-*`); `style` and presentation values are checked as CSS. */
export const ALLOWED_ATTRIBUTES = new Set([
  "id", "class", "role", "lang", "xmlns", "viewbox", "preserveaspectratio", "transform",
  "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "dx", "dy", "width", "height", "d", "points",
  "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
  "stroke-linejoin", "stroke-opacity", "opacity", "font-size", "font-family", "font-weight", "font-style",
  "text-anchor", "dominant-baseline", "alignment-baseline", "marker-start", "marker-mid", "marker-end",
  "markerwidth", "markerheight", "markerunits", "refx", "refy", "orient", "offset", "stop-color", "stop-opacity",
  "gradientunits", "gradienttransform", "clippathunits", "clip-path", "style", "href", "xlink:href",
  "mask", "maskunits", "maskcontentunits", // the icon packs' masks (gcp), local `url(#id)` only like clip-path
]);
/** The only CSS functions that may appear (what Mermaid and the own renderers write); `url()` only as `url(#id)`.
 * Anything else, e.g. `image-set("…")`, `cross-fade()`, `element()`, `src()`, could load or reference content. */
export const CSS_FUNCTIONS = new Set([
  "url", "var", "calc", "min", "max", "clamp", "rgb", "rgba", "hsl", "hsla", "drop-shadow", "translate",
  "translatex", "translatey", "rotate", "scale", "scalex", "scaley", "skewx", "skewy", "matrix", "cubic-bezier", "steps",
  // selector pseudo-classes (they select, they never load)
  "not", "is", "where", "has", "nth-child", "nth-of-type", "nth-last-child", "nth-last-of-type", "lang", "dir",
]);
const BAD_SCHEMES = ["javascript:", "data:", "vbscript:"];

/** Lower case without whitespace and control characters, so `java\tscript:` and ` JaVaScRiPt:` are caught. */
export function normalized(value) {
  return String(value ?? "").replace(/[\u0000- \u007f-\u009f]/g, "").toLowerCase();
}

/** Only a same-document fragment (`#id`) may be referenced. */
export function isFragment(value) {
  return /^#[\w.:-]+$/.test(normalized(value));
}

/** CSS that may stay. Strict, so no decoding is needed: no backslash (escapes like `@\69mport`), no comment (`@im/**\/port`),
 * no at-rule but `@media` and `@keyframes` (Mermaid's edge animations), no `expression(`, no scheme, only the functions
 * of `CSS_FUNCTIONS`, and every `url(…)` exactly `url(#id)`. */
export function isSafeCss(text) {
  const css = normalized(text);
  if (css.includes("\\") || css.includes("/*") || css.includes("expression(")) return false;
  if (BAD_SCHEMES.some((s) => css.includes(s))) return false;
  // whitespace is gone after normalizing, so the name runs into what follows it (`@keyframesdash{`, `@media(`)
  if ([...css.matchAll(/@([a-z-]*)/g)].some((m) => !/^(media|keyframes)/.test(m[1]))) return false;
  // function tokens are an identifier directly followed by "(": read them from the text itself (not the normalized
  // one, which joins neighbouring words); escapes and comments that could hide a name are refused above
  const names = [...String(text ?? "").toLowerCase().matchAll(/(?<![a-z0-9_@-])([a-z0-9_-]+)\(/g)].map((m) => m[1]);
  if (names.some((name) => !CSS_FUNCTIONS.has(name))) return false;
  return css.replace(/url\((['"]?)#[\w.:-]+\1\)/g, "").includes("url(") === false;
}

/** Whether an attribute must go: anything outside the allow-list, event handlers, links other than `#fragment`
 * (and only on `use`), any dangerous scheme, unsafe CSS in `style` or a presentation value. */
export function isUnsafeAttribute(name, value, tag = "") {
  const key = String(name).toLowerCase();
  if (key.startsWith("on")) return true;
  if (key === "href" || key === "xlink:href") return tag !== "use" || !isFragment(value);
  if (!(ALLOWED_ATTRIBUTES.has(key) || /^aria-[a-z-]+$/.test(key) || /^data-[a-z0-9-]+$/.test(key))) return true;
  if (key.startsWith("data-") || key.startsWith("aria-") || key === "id" || key === "class" || key === "role") {
    return BAD_SCHEMES.some((s) => normalized(value).includes(s));
  }
  return !isSafeCss(value);
}

/** Whether an attribute of a label element (HTML inside a foreignObject) must go. */
export function isUnsafeLabelAttribute(name, value) {
  const key = String(name).toLowerCase();
  if (!LABEL_ATTRIBUTES.has(key)) return true;
  if (key === "xmlns") return String(value).trim() !== XHTML_NS;
  if (key === "style") return !isSafeCss(value);
  return BAD_SCHEMES.some((s) => normalized(value).includes(s));
}

function tagOf(node) {
  return String(node.localName || node.tagName || "").toLowerCase();
}

/** Sanitize `root` (an `<svg>` element) in place: elements outside the allow-list or the SVG namespace go with their
 * subtree, `<style>` with unsafe CSS goes, unsafe attributes go. With `htmlLabels` (Mermaid's output, #197) an SVG
 * `foreignObject` may stay, holding only `LABEL_ELEMENTS` in the XHTML namespace with `LABEL_ATTRIBUTES` (text stays
 * text); anything else in it goes with its subtree. Node interface used: `localName`/`tagName`, `namespaceURI`,
 * `children`, `attributes` ({name, value}), `removeAttribute`, `remove`, `textContent`. Throws when the root itself is
 * not an SVG `<svg>`. Returns `root`. */
export function sanitizeSvg(root, { htmlLabels = false } = {}) {
  if (tagOf(root) !== "svg" || (root.namespaceURI && root.namespaceURI !== SVG_NS)) throw new Error(t("ungültiges SVG"));
  const label = (node) => {
    if (node.namespaceURI !== XHTML_NS || !LABEL_ELEMENTS.has(tagOf(node))) {
      node.remove();
      return;
    }
    for (const attr of [...(node.attributes || [])]) if (isUnsafeLabelAttribute(attr.name, attr.value)) node.removeAttribute(attr.name);
    for (const child of [...(node.children || [])]) label(child);
  };
  const visit = (node) => {
    const tag = tagOf(node);
    const foreign = node.namespaceURI != null && node.namespaceURI !== SVG_NS;
    if (htmlLabels && tag === "foreignobject" && !foreign && node !== root) {
      for (const attr of [...(node.attributes || [])]) if (isUnsafeAttribute(attr.name, attr.value, tag)) node.removeAttribute(attr.name);
      for (const child of [...(node.children || [])]) label(child);
      return;
    }
    if (node !== root && (foreign || !ALLOWED_ELEMENTS.has(tag) || (tag === "style" && !isSafeCss(node.textContent)))) {
      node.remove();
      return;
    }
    for (const attr of [...(node.attributes || [])]) if (isUnsafeAttribute(attr.name, attr.value, tag)) node.removeAttribute(attr.name);
    for (const child of [...(node.children || [])]) visit(child);
  };
  visit(root);
  return root;
}
