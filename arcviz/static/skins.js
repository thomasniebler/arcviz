// UI skins (spec: docs/architecture/specs/ui-skins.md): the list shown in the top bar and the lookup of a
// stored choice. A skin restyles the UI chrome only; the diagram stage is never part of it (ADR-0049).
import { N_ } from "./i18n.js";

/** Ordered list of skins; `id` is the value of `data-skin` on <html>, `label` is shown in the selector (through `t()`). */
export const SKINS = [
  { id: "classic", label: N_("Klassisch") },
  { id: "mission-control", label: "Mission Control" },
  { id: "synthwave", label: "Synthwave" },
];

export const DEFAULT_SKIN = "classic";

export const SKIN_STORAGE_KEY = "arcviz.skin";

/** A known skin id, or the default for anything else (unknown, empty, not a string). */
export function resolveSkin(value) {
  return SKINS.some((s) => s.id === value) ? value : DEFAULT_SKIN;
}
