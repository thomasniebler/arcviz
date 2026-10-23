// Unit tests for the local voice command matcher. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { matchCommand, normalize, resolveTarget } from "../../arcviz/static/commands.js";

const nodes = {
  "prod-deployment": { id: "prod-deployment", name: "prod-deployment", kind: "deployment" },
  "prod-vnet": { id: "prod-vnet", name: "Prod VNet", kind: "vnet", parent: "prod-deployment" },
  "service-a": { id: "service-a", name: "service-a", kind: "service", parent: "prod-vnet" },
  "orders-db": { id: "orders-db", name: "Orders database", kind: "database", parent: "prod-deployment" },
};
const ctx = { nodes, containerKinds: ["deployment", "vnet", "subnet", "node", "cluster", "group", "region"] };

test("normalize folds case, punctuation and umlauts", () => {
  assert.equal(normalize("  Zoom RÜCKGÄNGIG, bitte!  "), "zoom rueckgaengig bitte");
});

test("simple phrases in German and English", () => {
  assert.equal(matchCommand("Zoom rein", ctx).id, "zoom_in");
  assert.equal(matchCommand("zoom out", ctx).id, "zoom_out");
  assert.equal(matchCommand("Rückgängig.", ctx).id, "undo");
  assert.equal(matchCommand("Wiederholen!", ctx).id, "redo");
  assert.equal(matchCommand("redo", ctx).id, "redo");
  assert.equal(matchCommand("Wiederherstellen", ctx), null); // the restore button, not redo
  assert.equal(matchCommand("Nächstes Diagramm", ctx).id, "next_diagram");
  assert.equal(matchCommand("show source", ctx).id, "toggle_source");
  assert.equal(matchCommand("Hundert Prozent", ctx).id, "zoom_reset");
  assert.equal(matchCommand("Einpassen bitte", ctx).id, "fit");
  assert.equal(matchCommand("Version sichern", ctx).id, "snapshot");
});

test("fillers are ignored, ordinary instructions are not commands", () => {
  assert.equal(matchCommand("bitte zoom raus jetzt", ctx).id, "zoom_out");
  assert.equal(matchCommand("Füge dem Deployment ein VNet hinzu", ctx), null);
  assert.equal(matchCommand("", ctx), null);
  assert.equal(matchCommand("Verbinde service-a mit orders-db", ctx), null);
});

test("focus commands resolve containers by name or id, loosely", () => {
  assert.deepEqual(matchCommand("Fokus auf prod vnet", ctx).args.node.id, "prod-vnet");
  assert.equal(matchCommand("zeig mir das Prod VNet", ctx).args.node.id, "prod-vnet");
  assert.equal(matchCommand("focus on prod deployment", ctx).args.node.id, "prod-deployment");
  assert.equal(matchCommand("öffne vnet", ctx).args.node.id, "prod-vnet");
  assert.equal(matchCommand("zeig ganzes diagramm", ctx).id, "focus_all");
  const miss = matchCommand("Fokus auf orders database", ctx); // a leaf, not a container
  assert.equal(miss.id, "focus");
  assert.match(miss.error, /Kein Container/);
});

test("resolveTarget scores exact over prefix over substring", () => {
  assert.equal(resolveTarget("Orders database", nodes).id, "orders-db");
  assert.equal(resolveTarget("orders", nodes).id, "orders-db");
  assert.equal(resolveTarget("service", nodes).id, "service-a");
  assert.equal(resolveTarget("completely different", nodes), null);
});
