// Internationalization of the page (spec: docs/architecture/specs/internationalization.md, ADR-0199). Pure: the German
// source text is the key, `t()` returns the English entry under `en` and the text itself otherwise, so a missing entry
// shows German, never a key. `initI18n` (called first by app.js) decides the language and loads the catalog.
export const LANGUAGES = ["de", "en"];
export const DEFAULT_LANGUAGE = "de";
export const STORAGE_KEY = "arcviz.lang";
export const LANGUAGE_LABELS = { de: "Deutsch", en: "English" };

let current = DEFAULT_LANGUAGE;
let catalog = {};

/** The language to use: a stored valid choice, else the first supported entry of the browser's list, else German. */
export function resolveLanguage(stored, preferred = []) {
  if (LANGUAGES.includes(stored)) return stored;
  for (const tag of preferred || []) {
    const lang = String(tag).toLowerCase().split("-")[0];
    if (LANGUAGES.includes(lang)) return lang;
  }
  return DEFAULT_LANGUAGE;
}

/** `{name}` placeholders replaced by `params` (an unknown name stays as written). */
export function interpolate(text, params) {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}

/** The text in the current language. */
export function t(text, params) {
  const out = current === "en" ? (catalog[text] ?? text) : text;
  return interpolate(out, params);
}

/** Marks a German text for the catalog without translating it, for texts kept in module-level tables: `t()` them when
 * they are shown (a module is evaluated before the language is known). */
export function N_(text) { return text; }

export function getLanguage() { return current; }

/** Sets the language and the catalog (tests, `initI18n`). */
export function setLanguage(lang, entries = {}) {
  current = LANGUAGES.includes(lang) ? lang : DEFAULT_LANGUAGE;
  catalog = entries;
}

/** The locale tag for `toLocaleString` and friends. */
export function locale() { return current === "en" ? "en-GB" : "de-DE"; }

/** Fetches `/static/locales/index.json` and every fragment it lists; fragments that fail are skipped. */
export async function loadCatalog(fetchFn) {
  const names = await (await fetchFn("/static/locales/index.json")).json();
  const parts = await Promise.all(names.map((n) => fetchFn(`/static/locales/en/${n}.json`).then((r) => r.json()).catch(() => ({}))));
  return Object.assign({}, ...parts);
}

const ATTRIBUTES = { "data-i18n-title": "title", "data-i18n-placeholder": "placeholder", "data-i18n-aria-label": "aria-label" };

/** Translates static markup below `root`: `data-i18n` (text content) and the attribute variants; the German text is the key. */
export function applyDom(root) {
  if (current === "de") return;
  for (const node of root.querySelectorAll("[data-i18n]")) {
    const key = node.dataset.i18n || node.textContent.trim();
    node.dataset.i18n = key;
    node.textContent = t(key);
  }
  for (const [marker, attribute] of Object.entries(ATTRIBUTES)) {
    for (const node of root.querySelectorAll(`[${marker}]`)) {
      const key = node.getAttribute(marker) || node.getAttribute(attribute) || "";
      node.setAttribute(attribute, t(key));
    }
  }
}

/** Decides and loads the language, sets `<html lang>` and translates the static markup. Never throws (German on a failure). */
export async function initI18n(env = globalThis) {
  let stored = null;
  try { stored = env.localStorage.getItem(STORAGE_KEY); } catch { /* storage blocked */ }
  const lang = resolveLanguage(stored, env.navigator?.languages || [env.navigator?.language]);
  let entries = {};
  if (lang === "en") {
    try { entries = await loadCatalog(env.fetch.bind(env)); } catch { /* German texts */ }
  }
  setLanguage(lang, entries);
  env.document.documentElement.lang = lang;
  applyDom(env.document);
  return lang;
}

/** Stores the user's choice; the caller reloads the page. */
export function storeLanguage(lang, storage = globalThis.localStorage) {
  try { storage.setItem(STORAGE_KEY, lang); } catch { /* storage blocked: the browser language applies */ }
}
