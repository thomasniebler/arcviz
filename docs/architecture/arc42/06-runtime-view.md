# 6. Runtime view

The scenarios are Arcviz sequence diagrams (`docs/architecture/diagrams/runtime-*.arcviz.json`, import them into
the app to edit them), rendered by `make diagrams` with Arcviz's own SVG renderer. Their lifelines are building
blocks of the [server component view](05-building-block-view.md) (a lifeline id names its component, `services:…`
for two lifelines of one component), which `tests/test_architecture_docs.py` checks along with the renders (#120).

## 6.1 Chat turn (apply mode)

The central scenario. Details: [Agent](../../spec/agent.md), [LLM layer](../../spec/llm-layer.md).

![Chat turn (apply mode)](../diagrams/runtime-chat-turn.svg)

Finalization runs in a `finally` block: `commit_turn` writes latest and an automatic version, conditional on the last
revision, and appends the transcript; then the chat may be compacted in the background.

What the design guarantees, and where it falls short:
- `latest` is current after every operation, so a crash mid-turn loses no applied operation.
- Finalization runs in `finally`; when the client disconnects, the server closes the generator on a worker thread as
  soon as the response ends (#112, ADR-0126).
- An exception other than `OperationError` in a tool is rolled back and reported as an internal error
  tool result; the turn ends with `final` (#66).
- Every save is conditional on the revision the turn last saw. Another request on the same diagram
  during the turn is kept: the next tool call is re-applied on top of it, and finalization skips its
  version instead of overwriting a newer state (#65).

## 6.1b Project chat turn

A turn over the diagrams of a project (M17, [project chat](../specs/project-chat.md)). The model edits
one *active* diagram at a time; `open_diagram`/`create_diagram` switch it, and the tools offered in the
next round are those of its type (ADR-0058). Each diagram the turn edits follows 6.1: write-through
after every call, one automatic version per changed diagram at the end (ADR-0059).

![Project chat turn](../diagrams/runtime-project-chat-turn.svg)

The UI opens the diagram of a `tool_result` that is not the shown one, lists the changed diagrams
below the reply and marks them in the navigator. In propose mode a turn may change one existing
diagram only (ADR-0060).

## 6.2 Direct edit (inspector or REST)

![Direct edit](../diagrams/runtime-direct-edit.svg)

The same validation path as the LLM's (quality goal 1). Wrong argument *types* still produce a 500
instead of a 400 (#66).

## 6.3 Propose, then confirm

Details: [Propose then confirm](../specs/propose-confirm.md), [ADR-0015](../adr/0015-client-held-proposals.md).

![Propose, then confirm](../diagrams/runtime-propose-confirm.svg)

The proposal lives only in the client. The server re-checks it against the base fingerprint, and the
write is conditional on the revision of the state it checked (#65), so a stale proposal can't
overwrite newer work.

## 6.4 Architecture review

Details: [Architecture review](../../spec/review.md), [ADR-0024](../adr/0024-llm-review-as-one-validated-tool-call.md).

![Architecture review](../diagrams/runtime-review.svg)

The **project review** (M17, [project review](../specs/project-review.md)) follows the same shape for a
project: `POST /api/projects/{pid}/review` reads the members through `ProjectService.load_members`, runs
the project rules and every diagram's rule checks (`project_review.run_project_checks`), then one
`complete_tool` call over a budgeted digest of all diagrams; findings name their diagram, and the UI
opens that diagram before highlighting the elements.

## 6.5 Undo and restore

`POST /undo` asks the store for `undo_target` (walk back to the most recent version with a different
fingerprint, skipping manual snapshots and following `restored_from`) and records the restored state as a
**new** version with source `undo`. Redo replays the undo stack ([Redo](../specs/redo.md)). History
is never rewritten, so every state that was ever saved remains restorable
([ADR-0003](../adr/0003-snapshots-not-diffs.md), [ADR-0008](../adr/0008-redo-from-undo-redo-version-sources.md)).

## 6.6 Startup

`create_app` activates the kind catalogue (`ARCVIZ_KINDS_FILE`; an invalid file stops startup) and creates
the store (`ARCVIZ_STORE*`, plus the Git mirror). The provider and agent are created lazily on the first chat or readiness
probe. The docs mount is chosen once (section 7). On boot, the web UI creates the demo diagram
(`template: demo`) if the store holds no diagram yet.
