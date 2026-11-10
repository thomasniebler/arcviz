# ADR-0218: Change sets make one edit across documents undoable

- Status: Proposed
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`

## Context

One edit in a view may write the structure document, a behaviour document and the view. The stores have
conditional single-document writes only (Cosmos DB has no cross-partition transaction). Undo, redo and the versions
panel are per diagram today (ADR-0003: full snapshots, linear history).

## Options

1. **Project-wide history document.** Every edit appends to one log; a hot spot and an unbounded document.
2. **Change sets on version metadata.** Each version written by one edit carries the same `changeset` id, its
   `context` and touched ids; documents are written in the order structure → behaviour → view; readers tolerate a
   partly written change set; a view's history is merged from three version listings.

## Decision

Option 2: it needs no new primitive and no multi-document atomicity.

## Consequences

- Undo in a view reverts the newest change set made in that view and refuses (409) when a later change set touched
  the same items (open question 2 of the spec).
- A crash between documents leaves a model change not yet shown, or a view naming an element not yet created; both
  are harmless and repaired by the next write.
- Version comparison resolves projections at change-set boundaries.
