# ADR-0037: Configurable kinds extend deployment diagrams only

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/diagram-types.md`, `docs/architecture/specs/configurable-kinds.md`

## Context

The kinds file (`ARCVIZ_KINDS_FILE`, ADR-0010) extends the node and edge kinds process-wide. With
diagram types (#98) there is a kind catalogue per type. Activity and sequence diagrams follow UML 2.5.1
(ADR-0030); their element kinds are metaclasses with a fixed notation drawn by their renderer, and the
sequence renderer has no notion of a configurable style or shape.

## Options

1. **The kinds file keeps its format and extends the deployment catalogue only.** Existing files keep
   their meaning; activity and sequence vocabularies stay closed.
2. **Sections per type** (`{"deployment": {...}, "activity": {...}}`). More general, but a new format,
   and configured activity kinds would need a notation (which UML does not define for free kinds).
3. **One process-wide catalogue for all types.** Deployment kinds would become valid in activity
   diagrams.

## Decision

Option 1. A configured kind may not take the name of any built-in kind of any type (the names of the
other types are reserved), so kind names stay disjoint across types. Kind packs and icons (#100) use
the same format and the same scope.

## Consequences

- No migration for existing kinds files.
- Extending activity diagrams (for example with further UML action kinds) is a code change following
  the specification.
- `/api/config` reports configured kinds under the deployment type (and, for compatibility, at the
  top level as before).
