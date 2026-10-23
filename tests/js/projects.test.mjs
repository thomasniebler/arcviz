// Pure logic of the project navigator (spec: docs/architecture/specs/project-navigator.md). Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  diagramOrder, nextDiagramId, pickDiagram, pickProject, projectOptionLabel, sectionChoices, siblingMove, treeRows,
} from "../../arcviz/static/projects.js";

const tree = {
  diagrams: ["overview", "gone"],
  sections: [
    { id: "backend", title: "Backend", diagrams: [], sections: [
      { id: "services", title: "Services", diagrams: ["orders", "payments"], sections: [] },
    ] },
    { id: "frontend", title: "Frontend", diagrams: ["web"], sections: [] },
  ],
};
const diagrams = [
  { id: "overview", title: "Überblick", section: null },
  { id: "orders", title: "Bestellungen", section: "services" },
  { id: "payments", title: "Zahlungen", section: "services" },
  { id: "web", title: "Web", section: "frontend" },
];
const view = { project: { id: "shop" }, tree, diagrams };

test("tree rows list sections first, then diagrams, with depth and sibling positions", () => {
  const rows = treeRows(tree, diagrams);
  assert.deepEqual(rows.map((r) => [r.kind, r.id, r.depth, r.index, r.count]), [
    ["section", "backend", 0, 0, 2],
    ["section", "services", 1, 0, 1],
    ["diagram", "orders", 2, 0, 2],
    ["diagram", "payments", 2, 1, 2],
    ["section", "frontend", 0, 1, 2],
    ["diagram", "web", 1, 0, 1],
    ["diagram", "overview", 0, 0, 1],
  ]);
  assert.equal(rows[2].title, "Bestellungen");
  assert.equal(rows[2].section, "services");
  assert.equal(rows[1].parent, "backend");
  assert.equal(rows[0].empty, false);
});

test("diagrams without info (deleted meanwhile) are skipped and not counted", () => {
  const top = treeRows(tree, diagrams).filter((r) => r.depth === 0 && r.kind === "diagram");
  assert.deepEqual(top.map((r) => [r.id, r.count]), [["overview", 1]]);
});

test("collapsed sections hide their contents but stay listed", () => {
  const rows = treeRows(tree, diagrams, ["backend"]);
  assert.deepEqual(rows.map((r) => r.id), ["backend", "frontend", "web", "overview"]);
  assert.equal(rows[0].collapsed, true);
});

test("an empty section is marked", () => {
  const rows = treeRows({ diagrams: [], sections: [{ id: "x", title: "X", diagrams: [], sections: [] }] }, []);
  assert.equal(rows[0].empty, true);
});

test("section choices are indented and start with the top level", () => {
  assert.deepEqual(sectionChoices(tree), [
    { value: "", label: "Oberste Ebene" },
    { value: "backend", label: "Backend" },
    { value: "services", label: "   Services" },
    { value: "frontend", label: "Frontend" },
  ]);
});

test("project picking prefers the wanted, then the remembered, then the first project", () => {
  const projects = [{ id: "a" }, { id: "b" }];
  assert.equal(pickProject(projects, "b", "a"), "b");
  assert.equal(pickProject(projects, "gone", "b"), "b");
  assert.equal(pickProject(projects, null, "gone"), "a");
  assert.equal(pickProject([], null, null), null);
});

test("diagram picking follows the tree order and ignores deleted ids", () => {
  assert.deepEqual(diagramOrder(view), ["orders", "payments", "web", "overview"]);
  assert.equal(pickDiagram(view, "web", "orders"), "web");
  assert.equal(pickDiagram(view, "gone", "payments"), "payments");
  assert.equal(pickDiagram(view, null, "gone"), "orders");
  assert.equal(pickDiagram({ tree: { diagrams: [], sections: [] }, diagrams: [] }, null, null), null);
});

test("sibling moves give the new position or null at an end", () => {
  const rows = treeRows(tree, diagrams);
  const orders = rows.find((r) => r.id === "orders");
  assert.equal(siblingMove(orders, -1), null);
  assert.equal(siblingMove(orders, 1), 1);
  const frontend = rows.find((r) => r.id === "frontend");
  assert.equal(siblingMove(frontend, -1), 0);
  assert.equal(siblingMove(frontend, 1), null);
});

test("the next diagram wraps around in tree order", () => {
  assert.equal(nextDiagramId(view, "orders"), "payments");
  assert.equal(nextDiagramId(view, "overview"), "orders");
  assert.equal(nextDiagramId(view, "gone"), "orders");
  assert.equal(nextDiagramId(view, "orders", -1), "overview");
  assert.equal(nextDiagramId(view, "web", -1), "payments");
  assert.equal(nextDiagramId({ tree: { diagrams: ["x"], sections: [] }, diagrams: [{ id: "x" }] }, "x"), null);
});

test("project labels count diagrams in German", () => {
  assert.equal(projectOptionLabel({ title: "Shop", diagram_count: 1 }), "Shop (1 Diagramm)");
  assert.equal(projectOptionLabel({ title: "Shop", diagram_count: 3 }), "Shop (3 Diagramme)");
});
