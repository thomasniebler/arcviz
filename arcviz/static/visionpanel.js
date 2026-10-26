// The vision tab of the Architektur page (spec: docs/architecture/specs/project-vision.md, ADR-0203): what the product
// is for, what success looks like and what it deliberately is not. One form; assistants and the steward only propose.
import { api } from "./api.js";
import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { confirmDiscard } from "./mdpane.js";
import { currentProject } from "./navigator.js";
import { MAX_ENTRIES, MAX_ENTRY, MAX_STATEMENT, bodyFromForm, changed, formValues, problems } from "./vision.js";

let vision = null;
let dirty = false;

function say(text) { $("vision-status").textContent = text || ""; }

const path = () => `/api/projects/${encodeURIComponent(currentProject())}`;

/** Whether the editor holds changes that are not stored. */
export function isVisionDirty() { return dirty; }

function fillForm() {
  const values = formValues(vision);
  $("vision-statement").value = values.statement;
  $("vision-goals").value = values.goals;
  $("vision-non-goals").value = values.non_goals;
  dirty = false;
}

function current() {
  return { statement: $("vision-statement").value, goals: $("vision-goals").value, non_goals: $("vision-non-goals").value };
}

function problemText(found) {
  if (found.includes("statement")) return t("Die Aussage darf höchstens {n} Zeichen haben.", { n: MAX_STATEMENT });
  if (found.includes("count")) return t("Höchstens {n} Einträge je Liste.", { n: MAX_ENTRIES });
  return t("Ein Eintrag darf höchstens {n} Zeichen haben.", { n: MAX_ENTRY });
}

async function save() {
  const body = bodyFromForm(current());
  const found = problems(body);
  if (found.length) { say(problemText(found)); return; }
  if (!changed(vision, current())) { dirty = false; say(t("Keine Änderungen.")); return; }
  const result = await api(`${path()}/vision`, { method: "PUT", body: JSON.stringify(body) });
  vision = result.project.vision ?? null;
  fillForm();
  say(t("Gespeichert."));
}

/** Load and show the vision of the open project (when the tab is shown). */
export async function loadVision() {
  if (!currentProject()) return;
  const result = await api(path());
  vision = result.project.vision ?? null;
  say("");
  if (!dirty) fillForm(); // data arriving late must not wipe what the user has already typed
}

export function initVisionPanel() {
  const form = $("vision-form");
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    save().catch((err) => say(err.message || String(err)));
  });
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); fillForm(); } });
  $("vision-cancel").addEventListener("click", () => { if (confirmDiscard(dirty)) fillForm(); });
}
