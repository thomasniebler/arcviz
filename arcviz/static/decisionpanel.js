// The decisions tab of the Architektur page (specs: docs/architecture/specs/decision-support.md and
// architecture-workspace.md): architecture decision records by status as a filterable card list with one editor beside it
// (title, status, superseded by, context, decision, consequences; the options read-only with the chosen one marked; a
// new decision or the selected one) and the Markdown ADR. Accepted decisions bind the assistants and the reviews.
import { api } from "./api.js";
import { $, option } from "./dom.js";
import { STATUS_LABELS, adrLabel, changedFields, sortedDecisions, statusLabel } from "./decisions.js";
import { locale, t } from "./i18n.js";
import { deleteGate } from "./lifecycle.js";
import { filterItems, stepId } from "./listnav.js";
import { bindListKeys, card, chip, confirmDiscard, hideIfEmpty, markSelected } from "./mdpane.js";
import { currentProject } from "./navigator.js";
import { chosenRequirements, fillRequirementSelect } from "./requirementpicker.js";

let decisions = [];
let drivers = [];
let elements = [];
let scenarios = [];
let editing = null; // the id of the decision in the editor
let creating = false; // the editor holds a new decision
let dirty = false;

/** Whether the editor holds changes that are not stored. */
export function isDecisionDirty() { return dirty; }

