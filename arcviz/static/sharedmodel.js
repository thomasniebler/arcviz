// The shared project model in the UI (spec: docs/architecture/specs/shared-project-model.md): pure helpers for
// the model panel and the inspector. No DOM; tested in tests/js/sharedmodel.test.mjs.

import { N_, getLanguage, t } from "./i18n.js";

/** German labels of the abstract element kinds (/api/config `project_model.element_kinds`). */
export const ELEMENT_KIND_LABELS = {
  actor: N_("Akteur"),
  system: N_("System"),
  subsystem: N_("Teilsystem"),
  component: N_("Komponente"),
  node: N_("Knoten"),
  data: N_("Daten"),
};

export function kindLabel(kind) {
  return ELEMENT_KIND_LABELS[kind] ? t(ELEMENT_KIND_LABELS[kind]) : kind;
}

/** The element kinds a view element of `viewKind` may represent in a diagram of `type` (empty: not linkable). */
export function linkableKinds(config, type, viewKind) {
  return config?.diagram_types?.[type]?.linkable?.[viewKind] || [];
}

/** The node or lifeline `id` of a shown diagram as `{id, name, kind, ref}` (lifelines have kind "lifeline"). */
export function viewElement(nodes, interaction, id) {
  if (interaction) {
    const lifeline = (interaction.lifelines || []).find((l) => l.id === id);
    return lifeline ? { id, name: lifeline.name, kind: "lifeline", ref: lifeline.ref ?? null, actor: !!lifeline.actor } : null;
  }
  const node = nodes?.[id];
  return node ? { id, name: node.name, kind: node.kind, ref: node.ref ?? null } : null;
}

export function findModelElement(model, id) {
  return (model?.elements || []).find((e) => e.id === id) || null;
}

/** Options for linking a view element: the elements of the allowed kinds, sorted by name. */
export function linkChoices(model, kinds) {
  return (model?.elements || [])
    .filter((e) => kinds.includes(e.kind))
    .sort((a, b) => a.name.localeCompare(b.name, getLanguage()))
    .map((e) => ({ value: e.id, label: `${e.name} (${kindLabel(e.kind)})` }));
}

/** The element kind "promote" preselects: an actor for an actor lifeline, else the first allowed kind. */
export function defaultKind(view, kinds) {
  if (view?.actor && kinds.includes("actor")) return "actor";
  return kinds[0] ?? null;
}

/** One code link as text: "backend:src/api · App (Einstieg)" (repo, symbol and note only when set). */
export function codeRefLabel(ref) {
  const where = `${ref.repo ? `${ref.repo}:` : ""}${ref.path}`;
  return `${where}${ref.symbol ? ` · ${ref.symbol}` : ""}${ref.note ? ` (${ref.note})` : ""}`;
}

/** The POST body of a new code link from the form's values, or null without a path. */
export function codeRefBody(values) {
  const path = (values.path || "").trim();
  if (!path) return null;
  return { path, repo: (values.repo || "").trim(), symbol: (values.symbol || "").trim(), note: (values.note || "").trim() };
}

/** The caption of an element's code section: "Code" or "Code (2)". */
export function codeRefsSummary(element) {
  const n = (element?.code_refs || []).length;
  return n ? t("Code ({n})", { n }) : "Code";
}

/** Where an element is shown, as text: "Plattform, Checkout (2×)" or "nicht verwendet". */
export function usageText(element) {
  const counts = new Map();
  for (const u of element?.usages || []) counts.set(u.title, (counts.get(u.title) || 0) + 1);
  if (!counts.size) return t("nicht verwendet");
  return [...counts].map(([title, n]) => (n > 1 ? t("{title} ({n}×)", { title, n }) : title)).join(", ");
}

/** The PATCH body of an element edit relative to `element`, or null when nothing changed. */
export function elementChanges(element, values) {
  const body = {};
  const name = (values.name ?? "").trim();
  if (name && name !== element.name) body.name = name;
  const description = (values.description ?? "").trim();
  if (description !== (element.description || "")) body.description = description;
  if (values.kind && values.kind !== element.kind) body.kind = values.kind;
  if (values.parent !== undefined && (values.parent || null) !== (element.parent ?? null)) body.parent = values.parent || null;
  return Object.keys(body).length ? body : null;
}

/** A short status line after a move into another project (#153): what came along, which links were dropped. */
export function carriedText(carried) {
  if (!carried) return "";
  const { elements: e = {}, relations: r = {} } = carried;
  const parts = [];
  if (e.copied?.length) parts.push(t("{n} Element(e) kopiert", { n: e.copied.length }));
  if (e.mapped?.length) parts.push(t("{n} zugeordnet", { n: e.mapped.length }));
  if (r.copied?.length) parts.push(t("{n} Beziehung(en) kopiert", { n: r.copied.length }));
  if (r.mapped?.length) parts.push(t("{n} Beziehung(en) zugeordnet", { n: r.mapped.length }));
  const dropped = [...(e.dropped || []).map((d) => t("Element {id}", { id: d.id })), ...(r.dropped || []).map((d) => t("Beziehung {id}", { id: d.id }))];
  const texts = [];
  if (parts.length) texts.push(t("Modell übernommen: {parts}", { parts: parts.join(", ") }));
  if (dropped.length) texts.push(t("Verknüpfung gelöst: {items}", { items: dropped.join(", ") }));
  return texts.join("; ");
}

/** A short status line after a rename: which diagrams followed, which did not (yet). */
export function propagationText(result) {
  const parts = [];
  if (result?.propagated?.length) parts.push(t("umbenannt in {n} Diagramm(en)", { n: result.propagated.length }));
  if (result?.failed?.length) parts.push(t("noch nicht in {items} (später „Abgleichen“)", { items: result.failed.join(", ") }));
  return parts.join("; ");
}

/** The confirmation of **Unverknüpfte übernehmen** (spec: elements-by-default.md): counts, then per diagram what
 * happens to its unlinked building blocks. */
export function adoptionPreview(plan) {
  const { linked = 0, created = 0, skipped = 0 } = plan.counts || {};
  const lines = [skipped
    ? t("Unverknüpfte Bausteine übernehmen? {linked} mit vorhandenen Projektelementen verknüpfen, {created} als neue Projektelemente anlegen, {skipped} bleiben unverknüpft.", { linked, created, skipped })
    : t("Unverknüpfte Bausteine übernehmen? {linked} mit vorhandenen Projektelementen verknüpfen, {created} als neue Projektelemente anlegen.", { linked, created }), ""];
  for (const d of plan.diagrams || []) {
    const parts = [
      ...(d.linked || []).map((v) => t("{name} → vorhanden", { name: v.name })),
      ...(d.created || []).map((v) => t("{name} (neu)", { name: v.name })),
      ...(d.skipped || []).map((v) => t("{name} (bleibt: passt nicht)", { name: v.name })),
    ];
    lines.push(`${d.title}: ${parts.slice(0, 8).join(", ")}${parts.length > 8 ? ` … (+${parts.length - 8})` : ""}`);
  }
  return lines.slice(0, 22).join("\n");
}

/** The status after **Unverknüpfte übernehmen**. */
export function adoptionText(result) {
  const { linked = 0, created = 0 } = result.counts || {};
  const failed = (result.failed || []).length;
  return failed
    ? t("{n} Baustein(e) übernommen ({created} neue Projektelemente); {failed} Diagramm(e) konnten nicht geschrieben werden.", { n: linked + created, created, failed })
    : t("{n} Baustein(e) übernommen ({created} neue Projektelemente).", { n: linked + created, created });
}
