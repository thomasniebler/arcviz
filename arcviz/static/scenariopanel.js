// The scenarios tab of the Architektur page (specs: docs/architecture/specs/decision-support.md and
// architecture-workspace.md): quality scenarios in the six ATAM parts as a filterable card list grouped by the driver
// they serve (core scenarios first) with one editor beside it (the selected scenario, or a new one); the what-if
// analysis of the selected scenario (nothing stored) and, per option, a decision draft or a preview turn in the chat.
import { api } from "./api.js";
import { $, option } from "./dom.js";
import { SCENARIO_KIND_LABELS, SCENARIO_PARTS, adrLabel, changedFields, decisionFromAnalysis, effectLabel, groupScenarios, scenarioKindLabel, scenarioLine } from "./decisions.js";
import { sortedDrivers } from "./drivers.js";
import { t } from "./i18n.js";
import { RETIRED_LABEL, deleteGate, isRetired, lifecycleItems, retireLabel, toggledStatus } from "./lifecycle.js";
import { filterItems, stepId } from "./listnav.js";
import { bindListKeys, card, chip, confirmDiscard, hideIfEmpty, markSelected } from "./mdpane.js";
import { currentProject } from "./navigator.js";
import { chosenRequirements, fillRequirementSelect } from "./requirementpicker.js";
import { requirementsText } from "./requirements.js";

let scenarios = [];
let drivers = [];
let elements = [];
let selected = null; // the id of the scenario in the editor, null = a new one
let dirty = false;
let hooks = { onDecided: () => {}, onApply: () => {} };

function path(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

function say(text) { $("scenarios-status").textContent = text || ""; }

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, className, handler, title) {
  const b = el("button", `small ${className}`, text);
  b.type = "button";
  if (title) b.title = title;
  b.addEventListener("click", () => guarded(handler));
  return b;
}

const driverTitle = (id) => drivers.find((d) => d.id === id)?.title ?? id;
const elementName = (id) => elements.find((e) => e.id === id)?.name ?? id;

// ---- the editor ----

const byId = (id) => scenarios.find((s) => s.id === id) ?? null;

/** Whether the editor holds changes that are not stored. */
export function isScenarioDirty() { return dirty; }

function fillForm(s) {
  selected = s?.id ?? null;
  $("scenario-id").value = s?.id || "";
  $("scenario-form-heading").textContent = s ? t("Szenario bearbeiten ({id})", { id: s.id }) : t("Neues Szenario");
  $("scenario-title").value = s?.title || "";
  $("scenario-kind").value = s?.kind || "usage";
  const driverSelect = $("scenario-driver");
  driverSelect.innerHTML = "";
  driverSelect.appendChild(option("", t("— kein Treiber —"), !s?.driver));
  // a retired driver is no target for new links (it stays selectable where the scenario points to it already)
  for (const d of sortedDrivers(drivers)) if (!isRetired(d) || d.id === s?.driver) driverSelect.appendChild(option(d.id, isRetired(d) ? `${d.title} (${t(RETIRED_LABEL)})` : d.title, d.id === s?.driver));
  $("scenario-core").checked = Boolean(s?.core);
  fillRequirementSelect($("scenario-requirements"), s?.requirements || []).then(() => hideIfEmpty($("scenario-requirements"))).catch(() => { $("scenario-requirements").disabled = true; });
  for (const [key] of SCENARIO_PARTS) $(`scenario-part-${key}`).value = s?.[key] || "";
  $("scenario-save").textContent = s ? t("Speichern") : t("Anlegen");
  for (const id of ["scenario-analyse", "scenario-retire", "scenario-delete"]) $(id).hidden = !s;
  if (s) {
    $("scenario-retire").textContent = retireLabel(s);
    const gate = deleteGate("scenario", s);
    $("scenario-delete").disabled = !gate.allowed;
    $("scenario-delete").title = gate.reason;
  }
  $("scenario-analysis").replaceChildren();
  dirty = false;
  markSelected($("scenarios-list"), selected);
}

function open(id) {
  if (id === selected || !confirmDiscard(dirty)) return;
  fillForm(byId(id));
}

function startNew() {
  if (!confirmDiscard(dirty)) return;
  fillForm(null);
  $("scenario-title").focus();
}

function formValues() {
  const values = { title: $("scenario-title").value, kind: $("scenario-kind").value, driver: $("scenario-driver").value, core: $("scenario-core").checked, requirements: chosenRequirements($("scenario-requirements")) };
  for (const [key] of SCENARIO_PARTS) values[key] = $(`scenario-part-${key}`).value;
  return values;
}