function path(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

function say(text) { $("decisions-status").textContent = text || ""; }

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const STATUS_TONES = { accepted: "ok", draft: "", superseded: "warn", rejected: "danger" };

function visible() {
  return filterItems(sortedDecisions(decisions), $("decisions-filter").value, (d) => [adrLabel(d.id), d.title, statusLabel(d.status), d.context].join(" "));
}

function render() {
  const list = $("decisions-list");
  list.replaceChildren();
  $("decisions-empty").classList.toggle("hidden", decisions.length > 0);
  const shown = visible();
  for (const d of shown) {
    const li = el("li", `decision-row status-${d.status}`);
    li.dataset.decisionId = d.id;
    li.appendChild(card({ id: d.id, lead: adrLabel(d.id), title: d.title, chips: [chip(statusLabel(d.status), STATUS_TONES[d.status] || "")] }));
    list.appendChild(li);
  }
  markSelected(list, editing);
  if ($("decisions-filter").value.trim()) say(t("{shown} von {total} Entscheidungen", { shown: shown.length, total: decisions.length }));
}

function showEditor(on) {
  $("decision-form").classList.toggle("hidden", !on);
  $("decision-empty-detail").classList.toggle("hidden", on);
}

function statusSelect(selected) {
  const status = $("decision-status");
  status.innerHTML = "";
  for (const value of Object.keys(STATUS_LABELS)) status.appendChild(option(value, statusLabel(value), value === selected));
}

/** The editor for a new decision. */
function startNew() {
  if (!confirmDiscard(dirty)) return;
  editing = null;
  creating = true;
  showEditor(true);
  $("decision-form-heading").textContent = t("Neue Entscheidung");
  $("decision-title").value = "";
  statusSelect("draft");
  const superseded = $("decision-superseded");
  superseded.replaceChildren(option("", t("— nicht ersetzt —"), true));
  superseded.disabled = true;
  $("decision-context").value = "";
  fillRequirementSelect($("decision-requirements"), []).then(() => hideIfEmpty($("decision-requirements"))).catch(() => { $("decision-requirements").disabled = true; });
  $("decision-text").value = "";
  $("decision-consequences").value = "";
  $("decision-options").replaceChildren();
  $("decision-refs").textContent = "";
  $("decision-markdown").classList.add("hidden");
  $("decision-delete").classList.add("hidden");
  $("decision-save").textContent = t("Anlegen");
  dirty = false;
  render();
  $("decision-title").focus();
}

function edit(id) {
  const d = decisions.find((x) => x.id === id);
  editing = d ? d.id : null;
  creating = false;
  showEditor(Boolean(d));
  dirty = false;
  render();
  if (!d) return;
  $("decision-form-heading").textContent = t("{adr} bearbeiten", { adr: adrLabel(d.id) });
  $("decision-superseded").disabled = false;
  $("decision-markdown").classList.remove("hidden");
  $("decision-delete").classList.remove("hidden");
  const gate = deleteGate("decision", d);
  $("decision-delete").disabled = !gate.allowed;
  $("decision-delete").title = gate.reason;
  $("decision-save").textContent = t("Speichern");
  statusSelect(d.status);
  const superseded = $("decision-superseded");
  superseded.innerHTML = "";
  superseded.appendChild(option("", t("— nicht ersetzt —"), !d.superseded_by));
  for (const other of decisions) if (other.id !== d.id) superseded.appendChild(option(other.id, t("ersetzt durch {adr}: {title}", { adr: adrLabel(other.id), title: other.title }), other.id === d.superseded_by));
  $("decision-title").value = d.title;
  $("decision-context").value = d.context || "";
  fillRequirementSelect($("decision-requirements"), d.requirements || []).then(() => hideIfEmpty($("decision-requirements"))).catch(() => { $("decision-requirements").disabled = true; });
  $("decision-text").value = d.decision || "";
  $("decision-consequences").value = d.consequences || "";
  const options = $("decision-options");
  options.replaceChildren();
  if (d.options.length) options.appendChild(el("span", "points-label", t("Optionen")));
  d.options.forEach((o, n) => {
    const card = el("div", `analysis-option${d.chosen === n ? " chosen" : ""}`);
    card.appendChild(el("strong", "", `${n + 1}. ${o.title}${d.chosen === n ? ` ${t("(gewählt)")}` : ""}`));
    if (o.description) card.appendChild(el("p", "", o.description));
    for (const [label, items, cls] of [[t("Pro"), o.pros, "pros"], [t("Contra"), o.cons, "cons"]]) {
      if (!items.length) continue;
      card.appendChild(el("span", `points-label ${cls}`, label));
      const list = el("ul", `points ${cls}`);
      for (const item of items) list.appendChild(el("li", "", item));
      card.appendChild(list);
    }
    options.appendChild(card);
  });
  const name = (list, id, attr) => list.find((x) => x.id === id)?.[attr] ?? id;
  const refs = [
    d.scenario ? t("Szenario: {title}", { title: name(scenarios, d.scenario, "title") }) : "",
    d.requirements?.length ? t("Anforderungen: {ids}", { ids: d.requirements.join(", ") }) : "",
    d.drivers.length ? t("Treiber: {titles}", { titles: d.drivers.map((x) => name(drivers, x, "title")).join(", ") }) : "",
    d.elements.length ? t("Elemente: {names}", { names: d.elements.map((x) => name(elements, x, "name")).join(", ") }) : "",
    d.decided_at ? t("angenommen am {date}", { date: new Date(d.decided_at).toLocaleDateString(locale()) }) : "",
  ].filter(Boolean);
  $("decision-refs").textContent = refs.join(" · ");
  $("decision-markdown").href = path("decisions", d.id, "markdown");
}

async function createDecision() {
  const title = $("decision-title").value.trim();
  if (!title) { say(t("Bitte einen Titel angeben.")); $("decision-title").focus(); return; }
  const result = await api(path("decisions"), { method: "POST", body: JSON.stringify({
    title, status: $("decision-status").value, context: $("decision-context").value, decision: $("decision-text").value,
    consequences: $("decision-consequences").value, requirements: chosenRequirements($("decision-requirements")),
  }) });
  decisions = result.decisions;
  edit(result.decision.id);
  say(t("{adr} angelegt.", { adr: adrLabel(result.decision.id) }));
}

async function save() {
  if (creating) { await createDecision(); return; }
  const d = decisions.find((x) => x.id === editing);
  if (!d) return;
  const values = {
    title: $("decision-title").value, status: $("decision-status").value, superseded_by: $("decision-superseded").value,
    context: $("decision-context").value, decision: $("decision-text").value, consequences: $("decision-consequences").value,
    requirements: chosenRequirements($("decision-requirements")),
  };
  const body = changedFields(d, values, ["title", "status", "superseded_by", "context", "decision", "consequences", "requirements"]);
  if (!body) { dirty = false; say(t("Keine Änderungen.")); return; }
  if ("decision" in body) { body.text = body.decision; delete body.decision; } // update_decision names it `text`
  const result = await api(path("decisions", d.id), { method: "PATCH", body: JSON.stringify(body) });
  decisions = result.decisions;
  edit(d.id);
  say(result.decision.status === "accepted" ? t("{adr} gespeichert – angenommen: Assistent und Review beachten sie jetzt.", { adr: adrLabel(d.id) }) : t("{adr} gespeichert.", { adr: adrLabel(d.id) }));
}

/** Load and show the decisions of the open project; `open` selects one in the editor. */
export async function loadDecisions(open = null) {
  if (!currentProject()) return;
  const [listed, driverList, model, scenarioList] = await Promise.all([
    api(path("decisions")), api(path("drivers")), api(path("elements")), api(path("scenarios")),
  ]);
  decisions = listed.decisions;
  drivers = driverList.drivers;
  elements = model.elements || [];
  scenarios = scenarioList.scenarios;
  say("");
  if ((creating || dirty) && open == null) { render(); return; } // late data must not wipe what the user has already typed
  edit(open ?? (decisions.some((d) => d.id === editing) ? editing : null));
}

export function initDecisionPanel() {
  const list = $("decisions-list");
  const open = (id) => { if (id !== editing && confirmDiscard(dirty)) edit(id); };
  list.addEventListener("click", (e) => { const c = e.target.closest(".md-card"); if (c) open(c.dataset.id); });
  bindListKeys(list, (delta) => {
    const next = stepId(visible().map((d) => d.id), editing, delta);
    if (next) { open(next); list.querySelector(`.md-card[data-id="${next}"]`)?.focus(); }
  }, open);
  $("decisions-filter").addEventListener("input", () => { say(""); render(); });
  $("decision-add").addEventListener("click", startNew);
  const form = $("decision-form");
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("change", () => { dirty = true; });
  form.addEventListener("submit", (e) => { e.preventDefault(); guarded(save); });
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); if (creating) { dirty = false; startNew(); } else edit(editing); } });
  $("decision-close").addEventListener("click", () => { if (confirmDiscard(dirty)) edit(null); });
  $("decision-delete").addEventListener("click", () => guarded(async () => {
    const d = decisions.find((x) => x.id === editing);
    if (!d || !deleteGate("decision", d).allowed) return;
    if (!confirm(t("{adr} „{title}“ endgültig löschen?", { adr: adrLabel(d.id), title: d.title }))) return;
    decisions = (await api(path("decisions", d.id), { method: "DELETE" })).decisions;
    edit(null);
  }));
}
