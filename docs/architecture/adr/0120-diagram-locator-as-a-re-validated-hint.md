# ADR-0120: A diagram→project locator as a re-validated hint that heals on read

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/scale-and-robustness.md` (#181)

## Context

A diagram document does not know its project (ADR-0051); the membership lives in the project document. Every
authorised diagram request found the diagram's project by listing every project (`AccessPolicy.locate`, arc42 R21),
and `GET /api/diagrams` and every project view listed every diagram of the store. On Cosmos DB these are
cross-partition queries over all tenants, billed per item, on every request. M21 solved the same problem for
project lists with a per-user listing document that is a hint, re-validated on read.

## Options

1. **A locator document per diagram, re-validated, written by the services; no fallback.** One point read finds the
   project; a missing or stale entry (a crash, data from before M22, an old replica in a rolling upgrade) makes the
   diagram invisible until the start-up repair.
2. **As 1, with a fallback scan for every miss.** Always correct, but every probe of an unknown id costs a scan:
   outsiders can make the app scan at will.
3. **As 1, with a fallback scan only for diagrams that exist, healing the entry with a conditional write.** Unknown
   ids cost two point reads; an existing diagram without a valid entry is scanned once and then has one.
4. **Store the project id in the diagram document.** Needs a transaction across the diagram and the project
   document for every move (Cosmos DB partitions per diagram), and a migration of every diagram.

## Decision

Option 3. The entry (`location` keyed document) lists **every project that may hold the id** (a superset: writers
list a project before adding its membership and drop it only after the membership is gone); the project reads decide
(membership, the newest one if several hold it) and the guard decides (role). A delete removes the id from every
listed project, and a create of an id first clears every listed project, so a stale membership left by a crash never
shows a new diagram of the same id elsewhere; neither needs a scan. A writer marks the project it lists as pending with a token of
its own and clears it after its membership write; a pending project is never dropped (no clocks: replica skew cannot
break the superset). Only the store's definite "not found" counts as absent: every other failed read aborts the write
that needed it. The fallback scan for an existing diagram without a valid entry is single flight per process and
reused for a few seconds; it heals the entry, and when it finds no holder (an orphan) it records `scanned_at`, so the
orphan is not scanned again for ten minutes. The repair at start-up backfills entries. A caller's read
may heal the entry: it is a hint document, not a membership, so ADR-0088 (a caller's read repairs no membership) is
not affected.

## Consequences

Diagram requests, project views and `GET /api/diagrams` cost point reads only. `place` removes the source it located,
not every project; duplicates from crashes resolve to the newest membership on read and are removed by `reconcile`
(R15) or by the next delete or create of the id. Orphans cost one scan per ten minutes. A rolling upgrade heals itself
on first access; data written by an older replica is outside the invariant until the next start.
