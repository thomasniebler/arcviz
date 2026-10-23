# ADR-0015: Proposals are dry runs held by the client and replayed on confirm

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/propose-confirm.md`

## Context

Issue #75 asks for an optional mode in which the operations of a chat turn are shown as a
highlighted diff before they are persisted, and the user confirms or discards them. Today the agent
persists after every successful tool call (`save_latest`) and versions the turn in a `finally` block.
The `file` and `sqlite` stores are single-process, `cosmos` may run with several replicas. All
mutations must go through `operations.apply`.

## Options

1. **Apply as usual, roll back on discard.** Reuse the normal path and restore the previous version on
   "discard". Simple, but the changes *are* persisted (latest, a version, the Git mirror) before the
   user agreed, which is what the issue wants to avoid, and history gets two extra versions.
2. **Dry run, pending proposal kept on the server** (in memory or in the store), confirmed by id.
   The client sends nothing but an id. In memory it is lost on restart and breaks with several
   replicas; in the store it needs a new persistence primitive on every backend.
3. **Dry run, proposal held by the client.** The final event carries the operations plus content
   fingerprints of the base and the result; confirm sends them back, the server checks the base,
   replays through `operations.apply` and checks the result. Stateless, works across restarts and
   replicas, no store change. The client could send different operations, but it can already do
   anything through `POST /ops`, and the replay validates everything.

## Decision

Option 3: nothing is persisted before the user confirms, and the server keeps no state between the
turn and the confirmation.

## Consequences

- The base check is not atomic with the write (no conditional write in the stores); a concurrent write
  in the moment between the final check and `save_latest` is last-writer-wins, as for all other writes.
  A store-level compare-and-swap is a possible follow-up.
- A reload drops a pending proposal (acceptable; nothing was changed).
- Confirm fails with 409 if anything changed the diagram meanwhile; the UI locks other edits while a
  proposal is pending to make that rare.
- The transcript gets short assistant notes for the outcome, so two assistant entries can follow each
  other; the Anthropic provider already merges consecutive same-role turns.
