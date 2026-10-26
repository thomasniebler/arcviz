// The drivers tab of the Architektur page (specs: docs/architecture/specs/architecture-drivers.md and
// architecture-workspace.md): the open project's architecture drivers by priority (ATAM: business importance ×
// technical risk) as a filterable card list with one editor beside it (the selected driver, or a new one), reorder
// inside a priority class and delete. The page itself (tabs, showing, hiding) is archview.js.
import { api } from "./api.js";
import { $, option } from "./dom.js";
import { DRIVER_KIND_LABELS, LEVEL_LABELS, concernsText, driverChanges, driverKindLabel, importanceLabel, moveTarget, priorityBadge, priorityTitle, riskLabel, sortedDrivers } from "./drivers.js";
import { t } from "./i18n.js";
import { RETIRED_LABEL, deleteGate, isRetired, lifecycleItems, retireLabel, toggledStatus } from "./lifecycle.js";
import { filterItems, stepId } from "./listnav.js";
import { bindListKeys, card, chip, confirmDiscard, hideIfEmpty, markSelected } from "./mdpane.js";
import { currentProject } from "./navigator.js";
import { chosenRequirements, fillRequirementSelect } from "./requirementpicker.js";
import { qualityLabel } from "./qualities.js";
import { requirementsText } from "./requirements.js";
import { state } from "./state.js";

let drivers = []; // GET /api/projects/{pid}/drivers of the open project while the panel is open
let elements = []; // the project's elements, to pick the ones a driver concerns
let selected = null; // the id of the driver in the editor, null = a new one
let dirty = false;

function path(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

function say(text) { $("drivers-status").textContent = text || ""; }

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); }
}

function kinds() { return state.config?.project_model?.driver_kinds || Object.keys(DRIVER_KIND_LABELS); }
function levels() { return state.config?.project_model?.driver_levels || Object.keys(LEVEL_LABELS); }
const driverById = (id) => drivers.find((d) => d.id === id) ?? null;
const chosenElements = () => [...$("driver-elements").selectedOptions].map((o) => o.value);

/** Whether the editor holds changes that are not stored. */
export function isDriverDirty() { return dirty; }

function fillSelect(id, values, labelOf, selectedValue) {
  const s = $(id);
  s.replaceChildren(...values.map((v) => option(v, labelOf(v), v === selectedValue)));
}

function haystack(d) { return [d.title, d.description, driverKindLabel(d.kind), concernsText(d, elements)].join(" "); }

function visibleDrivers() {
  return filterItems(sortedDrivers(lifecycleItems(drivers, $("drivers-show-retired").checked)), $("drivers-filter").value, haystack);
}

function renderList() {
  const list = $("drivers-list");
  list.replaceChildren();
  $("drivers-empty").classList.toggle("hidden", drivers.length > 0);
  const shown = visibleDrivers();
  for (const driver of shown) {
    const li = document.createElement("li");
    li.className = `driver-row${isRetired(driver) ? " retired" : ""}`;
    li.dataset.driverId = driver.id;
    const badge = document.createElement("span");
    badge.className = `driver-badge prio-${driver.importance}`;
    badge.textContent = priorityBadge(driver);
    badge.title = priorityTitle(driver);
    li.appendChild(card({
      id: driver.id, lead: badge, title: driver.title,
      chips: [chip(driverKindLabel(driver.kind), "accent"), ...(driver.qualities || []).map((q) => chip(qualityLabel(q), "ok")), ...(isRetired(driver) ? [chip(t(RETIRED_LABEL), "")] : [])], // the badge carries importance and risk (its title spells them out)
      meta: [concernsText(driver, elements), requirementsText(driver)].filter(Boolean).join(" · "),
    }));
    list.appendChild(li);
  }
  markSelected(list, selected);
  const filtering = $("drivers-filter").value.trim() !== "";
  if (filtering) say(t("{shown} von {total} Treibern", { shown: shown.length, total: drivers.length }));
}

function fillForm(driver) {
  selected = driver?.id ?? null;
  $("driver-form-heading").textContent = driver ? t("Treiber bearbeiten ({id})", { id: driver.id }) : t("Neuer Treiber");
  $("driver-title").value = driver?.title ?? "";
  fillSelect("driver-kind", kinds(), driverKindLabel, driver?.kind ?? "quality_goal");
  fillSelect("driver-importance", levels(), importanceLabel, driver?.importance ?? "medium");
  fillSelect("driver-risk", levels(), riskLabel, driver?.risk ?? "medium");
  $("driver-description").value = driver?.description ?? "";
  const concerns = $("driver-elements");
  concerns.replaceChildren(...elements.map((e) => option(e.id, e.name, (driver?.elements || []).includes(e.id))));
  concerns.size = Math.min(4, Math.max(2, elements.length));
  concerns.closest("label").classList.toggle("hidden", !elements.length); // nothing to pick: no empty box
  concerns.title = t("Betroffene Projektelemente (Strg/⌘ für mehrere)");
  fillRequirementSelect($("driver-requirements"), driver?.requirements || []).then(() => hideIfEmpty($("driver-requirements"))).catch(() => { $("driver-requirements").disabled = true; });
  $("driver-save").textContent = driver ? t("Speichern") : t("Anlegen");
  for (const id of ["driver-retire", "driver-delete", "driver-up", "driver-down"]) $(id).classList.toggle("hidden", !driver);
  if (driver) {
    $("driver-retire").textContent = retireLabel(driver);
    const gate = deleteGate("driver", driver);
    $("driver-delete").disabled = !gate.allowed;
    $("driver-delete").title = gate.reason;
    $("driver-up").disabled = moveTarget(drivers, driver.id, -1) == null;
    $("driver-down").disabled = moveTarget(drivers, driver.id, 1) == null;
  }
  dirty = false;
  markSelected($("drivers-list"), selected);
}

