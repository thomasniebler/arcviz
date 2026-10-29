import test from "node:test";
import assert from "node:assert/strict";
import { fitHeight, snippet } from "../../arcviz/static/readability.js";

test("a snippet is one line and shortened at a word", () => {
  assert.equal(snippet("kurz"), "kurz");
  assert.equal(snippet(""), "");
  assert.equal(snippet(null), "");
  assert.equal(snippet("Zeile eins\n\n  Zeile   zwei"), "Zeile eins Zeile zwei");
  const long = "Wir nehmen zwei Regionen, damit der Ausfall einer Region nicht den Betrieb stoppt und weitere Worte folgen";
  const cut = snippet(long, 50);
  assert.ok(cut.endsWith("…") && cut.length <= 51, cut);
  assert.equal(cut, "Wir nehmen zwei Regionen, damit der Ausfall einer…");
});

test("a snippet drops Markdown markers and cuts a long word hard", () => {
  assert.equal(snippet("## Titel *fett*"), "Titel fett");
  assert.equal(snippet("x".repeat(30), 10), "xxxxxxxxxx…");
});

test("a text area grows to its content between a minimum and a cap", () => {
  assert.deepEqual(fitHeight(40, 80, 400), { height: 80, overflow: false });
  assert.deepEqual(fitHeight(200, 80, 400), { height: 200, overflow: false });
  assert.deepEqual(fitHeight(900, 80, 400), { height: 400, overflow: true });
  assert.deepEqual(fitHeight(undefined, 80, 400), { height: 80, overflow: false });
});
