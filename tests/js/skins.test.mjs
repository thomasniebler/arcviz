// Unit tests for the UI skin list (spec: docs/architecture/specs/ui-skins.md). Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_SKIN, SKINS, SKIN_STORAGE_KEY, resolveSkin } from "../../arcviz/static/skins.js";

test("SKINS lists three skins with unique ids, the default first", () => {
  const ids = SKINS.map((s) => s.id);
  assert.deepEqual(ids, ["classic", "mission-control", "synthwave"]);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(ids[0], DEFAULT_SKIN);
  for (const s of SKINS) assert.ok(s.label.length > 0);
});

test("resolveSkin keeps known ids and falls back to the default", () => {
  for (const s of SKINS) assert.equal(resolveSkin(s.id), s.id);
  for (const bad of [null, undefined, "", "unicorn", "CODEX", "napkin", "codex", 42, {}]) assert.equal(resolveSkin(bad), DEFAULT_SKIN);
});

test("the storage key follows the arcviz.* convention", () => {
  assert.equal(SKIN_STORAGE_KEY, "arcviz.skin");
});
