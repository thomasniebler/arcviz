# ADR-0044: Conditional writes on a latest revision, retried per mutation path

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/concurrent-mutations.md`

## Context

Every mutation loaded the diagram, changed it in memory and wrote it back unconditionally
(`write_latest`, Cosmos `upsert`). Two tabs, overlapping chat turns or a chat turn plus a direct edit,
restore or undo lost accepted changes, the agent's finalization wrote its copy a second time, and a
turn outliving a deletion recreated the diagram (#65). The `file` and `sqlite` stores run in one process
per data directory; `cosmos` is meant for several replicas. M10 plans optimistic concurrency for HTTP
clients with `ETag`/`If-Match` (#86).

## Options

1. **A lock per diagram around every request's read-modify-write.** Simple for one process, but does
   nothing across Cosmos DB replicas, holds the lock for a whole chat turn (seconds to minutes of model
   calls) or has to be released between tool calls, and gives #86 nothing to build on.
2. **Distributed lock or lease** (Cosmos lease item, Redis). Works across replicas, but needs expiry,
   fencing tokens and a new dependency or item type; a crashed holder blocks the diagram.
3. **A revision on latest and a compare-and-swap write**, atomic with the version append, in every
   backend (file: per-diagram lock in the process; SQLite: one transaction; Cosmos DB: `If-Match` on the
   item's ETag inside a transactional batch). Each path decides what to do on a conflict: re-apply its
   intent, skip, or answer 409.
4. **Merge on write** (operational transform or a three-way merge of states). Most permissive, but a
   large design for a problem that re-applying the operation already solves.

## Decision

Option 3: it protects the whole sequence on every backend including Cosmos DB replicas, keeps writes
lock-free across requests, and the revision is exactly the token #86 will expose as `ETag`. Intent-based
requests (operations, agent tool calls, restore, undo/redo, manual snapshot, proposal confirm) retry by
re-loading and re-applying up to five times and then answer 409; agent finalization skips its version
instead of writing over a newer state; deleted diagrams are never written again.

## Consequences

- New primitives `read_latest` and `commit_latest`; `create_latest` writes version 1 atomically;
  `ConflictError` (a `StoreError`) is answered with 409. Every backend changed; the store contract and
  the Cosmos fake (ETags, `If-Match`, 412, batch `create`/`read`) cover them.
- No lost updates within one process for `file`/`sqlite` and across replicas for `cosmos`. The single
  process rule for `file`/`sqlite` stays.
- A turn whose finalization conflicts records no version of its own; its operations land in the next
  version. Re-applied operations may produce different results than the first attempt would have (for
  example a generated id `x-2`), which is visible in the response.
- #86 only has to map `ETag`/`If-Match` onto the revision and call `update` with an expected revision
  and one attempt.
