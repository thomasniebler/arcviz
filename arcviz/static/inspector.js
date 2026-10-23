// Direct model edits without the LLM, via POST /ops: the element inspector (graph nodes, or the
// interaction elements of sequence diagrams), clicks on the diagram and the layout-direction selector.
// Concurrent edits (spec: docs/architecture/specs/collaboration.md, ADR-0099): the inspector keeps the base (revision
// and values) of the element it shows, keeps typed values on a remote change and resolves a 412 field by field.
import { api, ifMatch } from "./api.js";
import { canonicalProperties, changedBy, displayValue, fieldConflicts, nodeValues, remoteChange, same } from "./conflicts.js";
import { createWriteQueue } from "./writequeue.js";
import { renderView } from "./canvas.js";
import { $, el, option } from "./dom.js";
import { refreshDiagramListLabel } from "./navigator.js";
import { ELEMENT_LABELS, elementFields, elementRemove, elementTitle, elementUpdate, findElement } from "./elements.js";
import { edgeSummary } from "./format.js";
import { notifySelection, onDiagramShown, onDiagramSwitched, setFocus, showDiagram } from "./diagram.js";
import { containerOptions, descendantIds, isContainer, nodeIdForSvgId, propertyChanges, withCurrent } from "./model.js";
import { defaultKind, findModelElement, kindLabel, linkChoices, linkableKinds, propagationText, viewElement } from "./sharedmodel.js";
import { completenessText, relationChoices, relationKindName, relationText, relationsNotDrawn, shownElements, viewConnections } from "./relations.js";
import { focusQuery, state } from "./state.js";
import { fieldSpecs, typeConfig, typedFieldNames, typedSpecs, typedValues } from "./types.js";
import { setBusy, setStatus } from "./ui.js";
import { N_, t } from "./i18n.js";

let insp = null;
let base = null; // {id, mode, revision, values}: what the shown element was when the form was opened or rebased

const EDGE_DIRECTIONS = [["default", N_("Standard der Art")], ["forward", N_("→ vorwärts")], ["both", N_("↔ beidseitig")], ["none", N_("— ohne Pfeil")]];

// ---------- clicks on the rendered diagram ----------------------------------------------

/** Map an element inside the rendered SVG back to the node id it represents. */
function nodeIdOf(target) {
  const g = target.closest?.("g.cluster, g.node, g.icon-shape");
  if (!g) return null;
  return nodeIdForSvgId(g.id, state.mermaidIds);
}

/** Canvas click: drill into a container, jump to a stub's container, or edit a leaf element. */
export function handleDiagramClick(target) {
  if (state.compare) return; // the comparison shows a merged view, not the model
  if (state.view?.renderer === "svg") { // own SVG renderer: every element carries its model id
    const g = target.closest?.("[data-id]");
    if (g && findElement(state.interaction, g.dataset.id)) openElementInspector(g.dataset.id);
    return;
  }
  const id = nodeIdOf(target);
  if (!id || !state.nodes[id]) return;
  const node = state.nodes[id];
  const g = target.closest("g.cluster, g.node, g.icon-shape");
  if (g.classList.contains("cluster")) setFocus(id);                       // drill into a container
  else if (g.classList.contains("stub")) setFocus(node.parent ?? null);    // jump to a stub's container
  else openInspector(id);                                                   // edit a leaf element
}

// ---------- element inspector -------------------------------------------------------------

/** A row of the property table. A list field (class members) takes a textarea, one entry per line; the row of a typed
 *  field is a normal row, whose key the server reserves for `fields` (ADR-0177). */
function propRow(key = "", value = "", spec = null) {
  const tr = document.createElement("tr");
  const cell = (child) => { const td = document.createElement("td"); td.appendChild(child); return td; };
  const keyInput = document.createElement("input");
  keyInput.className = "prop-key"; keyInput.placeholder = t("Schlüssel"); keyInput.value = key;
  const valueInput = document.createElement(spec?.list ? "textarea" : "input");
  valueInput.className = "prop-value";
  valueInput.placeholder = spec?.list ? t("Eine Zeile pro Eintrag") : t("Wert");
  if (spec?.list) valueInput.rows = 3;
  valueInput.value = value;
  const remove = document.createElement("button");
  remove.type = "button"; remove.className = "small"; remove.title = t("Eigenschaft entfernen"); remove.textContent = "−";
  remove.addEventListener("click", () => tr.remove());
  const actions = cell(remove);
  actions.className = "actions";
  tr.append(cell(keyInput), cell(valueInput), actions);
  return tr;
}

function showInspectorError(message) {
  insp.error.textContent = message || "";
  insp.error.classList.toggle("hidden", !message);
}

/** The rows of a property table: the stored properties, then an empty row for every typed field of the kind that is not set. */
function fillPropRows(tbody, properties, kind, edges = false) {
  const bySpec = new Map(typedSpecs(state.config, state.type, edges).map((s) => [s.name, s]));
  for (const [k, v] of Object.entries(properties || {})) tbody.appendChild(propRow(k, v, bySpec.get(k)));
  for (const spec of fieldSpecs(state.config, state.type, kind, edges)) {
    if (!(spec.name in (properties || {}))) tbody.appendChild(propRow(spec.name, "", spec));
  }
}

/** Collect a key/value table into {properties, remove_properties, fields?} relative to `current`, or null if unchanged. */
function propChanges(tbody, current, edges = false) {
  const rows = [...tbody.querySelectorAll("tr")].map((tr) => [tr.querySelector(".prop-key").value, tr.querySelector(".prop-value").value]);
  const changes = propertyChanges(rows, current, typedFieldNames(state.config, state.type, edges));
  if (changes?.fields) changes.fields = typedValues(changes.fields, typedSpecs(state.config, state.type, edges));
  return changes;
}

