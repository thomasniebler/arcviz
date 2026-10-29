import test from "node:test";
import assert from "node:assert/strict";
import { noteStep, progressPercent, progressText, remarkLabel, remarksBySection, severityLabel, statusLabel, stepFor } from "../../arcviz/static/gaps.js";

test("labels in the current language, unknown keys as they are", () => {
  assert.equal(statusLabel("missing"), "fehlt");
  assert.equal(severityLabel("high"), "wichtig");
  assert.equal(remarkLabel("thin"), "dünn");
  assert.equal(statusLabel("x"), "x");
});

test("the progress line and bar", () => {
  assert.equal(progressText({ complete: 7, total: 12 }), "7 von 12 Abschnitten vollständig");
  assert.equal(progressPercent({ complete: 3, total: 12 }), 25);
  assert.equal(progressPercent({ complete: 0, total: 0 }), 0);
});

test("every kind of action becomes a route or a control", () => {
  assert.deepEqual(stepFor({ kind: "tab", target: "vision" }), { label: "Vision ergänzen", route: "#/architecture/vision" });
  assert.deepEqual(stepFor({ kind: "tab", target: "decisions", item: "adr2" }), { label: "Entscheidung öffnen", route: "#/architecture/decisions", item: "adr2" });
  assert.deepEqual(stepFor({ kind: "tab", target: "documentation", section: "08" }), { label: "Notiz schreiben", route: "#/architecture/documentation", section: "08" });
  assert.deepEqual(stepFor({ kind: "model" }), { label: "Modell öffnen", click: "btn-model" });
  assert.deepEqual(stepFor({ kind: "new-diagram", diagram_type: "sequence" }), { label: "Diagramm anlegen: Sequenzdiagramm", click: "btn-new", diagramType: "sequence" });
  assert.equal(stepFor({ kind: "teleport" }), null);
  assert.equal(stepFor(undefined), null);
  assert.deepEqual(noteStep("03"), { label: "Notiz schreiben", route: "#/architecture/documentation", section: "03" });
});

test("remarks are grouped by section and a malformed answer is empty", () => {
  const grouped = remarksBySection([{ section: "01", text: "a" }, { section: "09", text: "b" }, { section: "01", text: "c" }]);
  assert.deepEqual(Object.keys(grouped), ["01", "09"]);
  assert.equal(grouped["01"].length, 2);
  assert.deepEqual(remarksBySection(null), {});
});
