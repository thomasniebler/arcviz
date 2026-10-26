// ISO/IEC 25010 quality characteristics of scenarios (spec: docs/architecture/specs/iso25010-qualities.md, ADR-0202):
// labels, the one-or-two rule and the choice state of the editor. Pure module: no DOM.
import { N_, t } from "./i18n.js";

export const MAX_QUALITIES = 2;

// The nine characteristics of ISO/IEC 25010:2023 in the order of the standard (names as the server knows them).
export const QUALITY_LABELS = {
  functional_suitability: N_("Funktionale Eignung"),
  performance_efficiency: N_("Leistungseffizienz"),
  compatibility: N_("Kompatibilität"),
  interaction_capability: N_("Interaktionsfähigkeit"),
  reliability: N_("Zuverlässigkeit"),
  security: N_("Informationssicherheit"),
  maintainability: N_("Wartbarkeit"),
  flexibility: N_("Flexibilität"),
  safety: N_("Funktionssicherheit"),
};
export const QUALITY_NAMES = Object.keys(QUALITY_LABELS);

/** The label of a characteristic in the current language; an unknown name as it is. */
export const qualityLabel = (name) => (QUALITY_LABELS[name] ? t(QUALITY_LABELS[name]) : name);

/** `names` reduced to what the server accepts: known, each once, at most two, in the given order (a model's answer is
 * never trusted, the user can always pick from all nine). */
export function validQualities(names) {
  const kept = [];
  for (const name of Array.isArray(names) ? names : []) {
    if (QUALITY_NAMES.includes(name) && !kept.includes(name) && kept.length < MAX_QUALITIES) kept.push(name);
  }
  return kept;
}

/** The nine choices for the editor given the `selected` ones: `[{name, checked, disabled}]`; once two are chosen the
 * others are disabled, so nothing is dropped silently. */
export function qualityChoices(selected) {
  const chosen = validQualities(selected);
  return QUALITY_NAMES.map((name) => ({ name, checked: chosen.includes(name), disabled: !chosen.includes(name) && chosen.length >= MAX_QUALITIES }));
}

/** The characteristics after a click on `name`: toggled, and unchanged when it would be a third. */
export function toggledQuality(selected, name) {
  const chosen = validQualities(selected);
  if (chosen.includes(name)) return chosen.filter((n) => n !== name);
  return chosen.length >= MAX_QUALITIES || !QUALITY_NAMES.includes(name) ? chosen : [...chosen, name];
}

/** `Zuverlässigkeit, Informationssicherheit` for a scenario or a driver (its covered characteristics); "" for none. */
export const qualitiesText = (item) => (item?.qualities || []).map(qualityLabel).join(", ");

/** The scenario text sent to the suggestion route: title, kind and the six parts as typed. */
export function suggestionBody(values, parts) {
  const body = { title: values.title || "", kind: values.kind };
  for (const key of parts) if ((values[key] || "").trim()) body[key] = values[key];
  return body;
}