/** Edge table of the inspector; separate so edge-only commits do not rebuild (and reset) the node form. */
function renderInspectorEdges(id) {
  insp.edges.innerHTML = "";
  const touching = state.edges.filter((e) => e.source === id || e.target === id);
  const elements = state.projectId && touching.length && (typeConfig(state.config, state.type).completeness || []).length ? projectElements() : null;
  insp.noEdges.classList.toggle("hidden", touching.length > 0);
  for (const edge of touching) {
    // Built with DOM APIs: ids are free-form strings and must never reach innerHTML.
    const tr = document.createElement("tr");
    tr.className = "edge-row";
    const ends = document.createElement("td");
    ends.className = "edge-ends";
    ends.title = `${edge.id}: ${edge.source} → ${edge.target}`;
    const other = edge.source === id ? edge.target : edge.source;
    ends.textContent = edge.source === id ? `→ ${other}` : `← ${other}`;
    const labelCell = document.createElement("td");
    const labelInput = document.createElement("input");
    labelInput.className = "edge-label"; labelInput.placeholder = t("Label"); labelInput.value = edge.label || "";
    labelCell.appendChild(labelInput);
    const kindCell = document.createElement("td");
    const kindSel = document.createElement("select");
    kindSel.className = "edge-kind";
    for (const k of withCurrent(typeConfig(state.config, state.type).edge_kinds, edge.kind)) kindSel.appendChild(option(k, k, k === edge.kind));
    kindCell.appendChild(kindSel);
    const actions = document.createElement("td");
    actions.className = "actions";
    const more = document.createElement("button");
    more.type = "button"; more.className = "small edge-more"; more.title = t("Richtung und Eigenschaften der Kante (z. B. protocol, port)");
    // The button summarises what the details row holds: an explicit direction and the number of properties.
    more.textContent = edgeSummary(edge);
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "small danger"; remove.title = t("Kante entfernen"); remove.textContent = "−";
    actions.append(more, remove);
    tr.append(ends, labelCell, kindCell, actions);

    // Collapsible details below the row: direction, and the same key/value editor as for elements.
    const propsTr = document.createElement("tr");
    propsTr.className = "edge-props hidden";
    const propsCell = document.createElement("td");
    propsCell.colSpan = 4;
    const dirLabel = document.createElement("label");
    dirLabel.className = "inline";
    dirLabel.textContent = t("Richtung") + " ";
    const dirSel = document.createElement("select");
    dirSel.className = "edge-direction";
    for (const [v, label] of EDGE_DIRECTIONS) dirSel.appendChild(option(v, t(label), v === (edge.direction || "default")));
    dirLabel.appendChild(dirSel);
    const propsTable = document.createElement("table");
    const propsBody = document.createElement("tbody");
    propsTable.appendChild(propsBody);
    fillPropRows(propsBody, edge.properties, edge.kind, true);
    const propAdd = document.createElement("button");
    propAdd.type = "button"; propAdd.className = "small"; propAdd.textContent = t("+ Eigenschaft");
    const propSave = document.createElement("button");
    propSave.type = "button"; propSave.className = "small edge-props-save"; propSave.textContent = t("Übernehmen");
    // Types whose edges are always directed (activity: ActivityEdges) have no direction to choose.
    if (typeConfig(state.config, state.type).features?.edge_direction === false) dirLabel.classList.add("hidden");
    const relationBox = document.createElement("div");
    relationBox.className = "edge-relation insp-model";
    propsCell.append(dirLabel, propsTable, propAdd, propSave, relationBox);
    renderRelationBox(relationBox, edge.id, elements);
    propsTr.appendChild(propsCell);

    const commit = () => runOps([{ op: "update_edge", args: { id: edge.id, label: labelInput.value, kind: kindSel.value, direction: dirSel.value }, label: t("Kante {id} geändert", { id: edge.id }) }], { refresh: "edges" });
    labelInput.addEventListener("change", commit);
    kindSel.addEventListener("change", commit);
    dirSel.addEventListener("change", commit);
    more.addEventListener("click", () => propsTr.classList.toggle("hidden"));
    propAdd.addEventListener("click", () => { const row = propRow(); propsBody.appendChild(row); row.querySelector(".prop-key").focus(); });
    propSave.addEventListener("click", () => {
      const changes = propChanges(propsBody, edge.properties, true);
      if (changes) runOps([{ op: "update_edge", args: { id: edge.id, ...changes }, label: t("Kante {id}: Eigenschaften", { id: edge.id }) }], { refresh: "edges" });
    });
    remove.addEventListener("click", () => runOps([{ op: "remove_edge", args: { id: edge.id }, label: t("Kante {id} entfernt", { id: edge.id }) }], { refresh: "edges" }));
    insp.edges.append(tr, propsTr);
  }
}

