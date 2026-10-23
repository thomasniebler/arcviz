// Architecture review panel (spec: docs/architecture/specs/llm-review.md): rule checks (GET /checks), the LLM
// review (POST /review), the findings grouped by category, and the highlight of the selected finding on the canvas.
// Project mode (spec: docs/architecture/specs/project-review.md): the same panel for the open project; a finding
// names its diagram, and selecting it opens that diagram and highlights the elements there.
import { api } from "./api.js";
import { onRendered } from "./canvas.js";
import { onDiagramShown, onDiagramSwitched, showDiagram } from "./diagram.js";
import { $, el } from "./dom.js";
import { SEVERITY_LABELS, diagramKey, edgeSvgKeys, elementNames, findingTarget, groupFindings, highlightable, mergeFindings, modelElementLabel, suggestionPrompt } from "./findings.js";
import { reviewMeta } from "./format.js";
import { descendantIds, nodeIdForSvgId } from "./model.js";
import { needsConfirmation, resolutionRequest } from "./relations.js";
import { currentProjectInfo, onProjectShown, openInProject } from "./navigator.js";
import { focusQuery, state } from "./state.js";
import { t } from "./i18n.js";

const review = {
  open: false,
  rules: [],        // findings of the last rule check
  llm: null,        // {summary, findings, outcome} of the last LLM review (outcome = its `llm` object), or null
  stale: false,     // the diagram changed since the LLM review
  key: "",          // diagramKey of the diagram the findings belong to
  selected: null,   // id of the highlighted finding
  noRules: false,   // the diagram's type has no rule checks yet
  mode: "diagram",  // "diagram" (the shown diagram) or "project" (the open project)
  diagrams: {},     // project mode: id -> {title, type, section} of the reviewed project's diagrams
  model: {},        // project mode: id -> {name, kind} of the project's model elements
};
let shown = null;   // the diagram of the last showDiagram
const seenKeys = new Map(); // diagram id -> diagramKey when last shown: tells a changed diagram from a switch
let projectEdits = 0;       // project mode: changes seen to the project's diagrams (stale AI findings)
let checkSeq = 0, llmSeq = 0; // ignore answers of superseded requests
let rv = null;      // panel elements
let onApply = () => {}; // starts a propose-mode turn with the instruction (wired by app.js)

function findings() { return mergeFindings(review.rules, review.llm?.findings); }

// ---------- canvas highlight ------------------------------------------------------------------

function applyHighlight() {
  el.viewport.querySelectorAll(".review-hit").forEach((n) => n.classList.remove("review-hit"));
  const finding = review.selected && findings().find((f) => f.id === review.selected);
  if (!finding || state.compare || state.previewVersion != null) return; // other renders show other states
  if (!highlightable(finding, state.diagramId, review.mode === "project")) return; // another diagram's finding
  const wanted = new Set(finding.elements);
  if (state.view?.renderer === "svg") { // own SVG renderer (sequence diagrams): elements carry their model id
    el.viewport.querySelectorAll("g[data-id]").forEach((g) => { if (wanted.has(g.dataset.id)) g.classList.add("review-hit"); });
    return;
  }
  el.viewport.querySelectorAll("g.node[id], g.cluster[id], g.icon-shape[id]").forEach((g) => {
    if (wanted.has(nodeIdForSvgId(g.id, state.mermaidIds))) g.classList.add("review-hit");
  });
  const visible = state.focus ? new Set([state.focus, ...descendantIds(state.nodes, state.focus)]) : null;
  const keys = edgeSvgKeys(finding.edges, state.edges, state.mermaidIds, visible);
  if (!keys.length) return;
  el.viewport.querySelectorAll("path[id], path[data-id]").forEach((p) => {
    const id = p.getAttribute("data-id") || p.id;
    if (keys.some((k) => id === k || id.endsWith(`-${k}`))) p.classList.add("review-hit");
  });
}

