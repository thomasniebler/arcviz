# Redo after undo

- Status: Implemented (milestone M6)
- Issue: #70
- ADR: [0008 Redo from undo/redo version sources](../adr/0008-redo-from-undo-redo-version-sources.md)
- Depends on: [Persistence](persistence.md) (`restore`, `undo_target`, linear history)

## Summary

Undo restores the most recent different state and records it as a new version, so the history
stays linear. There is no way back after an undo except finding the right version in the selector
and restoring it. Redo is the forward counterpart: it re-applies the state an undo replaced, again
as a new version. To know what an undo replaced, undo and redo versions are recorded with their own
`source` values (`undo`, `redo`), and `redo_target` replays them as a stack.

## Goals

- `redo_target(id)` in the shared versioning logic (`DiagramRepository`), computed from version
  metadata only, so every backend gets it without changes.
- Repeated undos followed by repeated redos walk back and forth through the same states.
- Any real change after an undo (chat turn, direct operation, restore from the selector) clears the
  redo possibility, as in every editor. Manual snapshots do not (they do not change the state).
- REST endpoint `POST /api/diagrams/{id}/redo`, a **Redo** button next to **Undo**, keyboard
  shortcuts for both, and a local voice command.

## Non-goals

- Branching history or a redo tree. History stays linear.
- Making undos recorded before this change (source `restore`) redoable.
- Undo/redo of the chat transcript.

## Design

### Version sources

`VersionInfo.source` gains two values: `undo` and `redo` (in addition to `initial`, `auto`,
`manual`, `restore`). Both set `restored_from` like a restore. Existing data is unchanged; old undos
remain `restore` versions.

### Repository (derived operations in `store/base.py`)

```python
def restore(self, diagram_id, version, source="restore", label=None) -> tuple[Diagram, VersionInfo]
def undo(self, diagram_id) -> tuple[Diagram, VersionInfo] | None   # restore(undo_target, source="undo")
def redo_target(self, diagram_id) -> int | None
def redo(self, diagram_id) -> tuple[Diagram, VersionInfo] | None   # restore(redo_target, source="redo")
```

- `restore` keeps its behaviour and label for `source="restore"`. Undo and redo use the labels
  `Undo: restored version n (<label>)` and `Redo: restored version n (<label>)`.
- `undo_target` is unchanged: it follows `restored_from` of the latest version, which undo and redo
  versions set, so undo after redo goes back again.
- `redo_target` replays the version list in ascending order with a stack:

  | Version source | Effect |
  |---|---|
  | `undo` | push the version that held the state before the undo (`current`); `current = restored_from` |
  | `redo` | pop; `current = restored_from` |
  | `manual`, or any version with the same fingerprint as the current state (a no-op operation) | none |
  | `initial`, `auto`, `restore` that changed the content | clear the stack; current = `restored_from` or else this version |

  The result is the top of the stack, or `None` when it is empty.

### HTTP

- `POST /api/diagrams/{id}/redo?focus=` → 200 with `{"version": …, ...detail}` like `/undo`, or
  409 `Nothing to redo.`
- `/undo` now records `source: "undo"` (was `restore`).
- The diagram detail (`GET /api/diagrams/{id}` and every mutating response) gains `redo_target`
  next to `undo_target`.

### Frontend

- A **Redo** button after **Undo** (disabled when `redo_target` is null) posts `/redo` and posts a
  chat line like Undo's.
- Shortcuts outside text fields (`input`, `textarea`, `select`, contenteditable):
  ++ctrl+z++ / ++cmd+z++ = Undo, ++ctrl+shift+z++ / ++cmd+shift+z++ and ++ctrl+y++ = Redo. Inside
  text fields the browser's own text undo keeps working.
- Voice command `redo` with the phrases `wiederholen`, `redo`, `vorwaerts`, `nochmal anwenden`
  (not `wiederherstellen`, which names the restore button). Executed like `undo` by clicking the
  button; echoes "Nichts zu wiederholen." when it is disabled.

## Testing

- `tests/store_contract.py` (all backends): undo/redo sources and labels; undo, undo, redo, redo
  walks back and forth; a new `auto` version after an undo clears redo; a manual snapshot does not;
  `redo_target` is `None` without undos and for legacy `restore` versions; undo after redo.
- `tests/test_server.py`: `/redo` 200 and 409, `redo_target` in the detail, `/undo` source `undo`.
- `tests/js/commands.test.mjs`: the redo phrases match.
- `tests/e2e/smoke.mjs`: after Undo, Redo brings the vnet back and records a version; the button is
  disabled afterwards.

## Migration/compatibility

Additive. No stored data changes. Clients that display `source` see two new values. The Git
mirror's commit trailer shows `(undo)` / `(redo)`.

## Open questions

None.

## Decisions

- Redo derived from new `source` values `undo`/`redo` replayed as a stack, rather than a separate
  pointer stored per diagram or a new metadata field:
  [ADR-0008](../adr/0008-redo-from-undo-redo-version-sources.md) (Proposed).
- Shortcuts only outside text fields (conservative: never steal the browser's text undo). Recorded
  in ADR-0008.