export function openInspector(id, { keepError = false } = {}) {
  const node = state.nodes[id];
  if (!node) return;
  state.inspecting = id;
  el.inspector.dataset.mode = "node";
  if (!keepError) showInspectorError("");
  insp.title.textContent = `«${node.kind}» ${node.name}`;
  insp.id.textContent = node.id;
  insp.name.value = node.name;
  insp.kind.innerHTML = "";
  for (const k of withCurrent(typeConfig(state.config, state.type).node_kinds, node.kind)) insp.kind.appendChild(option(k, k, k === node.kind));
  insp.parent.innerHTML = "";
  insp.parent.appendChild(option("", t("— oberste Ebene —"), node.parent == null));
  const excluded = descendantIds(state.nodes, id); excluded.add(id);
  for (const c of containerOptions(state.nodes, typeConfig(state.config, state.type).container_kinds)) {
    if (!excluded.has(c.id)) insp.parent.appendChild(option(c.id, c.label.replace(/ /g, " "), c.id === node.parent));
  }
  insp.description.value = node.description || "";
  insp.props.innerHTML = "";
  fillPropRows(insp.props, node.properties, node.kind);
  renderInspectorEdges(id);
  insp.cascadeLabel.classList.toggle("hidden", !isContainer(node, typeConfig(state.config, state.type).container_kinds));
  insp.cascade.checked = false;
  el.inspector.classList.remove("hidden");
  insp.name.focus();
  renderModelSection(id);
  startEditing(id);
}

// ---------- the shared project model (spec: shared-project-model.md) ----------------------------------------

function showModeError(message) {
  if (el.inspector.dataset.mode === "element") showElementError(message);
  else showInspectorError(message);
}

