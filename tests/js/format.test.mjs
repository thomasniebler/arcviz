// Unit tests for the UI labels. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chatEntryMeta, compareSummary, describeCall, diagramStats, edgeSummary, exportName, finalMeta,
  authorName, tokenMeta, toolError, truncate, versionLabel, versionOptionLabel, versionTitle,
} from "../../arcviz/static/format.js";

test("describeCall shows strings raw and everything else as JSON", () => {
  assert.equal(describeCall("add_node", { name: "db", parent: null, properties: { a: 1 } }), 'add_node(name=db, parent=null, properties={"a":1})');
  assert.equal(describeCall("get_diagram", undefined), "get_diagram()");
});

test("toolError extracts the error of a JSON tool result", () => {
  assert.equal(toolError('{"error": "Unknown node \'x\'"}'), "Unknown node 'x'");
  assert.equal(toolError('{"ok": true}'), '{"ok": true}');
  assert.equal(toolError("plain text"), "plain text");
});

test("tokenMeta sums input and output tokens", () => {
  assert.equal(tokenMeta({ input_tokens: 1200, output_tokens: 34 }), "1.234 Tokens");
  assert.equal(tokenMeta({}), "");
  assert.equal(tokenMeta(undefined), "");
});

test("truncate adds an ellipsis only when needed", () => {
  assert.equal(truncate("abc", 3), "abc");
  assert.equal(truncate("abcd", 3), "ab…");
});

test("diagram and version labels", () => {
  const v = { version: 7, label: "x".repeat(60), source: "auto", created_at: "2026-10-06T10:00:00Z" };
  const opt = versionOptionLabel(v);
  assert.ok(opt.startsWith(`v7 · ${"x".repeat(47)}… (auto, `), opt);
  assert.equal(versionLabel(v), `v7 · ${"x".repeat(39)}…`);
  assert.equal(versionLabel({ version: 1, label: "Start" }), "v1 · Start");
  const stats = diagramStats({ nodes: { a: {}, b: {} }, edges: [{}], updated_at: "2026-10-06T10:00:00Z" });
  assert.ok(stats.startsWith("2 Elemente · 1 Kanten · zuletzt "), stats);
});

test("chat meta lines", () => {
  assert.equal(chatEntryMeta({ operations: [{}, {}], version: 4, usage: { input_tokens: 10, output_tokens: 5 } }), "2 Operation(en) · Version v4 · 15 Tokens");
  assert.equal(chatEntryMeta({ operations: [], proposed: true }), "Vorschlag");
  assert.equal(chatEntryMeta({}), "");
  assert.equal(finalMeta({ operations: [{}], version: { version: 9 }, usage: null }), "1 Operation(en) · Version v9");
  assert.equal(finalMeta({ operations: [], version: null }), "");
});

test("compareSummary", () => {
  const diff = { summary: { added: 1, removed: 2, changed: 3 }, title_changed: false, direction_changed: false };
  assert.equal(compareSummary(1, null, diff), "Vergleich v1 → aktueller Stand: +1 neu · −2 entfernt · ~3 geändert");
  assert.equal(compareSummary(1, 4, { ...diff, title_changed: true, direction_changed: true }),
    "Vergleich v1 → v4: +1 neu · −2 entfernt · ~3 geändert · Titel geändert · Richtung geändert");
});

test("edgeSummary shows the explicit direction and the property count", () => {
  assert.equal(edgeSummary({}), "⋯");
  assert.equal(edgeSummary({ direction: "both" }), "↔");
  assert.equal(edgeSummary({ properties: { a: "1", b: "2" } }), "2");
  assert.equal(edgeSummary({ direction: "none", properties: { a: "1" } }), "— 1");
  assert.equal(edgeSummary({ direction: "default", properties: {} }), "⋯");
});

test("exportName reflects focus, preview and comparison", () => {
  assert.equal(exportName({ diagramId: "demo" }, "svg"), "demo.svg");
  assert.equal(exportName({}, "png"), "diagram.png");
  assert.equal(exportName({ diagramId: "demo", focus: "vnet", previewVersion: 3 }, "svg"), "demo-vnet-v3.svg");
  assert.equal(exportName({ diagramId: "demo", compare: { base: 1, target: null } }, "png"), "demo-v1-vs-aktuell.png");
  assert.equal(exportName({ diagramId: "demo", compare: { base: 1, target: 5 } }, "png"), "demo-v1-vs-v5.png");
});

test("diagramStats counts lifelines and messages of sequence diagrams", async () => {
  const { diagramStats } = await import("../../arcviz/static/format.js");
  const d = { nodes: {}, edges: [], updated_at: "2026-10-06T10:00:00Z", interaction: { lifelines: [{ id: "a" }, { id: "b" }], fragments: [{ element: "message", id: "m1" }, { element: "combined_fragment", id: "cf1", operands: [{ id: "op1", fragments: [{ element: "message", id: "m2" }] }] }], executions: [] } };
  assert.match(diagramStats(d), /^2 Lebenslinien · 2 Nachrichten · zuletzt /);
});

test("authors of versions and chat entries are shown when known (M20)", () => {
  const v = { version: 3, label: "Umbenannt", source: "auto", created_at: "2026-10-07T10:00:00Z", author: "ada", author_name: "Ada Lovelace" };
  assert.ok(versionOptionLabel(v).endsWith(", Ada Lovelace)"), versionOptionLabel(v));
  assert.equal(versionTitle(v), "Umbenannt – von Ada Lovelace");
  const legacy = { version: 1, label: "Initial state", source: "initial", created_at: "2026-10-01T10:00:00Z" };
  assert.ok(versionOptionLabel(legacy).endsWith(")") && !versionOptionLabel(legacy).includes("undefined"));
  assert.equal(versionTitle(legacy), "Initial state");
  assert.equal(authorName({ author: "8d1f-oid" }), "8d1f-oid");
  assert.equal(authorName({ role: "user", content: "x" }), "");
});