async function select(id) {
  review.selected = review.selected === id ? null : id;
  rv.list.querySelectorAll("li.finding").forEach((li) => li.classList.toggle("selected", li.dataset.finding === review.selected));
  const finding = review.selected && findings().find((f) => f.id === review.selected);
  if (review.mode === "project" && finding?.diagram && finding.diagram !== state.diagramId && !state.busy) {
    await openInProject(finding.diagram); // the highlight follows once it is rendered (onRendered)
    return;
  }
  applyHighlight();
}

// ---------- panel -----------------------------------------------------------------------------

function findingItem(f) {
  const li = document.createElement("li");
  li.className = `finding sev-${f.severity}`;
  if (f.source === "llm" && review.stale) li.classList.add("stale");
  if (f.id === review.selected) li.classList.add("selected");
  li.dataset.finding = f.id;
  li.tabIndex = 0;
  const head = document.createElement("div");
  head.className = "finding-head";
  const sev = document.createElement("span");
  sev.className = "sev";
  sev.textContent = SEVERITY_LABELS[f.severity] ? t(SEVERITY_LABELS[f.severity]) : f.severity;
  head.appendChild(sev);
  if (f.source === "llm") {
    const ai = document.createElement("span");
    ai.className = "source";
    ai.textContent = t("KI");
    ai.title = t("Befund des KI-Reviews");
    head.appendChild(ai);
  }
  if (review.mode === "project") {
    const target = document.createElement("span");
    target.className = "target";
    target.textContent = findingTarget(f, review.diagrams, currentProjectInfo()?.sections).label;
    head.appendChild(target);
    const element = modelElementLabel(f, review.model);
    if (element) {
      const chip = document.createElement("span");
      chip.className = "target model-element";
      chip.textContent = element;
      head.appendChild(chip);
    }
  }
  const names = review.mode === "project" && f.diagram !== state.diagramId ? [] : elementNames(f, state.nodes);
  if (names.length) {
    const refs = document.createElement("span");
    refs.className = "refs";
    refs.textContent = names.join(", ");
    head.appendChild(refs);
  }
  const message = document.createElement("div");
  message.className = "finding-message";
  message.textContent = f.message;
  li.append(head, message);
  const prompt = (review.mode !== "project" || f.diagram) && suggestionPrompt(f, state.nodes);
  if (prompt) {
    const actions = document.createElement("div");
    actions.className = "finding-actions";
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "finding-apply";
    apply.textContent = t("Als Vorschlag umsetzen");
    const stale = f.source === "llm" && review.stale;
    apply.title = stale ? t("Diagramm geändert – KI-Review erneut ausführen") : t("Als Vorschau umsetzen: {suggestion}", { suggestion: f.suggestion });
    apply.disabled = state.busy || stale;
    apply.dataset.stale = String(stale);
    apply.addEventListener("click", async () => {
      if (state.busy) return;
      if (review.mode === "project" && f.diagram !== state.diagramId) await openInProject(f.diagram); // apply it there
      onApply(suggestionPrompt(f, state.nodes));
    });
    actions.appendChild(apply);
    li.appendChild(actions);
  }
  if (review.mode === "project" && f.resolutions?.length) li.appendChild(resolutionButtons(f));
  li.addEventListener("click", (e) => { if (!e.target.closest("button")) select(f.id); });
  li.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === li) { e.preventDefault(); select(f.id); } });
  return li;
}

// ---------- resolutions of correspondence findings (spec: model-relations.md, ADR-0138) ----------------------
// Nothing is applied by the review: each button is a person's decision. A proposal is a propose-mode turn on the
// diagram (preview, then confirm or discard); every other resolution is one request through the ordinary routes.

function resolutionButtons(f) {
  const box = document.createElement("div");
  box.className = "finding-actions finding-resolutions";
  for (const resolution of f.resolutions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "finding-apply finding-resolution small";
    button.dataset.action = resolution.action;
    button.textContent = resolution.label;
    button.disabled = state.busy;
    button.addEventListener("click", () => resolve(resolution));
    box.appendChild(button);
  }
  return box;
}

