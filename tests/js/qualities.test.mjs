import test from "node:test";
import assert from "node:assert/strict";
import { MAX_QUALITIES, QUALITY_NAMES, qualitiesText, qualityChoices, qualityLabel, suggestionBody, toggledQuality, validQualities } from "../../arcviz/static/qualities.js";

test("the nine characteristics of ISO/IEC 25010:2023, at most two per scenario", () => {
  assert.equal(QUALITY_NAMES.length, 9);
  assert.equal(MAX_QUALITIES, 2);
  assert.equal(qualityLabel("reliability"), "Zuverlässigkeit");
  assert.equal(qualityLabel("blau"), "blau");
});

test("a model's answer is reduced to known, distinct names, at most two", () => {
  assert.deepEqual(validQualities(["blau", "security", "security", "safety", "reliability"]), ["security", "safety"]);
  assert.deepEqual(validQualities(["x", 7, null]), []);
  assert.deepEqual(validQualities("security"), []);
  assert.deepEqual(validQualities(undefined), []);
});

test("two choices disable the other seven instead of dropping one silently", () => {
  const choices = qualityChoices(["security", "reliability"]);
  assert.deepEqual(choices.filter((c) => c.checked).map((c) => c.name), ["reliability", "security"]);
  assert.equal(choices.filter((c) => c.disabled).length, 7);
  assert.equal(qualityChoices(["security"]).filter((c) => c.disabled).length, 0);
  assert.equal(qualityChoices([]).filter((c) => c.checked).length, 0);
});

test("toggling adds, removes and refuses a third", () => {
  assert.deepEqual(toggledQuality([], "security"), ["security"]);
  assert.deepEqual(toggledQuality(["security"], "safety"), ["security", "safety"]);
  assert.deepEqual(toggledQuality(["security", "safety"], "safety"), ["security"]);
  assert.deepEqual(toggledQuality(["security", "safety"], "reliability"), ["security", "safety"]);
  assert.deepEqual(toggledQuality([], "blau"), []);
});

test("the text of a scenario or a driver", () => {
  assert.equal(qualitiesText({ qualities: ["reliability", "security"] }), "Zuverlässigkeit, Informationssicherheit");
  assert.equal(qualitiesText({}), "");
  assert.equal(qualitiesText(null), "");
});

test("the suggestion body carries the title, the kind and the filled parts only", () => {
  const values = { title: "Ausfall", kind: "failure", stimulus: "Region fällt aus", response: "  ", driver: "d1" };
  assert.deepEqual(suggestionBody(values, ["source", "stimulus", "response"]), { title: "Ausfall", kind: "failure", stimulus: "Region fällt aus" });
});
