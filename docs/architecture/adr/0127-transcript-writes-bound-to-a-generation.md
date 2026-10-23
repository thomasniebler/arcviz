# ADR-0127: Transcript writes are bound to a generation; placements to a project incarnation

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/hardening.md`

## Context

M17 (#140) bound transcript *compaction* to a generation token (ADR-0046) but left narrow windows (#143): a turn
checked the transcript generation and appended afterwards (a clear or a delete-and-re-create in between got the
entry); a project turn's `create_diagram` could place its new diagram into a project deleted and created again under
the same id during the turn; the Cosmos clear read the owner, then deleted the transcript items of whatever owner
held the id by then; a confirmed project proposal wrote its outcome note into the project the diagram had left. With
global ids and many tenants on one Cosmos container, "the same id" can be another user's project.

## Options

1. **A store primitive per write, conditional on a token** read where the caller made its decision: `append_*chat`
   and `clear_*chat` take an optional `generation`, checked atomically with the write (file lock, SQLite transaction,
   Cosmos batch with an ETag-conditional replace of the generation item); placements check a project incarnation
   inside their conditional project write.
2. **Re-check after writing and undo**: no atomicity; leaves an observable wrong state for a moment, and undoing a
   transcript append needs another primitive anyway.
3. **Cross-document transactions**: not available across Cosmos partitions.

## Decision

Option 1. The generation is the existing token (no new storage). The project incarnation is a new optional
`Project.incarnation` (a random token set by `new_project`, omitted from dumps while unset), with `created_at` as the
fallback for projects written before M23 (`created_at` has one-second resolution, so it alone could not tell two
quick re-creations apart). `clear_chat` reads the generation between two editor checks (editor checks always read
the project again), so it clears exactly the incarnation the check approved. The proposal note is appended only while
the diagram is still a member (re-checked after the replay) and with the generation read before the membership check.

## Consequences

- A chat cleared, imported or re-created during a diagram turn no longer receives that turn's entry (it did before;
  the project turn already behaved like this).
- One extra conditional replace per append with a generation on Cosmos (same partition, a few RU). Every Cosmos chat
  item carries the generation it was written in, and every append and compaction is guarded by it in its batch (also
  without an explicit generation), so no item has a dead generation; a clear sweeps the other generations' items and
  `load_chat` hides them until it is done (reviews of #194).
- Residual: a move between the proposal's membership re-check and its note append still writes the note (one line
  naming the version) into the old project; the window is one store round trip.