function open(id) {
  if (id === selected || !confirmDiscard(dirty)) return;
  fillForm(driverById(id));
}

function startNew() {
  if (!confirmDiscard(dirty)) return;
  fillForm(null);
  $("driver-title").focus();
}

function show(result, keep = selected) {
  drivers = result.drivers;
  renderList();
  fillForm(driverById(keep));
}

async function save() {
  const driver = driverById(selected);
  const values = {
    title: $("driver-title").value, kind: $("driver-kind").value, importance: $("driver-importance").value, risk: $("driver-risk").value,
    description: $("driver-description").value, elements: chosenElements(), requirements: chosenRequirements($("driver-requirements")),
  };
  if (!values.title.trim()) { say(t("Bitte einen Titel angeben.")); $("driver-title").focus(); return; }
  if (driver) {
    const body = driverChanges(driver, values);
    if (!body) { dirty = false; say(t("Keine Änderungen.")); return; }
    show(await api(path("drivers", driver.id), { method: "PATCH", body: JSON.stringify(body) }));
    say(t("Gespeichert."));
  } else {
    const result = await api(path("drivers"), { method: "POST", body: JSON.stringify({ ...values, title: values.title.trim() }) });
    show(result, result.driver.id);
    say(t("„{title}“ angelegt.", { title: result.driver.title }));
  }
}

/** Load and show the drivers of the open project (when the tab is shown). */
export async function loadDrivers() {
  if (!currentProject()) return;
  const [listed, model] = await Promise.all([api(path("drivers")), api(path("elements"))]);
  elements = model.elements || [];
  drivers = listed.drivers;
  say("");
  renderList();
  if (!dirty) fillForm(driverById(selected)); // data arriving late must not wipe what the user has already typed
}

export function initDriverPanel() {
  const list = $("drivers-list");
  list.addEventListener("click", (e) => { const c = e.target.closest(".md-card"); if (c) open(c.dataset.id); });
  bindListKeys(list, (delta) => {
    const next = stepId(visibleDrivers().map((d) => d.id), selected, delta);
    if (next) { open(next); list.querySelector(`.md-card[data-id="${next}"]`)?.focus(); }
  }, open);
  $("drivers-filter").addEventListener("input", () => { say(""); renderList(); });
  $("driver-add").addEventListener("click", startNew);
  const form = $("driver-form");
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("change", () => { dirty = true; });
  form.addEventListener("submit", (e) => { e.preventDefault(); guarded(save); });
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); fillForm(driverById(selected)); } });
  $("driver-cancel").addEventListener("click", () => fillForm(driverById(selected)));
  $("drivers-show-retired").addEventListener("change", () => { say(""); renderList(); });
  $("driver-retire").addEventListener("click", () => guarded(async () => {
    const driver = driverById(selected);
    if (!driver || !confirmDiscard(dirty)) return;
    show(await api(path("drivers", driver.id), { method: "PATCH", body: JSON.stringify({ status: toggledStatus(driver) }) }));
    say(isRetired(driverById(driver.id)) ? t("Außer Kraft gesetzt: der Treiber fließt nicht mehr in Prompts und Prüfungen ein.") : t("Wieder aktiv."));
  }));
  $("driver-delete").addEventListener("click", () => guarded(async () => {
    const driver = driverById(selected);
    if (!driver || !deleteGate("driver", driver).allowed) return;
    if (!confirm(t("Treiber „{title}“ endgültig löschen? Seine Szenarien bleiben ohne Treiber, Entscheidungen und Stakeholder verlieren den Verweis.", { title: driver.title }))) return;
    show(await api(path("drivers", driver.id), { method: "DELETE" }), null);
    say(t("Gelöscht."));
  }));
  const move = (dir) => () => guarded(async () => {
    const position = moveTarget(drivers, selected, dir);
    if (position == null || !confirmDiscard(dirty)) return;
    show(await api(path("drivers", selected), { method: "PATCH", body: JSON.stringify({ position }) }));
  });
  $("driver-up").addEventListener("click", move(-1));
  $("driver-down").addEventListener("click", move(1));
}
