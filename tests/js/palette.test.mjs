// Unit tests for the command palette registry and ranking (spec: docs/architecture/specs/command-palette.md).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { COMMANDS, GROUPS, fold, matchRanges, rankCommands, rememberCommand, scoreMatch } from "../../arcviz/static/palette.js";

const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "arcviz", "static", "index.html"), "utf8");
const labels = (rows) => rows.map((r) => r.command.label);

test("fold lower-cases and strips diacritics without changing the length", () => {
  assert.equal(fold("Größe Äpfel Élan"), "grose apfel elan");
  for (const text of ["Verfügbarkeit 99,9 %", "Ärger über Öl", "straße"]) assert.equal(fold(text).length, text.length);
});

test("scoreMatch needs every word, in any order", () => {
  assert.equal(scoreMatch("neu diagramm", "Neues Diagramm anlegen") > 0, true);
  assert.equal(scoreMatch("diagramm neu", "Neues Diagramm anlegen") > 0, true);
  assert.equal(scoreMatch("neu export", "Neues Diagramm anlegen"), null);
  assert.equal(scoreMatch("", "irgendwas"), 0);
});

test("scoreMatch ignores case and diacritics", () => {
  assert.notEqual(scoreMatch("VERFUGBARKEIT", "Verfügbarkeit"), null);
  assert.notEqual(scoreMatch("andern", "Ändern"), null);
});

test("a match at the start or at a word start beats one inside a word", () => {
  const start = scoreMatch("ver", "Version sichern");
  const wordStart = scoreMatch("ver", "Neue Version");
  const inside = scoreMatch("ver", "Zuverlässigkeit");
  assert.ok(start > wordStart && wordStart > inside, `${start} ${wordStart} ${inside}`);
});

test("matchRanges gives merged ranges in the original text", () => {
  assert.deepEqual(matchRanges("ver", "Version sichern"), [[0, 3]]);
  assert.deepEqual(matchRanges("sich ver", "Version sichern"), [[0, 3], [8, 12]]);
  assert.deepEqual(matchRanges("ver ers", "Version"), [[0, 4]]);
  assert.deepEqual(matchRanges("nichts", "Version"), []);
  assert.deepEqual(matchRanges("", "Version"), []);
  assert.deepEqual(matchRanges("verf", "Verfügbarkeit"), [[0, 4]]);
});

const sample = [
  { id: "a", label: "Neues Diagramm", group: "Diagramm", keywords: "anlegen erstellen" },
  { id: "b", label: "Diagramm importieren", group: "Diagramm", keywords: "datei json" },
  { id: "c", label: "Version sichern", group: "Version", keywords: "snapshot speichern" },
  { id: "d", label: "Treiber", group: "Gehe zu", keywords: "architektur" },
];

test("a blank query lists the recent commands first, then all in registry order", () => {
  const rows = rankCommands(sample, "  ", ["c", "zzz", "a"]);
  assert.deepEqual(rows.map((r) => r.command.id), ["c", "a", "b", "d"]);
  assert.deepEqual(rows.map((r) => r.recent === true), [true, true, false, false]);
});

test("a query filters, orders by match quality and keeps registry order for equal scores", () => {
  assert.deepEqual(labels(rankCommands(sample, "diagramm")), ["Diagramm importieren", "Neues Diagramm"]); // a start beats a later word
  assert.deepEqual(labels(rankCommands([{ id: "x", label: "Alpha", group: "G" }, { id: "y", label: "Alpha", group: "G" }], "alpha")), ["Alpha", "Alpha"]);
  assert.deepEqual(rankCommands([{ id: "x", label: "Alpha", group: "G" }, { id: "y", label: "Alpha", group: "G" }], "alpha").map((r) => r.command.id), ["x", "y"]);
  assert.deepEqual(rankCommands(sample, "snapshot").map((r) => r.command.id), ["c"]);
  assert.deepEqual(rankCommands(sample, "gibtesnicht"), []);
});

test("keywords are searched but only the label is highlighted", () => {
  const [row] = rankCommands(sample, "speichern");
  assert.equal(row.command.id, "c");
  assert.deepEqual(row.ranges, []);
  assert.deepEqual(rankCommands(sample, "treiber")[0].ranges, [[0, 7]]);
});

test("rememberCommand puts the id first without duplicates and keeps at most the limit", () => {
  assert.deepEqual(rememberCommand(["a", "b"], "b"), ["b", "a"]);
  assert.deepEqual(rememberCommand(["a", "b", "c"], "d", 3), ["d", "a", "b"]);
  assert.deepEqual(rememberCommand([], "x"), ["x"]);
  assert.deepEqual(rememberCommand(null, "x"), ["x"]);
});

test("the registry is well formed: unique ids, known groups, one action each", () => {
  const ids = COMMANDS.map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const c of COMMANDS) {
    assert.ok(GROUPS.includes(c.group), `${c.id}: unknown group ${c.group}`);
    assert.ok(c.label.length > 0, `${c.id}: no label`);
    const actions = ["click", "route", "href"].filter((k) => k in c);
    assert.equal(actions.length, 1, `${c.id}: needs exactly one of click/route/href`);
  }
});

test("every control the registry clicks exists in index.html", () => {
  for (const c of COMMANDS.filter((x) => "click" in x)) {
    assert.ok(html.includes(`id="${c.click}"`), `${c.id}: no element with id ${c.click}`);
  }
});

test("routes are location hashes of the shell", () => {
  for (const c of COMMANDS.filter((x) => "route" in x)) assert.match(c.route, /^(#\/(requirements|architecture\/(drivers|scenarios|decisions|stakeholders|vision|documentation|gaps)))?$/, c.id);
});

test("the palette finds the areas and the common actions by their obvious words", () => {
  const top = (q) => rankCommands(COMMANDS, q)[0]?.command.id;
  assert.equal(COMMANDS.find((c) => c.id === top("treiber")).route, "#/architecture/drivers");
  assert.equal(COMMANDS.find((c) => c.id === top("version sichern")).click, "btn-snapshot");
  assert.equal(COMMANDS.find((c) => c.id === top("rückgängig")).click, "btn-undo");
  assert.equal(COMMANDS.find((c) => c.id === top("anforderungen")).route, "#/requirements");
});
