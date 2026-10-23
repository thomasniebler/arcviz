import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MATRIX_LABELS,
  allowedProperties,
  completenessText,
  fits,
  matrixCell,
  matrixRows,
  needsConfirmation,
  relationChanges,
  relationChoices,
  relationKindLabel,
  relationText,
  relationUsageText,
  relationsNotDrawn,
  resolutionRequest,
  shownElements,
  viewConnections,
} from "../../arcviz/static/relations.js";

const config = {
  diagram_types: {
    deployment: {
      relations: { connection: ["communicates", "data_flow"], dependency: ["communicates"], deploys: ["deployed_on"] },
      completeness: ["communicates", "deployed_on", "data_flow"],
    },
    sequence: { relations: { message: ["communicates"] }, completeness: ["communicates"] },
    activity: { relations: {}, completeness: [] },
  },
};
const model = {
  elements: [
    { id: "web", name: "Web" }, { id: "orders", name: "Orders" }, { id: "db", name: "DB" }, { id: "k8s", name: "Cluster" },
  ],
  relations: [
    { id: "r1", kind: "communicates", source: "web", target: "orders" },
    { id: "r2", kind: "data_flow", source: "orders", target: "db" },
    { id: "r3", kind: "deployed_on", source: "orders", target: "k8s" },
  ],
};
const nodes = {
  web: { id: "web", ref: "web" }, orders: { id: "orders", ref: "orders" }, db: { id: "db", ref: "db" }, free: { id: "free" },
};

test("labels and texts", () => {
  assert.equal(relationKindLabel("deployed_on"), "läuft auf");
  assert.equal(relationKindLabel("other"), "other");
  assert.equal(relationText(model.relations[0], model), "Web kommuniziert mit Orders");
  assert.equal(relationText({ kind: "data_flow", source: "x", target: "db" }, model), "x Datenfluss zu DB");
  assert.equal(completenessText([]), "Vollständigkeit: offen");
  assert.equal(completenessText(["communicates", "deployed_on"]), "Vollständig: Kommunikation, Verteilung");
});

test("connections of deployment and sequence diagrams", () => {
  const edges = [
    { id: "e1", source: "web", target: "orders", kind: "connection", ref: "r1" },
    { id: "e2", source: "db", target: "orders", kind: "connection" },
    { id: "e3", source: "web", target: "free", kind: "dependency" },
  ];
  assert.deepEqual(viewConnections("deployment", nodes, edges, null), [
    { id: "e1", kind: "connection", sourceRef: "web", targetRef: "orders", ref: "r1" },
    { id: "e2", kind: "connection", sourceRef: "db", targetRef: "orders", ref: null },
    { id: "e3", kind: "dependency", sourceRef: "web", targetRef: null, ref: null },
  ]);
  assert.deepEqual(viewConnections("activity", nodes, edges, null), []);
  assert.equal(viewConnections("component", nodes, edges, null).length, edges.length); // connectors show relations too
  const interaction = {
    lifelines: [{ id: "a", ref: "web" }, { id: "b", ref: "orders" }],
    fragments: [
      { element: "message", id: "m1", sender: "a", receiver: "b", ref: "r1" },
      { element: "combined_fragment", id: "f", operands: [{ fragments: [{ element: "message", id: "m2", sender: "b", receiver: null }] }] },
    ],
  };
  assert.deepEqual(viewConnections("sequence", {}, [], interaction), [
    { id: "m1", kind: "message", sourceRef: "web", targetRef: "orders", ref: "r1" },
    { id: "m2", kind: "message", sourceRef: "orders", targetRef: null, ref: null },
  ]);
});

