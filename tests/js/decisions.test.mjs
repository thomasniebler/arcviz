// Unit tests for the decision support helpers (spec: docs/architecture/specs/decision-support.md).
// Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { adrLabel, changedFields, decisionFromAnalysis, groupScenarios, scenarioLine, sortedDecisions } from "../../arcviz/static/decisions.js";

test("ADR labels", () => {
  assert.equal(adrLabel("adr12"), "ADR-12");
  assert.equal(adrLabel("imported"), "imported");
});

test("scenarios grouped by driver, core first, unassigned last", () => {
  const drivers = [{ id: "d2" }, { id: "d1" }];
  const scenarios = [
    { id: "q1", driver: "d1" }, { id: "q2", driver: "d1", core: true }, { id: "q3" }, { id: "q4", driver: "d9" }, { id: "q5", driver: "d2" },
  ];
  assert.deepEqual(groupScenarios(scenarios, drivers).map((g) => [g.driver?.id ?? null, g.scenarios.map((s) => s.id)]), [
    ["d2", ["q5"]], ["d1", ["q2", "q1"]], [null, ["q3", "q4"]],
  ]);
  assert.equal(scenarioLine({ title: "T", stimulus: "Region weg", response_measure: "< 5 min" }), "Region weg → < 5 min");
  assert.equal(scenarioLine({ title: "T" }), "T");
});

test("decisions by status, newest first", () => {
  const list = [
    { id: "a", status: "draft", created_at: "1" }, { id: "b", status: "accepted", created_at: "1" },
    { id: "c", status: "draft", created_at: "2" }, { id: "d", status: "rejected", created_at: "3" },
  ];
  assert.deepEqual(sortedDecisions(list).map((d) => d.id), ["b", "c", "a", "d"]);
});

test("an ADR draft from an analysis option", () => {
  const scenario = { id: "q1", title: "Region fällt aus", stimulus: "Region A fällt aus", response_measure: "< 5 min", driver: "d1" };
  const analysis = {
    summary: "Heute fällt alles aus.",
    drivers: [{ driver: "d1", effect: "violates" }, { driver: "d2", effect: "at_risk" }, { driver: "d3", effect: "neutral" }],
    elements: [{ element: "api" }],
    sensitivity_points: ["Replikation"],
    tradeoffs: ["Verfügbarkeit gegen Kosten"],
    options: [
      { title: "Aktiv/Passiv", description: "", pros: ["günstig"], cons: ["RTO 30 min"] },
      { title: "Aktiv/Aktiv", description: "Zwei heiße Regionen.", pros: ["RTO < 1 min"], cons: [], instruction: "x" },
    ],
  };
  const body = decisionFromAnalysis(scenario, analysis, 1);
  assert.equal(body.title, "Region fällt aus: Aktiv/Aktiv");
  assert.equal(body.status, "draft");
  assert.equal(body.chosen, 1);
  assert.equal(body.scenario, "q1");
  assert.deepEqual(body.drivers, ["d1", "d2"]);
  assert.deepEqual(body.elements, ["api"]);
  assert.deepEqual(body.options[1], { title: "Aktiv/Aktiv", description: "Zwei heiße Regionen.", pros: ["RTO < 1 min"], cons: [] }); // no instruction
  assert.ok(body.context.startsWith("Szenario „Region fällt aus“:\nStimulus: Region A fällt aus\nReaktionsmaß: < 5 min\n\nHeute fällt alles aus."));
  assert.ok(body.context.includes("Zielkonflikte:\n- Verfügbarkeit gegen Kosten"));
  assert.equal(body.decision, "Gewählt: Aktiv/Aktiv. Zwei heiße Regionen.\n\nBegründung: ");
  assert.equal(body.consequences, "Positiv:\n- RTO < 1 min");
  assert.equal(decisionFromAnalysis(scenario, analysis, 0).consequences, "Positiv:\n- günstig\n\nNegativ:\n- RTO 30 min");
});

test("only changed fields are sent", () => {
  const item = { title: "A", core: false, drivers: ["d1"], role: "" };
  assert.equal(changedFields(item, { title: " A ", core: false, drivers: ["d1"] }, ["title", "core", "drivers"]), null);
  assert.deepEqual(changedFields(item, { title: "B", core: true, drivers: [], role: "Ops" }, ["title", "core", "drivers", "role"]), { title: "B", core: true, drivers: [], role: "Ops" });
});
