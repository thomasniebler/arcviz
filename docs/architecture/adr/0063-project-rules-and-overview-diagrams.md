# ADR-0063: Project rules, and an overview diagram is recognised by its title

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-review.md`

## Context

#136 asks for project rules such as empty sections, a missing overview diagram per section and missing
diagram types per subsystem. Diagrams carry no role ("overview") and sections no kind ("subsystem"); the
types are deployment (structure), activity and sequence (behaviour).

## Options

1. **A role field on diagrams or memberships** (overview, detail). Precise, but a model change with UI and
   migration, ahead of the shared project model (M18).
2. **Heuristics on existing data**: an overview diagram is one whose title says so (Übersicht, Overview,
   Kontext, Context, Landschaft, Landscape, Gesamtbild, Big Picture); a section is a subsystem; "types
   missing" means a section shows only structure or only behaviour. Rules are low-severity suggestions.
3. **Treat every deployment diagram as the overview.** Simple, but conflates structure with overview and
   duplicates the types rule.

## Decision

Option 2 (Proposed): useful hints without a model change; the low severity reflects that they are
heuristics. Rule set: `empty-section`, `section-without-overview` (two or more direct diagrams),
`missing-diagram-types`, `duplicate-title` (exact: medium; `difflib` ratio ≥ 0.85: low), `empty-diagram`.

## Consequences

- Wording of titles influences findings; the guide documents the recognised words.
- When M18 introduces roles or subsystems as model elements, these rules get a precise basis (new ADR).
