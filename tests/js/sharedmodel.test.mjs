import { test } from "node:test";
import assert from "node:assert/strict";
import {
  defaultKind,
  elementChanges,
  findModelElement,
  kindLabel,
  linkChoices,
  linkableKinds,
  carriedText,
  propagationText,
  usageText,
  viewElement,
} from "../../arcviz/static/sharedmodel.js";

const config = {
  diagram_types: {
    sequence: { linkable: { lifeline: ["component", "system", "subsystem", "node", "data", "actor"] } },
    activity: { linkable: { activity_partition: ["component", "actor"], data_store_node: ["data"] } },
  },
};
const model = {
  elements: [
    { id: "shop", name: "Shop", kind: "system", description: "", parent: null, usages: [] },
    { id: "kunde", name: "Kunde", kind: "actor", description: "Kauft ein", parent: null, usages: [{ title: "Checkout" }, { title: "Ablauf" }, { title: "Checkout" }] },
    { id: "db", name: "Datenbank", kind: "data", description: "", parent: "shop", usages: [] },
  ],
};

test("kinds and labels", () => {
  assert.equal(kindLabel("subsystem"), "Teilsystem");
  assert.equal(kindLabel("other"), "other");
  assert.deepEqual(linkableKinds(config, "activity", "data_store_node"), ["data"]);
  assert.deepEqual(linkableKinds(config, "activity", "opaque_action"), []);
  assert.deepEqual(linkableKinds({}, "deployment", "service"), []);
});

test("view elements of graphs and of sequence diagrams", () => {
  assert.deepEqual(viewElement({ a: { name: "A", kind: "service", ref: "shop" } }, null, "a"), { id: "a", name: "A", kind: "service", ref: "shop" });
  assert.deepEqual(viewElement({ a: { name: "A", kind: "service" } }, null, "a").ref, null);
  const interaction = { lifelines: [{ id: "k", name: "Kunde", actor: true }] };
  assert.deepEqual(viewElement({}, interaction, "k"), { id: "k", name: "Kunde", kind: "lifeline", ref: null, actor: true });
  assert.equal(viewElement({}, interaction, "m1"), null);
});

test("link choices follow the allowed kinds and sort by name", () => {
  assert.deepEqual(linkChoices(model, ["data", "system"]).map((c) => c.value), ["db", "shop"]);
  assert.equal(linkChoices(model, ["actor"])[0].label, "Kunde (Akteur)");
  assert.deepEqual(linkChoices(null, ["actor"]), []);
  assert.equal(findModelElement(model, "db").name, "Datenbank");
  assert.equal(findModelElement(model, "nope"), null);
});

test("promote preselects an actor for actor lifelines", () => {
  assert.equal(defaultKind({ actor: true }, ["component", "actor"]), "actor");
  assert.equal(defaultKind({ actor: false }, ["component", "actor"]), "component");
  assert.equal(defaultKind({}, []), null);
});

test("usages, changes and propagation texts", () => {
  assert.equal(usageText(model.elements[1]), "Checkout (2×), Ablauf");
  assert.equal(usageText(model.elements[0]), "nicht verwendet");
  assert.equal(elementChanges(model.elements[1], { name: " Kunde ", description: "Kauft ein", kind: "actor" }), null);
  assert.deepEqual(elementChanges(model.elements[1], { name: "Kundin", description: "", kind: "actor" }), { name: "Kundin", description: "" });
  assert.deepEqual(elementChanges(model.elements[2], { name: "Datenbank", description: "", kind: "data", parent: "" }), { parent: null });
  assert.equal(propagationText({ propagated: ["a", "b"], failed: [] }), "umbenannt in 2 Diagramm(en)");
  assert.equal(propagationText({ propagated: [], failed: ["c"] }), "noch nicht in c (später „Abgleichen“)");
});

test("carriedText: what a move into another project brought along (#153)", () => {
  const none = { copied: [], mapped: [], dropped: [] };
  assert.equal(carriedText(null), "");
  assert.equal(carriedText({ elements: none, relations: none }), "");
  assert.equal(
    carriedText({
      elements: { copied: [{ id: "a", as: "a" }, { id: "b", as: "b-2" }], mapped: [{ id: "c", as: "c" }], dropped: [] },
      relations: { copied: [{ id: "r1", as: "r4" }], mapped: [], dropped: [] },
    }),
    "Modell übernommen: 2 Element(e) kopiert, 1 zugeordnet, 1 Beziehung(en) kopiert",
  );
  assert.equal(
    carriedText({
      elements: { copied: [], mapped: [], dropped: [{ id: "db", reason: "x" }] },
      relations: { copied: [], mapped: [], dropped: [{ id: "r2", reason: "y" }] },
    }),
    "Verknüpfung gelöst: Element db, Beziehung r2",
  );
});

test("adoption preview and result texts", async () => {
  const { adoptionPreview, adoptionText } = await import("../../arcviz/static/sharedmodel.js");
  const plan = {
    counts: { linked: 1, created: 2, skipped: 1 },
    diagrams: [{ title: "Plattform", linked: [{ name: "API" }], created: [{ name: "DB" }, { name: "Queue" }], skipped: [{ name: "X" }] }],
  };
  const text = adoptionPreview(plan);
  assert.ok(text.startsWith("Unverknüpfte Bausteine übernehmen? 1 mit vorhandenen Projektelementen verknüpfen, 2 als neue Projektelemente anlegen, 1 bleiben unverknüpft."));
  assert.ok(text.includes("Plattform: API → vorhanden, DB (neu), Queue (neu), X (bleibt: passt nicht)"));
  assert.equal(adoptionText({ counts: { linked: 1, created: 2 }, failed: ["d"] }), "3 Baustein(e) übernommen (2 neue Projektelemente); 1 Diagramm(e) konnten nicht geschrieben werden.");
});

test("code links: label and request body", async () => {
  const { codeRefLabel, codeRefBody, codeRefsSummary } = await import("../../arcviz/static/sharedmodel.js");
  assert.equal(codeRefLabel({ path: "src/api", repo: "", symbol: "", note: "" }), "src/api");
  assert.equal(codeRefLabel({ path: "src/api", repo: "backend", symbol: "App", note: "Einstieg" }), "backend:src/api · App (Einstieg)");
  assert.deepEqual(codeRefBody({ path: " src/api ", repo: " backend ", symbol: "", note: "" }), { path: "src/api", repo: "backend", symbol: "", note: "" });
  assert.equal(codeRefBody({ path: "  ", repo: "x" }), null);
  assert.equal(codeRefsSummary({ code_refs: [] }), "Code");
  assert.equal(codeRefsSummary({}), "Code");
  assert.equal(codeRefsSummary({ code_refs: [{}, {}] }), "Code (2)");
});