function modelButton(label, onClick, className = "small") {
  const b = document.createElement("button");
  b.type = "button"; b.className = className; b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

function kindPicker(kinds, selected) {
  const select = document.createElement("select");
  select.setAttribute("aria-label", t("Art des Projektelements"));
  for (const k of kinds) select.appendChild(option(k, kindLabel(k), k === selected));
  return select;
}

/** Which project element the inspected node or lifeline stands for, and the actions to link, unlink or promote it. */
async function renderModelSection(id) {
  const section = insp.model;
  const shown = viewElement(state.nodes, state.interaction, id);
  const kinds = shown ? linkableKinds(state.config, state.type, shown.kind) : [];
  section.innerHTML = "";
  section.classList.toggle("hidden", !kinds.length || !state.projectId);
  if (!kinds.length || !state.projectId) return;
  let model = null;
  try { model = await api(`/api/projects/${encodeURIComponent(state.projectId)}/elements`); } catch { model = null; }
  if (state.inspecting !== id) return; // another element was opened meanwhile
  // Built with DOM APIs: element names and descriptions are free-form and must never reach innerHTML.
  section.innerHTML = "";
  const heading = document.createElement("h4");
  heading.textContent = t("Projektelement");
  section.appendChild(heading);
  const actions = document.createElement("div");
  actions.className = "model-actions";
  if (shown.ref) {
    const element = findModelElement(model, shown.ref);
    const linked = document.createElement("p");
    linked.className = "model-linked";
    linked.textContent = element ? t("Steht für „{name}“ ({kind}).", { name: element.name, kind: kindLabel(element.kind) }) : t("Verweist auf das fehlende Projektelement „{ref}“.", { ref: shown.ref });
    section.appendChild(linked);
    if (element?.description) { // the element's own description: read-only here, edited in the model panel
      const label = document.createElement("h5");
      label.className = "model-description-label";
      label.textContent = t("Beschreibung im Projektmodell");
      section.appendChild(label);
      const description = document.createElement("p");
      description.className = "muted model-linked model-description";
      description.textContent = element.description;
      section.appendChild(description);
    }
    const hint = document.createElement("p");
    hint.className = "muted model-hint";
    hint.textContent = t("Der Name kommt aus dem Projektmodell: eine Umbenennung hier erreicht jedes Diagramm, das das Element zeigt.");
    section.appendChild(hint);
    actions.appendChild(modelButton(t("Verknüpfung lösen"), () => linkAction("unlink", { view: id }), "small model-unlink"));
    hiddenMarkers(section, model, shown.ref);
  } else {
    const choices = linkChoices(model, kinds);
    if (choices.length) {
      const select = document.createElement("select");
      select.className = "model-choice";
      select.setAttribute("aria-label", t("Projektelement"));
      for (const c of choices) select.appendChild(option(c.value, c.label));
      actions.append(select, modelButton(t("Verknüpfen"), () => linkAction("link", { view: id, element: select.value }), "small model-link"));
    }
    const kind = kindPicker(kinds, defaultKind(shown, kinds));
    actions.append(kind, modelButton(t("Als Projektelement anlegen"), () => linkAction("promote", { view: id, kind: kind.value }), "small model-promote"));
  }
  section.appendChild(actions);
}

/** GET …/elements of the open project (elements and relations), or null when it cannot be read. */
function projectElements() {
  return api(`/api/projects/${encodeURIComponent(state.projectId)}/elements`).catch(() => null);
}

/** Which model relation an edge or message shows (the **Projektbeziehung** section, spec: model-relations.md), and the
 *  actions to link one of the relations that fit its ends, or to end the link. `elements`: a pending
 *  `projectElements()` shared by all rows of one render. */
async function renderRelationBox(box, connectionId, elements = null) {
  box.innerHTML = "";
  const showable = typeConfig(state.config, state.type).completeness || [];
  if (!state.projectId || !showable.length) return;
  const model = await (elements || projectElements()); // one request per render, shared by every edge row
  if (!model) return;
  const connection = viewConnections(state.type, state.nodes, state.edges, state.interaction).find((c) => c.id === connectionId);
  if (!connection || state.inspecting === null) return;
  box.innerHTML = "";
  const heading = document.createElement("h5");
  heading.textContent = t("Projektbeziehung");
  const text = document.createElement("p");
  text.className = "model-linked relation-linked";
  const actions = document.createElement("div");
  actions.className = "model-actions";
  if (connection.ref) {
    const relation = (model?.relations || []).find((r) => r.id === connection.ref);
    text.textContent = relation ? t("Zeigt „{relation}“.", { relation: relationText(relation, model) }) : t("Verweist auf die fehlende Beziehung „{ref}“.", { ref: connection.ref });
    actions.appendChild(modelButton(t("Verknüpfung lösen"), () => linkAction("unlink-relation", { view: connectionId }), "small relation-unlink"));
  } else {
    const choices = relationChoices(state.config, state.type, connection, model);
    if (choices.length) {
      text.textContent = t("Nicht mit einer Beziehung des Projektmodells verknüpft.");
      const select = document.createElement("select");
      select.className = "relation-choice";
      select.setAttribute("aria-label", t("Projektbeziehung"));
      for (const c of choices) select.appendChild(option(c.value, c.label));
      actions.append(select, modelButton(t("Verknüpfen"), () => linkAction("link-relation", { view: connectionId, relation: select.value }), "small relation-link"));
    } else {
      text.className = "muted";
      text.textContent = connection.sourceRef && connection.targetRef
        ? t("Keine passende Beziehung im Projektmodell (anlegen unter „Modell“).")
        : t("Beide Enden müssen mit Projektelementen verknüpft sein.");
    }
  }
  box.append(heading, text, actions);
}

/** The relations of the element whose other end this diagram shows, but that it does not draw: each with the marker
 *  "hier bewusst ausgeblendet" (spec: model-relations.md, ADR-0137), which keeps a complete diagram free of findings. */
function hiddenMarkers(section, model, elementId) {
  const connections = viewConnections(state.type, state.nodes, state.edges, state.interaction);
  const missing = relationsNotDrawn(state.config, state.type, model, elementId, shownElements(state.nodes, state.interaction), connections, state.nodes);
  if (!missing.length) return;
  const label = document.createElement("h5");
  label.textContent = t("Beziehungen, die hier nicht gezeichnet sind");
  const list = document.createElement("ul");
  list.className = "relation-hidden-list";
  for (const relation of missing) {
    const li = document.createElement("li");
    const box = document.createElement("input");
    box.type = "checkbox"; box.className = "relation-hidden"; box.dataset.relation = relation.id;
    box.checked = state.hiddenRelations.includes(relation.id);
    box.setAttribute("data-edit", "");
    box.addEventListener("change", () => linkAction("hidden-relations", { relation: relation.id, hidden: box.checked }));
    const text = document.createElement("label");
    text.append(box, " " + t("{relation} – hier bewusst ausgeblendet", { relation: relationText(relation, model) }));
    li.appendChild(text);
    list.appendChild(li);
  }
  section.append(label, list);
}

/** The completeness menu of the shown diagram: one checkbox per relation kind its type can declare (ADR-0137). */
function renderCompleteness() {
  const menu = $("completeness-menu");
  const kinds = typeConfig(state.config, state.type).completeness || [];
  menu.classList.toggle("hidden", !kinds.length || !state.projectId);
  $("completeness-summary").textContent = completenessText(state.complete);
  const box = $("completeness-kinds");
  box.innerHTML = "";
  for (const kind of kinds) {
    const label = document.createElement("label");
    label.className = "inline";
    const input = document.createElement("input");
    input.type = "checkbox"; input.value = kind; input.checked = state.complete.includes(kind);
    input.setAttribute("data-edit", "");
    input.addEventListener("change", async () => {
      const chosen = [...box.querySelectorAll("input:checked")].map((i) => i.value);
      const ok = await runOps([{ op: "set_completeness", args: { kinds: chosen }, label: t("Vollständigkeit: {kinds}", { kinds: chosen.map(relationKindName).join(", ") || t("offen") }) }]);
      if (ok === false) setStatus(t("Vollständigkeit nicht geändert: {error}", { error: insp.error.textContent }));
    });
    label.append(input, ` ${relationKindName(kind)}`);
    box.appendChild(label);
  }
}

/** POST /api/diagrams/{id}/link | unlink | promote | link-relation | unlink-relation | hidden-relations, then show the
 *  new state. */
async function linkAction(action, body) {
  setBusy(true);
  try {
    const headers = action === "promote" ? {} : ifMatch(state.revision); // promote writes the project first
    const detail = await api(`/api/diagrams/${encodeURIComponent(state.diagramId)}/${action}${focusQuery()}`, { method: "POST", body: JSON.stringify(body), headers });
    showDiagram(detail);
    showModeError("");
    await refreshDiagramListLabel();
  } catch (err) {
    let message = err.message || String(err);
    if (err.status === 412) {
      message = t("Inzwischen von {who} geändert – bitte erneut versuchen.", { who: changedBy(lastAuthor()) });
      try { showDiagram(await api(`/api/diagrams/${encodeURIComponent(state.diagramId)}${focusQuery()}`)); }
      catch (reloadErr) { message = t("Nicht gespeichert: das Diagramm wurde inzwischen geändert und konnte nicht neu geladen werden ({error}).", { error: reloadErr.message || reloadErr }); }
    }
    showModeError(message);
  } finally {
    setBusy(false);
    reopenInspector();
  }
}

/** Rename a linked element through the model (every diagram showing it follows); false after an error. */
async function renameThroughModel(ref, name) {
  try {
    const result = await api(`/api/projects/${encodeURIComponent(state.projectId)}/elements/${encodeURIComponent(ref)}`, { method: "PATCH", body: JSON.stringify({ name }) });
    showDiagram(await api(`/api/diagrams/${encodeURIComponent(state.diagramId)}${focusQuery()}`));
    advanceBase(); // the rename is this tab's own change
    setStatus(propagationText(result));
    await refreshDiagramListLabel();
    return true;
  } catch (err) {
    showModeError(err.message || String(err));
    return false;
  }
}

function reopenInspector() {
  if (!state.inspecting || !inspectedExists()) { if (state.inspecting) closeInspector(); return; }
  if (el.inspector.dataset.mode === "element") openElementInspector(state.inspecting, { keepError: true });
  else openInspector(state.inspecting, { keepError: true });
}

// ---------- interaction elements (sequence diagrams) ------------------------------------------

function showElementError(message) {
  insp.elementError.textContent = message || "";
  insp.elementError.classList.toggle("hidden", !message);
}

function fieldInput(field) {
  let input;
  if (field.type === "select" || field.type === "multi") {
    input = document.createElement("select");
    input.multiple = field.type === "multi";
    for (const [value, label] of field.options || []) {
      input.appendChild(option(value, label, field.type === "multi" ? field.value.includes(value) : value === field.value));
    }
  } else if (field.type === "checkbox") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = field.value;
  } else if (field.type === "info") {
    input = document.createElement("span");
    input.className = "info";
    input.textContent = field.value; // ids are free-form: text only, never innerHTML
    return input;
  } else if (field.type === "textarea") {
    input = document.createElement("textarea");
    input.rows = 3; input.maxLength = 2000;
    input.value = field.value;
  } else {
    input = document.createElement("input");
    input.value = field.value;
  }
  input.dataset.key = field.key;
  input.dataset.type = field.type;
  return input;
}

