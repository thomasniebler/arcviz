# ADR-0059: A multi-diagram turn writes through per call and versions each changed diagram

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-chat.md`

## Context

A diagram turn writes latest after every successful call (conditional on the revision, #65) and commits
latest plus one automatic version in a `finally` block (`commit_turn`). Version histories are per diagram;
no backend can write two diagram documents atomically (Cosmos DB batches are per partition).

## Options

1. **Per diagram, as today**: every diagram the turn changes keeps its own revision chain, write-through
   per call, and gets one automatic version with the turn's message as label at the end.
2. **Buffer all changes and commit them at the end.** No live persistence; a disconnect or crash loses
   the turn, and the live render would show unsaved state, contradicting the diagram turn semantics.
3. **A cross-diagram "turn version"** in the project document listing the diagram versions. Gives a
   project-level undo point, but needs a project history, which M16 deliberately left out.

## Decision

Option 1: each diagram behaves exactly as in a diagram turn, so undo, redo, restore and conflicts keep
their meaning per diagram.

## Consequences

- A turn can leave diagram A versioned and diagram B unversioned when B moved on meanwhile (logged, like
  today); the transcript entry lists the versions that were written.
- Undoing a multi-diagram turn means undoing in each diagram; the reply lists every changed diagram.
- A project history (if ever wanted) can be added later on top of the per-diagram versions.
