# Propose-then-confirm mode with diff preview

- Status: Implemented (milestone M7)
- Issue: #75
- ADR: [0015 Proposals are dry runs held by the client and replayed on confirm](../adr/0015-client-held-proposals.md)

## Summary

In the default mode every successful tool call is persisted immediately and the turn becomes a
version. The optional *propose* mode runs the same turn as a dry run: the operations are applied to an
in-memory copy of the diagram only, the canvas shows the result with the changed elements
highlighted, and the reply bubble lists the changes as a diff (added, removed, changed elements and
edges) with two buttons, **Übernehmen** and **Verwerfen**. Confirming replays the operations through
`operations.apply` on the stored diagram and saves the result as one version; discarding changes
nothing. The mode is a per-message choice made in the UI (a checkbox, off by default).

## Goals

- A propose turn never writes the diagram (`latest`, versions, Git mirror); only the transcript.
- The user sees what will change before it is persisted: highlighted render plus a structured diff.
- Confirming produces exactly the previewed state, or fails with HTTP 409 and changes nothing (the
  diagram changed meanwhile, or the replay did not reproduce the preview).
- All mutations still go through `operations.apply` (CLAUDE.md rule 2).
- No server-side state between the turn and the confirmation (restarts and several replicas are fine).

## Non-goals

- Editing a proposal (accepting some operations, rejecting others).
- Rendering removed elements as ghosts in the Mermaid diagram (the diff lists them; rendering is
  touched by other work and stays unchanged).
- Keeping a pending proposal across page reloads; reloading drops it (the diagram is unchanged, the
  transcript shows the proposed turn).
- A server-wide default; the mode is chosen per request.

## Design

### Diff: `arcviz/proposal.py`

A new module (imports `model`, `operations`, `store`):

```python
def diagram_diff(before: Diagram, after: Diagram) -> dict[str, Any]
```

returns

```json
{
  "title": ["Old", "New"],
  "direction": null,
  "nodes": {
    "added":   [{"id": "prod-vnet", "name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"}],
    "removed": [{"id": "old-db", "name": "old-db", "kind": "database", "parent": null}],
    "changed": [{"id": "service-a", "name": "service-a", "changes": {"parent": ["prod-deployment", "prod-vnet"], "properties.cidr": [null, "10.0.0.0/16"]}}]
  },
  "edges": {
    "added":   [{"id": "e4", "source": "service-c", "target": "orders-db", "label": "sql", "kind": "connection", "direction": null, "properties": {}}],
    "removed": [],
    "changed": [{"id": "e1", "source": "a", "target": "b", "changes": {"label": ["http", "https"]}}]
  }
}
```

`title` and `direction` (the layout direction, `[old, new]`) are `null` when unchanged. Node fields compared: `name`, `kind`, `parent`, and each property key
(`properties.<key>`, `null` for a missing side). Edges are matched by id; because edge ids
are reused (the first free `eN`), an id whose source or target differs counts as one removed and one
added edge, and `changes` covers `label`, `kind`, `direction` and each property key
(`properties.<key>`). Lists are sorted by id.

### Agent: `DiagramAgent.chat(..., propose=False)`

With `propose=True`:

