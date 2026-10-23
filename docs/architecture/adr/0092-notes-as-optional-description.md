# ADR-0092: Notes are an optional `description` set by the existing add/update operations

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`

## Context

Users and the assistant want to keep free text with a node or a lifeline (responsibilities, decisions, open
points). Every mutation goes through `operations.py`; stored documents, exports and version fingerprints must stay
unchanged for data without notes (the same constraint M18 met for `ref`).

## Options

1. **A new operation `set_description`.** One more tool, REST op and strategy branch per type; the assistant needs a
   second call to create a node with a note.
2. **A `description` argument of `add_node`/`update_node`/`add_lifeline`/`update_lifeline`.** No new operation;
   every path (tools, `POST /ops`, batches, proposals, the inspector) gets it at once.
3. **A property `note` in `Node.properties`.** No model change, but lifelines have no properties, Mermaid would
   not tell it from technical properties, and nothing limits its length.

## Decision

Option 2: an optional field `description: str | None` (≤ 2000 characters) on `Node` and `Lifeline`, omitted from
dumps while `None`; an empty string removes it.

## Consequences

Old JSON, exports and fingerprints are unchanged (checked against the v0.1.8 fixtures). Notes are not drawn.
Edges and other sequence elements have no notes (not requested).
