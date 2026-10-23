# Concurrent mutations: conditional writes on a latest revision

- Status: Implemented
- Issue: #65 (groundwork for #86, optimistic concurrency for clients)
- ADR: [0044](../adr/0044-conditional-writes-on-a-latest-revision.md)
- Depends on: [Persistence spec](persistence.md) (`DiagramRepository`), [Propose and confirm](propose-confirm.md)

## Summary

Every write path loaded a diagram, changed it in memory and wrote it back unconditionally. Locks and
transactions inside single store primitives did not cover that sequence, so two tabs, overlapping chat
turns, or a chat turn plus a direct edit, restore or undo silently overwrote each other's accepted
changes (reproduced on the file and SQLite stores). The agent even wrote its in-memory copy again during
finalization, and a turn that outlived a deletion recreated the diagram.

This change gives the stored latest state a **revision**, an opaque token that every write replaces,
and makes every application write a **compare-and-swap** on it: the write succeeds only if the
revision is still the one the writer read, otherwise the store raises `ConflictError` and writes
nothing. A write that records a version writes latest and the version **atomically**. On top of that
primitive each mutation path decides whether a conflict is retried (re-load, re-apply) or surfaced
as HTTP 409. A deleted diagram is never written again.

## Goals

- No accepted change is lost silently: two overlapping mutations either both end up in the latest
  state, or one of them gets an explicit conflict.
- Chat finalization never writes over a newer direct edit, restore or undo; a deleted diagram is not
  resurrected by an in-flight turn, a direct operation, a restore or a chat append.
- One primitive for all backends (file, SQLite, Cosmos DB), covered by the store contract.
- A defined consistency contract between the latest state, the versions and the API responses.
- A revision that #86 can expose as an `ETag` and check against `If-Match` without another storage
  change.

## Non-goals

