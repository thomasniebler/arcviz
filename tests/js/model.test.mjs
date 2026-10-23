// Unit tests for the helpers over the diagram JSON. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  containerOptions, descendantIds, isContainer, nodeIdForSvgId, parentFocus, propertyChanges, staleFocus, withCurrent,
} from "../../arcviz/static/model.js";

const CONTAINERS = ["deployment", "vnet", "subnet"];
const nodes = {
  prod: { id: "prod", name: "prod", kind: "deployment", parent: null },
  vnet: { id: "vnet", name: "Vnet", kind: "vnet", parent: "prod" },
  "svc-b": { id: "svc-b", name: "b-service", kind: "service", parent: "vnet" },
  "svc-a": { id: "svc-a", name: "a-service", kind: "service", parent: "vnet" },
  empty: { id: "empty", name: "Alpha subnet", kind: "subnet", parent: "prod" },
  db: { id: "db", name: "db", kind: "database", parent: null },
  // a leaf kind that has children anyway (legacy data) still counts as a container in the selector
  odd: { id: "odd", name: "odd", kind: "service", parent: null },
  child: { id: "child", name: "child", kind: "service", parent: "odd" },
};

test("isContainer follows the configured kinds", () => {
  assert.equal(isContainer(nodes.vnet, CONTAINERS), true);
  assert.equal(isContainer(nodes.db, CONTAINERS), false);
});

test("containerOptions lists containers depth-first, sorted by name, indented", () => {
  assert.deepEqual(containerOptions(nodes, CONTAINERS), [
    { id: "odd", label: "odd" },
    { id: "prod", label: "prod" },
    { id: "empty", label: "  Alpha subnet" },
    { id: "vnet", label: "  Vnet" },
  ]);
});

test("descendantIds walks the whole subtree", () => {
  assert.deepEqual([...descendantIds(nodes, "prod")].sort(), ["empty", "svc-a", "svc-b", "vnet"]);
  assert.deepEqual([...descendantIds(nodes, "db")], []);
});

test("descendantIds terminates on a parent cycle", () => {
  const cyclic = { a: { id: "a", parent: "b" }, b: { id: "b", parent: "a" } };
  assert.deepEqual([...descendantIds(cyclic, "a")].sort(), ["a", "b"]);
});

test("withCurrent keeps an unconfigured current kind selectable", () => {
  assert.deepEqual(withCurrent(["a", "b"], "b"), ["a", "b"]);
  assert.deepEqual(withCurrent(["a", "b"], "legacy"), ["a", "b", "legacy"]);
  assert.deepEqual(withCurrent(["a"], null), ["a"]);
});

test("parentFocus goes one level up", () => {
  assert.equal(parentFocus(nodes, "vnet"), "prod");
  assert.equal(parentFocus(nodes, "prod"), null);
  assert.equal(parentFocus(nodes, null), null);
  assert.equal(parentFocus(nodes, "gone"), null);
});

test("nodeIdForSvgId strips the render prefix and the node wrapper", () => {
  const ids = { vnet: "n_vnet", "svc-a": "n_svc_a" };
  assert.equal(nodeIdForSvgId("arcviz-12-n_vnet", ids), "vnet");                  // cluster
  assert.equal(nodeIdForSvgId("arcviz-3-flowchart-n_svc_a-7", ids), "svc-a");     // node
  assert.equal(nodeIdForSvgId("flowchart-n_svc_a-0", ids), "svc-a");
  assert.equal(nodeIdForSvgId("arcviz-3-flowchart-n_unknown-1", ids), null);
  assert.equal(nodeIdForSvgId("", ids), null);
});

test("propertyChanges reports set and removed keys, or null when unchanged", () => {
  const current = { cidr: "10.0.0.0/16", tier: "gold" };
  assert.equal(propertyChanges([["cidr", "10.0.0.0/16"], [" tier ", "gold"]], current), null);
  assert.deepEqual(propertyChanges([["cidr", "10.1.0.0/16"], ["tier", "gold"]], current), {
    properties: { cidr: "10.1.0.0/16", tier: "gold" }, remove_properties: [],
  });
  assert.deepEqual(propertyChanges([["cidr", "10.0.0.0/16"], ["", "ignored"]], current), {
    properties: { cidr: "10.0.0.0/16" }, remove_properties: ["tier"],
  });
  assert.deepEqual(propertyChanges([["port", "443"]], undefined), { properties: { port: "443" }, remove_properties: [] });
  assert.equal(propertyChanges([], undefined), null);
});

test("propertyChanges sends typed fields as `fields`, never as properties (ADR-0177)", () => {
  const typed = ["entry", "do"];
  const current = { entry: "x", owner: "me" };
  // nothing changed
  assert.equal(propertyChanges([["entry", "x"], ["owner", "me"]], current, typed), null);
  // a typed value changed: only `fields` carries it
  assert.deepEqual(propertyChanges([["entry", "y"], ["owner", "me"]], current, typed), {
    properties: {}, remove_properties: [], fields: { entry: "y" },
  });
  // a typed row removed: removed through `fields`, not remove_properties
  assert.deepEqual(propertyChanges([["owner", "me"]], current, typed), { properties: {}, remove_properties: [], fields: { entry: "" } });
  // a typed row added next to a changed free property
  assert.deepEqual(propertyChanges([["entry", "x"], ["do", "z"], ["owner", "you"]], current, typed), {
    properties: { owner: "you" }, remove_properties: [], fields: { do: "z" },
  });
  // free changes alone behave as without typed names
  assert.deepEqual(propertyChanges([["entry", "x"]], current, typed), { properties: {}, remove_properties: ["owner"] });
  assert.equal(propertyChanges([], {}, typed), null);
});

test("staleFocus: an answer for another focus than the shown one is stale (#216)", () => {
  const shown = { prod: { id: "prod", parent: null }, vnet: { id: "vnet", parent: "prod" } };
  // asked for the focus shown now: never stale
  assert.equal(staleFocus("prod", "prod", shown, true), false);
  assert.equal(staleFocus(null, null, shown, true), false);
  assert.equal(staleFocus(undefined, null, shown, true), false);
  // the server shows a focus only when it was asked for it: a different one is an older request
  assert.equal(staleFocus("prod", null, shown, true), true);
  assert.equal(staleFocus("vnet", "prod", shown, true), true);
  // the whole diagram while a focus is shown: an older request, unless the focus cannot be shown any more
  assert.equal(staleFocus(null, "prod", shown, true), true);
  assert.equal(staleFocus(null, "gone", shown, true), false); // the container was removed: the fallback stands
  assert.equal(staleFocus(null, "prod", shown, false), false); // the type cannot focus
  assert.equal(staleFocus(null, "prod", undefined, true), false);
});