test("which relations a connection fits", () => {
  const reverse = { id: "e5", kind: "dependency", sourceRef: "orders", targetRef: "web" };
  assert.ok(fits(config, "deployment", reverse, model.relations[0])); // communicates: either way
  const flowBack = { id: "e2", kind: "connection", sourceRef: "db", targetRef: "orders" };
  assert.ok(!fits(config, "deployment", flowBack, model.relations[1])); // data_flow keeps its orientation
  assert.ok(fits(config, "deployment", { ...flowBack, sourceRef: "orders", targetRef: "db" }, model.relations[1]));
  assert.ok(!fits(config, "deployment", { kind: "deploys", sourceRef: "web", targetRef: "orders" }, model.relations[0]));
  assert.deepEqual(relationChoices(config, "deployment", { kind: "connection", sourceRef: "orders", targetRef: "web" }, model), [
    { value: "r1", label: "Web kommuniziert mit Orders" },
  ]);
});

test("relations an element has here but that no connection shows", () => {
  const shown = shownElements(nodes, null);
  assert.deepEqual([...shown].sort(), ["db", "orders", "web"]);
  const connections = [{ id: "e1", kind: "connection", sourceRef: "web", targetRef: "orders", ref: null }];
  // r1 is shown by a fitting (unlinked) connection, r3's other end (k8s) is not in the diagram
  assert.deepEqual(relationsNotDrawn(config, "deployment", model, "orders", shown, connections).map((r) => r.id), ["r2"]);
  assert.deepEqual(relationsNotDrawn(config, "deployment", model, "orders", shown, []).map((r) => r.id), ["r1", "r2"]);
  assert.deepEqual(relationsNotDrawn(config, "sequence", model, "orders", shown, []).map((r) => r.id), ["r1"]);
  assert.deepEqual(relationsNotDrawn(config, "activity", model, "orders", shown, []), []);
  const lifelines = { lifelines: [{ id: "a", ref: "web" }, { id: "b" }] };
  assert.deepEqual([...shownElements({}, lifelines)], ["web"]);
});

test("the requests of the resolutions", () => {
  assert.equal(resolutionRequest("p 1", { action: "propose", diagram: "d", prompt: "x" }), null);
  assert.deepEqual(resolutionRequest("p", { action: "hide", diagram: "d/1", relation: "r1" }), {
    method: "POST", path: "/api/diagrams/d%2F1/hidden-relations", body: { relation: "r1", hidden: true },
  });
  assert.deepEqual(resolutionRequest("p", { action: "link_relation", diagram: "d", view: "e1", relation: "r1" }).body, { view: "e1", relation: "r1" });
  assert.deepEqual(resolutionRequest("p", { action: "update_relation", relation: "r1", args: { properties: { port: "1" } } }), {
    method: "PATCH", path: "/api/projects/p/relations/r1", body: { properties: { port: "1" } },
  });
  assert.equal(resolutionRequest("p", { action: "remove_relation", relation: "r1" }).method, "DELETE");
  assert.equal(resolutionRequest("p", { action: "create_relation", args: { kind: "communicates" } }).path, "/api/projects/p/relations");
  assert.deepEqual(resolutionRequest("p", { action: "move_element", model_element: "web", args: { parent: "shop" } }), {
    method: "PATCH", path: "/api/projects/p/elements/web", body: { parent: "shop" },
  });
  assert.equal(resolutionRequest("p", { action: "sync" }).path, "/api/projects/p/model/sync");
  assert.equal(resolutionRequest("p", { action: "unknown" }), null);
  assert.ok(needsConfirmation({ action: "remove_relation" }) && !needsConfirmation({ action: "hide" }));
});