- `ETag`/`If-Match` for HTTP clients and pushing changes to other clients (#86, M10).
- Making the `file` and `sqlite` stores safe for several *processes* (they stay single-process per data
  directory; the new locks are per process).
- Merging concurrent edits of the same element (re-applying an operation is the merge).
- Crash atomicity of the file store (#117).

## Design

### Revision

`latest` carries a revision: a random token (`base.new_token()`, 16 hex characters) written together
with the state. Every write of latest stores a new one, also a write with unchanged content. Data from
before this change has no revision and reads as `"0"`; the first write replaces it. Tokens are random,
so a diagram that is deleted and created again never repeats an old revision.

| Backend | Where |
|---|---|
| `file` | top-level key `"revision"` in `latest.json` (ignored when the diagram is parsed) |
| `sqlite` | column `diagrams.revision` (added to older databases on start-up) |
| `cosmos` | field `revision` of the `latest` item; the item's `_etag` makes the write conditional |

### Repository primitives

```python
class ConflictError(StoreError):
    """The latest state is not at the expected revision any more (or the diagram is gone); nothing was written."""

read_latest(diagram_id) -> tuple[Diagram, str]          # state and revision; StoreError if unknown
create_latest(diagram, info) -> tuple[str, VersionInfo]  # latest + version 1 atomically; StoreError if it exists
commit_latest(diagram, expected, info) -> tuple[str, VersionInfo | None]
write_latest(diagram) -> str                             # unconditional, for arcviz-migrate only
```

`commit_latest(diagram, expected, info)` atomically: if the diagram exists and (`expected` is `None`
or) its revision equals `expected`, writes `diagram` as latest with a new revision and, when `info` is
given, appends it as the next version with `diagram` as snapshot; returns the new revision and the
stored version. Otherwise it raises `ConflictError` and writes nothing, in particular when the diagram
does not exist (a deleted diagram has no revision to match, and `expected=None` never creates one).

`create_latest` now writes the initial version in the same atomic step, so no other write can come
between the creation and version 1. `load_latest(id)` becomes a derived method (`read_latest(id)[0]`).
`append_version` stays (migration); `append_chat` now raises `StoreError` for an unknown diagram
instead of creating its transcript.

| Backend | Atomicity of `commit_latest` and `create_latest` |
|---|---|
| `file` | a lock per diagram (process-wide registry keyed by data directory and id) around read-compare-write; the version file is claimed with `O_EXCL` as before. The chat functions, `purge` and `create_latest` take the same lock, so a deletion cannot interleave with a write in the process. |
| `sqlite` | one `BEGIN IMMEDIATE` transaction: select the revision, compare, update latest, insert the version. |
| `cosmos` | read `latest` (revision and `_etag`), compare, then a transactional batch of `replace latest` with `if_match_etag` and `create version:NNNN`. 412/404 → `ConflictError`; 409 on the version id → the next number. After a transient error (the write may have landed) the item is read back: our new revision means success, the expected one means "retry", anything else is a conflict. `create_latest` is a batch of two `create`s. `append_chat` is a batch of `read latest` and `create chat:NNNNNN`, so it fails for a deleted diagram. |

### Derived operations

```python
load_with_revision(diagram_id) -> tuple[Diagram, str]
save_latest(diagram, expected=None) -> str                 # integrity check + commit without version
commit(diagram, expected, *, label, source="auto", restored_from=None, details=None) -> tuple[str, VersionInfo]
update(diagram_id, change, attempts=MUTATION_ATTEMPTS) -> Updated | None
snapshot_latest(diagram_id, label, source="manual") -> VersionInfo
restore(diagram_id, version, source="restore", label=None)   # via update()
undo(diagram_id) / redo(diagram_id)                         # via update(); target recomputed per attempt
```

`commit` checks integrity, calls `commit_latest` with a `VersionInfo` and feeds the Git mirror.
`update` is the retry loop every server-side mutation uses:

```python
for attempt in range(attempts):
    diagram, revision = read_latest(diagram_id)        # StoreError (404) when the diagram is gone
    change = change_fn(diagram)                         # may raise (OperationError → nothing written)
    if change is None: return None                      # nothing to do (e.g. nothing to undo)
    try: return commit(change.diagram, revision, label=change.label, ...)
    except ConflictError: continue                      # someone wrote in between: re-load, re-apply
raise ConflictError("... changed by another request while this one was applied; try again.")
```

`Change(diagram, result, label, source, restored_from, details)` says what to write; `Updated` carries
it together with the stored version and the new revision. `MUTATION_ATTEMPTS = 5`.

### Decision per mutation path

| Path | On conflict | Why |
|---|---|---|
| `POST …/ops` | retry: re-load and re-apply the operation (up to 5 attempts), then 409 | The request names an intent ("add node X"), not a base state; re-validating it against the newer state is what the user asked for. An operation that no longer applies is a 400 with the operation's message. |
| Agent turn, after each tool call | retry: re-load latest (which already holds this turn's earlier operations) and re-apply the current call (up to 5 attempts) | Every successful call is committed at once, so the turn never holds more than one unsaved operation; the model and the live view continue on the rebased state. A call that no longer applies is an error tool result; a deleted diagram is an error tool result "The diagram was deleted." |
| Agent finalization (version of the turn) | no retry, no write: the version is skipped (`version: null` in the transcript) | Latest moved on after the turn's last committed call; the turn's operations are already part of that newer state, and whoever wrote it recorded it (or restored something on purpose). Writing would overwrite it. |
| Proposal confirm | retry the replay (up to 5 attempts); the base fingerprint check turns a real change into 409 `ProposalConflict` | Retrying covers writes that did not change the content (a manual snapshot); a content change makes the proposal stale, as before, but now the check is atomic with the write. |
| Restore, undo, redo | retry (up to 5 attempts), the undo/redo target recomputed from the current history each time, then 409 | Undo means "undo the latest change"; after a concurrent change the target is a different version. |
| Manual snapshot | retry: snapshot the then-current state | The request asks for a snapshot of the current state. |
| Create, import, template | none: `create_latest` is atomic and fails with 409 if the id exists (import picks the next free suffix) | A new diagram has no base to conflict with. |
| Delete | none: `purge` | Later writes of the deleted diagram fail; requests in flight answer 404 (re-load) or, for a turn, error tool results. |
| Chat append, clear, compaction | not revision-checked (the transcript is append-only); appends fail for a deleted diagram; compaction is guarded by the transcript generation (#67) | The transcript does not share the latest state's revision, so chat activity never conflicts with model edits. |

`ConflictError` that escapes a request handler (all attempts used) is answered by an exception handler
with HTTP 409 and the message in `detail`.

### Consistency contract

- **Latest is authoritative** and changes only through `commit_latest`/`create_latest` (the app) or
  `write_latest` (migration).
- **No lost update:** every application write is conditional on the revision its change was computed
  from. A change is either in the latest state or its request saw an error (400/404/409, an error tool
  result, or a skipped turn version whose operations are in the newer state).
- **Versions:** a versioned write stores latest and the version atomically, so right after it the
  newest version equals latest. Between an agent turn's first tool call and its finalization, latest
  is ahead of the newest version (the live state of the turn, as before); a turn whose finalization
  is skipped leaves its operations to the next version. Version numbers stay gap-free and linear.
- **API responses** are read after the write (read-your-writes): the returned `diagram`, rendering and
  `versions` contain this request's change and possibly later ones from concurrent requests; they
  never lack this request's change.
- **Deletion** is final for every request that started before it: nothing writes latest, versions or
  the transcript of a deleted diagram again. A diagram created later under the same id is a new
  incarnation with new revisions and a new transcript generation.

### Path to #86

The revision is already an opaque, write-unique token per diagram, so #86 can return it as `ETag` on
`GET /api/diagrams/{id}` and the mutation responses, and pass a client's `If-Match` to `update(...,
expected=...)`, which then makes one attempt and answers 412 instead of re-applying. Nothing else in
the store has to change.

## Testing

- Store contract (`tests/store_contract.py`, all backends incl. the Cosmos fake with ETags): the
  issue's reproduction (two copies, the second write conflicts and the first change survives); the
  revision changes on every write; a conflict writes neither latest nor the version; `update` retries
  and keeps both changes with a deterministic interleaving, gives up with `ConflictError`; 20 threads
  each adding a node through `update` lose nothing and the newest version equals latest; restore and
  undo retried around a concurrent change; nothing (latest, versions, chat) resurrects a deleted
  diagram; legacy data without a revision; `create_latest` writes version 1 atomically.
- Agent (`tests/test_concurrency.py`): a direct edit between two tool calls is kept and the second call
  is rebased; finalization after a newer direct edit or restore skips its version and keeps the newer
  state; a deletion during a turn ends with error tool results, a `final` event and no diagram; the live
  render follows the rebased state.
- Server: a concurrent write injected before the first commit of `/ops`, restore, undo, manual snapshot
  and proposal confirm is retried and both changes survive; exhausted attempts answer 409; a deleted
  diagram answers 404.

## Migration / compatibility

- Stored data needs no migration: a missing revision reads as `"0"`; SQLite gets the column on
  start-up.
- Repository API: `load_latest` is no longer abstract (backends implement `read_latest`);
  `create_latest` takes the initial `VersionInfo` and returns `(revision, info)`; `commit_latest` is
  new; `write_latest` returns the revision and is meant for migration only; `save_latest` takes an
  optional `expected`. Backends outside this repository have to implement the new primitives.
- HTTP: a mutation that keeps conflicting answers 409 (new for `/ops`, restore, undo, redo, snapshot).
  Agent finalization may record no version for a turn whose operations a newer write already contains.

## Decisions

- Revision + compare-and-swap with per-path retry instead of a lock per diagram (ADR-0044,
  *Proposed*): it is the only option that also works across Cosmos DB replicas, and it is what #86 needs.
- Retry by re-applying the operation for intent-based requests (ops, agent calls, restore, undo,
  snapshot), 409 only after 5 attempts; skip the turn version on a finalization conflict.
- The file store's per-diagram lock is per process; single-writer across processes stays documented.

## Open questions

None.
