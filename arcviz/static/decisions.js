// Decision support (spec: docs/architecture/specs/decision-support.md): labels, grouping and the ADR draft built from
// a what-if analysis. Pure module: no DOM.
import { N_, t } from "./i18n.js";

export const SCENARIO_KIND_LABELS = {
  usage: N_("Nutzung"), growth: N_("Wachstum"), change: N_("Änderung"), failure: N_("Ausfall"), threat: N_("Bedrohung"), cost: N_("Kosten"), other: N_("Sonstiges"),
};
// The six parts of an ATAM quality scenario, in their order, with a German label and a hint.
export const SCENARIO_PARTS = [
  ["source", N_("Quelle"), N_("Wer oder was löst es aus? (Nutzergruppe, Fremdsystem, Angreifer, Betrieb)")],
  ["stimulus", N_("Stimulus"), N_("Was passiert? („Was, wenn …“)")],
  ["environment", N_("Umgebung"), N_("Unter welchen Bedingungen? (Normalbetrieb, Lastspitze, während eines Deployments)")],
  ["artifact", N_("Artefakt"), N_("Was ist betroffen? (Gesamtsystem, eine Komponente)")],
  ["response", N_("Reaktion"), N_("Wie soll das System reagieren?")],
  ["response_measure", N_("Reaktionsmaß"), N_("Woran wird die Reaktion gemessen? (z. B. < 5 min Ausfall)")],
];
export const STATUS_LABELS = { draft: N_("Entwurf"), accepted: N_("Angenommen"), superseded: N_("Ersetzt"), rejected: N_("Verworfen") };
export const EFFECT_LABELS = { supports: N_("unterstützt"), at_risk: N_("gefährdet"), violates: N_("verletzt"), neutral: N_("neutral") };
const labelOf = (table, key) => (table[key] ? t(table[key]) : key);

/** The label of a scenario kind, decision status or driver effect in the current language; an unknown key as it is. */
export const scenarioKindLabel = (kind) => labelOf(SCENARIO_KIND_LABELS, kind);
export const statusLabel = (status) => labelOf(STATUS_LABELS, status);
export const effectLabel = (effect) => labelOf(EFFECT_LABELS, effect);
const STATUS_ORDER = ["accepted", "draft", "superseded", "rejected"];

/** `adr3` -> `ADR-3`. */
export function adrLabel(id) {
  const m = /^adr(\d+)$/.exec(id || "");
  return m ? `ADR-${m[1]}` : id || "";
}

/** Scenarios grouped by the driver they serve, in the drivers' order (`drivers` as given, e.g. by priority), the
 * unassigned last; core scenarios first inside a group. `[{driver: {...}|null, scenarios: [...]}]`, empty groups left
 * out. */
export function groupScenarios(scenarios, drivers) {
  const ordered = (list) => [...list].sort((a, b) => Number(Boolean(b.core)) - Number(Boolean(a.core)));
  const known = new Set((drivers || []).map((d) => d.id));
  const groups = (drivers || []).map((d) => ({ driver: d, scenarios: ordered((scenarios || []).filter((s) => s.driver === d.id)) }));
  groups.push({ driver: null, scenarios: ordered((scenarios || []).filter((s) => !s.driver || !known.has(s.driver))) });
  return groups.filter((g) => g.scenarios.length);
}

/** One line for a scenario: `stimulus → response measure` (or its title). */
export function scenarioLine(s) {
  const what = s.stimulus || s.title;
  return s.response_measure ? `${what} → ${s.response_measure}` : what;
}

/** Decisions by status (accepted, draft, superseded, rejected), the newest first inside a status. */
export function sortedDecisions(decisions) {
  const rank = (d) => { const n = STATUS_ORDER.indexOf(d.status); return n === -1 ? STATUS_ORDER.length : n; };
  return [...(decisions || [])].sort((a, b) => rank(a) - rank(b) || (b.created_at || "").localeCompare(a.created_at || ""));
}

const bullet = (items) => items.map((i) => `- ${i}`).join("\n");
const clip = (text, max) => (text.length > max ? text.slice(0, max - 1) + "…" : text);

/** The body of `POST …/decisions` for option `index` of an analysis (spec: decision-support.md): an ADR draft the user
 * edits. Texts are clipped to the server's bounds (titles 160, long texts 3000). */
export function decisionFromAnalysis(scenario, analysis, index) {
  const option = analysis.options[index];
  const parts = SCENARIO_PARTS.filter(([key]) => scenario[key]).map(([key, label]) => `${t(label)}: ${scenario[key]}`);
  const context = [
    t("Szenario „{title}“:", { title: scenario.title }), ...parts,
    ...(analysis.summary ? ["", analysis.summary] : []),
    ...(analysis.sensitivity_points?.length ? ["", t("Sensitivitätspunkte:"), bullet(analysis.sensitivity_points)] : []),
    ...(analysis.tradeoffs?.length ? ["", t("Zielkonflikte:"), bullet(analysis.tradeoffs)] : []),
  ].join("\n");
  const consequences = [
    ...(option.pros.length ? [t("Positiv:"), bullet(option.pros)] : []),
    ...(option.cons.length ? [option.pros.length ? "" : null, t("Negativ:"), bullet(option.cons)].filter((l) => l !== null) : []),
  ].join("\n");
  const rated = (analysis.drivers || []).filter((d) => d.effect !== "neutral").map((d) => d.driver);
  const drivers = [...new Set([...(scenario.driver ? [scenario.driver] : []), ...rated])];
  return {
    title: clip(`${scenario.title}: ${option.title}`, 160),
    status: "draft",
    context: clip(context, 3000),
    options: analysis.options.map((o) => ({ title: o.title, description: o.description || "", pros: o.pros || [], cons: o.cons || [] })),
    chosen: index,
    decision: clip(`${t("Gewählt: {title}.", { title: option.title })}${option.description ? ` ${option.description}` : ""}\n\n${t("Begründung:")} `, 3000),
    consequences: clip(consequences, 3000),
    scenario: scenario.id,
    drivers: drivers.slice(0, 20),
    elements: (analysis.elements || []).map((e) => e.element).slice(0, 20),
  };
}

/** The PATCH body of an edited item: only the fields in `keys` that changed; null when nothing did. Lists compare by
 * content, texts are trimmed. */
export function changedFields(item, edited, keys) {
  const body = {};
  for (const key of keys) {
    if (!(key in edited)) continue;
    let value = edited[key];
    if (typeof value === "string") value = value.trim();
    const before = item[key] ?? (Array.isArray(value) ? [] : typeof value === "boolean" ? false : "");
    const same = Array.isArray(value) ? JSON.stringify(value) === JSON.stringify(before) : value === before;
    if (!same) body[key] = value;
  }
  return Object.keys(body).length ? body : null;
}
