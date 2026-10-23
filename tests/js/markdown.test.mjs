// Unit tests for the Markdown subset of assistant replies (spec: docs/architecture/specs/chat-markdown.md).
// Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseBlocks, parseInline, plainText, safeHref } from "../../arcviz/static/markdown.js";

const text = (t) => ({ type: "text", text: t });

test("plain text is one paragraph, a single line break stays a break", () => {
  assert.deepEqual(parseBlocks("Hallo\nWelt"), [{ type: "paragraph", children: [text("Hallo"), { type: "br" }, text("Welt")] }]);
  assert.deepEqual(parseBlocks("a\n\nb").map((b) => b.type), ["paragraph", "paragraph"]);
  assert.deepEqual(parseBlocks(""), []);
});

test("headings, rules and quotes", () => {
  const [h, r, q] = parseBlocks("## Ergebnis ##\n---\n> zitiert\n> weiter");
  assert.deepEqual(h, { type: "heading", level: 2, children: [text("Ergebnis")] });
  assert.equal(r.type, "rule");
  assert.equal(q.type, "quote");
  assert.deepEqual(q.children[0].children, [text("zitiert"), { type: "br" }, text("weiter")]);
});

test("inline strong, emphasis, strikethrough and code", () => {
  assert.deepEqual(parseInline("**fett** und *kursiv*, ~~weg~~ `a*b*c`"), [
    { type: "strong", children: [text("fett")] }, text(" und "), { type: "em", children: [text("kursiv")] }, text(", "),
    { type: "del", children: [text("weg")] }, text(" "), { type: "code", text: "a*b*c" },
  ]);
  assert.deepEqual(parseInline("__fett__ _kursiv_"), [{ type: "strong", children: [text("fett")] }, text(" "), { type: "em", children: [text("kursiv")] }]);
});

test("identifiers keep their underscores and stars stay literal when unmatched", () => {
  assert.deepEqual(parseInline("snake_case_name und prod_db"), [text("snake_case_name und prod_db")]);
  assert.deepEqual(parseInline("2 * 3 = 6"), [text("2 * 3 = 6")]);
  assert.deepEqual(parseInline("**noch offen"), [text("**noch offen")]); // a streaming reply: not closed yet
  assert.deepEqual(parseInline("\\*kein\\*"), [text("*kein*")]);
});

test("links: only http(s) and mailto, bare URLs too", () => {
  assert.deepEqual(parseInline("[Doku](https://example.org/a)"), [{ type: "link", href: "https://example.org/a", children: [text("Doku")] }]);
  assert.deepEqual(parseInline("[x](javascript:alert(1))"), [text("x")]);
  assert.deepEqual(parseInline("siehe https://example.org/x."), [text("siehe "), { type: "link", href: "https://example.org/x", children: [text("https://example.org/x")] }, text(".")]);
  assert.equal(safeHref("JavaScript:alert(1)"), null);
  assert.equal(safeHref("data:text/html,x"), null);
  assert.equal(safeHref("mailto:a@b.de"), "mailto:a@b.de");
});

test("markup stays text: nothing is HTML", () => {
  assert.deepEqual(parseBlocks("<b>fett</b> <img src=x onerror=alert(1)>"), [{ type: "paragraph", children: [text("<b>fett</b> <img src=x onerror=alert(1)>")] }]);
});

test("bullet and numbered lists, nested by indentation", () => {
  const [list] = parseBlocks("- eins\n- zwei\n  - zwei.a\n  - zwei.b\n- drei");
  assert.equal(list.type, "list");
  assert.equal(list.ordered, false);
  assert.equal(list.items.length, 3);
  assert.deepEqual(list.items[0], [{ type: "paragraph", children: [text("eins")] }]);
  const nested = list.items[1][1];
  assert.equal(nested.type, "list");
  assert.equal(nested.items.length, 2);
  const [num] = parseBlocks("3. drei\n4. vier");
  assert.equal(num.ordered, true);
  assert.equal(num.start, 3);
  assert.equal(num.items.length, 2);
});

test("a blank line between items keeps one list; a paragraph after it ends the list", () => {
  const blocks = parseBlocks("1. a\n\n2. b\n\nDanach.");
  assert.deepEqual(blocks.map((b) => b.type), ["list", "paragraph"]);
  assert.equal(blocks[0].items.length, 2);
});

test("a list directly after a paragraph line starts a list", () => {
  assert.deepEqual(parseBlocks("Geändert:\n- a\n- b").map((b) => b.type), ["paragraph", "list"]);
});

test("fenced code keeps its text, also while the fence is still open", () => {
  assert.deepEqual(parseBlocks("```json\n{\"a\": \"**b**\"}\n```"), [{ type: "code", lang: "json", text: "{\"a\": \"**b**\"}" }]);
  assert.deepEqual(parseBlocks("```\nnoch"), [{ type: "code", lang: "", text: "noch" }]);
});

test("tables with alignment; short rows are padded", () => {
  const [t] = parseBlocks("| Treiber | Prio |\n|:---|---:|\n| Last | **hoch** |\n| Kosten |");
  assert.equal(t.type, "table");
  assert.deepEqual(t.align, ["left", "right"]);
  assert.deepEqual(t.header, [[text("Treiber")], [text("Prio")]]);
  assert.deepEqual(t.rows[0][1], [{ type: "strong", children: [text("hoch")] }]);
  assert.deepEqual(t.rows[1][1], []);
});

test("plain text for speech output drops the marks and code blocks", () => {
  assert.equal(plainText("## Ergebnis\n- **Last**: hoch\n- `api` neu\n\n```\ncode\n```\nFertig."), "Ergebnis\nLast: hoch\napi neu\nFertig.");
});