async function save() {
  const values = formValues();
  if (!values.title.trim()) { say(t("Bitte einen Titel angeben.")); $("scenario-title").focus(); return; }
  if (values.core && !values.driver) { say(t("Ein Kernszenario gehört zu einem Treiber: bitte einen Treiber wählen.")); return; }
  let result;
  if (selected) {
    const body = changedFields(byId(selected), values, ["title", "kind", "driver", "core", "requirements", ...SCENARIO_PARTS.map(([k]) => k)]);
    if (!body) { dirty = false; say(t("Keine Änderungen.")); return; }
    result = await api(path("scenarios", selected), { method: "PATCH", body: JSON.stringify(body) });
  } else {
    const body = { ...values, title: values.title.trim() };
    if (!body.driver) delete body.driver;
    result = await api(path("scenarios"), { method: "POST", body: JSON.stringify(body) });
  }
  scenarios = result.scenarios;
  renderList();
  fillForm(byId(result.scenario.id));
  say(t("„{title}“ gespeichert.", { title: result.scenario.title }));
}

// ---- the analysis ----

function renderAnalysis(box, scenario, analysis) {
  box.replaceChildren();
  if (!analysis.llm?.ok && !analysis.options?.length) {
    box.appendChild(el("p", "muted", t("Analyse fehlgeschlagen: {error}", { error: analysis.llm?.error || t("keine Optionen") })));
    return;
  }
  if (analysis.summary) box.appendChild(el("p", "analysis-summary", analysis.summary));
  const facts = el("ul", "analysis-facts");
  for (const d of analysis.drivers || []) {
    facts.appendChild(el("li", `effect-${d.effect}`, t("Treiber „{driver}“: {effect} – {reason}", { driver: driverTitle(d.driver), effect: effectLabel(d.effect), reason: d.reason })));
  }
  for (const e of analysis.elements || []) facts.appendChild(el("li", "", t("Betrifft „{element}“: {reason}", { element: elementName(e.element), reason: e.reason })));
  for (const p of analysis.sensitivity_points || []) facts.appendChild(el("li", "", t("Sensitivitätspunkt: {point}", { point: p })));
  for (const tradeoff of analysis.tradeoffs || []) facts.appendChild(el("li", "", t("Zielkonflikt: {tradeoff}", { tradeoff })));
  if (facts.children.length) box.appendChild(facts);
  const options = el("div", "analysis-options");
  analysis.options.forEach((o, index) => {
    const card = el("div", `analysis-option${analysis.recommended === index ? " recommended" : ""}`);
    card.dataset.option = String(index);
    card.appendChild(el("strong", "", `${index + 1}. ${o.title}${analysis.recommended === index ? ` ${t("(empfohlen)")}` : ""}`));
    if (o.description) card.appendChild(el("p", "", o.description));
    const points = (label, items, cls) => {
      if (!items.length) return;
      card.appendChild(el("span", `points-label ${cls}`, label));
      const list = el("ul", `points ${cls}`);
      for (const item of items) list.appendChild(el("li", "", item));
      card.appendChild(list);
    };
    points(t("Pro"), o.pros, "pros");
    points(t("Contra"), o.cons, "cons");
    const actions = el("div", "option-actions");
    actions.appendChild(button(t("Als Entscheidung festhalten"), "edit-only option-decide", async () => {
      const result = await api(path("decisions"), { method: "POST", body: JSON.stringify(decisionFromAnalysis(scenario, analysis, index)) });
      say(t("Entwurf {adr} angelegt.", { adr: adrLabel(result.decision.id) }));
      hooks.onDecided(result.decision.id);
    }, t("Legt einen bearbeitbaren Entscheidungsentwurf (ADR) mit allen Optionen an; diese gilt als gewählt")));
    if (o.instruction) {
      actions.appendChild(button(t("Als Vorschlag umsetzen"), "edit-only option-apply", () => hooks.onApply(o.instruction), o.instruction));
    }
    card.appendChild(actions);
    options.appendChild(card);
  });
  box.appendChild(options);
}

async function analyse(scenario, box) {
  box.replaceChildren(el("p", "muted", t("Analysiere … (ein Modellaufruf, nichts wird gespeichert)")));
  const analysis = await api(path("scenarios", scenario.id, "analysis"), { method: "POST" });
  renderAnalysis(box, scenario, analysis);
}

// ---- the list ----

function haystack(s) {
  return [s.title, scenarioKindLabel(s.kind), s.driver ? driverTitle(s.driver) : "", scenarioLine(s)].join(" ");
}

function visibleScenarios() { return filterItems(lifecycleItems(scenarios, $("scenarios-show-retired").checked), $("scenarios-filter").value, haystack); }

