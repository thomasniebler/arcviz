# ADR-0187: Project proposals carry independent items that a person applies in part

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/architecture-objects-over-mcp.md`

## Context

Stored proposals (ADR-0172) are operations on **one diagram**, validated by a dry run, applied or discarded as a whole,
stale when the diagram changed. Drivers, scenarios and decisions live in the project document (ADR-0163, ADR-0166). A
derivation from 200 to 500 requirements yields dozens of candidate drivers and scenarios at once; the person will want
to take some and leave others, and a scenario may refer to a driver proposed in the same batch.

## Options

1. **One proposal per object.** Reuses the diagram proposal shape; the 30-pending cap is hit at once and the person
   faces dozens of list entries without grouping.
2. **A proposal with all-or-nothing apply**, like diagrams. Simple; one bad item blocks 40 good ones.
3. **A proposal with items** (`kind: "project"`): each item is one project operation with a local `key`; later items may
   reference earlier ones as `@key`; a person applies a subset (the items it depends on are applied first or the apply is
   refused), the rest stay pending or are discarded item by item.

## Decision

Option 3, in the existing `proposal` document and service: `StoredProposal.kind` (default `"diagram"`, so stored
documents are unchanged), `items`, per-item state. Staleness is per item: an `update_*` item carries a fingerprint of its
target and is refused when the target changed; an `add_*` item cannot go stale.

## Consequences

- Same service, same REST routes (with `accept`/`discard` taking item keys), same list in the UI; the diagram path is
  untouched.
- The proposal document grows: project proposals have their own, smaller caps and drop their arguments once decided so
  the document stays far below the item limit (arithmetic in the spec).
- `@key` references need a resolver and a dependency check in `services/proposals.py`; applied items record the id they
  created so that an interrupted apply can be completed (the existing claim mechanism covers the crash case).
