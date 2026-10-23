// The SVG allow-list sanitizer (arcviz/static/svgsafe.js; review of #194, spec: docs/architecture/specs/hardening.md).
// Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { LABEL_ELEMENTS, SVG_NS, XHTML_NS, isFragment, isSafeCss, isUnsafeAttribute, isUnsafeLabelAttribute, sanitizeSvg } from "../../arcviz/static/svgsafe.js";

const XHTML = "http://www.w3.org/1999/xhtml";

/** A minimal element: what sanitizeSvg uses of the DOM. */
class El {
  constructor(tag, attrs = {}, children = [], text = "", ns = SVG_NS) {
    this.localName = tag;
    this.namespaceURI = ns;
    this.attrs = new Map(Object.entries(attrs));
    this.children = [];
    this.parent = null;
    this._text = text;
    for (const c of children) { c.parent = this; this.children.push(c); }
  }
  get attributes() { return [...this.attrs].map(([name, value]) => ({ name, value })); }
  removeAttribute(name) { this.attrs.delete(name); }
  remove() { const siblings = this.parent.children; siblings.splice(siblings.indexOf(this), 1); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  tags() { return [this.localName, ...this.children.flatMap((c) => c.tags())]; }
}

test("only the allow-listed SVG elements stay; anything else and other namespaces go with their subtree", () => {
  const root = new El("svg", {}, [
    new El("script", {}, [], "alert(1)"),
    new El("foreignObject", {}, [new El("div", { onclick: "x()" }, [], "", XHTML)]),
    new El("a", { href: "#ok" }, [new El("text", {}, [], "klick")]),
    new El("animate", { attributeName: "href", to: "javascript:alert(1)" }),
    new El("set", { attributeName: "href", to: "javascript:alert(1)" }),
    new El("image", { href: "#x" }),
    new El("img", { src: "x" }, [], "", XHTML),
    new El("meta", { "http-equiv": "refresh" }, [], "", XHTML),
    new El("form", { action: "https://evil.example" }, [], "", XHTML),
    new El("text", {}, [], "", XHTML), // an allow-listed name in another namespace
    new El("unknownWidget"),
    new El("g", {}, [new El("rect", { width: "1" }), new El("text", {}, [new El("tspan", {}, [], "<b>bleibt Text</b>")])]),
    new El("defs", {}, [new El("marker", { id: "arrow" }, [new El("path", { d: "M0 0" })])]),
  ]);
  sanitizeSvg(root);
  assert.deepEqual(root.tags(), ["svg", "g", "rect", "text", "tspan", "defs", "marker", "path"]);
});

test("the root must be an SVG <svg>", () => {
  assert.throws(() => sanitizeSvg(new El("html", {}, [], "", XHTML)));
  assert.throws(() => sanitizeSvg(new El("svg", {}, [], "", XHTML)));
});

test("styles: escapes, comments, at-rules and any url() but url(#id) drop the <style>", () => {
  const evil = [
    "rect { fill: url(javascript:alert(1)) }",
    "@import 'https://evil.example/x.css';",
    "@\\69mport 'https://evil.example/x.css';",
    "@im/**/port 'https://evil.example/x.css';",
    "rect { fill: u\\72l(https://evil.example/t.png) }",
    "rect { fill: url(java\\73 cript:alert(1)) }",
    "rect { background: url(https://evil.example/t.png) }",
    "rect { fill: url(\"#ok\") ; behavior: url(x.htc) }",
    "@font-face { font-family: x; src: local(x) }",
    "rect { width: expression(alert(1)) }",
  ];
  const good = ".msg { marker-end: url(#arrow); fill: #222 } @media (prefers-color-scheme: dark) { .x { fill: #eee } }";
  const root = new El("svg", {}, [...evil.map((css) => new El("style", {}, [], css)), new El("style", {}, [], good)]);
  sanitizeSvg(root);
  assert.deepEqual(root.children.map((c) => c.textContent), [good]);
  for (const css of evil) assert.ok(!isSafeCss(css), css);
});

test("attributes: allow-list, handlers, URL-bearing ones and unsafe CSS values go", () => {
  const attrs = {
    onload: "alert(1)",
    onClick: "alert(1)",
    href: "#arrow",
    srcset: "x.png 1x",
    ping: "https://evil.example/",
    poster: "x.png",
    background: "x.png",
    formaction: "javascript:alert(1)",
    action: "https://evil.example/",
    src: "data:text/html,<script>alert(1)</script>",
    fill: "url(#grad)",
    "marker-end": "url(#arrow)",
    filter: "url(https://evil.example/f.svg#x)",
    stroke: "u\\72l(https://evil.example/)",
    style: "fill: url( 'JAVASCRIPT:alert(1)' )",
    "data-id": "evil",
    "data-x": " java\tscript:alert(1)",
    "aria-label": "Nachricht",
    x: "10",
  };
  const node = new El("rect", attrs);
  sanitizeSvg(new El("svg", {}, [node]));
  assert.deepEqual([...node.attrs.keys()].sort(), ["aria-label", "data-id", "fill", "marker-end", "x"]);
  const use = new El("use", { href: "#shape", "xlink:href": "https://evil.example/#x" });
  sanitizeSvg(new El("svg", {}, [use]));
  assert.deepEqual([...use.attrs.keys()], ["href"]); // a #fragment, and only on <use>
});

test("only a same-document fragment counts as a safe reference", () => {
  for (const ok of ["#arrow", " #a-b ", "#x"]) assert.ok(isFragment(ok), ok);
  for (const bad of ["javascript:x", "data:x", "https://a/#b", "//a", "x#y", "", "##", "#a b(", "#\\61"]) assert.ok(!isFragment(bad), bad);
  assert.ok(isUnsafeAttribute("href", "#ok", "rect") && !isUnsafeAttribute("href", "#ok", "use"));
  assert.ok(isUnsafeAttribute("values", "1;2") && !isUnsafeAttribute("class", "msg"));
});

// ---- Mermaid's output (#197): htmlLabels as foreignObject with an HTML sub-sanitizer ---------------------------------

const html = (tag, attrs = {}, children = [], text = "") => new El(tag, attrs, children, text, XHTML);

/** A node label as Mermaid 11 writes it with htmlLabels: true. */
function mermaidLabel(text, extra = []) {
  return new El("foreignObject", { width: "120", height: "24" }, [
    html("div", { xmlns: XHTML, style: "display: table-cell; white-space: nowrap; line-height: 1.5;" }, [
      html("span", { class: "nodeLabel", style: "color:#1b2a4a !important" }, [html("p", {}, [html("br")], text), ...extra]),
    ]),
  ]);
}

test("with htmlLabels a foreignObject keeps exactly the label markup Mermaid produces", () => {
  assert.deepEqual([...LABEL_ELEMENTS].sort(), ["br", "div", "p", "span"]);
  const label = mermaidLabel("«service» <img src=x onerror=alert(1)>"); // the server's escaping makes this text
  const root = new El("svg", {}, [new El("g", { class: "label" }, [label])]);
  sanitizeSvg(root, { htmlLabels: true });
  assert.deepEqual(root.tags(), ["svg", "g", "foreignObject", "div", "span", "p", "br"]);
  assert.equal(root.textContent, "«service» <img src=x onerror=alert(1)>");
  const [div] = label.children;
  assert.deepEqual([...div.attrs.keys()], ["xmlns", "style"]);
  assert.deepEqual([...label.attrs.keys()], ["width", "height"]);
});

test("anything else inside a label goes: other elements, other namespaces, handlers, links, unsafe CSS", () => {
  const evil = [
    html("img", { src: "x", onerror: "alert(1)" }),
    html("a", { href: "javascript:alert(1)" }, [], "klick"),
    html("script", {}, [], "alert(1)"),
    html("iframe", { srcdoc: "<script>alert(1)</script>" }),
    html("form", { action: "https://evil.example/" }, [html("input", { name: "pw" })]),
    html("style", {}, [], "* { background: url(https://evil.example/t.png) }"),
    html("svg", {}, [html("script")]),
    new El("svg", {}, [new El("script", {}, [], "alert(1)")]), // SVG inside the HTML label
    new El("text", {}, [], "svg text in html"),
    html("b", {}, [], "Markdown-Fett"), // not produced from escaped labels: goes too
  ];
  const label = mermaidLabel("ok", evil);
  const span = label.children[0].children[0];
  span.attrs.set("onclick", "alert(1)");
  span.attrs.set("data-x", "1");
  span.attrs.set("title", "t");
  label.children[0].attrs.set("style", "background: url(javascript:alert(1))");
  label.children[0].attrs.set("xmlns", "http://www.w3.org/2000/svg");
  label.attrs.set("requiredExtensions", "http://www.w3.org/1999/xhtml");
  label.attrs.set("onload", "alert(1)");
  const root = new El("svg", {}, [label]);
  sanitizeSvg(root, { htmlLabels: true });
  assert.deepEqual(root.tags(), ["svg", "foreignObject", "div", "span", "p", "br"]);
  assert.deepEqual([...span.attrs.keys()], ["class", "style"]);
  assert.deepEqual([...label.children[0].attrs.keys()], []);
  assert.deepEqual([...label.attrs.keys()], ["width", "height"]);
});

test("label markup is only allowed inside a foreignObject, and a foreignObject only with htmlLabels", () => {
  const stray = new El("svg", {}, [html("div", {}, [], "x"), new El("g", {}, [html("span", {}, [], "y")])]);
  sanitizeSvg(stray, { htmlLabels: true });
  assert.deepEqual(stray.tags(), ["svg", "g"]);
  const own = new El("svg", {}, [mermaidLabel("x")]);
  sanitizeSvg(own); // the own renderers never produce foreignObject
  assert.deepEqual(own.tags(), ["svg"]);
  const nested = new El("svg", {}, [new El("foreignObject", {}, [html("div", {}, [new El("foreignObject", {}, [html("div")])])])]);
  sanitizeSvg(nested, { htmlLabels: true });
  assert.deepEqual(nested.tags(), ["svg", "foreignObject", "div"]);
});

test("label attributes: class, a safe style and the XHTML namespace declaration only", () => {
  assert.ok(!isUnsafeLabelAttribute("class", "nodeLabel"));
  assert.ok(!isUnsafeLabelAttribute("style", "color:#222 !important; max-width: 200px"));
  assert.ok(!isUnsafeLabelAttribute("xmlns", XHTML_NS));
  for (const [name, value] of [["xmlns", SVG_NS], ["style", "background:url(https://x/)"], ["onclick", "x()"], ["href", "#a"], ["id", "x"], ["class", "javascript:x"]]) {
    assert.ok(isUnsafeLabelAttribute(name, value), `${name}=${value}`);
  }
});

test("@keyframes (Mermaid's edge animations) is allowed, as long as nothing in it loads", () => {
  const css = "@keyframes dash { to { stroke-dashoffset: 0; } } .edge-animation-fast { animation: dash 50s linear infinite; }";
  assert.ok(isSafeCss(css));
  assert.ok(!isSafeCss("@keyframes x { from { background: url(https://evil.example/t.png) } }"));
  assert.ok(!isSafeCss("@keyframes x {} @import 'https://evil.example/x.css';"));
});

// ---- coordinator review of #199: CSS functions by allow-list, masks of the icon packs ----------------------------

test("only allow-listed CSS functions: no image-set, cross-fade, element() or anything else that may load", () => {
  for (const css of [
    'rect { fill: image-set("https://evil.example/x.png" 1x) }',
    'rect { background: -webkit-image-set("https://evil.example/x.png" 1x) }',
    'rect { background: cross-fade(50%, "https://evil.example/a.png", "b.png") }',
    "rect { background: element(#secret) }",
    'rect { src: src("https://evil.example/f.woff") }',
    'rect { fill: paint(worklet) }',
    "rect { list-style: symbols(cyclic \"x\") }",
    'rect { fill: IMAGE-SET("https://evil.example/x.png" 1x) }',
    'rect { fill: image\u0000-set("https://evil.example/x.png" 1x) }',
  ]) assert.ok(!isSafeCss(css), css);
  for (const css of [
    ".edge { stroke: rgb(1, 2, 3); fill: rgba(0,0,0,.5); color: hsl(10 20% 30%) }",
    ".node { filter: drop-shadow(1px 1px 2px rgba(0,0,0,0.2)); transform: translate(1px, 2px) }",
    "text { fill: var(--stage-text, #222); width: calc(100% - 2px) }",
    ".x { marker-end: url(#arrow) }",
    "@keyframes dash { to { stroke-dashoffset: 0 } } .e { animation: dash 2s cubic-bezier(.1,.2,.3,1) infinite }",
  ]) assert.ok(isSafeCss(css), css);
});

/** A tiny parser for the flat SVG markup of the vendored icon bodies (enough for them, nothing else). */
function parseIcon(body) {
  const root = new El("svg");
  const stack = [root];
  for (const m of body.matchAll(/<(\/?)([a-zA-Z]+)((?:\s+[a-zA-Z:-]+="[^"]*")*)\s*(\/?)>/g)) {
    const [, closing, tag, attrText, selfClosing] = m;
    if (closing) { stack.pop(); continue; }
    const attrs = Object.fromEntries([...attrText.matchAll(/([a-zA-Z:-]+)="([^"]*)"/g)].map((a) => [a[1], a[2]]));
    const el = new El(tag, attrs);
    const parent = stack.at(-1);
    el.parent = parent;
    parent.children.push(el);
    if (!selfClosing) stack.push(el);
  }
  return root;
}

test("the masks of the vendored icon packs survive (gcp cloud-functions), with local references only", async () => {
  const { readFileSync } = await import("node:fs");
  const pack = JSON.parse(readFileSync(new URL("../../arcviz/static/vendor/icons/gcp.json", import.meta.url), "utf8"));
  const root = parseIcon(pack.icons["cloud-functions"].body);
  const before = root.tags();
  sanitizeSvg(root, { htmlLabels: true });
  assert.deepEqual(root.tags(), before); // nothing of the icon is dropped
  const masked = [];
  const walk = (n) => { if (n.attrs.has("mask")) masked.push(n.attrs.get("mask")); n.children.forEach(walk); };
  walk(root);
  assert.ok(root.tags().includes("mask") && masked.length > 0 && masked.every((v) => /^url\(#[\w-]+\)$/.test(v)), masked.join());
  const evil = new El("svg", {}, [new El("mask", { id: "m" }), new El("rect", { mask: "url(https://evil.example/m.svg#m)" })]);
  sanitizeSvg(evil);
  assert.deepEqual([...evil.children[1].attrs.keys()], []);
});
