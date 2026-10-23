// Elements of a sequence diagram's interaction in the inspector (spec: docs/architecture/specs/sequence-diagrams.md):
// lookup by id, the form fields per element kind, and the operation an edit or a delete becomes.
// Pure module: no DOM access, unit-tested with node --test.
import { N_, t } from "./i18n.js";

export const ELEMENT_LABELS = {
  lifeline: N_("Lebenslinie"),
  message: N_("Nachricht"),
  combined_fragment: N_("Kombiniertes Fragment"),
  interaction_operand: N_("Operand"),
  interaction_use: N_("Interaktionsreferenz (ref)"),
  execution_specification: N_("Aktivierung"),
  destruction_occurrence_specification: N_("Zerstörung"),
};

/** Depth-first walk over the fragment tree in time order: `fn(fragment)`, operands via `onOperand(op, owner)`. */
function walk(fragments, fn, onOperand) {
  for (const f of fragments || []) {
    fn(f);
    if (f.element === "combined_fragment") for (const op of f.operands || []) { onOperand?.(op, f); walk(op.fragments, fn, onOperand); }
  }
}

/** `{kind, element}` for an id of the interaction, or null. */
export function findElement(interaction, id) {
  if (!interaction || id == null) return null;
  const lifeline = (interaction.lifelines || []).find((l) => l.id === id);
  if (lifeline) return { kind: "lifeline", element: lifeline };
  let found = null;
  walk(interaction.fragments, (f) => { if (!found && f.id === id) found = { kind: f.element, element: f }; },
    (op) => { if (!found && op.id === id) found = { kind: "interaction_operand", element: op }; });
  if (found) return found;
  const execution = (interaction.executions || []).find((e) => e.id === id);
  return execution ? { kind: "execution_specification", element: execution } : null;
}

/** Number of messages (anywhere in the fragment tree). */
export function messageCount(interaction) {
  let count = 0;
  walk(interaction?.fragments, (f) => { if (f.element === "message") count++; });
  return count;
}

/** Heading of the inspector: the German kind and a short description. */
export function elementTitle(kind, element) {
  const label = ELEMENT_LABELS[kind] ? t(ELEMENT_LABELS[kind]) : kind;
  if (kind === "lifeline") return `${label} ${element.name}`;
  if (kind === "message") return `${label} ${element.name}`;
  if (kind === "combined_fragment") return `${label} ${element.operator}`;
  if (kind === "interaction_operand") return `${label}${element.guard ? ` [${element.guard}]` : ""}`;
  if (kind === "interaction_use") return `${label} ${element.refers_to}`;
  if (kind === "execution_specification") return t("{label} auf {lifeline}", { label, lifeline: element.lifeline });
  return t("{label} von {lifeline}", { label, lifeline: element.lifeline });
}

/**
 * Form fields of an element: `{key, label, type: "text" | "textarea" | "select" | "checkbox" | "multi" | "info", value,
 * options}`.
 * `ctx` holds `lifelines` (from the interaction) and the enumerations of /api/config (`message_sorts`,
 * `interaction_operators`).
 */
export function elementFields(kind, element, ctx = {}) {
  const lifelines = (ctx.lifelines || []).map((l) => [l.id, l.name]);
  switch (kind) {
    case "lifeline":
      return [
        { key: "name", label: t("Name"), type: "text", value: element.name },
        { key: "type", label: t("Typ"), type: "text", value: element.type || "" },
        { key: "actor", label: t("Akteur"), type: "checkbox", value: Boolean(element.actor) },
        { key: "description", label: t("Notiz"), type: "textarea", value: element.description || "" },
      ];
    case "message":
      return [
        { key: "name", label: t("Name"), type: "text", value: element.name },
        { key: "sort", label: t("Art"), type: "select", value: element.sort, options: (ctx.message_sorts || [element.sort]).map((s) => [s, s]) },
        { key: "sender", label: t("Sender"), type: "select", value: element.sender || "", options: [["", t("— (gefundene Nachricht)")], ...lifelines] },
        { key: "receiver", label: t("Empfänger"), type: "select", value: element.receiver || "", options: [["", t("— (verlorene Nachricht)")], ...lifelines] },
      ];
    case "combined_fragment": {
      const fields = [
        { key: "operator", label: t("Operator"), type: "select", value: element.operator, options: (ctx.interaction_operators || [element.operator]).map((o) => [o, o]) },
        { key: "covered", label: t("Lebenslinien"), type: "multi", value: [...(element.covered || [])], options: lifelines },
      ];
      if (["ignore", "consider"].includes(element.operator)) fields.push({ key: "messages", label: t("Nachrichten (kommagetrennt)"), type: "text", value: (element.messages || []).join(", ") });
      return fields;
    }
    case "interaction_operand":
      return [{ key: "guard", label: t("Bedingung"), type: "text", value: element.guard || "" }];
    case "interaction_use":
      return [
        { key: "refers_to", label: t("Referenziert"), type: "text", value: element.refers_to },
        { key: "covered", label: t("Lebenslinien"), type: "multi", value: [...(element.covered || [])], options: lifelines },
      ];
    case "execution_specification":
      return [{ key: "span", label: t("Von – bis"), type: "info", value: `${element.start} → ${element.finish || t("letzte Nachricht")}` }];
    default:
      return [];
  }
}

const UPDATE_OPS = {
  lifeline: "update_lifeline",
  message: "update_message",
  combined_fragment: "update_combined_fragment",
  interaction_operand: "update_interaction_operand",
  interaction_use: "update_interaction_use",
};

function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

/** The `{op, args}` that applies the edited `values` (key -> value) to the element, or null if nothing changed. */
export function elementUpdate(kind, element, values) {
  const op = UPDATE_OPS[kind];
  if (!op) return null;
  const args = { id: element.id };
  for (const field of elementFields(kind, element)) {
    if (!(field.key in values) || field.type === "info") continue;
    let value = values[field.key];
    if (field.key === "messages") value = String(value).split(",").map((s) => s.trim()).filter(Boolean);
    const current = field.key === "messages" ? element.messages || [] : field.value;
    if (typeof value === "string" && (field.key === "name" || field.key === "description")) value = value.trim();
    if (!same(value, current)) args[field.key] = value;
  }
  return Object.keys(args).length > 1 ? { op, args } : null;
}

/** The `{op, args}` that deletes the element. */
export function elementRemove(kind, element) {
  return kind === "lifeline" ? { op: "remove_lifeline", args: { id: element.id } } : { op: "remove_fragment", args: { id: element.id } };
}
