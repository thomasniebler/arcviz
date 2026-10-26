# ADR-0207: arc42 is assembled deterministically and rendered on the server

- Status: Accepted
- Date: 2026-10-26
- Spec: `docs/architecture/specs/arc42-export.md`

## Context

The user wants a live arc42 page at `/project/<id>/arc42`, preferably rendered like the Zensical documentation. Zensical is a docs-only build tool (ADR-0109, ADR-0116), the frontend renders Markdown with its own `markdown.js` (ADR-0162), and the project already holds most arc42 content.

## Options

1. **Browser-side render with `markdown.js`.** No new dependency; needs JavaScript, not a plain shareable page.
2. **Server-side HTML from Markdown with a library (markdown-it-py, raw HTML off).** One small pure-Python dependency; a plain URL works without the SPA; the same section tree feeds the other writers.
3. **Zensical at request time.** Makes a docs tool a runtime dependency and needs a build step per request.

## Decision

Option 2 (the user's choice): a pure builder `arc42.build` creates a section tree, one renderer produces HTML, and the Markdown, AsciiDoc and docx writers read the same tree. The export never calls a model.

## Consequences

A runtime dependency (`markdown-it-py`, reasons in the PR) and a new class of server-rendered HTML that needs hostile-input tests and a strict CSP. `docx` is an optional extra.

## Amendment (implementation)

The section tree holds plain text only, so the page is written with `html.escape` from structured blocks and no Markdown library is added. This keeps the decision (a deterministic tree, rendered on the server, one tree for all writers, no model call) and drops its one runtime dependency. `docx` stays an optional extra.
