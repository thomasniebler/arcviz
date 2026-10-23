// Unit tests for the requirements view's pure helpers. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  acceptItems, coverageLabel, displayText, importProblemLines, importSummary, isUnremarkable, kindLabel, listQuery, pipeline,
  optionLabel, priorityLabel, progressText, requirementsText, unknownIds, rangeIds, shouldContinue, stepLabel, suggestionFields, toggled, unremarkableIds,
} from "../../arcviz/static/requirements.js";

test("labels are German and unknown values stay visible", () => {
  assert.equal(stepLabel("suggested"), "Vorschlag");
  assert.equal(kindLabel("quality"), "Qualität");
  assert.equal(priorityLabel("must"), "Muss");
  assert.equal(priorityLabel(""), "–");
  assert.equal(coverageLabel(null), "–");
  assert.equal(coverageLabel("scenario"), "Szenario");
  assert.equal(kindLabel("odd"), "odd");
});

test("a row shows the statement, else the original wording", () => {
  assert.equal(displayText({ raw: "roh", statement: "Aussage." }), "Aussage.");
  assert.equal(displayText({ raw: "roh", statement: "" }), "roh");
});

test("the list query leaves out empty filters", () => {
  assert.equal(listQuery({}), "?limit=100");
  assert.equal(listQuery({ step: "raw", q: "abc", theme: "", suggested: true }, { limit: 50, offset: 100 }), "?step=raw&q=abc&suggested=true&limit=50&offset=100");
  assert.equal(listQuery(undefined, { offset: 0 }), "?limit=100");
});

test("the pipeline lists every step in order with its count", () => {
  assert.deepEqual(pipeline({ steps: { raw: 5, refined: 2 } }).map((p) => [p.step, p.count]), [
    ["raw", 5], ["suggested", 0], ["refined", 2], ["derived", 0], ["rejected", 0], ["duplicate", 0],
  ]);
  assert.equal(pipeline(null).length, 6);
});

const rows = [
  { id: "req1", suggestion: { rev: "a1", issues: [] } },
  { id: "req2", suggestion: { rev: "b2", issues: [{ kind: "conflict", resolved: false }] } },
  { id: "req3", suggestion: { rev: "c3", issues: [{ kind: "conflict", resolved: true }, { kind: "ambiguous" }] } },
  { id: "req4", suggestion: { rev: "d4", issues: [], duplicate_of: "req1" } },
  { id: "req5", suggestion: null },
];

test("accept items name the revision the user saw and skip rows without a suggestion", () => {
  assert.deepEqual(acceptItems(rows, ["req1", "req5", "req3"]), [{ id: "req1", expected: "a1" }, { id: "req3", expected: "c3" }]);
  assert.deepEqual(acceptItems(rows, ["req1"], ["theme"]), [{ id: "req1", expected: "a1", fields: ["theme"] }]);
  assert.deepEqual(acceptItems(rows, []), []);
});

test("the unremarkable bulk button skips open conflicts, duplicates and suggested duplicate_of", () => {
  assert.equal(isUnremarkable(rows[0]), true);
  assert.equal(isUnremarkable(rows[1]), false);
  assert.equal(isUnremarkable(rows[2]), true); // a resolved conflict does not count
  assert.equal(isUnremarkable(rows[3]), false);
  assert.equal(isUnremarkable(rows[4]), false);
  assert.deepEqual(unremarkableIds(rows), ["req1", "req3"]);
});

test("shift-click ranges and toggling", () => {
  const order = ["a", "b", "c", "d", "e"];
  assert.deepEqual(rangeIds(order, "b", "d"), ["b", "c", "d"]);
  assert.deepEqual(rangeIds(order, "d", "b"), ["b", "c", "d"]);
  assert.deepEqual(rangeIds(order, null, "c"), ["c"]);
  assert.deepEqual(rangeIds(order, "a", "zz"), []);
  assert.deepEqual(toggled(["a", "b"], "b"), ["a"]);
  assert.deepEqual(toggled(["a"], "c"), ["a", "c"]);
});

