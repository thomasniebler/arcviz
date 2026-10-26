import assert from "node:assert/strict";
import test from "node:test";
import { bodyFromForm, changed, formValues, parseLines, problems } from "../../arcviz/static/vision.js";

test("parseLines keeps one trimmed entry per non-blank line", () => {
  assert.deepEqual(parseLines(" a \n\n b\n  \n"), ["a", "b"]);
  assert.deepEqual(parseLines(undefined), []);
});

test("formValues and bodyFromForm round trip", () => {
  const vision = { statement: "Schnell", goals: ["A", "B"], non_goals: ["X"] };
  assert.deepEqual(bodyFromForm(formValues(vision)), vision);
  assert.deepEqual(bodyFromForm(formValues(null)), { statement: "", goals: [], non_goals: [] });
});

test("problems reports the bounds of the server", () => {
  assert.deepEqual(problems({ statement: "x".repeat(2001), goals: [], non_goals: [] }), ["statement"]);
  assert.deepEqual(problems({ statement: "", goals: Array(11).fill("g"), non_goals: [] }), ["count"]);
  assert.deepEqual(problems({ statement: "", goals: [], non_goals: ["x".repeat(201)] }), ["length"]);
  assert.deepEqual(problems({ statement: "ok", goals: ["g"], non_goals: [] }), []);
});

test("changed ignores whitespace-only differences", () => {
  const vision = { statement: "Schnell", goals: ["A"], non_goals: [] };
  assert.equal(changed(vision, { statement: " Schnell ", goals: "A\n", non_goals: "" }), false);
  assert.equal(changed(vision, { statement: "Schnell", goals: "A\nB", non_goals: "" }), true);
  assert.equal(changed(null, { statement: "", goals: "", non_goals: "" }), false);
});
