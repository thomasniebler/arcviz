# ADR-0199: The German source text is the message key; English lives in catalogs beside it

- Status: Accepted
- Date: 2026-10-22
- Spec: `docs/architecture/specs/internationalization.md`

## Context

The UI, the rule findings, the review rules and the server's error messages are German and written inline: about 65 files
in `arcviz/` hold user-visible text, much of it assembled from f-strings. The product must offer German and English, German
stays the default for existing users, and tests, docs and the eval set assert German texts.

## Options

1. **Symbolic keys** (`finding.no_backup.message`) with a catalog per language. The usual practice, but every call site and
   every test changes, German disappears from the code and review of text is harder, and one missing key shows a key.
2. **German source text as the key** (gettext style): `t("Speichern")`, `_("{n} Knoten ohne Verbindung", n=3)`. The code
   stays readable, German needs no catalog (a missing English entry falls back to German, never to a key), and the migration
   is mechanical. A reworded German text needs the same edit in the English catalog; a test finds literals without entry.
3. **English as the source text.** Cleaner for translators, but changes every existing text and test.

## Decision

Option 2, in the frontend (`i18n.js`: `t`) and the server (`arcviz/i18n.py`: `_`), with an English catalog per area
(`locales/en/*.json`). A test per side extracts every literal and fails when the English catalog lacks it, or holds an entry
no literal uses.

## Consequences

German remains the reference language; adding a language later means one more catalog directory. A German sentence is its
own identity, so rewording it is a catalog edit as well. Texts built by concatenation are not translatable and are rewritten
with placeholders.
