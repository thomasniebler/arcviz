// The model panel (spec: docs/architecture/specs/shared-project-model.md): the open project's shared elements
// with their kinds, descriptions and usages; add, edit (a rename reaches every diagram), delete and sync. Since M25
// also the relations between elements and the relation × diagram matrix (spec: model-relations.md).
import { api } from "./api.js";
import { onRendered } from "./canvas.js";
import { onDiagramSwitched, showDiagram } from "./diagram.js";
import { $, el, option } from "./dom.js";
import { edgeSvgKeys } from "./findings.js";
import { nodeIdForSvgId } from "./model.js";
import { currentProject, onProjectShown, openInProject, refreshDiagramListLabel } from "./navigator.js";
import { allowedProperties, matrixLabel, matrixRows, propertyLabel, relationChanges, relationKindLabel, relationKindName, relationText, relationUsageText } from "./relations.js";
import { adoptionPreview, adoptionText, codeRefBody, codeRefLabel, codeRefsSummary, elementChanges, kindLabel, propagationText, usageText } from "./sharedmodel.js";
import { t } from "./i18n.js";
import { focusQuery, state } from "./state.js";

let model = null; // GET /api/projects/{pid}/model of the open project while the panel is open

function modelPath(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

function kinds() {
  return state.config?.project_model?.element_kinds || ["actor", "system", "subsystem", "component", "node", "data"];
}

function say(text) { $("model-status").textContent = text || ""; }

function kindSelect(selected, className = "model-kind") {
  const select = document.createElement("select");
  select.className = className;
  for (const k of kinds()) select.appendChild(option(k, kindLabel(k), k === selected));
  return select;
}

function parentSelect(element) {
  const select = document.createElement("select");
  select.className = "model-parent";
  select.title = t("Teil von");
  select.appendChild(option("", t("— eigenständig —"), !element.parent));
  for (const other of model.elements) if (other.id !== element.id) select.appendChild(option(other.id, other.name, other.id === element.parent));
  return select;
}

/** The shown diagram may have followed a rename or a delete: show its stored state and the tree's titles. */
async function refreshShown() {
  if (state.diagramId) showDiagram(await api(`/api/diagrams/${encodeURIComponent(state.diagramId)}${focusQuery()}`));
  await refreshDiagramListLabel();
}

function relationKinds() {
  return state.config?.project_model?.relation_kinds || ["communicates", "deployed_on", "data_flow"];
}

function elementSelect(selected, className, label) {
  const select = document.createElement("select");
  select.className = className;
  select.setAttribute("aria-label", label);
  for (const e of model.elements) select.appendChild(option(e.id, e.name, e.id === selected));
  return select;
}

function relationKindSelect(selected, className = "relation-kind") {
  const select = document.createElement("select");
  select.className = className;
  select.setAttribute("aria-label", t("Art der Beziehung"));
  for (const k of relationKinds()) select.appendChild(option(k, relationKindLabel(k), k === selected));
  return select;
}

/** One input per property the relation's kind allows: free text for protocol and port, a choice for the others. */
function propertyInputs(relation) {
  const box = document.createElement("span");
  box.className = "relation-props";
  const values = state.config?.project_model?.property_values || { direction: ["forward", "both", "none"], mode: ["sync", "async"] };
  const inputs = {};
  for (const key of allowedProperties(state.config, relation.kind)) {
    let input;
    if (values[key]) {
      input = document.createElement("select");
      input.appendChild(option("", `${propertyLabel(key)}: –`, !relation.properties?.[key]));
      for (const v of values[key]) input.appendChild(option(v, `${propertyLabel(key)}: ${v}`, relation.properties?.[key] === v));
    } else {
      input = document.createElement("input");
      input.placeholder = propertyLabel(key);
      input.value = relation.properties?.[key] || "";
      input.maxLength = 64;
    }
    input.className = "relation-prop";
    input.dataset.property = key;
    input.setAttribute("aria-label", propertyLabel(key));
    inputs[key] = input;
    box.appendChild(input);
  }
  return { box, inputs };
}

function renderRelations() {
  const list = $("relation-list");
  list.innerHTML = "";
  const relations = model.relations || [];
  $("relation-empty").classList.toggle("hidden", relations.length > 0);
  for (const relation of relations) {
    // Built with DOM APIs: names and descriptions are free-form and must never reach innerHTML.
    const li = document.createElement("li");
    li.className = "model-row relation-row";
    li.dataset.relationId = relation.id;
    li.title = relationText(relation, model);
    const source = elementSelect(relation.source, "relation-source", t("Von"));
    const kind = relationKindSelect(relation.kind);
    const target = elementSelect(relation.target, "relation-target", t("Zu"));
    const { box, inputs } = propertyInputs(relation);
    const description = document.createElement("input");
    description.className = "relation-description"; description.value = relation.description || "";
    description.placeholder = t("Beschreibung"); description.maxLength = 300; description.setAttribute("aria-label", t("Beschreibung"));
    const usage = document.createElement("span");
    usage.className = "muted model-usage relation-usage"; usage.textContent = relationUsageText(relation);
    const save = document.createElement("button");
    save.type = "button"; save.className = "small relation-save"; save.textContent = t("Speichern");
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "small danger relation-delete"; remove.textContent = t("Löschen");
    save.addEventListener("click", () => guarded(async () => {
      const properties = Object.fromEntries(Object.entries(inputs).map(([k, input]) => [k, input.value]));
      const body = relationChanges(relation, { kind: kind.value, source: source.value, target: target.value, description: description.value, properties });
      if (!body) return;
      if (body.kind) { // properties the new kind does not allow go with the change
        const allowed = allowedProperties(state.config, body.kind);
        const dropped = Object.keys(relation.properties || {}).filter((k) => !allowed.includes(k));
        if (dropped.length) body.remove_properties = [...new Set([...(body.remove_properties || []), ...dropped])];
        if (body.properties) for (const k of Object.keys(body.properties)) if (!allowed.includes(k)) delete body.properties[k];
      }
      const result = await api(modelPath("relations", relation.id), { method: "PATCH", body: JSON.stringify(body) });
      model = result.model;
      render();
      say(result.unlinked.length ? t("Gespeichert; in {n} Diagramm(en) passte die Verknüpfung nicht mehr und wurde gelöst.", { n: result.unlinked.length }) : t("Gespeichert."));
      if (result.unlinked.length) await refreshShown();
    }));
    remove.addEventListener("click", () => guarded(async () => {
      if (!confirm(t("Beziehung „{text}“ löschen? Die Diagramme behalten ihre Kanten und Nachrichten, nur die Verknüpfung entfällt.", { text: relationText(relation, model) }))) return;
      const result = await api(modelPath("relations", relation.id), { method: "DELETE" });
      model = result.model;
      render();
      say(result.unlinked.length ? t("Verknüpfung in {n} Diagramm(en) gelöst.", { n: result.unlinked.length }) : t("Gelöscht."));
      if (result.unlinked.length) await refreshShown();
    }));
    li.append(source, kind, target, box, description, save, remove, usage);
    list.appendChild(li);
  }
  const form = $("relation-new");
  form.classList.toggle("hidden", model.elements.length < 2);
  for (const [id, label] of [["relation-new-source", t("Von")], ["relation-new-target", t("Zu")]]) {
    const select = $(id);
    const keep = select.value;
    select.innerHTML = "";
    select.appendChild(option("", t("{label} …", { label }), !keep));
    for (const e of model.elements) select.appendChild(option(e.id, e.name, e.id === keep));
  }
  if ($("model-matrix").getAttribute("aria-pressed") === "true") renderMatrix();
}

// ---------- the relation × diagram matrix -------------------------------------------------------------------

const CELL_SIGNS = { linked: "●", shown: "○", hidden: "–", missing: "!" };
let marked = null; // {diagram, view} the matrix opened: marked on every render of that diagram until another one
let next = null;   // the mark for the diagram being opened (a switch clears the old one)

function renderMatrix() {
  const box = $("relation-matrix");
  box.innerHTML = "";
  const { columns, rows } = matrixRows(model);
  if (!rows.length || !columns.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = rows.length ? t("Keine Verteilungs- oder Sequenzdiagramme im Projekt.") : t("Noch keine Beziehungen.");
    box.appendChild(p);
    return;
  }
  const table = document.createElement("table");
  const head = document.createElement("tr");
  head.appendChild(document.createElement("th"));
  for (const d of columns) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = d.title;
    if (d.complete?.length) th.title = t("vollständig für: {kinds}", { kinds: d.complete.map(relationKindName).join(", ") });
    head.appendChild(th);
  }
  table.appendChild(head);
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.dataset.relationId = row.relation.id;
    const label = document.createElement("th");
    label.scope = "row";
    label.className = "relation-label";
    label.textContent = row.label;
    tr.appendChild(label);
    row.cells.forEach((cell, i) => {
      const td = document.createElement("td");
      td.className = cell.state ? `cell-${cell.state}` : "cell-none";
      td.dataset.diagram = cell.diagram;
      td.title = cell.state ? matrixLabel(cell.state) : t("nicht gezeigt");
      if (cell.view) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = CELL_SIGNS[cell.state];
        b.title = t("{state} – in „{title}“ öffnen und markieren", { state: matrixLabel(cell.state), title: columns[i].title });
        b.addEventListener("click", () => guarded(() => showConnection(cell.diagram, cell.view)));
        td.appendChild(b);
      } else {
        td.textContent = CELL_SIGNS[cell.state] || "";
      }
      tr.appendChild(td);
    });
    table.appendChild(tr);
  }
  const legend = document.createElement("p");
  legend.className = "muted";
  legend.textContent = Object.keys(CELL_SIGNS).map((k) => `${CELL_SIGNS[k]} ${matrixLabel(k)}`).join(" · ");
  box.append(table, legend);
}

