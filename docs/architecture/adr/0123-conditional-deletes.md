# ADR-0123: Conditional deletes; If-Match on DELETE of diagrams and projects

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/scale-and-robustness.md` (#175)

## Context

Since M10 every diagram write honours `If-Match` (ADR-0094), deletes did not: the stores had no conditional delete
for diagrams, and project responses carried no revision a client could send.

## Options

1. **A revision check in the service, then an unconditional delete.** A write between the check and the delete is
   lost silently.
2. **A conditional delete primitive** (`purge(id, expected)`), atomic per backend, and one attempt in the service,
   like a conditional write.

## Decision

Option 2. File: under the diagram lock; SQLite: `DELETE … WHERE revision = ?`; Cosmos DB: delete of `latest` with
`If-Match` on the ETag of the revision read (the rest of the partition follows as before). Projects already had
`delete_project(id, expected)`; project views now carry `revision` and `GET /api/projects/{id}` an `ETag`.
Access is checked first, so outsiders still get 404 and non-owners 403.

## Consequences

`DELETE` with a stale `If-Match` answers 412 with the current revision. The browser does not send `If-Match` on
deletes (a follow-up if the UI wants it).
