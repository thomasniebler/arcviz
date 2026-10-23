// The stakeholders tab of the Architektur page (specs: docs/architecture/specs/decision-support.md and
// architecture-workspace.md): who has a stake in the architecture, their role, concerns, influence and the drivers they
// care about, as a filterable card list with one editor beside it (the selected stakeholder, or a new one).
import { api } from "./api.js";
import { $, option } from "./dom.js";
import { changedFields } from "./decisions.js";
import { LEVEL_LABELS, levelWord, sortedDrivers } from "./drivers.js";
import { t } from "./i18n.js";
import { filterItems, stepId } from "./listnav.js";
import { bindListKeys, card, chip, confirmDiscard, hideIfEmpty, markSelected } from "./mdpane.js";
import { currentProject } from "./navigator.js";

let stakeholders = [];
let drivers = [];
let selected = null; // the id of the stakeholder in the editor, null = a new one
let dirty = false;

function path(...parts) {
  return `/api/projects/${[currentProject(), ...parts].map((p) => encodeURIComponent(p)).join("/")}`;
}

function say(text) { $("stakeholders-status").textContent = text || ""; }

async function guarded(action) {
  try { await action(); } catch (err) { say(err.message || String(err)); }
}

const byId = (id) => stakeholders.find((s) => s.id === id) ?? null;
const influenceLabel = (v) => t("Einfluss {level}", { level: levelWord(v) });

/** Whether the editor holds changes that are not stored. */
export function isStakeholderDirty() { return dirty; }

function visible() { return filterItems(stakeholders, $("stakeholders-filter").value, (s) => [s.name, s.role, s.concerns].join(" ")); }

function renderList() {
  const list = $("stakeholders-list");
  list.replaceChildren();
  $("stakeholders-empty").classList.toggle("hidden", stakeholders.length > 0);
  const shown = visible();
  for (const s of shown) {
    const li = document.createElement("li");
    li.className = "stakeholder-row";
    li.dataset.stakeholderId = s.id;
    li.appendChild(card({ id: s.id, title: s.name, chips: [chip(s.role || t("ohne Rolle")), chip(influenceLabel(s.influence))], meta: s.concerns || "" }));
    list.appendChild(li);
  }
  markSelected(list, selected);
  if ($("stakeholders-filter").value.trim()) say(t("{shown} von {total} Stakeholdern", { shown: shown.length, total: stakeholders.length }));
}

function fillForm(s) {
  selected = s?.id ?? null;
  $("stakeholder-form-heading").textContent = s ? t("Stakeholder bearbeiten ({id})", { id: s.id }) : t("Neuer Stakeholder");
  $("stakeholder-name").value = s?.name ?? "";
  $("stakeholder-role").value = s?.role ?? "";
  const influence = $("stakeholder-influence");
  influence.replaceChildren(...Object.keys(LEVEL_LABELS).map((v) => option(v, influenceLabel(v), v === (s?.influence ?? "medium"))));
  $("stakeholder-concerns").value = s?.concerns ?? "";
  const cares = $("stakeholder-drivers");
  cares.replaceChildren(...sortedDrivers(drivers).map((d) => option(d.id, d.title, (s?.drivers || []).includes(d.id))));
  cares.size = Math.min(4, Math.max(2, drivers.length));
  cares.title = t("Treiber, die ihnen wichtig sind (Strg/⌘ für mehrere)");
  hideIfEmpty(cares);
  $("stakeholder-save").textContent = s ? t("Speichern") : t("Anlegen");
  $("stakeholder-delete").classList.toggle("hidden", !s);
  dirty = false;
  markSelected($("stakeholders-list"), selected);
}

function open(id) {
  if (id === selected || !confirmDiscard(dirty)) return;
  fillForm(byId(id));
}

function startNew() {
  if (!confirmDiscard(dirty)) return;
  fillForm(null);
  $("stakeholder-name").focus();
}

function show(result, keep = selected) {
  stakeholders = result.stakeholders;
  renderList();
  fillForm(byId(keep));
}

async function save() {
  const s = byId(selected);
  const values = {
    name: $("stakeholder-name").value, role: $("stakeholder-role").value, influence: $("stakeholder-influence").value,
    concerns: $("stakeholder-concerns").value, drivers: [...$("stakeholder-drivers").selectedOptions].map((o) => o.value),
  };
  if (!values.name.trim()) { say(t("Bitte einen Namen angeben.")); $("stakeholder-name").focus(); return; }
  if (s) {
    const body = changedFields(s, values, ["name", "role", "influence", "concerns", "drivers"]);
    if (!body) { dirty = false; say(t("Keine Änderungen.")); return; }
    show(await api(path("stakeholders", s.id), { method: "PATCH", body: JSON.stringify(body) }));
    say(t("Gespeichert."));
  } else {
    const result = await api(path("stakeholders"), { method: "POST", body: JSON.stringify({ ...values, name: values.name.trim(), role: values.role.trim(), concerns: values.concerns.trim() }) });
    show(result, result.stakeholder.id);
    say(t("„{title}“ angelegt.", { title: result.stakeholder.name }));
  }
}

/** Load and show the stakeholders of the open project (when the tab is shown). */
export async function loadStakeholders() {
  if (!currentProject()) return;
  const [listed, driverList] = await Promise.all([api(path("stakeholders")), api(path("drivers"))]);
  stakeholders = listed.stakeholders;
  drivers = driverList.drivers;
  say("");
  renderList();
  if (!dirty) fillForm(byId(selected)); // data arriving late must not wipe what the user has already typed
}

export function initStakeholderPanel() {
  const list = $("stakeholders-list");
  list.addEventListener("click", (e) => { const c = e.target.closest(".md-card"); if (c) open(c.dataset.id); });
  bindListKeys(list, (delta) => {
    const next = stepId(visible().map((s) => s.id), selected, delta);
    if (next) { open(next); list.querySelector(`.md-card[data-id="${next}"]`)?.focus(); }
  }, open);
  $("stakeholders-filter").addEventListener("input", () => { say(""); renderList(); });
  $("stakeholder-add").addEventListener("click", startNew);
  const form = $("stakeholder-form");
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("change", () => { dirty = true; });
  form.addEventListener("submit", (e) => { e.preventDefault(); guarded(save); });
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); fillForm(byId(selected)); } });
  $("stakeholder-cancel").addEventListener("click", () => fillForm(byId(selected)));
  $("stakeholder-delete").addEventListener("click", () => guarded(async () => {
    const s = byId(selected);
    if (!s || !confirm(t("Stakeholder „{name}“ löschen?", { name: s.name }))) return;
    show(await api(path("stakeholders", s.id), { method: "DELETE" }), null);
    say(t("Gelöscht."));
  }));
}
