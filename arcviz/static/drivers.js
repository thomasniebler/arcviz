// Architecture drivers (spec: docs/architecture/specs/architecture-drivers.md): labels, the ATAM priority order
// (importance, then risk, then the list order) and the moves inside a priority class. Pure module: no DOM.

import { N_, t } from "./i18n.js";

export const DRIVER_KIND_LABELS = {
  quality_goal: N_("Qualitätsziel"),
  constraint: N_("Randbedingung"),
  requirement: N_("Anforderung"),
  risk: N_("Risiko"),
};
export const LEVEL_LABELS = { high: N_("hoch"), medium: N_("mittel"), low: N_("niedrig") };
const LEVELS = ["high", "medium", "low"];
const SHORT = { high: "H", medium: "M", low: "L" };

export function driverKindLabel(kind) { return DRIVER_KIND_LABELS[kind] ? t(DRIVER_KIND_LABELS[kind]) : kind; }

/** The level in words (hoch/mittel/niedrig); an unknown level as it is. */
export function levelWord(level) { return LEVEL_LABELS[level] ? t(LEVEL_LABELS[level]) : level; }

export const importanceLabel = (level) => t("Wichtigkeit {level}", { level: levelWord(level) });
export const riskLabel = (level) => t("Risiko {level}", { level: levelWord(level) });

/** "H/M": business importance / technical risk, as in an ATAM utility tree. */
export function priorityBadge(driver) { return `${SHORT[driver.importance] || "?"}/${SHORT[driver.risk] || "?"}`; }

/** Tooltip of the badge. */
export function priorityTitle(driver) {
  return t("Wichtigkeit {importance}, Risiko {risk}", { importance: levelWord(driver.importance), risk: levelWord(driver.risk) });
}

const rank = (level) => { const n = LEVELS.indexOf(level); return n === -1 ? LEVELS.length : n; };
const sameClass = (a, b) => a.importance === b.importance && a.risk === b.risk;

/** The drivers by priority: importance first, then risk; the list order inside a class (a stable sort). */
export function sortedDrivers(drivers) {
  return [...(drivers || [])].sort((a, b) => rank(a.importance) - rank(b.importance) || rank(a.risk) - rank(b.risk));
}

/** The list position to PATCH so that the driver swaps with its neighbour in its priority class (`dir` -1 = up, +1 =
 * down); null when it is first or last of its class. `move_driver` removes the driver first, so the neighbour's own
 * list index is the target in both directions. */
export function moveTarget(drivers, id, dir) {
  const list = drivers || [];
  const ranked = sortedDrivers(list);
  const at = ranked.findIndex((d) => d.id === id);
  const neighbour = at === -1 ? null : ranked[at + dir];
  if (!neighbour || !sameClass(neighbour, ranked[at])) return null;
  return list.findIndex((d) => d.id === neighbour.id);
}

/** The PATCH body of an edited driver: only what changed; null when nothing did. */
export function driverChanges(driver, edited) {
  const body = {};
  for (const key of ["title", "kind", "importance", "risk", "description"]) {
    const value = key === "title" || key === "description" ? (edited[key] ?? "").trim() : edited[key];
    if (value !== undefined && value !== (driver[key] ?? "")) body[key] = value;
  }
  const elements = edited.elements;
  if (elements && (elements.length !== (driver.elements || []).length || elements.some((e, i) => e !== driver.elements[i]))) body.elements = elements;
  const reqs = edited.requirements;
  const before = driver.requirements || [];
  if (reqs && (reqs.length !== before.length || reqs.some((r, i) => r !== before[i]))) body.requirements = reqs;
  return Object.keys(body).length ? body : null;
}

/** "betrifft: API, Datenbank" with the elements' names; "" for none. Unknown ids are shown as they are. */
export function concernsText(driver, elements) {
  const names = (driver.elements || []).map((id) => (elements || []).find((e) => e.id === id)?.name || id);
  return names.length ? t("betrifft: {names}", { names: names.join(", ") }) : "";
}
