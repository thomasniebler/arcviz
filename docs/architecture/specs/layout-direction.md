# Layout direction

- Status: Implemented (milestone M8)
- Issue: #78
- ADR: [0017 Layout direction on the diagram, ELK not vendored](../adr/0017-layout-direction-on-the-diagram-elk-not-vendored.md)

## Summary

Every diagram is rendered top-to-bottom today (`flowchart TB`, hard-coded default of
`mermaid.render`). Wide deployments with many siblings read better left-to-right. The diagram gets a
persisted layout direction that the user (canvas toolbar) and the agent (a new tool) can change. The
direction is part of the model, so it is versioned, undoable and exported like any other change.
ELK, Mermaid's alternative layout engine for large nested graphs, is evaluated and **not** vendored
in this milestone (ADR-0017).

## Goals

- A diagram has a direction `TB`, `BT`, `LR` or `RL` (default `TB`) that every render uses: the
  canvas, live renders during a chat turn, version previews, comparisons, exports and the Git mirror.
- The direction is changed through the single mutation path (`operations.apply`), from the REST API,
  the UI and the LLM alike, and creates a version like every other operation.
- Existing stored diagrams load unchanged and keep their fingerprints.

## Non-goals

- Per-container directions (Mermaid ignores a subgraph's `direction` as soon as one of its nodes is
  linked from outside, so it would not do what users expect).
- Alternative layout engines (ELK) and manual positioning.

## Design

### Model

`model.py` gains `DIRECTIONS = ("TB", "BT", "LR", "RL")` and a field
`Diagram.direction: str = "TB"`. Older documents without the field validate with the default.
`Diagram.summary()` adds the line `Layout direction: LR` after the header only when the direction
is not the default, so prompts of existing diagrams do not change.

`TD` is accepted as an alias of `TB` by the operation (Mermaid treats them as equal) and stored as
`TB`.

### Operation

`operations.set_direction(diagram, direction)` normalises (`strip().upper()`, `TD` → `TB`) and
rejects anything outside `DIRECTIONS` with an `OperationError` listing the allowed values. It
returns `{"direction": <value>}`; `touched` is empty (no node changes). Registered in `OPERATIONS`.

The rollback in `operations.apply` restores every model field of the snapshot (not only
`nodes`/`edges`/`title`), so model fields added later are covered as well.

### Agent

A `ToolSpec("set_direction", …)` with `direction` as an `enum` of `DIRECTIONS`. The system prompt
needs no example; the tool description says when to use it ("wide diagrams with many siblings read
better LR").

### Rendering

`mermaid.render(diagram, …, direction=None)` uses `diagram.direction` when `direction` is `None`;
an explicit argument still wins (kept for callers that want a fixed direction). Subgraphs keep
emitting `direction <same>`.

### Fingerprint compatibility

`store.base.fingerprint()` drops `direction` from the hashed payload while it is the default, so
fingerprints of versions written before this change stay equal to fingerprints of the same content
afterwards (otherwise `undo_target` would treat an unchanged diagram as changed once).

### HTTP and UI

`GET /api/config` returns `directions`. The canvas toolbar gets a select *Richtung* (`TB ↓`,
`LR →`, `BT ↑`, `RL ←`) showing the current diagram's direction; a change runs `set_direction`
through `POST /ops` like the inspector does (which also ends a version preview or comparison, as
every edit does). It is disabled while busy.

## Testing

- `test_operations.py`: valid values, `TD` alias, lower case, invalid value (error lists values, no
  change), rollback leaves `direction` untouched.
- `test_mermaid.py`: render uses the stored direction for the root and subgraphs; explicit argument
  wins.
- `test_model.py`: default, old documents without the field, summary line only when not default.
- `test_store.py` (contract): fingerprint unchanged for a default direction, changed for `LR`;
  direction survives save/load and versions.
- `test_agent.py`: the tool exists with the enum; a scripted `set_direction` call persists.
- `test_server.py`: `/api/config` lists directions; `POST /ops set_direction` re-renders.
- e2e: switching the toolbar select to `LR` re-renders with `flowchart LR`.

## Migration/compatibility

No migration: the field has a default. API clients that post full diagrams are not affected.

## Open questions

None.

## Decisions

- Direction lives on the model, not in the view or the URL (ADR-0017).
- ELK is not vendored (ADR-0017, status *Proposed* until the maintainer confirms).
