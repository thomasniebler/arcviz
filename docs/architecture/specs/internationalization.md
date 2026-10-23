# Internationalization (German and English)

- Status: Accepted
- Issue: not yet filed
- ADR: [0199 The German source text is the message key](../adr/0199-german-source-text-as-message-key.md)

## Summary

The UI, the server's user-facing messages (rule and review findings, operation and API errors, templates, the login page,
the release banner) and the answer language of the LLM calls are available in German (`de`) and English (`en`).

## Goals

- A user picks the language in the personal settings; the choice is stored in the browser (`localStorage`, key
  `arcviz.lang`) and applies after a reload.
- Without a stored choice the browser's preferred languages decide (first `de*` or `en*` entry), otherwise German.
- Every API request carries `Accept-Language`; the server answers messages in that language. A request without the header
  (tests, scripts, MCP clients) gets German, as before.
- The assistants answer in the chosen language (a language line in every system prompt; the review, analysis and workshop
  calls name the language in their instructions).

## Non-goals

- The documentation under `docs/` stays English; the technical identifiers (ids, kinds, operation names, JSON schemas) stay
  untranslated. User content (diagram names, notes, transcripts, stored findings of earlier reviews) is never translated.
- No server-side per-user language setting (a later change may add one).
- No third language, no plural rules beyond what a sentence needs (texts avoid "(s)" by wording).

## Design

### Server (`arcviz/i18n.py`)

- `LANGUAGES = ("de", "en")`, `DEFAULT_LANGUAGE = "de"`; the current language is a `ContextVar`. `LanguageMiddleware` (pure
  ASGI, inside the request-context middleware) sets it from `Accept-Language` through `parse_accept_language`
  (q-values honoured, unknown languages skipped). Worker threads started with `run_in_threadpool` inherit it.
- `_(text, **params)` returns the English entry of the German `text` when the current language is `en` (catalog
  `arcviz/locales/en/*.json`, merged at import; the file name is the owning module), else `text`; `params` are applied with
  `str.format`. `language()` and `language_name()` (`"German"`/`"English"`, for prompts) read the context.
- Texts that are data rather than output (rule ids, categories, kinds) are not wrapped. A module-level constant that would be
  evaluated once at import stays a function or a lazy `_` call.
- Finding messages computed per request are translated when computed; stored data is not touched.

### Frontend (`static/i18n.js`, pure)

- `t(text, params)`: English entry of the German `text` when the language is `en`, else `text`; placeholders `{name}`.
  `setLanguage`, `getLanguage`, `resolveLanguage(stored, preferred)` and `loadCatalog(fetchFn)` (reads
  `/static/locales/index.json`, then every listed fragment `/static/locales/en/<area>.json`).
- `applyDom(root)` translates static markup: `data-i18n` (text content), `data-i18n-title`, `data-i18n-placeholder`,
  `data-i18n-aria-label`; the key is the German text. It runs once at boot for `en` (German is the markup).
- `app.js` awaits `initI18n()` first; `api.js` adds `Accept-Language` to every request; the settings dialog gets a language
  select, changing it stores the choice and reloads the page.
- `document.documentElement.lang` follows the language. Locale formatting (dates, numbers) uses the language tag.

### Tests

- `tests/test_i18n.py`: header parsing, `_` with and without catalog entry and parameters, middleware, every `_("literal")`
  of `arcviz/` has an English entry and every entry is used, placeholders match between German and English.
- `tests/js/i18n.test.mjs`: the same for `t(...)` literals and `data-i18n*` attributes.
- e2e: the browser context runs with locale `de-DE`; one step switches to English and checks a few texts.

## Migration/compatibility

Default German keeps every existing text and test; no data or API changes. Exports, bundles and stored transcripts are
language-neutral or untouched.

## Decisions

- German source text as key (ADR-0199). Language switch reloads the page (no live re-render). Language per browser, not per
  account.
