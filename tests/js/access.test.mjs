// Users, own and shared projects and their roles in the UI (spec: docs/architecture/specs/personal-workspaces.md).
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  abilities, addedText, offerDemo, canEdit, canOwn, editLock, EMPTY_PROJECTS_TEXT, personLabel, projectGroups, READ_ONLY_HINT, roleLabel, SHARE_ROLES, shareRows, sharedLabel,
} from "../../arcviz/static/access.js";

test("roles decide what may be edited and owned", () => {
  assert.equal(canEdit("viewer"), false);
  assert.equal(canEdit("editor"), true);
  assert.equal(canEdit("owner"), true);
  assert.equal(canEdit(null), true); // unknown: the server decides
  assert.equal(canOwn("editor"), false);
  assert.equal(canOwn("owner"), true);
  assert.deepEqual(abilities("viewer"), { edit: false, own: false });
  assert.equal(roleLabel("viewer"), "Betrachten");
  assert.equal(roleLabel("editor"), "Bearbeiten");
  assert.equal(roleLabel("owner"), "Besitzen");
  assert.ok(READ_ONLY_HINT.startsWith("Nur Lesezugriff"));
});

test("the navigator lists own projects, then the shared ones with their owner", () => {
  const projects = [
    { id: "b", title: "Beta", shared: true, owner: { id: "bob", name: "Bob" }, diagram_count: 1 },
    { id: "a", title: "Alpha", shared: false, owner: { id: "me", name: "Me" }, diagram_count: 2 },
    { id: "c", title: "Gamma", shared: true, owner: { id: "eve", name: "Eve" }, diagram_count: 0 },
  ];
  const groups = projectGroups(projects);
  assert.deepEqual(groups.map((g) => [g.label, g.projects.map((p) => p.id)]), [
    ["Meine Projekte", ["a"]],
    ["Mit mir geteilt", ["b", "c"]],
  ]);
  assert.deepEqual(projectGroups([projects[1]]).map((g) => g.label), ["Meine Projekte"]); // empty groups are left out
  assert.deepEqual(projectGroups([]), []);
  assert.equal(sharedLabel(projects[0], "Beta (1 Diagramm)"), "Beta (1 Diagramm) · Bob");
  assert.equal(sharedLabel(projects[1], "Alpha (2 Diagramme)"), "Alpha (2 Diagramme)");
  assert.ok(EMPTY_PROJECTS_TEXT.includes("noch keine Projekte"));
});

test("only the admin of a fresh installation gets the demo, once", () => {
  const fresh = [{ id: "home_0123", shared: false, diagram_count: 0 }];
  assert.equal(offerDemo(fresh, { admin: true }, false), true);
  assert.equal(offerDemo(fresh, { admin: true }, true), false); // offered before in this browser
  assert.equal(offerDemo(fresh, { admin: false }, false), false); // other users start with an empty default project
  assert.equal(offerDemo([{ ...fresh[0], diagram_count: 1 }], { admin: true }, false), false);
  assert.equal(offerDemo([...fresh, { id: "shop", shared: false, diagram_count: 0 }], { admin: true }, false), false);
});

test("only owners change or remove shares, and never grant owner", () => {
  assert.deepEqual(SHARE_ROLES, ["viewer", "editor"]);
  const access = {
    project: "shop", role: "owner", owner: { id: "ada", name: "Ada", email: "ada@example.org" },
    shares: [
      { id: "bob", name: "Bob", email: null, role: "editor" },
      { id: "eve", name: "Eve", email: "eve@example.org", role: "viewer" },
    ],
  };
  const rows = shareRows(access);
  assert.deepEqual(rows.map((r) => [r.id, r.role, r.editable, r.roles]), [
    ["ada", "owner", false, ["owner"]],
    ["bob", "editor", true, ["viewer", "editor"]],
    ["eve", "viewer", true, ["viewer", "editor"]],
  ]);
  assert.equal(rows[0].note, "Besitzer des Projekts");
  assert.equal(rows[1].label, "Bob");
  assert.equal(rows[2].label, "Eve <eve@example.org>");
  const asRecipient = shareRows({ ...access, role: "viewer", shares: [{ id: "bob", name: "Bob", role: "viewer" }] });
  assert.ok(asRecipient.every((r) => !r.editable));
  assert.equal(personLabel({ name: "x@y.z", email: "x@y.z" }), "x@y.z");
});

test("read-only mode gives back exactly the controls it disabled", () => {
  // an enabled control: disabled and marked when the role becomes viewer, enabled again for an editor
  let mic = editLock(true, false, false);
  assert.deepEqual(mic, { disabled: true, locked: true });
  assert.deepEqual(editLock(true, mic.disabled, mic.locked), { disabled: true, locked: true }); // stays locked
  assert.deepEqual(editLock(false, mic.disabled, mic.locked), { disabled: false, locked: false });
  // a control its feature disabled (no speech recognition): read-only mode never enables it
  const unsupported = editLock(true, true, false);
  assert.deepEqual(unsupported, { disabled: true, locked: false });
  assert.deepEqual(editLock(false, unsupported.disabled, unsupported.locked), { disabled: true, locked: false });
  assert.deepEqual(editLock(false, false, false), { disabled: false, locked: false }); // editors: untouched
});

test("sharing names exactly who it was shared with", () => {
  assert.equal(addedText({ user: "bob@example.org", name: "Bob" }, "bob@example.org", "viewer"), "Freigegeben für Bob (bob@example.org): Betrachten.");
  assert.equal(addedText(null, "bob", "editor"), "Freigegeben für bob: Bearbeiten.");
});

test("share suggestions: label and when to ask", async () => {
  const { suggestionLabel, wantsSuggestions } = await import("../../arcviz/static/access.js");
  assert.equal(suggestionLabel({ id: "u1", name: "Ada Lovelace", email: "ada@example.org" }), "Ada Lovelace · ada@example.org");
  assert.equal(suggestionLabel({ id: "u1", name: "Ada", email: null }), "Ada");
  assert.equal(wantsSuggestions("a", []), false);
  assert.equal(wantsSuggestions(" ad ", []), true);
  assert.equal(wantsSuggestions("u1", [{ id: "u1" }]), false); // just chosen
});
