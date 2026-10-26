# ADR-0208: Prose for the sections the model does not cover

- Status: Accepted
- Date: 2026-10-26
- Spec: `docs/architecture/specs/arc42-export.md`

## Context

Sections 3, 4, 8 and 12 of arc42 are only partly derivable from the model. The page must show gaps honestly, and the steward should be able to propose drafts.

## Options

1. **Generate prose with an LLM at export time.** Always "complete"; unreviewed, non-reproducible, costs tokens per view.
2. **Leave the sections empty.** Honest, but the document is never finished.
3. **A per-project note per section (`Project.arc42_notes`, ≤ 8000 characters each, edited by people, proposable by the steward) combined with derived content.** Reproducible, reviewable, versioned with the project.

## Decision

Option 3 (`set_arc42_note(section, text)`, public project operation, proposable, refused for direct agent calls).

## Consequences

One more optional project field and operation; the export stays deterministic; gaps become review findings the steward can act on.
