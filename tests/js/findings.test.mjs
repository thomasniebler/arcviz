// Unit tests for the review findings helpers. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { CATEGORY_LABELS, SEVERITY_LABELS, diagramKey, edgeSvgKeys, elementNames, findingTarget, groupFindings, highlightable, mergeFindings, suggestionPrompt } from "../../arcviz/static/findings.js";
import { reviewMeta } from "../../arcviz/static/format.js";

const f = (id, category, severity = "low", extra = {}) => ({ id, source: "rule", category, severity, message: id, elements: [], edges: [], suggestion: null, ...extra });

test("labels cover every category and severity", () => {
  assert.deepEqual(CATEGORY_LABELS, { risk: "Risiken", suggestion: "Verbesserungen", strength: "Stärken" });
  assert.deepEqual(SEVERITY_LABELS, { high: "hoch", medium: "mittel", low: "niedrig", info: "Info" });
});

test("groups in the order risks, suggestions, strengths and keeps the order inside a group", () => {
  const groups = groupFindings([f("s1", "strength", "info"), f("r1", "risk", "high"), f("x", "suggestion"), f("r2", "risk", "low")]);
  assert.deepEqual(groups.map((g) => [g.category, g.label, g.findings.map((x) => x.id)]), [
    ["risk", "Risiken", ["r1", "r2"]],
    ["suggestion", "Verbesserungen", ["x"]],
    ["strength", "Stärken", ["s1"]],
  ]);
  assert.deepEqual(groupFindings([]), []);
  assert.deepEqual(groupFindings(null), []);
});

test("element names skip unknown ids", () => {
  const nodes = { a: { id: "a", name: "Service A" }, db: { id: "db", name: "orders-db" } };
  assert.deepEqual(elementNames({ elements: ["db", "ghost", "a"] }, nodes), ["orders-db", "Service A"]);
  assert.deepEqual(elementNames({}, nodes), []);
});

test("edge keys are L_<from>_<to>_<n>, numbered per node pair as Mermaid 11 does (0, 2, 3, …)", () => {
  const ids = { a: "n_a", b: "n_b", "c-1": "n_c_1" };
  const edges = [
    { id: "e1", source: "a", target: "b" },
    { id: "e2", source: "a", target: "c-1" },
    { id: "e3", source: "a", target: "b" },
    { id: "e4", source: "b", target: "a" },
    { id: "e5", source: "a", target: "b" },
  ];
  assert.deepEqual(edgeSvgKeys(["e3", "e1", "e2", "e4", "e5"], edges, ids), ["L_n_a_n_b_2", "L_n_a_n_b_0", "L_n_a_n_c_1_0", "L_n_b_n_a_0", "L_n_a_n_b_3"]);
  assert.deepEqual(edgeSvgKeys(["e9"], edges, ids), []);
  assert.deepEqual(edgeSvgKeys(undefined, edges, ids), []);
});

test("edges that are not drawn neither get a key nor count", () => {
  const ids = { a: "n_a", b: "n_b", x: "n_x" };
  const edges = [{ id: "e1", source: "a", target: "ghost" }, { id: "e2", source: "x", target: "a" }, { id: "e3", source: "a", target: "b" }, { id: "e4", source: "a", target: "b" }];
  assert.deepEqual(edgeSvgKeys(["e1", "e2", "e3", "e4"], edges, ids), ["L_n_x_n_a_0", "L_n_a_n_b_0", "L_n_a_n_b_2"]);
  // focused on a container holding only b: edges with no endpoint in view are not drawn
  assert.deepEqual(edgeSvgKeys(["e2", "e4"], edges, ids, new Set(["b"])), ["L_n_a_n_b_2"]);
});

test("the diagram key ignores timestamps and changes with the content", () => {
  const d = { id: "d", title: "T", direction: "TB", nodes: { a: { id: "a", name: "a", kind: "service", parent: null, properties: {} } }, edges: [], created_at: "x", updated_at: "y" };
  assert.equal(diagramKey(d), diagramKey({ ...d, updated_at: "z" }));
  assert.notEqual(diagramKey(d), diagramKey({ ...d, title: "U" }));
  assert.notEqual(diagramKey(d), diagramKey({ ...d, hidden_relations: ["r1"] })); // the project review re-checks
  assert.notEqual(diagramKey(d), diagramKey({ ...d, complete: ["communicates"] }));
  // sequence diagrams keep their content in `interaction`
  const seq = { ...d, interaction: { lifelines: [{ id: "a", name: "A" }], fragments: [] } };
  assert.notEqual(diagramKey(seq), diagramKey({ ...seq, interaction: { lifelines: [], fragments: [] } }));
  assert.notEqual(diagramKey(d), diagramKey({ ...d, edges: [{ id: "e1", source: "a", target: "a" }] }));
  assert.equal(diagramKey(null), "");
});