async function resolve(resolution) {
  if (state.busy) return;
  if (resolution.action === "propose") {
    if (resolution.diagram && resolution.diagram !== state.diagramId) await openInProject(resolution.diagram);
    onApply(resolution.prompt); // the findings are checked again once the confirmed proposal changed the diagram
    return;
  }
  const request = resolutionRequest(state.projectId, resolution);
  if (!request) return;
  if (needsConfirmation(resolution) && !confirm(t("{label}? Die Diagramme behalten ihre Verbindungen, nur die Verknüpfung entfällt.", { label: resolution.label }))) return;
  rv.status.textContent = t("Wird angewendet …");
  try {
    await api(request.path, { method: request.method, ...(request.body ? { body: JSON.stringify(request.body) } : {}) });
    // the shown diagram may have changed with it (a marker, a link, a link the model change took away)
    if (state.diagramId) showDiagram(await api(`/api/diagrams/${encodeURIComponent(state.diagramId)}${focusQuery()}`));
    await runChecks();
  } catch (err) {
    rv.status.textContent = t("Nicht angewendet: {error}", { error: err.message || err });
  }
}

function renderPanel() {
  const all = findings();
  if (review.selected && !all.some((f) => f.id === review.selected)) review.selected = null;
  rv.list.innerHTML = "";
  for (const group of groupFindings(all)) {
    const h = document.createElement("h4");
    h.textContent = t("{label} ({count})", { label: group.label, count: group.findings.length });
    const ul = document.createElement("ul");
    for (const f of group.findings) ul.appendChild(findingItem(f));
    rv.list.append(h, ul);
  }
  if (!all.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = review.mode !== "project" && review.noRules
      ? t("Für diesen Diagrammtyp gibt es noch keine Regelprüfungen; das KI-Review ist verfügbar.") : t("Keine Befunde.");
    rv.list.replaceChildren(p);
  }
  rv.summary.textContent = review.llm?.summary || "";
  rv.summary.classList.toggle("hidden", !review.llm?.summary);
  rv.stale.classList.toggle("hidden", !(review.llm && review.stale));
  rv.status.textContent = reviewMeta({ findings: all, llm: review.llm?.outcome });
  applyHighlight();
}

function reviewPath(kind) {
  return review.mode === "project" ? `/api/projects/${encodeURIComponent(state.projectId)}/${kind}` : `/api/diagrams/${state.diagramId}/${kind}`;
}

/** Still the same review target as when a request started (a project review survives diagram switches). */
function sameTarget(mode, id) { return review.mode === mode && id === (mode === "project" ? state.projectId : state.diagramId); }

async function runChecks() {
  const mode = review.mode, id = mode === "project" ? state.projectId : state.diagramId, mine = ++checkSeq;
  const key = diagramKey(shown);
  try {
    const data = await api(reviewPath("checks"));
    if (mine !== checkSeq || !sameTarget(mode, id)) return;
    if (mode === "project") { review.rules = data.findings; review.diagrams = data.diagrams || {}; review.model = data.model || {}; renderPanel(); return; }
    if (review.llm && key !== review.key) review.stale = true; // keep the LLM findings, marked as outdated
    review.rules = data.findings;
    review.noRules = !(data.rules || []).length; // no rule checks registered for this diagram type yet
    review.key = key;
    renderPanel();
  } catch (err) {
    if (mine === checkSeq) rv.status.textContent = t("Prüfung fehlgeschlagen: {error}", { error: err.message || err });
  }
}