/** Open the inspector for an element of a sequence diagram (lifeline, message, fragment, operand, …). */
export function openElementInspector(id, { keepError = false } = {}) {
  const found = findElement(state.interaction, id);
  if (!found) return;
  state.inspecting = id;
  el.inspector.dataset.mode = "element";
  if (!keepError) showElementError("");
  insp.title.textContent = elementTitle(found.kind, found.element);
  insp.id.textContent = id;
  insp.elementFields.innerHTML = "";
  const ctx = { lifelines: state.interaction.lifelines, ...typeConfig(state.config, state.type) };
  for (const field of elementFields(found.kind, found.element, ctx)) {
    const label = document.createElement("label");
    label.textContent = field.label;
    if (field.type === "checkbox") label.className = "inline";
    label.appendChild(fieldInput(field));
    insp.elementFields.appendChild(label);
  }
  insp.elementSave.classList.toggle("hidden", !elementFields(found.kind, found.element, ctx).some((f) => f.type !== "info"));
  insp.elementDelete.textContent = t("{element} löschen", { element: ELEMENT_LABELS[found.kind] ? t(ELEMENT_LABELS[found.kind]) : t("Element") });
  insp.relation.innerHTML = "";
  insp.relation.classList.toggle("hidden", found.kind !== "message");
  if (found.kind === "message") renderRelationBox(insp.relation, id);
  el.inspector.classList.remove("hidden");
  insp.elementFields.querySelector("input, select")?.focus();
  renderModelSection(id);
  startEditing(id);
}

function elementValues() {
  const values = {};
  for (const input of insp.elementFields.querySelectorAll("[data-key]")) {
    const type = input.dataset.type;
    values[input.dataset.key] = type === "checkbox" ? input.checked : type === "multi" ? [...input.selectedOptions].map((o) => o.value) : input.value;
  }
  return values;
}

/** Whether the inspected id still exists in the shown diagram. */
function inspectedExists() {
  return el.inspector.dataset.mode === "element" ? Boolean(findElement(state.interaction, state.inspecting)) : Boolean(state.nodes[state.inspecting]);
}

export function closeInspector() {
  state.inspecting = null;
  base = null;
  hideConflict();
  el.inspector.classList.add("hidden");
  notifySelection(null);
}

// ---------- concurrent edits (spec: collaboration.md, ADR-0099) ------------------------------------------------

/** The values of a sequence element's fields as the shown state has them. */
function elementBaseValues(found) {
  const ctx = { lifelines: state.interaction.lifelines, ...typeConfig(state.config, state.type) };
  return Object.fromEntries(elementFields(found.kind, found.element, ctx).filter((f) => f.type !== "info").map((f) => [f.key, f.value]));
}

/** The shown element's field values in the shown state (null when it is gone). */
function shownValues(id) {
  if (el.inspector.dataset.mode === "element") {
    const found = findElement(state.interaction, id);
    return found ? elementBaseValues(found) : null;
  }
  return state.nodes[id] ? nodeValues(state.nodes[id]) : null;
}

/** What the form holds now, comparable with `shownValues`. */
function formValues() {
  if (el.inspector.dataset.mode === "element") {
    const values = elementValues();
    for (const key of ["name", "description"]) if (typeof values[key] === "string") values[key] = values[key].trim();
    return values;
  }
  const properties = {};
  for (const tr of insp.props.querySelectorAll("tr")) {
    const key = tr.querySelector(".prop-key").value.trim();
    if (key) properties[key] = tr.querySelector(".prop-value").value;
  }
  return {
    name: insp.name.value.trim(), kind: insp.kind.value, parent: insp.parent.value || null,
    description: insp.description.value.trim(), properties: canonicalProperties(properties),
  };
}

/** Put a value from the server into one field of the open form. */
function setField(key, value) {
  if (el.inspector.dataset.mode === "element") {
    const input = insp.elementFields.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!input) return;
    if (input.dataset.type === "checkbox") input.checked = Boolean(value);
    else if (input.dataset.type === "multi") for (const o of input.options) o.selected = (value || []).includes(o.value);
    else {
      // a value this form has no option for yet (a lifeline added meanwhile): add it, never empty the select
      if (input.tagName === "SELECT" && ![...input.options].some((o) => o.value === (value ?? ""))) input.appendChild(option(value ?? "", value ?? ""));
      input.value = value ?? "";
    }
    return;
  }
  if (key === "properties") {
    insp.props.innerHTML = "";
    fillPropRows(insp.props, Object.fromEntries(JSON.parse(value)), insp.kind.value);
    return;
  }
  const input = { name: insp.name, kind: insp.kind, parent: insp.parent, description: insp.description }[key];
  if (!input) return;
  if (input.tagName === "SELECT" && ![...input.options].some((o) => o.value === (value ?? ""))) input.appendChild(option(value ?? "", value ?? ""));
  input.value = value ?? "";
}