/** Open the diagram and mark the connection (a deployment edge or a sequence message) once it is rendered. */
async function showConnection(diagramId, view) {
  $("model-dialog").close();
  if (diagramId !== state.diagramId) {
    next = { diagram: diagramId, view };
    await openInProject(diagramId);
    return;
  }
  marked = { diagram: diagramId, view };
  markConnection();
}

function markConnection() {
  el.viewport.querySelectorAll(".relation-hit").forEach((n) => n.classList.remove("relation-hit"));
  if (!marked || marked.diagram !== state.diagramId || state.compare || state.previewVersion != null) return;
  const { view } = marked;
  el.viewport.querySelectorAll("g[data-id]").forEach((g) => { if (g.dataset.id === view) g.classList.add("relation-hit"); });
  // a deployed_on shown by nesting: the nested node itself
  el.viewport.querySelectorAll("g.node[id], g.cluster[id], g.icon-shape[id]").forEach((g) => {
    if (nodeIdForSvgId(g.id, state.mermaidIds) === view) g.classList.add("relation-hit");
  });
  const keys = edgeSvgKeys([view], state.edges, state.mermaidIds);
  el.viewport.querySelectorAll("path[id], path[data-id]").forEach((p) => {
    const id = p.getAttribute("data-id") || p.id;
    if (keys.some((k) => id === k || id.endsWith(`-${k}`))) p.classList.add("relation-hit");
  });
}

