# ADR-0122: A revision-only read primitive for live polling

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/scale-and-robustness.md` (#174)

## Context

Every live stream polls the latest revision of its diagram every `ARCVIZ_LIVE_POLL_SECONDS` (shared per diagram
through `LiveHub.shared`), and did so by reading and validating the whole diagram.

## Options

1. **`read_revision` primitive per backend.** File: `stat()` plus a cache keyed by inode, mtime and size (every
   write replaces the file); SQLite: one column; Cosmos DB: a single-partition projection query.
2. **Cosmos DB: a point read with `If-None-Match` on the last ETag** (304 when unchanged). Cheapest when nothing
   changed, but it needs an ETag→revision cache per diagram and process, and the SDK's handling of 304 differs
   between versions.
3. **A separate revision item next to `latest`.** One more write in every commit batch and a migration.

## Decision

Option 1. The projection query costs a fixed small charge independent of the diagram's size and transfers no
diagram; the file and SQLite variants avoid parsing and validating the document.

## Consequences

The store contract gains `read_revision`; `LiveService` uses it. For very small diagrams a Cosmos point read could be
marginally cheaper than the query; the poll is shared per diagram, so the difference is per diagram, not per tab.