/** The label (form row) of a field, for a note next to it. */
function fieldRow(key) {
  if (el.inspector.dataset.mode === "element") return insp.elementFields.querySelector(`[data-key="${CSS.escape(key)}"]`)?.closest("label") ?? null;
  if (key === "properties") return insp.props.closest("fieldset");
  return { name: insp.name, kind: insp.kind, parent: insp.parent, description: insp.description }[key]?.closest("label") ?? null;
}

const FIELD_LABELS = { name: N_("Name"), kind: N_("Art"), parent: N_("Container"), description: N_("Notiz"), properties: N_("Eigenschaften") };

function fieldLabel(key) {
  if (el.inspector.dataset.mode !== "element") return FIELD_LABELS[key] ? t(FIELD_LABELS[key]) : key;
  const found = findElement(state.interaction, state.inspecting);
  return (found && elementFields(found.kind, found.element).find((f) => f.key === key)?.label) || key;
}

function clearRemoteNotes() { el.inspector.querySelectorAll(".remote-note").forEach((n) => n.remove()); }

function remoteNote(key, text) {
  const row = fieldRow(key);
  if (!row) return;
  row.querySelector(":scope > .remote-note")?.remove();
  const note = document.createElement("small");
  note.className = "remote-note";
  note.textContent = text; // names and values are free text: never innerHTML
  row.appendChild(note);
}

/** A freshly opened (or reloaded) form: its values are the shown state at the shown revision. */
function startEditing(id) {
  base = { id, mode: el.inspector.dataset.mode, revision: state.revision, values: shownValues(id) };
  clearRemoteNotes();
  hideConflict();
  notifySelection(id);
}

/** After a write of this tab: the form moves on to the new revision, since its own change is no conflict. */
function advanceBase() {
  if (!base || base.id !== state.inspecting) return;
  const theirs = shownValues(base.id);
  if (!theirs) return;
  const result = remoteChange(base.values, theirs, formValues());
  for (const [key, value] of Object.entries(result.updates)) setField(key, value);
  if (result.rebase) base = { ...base, revision: state.revision, values: theirs };
}

/** Only the fields the user changed in the form (against the base) are sent; untouched ones may hold a value the
 * form cannot show. */
function touchedArgs(args) {
  if (!base) return args;
  const mine = formValues();
  return Object.fromEntries(Object.entries(args).filter(([key]) => key === "id" || !same(mine[key], base.values[key])));
}

/** Who made the latest change of the shown state, as far as this tab knows. */
function lastAuthor() {
  if (state.lastChange && state.lastChange.revision === state.revision) return state.lastChange.author_name;
  return state.versions?.at?.(-1)?.author_name ?? null;
}

/** Someone else changed the diagram while the form is open: untouched fields follow, touched ones keep the typed
 * value with a note; without a conflict the form moves on to the new revision. */
function applyRemote(remote) {
  const theirs = shownValues(base.id);
  if (!theirs) return;
  const result = remoteChange(base.values, theirs, formValues());
  for (const [key, value] of Object.entries(result.updates)) setField(key, value);
  const who = changedBy(remote.author_name);
  for (const key of result.conflicts) remoteNote(key, t("Inzwischen von {who} geändert: „{value}“", { who, value: displayValue(key, theirs[key]) }));
  if (result.rebase) base = { ...base, revision: state.revision, values: theirs };
  if (el.inspector.dataset.mode !== "element" && !insp.edges.contains(document.activeElement)) renderInspectorEdges(base.id);
}

function hideConflict() {
  if (!insp) return;
  insp.conflict.innerHTML = "";
  insp.conflict.classList.add("hidden");
}

/** After a 412 on saving the form: show the user's values next to the current ones and let them choose. */
function showConflict(form) {
  const theirs = shownValues(base.id);
  const mine = formValues();
  const conflicts = fieldConflicts(base.values, theirs, mine);
  const result = remoteChange(base.values, theirs, mine);
  for (const [key, value] of Object.entries(result.updates)) setField(key, value); // untouched fields follow
  const rebaseAndSave = () => { base = { ...base, revision: state.revision, values: theirs }; hideConflict(); clearRemoteNotes(); form.requestSubmit(); };
  if (!conflicts.length) { rebaseAndSave(); return; } // only fields the user did not touch changed
  const box = insp.conflict;
  box.innerHTML = "";
  const text = document.createElement("p");
  text.textContent = t("Inzwischen von {who} geändert – Ihre Änderung wurde nicht gespeichert.", { who: changedBy(lastAuthor()) });
  const table = document.createElement("table");
  const head = table.insertRow();
  for (const label of [t("Feld"), t("Ihr Wert"), t("Aktuell")]) { const th = document.createElement("th"); th.textContent = label; head.appendChild(th); }
  for (const c of conflicts) {
    const row = table.insertRow();
    row.dataset.key = c.key;
    for (const value of [fieldLabel(c.key), displayValue(c.key, c.mine), displayValue(c.key, c.theirs)]) row.insertCell().textContent = value;
  }
  const actions = document.createElement("div");
  actions.className = "conflict-actions";
  const keep = modelButton(t("Meine Werte speichern"), rebaseAndSave, "small conflict-mine");
  const take = modelButton(t("Aktuelle Werte übernehmen"), () => { hideConflict(); reopenInspector(); }, "small conflict-theirs");
  actions.append(keep, take);
  box.append(text, table, actions);
  box.classList.remove("hidden");
}

