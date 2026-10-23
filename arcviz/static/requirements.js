// Pure helpers of the requirements view (spec: docs/architecture/specs/requirements-workshop.md): labels, the query of the
// table, the pipeline counts, which suggestions a bulk button takes, selection ranges and when a chunk loop goes on.
// No DOM access; unit-tested with node --test.
import { N_, locale, t } from "./i18n.js";

export const STEP_ORDER = ["raw", "suggested", "refined", "derived", "rejected", "duplicate"];
export const STEP_LABELS = { raw: N_("Roh"), suggested: N_("Vorschlag"), refined: N_("Verfeinert"), derived: N_("Abgeleitet"), rejected: N_("Verworfen"), duplicate: N_("Duplikat") };
export const KIND_LABELS = { functional: N_("Funktional"), quality: N_("Qualität"), constraint: N_("Randbedingung"), business: N_("Fachlich"), unclear: N_("Unklar") };
export const PRIORITY_LABELS = { "": "–", must: N_("Muss"), should: N_("Soll"), could: N_("Kann"), wont: N_("Entfällt") };
export const COVERAGE_LABELS = { uncovered: N_("offen"), driver: N_("Treiber"), scenario: N_("Szenario"), decision: N_("Entscheidung") };
export const ISSUE_LABELS = {
  ambiguous: N_("mehrdeutig"), not_measurable: N_("nicht messbar"), incomplete: N_("unvollständig"),
  duplicate: N_("Duplikat"), conflict: N_("Widerspruch"), out_of_scope: N_("außerhalb des Umfangs"),
};
export const SUGGESTION_FIELD_LABELS = {
  statement: N_("Aussage"), kind: N_("Art"), priority: N_("Priorität"), measure: N_("Maß"), theme: N_("Thema"),
  stakeholders: N_("Stakeholder"), issues: N_("Befunde"), duplicate_of: N_("Duplikat von"),
};
export const PAGE_SIZE = 100;

const label = (table, key, fallback = "–") => (key == null || key === "" ? (table[""] ?? fallback) : table[key] ? t(table[key]) : String(key));
export const stepLabel = (step) => label(STEP_LABELS, step);
export const kindLabel = (kind) => label(KIND_LABELS, kind);
export const priorityLabel = (priority) => label(PRIORITY_LABELS, priority);
export const coverageLabel = (coverage) => (coverage == null ? "–" : label(COVERAGE_LABELS, coverage));
export const issueLabel = (kind) => label(ISSUE_LABELS, kind);

/** The text a row shows: the accepted statement, else the original wording. */
export function displayText(row) {
  return row.statement || row.raw;
}

/** The query string of `GET /requirements` for the filters (empty values are left out). */
export function listQuery(filters, { limit = PAGE_SIZE, offset = 0 } = {}) {
  const params = new URLSearchParams();
  for (const key of ["step", "kind", "theme", "coverage", "q"]) {
    const value = filters?.[key];
    if (value) params.set(key, value);
  }
  if (filters?.suggested) params.set("suggested", "true");
  params.set("limit", String(limit));
  if (offset) params.set("offset", String(offset));
  return `?${params}`;
}

/** The steps of the pipeline with their counts, in order: `[{step, label, count}]` from a digest. */
export function pipeline(digest) {
  const steps = digest?.steps || {};
  return STEP_ORDER.map((step) => ({ step, label: stepLabel(step), count: steps[step] || 0 }));
}

/** The items `POST …/accept` takes for the ticked rows that have a suggestion: each names the revision the user saw. */
export function acceptItems(rows, ids, fields) {
  const wanted = new Set(ids);
  return rows
    .filter((r) => wanted.has(r.id) && r.suggestion)
    .map((r) => ({ id: r.id, expected: r.suggestion.rev, ...(fields ? { fields } : {}) }));
}

/** Whether the bulk button "Unauffällige übernehmen" takes a row: a suggestion without an open conflict or duplicate. */
export function isUnremarkable(row) {
  const s = row.suggestion;
  if (!s || s.duplicate_of) return false;
  return !(s.issues || []).some((i) => !i.resolved && (i.kind === "conflict" || i.kind === "duplicate"));
}

export function unremarkableIds(rows) {
  return rows.filter(isUnremarkable).map((r) => r.id);
}

/** The ids from `anchor` to `target` in the order shown, both included (shift-click); `target` alone without an anchor. */
export function rangeIds(orderedIds, anchor, target) {
  const a = orderedIds.indexOf(anchor);
  const b = orderedIds.indexOf(target);
  if (b < 0) return [];
  if (a < 0) return [target];
  return orderedIds.slice(Math.min(a, b), Math.max(a, b) + 1);
}

