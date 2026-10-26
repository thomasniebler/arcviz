import assert from "node:assert/strict";
import test from "node:test";
import { itemSections } from "../../arcviz/static/proposal.js";
import { outcomeText, statusLine, stewardRights } from "../../arcviz/static/steward.js";

test("the status line says nothing while the installation has no steward", () => {
  assert.equal(statusLine({ available: false }), "");
  assert.equal(statusLine(null), "");
});

test("the status line tells off, never checked and the last run", () => {
  assert.match(statusLine({ available: true, enabled: false }), /aus/);
  const never = statusLine({ available: true, enabled: true, verifier: "configured", last_run: null, runs_today: 0, max_runs_per_day: 4 });
  assert.match(never, /Noch nicht geprüft/);
  const ran = statusLine({ available: true, enabled: true, verifier: "missing", last_run: { outcome: "proposed" }, runs_today: 2, max_runs_per_day: 4 });
  assert.match(ran, /Vorschlag erstellt/);
  assert.match(ran, /2 von 4/);
  assert.match(ran, /nur Prüfregeln/);
  assert.equal(outcomeText("odd"), "odd");
});

test("only an owner switches the steward and an editor may run it", () => {
  assert.deepEqual(stewardRights("owner"), { switch: true, run: true });
  assert.deepEqual(stewardRights("editor"), { switch: false, run: true });
  assert.deepEqual(stewardRights("viewer"), { switch: false, run: false });
});

test("items are grouped: ungrouped first, fixes, then next steps", () => {
  const items = [{ key: "a", group: "next_step" }, { key: "b" }, { key: "c", group: "fix" }, { key: "d", group: "fix" }];
  const sections = itemSections(items);
  assert.deepEqual(sections.map((s) => s.group), ["", "fix", "next_step"]);
  assert.deepEqual(sections[1].items.map((i) => i.key), ["c", "d"]);
  assert.equal(sections[0].label, "");
  assert.deepEqual(itemSections([]), []);
});