/** Apply operations one after another through POST /ops (queued behind this tab's earlier ones); stop at the first
 * error and show it. Resolves to true / false, or null when nothing of it may show any more (the write was dropped,
 * or another diagram was opened meanwhile).
 * `refresh: "edges"` re-renders only the edge table afterwards, so unsaved node fields survive. The first operation
 * is sent with `If-Match`: `revision` (a form save: the base the user saw, captured on Save) advanced by this tab's
 * own writes since, else the shown revision; every next one with the revision the previous one produced. A 412
 * reloads the diagram; with `form` (`{element, id}`: a save of the node or element form) the user then chooses
 * between their values and the current ones (ADR-0099). */
// This tab's writes run one after another (an edge input also commits on blur), each bound to the diagram it was
// queued for (writequeue.js).
const writes = createWriteQueue(() => ({ diagramId: state.diagramId, revision: state.revision }));

export async function runOps(ops, options = {}) {
  const outcome = await writes.enqueue((revision) => runOpsNow(ops, { ...options, revision }), {
    diagramId: state.diagramId,
    revision: options.revision,
  });
  if (outcome.dropped) setStatus(t("Nicht gespeichert: inzwischen ist ein anderes Diagramm geöffnet."));
  return outcome.dropped ? null : outcome.value;
}

/** The base a save of the open form is sent against (captured on Save; never another element's, never the shown
 * revision): null when the form has none. */
function savedBase() {
  return base && base.id === state.inspecting && base.mode === el.inspector.dataset.mode ? { id: base.id, revision: base.revision } : null;
}

/** `revision` is the If-Match of the first operation (the queue resolved it when this run started). */
async function runOpsNow(ops, { refresh = "all", revision = null, form = null } = {}) {
  if (!ops.length) return true;
  const diagramId = state.diagramId; // the queue checked it is still shown; every request of this run goes there
  const inspected = state.inspecting;
  const here = () => state.diagramId === diagramId; // another diagram opened meanwhile: nothing of this run shows
  const show = (detail) => { if (here()) showDiagram(detail); };
  setBusy(true);
  let detail = null;
  let stale = false;
  let keepForm = false; // the reload after a 412 failed: leave the form as it is
  try {
    let expected = revision;
    for (const { op, args, label } of ops) {
      detail = await api(`/api/diagrams/${encodeURIComponent(diagramId)}/ops${focusQuery()}`, { method: "POST", body: JSON.stringify({ op, args, label }), headers: ifMatch(expected) });
      writes.advanced(diagramId, expected, detail.revision); // an own step: later saves based on `expected` follow it
      expected = detail.revision;
      show(detail);
    }
    if (!here()) return null;
    if (refresh === "edges" || state.inspecting !== inspected) advanceBase(); // this tab's own change is no conflict
    showInspectorError("");
    await refreshDiagramListLabel();
    return true;
  } catch (err) {
    if (!here()) { // never on the diagram shown now; the status line says what was lost
      setStatus(t("Nicht gespeichert im vorher geöffneten Diagramm: {reason}", { reason: err.status === 412 ? t("inzwischen geändert") : err.message || err }));
      return null;
    }
    if (err.status === 412) {
      try {
        show(await api(`/api/diagrams/${encodeURIComponent(diagramId)}${focusQuery()}`)); // the current state
      } catch (reloadErr) { // no conflict from old values: say what happened, keep the form
        keepForm = true;
        showModeError(t("Nicht gespeichert: das Diagramm wurde inzwischen geändert und konnte nicht neu geladen werden ({error}).", { error: reloadErr.message || reloadErr }));
        return false;
      }
      if (!here()) return null;
      stale = Boolean(form && state.inspecting === form.id && base?.id === form.id);
      if (form && !stale) setStatus(t("Nicht gespeichert: „{id}“ wurde inzwischen von {who} geändert.", { id: form.id, who: changedBy(lastAuthor()) }));
      else if (!stale) showModeError(t("Inzwischen von {who} geändert – die Ansicht ist aktualisiert, bitte erneut versuchen.", { who: changedBy(lastAuthor()) }));
      return false;
    }
    if (el.inspector.dataset.mode === "element") showElementError(err.message || String(err));
    else showInspectorError(err.message || String(err));
    if (detail) show(detail);
    return false;
  } finally {
    setBusy(false);
    if (keepForm || !here()) { /* nothing to show but the message */ }
    else if (stale && inspectedExists()) showConflict(form.element); // keeps what the user typed
    else if (state.inspecting && state.inspecting !== inspected && inspectedExists()) { /* another element's form: keep it */ }
    else if (state.inspecting && inspectedExists()) {
      // keepError: the message set in catch must survive the refresh
      if (el.inspector.dataset.mode === "element") openElementInspector(state.inspecting, { keepError: true });
      else if (refresh === "edges") renderInspectorEdges(state.inspecting);
      else openInspector(state.inspecting, { keepError: true });
    } else if (state.inspecting) closeInspector();
  }
}

