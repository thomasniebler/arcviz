// Graded deletion of the architecture objects (spec: docs/architecture/specs/graded-deletion.md, ADR-0198): drivers and
// scenarios are retired before they can be deleted, a decision only when it is not accepted, requirements always.
// Pure module: no DOM.
import { N_, t } from "./i18n.js";

export const RETIRED_LABEL = N_("Außer Kraft"); // t() it where it is shown

export function isRetired(item) { return item?.status === "retired"; }

/** The items to list: retired ones only when `showRetired`; the order is kept. */
export function lifecycleItems(items, showRetired) {
  return showRetired ? items : (items || []).filter((i) => !isRetired(i));
}

/** The label of the button that retires an active driver or scenario or reactivates a retired one. */
export function retireLabel(item) { return isRetired(item) ? t("Wieder aktivieren") : t("Außer Kraft setzen"); }

/** The status a click on that button sets. */
export function toggledStatus(item) { return isRetired(item) ? "active" : "retired"; }

/** Whether `item` of `kind` ("driver", "scenario", "decision", "requirement") may be deleted now, and why not. */
export function deleteGate(kind, item) {
  if (!item) return { allowed: false, reason: "" };
  if (kind === "driver" || kind === "scenario") {
    return isRetired(item) ? { allowed: true, reason: "" } : { allowed: false, reason: t("Erst außer Kraft setzen, dann löschen.") };
  }
  if (kind === "decision" && item.status === "accepted") {
    return { allowed: false, reason: t("Angenommene Entscheidungen erst verwerfen oder ersetzen, dann löschen.") };
  }
  return { allowed: true, reason: "" };
}

/** The confirmation of deleting one requirement. */
export function requirementDeleteConfirm(requirement) {
  return t("Anforderung {id} löschen? Sie wird auch aus Treibern, Szenarien und Entscheidungen entfernt, die sie nennen.", { id: requirement.id });
}

/** The confirmation of deleting the selected requirements; names the count and how many of them are referenced. */
export function requirementsDeleteConfirm(rows, ids) {
  const referenced = ids.filter((id) => {
    const coverage = rows.find((r) => r.id === id)?.coverage;
    return coverage != null && coverage !== "uncovered";
  }).length;
  // `coverage` is only known for refined requirements, so this is a lower bound
  const used = referenced ? " " + t("Mindestens {count} davon werden von Treibern, Szenarien oder Entscheidungen genannt; die Verweise entfallen.", { count: referenced }) : "";
  const head = ids.length === 1 ? t("1 Anforderung löschen?") : t("{count} Anforderungen löschen?", { count: ids.length });
  return `${head}${used} ${t("Das lässt sich nicht rückgängig machen.")}`;
}
