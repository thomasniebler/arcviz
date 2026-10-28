// The arc42 gap analysis in the UI (spec: docs/architecture/specs/arc42-gap-analysis.md, ADR-0211): labels, the next step
// of a gap as a route or a control to click, the progress line and the remarks of the model pass by section.
// Pure module: no DOM.
import { N_, t } from "./i18n.js";

export const STATUS_LABELS = { complete: N_("vollständig"), partial: N_("teilweise"), missing: N_("fehlt") };
export const STATUS_TONES = { complete: "ok", partial: "warn", missing: "danger" };
export const SEVERITY_LABELS = { high: N_("wichtig"), medium: N_("mittel"), low: N_("gering") };
export const SEVERITY_TONES = { high: "danger", medium: "warn", low: "" };
export const REMARK_LABELS = { thin: N_("dünn"), inconsistent: N_("widersprüchlich"), outdated: N_("veraltet") };
const TAB_STEPS = {
  vision: N_("Vision ergänzen"), drivers: N_("Treiber anlegen"), stakeholders: N_("Stakeholder anlegen"),
  decisions: N_("Entscheidungen öffnen"), scenarios: N_("Szenarien öffnen"), documentation: N_("Notiz schreiben"),
};
const DIAGRAM_TYPES = { deployment: N_("Verteilungsdiagramm"), sequence: N_("Sequenzdiagramm") };
const labelOf = (table, key) => (table[key] ? t(table[key]) : key);

export const statusLabel = (status) => labelOf(STATUS_LABELS, status);
export const severityLabel = (severity) => labelOf(SEVERITY_LABELS, severity);
export const remarkLabel = (kind) => labelOf(REMARK_LABELS, kind);

/** `7 von 12 Abschnitten vollständig`. */
export const progressText = (report) => t("{complete} von {total} Abschnitten vollständig", { complete: report.complete, total: report.total });

/** The width of the progress bar in percent (0 to 100). */
export function progressPercent(report) {
  return report.total ? Math.round((100 * report.complete) / report.total) : 0;
}

/** The next step of a gap's `action`: `{label, route?, click?, item?, section?}`; `route` is a location hash, `click` the id of
 * an existing control, null for an action this version does not know. */
export function stepFor(action) {
  if (!action) return null;
  if (action.kind === "tab" && action.target) {
    const label = action.target === "decisions" && action.item ? t("Entscheidung öffnen") : labelOf(TAB_STEPS, action.target);
    return { label, route: `#/architecture/${action.target}`, ...(action.item ? { item: action.item } : {}), ...(action.section ? { section: action.section } : {}) };
  }
  if (action.kind === "model") return { label: t("Modell öffnen"), click: "btn-model" };
  if (action.kind === "new-diagram") return { label: t("Diagramm anlegen: {type}", { type: labelOf(DIAGRAM_TYPES, action.diagram_type) }), click: "btn-new" };
  return null;
}

/** The step that writes a note into section `id` (every section can be completed by prose). */
export const noteStep = (id) => ({ label: t("Notiz schreiben"), route: "#/architecture/documentation", section: id });

/** The remarks of the model pass grouped by section id: `{"01": [{kind, text, conflicts_with?}]}`. */
export function remarksBySection(remarks) {
  const out = {};
  for (const r of Array.isArray(remarks) ? remarks : []) (out[r.section] ||= []).push(r);
  return out;
}
