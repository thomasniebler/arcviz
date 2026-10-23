# ADR-0003: Store full snapshots per version, not diffs

- Status: Accepted
- Date: 2026-10-04
- Spec: `docs/architecture/specs/persistence.md`

## Context

Every chat turn that changes the diagram creates a version. With database backends in view, the
question came up whether versions should be stored as JSON diffs against the previous state to save
space.

A diagram is a few kilobytes of JSON. Document size limits of the candidate stores are 400 KB
(DynamoDB), 1 MB (Firestore), 2 MB (Cosmos DB NoSQL), 16 MB (MongoDB). Preview, restore and undo each
read one version.

## Options

1. **Full snapshot per version** (status quo). One read per operation; trivial restore; versions are
   independent and can be deleted individually.
2. **Diff per version against the predecessor.** Smaller storage; every preview or restore replays
   the chain from the last full snapshot; needs a diff format, a patch implementation and tests for
   both; a corrupted diff breaks all later versions.
3. **Snapshots with periodic diffs (hybrid).** The complexity of option 2 with part of the saving.

## Decision

Option 1. Storage is not a constraint at this size and the operations the UI performs are all
single-version reads.

## Consequences

- The `DiagramRepository` contract stores and returns whole `Diagram` objects per version.
- If diagrams ever approach the backend's item limit, a backend may compress or delta-encode
  **internally** without changing the contract; that would be a new ADR.
