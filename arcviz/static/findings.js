// Review findings for the review panel (spec: docs/architecture/specs/llm-review.md): grouping, German
// labels, and the ids needed to highlight a finding on the rendered SVG.
// Pure module: no DOM access, unit-tested with node --test.
import { N_, t } from "./i18n.js";

export const CATEGORY_LABELS = { risk: N_("Risiken"), suggestion: N_("Verbesserungen"), strength: N_("Stärken") };
export const SEVERITY_LABELS = { high: N_("hoch"), medium: N_("mittel"), low: N_("niedrig"), info: N_("Info") };

/** `[{category, label, findings}]` in the order risks, suggestions, strengths; empty groups are left out. */
export function groupFindings(findings) {
  return Object.keys(CATEGORY_LABELS)
    .map((category) => ({ category, label: t(CATEGORY_LABELS[category]), findings: (findings || []).filter((f) => f.category === category) }))
    .filter((g) => g.findings.length);
}

const SEVERITY_RANK = { high: 0, medium: 1, low: 2, info: 3 };
const CATEGORY_RANK = { risk: 0, suggestion: 1, strength: 2 };

/** Rule findings followed by LLM findings, stably sorted by severity, then category (as the server sorts). */
export function mergeFindings(ruleFindings, llmFindings) {
  return [...(ruleFindings || []), ...(llmFindings || [])]
    .map((f, i) => [f, i])
    .sort(([a, i], [b, j]) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || CATEGORY_RANK[a.category] - CATEGORY_RANK[b.category] || i - j)
    .map(([f]) => f);
}

/** Names of the finding's nodes that exist in `nodes` (unknown ids are skipped). */
export function elementNames(finding, nodes) {
  return (finding.elements || []).filter((id) => nodes[id]).map((id) => nodes[id].name);
}

/** Mermaid 11 ids of the paths of the given edges: `L_<from>_<to>_<n>`. Mermaid numbers the edges between the
 * same two nodes in drawing order as 0, 2, 3, … (the n-th earlier one gives n + 1; there is no 1). Only drawn edges
 * count: both endpoints have a Mermaid id and, when `visible` (the node ids of the focused subtree) is given, at
 * least one is visible, in the order of `edges`, as mermaid.render emits them. */
export function edgeSvgKeys(edgeIds, edges, mermaidIds, visible = null) {
  const wanted = new Set(edgeIds || []);
  const keys = new Map();
  const seen = new Map(); // "from to" -> number of drawn edges between them so far
  for (const e of edges || []) {
    const from = mermaidIds[e.source], to = mermaidIds[e.target];
    if (!from || !to || (visible && !visible.has(e.source) && !visible.has(e.target))) continue;
    const earlier = seen.get(`${from} ${to}`) || 0;
    seen.set(`${from} ${to}`, earlier + 1);
    if (wanted.has(e.id)) keys.set(e.id, `L_${from}_${to}_${earlier ? earlier + 1 : 0}`);
  }
  return [...(edgeIds || [])].filter((id) => keys.has(id)).map((id) => keys.get(id));
}

/** A string that changes whenever the content of the diagram changes (timestamps excluded). */
export function diagramKey(diagram) {
  if (!diagram) return "";
  // completeness and hidden relations (model-relations.md) change what the project review reports
  return JSON.stringify([diagram.title, diagram.direction, diagram.nodes, diagram.edges, diagram.interaction ?? null, diagram.complete ?? [], diagram.hidden_relations ?? []]);
}

const CATEGORY_SINGULAR = { risk: N_("Risiko"), suggestion: N_("Verbesserung") };

/** The chat instruction that applies a finding's suggestion (sent as a propose-mode turn), or null if the
 * finding has nothing to apply (no suggestion, or a strength). */
export function suggestionPrompt(finding, nodes) {
  if (!finding.suggestion || !CATEGORY_SINGULAR[finding.category]) return null;
  const refs = [];
  const elements = (finding.elements || []).filter((id) => nodes[id]).map((id) => `${nodes[id].name} (${id})`);
  if (elements.length) refs.push(t("Betroffene Elemente: {items}", { items: elements.join(", ") }));
  if (finding.edges?.length) refs.push(t("Betroffene Kanten: {items}", { items: finding.edges.join(", ") }));
  return [
    t("Setze diesen Vorschlag aus dem Architektur-Review um: {suggestion}", { suggestion: finding.suggestion }),
    t("Befund ({category}, {severity}): {message}", { category: t(CATEGORY_SINGULAR[finding.category]), severity: SEVERITY_LABELS[finding.severity] ? t(SEVERITY_LABELS[finding.severity]) : finding.severity, message: finding.message }),
    refs.join(" · "),
  ].filter(Boolean).join("\n");
}

// ---- project review (spec: docs/architecture/specs/project-review.md) ----

/** What a project finding concerns, for its chip: `{kind: "diagram"|"section"|"project", id, label}`.
 * `diagrams` is the review answer's index (id -> {title}), `sections` the project's sections. */
export function findingTarget(finding, diagrams, sections) {
  const title = (id) => diagrams?.[id]?.title ?? id;
  const ids = finding.diagrams?.length ? finding.diagrams : finding.diagram ? [finding.diagram] : [];
  if (ids.length) return { kind: "diagram", id: finding.diagram ?? ids[0], label: ids.length > 1 ? t("Diagramme: {titles}", { titles: ids.map(title).join(", ") }) : t("Diagramm: {titles}", { titles: ids.map(title).join(", ") }) };
  if (finding.section) {
    const section = (sections || []).find((s) => s.id === finding.section);
    return { kind: "section", id: finding.section, label: t("Abschnitt: {title}", { title: section?.title ?? finding.section }) };
  }
  return { kind: "project", id: null, label: t("Projekt") };
}

/** The project model element a finding concerns, as a chip label (`Element: Name`), or null (spec:
 * shared-project-model.md). `model` is the review answer's index (id -> {name, kind}). */
export function modelElementLabel(finding, model) {
  if (!finding.model_element) return null;
  return t("Element: {name}", { name: model?.[finding.model_element]?.name ?? finding.model_element });
}

/** Whether a finding can be marked on the canvas: in the project review only findings of the shown diagram. */
export function highlightable(finding, shownDiagramId, projectMode) {
  return !projectMode || (Boolean(finding.diagram) && finding.diagram === shownDiagramId);
}
