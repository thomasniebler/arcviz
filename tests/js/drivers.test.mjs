// Unit tests for the architecture driver helpers (spec: docs/architecture/specs/architecture-drivers.md).
// Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { concernsText, driverChanges, driverKindLabel, moveTarget, priorityBadge, priorityTitle, sortedDrivers } from "../../arcviz/static/drivers.js";

const d = (id, importance, risk, extra = {}) => ({ id, title: id, kind: "quality_goal", importance, risk, description: "", elements: [], ...extra });

test("labels and the priority badge", () => {
  assert.equal(driverKindLabel("constraint"), "Randbedingung");
  assert.equal(driverKindLabel("other"), "other");
  assert.equal(priorityBadge(d("d1", "high", "medium")), "H/M");
  assert.equal(priorityTitle(d("d1", "low", "high")), "Wichtigkeit niedrig, Risiko hoch");
});

test("sorted by importance, then risk, then list order", () => {
  const list = [d("ll", "low", "low"), d("hm", "high", "medium"), d("hh", "high", "high"), d("hh2", "high", "high"), d("mh", "medium", "high")];
  assert.deepEqual(sortedDrivers(list).map((x) => x.id), ["hh", "hh2", "hm", "mh", "ll"]);
  assert.deepEqual(list.map((x) => x.id), ["ll", "hm", "hh", "hh2", "mh"]); // not changed in place
});

test("moves stay inside a priority class", () => {
  const list = [d("a", "high", "high"), d("x", "low", "low"), d("b", "high", "high")];
  assert.equal(moveTarget(list, "b", -1), 0); // before a
  assert.equal(moveTarget(list, "a", 1), 2); // after b
  assert.equal(moveTarget(list, "a", -1), null);
  assert.equal(moveTarget(list, "b", 1), null); // x is another class
  assert.equal(moveTarget(list, "nope", 1), null);
});

test("only changed fields are sent", () => {
  const driver = d("d1", "high", "low", { description: "x", elements: ["api"] });
  assert.equal(driverChanges(driver, { title: " d1 ", kind: "quality_goal", importance: "high", risk: "low", description: "x", elements: ["api"] }), null);
  assert.deepEqual(driverChanges(driver, { title: "Last", importance: "medium", description: "", elements: [] }), { title: "Last", importance: "medium", description: "", elements: [] });
});

test("concerned elements by name", () => {
  assert.equal(concernsText(d("d1", "high", "low", { elements: ["api", "gone"] }), [{ id: "api", name: "API" }]), "betrifft: API, gone");
  assert.equal(concernsText(d("d1", "high", "low"), []), "");
});

test("driverChanges compares the requirements the driver covers", () => {
  const same = { title: "d1", description: "" };
  const driver = d("d1", "high", "low", { requirements: ["req1"] });
  assert.equal(driverChanges(driver, { ...same, requirements: ["req1"] }), null);
  assert.deepEqual(driverChanges(driver, { ...same, requirements: ["req1", "req2"] }), { requirements: ["req1", "req2"] });
  assert.deepEqual(driverChanges(d("d2", "high", "low"), { title: "d2", description: "", requirements: ["req3"] }), { requirements: ["req3"] });
  assert.equal(driverChanges(d("d2", "high", "low"), { title: "d2", description: "", requirements: [] }), null);
});