function render() {
  renderElements();
  renderRelations();
}

function renderElements() {
  const list = $("model-list");
  list.innerHTML = "";
  $("model-empty").classList.toggle("hidden", model.elements.length > 0);
  for (const element of model.elements) {
    // Built with DOM APIs: names and descriptions are free-form and must never reach innerHTML.
    const li = document.createElement("li");
    li.className = "model-row";
    li.dataset.elementId = element.id;
    const name = document.createElement("input");
    name.className = "model-name"; name.value = element.name; name.setAttribute("aria-label", t("Name"));
    const description = document.createElement("input");
    description.className = "model-description"; description.value = element.description || "";
    description.placeholder = t("Beschreibung"); description.setAttribute("aria-label", t("Beschreibung"));
    const usage = document.createElement("span");
    usage.className = "muted model-usage"; usage.textContent = usageText(element);
    const save = document.createElement("button");
    save.type = "button"; save.className = "small model-save"; save.textContent = t("Speichern");
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "small danger model-delete"; remove.textContent = t("Löschen");
    const kind = kindSelect(element.kind), parent = parentSelect(element);
    save.addEventListener("click", () => guarded(async () => {
      const body = elementChanges(element, { name: name.value, description: description.value, kind: kind.value, parent: parent.value });
      if (!body) return;
      const result = await api(modelPath("elements", element.id), { method: "PATCH", body: JSON.stringify(body) });
      model = result.model;
      render();
      say(propagationText(result) || t("Gespeichert."));
      if (body.name) await refreshShown();
    }));
    remove.addEventListener("click", () => guarded(async () => {
      if (!confirm(t("Projektelement „{name}“ löschen? Die Diagramme behalten ihre Elemente, nur die Verknüpfung entfällt.", { name: element.name }))) return;
      const result = await api(modelPath("elements", element.id), { method: "DELETE" });
      model = result.model;
      render();
      say(result.unlinked.length ? t("Verknüpfung in {n} Diagramm(en) gelöst.", { n: result.unlinked.length }) : t("Gelöscht."));
      if (result.unlinked.length) await refreshShown();
    }));
    li.append(name, kind, parent, description, save, remove, usage, codeSection(element));
    list.appendChild(li);
  }
}

