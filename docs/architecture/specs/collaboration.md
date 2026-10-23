# Collaboration: notes, live updates with optimistic concurrency, presence

- Status: Implemented (milestone M10)
- Issues: #87 (notes on nodes and lifelines), #86 (optimistic concurrency and live push), #171 (presence); M21:
  #173 (presence across replicas), #176 (versioned watches)
- ADRs: [0092 Notes are an optional `description` set by the existing add/update operations](../adr/0092-notes-as-optional-description.md),
  [0093 Notes in the assistant's prompt are budgeted before containers collapse](../adr/0093-notes-in-the-prompt-are-budgeted.md),
  [0094 ETag is the latest revision; `If-Match` makes a write a single compare-and-swap (412)](../adr/0094-etag-and-if-match-on-the-latest-revision.md),
  [0095 One live stream per tab: in-process bus plus a revision poll, events are refetch hints](../adr/0095-live-stream-bus-plus-poll.md),
  [0096 A stream re-checks access on every event and every poll; it ends with an unexplained `gone`](../adr/0096-streams-recheck-access.md),
  [0097 Presence lives in process memory, per instance](../adr/0097-presence-in-process-memory.md),
  [0098 Streams are bounded: caps, lifetime, shutdown](../adr/0098-bounded-streams.md),
  [0099 The inspector keeps typed values and resolves a 412 field by field](../adr/0099-inspector-conflicts.md),
  [0104 Presence across replicas as expiring Cosmos DB items](../adr/0104-presence-across-replicas-in-cosmos.md) (#173, M21),
  [0105 A stream's watch is versioned](../adr/0105-versioned-watches.md) (#176, M21)
- Builds on: [Concurrent mutations](concurrent-mutations.md) (#65, ADR-0044: the latest *revision*),
  [Users, workspaces and sharing](workspaces-and-sharing.md) (M20, ADR-0084: access in the services),
  [Request limits](request-limits.md) (ADR-0048), [Shared project model](shared-project-model.md)

## Summary

Several people now work in the same workspace (M20). M10 makes that work together:

- **Notes (#87).** Deployment and activity nodes and sequence lifelines get an optional free-text `description`.
  It is set by the existing add/update operations (so by the assistant, the inspector and `POST /ops` alike),
  omitted from every dump while unset (old JSON, exports and fingerprints stay byte-identical), shown to the
  assistant in the diagram summary within a budget and edited in the inspector. A node or lifeline linked to a
  project-model element also shows the element's description, read-only.
- **Optimistic concurrency (#86).** Every diagram `GET` answers `ETag: "<revision>"` (the latest revision of
  #65). Mutating diagram endpoints accept `If-Match`; a stale one is answered **412** and nothing is written.
  Without the header the behaviour is today's: the server re-applies the change on the newer state.
- **Live push (#86).** One server-sent-event stream per browser tab (`GET /api/diagrams/{id}/live`, or
  `GET /api/projects/{id}/live` when no diagram is open), fed by an **in-process event bus** (instant within one
  instance) and a **revision poll** every few seconds (changes made on other replicas, Cosmos). Events carry the
  new revision and the author and tell the client to refetch. The UI re-renders at once; an open inspector keeps
  what the user typed and says "changed by X meanwhile"; saving sends `If-Match`, and a 412 shows both values to
  choose from.
- **Presence (#171).** The same stream carries who has the diagram (and the project) open, each viewer's selected
  element (a coloured outline with initials on the canvas) and "X is chatting with the assistant" during someone
  else's turn. Presence is ephemeral and in memory, **per instance** (see *Limitations*).
- **Security.** Streams and presence go through a `LiveService` bound to the caller. Only callers who may view a
  diagram can open its stream and appear in its presence; access is re-checked on every delivered event and every
  poll, and a stream whose caller lost access, or whose diagram was deleted or moved out of sight, ends with a
  `gone` event that does not say which (404 semantics). Streams are capped per user and per process, never hold a
  worker thread while idle and end on shutdown.

## Goals

- A note per node and lifeline that the assistant reads and the user edits, without changing existing data.
- No silent overwrite of a field another user changed while it was being edited in the inspector.
- Everybody looking at a diagram sees every change within a moment: at once on the same instance, after at most
  one poll interval across replicas.
- See who else is there and what they look at, without new infrastructure or runtime dependencies.
- Nothing about a diagram reaches a caller who may not view it, also not after their access ends.

## Non-goals

- Merging concurrent text edits (no operational transforms or CRDTs); the user chooses on a conflict.
- `If-Match` for the chat turns (a turn re-applies its operations by design, #65), for confirming a proposal (it is
  already conditional on the proposal's base fingerprint, 409), for `DELETE` (the store's delete primitive is not
  conditional) and for project or workspace documents.
- Rendering in the event (each client has its own focus); live chat transcripts across replicas (see below).
- Presence across replicas (needs shared infrastructure, see *Limitations*), cursors or typing indicators.

## Design

### Notes (#87)

**Model.** `model.Node.description: str | None = None` and `sequence.Lifeline.description: str | None = None`,
at most `MAX_DESCRIPTION = 2000` characters (pydantic `max_length`, so an import with a longer note is refused).
The wrap serializers that already omit an unset `ref` (M18) omit an unset `description` too, so a document
without notes dumps, exports and fingerprints exactly as before (`tests/test_compat_v018.py` checks the stores
written by v0.1.8; ADR-0092).

**Operations.** `add_node`, `update_node`, `add_lifeline` and `update_lifeline` take `description: str | None`.
`operations.clean_description` strips it; an empty string removes the note (`None` = unchanged on update); longer
than `MAX_DESCRIPTION` is an `OperationError` that names the limit. No new operation, so every path (tools,
`POST /ops`, batches, proposals) gets it, and the activity tools inherit it from the deployment tools. The tool
specs describe the argument; `tests/strategies.py` draws it.

**Prompt.** `Diagram.summary(budget)` shows a note after its node or lifeline as `— note: "<text>"` (line breaks
become spaces, at most `NOTE_CHARS = 200` characters, an ellipsis marks the cut). When the summary exceeds the
budget, notes are first shortened to `SHORT_NOTE_CHARS = 40`, then dropped to `— note (call get_diagram)`, and
only then are containers collapsed (ADR-0093). `get_diagram` returns the full notes (they are part of the model
dump). Sequence summaries apply the same steps to their lifelines.

**Rendering, export, comparison.** Notes are not drawn. The JSON export carries them; PlantUML ignores them. The
version comparison reports a changed note as a changed node (it compares model dumps).

**UI.** The node inspector has a **Notiz** textarea; the lifeline form gets the same field (`elements.js`, type
`textarea`). For a linked element the **Projektelement** section shows the element's description read-only under
the heading "Beschreibung im Projektmodell" (it is edited in the model panel).

### Optimistic concurrency (#86)

**ETag.** `GET /api/diagrams/{id}`, `/render`, `/mermaid` and `/export` (the current state, not `?version=`)
answer `ETag: "<revision>"`, and every diagram detail (also the answers of mutations) carries `"revision"`. The
revision is the opaque token of #65 (`"0"` for latest states stored before revisions). It identifies the diagram
state, not the response: the detail also holds versions and the transcript, which change without a new revision,
so the server ignores `If-None-Match`.

**If-Match.** Accepted on `POST /api/diagrams/{id}/ops`, `/link`, `/unlink`, `/versions` (snapshot),
`/versions/{n}/restore`, `/undo`, `/redo`. `"<rev>"` and a bare token are accepted; a list matches when any listed
tag is the latest revision; a weak tag (`W/"<rev>"`) never matches, since If-Match uses the strong comparison (RFC
9110); `*` anywhere and an empty value mean "no condition" (the
diagram only has to exist). The route passes the tags to the service method as `expected` (`ports.Expected`: one
revision or a tuple); access is checked first (404/403 before 412). The store's `update(..., expected=)` then makes **one**
attempt: it reads latest, raises `RevisionMismatch` (a `ConflictError`) when the revision is none of `expected`, and
writes conditionally on it, so a concurrent write in between is a `RevisionMismatch` too (ADR-0094). The handler
answers **412** `{"detail": "...", "revision": "<current or null>"}`. `snapshot_latest`, `restore`, `undo` and
`redo` take `expected` as well. Without `If-Match` nothing changes (re-apply on conflicts, 409 after
`MUTATION_ATTEMPTS`). Unknown routes keep ignoring the header.

### Live channel (#86)

**Hub (`arcviz/live.py`, no package imports).** `LiveHub` is the in-process event bus and presence registry:

```python
class LiveHub:
    def publish(self, event: dict[str, Any]) -> None: ...  # thread-safe, never blocks, never raises
    def subscribe(self, user: Identity, session: str, watch: Watch, loop) -> Subscription: ...  # StreamLimit
    def unsubscribe(self, sub: Subscription) -> None: ...
    def select(self, user_id: str, session: str, diagram: str, selection: str | None) -> bool: ...
    def chatting(self, user: Identity, diagram: str | None, project: str | None) -> ContextManager: ...
    def presence(self, sub: Subscription) -> dict[str, Any]: ...
    def close(self) -> None: ...  # shutdown: every stream ends at once
```

A `Subscription` has a bounded `asyncio.Queue` on the event loop that opened it; `publish` (called from worker
threads) hands an event to every matching subscription with `loop.call_soon_threadsafe`. A full queue is not an
error: the stream re-reads the state on its next check anyway. Matching: an event with `diagram` goes to streams of
that diagram, one with `project` to streams of that project (diagram streams know their project), one with
`workspace` to streams in that workspace, one with none of them (`access`) to every stream.

**Publishers (the services, through `EventSink`).** `ServiceRoot(repository, events=hub)` hands the hub to the
services as an `EventSink` (a protocol of `arcviz/live.py` with `publish`). They publish *after* a successful write:

| Service write | Event |
|---|---|
| `DiagramService._update` (operations, link/unlink, propagation, snapshot, restore, undo, redo, unlink on move) | `{"type": "changed", "diagram", "author", "author_name", "origin": "user"}` |
| `DiagramService.write_through`, `commit_turn` (both agents) | the same with `"origin": "assistant"` |
| `DiagramService.append_chat`, `clear_chat`, `compact_chat` | `{"type": "chat", "diagram"}` |
| `DiagramService.delete`, `move` | `{"type": "access", "diagram"}` |
| `ProjectService._update` (sections, membership, roles, elements, moves), `delete`, project chat | `{"type": "project", "project"}` (`"chat"` for the transcript) |
| `WorkspaceService` membership, ownership, delete | `{"type": "access", "workspace"}` |

Events are hints, never data: no diagram content, no render (ADR-0095). An `EventSink` failure is logged and
never fails the write.

**Stream (`LiveService`, `services/live.py`, bound to the caller).**

```python
@dataclass
class Watch:  # what one stream watches, as last checked
    diagram: str | None
    project: str
    workspace: str | None
    role: str
    revision: str | None
    project_revision: str


class LiveService:
    def watch_diagram(self, diagram_id: str) -> Watch: ...  # viewer, else DiagramNotFound (404)
    def watch_project(self, project_id: str) -> Watch: ...  # viewer, else ProjectNotFound (404)
    def recheck(self, watch: Watch) -> Watch | None: ...  # shared reads, the caller's role; None when gone
    def open_diagram(self, diagram_id: str, session: str | None, loop) -> Subscription: ...  # watch_diagram + hub
    def open_project(self, project_id: str, session: str | None, loop) -> Subscription: ...  # watch_project + hub
    def select(self, diagram_id: str, session: str, selection: str | None) -> None: ...  # viewer, UnknownSession
    def chatting(self, diagram_id: str | None = None, project_id: str | None = None) -> ContextManager: ...
```

A stream is only opened through `open_diagram`/`open_project`, which build the watch themselves after the access
check; there is no way to subscribe with a watch from elsewhere, and `recheck` re-checks any watch for its caller.

`recheck` reads the project with its revision, checks that the diagram is still a member (else locates it again:
a move within sight continues the stream with the new project), computes the caller's role from the workspace
document (`workspaces.project_role`), and reads the diagram's revision: three point reads, **shared by every stream**
(`LiveHub.shared(kind, key, load)`). A shared read is valid until an event invalidates it (a `changed`/`access`
event for the diagram, a `project` event, a workspace `access` event; an `access` event without a target clears
everything, also reads in flight, by bumping a global epoch; transcript events change nothing) or half a poll
interval passed (other replicas); concurrent re-checks wait for the one read, at most `LiveHub.shared_wait` (the
presence TTL): a read in flight longer than that counts as stuck, its waiters fail with `SharedReadTimeout` and the
next caller replaces the entry and reads afresh, so a hanging store never queues the worker threads behind one
lock. Failures are not cached. So one change
costs one read per document, fanned out to the N streams of that diagram, each still access-checked for its own
caller. **Staleness window:** a change made on the same instance is seen at once; one made on another replica
(say, a member removed there) at the latest after about `1.5 × poll_seconds` (half an interval of cache, plus the
stream's own interval). The cache holds at most `SHARED_CAPACITY = 4096` entries (least recently used go first; an entry in use, a
read in flight or its waiters, is never evicted, so no second read starts for it; the capacity must be at least 1)
and keeps only what a re-check uses: a diagram's revision (never the diagram), the project document (membership,
roles, workspace) and the workspace document (owner, members). The async loop (`live.run_stream`) runs it with `asyncio.to_thread`, so a stream holds a worker thread only for those
reads. A re-check is raced against the stream's close (`Subscription.closed`, set by `close`) and bounded by the
shorter of the remaining lifetime and `shared_wait`: shutdown, `replaced` and `stale` end the stream at once with
their `bye` even while the store hangs, a hanging re-check ends it with `bye: error` (`expired` at the end of its
lifetime), and the abandoned worker thread finishes on its own; the pruning exemption of a running re-check lasts
one TTL only. Invalidation counters (`generations`) exist only for cached keys and are evicted with them, so events
about documents no stream reads leave nothing behind. The stores report a missing document and a failed read both as `StoreError`, so a failed re-check looks
again (`LiveService._gone`: does the diagram exist, may the caller see it) before it answers `gone`; when the second
look finds it, the failure was transient and the stream ends with `{"type": "bye", "reason": "error"}` instead (the
client reconnects, which checks access again).

`run_stream` (in `arcviz/live.py`) produces the events of one stream:

1. `hello`: `{session, diagram, project, revision, project_revision, role, poll_seconds}` and a `presence`.
2. Then it waits on the queue with a timeout of `poll_seconds`. Every delivered bus event (a burst is drained and
   coalesced) and every timeout triggers `recheck` (ADR-0096). A failed recheck sends `{"type": "gone"}` and ends
   the stream; `gone` never says whether the diagram was deleted, moved out of sight or the caller lost access.
3. After a successful recheck: a new diagram revision sends `{"type": "changed", "diagram", "revision", "author",
   "author_name", "origin"}` (author and origin from the coalesced bus events; `null` when only the poll saw it,
   i.e. another replica wrote, or the poll ran between a write and its bus event; in that case the arriving bus
   event names the author in a second `changed` with the same revision); a new project revision or a moved diagram
   sends `{"type": "project", "project", "revision"}`; a bus `chat` event of the diagram's transcript sends
   `{"type": "chat", "diagram", "project"}`, one of the project's transcript `{"type": "chat", "diagram": null,
   "project"}` (the diagram UI ignores it); a changed role sends `{"type": "access", "role"}`. A queue that overflowed counts as a bus event.
4. Presence changes (a marker on the queue) send a `presence` snapshot, at most every `0.25 s` per stream
   (trailing edge, so the last state always arrives); each poll also sends one when it differs from the last.
5. `: keep-alive` comments when a poll found nothing; the stream ends after `max_seconds` with
   `{"type": "bye", "reason": "expired"}`, on shutdown with `"reason": "shutdown"`, and when a stream of the same
   caller and session replaced it with `"reason": "replaced"`. The end of a stream is a flag on the subscription
   (`Subscription.close`), not a queued message, so a full queue cannot lose it. Every wake-up of the loop marks the
   stream alive; one that has not woken up for the presence TTL is dropped from the hub and ended (`"stale"`).
6. **No change is lost to the client** (#213): a stream reports what its re-checks see until its `bye`; a change
   written after its last re-check, or whose re-check the end of its lifetime cut short, is not reported by that
   stream but is in the `revision` of the next stream's `hello`, which `live.js` compares with the revision it shows
   (and refreshes on a difference). Tests therefore never race a write against a stream's lifetime: they record what
   the stream yields (`tests/test_server_live.py`, `recorded_streams`) and wait for conditions.

**Routes.** `GET /api/diagrams/{id}/live?session=…` and `GET /api/projects/{id}/live?session=…` take `svc: TurnSvc` (no per-request project cache, ADR-0121)
and answer 404 for a resource the caller may not view (the same `watch_*` checks as other reads), 429/503 at the
caps (ADR-0098), else `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`. `session`
is a client-chosen id (`[A-Za-z0-9_-]{8,64}`, random per tab); it is scoped to the caller, and a new stream with
the same caller and session replaces the old one (reconnects keep their presence). A missing or invalid session
gets a random one (in `hello`).

**Configuration** (`LiveConfig.from_env` in `arcviz/live.py`, caps in `limits.LimitConfig`):

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_LIVE_POLL_SECONDS` | `5` | Revision poll and access re-check interval of every open stream (decimal allowed, ≥ 0.1) |
| `ARCVIZ_LIVE_MAX_SECONDS` | `900` | A stream ends after this long; the client reconnects at once |
| `ARCVIZ_LIVE_MAX_STREAMS` | `1000` | Open streams per process (503 beyond; 0 = unlimited) |
| `ARCVIZ_LIVE_MAX_STREAMS_PER_USER` | `20` | Open streams per user and process (429 beyond; 0 = unlimited) |

Cost of the poll: three point reads per stream and interval (≈ 0.6 reads/s per open tab at the default).

**Shutdown and threads.** Streams are async generators; the store reads run in the threadpool one at a time per
stream. The app's lifespan start-up (`server.close_on_shutdown_signals`) wraps the SIGINT/SIGTERM handlers that
uvicorn installed before it: a shutdown signal first calls `hub.close()`, which ends every open stream at once, and
then uvicorn's own handler, which starts the drain. uvicorn runs the lifespan *shutdown* only after that drain, so
closing the hub there would be too late; the start-up runs before any signal, so the wrap works however the app is
served (`arcviz`, `uvicorn --factory`, the e2e server, a uvicorn worker that keeps Python signal handlers). It needs
the main thread; elsewhere (and where the handler is not a Python callable) `ARCVIZ_LIVE_MAX_SECONDS` and uvicorn's
`--timeout-graceful-shutdown` bound the wait. The lifespan shutdown closes the hub again. `/healthz` and `/readyz` neither open nor count streams.

**Limits.** `rate_class` ignores `/live` and `/presence` (they are throttled separately); the presence body is
limited to `PRESENCE_MAX_BODY = 4096` bytes (`LimitConfig.body_limit`).

### Presence (#171)

**Registry.** Every subscription is a presence entry: session, user id, display name, diagram, project, selection
(an element id, at most 200 characters, or `null`), `seen` (refreshed by every check of its stream). An entry of
an ended stream lingers `LINGER_SECONDS = 5` so a reconnect does not flicker; streams whose loop has not woken up for
`max(3 × poll_seconds, 15 s)` are dropped and ended (a stream task that died or hangs). The registry is indexed by
project (a diagram's viewers are in its project), so a snapshot and a presence poke touch only the streams of that
project; pruning runs at most once a second, and never drops a stream whose re-check is running (`checking`; the loop
also marks itself alive before and after every re-check). `chatting(...)` marks the caller as chatting
on a diagram (diagram turn) or on the project and the turn's first active diagram (project turn) for the duration
of the turn (`server.py` wraps the turn's event generator). A project turn's `active` comes from the request: it is
only named when the caller may view it and it belongs to that project; otherwise the turn counts for the project
alone.

**Snapshot** sent to a stream: `{"type": "presence", "viewers": [{session, user, name, diagram, selection}],
"project_viewers": [{session, user, name, diagram}], "chatting": [{user, name, diagram, project}]}`: the
viewers of the stream's diagram, the viewers of its project (every stream of a diagram in that project and every
project stream) and who is chatting there. The caller's own entries are included; the UI hides its own user.

**Selection.** `POST /api/diagrams/{id}/presence` `{"session", "selection"}` → 204. The caller must be allowed to
view the diagram (404 otherwise, like every read) and the session must be an open stream of the caller on that
diagram (409 "Unknown live session" otherwise); a selection over 200 characters is 400. With an open stream the access
check is a shared point read of that stream's project and workspace, never a scan of every project. The latest value
always wins. Right after a move the stream may still name the old project: the check then locates the diagram
again, like a re-check, and re-points the stream (its project in the presence index) **before** the selection is
recorded or poked, so it is shown only to the viewers of the project the diagram is in now. The watch is
**versioned** (#176, [ADR-0105](../adr/0105-versioned-watches.md)): every update bumps `Subscription.watch_version`,
and `LiveHub.update_watch(sub, watch, based_on=…)` refuses an update based on an older version. A stream re-check
that started before the move and finishes after the selection re-pointed the stream is dropped (no events; its bus
events carry over) and the stream re-checks the newer watch at once, so it never points back to the old project;
`select` itself retries when a re-check won the race. The pokes to the other streams are throttled per stream to one per 250 ms with one trailing poke, so a
client sending faster costs no fan-out, and the streams throttle what they send (see above). The client debounces its updates by 250 ms and sends the selection
again after every `hello` (a new stream starts without one).

**Access.** Only open streams appear in presence, and streams exist only for viewers whose access is re-checked;
presence snapshots are only sent to such streams, so presence never reaches nor shows anybody who may not view the
diagram.

**Across replicas (#173, [ADR-0104](../adr/0104-presence-across-replicas-in-cosmos.md), superseding ADR-0097).**
With the `file` and `sqlite` stores (one instance) presence stays in process memory. With `cosmos`, `ServiceRoot`
hands the store's `presence_channel()` to the hub (`LiveHub.share_presence`), and one **relay thread** per process
(`arcviz-presence-relay`, started with the first stream or chat turn, stopped by `LiveHub.close`) runs
`LiveHub.sync_presence()` once per poll interval and soon after every presence change (a poke wakes it; at most
four cycles a second):

1. For every project this replica has entries for (open and lingering streams, chat turns), it upserts **one item
   per project and replica**: `{"replica", "expires": now + ttl, "viewers": [{session, user, name, diagram,
   selection}], "chatting": [{user, name, diagram, project}]}`, but only when the entries changed or the heartbeat
   (`ttl / 3`) is due; when nobody is left, it deletes the item. `ttl` is the presence TTL `max(3 × poll, 15 s)`.
2. For every project it has **open** streams for, it reads the other replicas' items with **one query per project**
   through `LiveHub.shared("presence", project, …)` (valid for half a poll interval: one query per project and poll
   whatever the number of streams), drops expired and malformed entries, keeps them per project, and pokes that
   project's streams when they changed. Entries of projects without open streams here are forgotten.

`presence(sub)` merges the remote entries of the stream's project with the local ones (a session present locally is
shown once, from here). Access is unchanged: an item of project P holds only streams that watch P on their replica
(access-checked there, ADR-0096), and it is shown only to streams that watch P here. A replica deletes its items on
shutdown; one that crashed stops refreshing them, so readers drop its entries after the TTL. A failing channel
(throttling, an outage) is logged once per failure streak and leaves local presence untouched.

**Cosmos items.** Partition `presence:<project>` (no diagram, project or document partition can have that form), id
the replica id (16 hex digits, random per process), type `presence`, `ttl` the TTL in whole seconds, the body
under `presence`. `read_presence` is one single-partition query (`type = "presence"`); it returns only items whose
`expires` lies in the future and deletes, best effort and conditional on the ETag it read, items far past their TTL
(`expires + ttl` passed: a container without TTL). New containers are created with TTL enabled (`default_ttl =
-1`: nothing expires unless the item says so).

**Cost and staleness.** Per replica and project with viewers: at most one query per half poll interval, one write
per change (at most four a second) and one per heartbeat. A selection made on another replica appears within about
`poll / 2` plus one relay cycle; an entry that left a project (a moved diagram, a revoked share) may be shown there
for that long, like every cross-replica change (`1.5 × poll`, ADR-0096).

### Frontend

- `static/live.js` (feature): one stream per tab, for the shown diagram (else the shown project), read with
  `fetch` + `readSse` (status codes: 401 → login, 404 → stop, 429/503 → back off 30 s; other failures and normal
  ends → reconnect after `liveBackoff(attempt)`); `session` is random per page load, kept in memory (a duplicated
  tab must not share it). Every `hello` compares the revisions with what the tab shows, so changes made while the
  stream was reconnecting are fetched. On `changed` with a revision
  other than `state.revision`: refetch the detail (debounced 150 ms) and `showDiagram(detail, {remote})`, unless a
  chat turn, a proposal, a version preview or a comparison is shown (then it retries when that ends); `chat`
  refreshes the transcript when the diagram chat is shown and idle; `project` refreshes the navigator (and follows
  the open diagram into another project it was moved to); a failed refetch is retried with a backoff, because the
  stream reports each change only once; `gone` shows "nicht mehr verfügbar" and reloads the list.
- `static/presence.js` (pure): initials, a colour per user, the viewers to show (own user hidden, one entry per
  user), selection marks and the chat notice text. `live.js` draws chips (`#presence`) next to the title, a
  coloured outline plus initials around each selected element after every render (`canvas.onRendered`), and a
  line above the chat input while someone else is chatting.
- `static/conflicts.js` (pure): `same`, `canonicalProperties`, `nodeValues` (a node's comparable field values),
  `remoteChange(base, theirs, mine)`, `fieldConflicts(base, theirs, mine)` (fields both sides changed, to different
  values), `parseEtag`, `liveBackoff`, `changedBy`, `displayValue` (ADR-0099).
- Inspector: it remembers the **base** (revision and values) of the element it shows. A remote change that leaves
  the element as it was rebases silently. One that changes it updates every field the user has not touched and
  marks the touched ones "Inzwischen von X geändert: <value>" while keeping the typed value; the base stays, so
  saving sends the old revision as `If-Match` and gets 412. A save with several operations (fields and container)
  is one `apply_batch`, so a 412 never leaves part of it written. On a 412 the inspector shows, per conflicting field,
  "Ihr Wert" and "Aktuell (X)" with **Meine Werte speichern** (rebase onto the current state and save again) and
  **Aktuelle Werte übernehmen** (discard); when only fields the user did not touch changed, it rebases and saves at
  once. Other direct edits (edges, direction, link/unlink) send the current revision; a 412 reloads and says so.
  Promote, undo, redo, restore and snapshots send no `If-Match` (deliberate actions on the whole diagram, which
  re-apply on the newest state as before). This tab's own writes never conflict with itself: its operations run
  one after another through `static/writequeue.js` (an edge input also commits on blur). Each write is bound to the
  diagram it was queued for: when another diagram is shown by the time its turn comes, it is dropped (with a status
  message), never sent to the new diagram. A form save captures its base (element id and revision) when Save is
  clicked and is sent with that revision **advanced only by this tab's own writes** completed since (the queue
  records each own write's `If-Match` → answer step and follows them); never another element's base, never the
  shown revision, so a remote change to the element meanwhile answers 412 even when the form shows another element
  by then (the status line says what was not saved). Other writes use the shown revision when they start. After an
  own write that leaves the form open (edge rows, a rename through the model, a save of another element) the base
  moves to the new revision. Once another diagram is shown, the outcome of a write to the old one (412, error,
  success, list label) never shows on the new one; a failure is named in the status line, and a dropped write keeps
  its "Nicht gespeichert" status. A remote value a `<select>` has no option for yet (a lifeline added meanwhile) gets
  one, and a save sends only the fields the user changed against the base. When the reload after a 412 fails, the
  inspector says so and keeps the form, without a conflict built from old values.
- `diagram.js` gains `notifySelection(id)` / `onSelection(fn)` (the inspector reports what it shows, `live.js`
  posts it) and `state.revision`.

## Testing

- `tests/test_notes.py`: model omission and fingerprints, the four operations (set, change, clear, limit, rollback),
  the summary and its budget steps, `get_diagram`, export/import, tool specs; `tests/test_compat_v018.py`: stores
  written by v0.1.8 dump without `description`; Hypothesis strategies draw notes.
- `tests/store_contract.py`: `update(..., expected=)` succeeds on the current revision, raises `RevisionMismatch`
  on a stale one and on a concurrent write, writes nothing; `snapshot_latest`/`restore`/`undo`/`redo` likewise.
- `tests/test_live.py`: the hub (routing, thread-safe publish, caps, replace by session, presence linger/TTL,
  throttling, close), `run_stream` with fake checks (hello, coalescing, poll-only changes without author, gone,
  expiry, shutdown, keep-alive), `LiveService` (viewer only, recheck after a member is removed, a project moved
  away, a diagram deleted or moved, role change), the services' events (operations, agent turns with
  `ScriptedProvider`, chat, project and workspace writes).
- `tests/test_server_live.py`: ETag on the GETs, `revision` in details, `If-Match` on every listed route (match,
  stale → 412 with nothing written, `*`, weak, absent → old behaviour), 412 after 403/404 ordering, the stream
  routes end to end with a short `ARCVIZ_LIVE_MAX_SECONDS` (events of a concurrent write arrive), 404 for
  outsiders, presence posts (404 for a foreign session), caps (429/503), limits (body size, no rate class),
  `/readyz` unaffected; `tests/test_access_matrix.py` classifies the new routes.
- `tests/test_live_review.py`: the findings of the reviews (presence injection through `active`, throttled
  selections without a project scan, shared reads (one per change, bounded, revision only, epoch), slow and hanging
  re-checks, project-transcript events, the lifespan's signal hook and re-opened hub, `If-Match` lists and the strong
  comparison, a selection right after a move, a client gone before the response started; third round: a moved
  selection shown in the new project only, a hanging re-check ended at once by shutdown or replacement and capped at
  `shared_wait`, stuck shared reads time out and are replaced, eviction keeps reads in flight, bounded
  generations); `tests/test_access.py`
  classifies every public method of `LiveService`.
- `tests/js/presence.test.mjs`, `tests/js/conflicts.test.mjs`, `tests/js/writequeue.test.mjs`, `modules.test.mjs`
  (new modules in `LAYERS`).
- `tests/e2e/smoke.mjs` step 18: two browser contexts (alice, bob) on one diagram: bob sees alice's presence chip
  and selection outline; alice's edit re-renders bob's canvas; bob's open inspector keeps his typed name and shows
  "Inzwischen … geändert"; saving gives the conflict choice, and choosing his value saves it; a stale `If-Match` is
  412; alice sees bob's chat notice during his turn (the e2e server slows the scripted model down with
  `ARCVIZ_E2E_TURN_DELAY` for this).

## Migration and compatibility

No data migration: notes are optional and omitted when unset; nothing else is stored. API: new fields
(`revision`, `description`) and headers only add to responses; `If-Match` is optional. Clients that ignore the
live routes behave as before.

## Decisions

- Scope of notes: nodes (deployment, activity) and lifelines; set by the existing operations; budgeted in the
  prompt; the linked element's description read-only (owner, 2026-10-08; ADR-0092, ADR-0093).
- In-process bus plus a configurable revision poll; no new dependency (owner, 2026-10-08; ADR-0095).
- `If-Match` with the revision ETag, 412 when stale, today's behaviour without it (owner; ADR-0094).
- UI: re-render at once, keep typed values with "changed by X meanwhile", choose on 412 (owner; ADR-0099).
- Events respect M20 access and are re-checked on role changes, deletes and moves (owner; ADR-0096).
- Presence: viewers plus selection, ephemeral, throttled, same channel (owner for the scope); per instance in M10
  (ADR-0097), across replicas with Cosmos DB as expiring items read in the poll, no new dependency (owner, M21,
  #173; ADR-0104).
- Caps, lifetime and the shutdown hook (ADR-0098, to review).

## Open questions

None blocking. Follow-ups worth an issue: presence across replicas (shared pub/sub), live transcripts across
replicas (poll the transcript generation). Done in M22 ([scale and robustness](scale-and-robustness.md)): `If-Match`
for deletes (a conditional delete primitive, #175) and a revision-only read primitive for the poll (#174:
`read_revision`, which `LiveService` re-checks use instead of `load_with_revision`).
