import test from "node:test";
import assert from "node:assert/strict";
import { MAX_ASSUMPTIONS, blankAssumption, effortLabel, mergeSuggestion, normalizeRows, suggestionBody, testabilityChip } from "../../arcviz/static/testability.js";
import { changedFields } from "../../arcviz/static/decisions.js";

test("rows are normalised like the server's dump, so unchanged rows are not a change", () => {
  const stored = [{ statement: "A", test: "t", effort: "low", evidence: "" }, { statement: "B", test: "", effort: "high", status: "confirmed", evidence: "ok" }];
  const rows = stored.map((r) => ({ ...blankAssumption(), ...r }));
  assert.deepEqual(normalizeRows(rows), stored);
  assert.equal(changedFields({ assumptions: stored }, { assumptions: normalizeRows(rows) }, ["assumptions"]), null);
});

test("rows without a statement are dropped, texts trimmed", () => {
  assert.deepEqual(normalizeRows([{ statement: "  " }, { statement: " x ", test: " y " }, null]), [{ statement: "x", test: "y", effort: "medium", evidence: "" }]);
  assert.deepEqual(normalizeRows(undefined), []);
});

test("a suggestion adds only new statements and never replaces what a person wrote", () => {
  const rows = [{ ...blankAssumption(), statement: "Failover < 60 s", test: "mein Test" }];
  const { rows: merged, added } = mergeSuggestion(rows, [{ statement: "failover < 60 S", test: "x" }, { statement: "Team kann K8s", test: "Interview", effort: "low" }, { statement: "" }, { statement: "Z", effort: "riesig" }]);
  assert.equal(added, 2);
  assert.equal(merged[0].test, "mein Test");
  assert.deepEqual(merged.map((r) => r.effort), ["medium", "low", "medium"]);
});

test("at most eight rows", () => {
  const suggested = Array.from({ length: 20 }, (_, n) => ({ statement: `s${n}` }));
  const { rows, added } = mergeSuggestion([], suggested);
  assert.equal(rows.length, MAX_ASSUMPTIONS);
  assert.equal(added, MAX_ASSUMPTIONS);
  assert.equal(mergeSuggestion(rows, suggested).added, 0);
});

test("the request carries the decision as typed", () => {
  const body = suggestionBody({ title: "T", context: " ", decision: "D" }, [{ title: "A", pros: ["p"] }], 0);
  assert.deepEqual(body, { title: "T", decision: "D", options: [{ title: "A", pros: ["p"], cons: [] }], chosen: 0 });
});

test("the chip names the gap, the refutation or the count", () => {
  assert.equal(testabilityChip({ status: "draft" }), null);
  assert.equal(testabilityChip({ status: "accepted" }).tone, "warn");
  assert.equal(testabilityChip({ assumptions: [{ status: "refuted" }, {}] }).tone, "danger");
  assert.deepEqual(testabilityChip({ assumptions: [{ status: "confirmed" }] }).tone, "ok");
  assert.equal(testabilityChip({ assumptions: [{}, { status: "confirmed" }] }).text, "2 Annahmen, 1 ungetestet");
  assert.equal(effortLabel("low"), "gering");
  assert.equal(effortLabel("x"), "x");
});
