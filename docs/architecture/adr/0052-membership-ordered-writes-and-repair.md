# ADR-0052: Membership across documents: ordered writes and a repair step

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/projects.md`

## Context

With membership in the project documents (ADR-0051), creating, moving and deleting a diagram writes two
or three documents. No backend writes them atomically: Cosmos DB transactional batches are limited to one
partition, and the diagram and each project live in different partitions. A process can crash between
the writes, and several replicas may work at once.

## Options

1. **Distributed transaction or saga log.** A pending-operation record replayed after a crash. Correct,
   but a new item type, a recovery loop and more writes on every placement.
2. **Ordered writes plus an idempotent repair step.** Order the writes so that a crash leaves only an
   orphan (diagram in no project), a duplicate (diagram in two projects, the newer placement intended) or
   a dangling membership (diagram gone), and repair those deterministically: duplicates keep the newest
   `added_at`, dangling memberships are dropped after `exists()` confirms, orphans go into the default
   project after a fresh re-read.
3. **Ignore the gap.** Simplest, but an orphan would be invisible in the navigator.

## Decision

Option 2: every intermediate state is recoverable without extra infrastructure, the repair is ordinary
conditional project writes, and it doubles as the migration of existing diagrams (ADR-0053).

## Consequences

- `ProjectService.reconcile()` runs on every project listing and in `arcviz-migrate --projects`.
- The repair reads every project and the diagram list; cheap at the expected scale, a risk entry in
  arc42 section 11 for very large installations.
- A move racing a repair can, in a narrow window, adopt the moving diagram into the default project; the
  re-read before adopting makes this require two unlucky reads in a row, and nothing is ever lost.