/** The element's code links (spec: agent-access.md): a collapsed list with remove buttons and a small add form. */
function codeSection(element) {
  const details = document.createElement("details");
  details.className = "model-code";
  const summary = document.createElement("summary");
  summary.textContent = codeRefsSummary(element);
  const list = document.createElement("ul");
  list.className = "model-code-list";
  for (const ref of element.code_refs || []) {
    const item = document.createElement("li");
    const text = document.createElement("code");
    text.textContent = codeRefLabel(ref);
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "small danger code-ref-delete"; remove.textContent = t("Entfernen");
    remove.addEventListener("click", () => guarded(async () => {
      const query = new URLSearchParams({ path: ref.path, repo: ref.repo || "", symbol: ref.symbol || "" });
      await api(`${modelPath("elements", element.id, "code-refs")}?${query}`, { method: "DELETE" });
      await reload(t("Codeverknüpfung zu „{name}“ entfernt.", { name: element.name }));
    }));
    item.append(text, " ", remove);
    list.appendChild(item);
  }
  const form = document.createElement("form");
  form.className = "model-code-new";
  const fields = {};
  for (const [key, label, width] of [["path", t("Pfad im Repository"), 18], ["repo", t("Repo"), 8], ["symbol", t("Symbol"), 10], ["note", t("Hinweis"), 12]]) {
    const input = document.createElement("input");
    input.placeholder = label; input.setAttribute("aria-label", label); input.size = width; input.autocomplete = "off";
    input.className = `code-ref-${key}`;
    fields[key] = input;
    form.appendChild(input);
  }
  const add = document.createElement("button");
  add.type = "submit"; add.className = "small code-ref-add"; add.textContent = t("Verknüpfen");
  form.appendChild(add);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(async () => {
      const body = codeRefBody(Object.fromEntries(Object.entries(fields).map(([k, input]) => [k, input.value])));
      if (!body) return;
      await api(modelPath("elements", element.id, "code-refs"), { method: "POST", body: JSON.stringify(body) });
      await reload(t("„{name}“ mit {path} verknüpft.", { name: element.name, path: body.path }));
    });
  });
  details.append(summary, list, form);
  details.open = openCode.has(element.id);
  details.addEventListener("toggle", () => { if (details.open) openCode.add(element.id); else openCode.delete(element.id); });
  return details;
}

const openCode = new Set(); // elements whose code section the user opened, kept across re-renders

async function reload(message) {
  model = await api(modelPath("model"));
  render();
  say(message);
}

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); }
}

async function open() {
  if (!currentProject()) return;
  model = await api(modelPath("model"));
  $("model-heading").textContent = t("Projektmodell");
  const kind = $("model-new-kind");
  kind.innerHTML = "";
  for (const k of kinds()) kind.appendChild(option(k, kindLabel(k), k === "component"));
  const kindNew = $("relation-new-kind");
  kindNew.innerHTML = "";
  for (const k of relationKinds()) kindNew.appendChild(option(k, relationKindLabel(k), k === "communicates"));
  say("");
  render();
  $("model-dialog").showModal();
}

export function initModelPanel() {
  $("btn-model").addEventListener("click", () => guarded(open));
  $("model-close").addEventListener("click", () => $("model-dialog").close());
  $("model-new").addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(async () => {
      const body = { name: $("model-new-name").value.trim(), kind: $("model-new-kind").value, description: $("model-new-description").value.trim() };
      if (!body.name) return;
      const result = await api(modelPath("elements"), { method: "POST", body: JSON.stringify(body) });
      model = result.model;
      $("model-new-name").value = ""; $("model-new-description").value = "";
      render();
      say(t("„{name}“ angelegt.", { name: result.element.name }));
    });
  });
  $("model-adopt").addEventListener("click", () => guarded(async () => {
    const plan = await api(modelPath("adoption"));
    if (!plan.diagrams.length) { say(t("Alle Bausteine der Diagramme sind schon Projektelemente.")); return; }
    if (!confirm(adoptionPreview(plan))) return;
    const result = await api(modelPath("adoption"), { method: "POST" });
    model = result.model;
    render();
    say(adoptionText(result));
    await refreshShown();
  }));
  $("model-sync").addEventListener("click", () => guarded(async () => {
    const result = await api(modelPath("model", "sync"), { method: "POST" });
    model = result.model;
    render();
    say(result.changed.length ? t("{n} Diagramm(e) abgeglichen.", { n: result.changed.length }) : t("Alle Diagramme stimmen mit dem Modell überein."));
    if (result.changed.length) await refreshShown();
  }));
  $("relation-new").addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(async () => {
      const body = {
        kind: $("relation-new-kind").value, source: $("relation-new-source").value, target: $("relation-new-target").value,
        description: $("relation-new-description").value.trim(),
      };
      if (!body.source || !body.target) { say(t("Bitte beide Elemente wählen.")); return; }
      const result = await api(modelPath("relations"), { method: "POST", body: JSON.stringify(body) });
      model = result.model;
      $("relation-new-description").value = "";
      render();
      say(t("Beziehung „{text}“ angelegt.", { text: relationText(result.relation, model) }));
    });
  });
  $("model-matrix").addEventListener("click", () => {
    const on = $("model-matrix").getAttribute("aria-pressed") !== "true";
    $("model-matrix").setAttribute("aria-pressed", String(on));
    $("relation-matrix").classList.toggle("hidden", !on);
    if (on && model) renderMatrix();
  });
  onRendered(markConnection);
  onDiagramSwitched(() => { marked = next; next = null; });
  onProjectShown(() => { if ($("model-dialog").open) $("model-dialog").close(); });
}
