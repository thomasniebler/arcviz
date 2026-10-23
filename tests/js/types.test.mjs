// Diagram types in the UI (spec: docs/architecture/specs/diagram-types.md). Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { TYPE_LABELS, fieldSpecs, templateChoices, typeConfig, typedFieldNames, typedSpecs, typedValues, unsupportedTitle, viewOf } from "../../arcviz/static/types.js";

const config = {
  node_kinds: ["service"], container_kinds: ["vnet"], edge_kinds: ["connection"],
  diagram_types: {
    deployment: { renderer: "mermaid", node_kinds: ["service"], container_kinds: ["vnet"], edge_kinds: ["connection"], templates: ["demo", "empty"], features: { focus: true, direction: true, compare: true, plantuml: true } },
    sequence: { renderer: "svg", node_kinds: [], container_kinds: [], edge_kinds: [], templates: ["checkout", "empty"], features: { focus: false, direction: false, compare: false, plantuml: false } },
  },
};

test("typeConfig picks the type and defaults to deployment", () => {
  assert.equal(typeConfig(config, "sequence").renderer, "svg");
  assert.deepEqual(typeConfig(config, undefined).container_kinds, ["vnet"]);
});

test("typeConfig falls back to the top-level kinds of an older server", () => {
  const old = { node_kinds: ["a"], container_kinds: ["b"], edge_kinds: ["c"] };
  assert.deepEqual(typeConfig(old, "deployment").node_kinds, ["a"]);
  assert.equal(typeConfig(old, "deployment").features.compare, true);
  assert.equal(typeConfig(old, "sequence").features.compare, false);
  assert.equal(typeConfig(null, "deployment").renderer, "mermaid");
});

test("viewOf reads the source under the renderer's key", () => {
  assert.deepEqual(viewOf({ renderer: "svg", svg: "<svg/>", mermaid: null }), { renderer: "svg", source: "<svg/>" });
  assert.deepEqual(viewOf({ renderer: "mermaid", mermaid: "flowchart TB", svg: null }), { renderer: "mermaid", source: "flowchart TB" });
  assert.deepEqual(viewOf({ mermaid: "flowchart TB" }), { renderer: "mermaid", source: "flowchart TB" }, "payload of an older server");
  assert.equal(viewOf({ renderer: "svg", svg: null }), null);
  assert.equal(viewOf(null), null);
});

test("templates and labels per type", () => {
  assert.deepEqual(templateChoices(config, "sequence", { checkout: "Checkout" }), [["checkout", "Checkout"], ["empty", "empty"]]);
  assert.equal(TYPE_LABELS.sequence, "Sequenzdiagramm");
  assert.match(unsupportedTitle("sequence"), /Sequenzdiagramm/);
});

test("typedFieldNames lists the field names of all kinds, nodes or edges", () => {
  const typed = { diagram_types: { state_machine: {
    node_fields: { state: [{ name: "entry" }, { name: "do" }, { name: "exit" }] },
    edge_fields: { transition: [{ name: "effect" }] },
  } } };
  assert.deepEqual(typedFieldNames(typed, "state_machine"), ["entry", "do", "exit"]);
  assert.deepEqual(typedFieldNames(typed, "state_machine", true), ["effect"]);
  assert.deepEqual(typedFieldNames(config, "deployment"), []);
  assert.deepEqual(typedFieldNames(null, "activity"), []);
  assert.equal(TYPE_LABELS.state_machine, "Zustandsdiagramm");
});

test("field specs, the typed field values the server takes, and the specs of a type", () => {
  const typed = { diagram_types: { class: {
    node_fields: {
      class: [{ name: "attributes", list: true }, { name: "abstract", flag: true }],
      interface: [{ name: "operations", list: true }],
    },
    edge_fields: { association: [{ name: "source_multiplicity" }] },
  } } };
  assert.deepEqual(fieldSpecs(typed, "class", "interface").map((s) => s.name), ["operations"]);
  assert.deepEqual(fieldSpecs(typed, "class", "package"), []);
  assert.deepEqual(fieldSpecs(typed, "class", "association", true).map((s) => s.name), ["source_multiplicity"]);
  const specs = typedSpecs(typed, "class");
  assert.deepEqual(specs.map((s) => s.name), ["attributes", "abstract", "operations"]);
  assert.deepEqual(
    typedValues({ attributes: "- a: int\n\n  + b() \n", abstract: "True", other: "x", operations: "" }, specs),
    { attributes: ["- a: int", "+ b()"], abstract: true, other: "x", operations: [] },
  );
  assert.deepEqual(typedValues({ abstract: "" }, specs), { abstract: false }, "empty removes the flag");
  assert.deepEqual(typedValues(null, specs), {});
  assert.equal(TYPE_LABELS.class, "Klassendiagramm");
});
