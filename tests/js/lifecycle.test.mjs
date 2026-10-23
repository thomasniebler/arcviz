import test from "node:test";
import assert from "node:assert/strict";
import { deleteGate, isRetired, lifecycleItems, requirementDeleteConfirm, requirementsDeleteConfirm, retireLabel, toggledStatus } from "../../arcviz/static/lifecycle.js";

test("retired items are hidden unless asked for, order kept", () => {
  const items = [{ id: "d1" }, { id: "d2", status: "retired" }, { id: "d3", status: "active" }];
  assert.deepEqual(lifecycleItems(items, false).map((i) => i.id), ["d1", "d3"]);
  assert.deepEqual(lifecycleItems(items, true).map((i) => i.id), ["d1", "d2", "d3"]);
  assert.equal(isRetired(items[1]), true);
  assert.equal(isRetired(null), false);
});

test("the retire button toggles", () => {
  assert.equal(retireLabel({}), "Außer Kraft setzen");
  assert.equal(toggledStatus({}), "retired");
  assert.equal(retireLabel({ status: "retired" }), "Wieder aktivieren");
  assert.equal(toggledStatus({ status: "retired" }), "active");
});

test("drivers and scenarios need retirement, decisions must not be accepted, requirements are free", () => {
  for (const kind of ["driver", "scenario"]) {
    assert.equal(deleteGate(kind, {}).allowed, false);
    assert.match(deleteGate(kind, {}).reason, /außer Kraft/);
    assert.equal(deleteGate(kind, { status: "retired" }).allowed, true);
  }
  assert.equal(deleteGate("decision", { status: "accepted" }).allowed, false);
  for (const status of ["draft", "rejected", "superseded"]) assert.equal(deleteGate("decision", { status }).allowed, true);
  assert.equal(deleteGate("requirement", { id: "req1" }).allowed, true);
  assert.equal(deleteGate("driver", null).allowed, false);
});

test("the requirement confirmations name the count and the references", () => {
  assert.match(requirementDeleteConfirm({ id: "req3" }), /req3/);
  const rows = [{ id: "req1", coverage: "driver" }, { id: "req2", coverage: "uncovered" }, { id: "req3", coverage: null }];
  assert.match(requirementsDeleteConfirm(rows, ["req1", "req2", "req3"]), /^3 Anforderungen löschen\? Mindestens 1 davon/);
  assert.match(requirementsDeleteConfirm(rows, ["req2"]), /^1 Anforderung löschen\? Das lässt/);
});
