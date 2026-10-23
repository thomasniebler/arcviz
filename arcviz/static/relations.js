// Relations of the shared project model in the UI (spec: docs/architecture/specs/model-relations.md): pure helpers for
// the model panel, the matrix, the inspector and the completeness menu. No DOM; tested in tests/js/relations.test.mjs.

import { N_, getLanguage, t } from "./i18n.js";

/** German labels of the relation kinds (/api/config `project_model.relation_kinds`). */
export const RELATION_KIND_LABELS = {
  communicates: N_("kommuniziert mit"),
  deployed_on: N_("läuft auf"),
  data_flow: N_("Datenfluss zu"),
};

/** Short German names of the kinds, for menus and column heads. */
export const RELATION_KIND_NAMES = {
  communicates: N_("Kommunikation"),
  deployed_on: N_("Verteilung"),
  data_flow: N_("Datenfluss"),
};

export function relationKindLabel(kind) {
  return RELATION_KIND_LABELS[kind] ? t(RELATION_KIND_LABELS[kind]) : kind;
}

export function relationKindName(kind) {
  return RELATION_KIND_NAMES[kind] ? t(RELATION_KIND_NAMES[kind]) : kind;
}

function nameOf(model, id) {
  return (model?.elements || []).find((e) => e.id === id)?.name || id;
}

/** "Web kommuniziert mit Orders" (element names from the model, ids when unknown). */
export function relationText(relation, model) {
  return `${nameOf(model, relation.source)} ${relationKindLabel(relation.kind)} ${nameOf(model, relation.target)}`;
}

/** The connections of a shown diagram as the model sees them (mirrors `project_model.view_connections`):
 *  `{id, kind, sourceRef, targetRef, ref}` for deployment edges and sequence messages; none for other types. */
export function viewConnections(type, nodes, edges, interaction) {
  if (interaction) {
    const refs = Object.fromEntries((interaction.lifelines || []).map((l) => [l.id, l.ref ?? null]));
    const out = [];
    const walk = (fragments) => {
      for (const f of fragments || []) {
        if (f.element === "message") out.push({ id: f.id, kind: "message", sourceRef: refs[f.sender] ?? null, targetRef: refs[f.receiver] ?? null, ref: f.ref ?? null });
        for (const op of f.operands || []) walk(op.fragments);
      }
    };
    walk(interaction.fragments);
    return out;
  }
  if (type !== "deployment" && type !== "component") return [];
  return (edges || []).map((e) => ({
    id: e.id, kind: e.kind, sourceRef: nodes?.[e.source]?.ref ?? null, targetRef: nodes?.[e.target]?.ref ?? null, ref: e.ref ?? null,
  }));
}

/** Whether a connection may show the relation: its kind can (config `diagram_types[type].relations`) and its ends stand
 *  for the relation's ends (either orientation, except data_flow). */
export function fits(config, type, connection, relation) {
  const kinds = config?.diagram_types?.[type]?.relations?.[connection.kind] || [];
  if (!kinds.includes(relation.kind)) return false;
  const { sourceRef: a, targetRef: b } = connection;
  if (!a || !b) return false;
  if (a === relation.source && b === relation.target) return true;
  return relation.kind !== "data_flow" && a === relation.target && b === relation.source;
}

/** The relations a connection could be linked to, sorted by text: `{value, label}` options. */
export function relationChoices(config, type, connection, model) {
  return (model?.relations || [])
    .filter((r) => fits(config, type, connection, r))
    .map((r) => ({ value: r.id, label: relationText(r, model) }))
    .sort((a, b) => a.label.localeCompare(b.label, getLanguage()));
}

/** The element ids the shown diagram's nodes or lifelines stand for. */
export function shownElements(nodes, interaction) {
  const views = interaction ? interaction.lifelines || [] : Object.values(nodes || {});
  return new Set(views.map((v) => v.ref).filter(Boolean));
}

/** Relations of `elementId` whose other end the diagram shows too, but that no connection of it shows (linked or
 *  fitting): the candidates for "hier bewusst ausgeblendet". Kinds the diagram cannot show are left out. */
