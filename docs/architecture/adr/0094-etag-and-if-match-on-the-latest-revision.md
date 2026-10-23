# ADR-0094: ETag is the latest revision; `If-Match` makes a write a single compare-and-swap (412)

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`
- Builds on: ADR-0044 (conditional writes on a latest revision)

## Context

Since #65 every write is conditional on the latest revision, and a conflicting server-side write re-applies the
operation on the newer state. That is right for "add a node", but a user who edits a field that someone else
changed meanwhile silently overwrites it. HTTP has a standard for this: `ETag` plus `If-Match` → 412.

## Options

1. **Check `If-Match` in the route, then write as before.** A write between the check and the write is lost (TOCTOU).
2. **Pass the expected revision down to the store's `update`:** one attempt, compare, conditional write; a
   mismatch or a concurrent write raises `RevisionMismatch` (a `ConflictError`) → 412.
3. **A content hash as ETag.** Needs reading and hashing; the revision already exists and changes on every write.

## Decision

Option 2 with the revision as ETag. `If-Match` lists match any listed tag, a weak tag never matches (strong comparison) and
`*` anywhere means "no condition" (RFC 9110); the store accepts any of the listed revisions. Without `If-Match` the write keeps re-applying (today's behaviour). Access is
checked before the revision, so 404/403 win over 412.

## Consequences

`update`, `snapshot_latest`, `restore`, `undo`, `redo` take `expected`. Chat turns, proposal confirmation (own
base fingerprint), deletes and project documents do not honour `If-Match` (documented). The revision changes also
for writes that keep the content (a snapshot), which then counts as a change.
