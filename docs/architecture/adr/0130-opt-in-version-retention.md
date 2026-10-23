# ADR-0130: Version retention is opt-in and never drops what undo, redo or manual snapshots need

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/hardening.md`

## Context

Versions were never removed (#114). On a shared Cosmos DB container every version is a stored item, so storage and
the metadata listing grow without bound. Versions are also the undo/redo history and the user's manual snapshots,
which users expect to keep.

## Options

1. **Keep everything** (status quo): simple, unbounded.
2. **Count-based retention, opt-in** (`ARCVIZ_VERSIONS_KEEP=N`, at least 10): after a write that added a version and
   left more than `N + slack`, delete the oldest beyond the newest `N`, never the first or the latest version, the
   versions undo and redo still need (the redo replay from its last clearing version, the undo target and cursor,
   `restored_from` of kept versions) and manual snapshots unless `ARCVIZ_VERSIONS_PRUNE_MANUAL` is set.
3. **Age-based retention** (`…_DAYS`): bounds nothing for a diagram edited heavily in one day.
4. **Retention on by default**: deletes data on upgrade without an operator's decision.

## Decision

Option 2, off by default. Pruning is a store primitive (`delete_versions`) driven by the pure selection
`history.prunable`, run best effort after the write; version numbers are never reused (numbering continues after the
highest stored number, and the latest version is never pruned).

## Consequences

- An operator bounds the history per diagram; undo/redo work within the kept window, and a property test checks that
  pruning never changes the undo or redo target.
- The Git mirror keeps its own history; pruned versions are gone from the store.
- A prune runs only every `slack` versions (decided from the version number, no listing per commit) and keeps the
  newest `keep + slack` (a grace). A target pruned anyway while in use answers 409 "reload the version list"
  (`VersionGone`); retrying computes a new target.
- The backend migration copies versions under their own numbers (`put_version`), so gaps survive a migration.