test("meta line of a review result", () => {
  assert.equal(reviewMeta({ findings: [f("a", "risk")] }), "1 Befund");
  assert.equal(reviewMeta({ findings: [f("a", "risk"), f("b", "risk")], llm: { ok: true, dropped: 0, usage: { input_tokens: 1000, output_tokens: 200 } } }), "2 Befunde · 1.200 Tokens");
  assert.equal(reviewMeta({ findings: [], llm: { ok: false, error: "RuntimeError: x", dropped: 0, usage: {} } }), "0 Befunde · KI-Review fehlgeschlagen: RuntimeError: x");
  assert.equal(reviewMeta({ findings: [], llm: { ok: true, dropped: 2, usage: {} } }), "0 Befunde · 2 ungültige KI-Befunde verworfen");
  assert.equal(reviewMeta({ findings: [], llm: { ok: true, dropped: 0, partial: true, usage: {} } }), "0 Befunde · Diagramm zu groß: eingeklappte Container nicht von der KI geprüft");
});

test("merging rule and LLM findings sorts by severity, then category, keeping the order otherwise", () => {
  const rules = [f("r-high", "risk", "high"), f("r-low", "suggestion", "low")];
  const llm = [f("l-info", "strength", "info"), f("l-med", "risk", "medium"), f("l-high-s", "suggestion", "high")];
  assert.deepEqual(mergeFindings(rules, llm).map((x) => x.id), ["r-high", "l-high-s", "l-med", "r-low", "l-info"]);
  assert.deepEqual(mergeFindings([], undefined), []);
});

test("the instruction of a suggestion names the finding and its elements", () => {
  const nodes = { "orders-db": { id: "orders-db", name: "Orders DB" }, "service-a": { id: "service-a", name: "service-a" } };
  const rule = f("database-without-replication:orders-db", "risk", "high", {
    message: "Die Datenbank „Orders DB“ hat keine Replikation.", elements: ["orders-db", "ghost"], suggestion: "Füge eine Replik hinzu.",
  });
  assert.equal(suggestionPrompt(rule, nodes), [
    "Setze diesen Vorschlag aus dem Architektur-Review um: Füge eine Replik hinzu.",
    "Befund (Risiko, hoch): Die Datenbank „Orders DB“ hat keine Replikation.",
    "Betroffene Elemente: Orders DB (orders-db)",
  ].join("\n"));
  const llm = f("llm-1", "suggestion", "low", { source: "llm", message: "TLS fehlt.", elements: ["service-a"], edges: ["e3"], suggestion: "Setze protocol=tls an e3." });
  assert.equal(suggestionPrompt(llm, nodes), "Setze diesen Vorschlag aus dem Architektur-Review um: Setze protocol=tls an e3.\nBefund (Verbesserung, niedrig): TLS fehlt.\nBetroffene Elemente: service-a (service-a) · Betroffene Kanten: e3");
  assert.equal(suggestionPrompt({ ...llm, elements: [], edges: [] }, nodes), "Setze diesen Vorschlag aus dem Architektur-Review um: Setze protocol=tls an e3.\nBefund (Verbesserung, niedrig): TLS fehlt.");
  assert.equal(suggestionPrompt(f("x", "risk", "high"), nodes), null);
  assert.equal(suggestionPrompt(f("s", "strength", "info", { suggestion: "egal" }), nodes), null);
});

// ---- project review (spec: docs/architecture/specs/project-review.md) ----

test("findingTarget labels a project finding by its diagram, section or the project", () => {
  const diagrams = { demo: { title: "Demo deployment", type: "deployment", section: "backend" } };
  const sections = [{ id: "backend", title: "Backend" }];
  assert.deepEqual(findingTarget({ diagram: "demo", section: null }, diagrams, sections), { kind: "diagram", id: "demo", label: "Diagramm: Demo deployment" });
  assert.deepEqual(findingTarget({ diagram: "gone" }, diagrams, sections), { kind: "diagram", id: "gone", label: "Diagramm: gone" });
  assert.deepEqual(findingTarget({ diagram: null, section: "backend" }, diagrams, sections), { kind: "section", id: "backend", label: "Abschnitt: Backend" });
  assert.deepEqual(findingTarget({ diagram: null, section: null }, diagrams, sections), { kind: "project", id: null, label: "Projekt" });
  assert.deepEqual(findingTarget({ diagrams: ["a", "b"], diagram: "a" }, { a: { title: "A" }, b: { title: "B" } }, []), { kind: "diagram", id: "a", label: "Diagramme: A, B" });
});

test("highlightable: only findings of the shown diagram in project mode, every finding in diagram mode", () => {
  assert.equal(highlightable({ diagram: "demo" }, "demo", true), true);
  assert.equal(highlightable({ diagram: "other" }, "demo", true), false);
  assert.equal(highlightable({ diagram: null }, "demo", true), false);
  assert.equal(highlightable({ elements: ["x"] }, "demo", false), true);
});

test("a project finding names its model element", async () => {
  const { modelElementLabel } = await import("../../arcviz/static/findings.js");
  assert.equal(modelElementLabel({ model_element: "db" }, { db: { name: "Datenbank", kind: "data" } }), "Element: Datenbank");
  assert.equal(modelElementLabel({ model_element: "gone" }, {}), "Element: gone");
  assert.equal(modelElementLabel({}, {}), null);
});