function renderList() {
  const list = $("scenarios-list");
  list.replaceChildren();
  $("scenarios-empty").classList.toggle("hidden", scenarios.length > 0);
  const shown = visibleScenarios();
  for (const group of groupScenarios(shown, sortedDrivers(drivers))) {
    const section = el("section", "scenario-group");
    section.appendChild(el("h5", "", group.driver ? t("Treiber: {title}", { title: group.driver.title }) : t("Ohne Treiber")));
    for (const s of group.scenarios) {
      // Built with DOM APIs: all texts are free-form and must never reach innerHTML.
      const item = el("article", `scenario-item${isRetired(s) ? " retired" : ""}`);
      item.dataset.scenarioId = s.id;
      item.appendChild(card({
        id: s.id, lead: el("span", "scenario-star", s.core ? "★" : ""), title: s.title,
        chips: [chip(scenarioKindLabel(s.kind), "accent"), ...(isRetired(s) ? [chip(t(RETIRED_LABEL), "")] : [])],
        meta: [scenarioLine(s), requirementsText(s)].filter(Boolean).join(" · "),
      }));
      section.appendChild(item);
    }
    list.appendChild(section);
  }
  markSelected(list, selected);
  if ($("scenarios-filter").value.trim()) say(t("{shown} von {total} Szenarien", { shown: shown.length, total: scenarios.length }));
}

/** Load and show the scenarios of the open project (when the tab is shown). */
export async function loadScenarios() {
  if (!currentProject()) return;
  const [listed, driverList, model] = await Promise.all([api(path("scenarios")), api(path("drivers")), api(path("elements"))]);
  scenarios = listed.scenarios;
  drivers = driverList.drivers;
  elements = model.elements || [];
  say("");
  renderList();
  if (!dirty) fillForm(byId(selected)); // data arriving late must not wipe what the user has already typed
}

export function initScenarioPanel(callbacks) {
  hooks = { ...hooks, ...callbacks };
  const kind = $("scenario-kind");
  for (const value of Object.keys(SCENARIO_KIND_LABELS)) kind.appendChild(option(value, scenarioKindLabel(value)));
  const parts = $("scenario-parts");
  for (const [key, label, hint] of SCENARIO_PARTS) {
    const field = el("label", "scenario-part field-col");
    field.appendChild(el("span", "", t(label)));
    const input = el("textarea");
    input.id = `scenario-part-${key}`;
    input.rows = key === "stimulus" || key === "response" ? 2 : 1;
    input.maxLength = 500;
    input.placeholder = t(hint);
    field.appendChild(input);
    parts.appendChild(field);
  }
  const list = $("scenarios-list");
  list.addEventListener("click", (e) => { const c = e.target.closest(".md-card"); if (c) open(c.dataset.id); });
  bindListKeys(list, (delta) => {
    const next = stepId(groupScenarios(visibleScenarios(), sortedDrivers(drivers)).flatMap((g) => g.scenarios.map((s) => s.id)), selected, delta);
    if (next) { open(next); list.querySelector(`.md-card[data-id="${next}"]`)?.focus(); }
  }, open);
  $("scenarios-filter").addEventListener("input", () => { say(""); renderList(); });
  $("scenario-add").addEventListener("click", startNew);
  const form = $("scenario-form");
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("change", () => { dirty = true; });
  form.addEventListener("submit", (e) => { e.preventDefault(); guarded(save); });
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); fillForm(byId(selected)); } });
  $("scenario-cancel").addEventListener("click", () => fillForm(byId(selected)));
  $("scenario-analyse").addEventListener("click", () => guarded(() => analyse(byId(selected), $("scenario-analysis"))));
  $("scenarios-show-retired").addEventListener("change", () => { say(""); renderList(); });
  $("scenario-retire").addEventListener("click", () => guarded(async () => {
    const s = byId(selected);
    if (!s || !confirmDiscard(dirty)) return;
    const result = await api(path("scenarios", s.id), { method: "PATCH", body: JSON.stringify({ status: toggledStatus(s) }) });
    scenarios = result.scenarios;
    renderList();
    fillForm(byId(s.id));
    say(isRetired(byId(s.id)) ? t("Außer Kraft gesetzt: das Szenario fließt nicht mehr in Prompts und Prüfungen ein.") : t("Wieder aktiv."));
  }));
  $("scenario-delete").addEventListener("click", () => guarded(async () => {
    const s = byId(selected);
    if (!s || !deleteGate("scenario", s).allowed) return;
    if (!confirm(t("Szenario „{title}“ endgültig löschen? Entscheidungen, die daraus entstanden sind, bleiben.", { title: s.title }))) return;
    scenarios = (await api(path("scenarios", s.id), { method: "DELETE" })).scenarios;
    renderList();
    fillForm(null);
  }));
}
