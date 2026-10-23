# ADR-0137: Open world; completeness is declared per diagram, hiding is marked per view

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/model-relations.md` (M25)

## Context

Owner decision (#203): a view that leaves something out says nothing about it. Omission becomes a finding only where a
diagram declares itself complete (#205), and a per-view marker "intentionally hidden here" suppresses that finding. The
issue leaves open whether the marker lives in the model or in the diagram.

## Decision

- **Open world** by default: no rule reports a relation missing from a diagram that declares nothing.
- `Diagram.complete` lists the relation kinds a diagram declares complete (`set_completeness`, public operation, agent
  tool, REST `/ops`); `["all"]` expands to the kinds the type can show at that moment.
- The hidden marker is **diagram side**: `Diagram.hidden_relations` (relation ids). It is a statement of the view,
  versioned, undone and restored with it, moves out with the diagram's other links (ADR-0079), and does not grow the
  project item. Its operation is internal (the model service checks the relation exists); `clear_relation` drops it
  when the relation is removed.
- Both fields are omitted from dumps while empty, so fingerprints of older diagrams are unchanged.

- Only types whose connections can show relations (deployment, sequence) carry `complete` and `hidden_relations`;
  the diagram invariants and the importer refuse them elsewhere, and the model service never sends relation operations
  to other types.

## Consequences

- A model-side marker (relation → diagrams) was rejected: it would put view statements into the model, change the
  project document for a view edit, and survive the diagram's undo.
- Activity diagrams declare nothing (they show no relations, ADR-0139).
