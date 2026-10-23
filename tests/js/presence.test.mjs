// Presence helpers (spec: docs/architecture/specs/collaboration.md, #171).
import { test } from "node:test";
import assert from "node:assert/strict";
import { PRESENCE_COLORS, chatNotice, colorOf, initials, selectionMarks, viewersToShow } from "../../arcviz/static/presence.js";

const snapshot = {
  viewers: [
    { session: "s1", user: "alice", name: "Alice Adams", diagram: "d", selection: "api" },
    { session: "s2", user: "bob", name: "Bob", diagram: "d", selection: null },
    { session: "s3", user: "bob", name: "Bob", diagram: "d", selection: "db" },
    { session: "s4", user: "me", name: "Me", diagram: "d", selection: "api" },
  ],
  project_viewers: [
    { session: "s1", user: "alice", name: "Alice Adams", diagram: "d" },
    { session: "s5", user: "carol", name: "Carol", diagram: "other" },
  ],
  chatting: [{ user: "bob", name: "Bob", diagram: "d", project: "p" }],
};

test("initials and colours", () => {
  assert.equal(initials("Alice Adams"), "AA");
  assert.equal(initials("bob"), "BO");
  assert.equal(initials("jean-luc.picard"), "JP");
  assert.equal(initials(""), "?");
  assert.equal(colorOf("alice"), colorOf("alice"));
  assert.ok(PRESENCE_COLORS.includes(colorOf("x")));
});

test("chips: one per other user, chatting marked; the project's when asked or nobody is on the diagram", () => {
  assert.deepEqual(viewersToShow(snapshot, "me").map((v) => [v.user, v.initials, v.chatting]), [["alice", "AA", false], ["bob", "BO", true]]);
  assert.deepEqual(viewersToShow(snapshot, "me", { project: true }).map((v) => v.user), ["alice", "carol"]);
  assert.deepEqual(viewersToShow({ ...snapshot, viewers: [] }, "me").map((v) => v.user), ["alice", "carol"]);
  assert.deepEqual(viewersToShow(null, "me"), []);
});

test("selection marks of others, one per user and element", () => {
  assert.deepEqual(selectionMarks(snapshot, "me").map((m) => [m.user, m.element]), [["alice", "api"], ["bob", "db"]]);
});

test("chat notice names the others in a turn", () => {
  assert.equal(chatNotice(snapshot, "me"), "Bob chattet gerade mit dem Assistenten.");
  assert.equal(chatNotice(snapshot, "bob"), "");
  const two = { chatting: [{ user: "a", name: "A" }, { user: "b", name: "B" }] };
  assert.equal(chatNotice(two, "me"), "A und B chatten gerade mit dem Assistenten.");
});
