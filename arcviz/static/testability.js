// Testability of decisions (spec: docs/architecture/specs/adr-testability.md, ADR-0209): labels, the rows of the editor,
// the suggestion request and its merge, the chip of a card. Pure module: no DOM.
import { N_, t } from "./i18n.js";

export const MAX_ASSUMPTIONS = 8;
export const MAX_ASSUMPTION = 300;
export const EFFORT_LABELS = { low: N_("gering"), medium: N_("mittel"), high: N_("hoch") };
export const ASSUMPTION_STATUS_LABELS = { untested: N_("ungetestet"), confirmed: N_("bestätigt"), refuted: N_("widerlegt") };
const labelOf = (table, key) => (table[key] ? t(table[key]) : key);

/** The label of an effort or an assumption status in the current language; an unknown key as it is. */
export const effortLabel = (effort) => labelOf(EFFORT_LABELS, effort);
export const assumptionStatusLabel = (status) => labelOf(ASSUMPTION_STATUS_LABELS, status);

/** A new, empty row of the editor. */
export const blankAssumption = () => ({ statement: "", test: "", effort: "medium", status: "untested", evidence: "" });

/** The rows as the server stores them: trimmed, rows without a statement dropped, `status` left out while untested, the
 * key order of the server's dump (so a comparison by content finds no difference in unchanged rows). */
export function normalizeRows(rows) {
  const out = [];
  for (const r of Array.isArray(rows) ? rows : []) {
    const statement = String(r?.statement ?? "").trim();
    if (!statement) continue;
    out.push({
      statement, test: String(r.test ?? "").trim(), effort: r.effort || "medium",
      ...(r.status && r.status !== "untested" ? { status: r.status } : {}), evidence: String(r.evidence ?? "").trim(),
    });
  }
  return out;
}

/** The body of the suggestion request: the decision as typed (title, texts, options, the chosen index). */
export function suggestionBody(values, options, chosen) {
  const body = { title: values.title || "" };
  for (const key of ["context", "decision", "consequences"]) if ((values[key] || "").trim()) body[key] = values[key];
  if (options?.length) body.options = options.map((o) => ({ title: o.title, pros: o.pros || [], cons: o.cons || [] }));
  if (Number.isInteger(chosen)) body.chosen = chosen;
  return body;
}

/** `rows` plus the suggested assumptions that are not there yet (same statement ignoring case), at most eight rows:
 * `{rows, added}`. Existing rows are never replaced (a person's text outranks a model's). */
export function mergeSuggestion(rows, suggested) {
  const merged = [...rows];
  const known = new Set(rows.map((r) => String(r.statement).trim().toLowerCase()));
  let added = 0;
  for (const s of Array.isArray(suggested) ? suggested : []) {
    const key = String(s?.statement ?? "").trim().toLowerCase();
    if (!key || known.has(key) || merged.length >= MAX_ASSUMPTIONS) continue;
    known.add(key);
    merged.push({ ...blankAssumption(), statement: String(s.statement).trim().slice(0, MAX_ASSUMPTION), test: s.test || "", effort: s.effort in EFFORT_LABELS ? s.effort : "medium" });
    added += 1;
  }
  return { rows: merged, added };
}

/** The chip of a decision card, `{text, tone}` or null: a refuted assumption, an accepted decision without any assumption
 * (the gap), else the count. */
export function testabilityChip(decision) {
  const list = decision?.assumptions || [];
  if (list.some((a) => a.status === "refuted")) return { text: t("Annahme widerlegt"), tone: "danger" };
  if (!list.length) return decision?.status === "accepted" ? { text: t("ohne Annahmen"), tone: "warn" } : null;
  const open = list.filter((a) => !a.status || a.status === "untested").length;
  return { text: t("{n} Annahmen, {open} ungetestet", { n: list.length, open }), tone: open ? "" : "ok" };
}
