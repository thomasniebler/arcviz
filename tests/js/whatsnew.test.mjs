import { test } from "node:test";
import assert from "node:assert/strict";
import { bannerTitle, bannerVisible, dismissKey, highlights } from "../../arcviz/static/whatsnew.js";

const entry = { version: "0.5.0", items: [{ emoji: "🚀", text: "Neu" }, { text: "Ohne Emoji" }, { emoji: "x", text: "  " }] };

test("highlights keeps usable items and defaults the emoji", () => {
  assert.deepEqual(highlights({ whats_new: entry }), { version: "0.5.0", items: [{ emoji: "🚀", text: "Neu" }, { emoji: "•", text: "Ohne Emoji" }] });
});

test("highlights is null for anything unusable", () => {
  for (const config of [null, {}, { whats_new: null }, { whats_new: { version: "1", items: [] } }, { whats_new: { items: [{ text: "x" }] } }, { whats_new: { version: "1", items: [{ text: " " }] } }])
    assert.equal(highlights(config), null);
});

test("the banner shows until this release's dismissal is remembered", () => {
  assert.equal(bannerVisible(entry, null), true);
  assert.equal(bannerVisible(entry, "1"), false);
  assert.equal(bannerVisible(null, null), false);
  assert.notEqual(dismissKey(entry), dismissKey({ version: "0.5.1" }));
});

test("the title names the highlighted release and the running one when they differ", () => {
  assert.equal(bannerTitle(entry, "0.5.0"), "Neu in Version 0.5.0");
  assert.equal(bannerTitle(entry, undefined), "Neu in Version 0.5.0");
  assert.equal(bannerTitle(entry, "0.5.2"), "Neu in Version 0.5.0 (läuft: 0.5.2)");
});