const panelModel = {
  elements: [
    { id: "web", name: "Web", usages: [{ diagram: "dep" }, { diagram: "seq" }] },
    { id: "orders", name: "Orders", usages: [{ diagram: "dep" }, { diagram: "seq" }] },
    { id: "db", name: "DB", usages: [{ diagram: "dep" }] },
  ],
  relations: [
    { id: "r1", kind: "communicates", source: "web", target: "orders", properties: { protocol: "HTTPS" }, description: "",
      usages: [{ diagram: "dep", title: "Plattform", view: "e1" }], shown: [{ diagram: "seq", title: "Checkout", view: "m1" }] },
    { id: "r2", kind: "data_flow", source: "orders", target: "db", properties: {}, description: "", usages: [], shown: [] },
  ],
  diagrams: [
    { id: "dep", title: "Plattform", type: "deployment", complete: ["data_flow"], hidden: [] },
    { id: "seq", title: "Checkout", type: "sequence", complete: [], hidden: ["r2"] },
    { id: "act", title: "Ablauf", type: "activity", complete: [], hidden: [] },
  ],
};

test("matrix cells: linked, shown, hidden, missing in a complete view, nothing", () => {
  const [r1, r2] = panelModel.relations;
  const [dep, seq, act] = panelModel.diagrams;
  assert.deepEqual(matrixCell(r1, dep, panelModel), { state: "linked", view: "e1" });
  assert.deepEqual(matrixCell(r1, seq, panelModel), { state: "shown", view: "m1" });
  assert.deepEqual(matrixCell(r2, seq, panelModel), { state: "hidden", view: null });
  assert.deepEqual(matrixCell(r2, dep, panelModel), { state: "missing", view: null }); // complete for data_flow, both ends shown
  assert.deepEqual(matrixCell(r1, act, panelModel), { state: "", view: null });
  assert.equal(MATRIX_LABELS.missing, "fehlt (vollständige Sicht)");
  const rows = matrixRows(panelModel);
  assert.deepEqual(rows.columns.map((d) => d.id), ["dep", "seq"]); // types that show relations
  assert.equal(rows.rows[0].label, "Web kommuniziert mit Orders");
  assert.deepEqual(rows.rows[1].cells.map((c) => c.state), ["missing", "hidden"]);
});

test("relation edits and texts", () => {
  const relation = panelModel.relations[0];
  assert.equal(relationChanges(relation, { kind: "communicates", source: "web", target: "orders", description: "", properties: { protocol: "HTTPS" } }), null);
  assert.deepEqual(relationChanges(relation, { kind: "communicates", source: "web", target: "db", description: " API ", properties: { protocol: "", port: "443" } }), {
    target: "db", description: "API", properties: { port: "443" }, remove_properties: ["protocol"],
  });
  assert.equal(relationUsageText(relation), "Plattform");
  assert.equal(relationUsageText(panelModel.relations[1]), "in keinem Diagramm verknüpft");
  const config = { project_model: { relation_properties: { deployed_on: [], communicates: ["protocol", "port", "direction", "mode"] } } };
  assert.deepEqual(allowedProperties(config, "deployed_on"), []);
  assert.deepEqual(allowedProperties(config, "communicates"), ["protocol", "port", "direction", "mode"]);
  assert.deepEqual(allowedProperties({}, "data_flow"), ["protocol", "port", "mode"]);
});

test("the shared fixture of shown relations (tests/fixtures/relations_shown.json, also checked by pytest)", async () => {
  const { readFileSync } = await import("node:fs");
  const { showsRelation: shows } = await import("../../arcviz/static/relations.js");
  const cases = JSON.parse(readFileSync(new URL("../fixtures/relations_shown.json", import.meta.url), "utf8"));
  const tables = {
    deployment: { relations: { connection: ["communicates", "data_flow"], dependency: ["communicates"], association: ["communicates"], replication: ["communicates"], deploys: ["deployed_on"] } },
    sequence: { relations: { message: ["communicates"] } },
    activity: { relations: {} },
  };
  const cfg = { diagram_types: tables };
  for (const c of cases) {
    const type = c.diagram.type || "deployment";
    const connections = viewConnections(type, c.diagram.nodes || {}, c.diagram.edges || [], c.diagram.interaction || null);
    assert.equal(shows(cfg, type, c.diagram.nodes || {}, connections, c.relation, c.kinds), c.shows, c.name);
  }
});
