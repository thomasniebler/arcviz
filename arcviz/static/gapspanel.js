// The Lücken tab of the Architektur page (spec: docs/architecture/specs/arc42-gap-analysis.md, ADR-0211): how complete the
// project is against the twelve arc42 sections, the gaps of each with a button to the place that fixes it, and an optional
// model pass whose remarks are advice only. Read-only: nothing here writes, so there is nothing to discard.
import { api } from "./api.js";
import { $ } from "./dom.js";
import { noteStep, progressPercent, progressText, remarkLabel, remarksBySection, severityLabel, SEVERITY_TONES, statusLabel, STATUS_TONES, stepFor } from "./gaps.js";
import { t } from "./i18n.js";
import { chip } from "./mdpane.js";
import { currentProject } from "./navigator.js";

let report = null;
let remarks = {}; // section id -> remarks of the model pass (advice, never stored)
let onStep = () => {};
let generation = 0; // bumped on every load: a review answer for an earlier project or state is dropped

/** The panel has no editor, so leaving it never loses anything. */
export function isGapsDirty() { return false; }

const base = () => `/api/projects/${encodeURIComponent(currentProject())}/arc42`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function stepButton(step, primary = false) {
  const button = el("button", `btn small${primary ? " primary" : ""}`, step.label);
  button.type = "button";
  button.addEventListener("click", () => onStep(step));
  return button;
}

function renderSummary() {
  const box = $("gaps-summary");
  box.replaceChildren();
  const line = el("p", "gaps-progress", progressText(report));
  const bar = el("div", "gaps-bar");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", "100");
  bar.setAttribute("aria-valuenow", String(progressPercent(report)));
  const fill = el("span");
  fill.style.width = `${progressPercent(report)}%`;
  bar.appendChild(fill);
  box.append(line, bar);
  if (report.next) {
    const step = stepFor(report.next.action);
    const next = el("p", "gaps-next");
    next.append(el("strong", "", t("Nächster Schritt: ")), document.createTextNode(report.next.message));
    box.appendChild(next);
    if (step) box.appendChild(stepButton(step, true));
  } else {
    box.appendChild(el("p", "gaps-next", t("Alle zwölf Abschnitte haben Inhalt und keine bekannte Lücke.")));
  }
}

function renderSection(section) {
  const item = el("li", `gap-card status-${section.status}`);
  item.dataset.section = section.id;
  const head = el("header", "gap-card-head");
  head.append(el("span", "gap-number", String(Number(section.id))), el("h4", "", section.title), chip(statusLabel(section.status), STATUS_TONES[section.status] || ""));
  item.appendChild(head);
  const list = el("ul", "gap-items");
  for (const gap of section.gaps) {
    const row = el("li", "gap-item");
    row.dataset.gap = gap.id;
    row.append(chip(severityLabel(gap.severity), SEVERITY_TONES[gap.severity] || ""), el("span", "gap-message", gap.message));
    const step = stepFor(gap.action);
    if (step) row.appendChild(stepButton(step));
    list.appendChild(row);
  }
  for (const r of remarks[section.id] || []) {
    const row = el("li", "gap-item gap-remark");
    const other = r.conflicts_with ? ` (${t("Abschnitt {n}", { n: Number(r.conflicts_with) })})` : "";
    row.append(chip(`${t("Rat")}: ${remarkLabel(r.kind)}`, "accent"), el("span", "gap-message", r.text + other));
    list.appendChild(row);
  }
  if (list.children.length) item.appendChild(list);
  const offersNote = section.gaps.some((g) => g.action?.target === "documentation");
  if (section.status === "missing" && section.id !== "01" && !offersNote) item.appendChild(stepButton(noteStep(section.id))); // prose also fills a section
  return item;
}

function render() {
  if (!report) return;
  renderSummary();
  $("gaps-list").replaceChildren(...report.sections.map(renderSection));
}

async function review() {
  const button = $("gaps-review");
  const note = $("gaps-review-note");
  const seen = generation;
  button.disabled = true;
  note.textContent = t("Prüfe … (ein Modellaufruf, nichts wird gespeichert)");
  try {
    const answer = await api(`${base()}/gaps/review`, { method: "POST" });
    if (seen !== generation) return;
    if (!answer.llm.ok) { note.textContent = t("Keine Hinweise: {error}", { error: answer.llm.error || "" }); return; }
    remarks = remarksBySection(answer.remarks);
    note.textContent = answer.remarks.length ? t("{n} Hinweise des Assistenten – nur ein Rat, gezählt wird nicht.", { n: answer.remarks.length }) : t("Der Assistent hat nichts zu beanstanden.");
    render();
  } catch (err) {
    if (seen === generation) note.textContent = t("Keine Hinweise: {error}", { error: err.message || String(err) });
  } finally {
    button.disabled = false;
  }
}

/** Load and show the gap analysis of the open project (when the tab is shown). */
export async function loadGaps() {
  if (!currentProject()) return;
  const seen = ++generation;
  const next = await api(`${base()}/gaps`);
  if (seen !== generation) return;
  report = next;
  remarks = {};
  $("gaps-status").textContent = "";
  $("gaps-review-note").textContent = "";
  render();
}

/** `step(step)` follows a next step: `{label, route?, click?, item?, section?}` (archview.js knows the other tabs). */
export function initGapsPanel({ step }) {
  onStep = step;
  $("gaps-review").addEventListener("click", () => review());
}
