// Personal settings (spec: docs/architecture/specs/personal-settings.md): the dialog behind the user menu in the top
// bar. It opens and closes and holds the language choice (spec: internationalization.md); its other controls (skin
// selector, agent access, provider line) are initialised by the modules they belong to (appearance.js, tokenpanel.js,
// app.js).
import { $ } from "./dom.js";
import { LANGUAGES, LANGUAGE_LABELS, getLanguage, storeLanguage } from "./i18n.js";

export function initSettings() {
  const dialog = $("settings-dialog");
  $("btn-settings").addEventListener("click", () => dialog.showModal());
  $("settings-close").addEventListener("click", () => dialog.close());
  const select = $("language-select");
  select.replaceChildren(...LANGUAGES.map((lang) => Object.assign(document.createElement("option"), { value: lang, textContent: LANGUAGE_LABELS[lang] })));
  select.value = getLanguage();
  select.addEventListener("change", () => { storeLanguage(select.value); window.location.reload(); });
}
