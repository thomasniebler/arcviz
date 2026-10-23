// Unit tests for the list helpers of the Architektur workspace (spec: docs/architecture/specs/architecture-workspace.md).
import { test } from "node:test";
import assert from "node:assert/strict";
import { filterItems, stepId } from "../../arcviz/static/listnav.js";

const items = [
  { id: "a", text: "Verfügbarkeit 99,9 %" },
  { id: "b", text: "Kosten unter 2000 € im Monat" },
  { id: "c", text: "Verfügbarkeit der Zahlung" },
  { id: "d", text: "Äußerst sicher" },
];
const textOf = (i) => i.text;

test("an empty or blank query keeps every item in order", () => {
  for (const q of ["", "   ", null, undefined]) assert.deepEqual(filterItems(items, q, textOf).map((i) => i.id), ["a", "b", "c", "d"]);
});

test("every word of the query must occur, case-insensitively, in any order", () => {
  assert.deepEqual(filterItems(items, "verfügbarkeit", textOf).map((i) => i.id), ["a", "c"]);
  assert.deepEqual(filterItems(items, "ZAHLUNG verfügbarkeit", textOf).map((i) => i.id), ["c"]);
  assert.deepEqual(filterItems(items, "monat kosten", textOf).map((i) => i.id), ["b"]);
  assert.deepEqual(filterItems(items, "nichts", textOf), []);
});

test("umlauts match case-insensitively and the order of the items is kept", () => {
  assert.deepEqual(filterItems(items, "äUßERST", textOf).map((i) => i.id), ["d"]);
  assert.deepEqual(filterItems([...items].reverse(), "verfügbarkeit", textOf).map((i) => i.id), ["c", "a"]);
});

test("stepId moves within the ids and clamps at the ends", () => {
  const ids = ["x", "y", "z"];
  assert.equal(stepId(ids, "x", 1), "y");
  assert.equal(stepId(ids, "y", -1), "x");
  assert.equal(stepId(ids, "z", 1), "z");
  assert.equal(stepId(ids, "x", -1), "x");
});

test("stepId without a selection starts at the first (down) or the last (up) id", () => {
  const ids = ["x", "y", "z"];
  assert.equal(stepId(ids, null, 1), "x");
  assert.equal(stepId(ids, null, -1), "z");
  assert.equal(stepId(ids, "gone", 1), "x");
});

test("stepId of an empty list is null", () => {
  assert.equal(stepId([], null, 1), null);
  assert.equal(stepId([], "x", -1), null);
});