export function relationsNotDrawn(config, type, model, elementId, shown, connections, nodes = {}) {
  const showable = config?.diagram_types?.[type]?.completeness || [];
  const kinds = elementKinds(model);
  return (model?.relations || []).filter((r) => {
    if (!showable.includes(r.kind) || (r.source !== elementId && r.target !== elementId)) return false;
    const other = r.source === elementId ? r.target : r.source;
    if (!shown.has(other)) return false;
    return !showsRelation(config, type, nodes, connections, r, kinds);
  });
}

/** Element id -> kind, from a model or GET …/elements answer. */
export function elementKinds(model) {
  return new Map((model?.elements || []).map((e) => [e.id, e.kind]));
}

/** Nesting in a deployment diagram (mirrors `project_model.nested`): for every node standing for a known element,
 *  `{view, element, holder, host}`: the element of the nearest linked ancestor, and of the nearest ancestor standing
 *  for a `node` element through any other containers. */
export function nestedNodes(type, nodes, kinds) {
  if (type !== "deployment") return [];
  // a Map: element ids such as "constructor", or the missing ref of an unlinked node, never match by accident
  const known = kinds instanceof Map ? kinds : new Map(Object.entries(kinds || {}));
  const linkedRef = (ref) => ref != null && known.has(ref);
  const out = [];
  for (const node of Object.values(nodes || {})) {
    if (!linkedRef(node.ref)) continue;
    const linked = [];
    const seen = new Set([node.id]);
    for (let parent = node.parent; parent != null && Object.hasOwn(nodes, parent) && !seen.has(parent); parent = nodes[parent].parent) {
      seen.add(parent);
      const ref = nodes[parent].ref;
      if (linkedRef(ref) && ref !== node.ref) linked.push(ref);
    }
    if (linked.length) out.push({ view: node.id, element: node.ref, holder: linked[0], host: linked.find((r) => known.get(r) === "node") ?? null });
  }
  return out;
}

/** Whether a diagram shows a relation: the one definition of "shown" (mirrors `project_model.shows`; both are checked
 *  against tests/fixtures/relations_shown.json): a linked connection, an unlinked one that fits, or for deployed_on
 *  the source's node nested under the target as its nearest node. */
export function showsRelation(config, type, nodes, connections, relation, kinds) {
  if (connections.some((c) => c.ref === relation.id || (!c.ref && fits(config, type, c, relation)))) return true;
  return relation.kind === "deployed_on" && nestedNodes(type, nodes, kinds).some((n) => n.element === relation.source && n.host === relation.target);
}

/** The completeness menu's summary: "Vollständig: Kommunikation, Verteilung" or "Offen (nichts erklärt)". */
export function completenessText(complete) {
  if (!complete?.length) return t("Vollständigkeit: offen");
  return t("Vollständig: {kinds}", { kinds: complete.map(relationKindName).join(", ") });
}

const enc = encodeURIComponent;

/** The request the project review panel sends for a resolution of a correspondence finding (spec: model-relations.md,
 *  *Resolution flow*): `{method, path, body}`; null for `propose`, which is a propose-mode chat turn instead. */
export function resolutionRequest(projectId, resolution) {
  const project = `/api/projects/${enc(projectId)}`;
  switch (resolution?.action) {
    case "hide":
      return { method: "POST", path: `/api/diagrams/${enc(resolution.diagram)}/hidden-relations`, body: { relation: resolution.relation, hidden: true } };
    case "link_relation":
      return { method: "POST", path: `/api/diagrams/${enc(resolution.diagram)}/link-relation`, body: { view: resolution.view, relation: resolution.relation } };
    case "update_relation":
      return { method: "PATCH", path: `${project}/relations/${enc(resolution.relation)}`, body: resolution.args || {} };
    case "remove_relation":
      return { method: "DELETE", path: `${project}/relations/${enc(resolution.relation)}`, body: null };
    case "create_relation":
      return { method: "POST", path: `${project}/relations`, body: resolution.args || {} };
    case "move_element":
      return { method: "PATCH", path: `${project}/elements/${enc(resolution.model_element)}`, body: resolution.args || {} };
    case "sync":
      return { method: "POST", path: `${project}/model/sync`, body: null };
    default:
      return null;
  }
}

/** Whether a resolution needs a confirmation first (it removes something from the shared model). */
export function needsConfirmation(resolution) {
  return resolution?.action === "remove_relation";
}

// ---------- the model panel and the matrix (#208) -----------------------------------------------------------------

