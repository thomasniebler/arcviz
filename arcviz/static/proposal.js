// Formats the diff of a propose-mode turn for the chat bubble (spec: docs/architecture/specs/propose-confirm.md).
// Pure module: no DOM access, unit-tested with node --test.
import { N_, t } from "./i18n.js";

const FIELD_LABELS = { name: N_("Name"), kind: N_("Art"), parent: N_("Container"), label: N_("Label"), direction: N_("Richtung") };

function show(value) {
  return value == null || value === "" ? "–" : String(value);
}

function fieldLabel(field) {
  if (field.startsWith("properties.")) return field.slice("properties.".length);
  return FIELD_LABELS[field] ? t(FIELD_LABELS[field]) : field;
}

function changeList(changes) {
  return Object.entries(changes).map(([field, [from, to]]) => `${fieldLabel(field)} ${show(from)} → ${show(to)}`).join(", ");
}

function edgeText(e) {
  return t("Kante {source} → {target}", { source: e.source, target: e.target }) + (e.label ? ` (${e.label})` : "");
}

/** Lines `{ sign: "+" | "−" | "~", text }` describing the diff, in a stable order. */
export function describeDiff(diff) {
  if (!diff) return [];
  const lines = [];
  if (diff.title) lines.push({ sign: "~", text: t("Titel: {from} → {to}", { from: show(diff.title[0]), to: show(diff.title[1]) }) });
  if (diff.direction) lines.push({ sign: "~", text: t("Richtung: {from} → {to}", { from: show(diff.direction[0]), to: show(diff.direction[1]) }) });
  for (const n of diff.nodes?.added || []) lines.push({ sign: "+", text: n.parent ? t("{name} ({kind}) in {parent}", { name: n.name, kind: n.kind, parent: n.parent }) : `${n.name} (${n.kind})` });
  for (const n of diff.nodes?.removed || []) lines.push({ sign: "−", text: `${n.name} (${n.kind})` });
  for (const n of diff.nodes?.changed || []) lines.push({ sign: "~", text: `${n.name}: ${changeList(n.changes)}` });
  for (const e of diff.edges?.added || []) lines.push({ sign: "+", text: edgeText(e) });
  for (const e of diff.edges?.removed || []) lines.push({ sign: "−", text: edgeText(e) });
  for (const e of diff.edges?.changed || []) lines.push({ sign: "~", text: `${edgeText(e)}: ${changeList(e.changes)}` });
  // sequence diagrams: interaction elements by id with a one-line description
  for (const [key, sign] of [["added", "+"], ["removed", "−"], ["changed", "~"]]) {
    for (const e of diff.elements?.[key] || []) lines.push({ sign, text: e.description });
  }
  return lines;
}

// ---- proposals stored by agents (spec: docs/architecture/specs/agent-access.md, ADR-0172) ----

const STATUS_LABELS = { pending: N_("offen"), applying: N_("wird übernommen"), applied: N_("übernommen"), discarded: N_("verworfen"), stale: N_("veraltet") };

export function statusLabel(status) {
  return STATUS_LABELS[status] ? t(STATUS_LABELS[status]) : status;
}

/** How many of the listed proposals wait for a decision (stale ones do not count: they cannot be applied). */
export function pendingCount(proposals) {
  return (proposals || []).filter((p) => p.status === "pending").length;
}

/** "p3 · Bestellungen · von Ada (mcp)": `titles` maps diagram ids to titles. */
export function storedHeading(proposal, titles) {
  const who = proposal.author_name || proposal.author;
  const what = proposal.kind === "project" ? t("Architektur") : titles?.[proposal.diagram] || proposal.diagram;
  return t("{id} · {what} · von {who} ({client})", { id: proposal.id, what, who, client: proposal.client });
}

/** Whether a person can still apply it (a stale one cannot: its diagram changed). */
export function canApply(proposal) {
  return proposal.status === "pending";
}

/** Whether a person can still decide it: apply or discard (a stale one can only be discarded). */
export function canDecide(proposal) {
  return proposal.status === "pending" || proposal.status === "stale";
}

// ---- project proposals: items a person applies in part (spec: docs/architecture/specs/architecture-objects-over-mcp.md, ADR-0187) ----

