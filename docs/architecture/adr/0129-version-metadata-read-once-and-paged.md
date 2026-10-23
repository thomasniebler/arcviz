# ADR-0129: Version metadata is read once per response, without snapshots, and paged

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/hardening.md`

## Context

Nearly every endpoint answers with the diagram detail, which called `list_versions`, `undo_target` and `redo_target`;
each of the latter listed again (#114). The file store parsed every full snapshot to read its `meta`, Cosmos DB
queried `SELECT *` (every snapshot document, billed per KB), and the whole list went into every response. History
grows by one version per turn and per operation, so latency, response size and RU cost grew with a diagram's lifetime.

## Options

1. **Metadata-only listings, one per response, a page in the response.** File: a small sidecar per version
   (`NNNN.meta.json`, created lazily for old data); SQLite: the existing `meta` column; Cosmos: a single-partition
   projection of `version` and `meta`. Undo/redo targets become pure functions of one listing (`history()`). The
   response carries the newest page plus `versions_total`/`versions_more`; older pages come from
   `GET …/versions?before=`.
2. **A version index document per diagram** (one read instead of a listing): one more document to keep consistent with
   every version write (in Cosmos the same partition, so a batch could do it), and a migration for every backend.
3. **Undo/redo pointers stored on `latest`**: removes the replay entirely, but changes the versioning semantics that
   the store contract pins down and needs a migration.

## Decision

Option 1: no new consistency invariant and no migration beyond the lazy sidecars; the remaining linear cost (the
metadata listing) is bounded by the retention policy (ADR-0130).

## Consequences

- One metadata listing and no snapshot read per diagram response in every backend; a contract test counts them.
- `versions` in responses is a page (default 50, `ARCVIZ_VERSIONS_PAGE`); API clients needing all versions call
  `GET /api/diagrams/{id}/versions` (unchanged without parameters).
- File stores written before M23 pay one snapshot read per old version on their first listing.