test("suggestionFields shows what would change and what was accepted", () => {
  const row = {
    statement: "", kind: "unclear", priority: "", measure: "", theme: "Daten", stakeholders: [], duplicate_of: null,
    suggestion: {
      statement: "Der Shop antwortet schnell.", kind: "quality", priority: "must", measure: "", theme: "Daten",
      stakeholders: ["s1"], issues: [{ kind: "not_measurable", text: "x" }], duplicate_of: null, accepted: ["kind"],
    },
  };
  const fields = suggestionFields(row, (id) => `Name ${id}`);
  assert.deepEqual(fields.map((f) => f.field), ["statement", "kind", "priority", "stakeholders", "issues"]);
  assert.deepEqual(fields.find((f) => f.field === "kind"), { field: "kind", label: "Art", current: "Unklar", suggested: "Qualität", accepted: true });
  assert.equal(fields.find((f) => f.field === "stakeholders").suggested, "Name s1");
  assert.equal(fields.find((f) => f.field === "issues").suggested, "nicht messbar");
  assert.deepEqual(suggestionFields({ suggestion: null }), []);
});

test("a chunk loop goes on only while it works, something is left and it makes progress", () => {
  const ok = { llm: { ok: true }, remaining: 10, refined: ["req1"] };
  assert.equal(shouldContinue(ok, null), true);
  assert.equal(shouldContinue(ok, 11), true);
  assert.equal(shouldContinue(ok, 10), false); // nothing left the queue
  assert.equal(shouldContinue({ ...ok, remaining: 0 }, 10), false);
  assert.equal(shouldContinue({ ...ok, refined: [] }, 20), false);
  assert.equal(shouldContinue({ ...ok, llm: { ok: false } }, 20), false);
  assert.equal(shouldContinue({ llm: { ok: true }, remaining: 3, proposal: { id: "p1" }, refined: undefined }, 5), true);
  assert.equal(shouldContinue(null, 1), false);
});

test("progress and import texts", () => {
  assert.equal(progressText("Aufbereiten", 2, 14), "Aufbereiten: 2 Durchgang/Durchgänge, noch 14 offen …");
  assert.equal(progressText("Aufbereiten", 3, 0), "Aufbereiten: fertig nach 3 Durchgang/Durchgängen.");
  const dry = { dry_run: true, format: "csv", added: ["req1", "req2"], skipped_duplicates: [{ row: 3, reason: "x" }], rejected: [], problems: [] };
  assert.equal(importSummary(dry), "Würde 2 anlegen, 1 schon vorhanden. Format: csv.");
  const real = { dry_run: false, format: "lines", added: ["req1"], skipped_duplicates: [], rejected: [{ row: 2, reason: "zu lang" }], problems: [{ row: 5, reason: "gekürzt" }] };
  assert.equal(importSummary(real), "1 angelegt, 0 schon vorhanden, 1 abgelehnt, 1 Hinweis(e). Format: lines.");
  assert.deepEqual(importProblemLines(real), ["Zeile 2: zu lang", "Zeile 5: gekürzt"]);
  const many = { problems: Array.from({ length: 12 }, (_, n) => ({ row: n + 1, reason: "r" })) };
  assert.equal(importProblemLines(many, 3).at(-1), "… und 9 weitere");
});

test("picker labels, the covers text and unknown ids", () => {
  assert.equal(optionLabel({ id: "req3", raw: "roh", statement: "Der Shop antwortet." }), "req3 · Der Shop antwortet.");
  assert.equal(optionLabel({ id: "req4", raw: "x".repeat(100), statement: "" }, 10), `req4 · ${"x".repeat(9)}…`);
  assert.equal(requirementsText({ requirements: ["req1", "req3"] }), "deckt: req1, req3");
  assert.equal(requirementsText({}), "");
  assert.deepEqual(unknownIds(["req1", "req9"], [{ id: "req1" }]), ["req9"]);
  assert.deepEqual(unknownIds(undefined, []), []);
});
