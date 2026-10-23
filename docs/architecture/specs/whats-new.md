# Release highlights: "new in this release" in every deployment

- Status: Implemented
- ADRs: [0175 Release highlights are a curated file, shown as a dismissible banner](../adr/0175-release-highlights-curated-banner.md)
- Builds on: [Docs in the app](docs-in-app.md), [Frontend split into ES modules](frontend-modules.md)

## Summary

The changelog was only on GitHub and, as a page, in the documentation. A deployment's users had no way to learn what
a new version brings. This change shows a short, hand-written list of highlights as a banner in the app, and links
the full changelog (which the documentation site already includes at `/docs/changelog/`, from `CHANGELOG.md`).

## Goals

- Users of a deployment see what is new after an upgrade, once, without being blocked.
- The wording is written for users, not taken from commit titles.
- Nothing to host: the highlights ship inside the wheel and the image, like the documentation.

## Non-goals

- Generating the text from commits (release-please titles are issue and milestone names), per-user read tracking on
  the server, a notification for people who do not open the app.

## Design

`arcviz/whatsnew.json` holds `{"releases": [{"version", "items": [{"emoji", "text", "text_en"?}]}]}`, newest first, at most
`MAX_ITEMS` (6) items of at most `MAX_TEXT` (200) characters per release. `arcviz/whatsnew.py` (no package imports)
validates it (`problems`), loads it (`load`; a missing or malformed file yields no highlights and never stops the start)
and picks `current(version)`: the newest release that is not newer than the running version, so a patch release without
its own entry keeps the highlights of its minor version.

`text` is German (the default); the optional `text_en` (same length limit) is the English text. `current(version, data,
language)` returns plain `{emoji, text}` items and puts `text_en` into `text` when the language is `en` and the item has
one, so a release without English texts shows German. `GET /api/config` answers in `arcviz.i18n.language()` (the request's
`Accept-Language`, spec `internationalization.md`). Add `text_en` to every new entry.

`GET /api/config` adds `version` and `whats_new` (`{version, items}` or `null`). The UI (`releasebanner.js`, pure
helpers in `whatsnew.js`) shows a slim bar under the header, **Neu in Version X** with the items in a dropdown and a link to
**Alle Änderungen** (`/docs/changelog/`). **✕** hides it and remembers that in the browser per highlighted release
(`localStorage` key `arcviz.whatsnew.<version>`, failures ignored), so the next release shows it again.
**Hilfe → Neu in dieser Version** reopens it. The texts are set with DOM APIs, never as markup.

### Release process

The release PR that release-please opens only bumps versions and `CHANGELOG.md`. Before merging it, add the entry for
the new version to `arcviz/whatsnew.json` on that PR's branch (a push to the branch), with a `text_en` for every item. `tests/test_whatsnew.py` rejects a
file that is malformed, unsorted, or whose newest entry is newer than `__version__`; a release without an entry for its
own version still starts, and shows the previous highlights.

## Testing

`tests/test_whatsnew.py` (validation, `current`, the language choice, `/api/config`), `tests/js/whatsnew.test.mjs`, `modules.test.mjs`
(layers); e2e: the banner shows, ✕ hides it across a reload, and **Hilfe** reopens it.

## Migration / compatibility

None; `/api/config` only gains fields.

## Decisions

ADR-0175, accepted.
