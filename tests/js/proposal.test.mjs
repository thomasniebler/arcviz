// Unit tests for the proposal diff formatter. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { describeDiff, describeItem, isProjectProposal, itemOpLabel, itemStateLabel, pendingItems, referencedKeys, selectionClosure, storedHeading } from "../../arcviz/static/proposal.js";

const empty = { title: null, nodes: { added: [], removed: [], changed: [] }, edges: { added: [], removed: [], changed: [] } };

test("no diff, no lines", () => {
  assert.deepEqual(describeDiff(empty), []);
  assert.deepEqual(describeDiff(null), []);
});

test("nodes, edges and title in a stable order", () => {
  const diff = {
    title: ["Alt", "Neu"],
    nodes: {
      added: [{ id: "prod-vnet", name: "prod-vnet", kind: "vnet", parent: "prod-deployment" }, { id: "x", name: "x", kind: "service", parent: null }],
      removed: [{ id: "old-db", name: "old-db", kind: "database", parent: null }],
      changed: [{ id: "service-a", name: "service-a", changes: { parent: ["prod-deployment", "prod-vnet"], "properties.cidr": [null, "10.0.0.0/16"] } }],
    },
    edges: {
      added: [{ id: "e7", source: "service-c", target: "orders-db", label: "sql", kind: "connection" }],
      removed: [{ id: "e2", source: "a", target: "b", label: null, kind: "connection" }],
      changed: [{ id: "e1", source: "a", target: "c", changes: { label: ["http", "https"] } }],
    },
  };
  assert.deepEqual(describeDiff(diff), [
    { sign: "~", text: "Titel: Alt → Neu" },
    { sign: "+", text: "prod-vnet (vnet) in prod-deployment" },
    { sign: "+", text: "x (service)" },
    { sign: "−", text: "old-db (database)" },
    { sign: "~", text: "service-a: Container prod-deployment → prod-vnet, cidr – → 10.0.0.0/16" },
    { sign: "+", text: "Kante service-c → orders-db (sql)" },
    { sign: "−", text: "Kante a → b" },
    { sign: "~", text: "Kante a → c: Label http → https" },
  ]);
});

test("layout direction change", () => {
  assert.deepEqual(describeDiff({ ...empty, direction: ["TB", "LR"] }), [{ sign: "~", text: "Richtung: TB → LR" }]);
});

test("interaction elements of sequence diagrams are listed", async () => {
  const { describeDiff } = await import("../../arcviz/static/proposal.js");
  const lines = describeDiff({ nodes: {}, edges: {}, elements: { added: [{ id: "m2", description: "reply b → a: ok" }], removed: [{ id: "m1", description: "synchCall a → b: go" }], changed: [] } });
  assert.deepEqual(lines, [{ sign: "+", text: "reply b → a: ok" }, { sign: "−", text: "synchCall a → b: go" }]);
});

test("stored proposals: status labels, pending count and heading", async () => {
  const { statusLabel, pendingCount, storedHeading, canApply, canDecide } = await import("../../arcviz/static/proposal.js");
  assert.equal(statusLabel("pending"), "offen");
  assert.equal(statusLabel("applying"), "wird übernommen");
  assert.equal(statusLabel("applied"), "übernommen");
  assert.equal(statusLabel("discarded"), "verworfen");
  assert.equal(statusLabel("stale"), "veraltet");
  assert.equal(statusLabel("x"), "x");
  assert.equal(pendingCount([{ status: "pending" }, { status: "stale" }, { status: "pending" }, { status: "applied" }]), 2);
  assert.equal(pendingCount(undefined), 0);
  assert.equal(
    storedHeading({ id: "p3", author_name: "Ada", author: "ada", diagram: "orders", client: "mcp" }, { orders: "Bestellungen" }),
    "p3 · Bestellungen · von Ada (mcp)",
  );
  assert.equal(storedHeading({ id: "p4", author: "ed", author_name: "", diagram: "x", client: "mcp" }, {}), "p4 · x · von ed (mcp)");
  assert.equal(canDecide({ status: "pending" }), true);
  assert.equal(canDecide({ status: "stale" }), true);
  assert.equal(canDecide({ status: "applied" }), false);
  assert.equal(canDecide({ status: "applying" }), false);
  assert.equal(canApply({ status: "pending" }), true);
  assert.equal(canApply({ status: "stale" }), false);
});

