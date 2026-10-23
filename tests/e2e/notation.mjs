// Notation test: what the vendored Mermaid can draw, and what it cannot, for the UML diagram types
// (spec: docs/architecture/specs/uml-graph-types.md, *Rendering*). No server needed.
//
//   npm test                      (from tests/e2e)
//   CHROMIUM_PATH=/path/to/chrome node notation.mjs
//
// SUPPORTED rows must render: a Mermaid upgrade that drops one fails here instead of in a user's diagram. UNSUPPORTED
// rows must fail: when an upgrade adds one, the spec's approximation table can be improved and the row moves over.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const here = dirname(fileURLToPath(import.meta.url));
const mermaidJs = readFileSync(join(here, "..", "..", "arcviz", "static", "vendor", "mermaid.min.js"), "utf8");

const SUPPORTED = {
  "initial pseudostate / initial node": "a@{ shape: f-circ }",
  "final state": "a@{ shape: fr-circ }",
  "terminate / flow final": "a@{ shape: cross-circ }",
  "junction": "a@{ shape: sm-circ }",
  "choice / decision": 'a@{ shape: diam, label: " " }',
  "fork / join bar": 'a@{ shape: fork, label: " " }',
  "history circle with a label": 'a@{ shape: circle, label: "H*" }',
  "use case as stadium": 'a@{ shape: stadium, label: "Bestellen" }',
  "interface as circle": 'a@{ shape: circle, label: "OrderApi" }',
  "classifier with compartments": 'a["«class»<br/>Order<br/>──────────<br/>- id: UUID<br/>──────────<br/>+ place(): Order"]',
  "rounded state": 'a("Neu<br/>──────────<br/>entry / x")',
  "association (no head)": "a --- b",
  "navigable association / generalization": "a --> b",
  "dependency / realization / include": 'a -.->|"«include»"| b',
  "aggregation end marker": "a --o b",
  "composition end marker (thick)": "a ==o b",
  "edge text with stereotype and multiplicities": 'a ---|"1 kunde – positionen 1..*"| b',
};
SUPPORTED["subgraph with a direction"] = 'subgraph s1["Webshop"]\n  direction LR\n  x --> y\n  end';

const UNSUPPORTED = {
  "ellipse shape (use cases are drawn as stadiums)": 'a@{ shape: ellipse, label: "x" }',
  "oval shape": 'a@{ shape: oval, label: "x" }',
  "end marker at the source end only (aggregation points from part to whole)": "a o-- b",
  "cross marker at the source end only": "a x-- b",
  "arrow head at the source end only": "a <-- b",
};

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, args: ["--no-sandbox"] });
const page = await browser.newPage();
await page.setContent("<html><body></body></html>");
await page.addScriptTag({ content: mermaidJs });
await page.evaluate(() => mermaid.initialize({ startOnLoad: false, securityLevel: "strict", htmlLabels: true }));

const failures = [];
let n = 0;
async function renders(source) {
  return page.evaluate(async ([id, text]) => {
    try { await mermaid.render(id, text); return true; } catch { return false; }
  }, [`probe${n++}`, `flowchart LR\n  ${source}`]);
}
for (const [name, source] of Object.entries(SUPPORTED)) {
  const ok = await renders(source);
  console.log(`${ok ? "✓" : "✗"} renders: ${name}`);
  if (!ok) failures.push(name);
}
for (const [name, source] of Object.entries(UNSUPPORTED)) {
  const ok = !(await renders(source));
  console.log(`${ok ? "✓" : "✗"} still unsupported: ${name}`);
  if (!ok) failures.push(name);
}
await browser.close();
if (failures.length) {
  console.error(`\n${failures.length} notation row(s) changed: ${failures.join("; ")}`);
  process.exit(1);
}