const ITEM_STATE_LABELS = { pending: N_("offen"), applied: N_("übernommen"), discarded: N_("verworfen"), stale: N_("veraltet"), failed: N_("fehlgeschlagen") };
const ITEM_OP_LABELS = {
  add_driver: N_("Treiber anlegen"),
  update_driver: N_("Treiber ändern"),
  add_scenario: N_("Szenario anlegen"),
  update_scenario: N_("Szenario ändern"),
  add_decision: N_("Entscheidung anlegen (Entwurf)"),
  update_decision: N_("Entscheidung ändern"),
  link_requirements: N_("Anforderungen verknüpfen"),
  set_vision: N_("Vision setzen"),
  set_arc42_note: N_("arc42-Notiz setzen"),
  add_element: N_("Element anlegen"),
  update_element: N_("Element ändern"),
  add_relation: N_("Beziehung anlegen"),
  update_relation: N_("Beziehung ändern"),
  remove_relation: N_("Beziehung entfernen"),
};
// What the steward says about its items (spec: docs/architecture/specs/project-steward.md): fixes first, then next steps.
const GROUP_LABELS = { fix: N_("Korrekturen"), next_step: N_("Nächste Schritte") };
const GROUP_ORDER = ["", "fix", "next_step"];

/** The items of a project proposal in sections by `group`, in the order of GROUP_ORDER; items without a group come first
 * and without a heading (`label` is ""). Empty sections are left out. */
export function itemSections(items) {
  return GROUP_ORDER
    .map((group) => ({ group, label: GROUP_LABELS[group] ? t(GROUP_LABELS[group]) : "", items: (items || []).filter((i) => (i.group || "") === group) }))
    .filter((s) => s.items.length);
}

export function itemStateLabel(state) {
  return ITEM_STATE_LABELS[state] ? t(ITEM_STATE_LABELS[state]) : state;
}

export function itemOpLabel(op) {
  return ITEM_OP_LABELS[op] ? t(ITEM_OP_LABELS[op]) : op;
}

/** The keys a value refers to as "@key", anywhere in nested lists and objects. */
export function referencedKeys(value) {
  if (typeof value === "string") return value.startsWith("@") && value.length > 1 ? [value.slice(1)] : [];
  if (Array.isArray(value)) return value.flatMap(referencedKeys);
  if (value && typeof value === "object") return Object.values(value).flatMap(referencedKeys);
  return [];
}

/**
 * The keys to apply when the user ticked `selected`: the ticked pending items plus the pending items they need (an item
 * that names `@key` needs that item). `blocked` lists the ticked keys that need an item that can no longer be applied
 * (discarded, failed or stale), with that dependency.
 */
export function selectionClosure(items, selected) {
  const byKey = new Map(items.map((i) => [i.key, i]));
  const keys = new Set();
  const blocked = [];
  const visit = (key) => {
    if (keys.has(key)) return;
    const item = byKey.get(key);
    if (!item || item.state !== "pending") return;
    keys.add(key);
    for (const need of referencedKeys(item.arguments)) {
      const dep = byKey.get(need);
      if (dep && dep.state === "pending") visit(need);
      else if (dep && dep.state !== "applied") blocked.push({ key, needs: need });
    }
  };
  for (const key of selected) visit(key);
  const stuck = new Set(blocked.map((b) => b.key));
  return { keys: items.filter((i) => keys.has(i.key) && !stuck.has(i.key)).map((i) => i.key), blocked };
}

/** Pending items, in order. */
export function pendingItems(proposal) {
  return (proposal.items || []).filter((i) => i.state === "pending");
}

/** One line per notable argument of an item, for the card: `{label, text}`. Arguments are empty once decided. */
export function describeItem(item) {
  const a = item.arguments || {};
  const lines = [];
  const add = (label, value) => { if (value != null && value !== "" && !(Array.isArray(value) && !value.length)) lines.push({ label, text: Array.isArray(value) ? value.join(", ") : String(value) }); };
  add(t("Titel"), a.title);
  add(t("Ziel"), a.driver ?? a.scenario ?? a.decision ?? a.target);
  add(t("Treiber"), a.driver && item.op !== "update_driver" ? a.driver : null);
  add(t("Art"), a.kind);
  add(t("Wichtigkeit"), a.importance);
  add(t("Risiko"), a.risk);
  add(t("Beschreibung"), a.description);
  add(t("Reiz"), a.stimulus);
  add(t("Reaktion"), a.response);
  add(t("Reaktionsmaß"), a.response_measure);
  add(t("Kontext"), a.context);
  add(t("Entscheidung"), a.decision ?? a.text);
  add(t("Anforderungen"), [...(a.requirements || []), ...(item.derived_from || [])]);
  add(t("Name"), a.name);
  add(t("Aussage"), a.statement);
  add(t("Ziele"), a.goals);
  add(t("Nicht-Ziele"), a.non_goals);
  if (item.op === "add_relation" || item.op === "update_relation") add(t("Beziehung"), [a.source, a.kind, a.target].filter(Boolean).join(" "));
  if (item.op === "set_arc42_note") { add(t("Abschnitt"), a.section); add(t("Notiz"), a.text); }
  return lines;
}

/** Whether the proposal is a project proposal (items instead of a diagram). */
export function isProjectProposal(proposal) {
  return proposal.kind === "project";
}
