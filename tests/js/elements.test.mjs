// Sequence-diagram elements in the inspector (spec: docs/architecture/specs/sequence-diagrams.md).
import { test } from "node:test";
import assert from "node:assert/strict";
import { ELEMENT_LABELS, elementFields, elementRemove, elementTitle, elementUpdate, findElement, messageCount } from "../../arcviz/static/elements.js";

const interaction = {
  lifelines: [{ id: "a", name: "Client", type: null, actor: true }, { id: "b", name: "Server", type: "API", actor: false }],
  fragments: [
    { element: "message", id: "m1", name: "go", sort: "synchCall", kind: "complete", sender: "a", receiver: "b" },
    { element: "combined_fragment", id: "cf1", operator: "alt", covered: ["a", "b"], messages: [], operands: [
      { id: "op1", guard: "ok", fragments: [{ element: "message", id: "m2", name: "ok", sort: "reply", kind: "complete", sender: "b", receiver: "a" }] },
      { id: "op2", guard: null, fragments: [] },
    ] },
    { element: "interaction_use", id: "ref1", refers_to: "Login", covered: ["a"] },
    { element: "destruction_occurrence_specification", id: "x1", lifeline: "b" },
  ],
  executions: [{ id: "ex1", lifeline: "b", start: "m1", finish: "m2" }],
};
const ctx = { lifelines: interaction.lifelines, message_sorts: ["synchCall", "reply"], interaction_operators: ["alt", "opt", "ignore"] };

test("findElement finds every kind, nested ones included", () => {
  const kinds = Object.fromEntries(["a", "m1", "cf1", "op2", "m2", "ref1", "x1", "ex1"].map((id) => [id, findElement(interaction, id)?.kind]));
  assert.deepEqual(kinds, { a: "lifeline", m1: "message", cf1: "combined_fragment", op2: "interaction_operand", m2: "message", ref1: "interaction_use", x1: "destruction_occurrence_specification", ex1: "execution_specification" });
  assert.equal(findElement(interaction, "nope"), null);
  assert.equal(findElement(null, "a"), null);
  assert.equal(messageCount(interaction), 2);
});

test("fields per kind", () => {
  const message = elementFields("message", findElement(interaction, "m1").element, ctx);
  assert.deepEqual(message.map((f) => f.key), ["name", "sort", "sender", "receiver"]);
  assert.deepEqual(message[2].options[0], ["", "— (gefundene Nachricht)"]);
  assert.deepEqual(elementFields("combined_fragment", findElement(interaction, "cf1").element, ctx).map((f) => f.key), ["operator", "covered"]);
  assert.deepEqual(elementFields("combined_fragment", { ...findElement(interaction, "cf1").element, operator: "ignore" }, ctx).map((f) => f.key), ["operator", "covered", "messages"]);
  assert.equal(elementFields("execution_specification", interaction.executions[0], ctx)[0].type, "info");
  assert.deepEqual(elementFields("destruction_occurrence_specification", interaction.fragments[3], ctx), []);
});

test("updates carry only what changed", () => {
  const m1 = findElement(interaction, "m1").element;
  assert.equal(elementUpdate("message", m1, { name: "go", sort: "synchCall", sender: "a", receiver: "b" }), null);
  assert.deepEqual(elementUpdate("message", m1, { name: " run ", sort: "reply", sender: "", receiver: "b" }), { op: "update_message", args: { id: "m1", name: "run", sort: "reply", sender: "" } });
  assert.deepEqual(elementUpdate("combined_fragment", findElement(interaction, "cf1").element, { operator: "alt", covered: ["a"] }), { op: "update_combined_fragment", args: { id: "cf1", covered: ["a"] } });
  assert.deepEqual(elementUpdate("combined_fragment", { ...findElement(interaction, "cf1").element, operator: "ignore" }, { messages: "a, b" }), { op: "update_combined_fragment", args: { id: "cf1", messages: ["a", "b"] } });
  assert.deepEqual(elementUpdate("interaction_operand", findElement(interaction, "op2").element, { guard: "else" }), { op: "update_interaction_operand", args: { id: "op2", guard: "else" } });
  assert.deepEqual(elementUpdate("lifeline", interaction.lifelines[0], { actor: false }), { op: "update_lifeline", args: { id: "a", actor: false } });
  assert.equal(elementUpdate("execution_specification", interaction.executions[0], {}), null);
});

test("delete and titles", () => {
  assert.deepEqual(elementRemove("lifeline", interaction.lifelines[1]), { op: "remove_lifeline", args: { id: "b" } });
  assert.deepEqual(elementRemove("message", interaction.fragments[0]), { op: "remove_fragment", args: { id: "m1" } });
  assert.equal(elementTitle("combined_fragment", interaction.fragments[1]), "Kombiniertes Fragment alt");
  assert.equal(elementTitle("interaction_operand", { guard: "ok" }), "Operand [ok]");
  assert.ok(Object.keys(ELEMENT_LABELS).length === 7);
});

test("a lifeline's note is a textarea field; an edit sends it trimmed, clearing sends an empty string", () => {
  const lifeline = { id: "k", name: "Kasse", description: "Frontend" };
  const field = elementFields("lifeline", lifeline).find((f) => f.key === "description");
  assert.deepEqual(field, { key: "description", label: "Notiz", type: "textarea", value: "Frontend" });
  assert.equal(elementFields("lifeline", { id: "k", name: "K" }).find((f) => f.key === "description").value, "");
  assert.equal(elementUpdate("lifeline", lifeline, { description: " Frontend " }), null);
  assert.deepEqual(elementUpdate("lifeline", lifeline, { description: " Web " }), { op: "update_lifeline", args: { id: "k", description: "Web" } });
  assert.deepEqual(elementUpdate("lifeline", lifeline, { description: "" }), { op: "update_lifeline", args: { id: "k", description: "" } });
});
