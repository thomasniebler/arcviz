// Unit tests for the pure helpers of the project chat (spec: docs/architecture/specs/project-chat.md).
// Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { changedDiagramItems, changedDiagramsLabel, describeProjectCall, diagramSwitch, scopeLabel } from "../../arcviz/static/projectchat.js";

test("describeProjectCall gives the project tools German descriptions and leaves other tools alone", () => {
  assert.equal(describeProjectCall("list_diagrams", {}), "Diagramme des Projekts auflisten");
  assert.equal(describeProjectCall("read_diagram", { diagram: "demo" }), "Diagramm „demo“ lesen");
  assert.equal(describeProjectCall("open_diagram", { diagram: "demo" }), "Diagramm „demo“ öffnen");
  assert.equal(describeProjectCall("create_diagram", { title: "Checkout", type: "sequence" }), "Neues Sequenzdiagramm „Checkout“ anlegen");
  assert.equal(describeProjectCall("create_diagram", { title: "Netz", type: "deployment", section: "backend" }), "Neues Verteilungsdiagramm „Netz“ anlegen (Abschnitt backend)");
  assert.equal(describeProjectCall("add_node", { name: "db" }), null);
});

test("changedDiagramsLabel names every changed or created diagram with its version", () => {
  assert.equal(changedDiagramsLabel([]), "");
  assert.equal(changedDiagramsLabel(undefined), "");
  assert.equal(
    changedDiagramsLabel([
      { id: "checkout", title: "Checkout", created: true, version: 2 },
      { id: "demo", title: "Deployment", created: false, version: 7 },
      { id: "order", title: "Bestellung", created: false, version: null },
    ]),
    "Geändert: Checkout (neu, v2) · Deployment (v7) · Bestellung",
  );
  assert.equal(changedDiagramsLabel([{ id: "x", title: "X", created: true, version: null }]), "Geändert: X (neu)");
});

test("diagramSwitch tells when an event belongs to another diagram than the shown one", () => {
  assert.equal(diagramSwitch({ diagram_id: "order" }, "demo"), "order");
  assert.equal(diagramSwitch({ diagram_id: "demo" }, "demo"), null);
  assert.equal(diagramSwitch({}, "demo"), null);
  assert.equal(diagramSwitch({ diagram_id: null }, "demo"), null);
  assert.equal(diagramSwitch({ diagram_id: "demo" }, null), "demo");
});

test("scopeLabel names the project", () => {
  assert.equal(scopeLabel({ title: "Shop" }), "Projekt-Chat · Shop");
  assert.equal(scopeLabel(null), "Projekt-Chat");
});

test("changedDiagramItems keeps the ids for the buttons below the reply", () => {
  assert.deepEqual(changedDiagramItems([{ id: "c", title: "Checkout", created: true, version: 1 }]), [{ id: "c", label: "Checkout (neu, v1)" }]);
  assert.deepEqual(changedDiagramItems(null), []);
});