// ---- project proposals ----

const items = [
  { key: "d", op: "add_driver", arguments: { title: "Antwortzeit" }, state: "pending" },
  { key: "q", op: "add_scenario", arguments: { title: "Spitze", driver: "@d" }, state: "pending" },
  { key: "r", op: "add_scenario", arguments: { title: "Ausfall", driver: "@d", stakeholders: ["s1"] }, state: "pending" },
  { key: "x", op: "add_driver", arguments: { title: "Kosten" }, state: "pending" },
];

test("referencedKeys finds @keys anywhere", () => {
  assert.deepEqual(referencedKeys({ driver: "@d", list: ["@a", "b", { deep: "@c" }], n: 3, at: "@" }).sort(), ["a", "c", "d"]);
  assert.deepEqual(referencedKeys("plain"), []);
  assert.deepEqual(referencedKeys(null), []);
});

test("ticking a scenario ticks the driver it needs", () => {
  assert.deepEqual(selectionClosure(items, ["q"]).keys, ["d", "q"]);
  assert.deepEqual(selectionClosure(items, ["x"]).keys, ["x"]);
  assert.deepEqual(selectionClosure(items, ["q", "r"]).keys, ["d", "q", "r"]);
  assert.deepEqual(selectionClosure(items, []).keys, []);
});

test("an applied dependency is not ticked again, a lost one blocks", () => {
  const applied = items.map((i) => (i.key === "d" ? { ...i, state: "applied" } : i));
  assert.deepEqual(selectionClosure(applied, ["q"]), { keys: ["q"], blocked: [] });
  const discarded = items.map((i) => (i.key === "d" ? { ...i, state: "discarded" } : i));
  assert.deepEqual(selectionClosure(discarded, ["q", "x"]), { keys: ["x"], blocked: [{ key: "q", needs: "d" }] });
});

test("decided items are never ticked", () => {
  const done = items.map((i) => ({ ...i, state: "applied" }));
  assert.deepEqual(selectionClosure(done, ["q"]).keys, []);
  assert.deepEqual(pendingItems({ items }).map((i) => i.key), ["d", "q", "r", "x"]);
  assert.deepEqual(pendingItems({}), []);
});

test("labels and the heading of a project proposal", () => {
  assert.equal(itemOpLabel("add_decision"), "Entscheidung anlegen (Entwurf)");
  assert.equal(itemOpLabel("odd"), "odd");
  assert.equal(itemStateLabel("stale"), "veraltet");
  assert.equal(storedHeading({ id: "p4", kind: "project", author_name: "Ada", client: "mcp", diagram: "" }, {}), "p4 · Architektur · von Ada (mcp)");
  assert.equal(storedHeading({ id: "p1", author: "ed", client: "mcp", diagram: "orders" }, { orders: "Bestellungen" }), "p1 · Bestellungen · von ed (mcp)");
  assert.equal(isProjectProposal({ kind: "project" }), true);
  assert.equal(isProjectProposal({ diagram: "x" }), false);
});

test("describeItem shows the notable arguments and the requirements it came from", () => {
  const lines = describeItem({ op: "add_scenario", arguments: { title: "Spitze", driver: "@d", core: true, response_measure: "< 200 ms" }, derived_from: ["req3"] });
  assert.deepEqual(lines.map((l) => l.label), ["Titel", "Ziel", "Treiber", "Reaktionsmaß", "Anforderungen"]);
  assert.equal(lines.at(-1).text, "req3");
  assert.deepEqual(describeItem({ op: "add_driver", arguments: {}, derived_from: [] }), []);
});
