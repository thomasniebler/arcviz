# ADR-0002: Repository pattern for persistence

- Status: Accepted
- Date: 2026-10-04
- Spec: `docs/architecture/specs/persistence.md`

## Context

`arcviz/store.py` is one concrete class writing JSON files: `latest.json`, immutable
`versions/NNNN.json` snapshots and `chat.json` per diagram. Version numbers are claimed with
`O_EXCL` file creation, writes are atomic via temp file + rename. The agent and the server import the
class directly.

That couples the application to a POSIX filesystem. Deployments need a mounted volume and exactly one
replica; serverless container platforms without such volumes (Cloud Run, Fargate/App Runner) are
ruled out, and object-storage FUSE mounts do not provide the `O_EXCL`/rename semantics the store relies
on. The versioning rules (linear history, restore recorded as new version, undo walk over fingerprints)
are intertwined with the file I/O, so a second backend would have to re-implement them.

## Options

1. **Keep the file store, document the single-replica constraint.** No work; deployment choice stays
   narrow forever and every cloud guide needs volume gymnastics.
2. **Replace the file store with one database.** Simple code, but local development and tests then
   need a database or emulator, and the human-readable `data/` directory, which is useful for
   debugging and as an export format, is lost.
3. **Repository pattern: an abstract `DiagramRepository` with storage primitives implemented per
   backend and the versioning logic implemented once on top.** The file backend stays the default;
   backends are selected by configuration; a contract test suite keeps them equivalent.

## Decision

Option 3. The primitives are deliberately few (load/save latest, atomic append of a version, list and
load versions, append/compact/clear chat, delete), so a backend is roughly 150 lines of I/O. All
semantics live in the shared base class.

## Consequences

- `arcviz/store.py` becomes the package `arcviz/store/`; `DiagramStore` remains an alias of the
  file backend for one release.
- The only hard requirement on a backend is an atomic version allocation. Database backends become
  safe with several replicas; the single-writer rule remains for `file` and `sqlite`.
- A migration command between backends is needed (and doubles as export/backup).
- Each backend adds a dependency as an optional extra and a CI job; the project accepts that cost per
  backend it actually ships (see ADR-0004 for the first one).
