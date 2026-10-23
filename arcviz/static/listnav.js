// List helpers of the Architektur workspace (spec: docs/architecture/specs/architecture-workspace.md, ADR-0196): the
// filter of the card lists and the keyboard step through them. Pure: no DOM.
import { locale } from "./i18n.js";

/** The items whose `textOf(item)` contains every word of `query` (case-insensitive, any order); order is kept. */
export function filterItems(items, query, textOf) {
  const words = String(query ?? "").toLocaleLowerCase(locale()).split(/\s+/).filter(Boolean);
  if (!words.length) return items;
  return items.filter((item) => {
    const text = String(textOf(item)).toLocaleLowerCase(locale());
    return words.every((w) => text.includes(w));
  });
}

/** The id `delta` (+1/-1) steps from `current` in `ids`, clamped to the ends; without a (known) selection the first id
 * for +1 and the last for -1; `null` for an empty list. */
export function stepId(ids, current, delta) {
  if (!ids.length) return null;
  const at = ids.indexOf(current);
  if (at < 0) return delta > 0 ? ids[0] : ids[ids.length - 1];
  return ids[Math.min(ids.length - 1, Math.max(0, at + delta))];
}
