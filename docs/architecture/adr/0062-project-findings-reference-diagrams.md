# ADR-0062: Project findings extend the finding schema with diagram references

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-review.md`

## Context

Rule checks and the LLM review share one `Finding` schema (ADR-0023) whose `elements`/`edges` are ids
within the reviewed diagram. A project review reports findings about several diagrams and about sections.

## Options

1. **Add `diagram`/`section` to `Finding`.** One schema, but every diagram review answer would grow
   `null` fields and diagram code would have to ignore them.
2. **`ProjectFinding(Finding)`** with `diagram` (the diagram the element ids belong to and the UI opens),
   `diagrams` (every diagram it concerns) and `section`; aggregated diagram findings get the id
   `<diagram>:<id>`.
3. **Element references as `diagram/element` strings.** No schema change, but the UI and the validation
   would parse ids, and ids may contain `/`-like characters in the future.

## Decision

Option 2: diagram reviews stay byte-for-byte the same, project findings are a strict superset that the
existing UI code can render.

## Consequences

- The frontend shows a chip with the diagram or section of a project finding and opens the diagram on
  selection.
- M18 can add model-element references as further fields without touching diagram findings.