- `base = fingerprint(diagram)` (the store's content hash) is taken before the turn.
- `execute` applies operations to the in-memory `diagram` exactly as before, but does **not** call
  `store.save_latest`. `get_diagram` sees the proposed state, so multi-round turns work.
- `tool_result` events carry the intermediate state as before (the live preview).
- `_finalize` does not save or snapshot. The transcript gets the user entry and an assistant entry
  with `operations`, `version: null` and `"proposed": true`.
- The `final` event has `version: null`, the proposed `diagram`/`mermaid` (changed elements
  highlighted) and

  ```json
  "proposal": {"base": "<fingerprint before>", "result": "<fingerprint after>",
               "operations": [{"op": "add_node", "arguments": {...}}], "diff": {...}}
  ```

  `proposal` is `null` when nothing was applied (pure answers, errors before the first operation) and
  in apply mode (the key is always present).

An error or a disconnect during a propose turn persists nothing but the transcript.

### Confirm and discard (`arcviz/proposal.py`, `server.py`)

```python
def apply_proposal(store, diagram_id, operations, base, result, label=None) -> tuple[Diagram, VersionInfo, list[str]]
def discard_proposal(store, diagram_id) -> None
class ProposalConflict(Exception): ...
```

`apply_proposal` loads the diagram; if `fingerprint(diagram) != base` it raises `ProposalConflict`
("The diagram changed since the proposal was made …"). It then applies every operation with
`operations.apply`; an `OperationError` becomes `ProposalConflict`. If the fingerprint of the result
differs from `result`, `ProposalConflict` as well. Only then: `store.save_latest`,
`store.snapshot(label=label or "Proposal applied", source="auto", details=<op lines>)` and a transcript
entry `{"role": "assistant", "content": "Proposal applied as version vN.", "proposal_outcome": "applied", "version": N}`.
Replays are deterministic on an identical base (ids come from `unique_id` and edge numbering), which
the `result` check verifies.

**Concurrency.** The base check detects any change made between the turn and the confirmation. Since
#65 it is atomic with the write: the replay runs inside `store.update`, whose write is conditional on
the revision the replay started from; a write landing in between makes the replay run again on the
new state, where the base check answers 409 if the content changed
([concurrent mutations](concurrent-mutations.md), ADR-0044). (Before #65 the check was repeated right
before an unconditional write, which left a window of a few milliseconds.)

`discard_proposal` appends `{"role": "assistant", "content": "Proposal discarded; the diagram was not changed.", "proposal_outcome": "discarded"}`
so the model sees in later turns that the proposal was not applied.

Endpoints:

| Endpoint | Body | Response |
|---|---|---|
| `POST /api/diagrams/{id}/chat` | adds `"mode": "apply" \| "propose"` (default `apply`) | SSE as before |
| `POST /api/diagrams/{id}/proposal/apply?focus=` | `{"operations": [...], "base": "...", "result": "...", "label": null}` | `{"version", "changed", ...detail}`; 404 unknown diagram; 409 conflict |
| `POST /api/diagrams/{id}/proposal/discard?focus=` | none | detail |

### Frontend

- A checkbox **Vorschau** in the composer row (`#propose-mode`, remembered in
  `localStorage["arcviz.propose"]`) sends `mode: "propose"`.
- On a `final` event with a `proposal`, the canvas keeps the proposed render, the banner reads
  *Vorschlag – noch nicht gespeichert*, the reply bubble gets the diff list (`describeDiff()` in the
  pure module `arcviz/static/proposal.js`: `+`/`−`/`~` lines in German) and the two buttons. The UI
  stays locked like during a turn (`setBusy(true)`), so nothing else can change the base meanwhile.
- **Übernehmen** posts the proposal; on success the detail is shown like after a turn; on 409 the
  message is shown in a red bubble and the current state is reloaded. **Verwerfen** posts discard and
  reloads the current state. Both end the lock.

## Testing

- `tests/test_proposal.py`: `diagram_diff` (added/removed/changed nodes and edges, properties, title,
  no changes); a propose turn with `ScriptedProvider` leaves `latest`, versions and the store
  untouched, writes the transcript with `proposed`, and the `final` event carries the proposal whose
  diff matches; apply mode has `proposal: null`; `apply_proposal` creates one auto version equal to
  the preview and a transcript note; stale base → conflict, nothing written; an operation that fails
  on replay → conflict; result mismatch → conflict; discard writes the note only; server endpoints
  (200, 404, 409) and the `mode` field (422 for an unknown mode).
- `tests/js/proposal.test.mjs`: `describeDiff`.
- e2e: a propose turn shows the diff and the banner, **Verwerfen** keeps the diagram, a second propose
  turn and **Übernehmen** creates a version.

## Migration / compatibility

Additive: `mode` defaults to `apply`, the `final` event gains `proposal` (always present, `null` in
apply mode), transcript entries may carry `proposed` / `proposal_outcome`. Existing data is unaffected.

## Open questions

None open; decisions taken without the maintainer are *Proposed* in ADR-0015.

## Decisions

- The proposal is a dry run whose operations the client sends back on confirm; no pending state on
  the server (ADR-0015, Proposed).
- Confirm requires an unchanged base and a replay that reproduces the preview; otherwise 409 and the
  user asks again (Proposed).
- The mode is a per-request opt-in from a UI checkbox, off by default (Proposed).
- Confirm and discard append a short assistant note to the transcript so the model knows the outcome
  (Proposed).