/** The ticked ids with `id` toggled (a new array, order kept). */
export function toggled(selection, id) {
  return selection.includes(id) ? selection.filter((x) => x !== id) : [...selection, id];
}

const SAME = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/**
 * What an open suggestion would change, per field: `[{field, label, current, suggested, accepted}]`. A field the suggestion
 * does not change (or whose value is already in force) is left out unless it still waits to be accepted and differs;
 * `accepted` marks the fields a person took already.
 */
export function suggestionFields(row, stakeholderName = (id) => id) {
  const s = row.suggestion;
  if (!s) return [];
  const show = (field, value) => {
    if (field === "stakeholders") return (value || []).map(stakeholderName).join(", ");
    if (field === "issues") return (value || []).map((i) => issueLabel(i.kind)).join(", ");
    if (field === "kind") return kindLabel(value);
    if (field === "priority") return priorityLabel(value);
    return value ?? "";
  };
  const out = [];
  for (const field of Object.keys(SUGGESTION_FIELD_LABELS)) {
    const accepted = (s.accepted || []).includes(field);
    const current = field === "duplicate_of" ? row.duplicate_of : row[field];
    const suggested = s[field];
    if (!accepted && SAME(current, suggested)) continue;
    if (!accepted && (suggested == null || suggested === "" || (Array.isArray(suggested) && !suggested.length))) continue;
    out.push({ field, label: t(SUGGESTION_FIELD_LABELS[field]), current: show(field, current), suggested: show(field, suggested), accepted });
  }
  return out;
}

/** Whether a chunk loop (refine or derive) goes on: the call worked, something is left and it made progress. */
export function shouldContinue(result, previousRemaining) {
  if (!result || !result.llm?.ok) return false;
  const left = result.remaining ?? 0;
  const progress = (result.refined?.length ?? 0) + (result.proposal ? 1 : 0) + (result.unmapped?.length ?? 0);
  return left > 0 && progress > 0 && (previousRemaining == null || left < previousRemaining);
}

export function progressText(verb, done, remaining) {
  return remaining > 0 ? t("{verb}: {done} Durchgang/Durchgänge, noch {remaining} offen …", { verb, done, remaining }) : t("{verb}: fertig nach {done} Durchgang/Durchgängen.", { verb, done });
}

/** A sentence for the answer of the import route (a dry run or the real one). */
export function importSummary(result) {
  const added = result.added?.length ?? 0;
  const skipped = result.skipped_duplicates?.length ?? 0;
  const rejected = result.rejected?.length ?? 0;
  const problems = result.problems?.length ?? 0;
  const head = result.dry_run ? t("Würde {count} anlegen", { count: added }) : t("{count} angelegt", { count: added });
  const parts = [head, t("{count} schon vorhanden", { count: skipped })];
  if (rejected) parts.push(t("{count} abgelehnt", { count: rejected }));
  if (problems) parts.push(t("{count} Hinweis(e)", { count: problems }));
  return t("{parts}. Format: {format}.", { parts: parts.join(", "), format: result.format });
}

/** "Zeilen 3, 7 und 9: Grund" lines of the problems of an import, at most `max`. */
export function importProblemLines(result, max = 8) {
  const lines = [...(result.problems || []), ...(result.rejected || []), ...(result.skipped_duplicates || [])]
    .sort((a, b) => a.row - b.row)
    .map((p) => t("Zeile {row}: {reason}", { row: p.row, reason: p.reason }));
  return lines.length > max ? [...lines.slice(0, max), t("… und {count} weitere", { count: lines.length - max })] : lines;
}

/** The label of a requirement in a picker: `req3 · Der Shop antwortet …` (statement or original wording, shortened). */
export function optionLabel(row, max = 70) {
  const text = displayText(row).replace(/\s+/g, " ").trim();
  return `${row.id} · ${text.length > max ? `${text.slice(0, max - 1)}…` : text}`;
}

/** "deckt: req1, req3" for a driver, scenario or decision that names requirements; "" for none. */
export function requirementsText(item) {
  const ids = item?.requirements || [];
  return ids.length ? t("deckt: {ids}", { ids: ids.join(", ") }) : "";
}

/** The ids of a requirement list that the picker does not know (removed meanwhile): shown, never silently dropped. */
export function unknownIds(selected, rows) {
  const known = new Set(rows.map((r) => r.id));
  return (selected || []).filter((id) => !known.has(id));
}