async function runLlm() {
  if (rv.llmButton.dataset.running === "true") return; // one paid model call at a time
  const mode = review.mode, id = mode === "project" ? state.projectId : state.diagramId, mine = ++llmSeq;
  const key = diagramKey(shown), edits = projectEdits;
  rv.llmButton.dataset.running = "true"; // setBusy keeps the button disabled while this is set
  rv.llmButton.disabled = true;
  rv.llmButton.textContent = t("KI prüft …");
  rv.status.textContent = t("KI-Review läuft …");
  try {
    const data = await api(reviewPath("review"), { method: "POST" });
    if (mine !== llmSeq || !sameTarget(mode, id)) return;
    if (mode === "project") {
      review.llm = { summary: data.summary, findings: data.findings.filter((f) => f.source === "llm"), outcome: data.llm };
      review.stale = edits !== projectEdits; // a diagram changed while the model was reviewing
      if (!review.stale) { // a stale answer's rule findings are older than the ones re-run after the edit
        review.rules = data.findings.filter((f) => f.source === "rule");
        review.diagrams = data.diagrams || {};
        review.model = data.model || {};
      }
      renderPanel();
      return;
    }
    review.llm = { summary: data.summary, findings: data.findings.filter((f) => f.source === "llm"), outcome: data.llm };
    review.stale = key !== diagramKey(shown); // the diagram changed while the model was reviewing
    if (!review.stale) { review.rules = data.findings.filter((f) => f.source === "rule"); review.key = key; }
    renderPanel();
  } catch (err) {
    if (mine === llmSeq) rv.status.textContent = t("KI-Review fehlgeschlagen: {error}", { error: err.message || err });
  } finally {
    rv.llmButton.dataset.running = "false";
    rv.llmButton.textContent = t("KI-Review");
    rv.llmButton.disabled = state.busy;
  }
}

function reset() {
  checkSeq++; llmSeq++;
  Object.assign(review, { rules: [], llm: null, stale: false, key: "", selected: null, noRules: false, diagrams: {} });
  if (rv) renderPanel();
}

/** Open the panel for the shown diagram (`mode` "diagram") or the open project ("project"), or close it. */
function setOpen(open, mode = review.mode) {
  if (open && mode !== review.mode) reset(); // findings of the other mode do not apply
  review.open = open;
  review.mode = mode;
  rv.panel.classList.toggle("hidden", !open);
  $("btn-review").setAttribute("aria-pressed", String(open && mode === "diagram"));
  $("btn-project-review").setAttribute("aria-pressed", String(open && mode === "project"));
  $("review-title").textContent = mode === "project" ? t("Projekt-Review · {title}", { title: currentProjectInfo()?.title ?? "" }) : t("Architektur-Review");
  if (open) runChecks();
  else { review.selected = null; applyHighlight(); }
}

const toggle = (mode) => setOpen(!(review.open && review.mode === mode), mode);

/** `apply(text)` runs the instruction of a finding's suggestion as a propose-mode chat turn. */
export function initReview({ onApply: apply } = {}) {
  if (apply) onApply = apply;
  rv = {
    panel: $("review"), list: $("review-list"), summary: $("review-summary"), status: $("review-status"),
    stale: $("review-stale"), llmButton: $("review-run-llm"),
  };
  $("btn-review").addEventListener("click", () => toggle("diagram"));
  $("btn-project-review").addEventListener("click", () => toggle("project"));
  onProjectShown(() => { if (review.mode === "project") { if (review.open) setOpen(false); reset(); } });
  $("review-close").addEventListener("click", () => setOpen(false));
  $("review-run-checks").addEventListener("click", runChecks);
  rv.llmButton.addEventListener("click", runLlm);
  onRendered(applyHighlight);
  onDiagramSwitched(() => { if (review.mode === "diagram") reset(); }); // project findings stay across diagrams
  onDiagramShown((detail) => {
    shown = detail.diagram;
    const shownKey = diagramKey(shown);
    const changed = seenKeys.has(shown.id) && seenKeys.get(shown.id) !== shownKey;
    seenKeys.set(shown.id, shownKey);
    if (review.mode === "project") {
      if (changed) { // a project diagram changed: the rules run again, AI findings are marked as outdated
        projectEdits++;
        if (review.llm) review.stale = true;
        if (review.open) { runChecks(); return; }
      }
      if (review.open) renderPanel(); // element names of the shown diagram
      return;
    }
    if (review.open && diagramKey(shown) !== review.key) runChecks();
  });
}
