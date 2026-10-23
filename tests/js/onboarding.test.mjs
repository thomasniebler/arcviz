import { test } from "node:test";
import assert from "node:assert/strict";
import { availableStarters, moveStep, nextVisible, STARTERS, starterBody, TOUR_STEPS } from "../../arcviz/static/onboarding.js";

test("every starter names a type, a template and a first message", () => {
  assert.deepEqual(STARTERS.map((s) => s.type), ["deployment", "activity", "sequence"]);
  for (const s of STARTERS) assert.ok(s.template && s.prompt && s.newTitle && s.blurb);
});

test("starterBody creates an empty diagram or the example in the given project", () => {
  const [s] = STARTERS;
  assert.deepEqual(starterBody(s, "p1", false), { title: "Mein Deployment", type: "deployment", template: "empty", project: "p1" });
  const example = starterBody(s, "p1", true);
  assert.equal(example.template, "aks-database");
  assert.match(example.title, /Beispiel/);
});

test("availableStarters keeps the types the server offers", () => {
  const config = { diagram_types: { deployment: {}, sequence: {} } };
  assert.deepEqual(availableStarters(config).map((s) => s.type), ["deployment", "sequence"]);
  assert.deepEqual(availableStarters(null), []);
});

test("moveStep stays inside the tour and ends past the last step", () => {
  assert.equal(moveStep(0, -1), 0);
  assert.equal(moveStep(0, 1), 1);
  assert.equal(moveStep(TOUR_STEPS.length - 1, 1), null);
});

test("every tour step has a target, a title and a text", () => {
  for (const s of TOUR_STEPS) assert.ok(s.target && s.title && s.text);
});

test("nextVisible skips hidden steps and ends at both borders instead of looping", () => {
  const shownFrom = (first) => (i) => i >= first;
  assert.equal(nextVisible(0, 1, shownFrom(1)), 1);
  assert.equal(nextVisible(0, -1, shownFrom(1)), null); // going back from a hidden first step
  assert.equal(nextVisible(1, -1, shownFrom(2)), null);
  assert.equal(nextVisible(2, -1, shownFrom(1)), 2);
  assert.equal(nextVisible(0, 1, () => false), null);
  assert.equal(nextVisible(TOUR_STEPS.length, 1, () => true), null);
});
