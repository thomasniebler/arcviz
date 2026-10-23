// A multi-select of the open project's requirements for the forms of drivers, scenarios and decisions (spec:
// docs/architecture/specs/requirements.md, ADR-0189): the links are stored on those objects, so their forms pick them. The
// list is read once per project and form opening (GET /requirements, up to three pages of 200) and cached.
import { api } from "./api.js";
import { option } from "./dom.js";
import { t } from "./i18n.js";
import { optionLabel, unknownIds } from "./requirements.js";
import { state } from "./state.js";

const cache = { project: null, rows: null };

async function rows() {
  const project = state.projectId;
  if (cache.project === project && cache.rows) return cache.rows;
  const found = [];
  for (let offset = 0; offset < 600; offset += 200) {
    const page = await api(`/api/projects/${encodeURIComponent(project)}/requirements?limit=200&offset=${offset}`);
    found.push(...page.requirements);
    if (found.length >= page.total) break;
  }
  cache.project = project;
  cache.rows = found;
  return found;
}

/** Forget the cached list (requirements were added, refined or removed). */
export function invalidateRequirementOptions() {
  cache.project = null;
  cache.rows = null;
}

/** Fill `select` (a `<select multiple>`) with the requirements and select `selected`; unknown ids stay selectable. */
export async function fillRequirementSelect(select, selected = []) {
  invalidateRequirementOptions(); // a form is opened rarely: always current
  const list = await rows();
  select.replaceChildren(
    ...unknownIds(selected, list).map((id) => option(id, `${id} · ${t("(nicht mehr vorhanden)")}`, true)),
    ...list.map((r) => option(r.id, optionLabel(r), selected.includes(r.id))),
  );
  select.size = Math.min(6, Math.max(2, list.length));
  select.title = t("Anforderungen, denen das Objekt dient (Strg/⌘ für mehrere)");
  return select;
}

/** A new `<select multiple>` for a list row, filled in the background. */
export function requirementSelect(selected, className) {
  const select = document.createElement("select");
  select.multiple = true;
  select.className = className;
  select.setAttribute("aria-label", t("Anforderungen"));
  fillRequirementSelect(select, selected).catch(() => { select.disabled = true; });
  return select;
}

export function chosenRequirements(select) {
  return [...select.selectedOptions].map((o) => o.value);
}
