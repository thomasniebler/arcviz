// Concurrent edits in the inspector (spec: docs/architecture/specs/collaboration.md, ADR-0099).
import { test } from "node:test";
import assert from "node:assert/strict";
import { canonicalProperties, changedBy, confirmedDelete, displayValue, fieldConflicts, liveBackoff, nodeValues, parseEtag, remoteChange, same } from "../../arcviz/static/conflicts.js";

const base = { name: "api", kind: "service", parent: null, description: "", properties: canonicalProperties({ b: "2", a: "1" }) };

test("node values are comparable: properties sorted, parent and note normalised", () => {
  assert.deepEqual(nodeValues({ name: "api", kind: "service", properties: { a: "1", b: "2" } }), base);
  assert.ok(same(canonicalProperties({ a: "1", b: "2" }), canonicalProperties({ b: "2", a: "1" })));
  assert.ok(same(null, undefined) && !same("", null));
});

test("a remote change of an untouched field updates it and rebases", () => {
  const theirs = { ...base, description: "von Bob" };
  const result = remoteChange(base, theirs, { ...base, name: "api-2" });
  assert.deepEqual(result, { changed: ["description"], updates: { description: "von Bob" }, conflicts: [], rebase: true });
});

test("a remote change of a touched field is a conflict and keeps the typed value", () => {
  const theirs = { ...base, name: "gateway" };
  const result = remoteChange(base, theirs, { ...base, name: "api-2" });
  assert.deepEqual(result, { changed: ["name"], updates: {}, conflicts: ["name"], rebase: false });
});

test("the same change on both sides is no conflict; an unrelated remote change neither", () => {
  assert.equal(remoteChange(base, { ...base, name: "x" }, { ...base, name: "x" }).rebase, true);
  assert.deepEqual(remoteChange(base, base, { ...base, name: "x" }), { changed: [], updates: {}, conflicts: [], rebase: true });
});

test("after a 412 the conflicts are the fields both sides changed to different values", () => {
  const theirs = { ...base, name: "gateway", kind: "gateway" };
  const mine = { ...base, name: "api-2", description: "neu" };
  assert.deepEqual(fieldConflicts(base, theirs, mine), [{ key: "name", mine: "api-2", theirs: "gateway" }]);
  assert.deepEqual(fieldConflicts(base, theirs, { ...base, name: "gateway" }), []);
  assert.deepEqual(fieldConflicts(base, base, mine), []); // a change elsewhere in the diagram: no conflict
});

test("etags, backoff and labels", () => {
  assert.equal(parseEtag('"abc"'), "abc");
  assert.equal(parseEtag('W/"abc"'), "abc");
  assert.equal(parseEtag(null), null);
  assert.equal(parseEtag('""'), null);
  assert.deepEqual([0, 1, 2, 5, 9].map(liveBackoff), [1000, 2000, 4000, 30000, 30000]);
  assert.equal(changedBy("Bob"), "Bob");
  assert.equal(changedBy(null), "jemand anderem");
  assert.equal(displayValue("properties", canonicalProperties({ a: "1" })), "a=1");
  assert.equal(displayValue("properties", canonicalProperties({})), "—");
  assert.equal(displayValue("name", ""), "—");
  assert.equal(displayValue("covered", ["a", "b"]), "a, b");
  assert.equal(displayValue("actor", true), "ja");
});

// ---- conditional deletes from the UI (#186) ---------------------------------------------------------------------

/** A scripted delete: `answers` for the confirmations, `outcomes` for the deletes ("ok", 412, or another status). */
function deleteWorld({ answers, outcomes, reloads = [] }) {
  const log = [];
  const options = {
    current: { revision: "r1", author: null },
    reload: async () => { const next = reloads.shift(); log.push(["reload", next?.revision ?? null]); return next ?? null; },
    ask: async (text) => { log.push(["ask", text]); return answers.shift(); },
    remove: async (revision) => {
      log.push(["delete", revision]);
      const outcome = outcomes.shift();
      if (outcome !== "ok") throw Object.assign(new Error(`status ${outcome}`), { status: outcome });
    },
    question: "Löschen?",
    changed: (who) => `Von ${changedBy(who)} geändert. Trotzdem löschen?`,
  };
  return { options, log };
}

test("a delete is sent with the revision the user was asked about", async () => {
  const { options, log } = deleteWorld({ answers: [true], outcomes: ["ok"] });
  assert.equal(await confirmedDelete(options), true);
  assert.deepEqual(log, [["ask", "Löschen?"], ["delete", "r1"]]);
});

test("declining deletes nothing", async () => {
  const { options, log } = deleteWorld({ answers: [false], outcomes: [] });
  assert.equal(await confirmedDelete(options), false);
  assert.deepEqual(log, [["ask", "Löschen?"]]);
});

test("a 412 reloads, says who changed it and asks again with the new revision", async () => {
  const { options, log } = deleteWorld({ answers: [true, true], outcomes: [412, "ok"], reloads: [{ revision: "r2", author: "Bob" }] });
  assert.equal(await confirmedDelete(options), true);
  assert.deepEqual(log, [["ask", "Löschen?"], ["delete", "r1"], ["reload", "r2"], ["ask", "Von Bob geändert. Trotzdem löschen?"], ["delete", "r2"]]);
});

test("after a 412 the user may decline, and an unknown author is somebody else", async () => {
  const { options, log } = deleteWorld({ answers: [true, false], outcomes: [412], reloads: [{ revision: "r2", author: null }] });
  assert.equal(await confirmedDelete(options), false);
  assert.deepEqual(log.at(-1), ["ask", "Von jemand anderem geändert. Trotzdem löschen?"]);
});

test("a reload that finds nothing deletable any more stops without asking", async () => {
  const { options, log } = deleteWorld({ answers: [true], outcomes: [412], reloads: [] });
  assert.equal(await confirmedDelete(options), false);
  assert.deepEqual(log, [["ask", "Löschen?"], ["delete", "r1"], ["reload", null]]);
});

test("other failures are not retried", async () => {
  const { options } = deleteWorld({ answers: [true], outcomes: [409] });
  await assert.rejects(() => confirmedDelete(options), (err) => err.status === 409);
});

// ---- coordinator review of #199: who a 412 names, and no silent stop -------------------------------------------

test("a 412 names the live stream's author of that revision, else the newest version's author, never the caller", async () => {
  const { changeAuthor } = await import("../../arcviz/static/conflicts.js");
  const me = { id: "ada" };
  const versions = [{ author: "bob", author_name: "Bob" }, { author: "ada", author_name: "Ada" }];
  assert.equal(changeAuthor({ revision: "r2", lastChange: { revision: "r2", author_name: "Carl" }, versions, me }), "Carl");
  assert.equal(changeAuthor({ revision: "r2", lastChange: { revision: "r1", author_name: "Carl" }, versions, me }), null);
  assert.equal(changeAuthor({ revision: "r2", versions: [...versions].reverse(), me }), "Bob");
  assert.equal(changeAuthor({ revision: "r2", versions: [], me }), null);
  assert.equal(changeAuthor({ revision: "r2", versions: [{ author_name: "Ohne Id" }], me }), "Ohne Id");
});

test("a reload that refuses to go on says why", async () => {
  const told = [];
  const { options } = deleteWorld({ answers: [true], outcomes: [412], reloads: [{ refused: "enthält inzwischen Diagramme" }] });
  const deleted = await confirmedDelete({ ...options, tell: async (text) => { told.push(text); } });
  assert.equal(deleted, false);
  assert.deepEqual(told, ["enthält inzwischen Diagramme"]);
});
