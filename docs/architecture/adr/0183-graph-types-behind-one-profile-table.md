# ADR-0183: Graph types sit behind one profile table

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/uml-graph-types.md`

## Context

The type tables of ADR-0031 cover every layer, but the *graph* types (nodes and edges) share their operations
(ADR-0034), and the activity type is wired into them by conditions: `GUARD_TYPES`, `DIRECTED_TYPES`, `DEFAULT_NODE_KINDS`,
`DEFAULT_EDGE_KINDS`, `activity_edge_problem`, `_check_edge_rules`, `_check_kind_change`, a branch in `exchange._rule_errors`,
`mermaid.edge_text` and `mermaid._label`. Four more graph types (state machine, class, use case, component) would each add
their own copy of these conditions, with type-specific parent rules, typed fields and well-formedness rules.

The product roadmap ("No new diagram types … in parallel", `docs/product/roadmap.md`; *Parked* in `ideas.md`) ruled new diagram types out
for the current period. In this session the maintainer decided to build the four types for requirements engineers,
architects and, through MCP, developers.

## Options

1. **One conditional per type in each place.** No refactor, the smallest first diff. Every place grows by four branches, and a
   rule that must hold in the operations and in the importer is written twice per type.
2. **A profile per graph type in one registry (`graph_types.py`)**: kinds, defaults, directedness, typed fields, edge and parent
   rules and whole-diagram rules as data and functions, used by operations, importer, tools and renderer. One refactor of the
   activity type first, with golden tests that prove nothing changed.
3. **Subclass `Diagram` per type.** Pydantic subclasses with their own validation. Changes the serialisation and every
   `Diagram(...)` call, and fights the one `type` field that the store, fingerprint and exchange rely on (ADR-0031).

## Decision

Option 2. The operations and the importer ask the profile; the activity type is the first profile and must behave
byte-identically (golden outputs). The roadmap and the ideas page are updated in the same change: diagram types move from
*Parked* to a named work item, "UML diagram types for requirements and architecture".

## Consequences

- The next graph type is a profile, not a new set of conditions; `tests/test_diagram_types.py` also checks that every
  graph type has one.
- The refactor can break activity diagrams; the golden tests (Mermaid, fingerprint, JSON and PlantUML export, tool schemas,
  prompt) are written before it.
- Deployment (configurable catalogue) and sequence (own model) stay outside the registry.
- The roadmap's ordering (A1..B5) is not changed otherwise; this work is added beside it by decision of the maintainer.