export function initInspector() {
  insp = {
    title: $("insp-title"), id: $("insp-id"), form: $("insp-form"), name: $("insp-name"), kind: $("insp-kind"),
    parent: $("insp-parent"), description: $("insp-description"), props: $("insp-props").querySelector("tbody"), propAdd: $("insp-prop-add"),
    error: $("insp-error"), save: $("insp-save"), edges: $("insp-edges").querySelector("tbody"), noEdges: $("insp-no-edges"),
    cascadeLabel: $("insp-cascade-label"), cascade: $("insp-cascade"), del: $("insp-delete"), close: $("insp-close"),
    elementForm: $("insp-element-form"), elementFields: $("insp-element-fields"), elementError: $("insp-element-error"),
    elementSave: $("insp-element-save"), elementDelete: $("insp-element-delete"), model: $("insp-model"),
    relation: $("insp-relation"),
    conflict: $("insp-conflict"),
  };
  insp.elementForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const found = findElement(state.interaction, state.inspecting);
    if (!found) return;
    const change = elementUpdate(found.kind, found.element, elementValues());
    if (change) change.args = touchedArgs(change.args); // never a field the user did not change
    if (!change || Object.keys(change.args).length === 1) { showElementError(""); return; }
    if (found.element.ref && "name" in change.args) { // a linked lifeline is renamed through the model
      if (!(await renameThroughModel(found.element.ref, change.args.name))) return;
      delete change.args.name;
      if (Object.keys(change.args).length === 1) { reopenInspector(); return; }
    }
    const saved = savedBase(); // captured now, when Save is clicked
    if (!saved) return;
    await runOps([{ ...change, label: t("{id} bearbeitet", { id: state.inspecting }) }], { revision: saved.revision, form: { element: insp.elementForm, id: saved.id } });
  });
  insp.elementDelete.addEventListener("click", async () => {
    const found = findElement(state.interaction, state.inspecting);
    if (!found) return;
    if (!confirm(t("„{title}“ löschen? Abhängige Elemente (z. B. Nachrichten einer Lebenslinie, Aktivierungen) werden mitgelöscht.", { title: elementTitle(found.kind, found.element) }))) return;
    const ok = await runOps([{ ...elementRemove(found.kind, found.element), label: t("{id} gelöscht", { id: state.inspecting }) }]);
    if (ok) closeInspector();
  });
  // A new state may drop the inspected element; another diagram never shows it. Someone else's change keeps what the
  // user typed (ADR-0099).
  onDiagramShown((detail, { remote } = {}) => {
    renderCompleteness();
    if (state.inspecting && !inspectedExists()) closeInspector();
    else if (remote && state.inspecting && base?.id === state.inspecting) applyRemote(remote);
  });
  onDiagramSwitched(closeInspector);

  insp.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const node = state.nodes[state.inspecting];
    if (!node) return;
    const ops = [];
    const changedProps = propChanges(insp.props, node.properties);
    const name = insp.name.value.trim();
    const kind = insp.kind.value;
    const description = insp.description.value.trim();
    const throughModel = Boolean(node.ref && name && name !== node.name); // a linked node is renamed through the model
    if (throughModel && !(await renameThroughModel(node.ref, name))) return;
    const noteChanged = description !== (node.description || "");
    if ((name !== node.name && !throughModel) || kind !== node.kind || changedProps || noteChanged) {
      const args = { id: node.id };
      if (name && name !== node.name && !throughModel) args.name = name;
      if (kind !== node.kind) args.kind = kind;
      if (noteChanged) args.description = description;
      if (changedProps) Object.assign(args, changedProps);
      ops.push({ op: "update_node", args, label: t("{id} bearbeitet", { id: node.id }) });
    }
    const parent = insp.parent.value || null;
    if (parent !== (node.parent ?? null)) ops.push({ op: "move_node", args: { id: node.id, parent }, label: t("{id} verschoben", { id: node.id }) });
    if (!ops.length) { showInspectorError(""); if (throughModel) reopenInspector(); return; }
    // Several changes are one write (all or nothing), so a 412 never leaves half of a save behind (ADR-0099).
    const save = ops.length === 1 ? ops : [{ op: "apply_batch", args: { operations: ops.map(({ op, args }) => ({ op, args })) }, label: t("{id} bearbeitet", { id: node.id }) }];
    const saved = savedBase(); // captured now, when Save is clicked
    if (!saved) return;
    await runOps(save, { revision: saved.revision, form: { element: insp.form, id: saved.id } });
  });
  insp.propAdd.addEventListener("click", () => { const tr = propRow(); insp.props.appendChild(tr); tr.querySelector(".prop-key").focus(); });
  insp.del.addEventListener("click", async () => {
    const node = state.nodes[state.inspecting];
    if (!node) return;
    const cascade = isContainer(node, typeConfig(state.config, state.type).container_kinds) && insp.cascade.checked;
    if (!confirm(cascade ? t("„{name}“ löschen – samt Inhalt? Alle Kanten des Elements werden entfernt.", { name: node.name }) : t("„{name}“ löschen? Alle Kanten des Elements werden entfernt.", { name: node.name }))) return;
    const ok = await runOps([{ op: "remove_node", args: { id: node.id, cascade }, label: t("{id} gelöscht", { id: node.id }) }]);
    if (ok) closeInspector();
  });
  insp.close.addEventListener("click", closeInspector);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.inspecting) closeInspector(); });
  el.editFocus.addEventListener("click", () => { if (state.focus) openInspector(state.focus); });

  // Layout direction: a model change through POST /ops, so it is versioned.
  el.direction.addEventListener("change", async () => {
    const direction = el.direction.value;
    setStatus("");
    const ok = await runOps([{ op: "set_direction", args: { direction }, label: t("Richtung {direction}", { direction }) }]);
    if (ok === null) return; // dropped or another diagram meanwhile: runOps already said so
    if (!ok) setStatus(t("Richtung nicht geändert: {error}", { error: insp.error.textContent }));
    else renderView(state.view, { fit: true }); // the new orientation rarely fits the old view
  });
}
