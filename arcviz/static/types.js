// Diagram types in the UI (spec: docs/architecture/specs/diagram-types.md): the per-type configuration from
// /api/config and the renderer-neutral view of a render payload. Pure module: no DOM access.

import { N_, t } from "./i18n.js";

/** German names of the diagram types (the API uses the English type names); `t()` them where they are shown. */
export const TYPE_LABELS = {
  deployment: N_("Verteilungsdiagramm"), activity: N_("Aktivitätsdiagramm"), sequence: N_("Sequenzdiagramm"), state_machine: N_("Zustandsdiagramm"),
  class: N_("Klassendiagramm"), use_case: N_("Anwendungsfalldiagramm"), component: N_("Komponentendiagramm"),
};

const NO_FEATURES = { focus: false, direction: false, compare: false, plantuml: false };

/** Configuration of a diagram type from /api/config; falls back to the top-level (deployment) kinds. */
export function typeConfig(config, type) {
  const t = config?.diagram_types?.[type || "deployment"];
  if (t) return t;
  return {
    renderer: "mermaid", node_kinds: config?.node_kinds || [], container_kinds: config?.container_kinds || [],
    edge_kinds: config?.edge_kinds || [], operations: [], templates: [],
    features: type && type !== "deployment" ? NO_FEATURES : { focus: true, direction: true, compare: true, plantuml: true },
  };
}

/** The names of the typed fields (ADR-0177) of a type's nodes or edges, over all kinds: they are stored as properties
 *  with reserved keys and must be sent as `fields`, never as free properties. */
export function typedFieldNames(config, type, edges = false) {
  const table = typeConfig(config, type)[edges ? "edge_fields" : "node_fields"] || {};
  return [...new Set(Object.values(table).flat().map((f) => f.name))];
}

/** The typed fields (ADR-0177) of one node or edge kind: `[{name, list, flag, max_items, max_length}]`. */
export function fieldSpecs(config, type, kind, edges = false) {
  return (typeConfig(config, type)[edges ? "edge_fields" : "node_fields"] || {})[kind] || [];
}

/** The value of a typed field as the server takes it: a list for a list field (one line per entry, empty lines
 *  dropped), a boolean for a flag (the text "true"), else the text. `fields` maps names to the texts of the form;
 *  `specs` are the fields of the type (`typedSpecs`). */
export function typedValues(fields, specs) {
  const out = {};
  for (const [name, text] of Object.entries(fields || {})) {
    const spec = specs.find((s) => s.name === name);
    if (spec?.list) out[name] = text.split("\n").map((l) => l.trim()).filter(Boolean);
    else if (spec?.flag) out[name] = text.trim().toLowerCase() === "true";
    else out[name] = text;
  }
  return out;
}

/** Every typed field of a type's nodes or edges, once per name (the first kind that has it). */
export function typedSpecs(config, type, edges = false) {
  const seen = new Map();
  for (const specs of Object.values(typeConfig(config, type)[edges ? "edge_fields" : "node_fields"] || {})) {
    for (const spec of specs) if (!seen.has(spec.name)) seen.set(spec.name, spec);
  }
  return [...seen.values()];
}

/** `{renderer, source}` of a render payload (`renderer` plus the source under `mermaid` or `svg`), or null. */
export function viewOf(payload) {
  if (!payload) return null;
  const renderer = payload.renderer || (payload.mermaid != null ? "mermaid" : null);
  if (!renderer || payload[renderer] == null) return null;
  return { renderer, source: payload[renderer] };
}

/** Tooltip of a control that the diagram type does not support. */
export function unsupportedTitle(type) {
  const label = TYPE_LABELS[type] || type;
  return t("Für {type} noch nicht verfügbar", { type: t(label) });
}

/** Template choices of a type for the new-diagram dialog: `[value, label]`, labels from `labels` or the name. */
export function templateChoices(config, type, labels = {}) {
  return (typeConfig(config, type).templates || ["empty"]).map((t) => [t, labels[t] || t]);
}
