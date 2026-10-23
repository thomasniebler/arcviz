# ADR-0011: Starter templates as code, listed statically in the UI

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/starter-templates.md`

## Context

`templates.TEMPLATES` maps ids to factory functions (`empty`, `demo`); the demo is built with
operation calls. The **Neu** dialog in `index.html` lists the two templates as static options with
German labels; `/api/config` reports only the ids. Issue #73 adds more starter diagrams.

## Options

1. **More factory functions, static options in `index.html`, a test that every id has an option.**
   Smallest change; labels stay in the German UI where all other UI text lives.
2. **Templates with metadata (title, description) served by `/api/config`, options built by
   `app.js`.** No duplication of the list, but labels would come from the server in the UI language,
   and the `templates` field of `/api/config` changes shape (or a second field appears).
3. **Templates as JSON files** under `arcviz/`. Data instead of code, but bypasses the operations
   that guarantee valid diagrams unless they are validated on load.

## Decision

Option 1, because it keeps the API unchanged, the UI text in one place, and every template built
through the single mutation path; the drift test makes the duplication safe.

## Consequences

- Adding a template means a function in `templates.py`, an entry in `TEMPLATES`, an option in
  `index.html` and a line in the user guide.
- Revisit option 2 if templates become user-defined.