/** German labels of the matrix cell states. */
export const MATRIX_LABELS = {
  linked: N_("verknüpft"),
  shown: N_("gezeigt (nicht verknüpft)"),
  hidden: N_("ausgeblendet"),
  missing: N_("fehlt (vollständige Sicht)"),
};

/** The matrix cell state as text in the current language. */
export function matrixLabel(state) {
  return MATRIX_LABELS[state] ? t(MATRIX_LABELS[state]) : state;
}

const DEFAULT_PROPERTIES = { communicates: ["protocol", "port", "direction", "mode"], deployed_on: [], data_flow: ["protocol", "port", "mode"] };

/** The properties a relation of `kind` may carry (/api/config `project_model.relation_properties`). */
export function allowedProperties(config, kind) {
  return config?.project_model?.relation_properties?.[kind] ?? DEFAULT_PROPERTIES[kind] ?? [];
}

function usedIn(model, elementId, diagramId) {
  return ((model?.elements || []).find((e) => e.id === elementId)?.usages || []).some((u) => u.diagram === diagramId);
}

/** The state of a relation in a diagram (`model` of GET /api/projects/{pid}/model): `{state, view}` with state
 *  linked (a connection shows it), shown (an unlinked connection fits), hidden (marked in the diagram), missing (the
 *  diagram is complete for the kind and shows both ends) or ""; `view` is the connection to open. */
export function matrixCell(relation, diagram, model) {
  const linked = (relation.usages || []).find((u) => u.diagram === diagram.id);
  if (linked) return { state: "linked", view: linked.view };
  const shown = (relation.shown || []).find((u) => u.diagram === diagram.id);
  if (shown) return { state: "shown", view: shown.view };
  if ((diagram.hidden || []).includes(relation.id)) return { state: "hidden", view: null };
  const complete = (diagram.complete || []).includes(relation.kind);
  if (complete && usedIn(model, relation.source, diagram.id) && usedIn(model, relation.target, diagram.id)) return { state: "missing", view: null };
  return { state: "", view: null };
}

/** The diagram types that can show relations (the server's model.RELATION_DIAGRAM_TYPES). */
export const RELATION_TYPES = ["deployment", "sequence", "component"];

/** The relation × diagram matrix: columns are the diagrams of types that show relations. */
export function matrixRows(model) {
  const columns = (model?.diagrams || []).filter((d) => RELATION_TYPES.includes(d.type));
  const rows = (model?.relations || []).map((relation) => ({
    relation,
    label: relationText(relation, model),
    cells: columns.map((diagram) => ({ diagram: diagram.id, ...matrixCell(relation, diagram, model) })),
  }));
  return { columns, rows };
}

/** The PATCH body of a relation edit relative to `relation`, or null when nothing changed. Cleared property values
 *  are removed (`remove_properties`). */
export function relationChanges(relation, values) {
  const body = {};
  for (const key of ["kind", "source", "target"]) if (values[key] && values[key] !== relation[key]) body[key] = values[key];
  const description = (values.description ?? "").trim();
  if (description !== (relation.description || "")) body.description = description;
  const set = {}, removed = [];
  for (const [key, raw] of Object.entries(values.properties || {})) {
    const value = String(raw ?? "").trim();
    if (!value && key in (relation.properties || {})) removed.push(key);
    else if (value && value !== relation.properties?.[key]) set[key] = value;
  }
  if (Object.keys(set).length) body.properties = set;
  if (removed.length) body.remove_properties = removed;
  return Object.keys(body).length ? body : null;
}

/** Where a relation is linked, as text: "Plattform, Checkout" or "in keinem Diagramm verknüpft". */
export function relationUsageText(relation) {
  const titles = [...new Set((relation?.usages || []).map((u) => u.title))];
  return titles.length ? titles.join(", ") : t("in keinem Diagramm verknüpft");
}

/** German labels of the relation properties. */
export const PROPERTY_LABELS = { protocol: N_("Protokoll"), port: N_("Port"), direction: N_("Richtung"), mode: N_("Aufruf") };

/** The label of a relation property in the current language (the key itself when unknown). */
export function propertyLabel(key) {
  return PROPERTY_LABELS[key] ? t(PROPERTY_LABELS[key]) : key;
}
