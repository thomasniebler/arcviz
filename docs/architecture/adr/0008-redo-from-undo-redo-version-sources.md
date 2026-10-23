# ADR-0008: Redo from undo/redo version sources

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/redo.md`

## Context

Undo is a restore of `undo_target` and is recorded as a new version with `source="restore"`
(ADR-0003, linear history). Redo (#70) needs to know which state an undo replaced and whether
anything changed since. Today an undo cannot be told apart from a restore chosen in the selector,
and nothing records the pre-undo state other than the version list itself. There are three store
backends; the versioning semantics live once in `DiagramRepository`.

## Options

1. **New `source` values `undo` and `redo`; `redo_target` replays the version list as a stack.**
   No schema change in any backend (the source is a string in the stored metadata), works from
   metadata alone, cleared by any real change automatically. Old undos (`restore`) are not
   redoable.
2. **A per-diagram redo pointer stored next to `latest`.** Needs a new primitive in every backend
   and must be kept consistent with versions written concurrently.
3. **A new `VersionInfo` field (e.g. `undo_of`).** Like option 1, but adds a field to every version
   record and still needs a way to tell undo from restore.

## Decision

Option 1, because it keeps redo a pure function of the version metadata in the shared base class
and needs no backend changes. Keyboard shortcuts (++ctrl+z++, ++ctrl+shift+z++, ++ctrl+y++) act only
outside text fields so the browser's text undo is never taken over.

## Consequences

- `source` has two more values that clients and the Git mirror show.
- `/undo` records `undo` instead of `restore` (visible in the version selector).
- `redo_target` is O(number of versions) per detail response, like `undo_target`.
- Undos recorded before this change cannot be redone.
