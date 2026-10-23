// Appearance: the UI skin selector in the top bar (spec: docs/architecture/specs/ui-skins.md). Switching
// only sets `data-skin` on <html>; the diagram is not re-rendered and its stage is not styled by a skin.
import { $, option } from "./dom.js";
import { t } from "./i18n.js";
import { SKINS, SKIN_STORAGE_KEY, resolveSkin } from "./skins.js";

let select = null;

/** Apply the skin `id` (unknown values fall back to the default) and return the applied id. */
export function applySkin(id) {
  const skin = resolveSkin(id);
  document.documentElement.dataset.skin = skin;
  if (select) select.value = skin;
  return skin;
}

function storedSkin() {
  try { return localStorage.getItem(SKIN_STORAGE_KEY); } catch { return null; }
}

/** Fill the selector, apply the stored skin, switch live on change and follow other tabs. */
export function initAppearance() {
  select = $("skin-select");
  for (const s of SKINS) select.appendChild(option(s.id, t(s.label)));
  applySkin(storedSkin());
  select.addEventListener("change", () => {
    const skin = applySkin(select.value);
    try { localStorage.setItem(SKIN_STORAGE_KEY, skin); } catch {}
  });
  window.addEventListener("storage", (e) => { if (e.key === SKIN_STORAGE_KEY) applySkin(e.newValue); });
}
