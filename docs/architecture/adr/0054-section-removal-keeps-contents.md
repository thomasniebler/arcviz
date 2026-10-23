# ADR-0054: Removing a section keeps its contents; only empty projects can be deleted

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/projects.md`

## Context

Sections are folders of diagrams and subsections. Deleting one, or a whole project, must not lose
diagrams by accident; diagrams have versions and transcripts that are expensive to recreate.

## Options

1. **Cascade**: removing a section or project deletes its diagrams. Fast, but one click destroys work.
2. **Refuse unless empty** for both. Safe, but emptying a section takes one move per diagram.
3. **Sections dissolve into their parent; projects must be empty.** Removing a section moves its
   subsections and diagrams up into its place, which loses nothing and needs no extra steps; deleting a
   project with diagrams is refused (409), so diagrams are moved or deleted explicitly first.

## Decision

Option 3: no operation deletes a diagram implicitly, and the common case (removing a grouping) is one
step.

## Consequences

- `remove_section` is always safe; the UI says the contents move up.
- `DELETE /api/projects/{id}` answers 409 while the project has diagrams; the UI disables deletion then.
