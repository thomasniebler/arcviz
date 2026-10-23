# Persistence

Source: `arcviz/store/` (`base.py`, `file.py`, `sqlite.py`, `cosmos.py`, `factory.py`, `migrate.py`) and
`arcviz/gitmirror.py`. Tests: `tests/store_contract.py` (run against every backend by
`tests/test_store.py`), `tests/test_store_file.py`, `tests/test_store_factory.py`,
`tests/test_migrate.py`, `tests/test_store_cosmos_live.py` (real endpoint, optional),
`tests/test_gitmirror.py`. Design and decisions: [Architecture: persistence](../architecture/specs/persistence.md),
ADRs 0002 to 0004.

## Repository

`base.DiagramRepository` is an abstract base class. Backends implement the *primitives*; the
*derived operations* are implemented once in the base and are what the application services call
(see [Application services](#application-services)); nothing else in the package calls a repository.
`arcviz.store.DiagramStore` is a deprecated alias of `FileStore`.

Derived operations that write take `expected` (a revision, HTTP `If-Match`): `update`, `snapshot_latest`, `restore`,
`undo`, `redo` then make one attempt and raise `RevisionMismatch` instead of re-applying (spec:
[Collaboration](../architecture/specs/collaboration.md)).

Primitives (abstract):

| Method | Contract |
|---|---|
| `list_diagrams()` | `DiagramInfo(id, title, updated_at, version_count)` for every diagram, sorted by id |
| `diagram_infos(ids)` | the `DiagramInfo` of every given id that exists, sorted by id (unknown ids left out): a caller's diagram list and a project view without listing the store (#181). File: `latest.json` and the version files per id; SQLite: one `IN (…)` query (chunks of 500); Cosmos DB: per id two single-partition queries (`SELECT c.title, c.updated_at FROM c WHERE c.id = @id`, `SELECT VALUE COUNT(1) FROM c WHERE c.type = @type`), never a cross-partition query |
| `exists(id)` | |
| `read_latest(id)` | `(Diagram, revision)`; `StoreError` if missing; the revision of data stored before revisions existed is `"0"` |
| `read_revision(id)` | the revision `read_latest` would return, without reading the diagram (live polling, #174, ADR-0122); `StoreError` if missing. File: `stat()` of `latest.json`, parsed (not validated) only when (inode, mtime, size) changed since the last call (a file younger than 2 s is never cached; at most 4096 diagrams are cached, least recently used go, a purge drops its entry); SQLite: `SELECT revision`; Cosmos DB: the single-partition query `SELECT VALUE c.revision FROM c WHERE c.id = @id` (an item without a revision: `"0"`) |
| `create_latest(diagram, info)` | atomic create-if-absent of latest **and** version 1 (`info`); returns `(revision, VersionInfo)`; `StoreError("… already exists.")` otherwise, nothing written |
| `commit_latest(diagram, expected, info)` | atomically: if the diagram exists and its revision is `expected` (any when `None`), writes latest with a new revision and, with `info`, appends the next version; returns `(revision, VersionInfo or None)`. Otherwise `ConflictError` and nothing written, in particular for a deleted diagram (#65) |
| `write_latest(diagram)` | unconditional create-or-replace with a new revision; `arcviz-migrate` only |
| `purge(id, expected=None)` | removes latest, versions and chat; `StoreError` if missing. With `expected` only if the latest revision is `expected`, atomically, else `ConflictError` and nothing removed (#175, ADR-0123): file under the diagram lock, SQLite `SELECT revision` and `DELETE` in one `BEGIN IMMEDIATE`, Cosmos DB the `latest` item deleted with `If-Match` on the ETag of the revision read, then the rest of the partition; a diagram deleted by another request in between is `StoreError` "not found" on every backend |
| `append_version(id, diagram, info)` | allocates the next version number atomically, stores meta + snapshot, returns `info` with `version` set |
| `list_versions(id)` | the version metadata, ascending (gaps after a prune), `[]` if none; reads no snapshot body in any backend (#114, ADR-0129) |
| `put_version(id, diagram, info)` | stores a version under exactly `info.version` (replacing one with that number): the backend migration (`store/migrate.py`), so a history with gaps from a prune keeps its numbers and `restored_from` stays valid; the migration refuses a target diagram with versions but no latest (an interrupted run) unless `--overwrite`, which removes them first |
| `delete_versions(id, numbers)` | removes those versions (the retention prune, ADR-0130); a version already gone is skipped; never called for the latest version, so numbers are never reused |
| `load_version(id, n)` | `(VersionInfo, Diagram)` or `StoreError` |
| `load_chat(id)` | ascending by `seq`; never returns entries older than the newest `summary` entry |
| `append_chat(id, entries, generation=None)` | assigns consecutive `seq` values (highest stored + 1, …); `StoreError` for an unknown diagram (a deleted diagram's transcript is never recreated; `clear_chat` and `import_chat` likewise); with `generation`, only while the transcript has it, checked atomically with the write: `ConflictError` and nothing written otherwise (#143, ADR-0127) |
| `chat_generation(id)` | the transcript's generation, an opaque token; `""` for an unknown diagram (creates nothing); a transcript without one gets one on first read |
| `compact_chat(id, upto_seq, summary, generation=None)` | replaces all entries with `seq <= upto_seq` by `summary` (stored with `seq = upto_seq`); returns `False` and writes nothing when no entry with `seq == upto_seq` exists any more (compacted past it while the summary was produced) or `generation` is given and is no longer current (cleared, imported, or deleted and created again; #67) |
| `clear_chat(id, generation=None)` | removes every entry and starts a new generation; with `generation` only while the transcript has it (`ConflictError` otherwise, ADR-0127) |
| `import_chat(id, entries)` | replaces the transcript verbatim, keeping `seq` (migration only); starts a new generation |
| `load_project_chat`, `append_project_chat`, `project_chat_generation`, `compact_project_chat`, `clear_project_chat` | the same for a project's transcript (project chat, [ADR-0057](../architecture/adr/0057-project-transcript-next-to-the-project-document.md)): keyed by the project id, `StoreError` for appends and clears of an unknown project, `""` generation for it; `delete_project` removes the transcript, `create_project` never inherits one |
| `list_documents(kind)`, `read_document(kind, key)`, `create_document(kind, key, body)`, `commit_document(kind, key, body, expected)`, `delete_document(kind, key, expected)` | keyed JSON documents of the kinds `workspace`, `user`, `placement`, `listing`, `rename`, `location`, `writer`, `proposal` (one per project: the stored proposals of agents) and `token` (one per user: the hashes of their personal access tokens) (`DOCUMENT_KINDS`, ADR-0082/0088/0100/0102/0120/0133/0172/0173; [agent access](agents.md)), keyed by an id-like key: `list` returns `(key, body, revision)` sorted by key; `create` is create-if-absent (`StoreError` "… already exists."); `commit`/`delete` are conditional on the revision (`ConflictError`). File: `<kind>s/<key>.json`; SQLite: table `documents`; Cosmos DB: item `<kind>` in partition `<kind>:<key>` |

Readiness (concrete in the base, overridable):

- `close()`: release what the backend holds open (SQLite: its connections); a no-op elsewhere.
- `ping()`: cheap reachability probe used by `GET /readyz`; returns `None` or raises. The default
  is `exists("readiness-probe")` (SQLite: one indexed `SELECT`; Cosmos DB: one point read, where 404
  is the normal answer and other HTTP errors become `StoreError`). `FileStore` overrides it, because
  its `exists` cannot fail: it raises `StoreError` unless `<root>/diagrams` is a directory the
  process may write to. `ping()` never writes and never creates a diagram.

Derived operations (concrete in the base):

- `create(title, diagram_id=None, diagram=None)`: id defaults to `slugify(title)` and must match
  `^[a-z0-9][a-z0-9_-]{0,63}$`; integrity check; `create_latest` with the `initial` version "Initial
  state" (so two replicas cannot create the same id twice, and nothing comes between the creation
  and version 1).
- `load(id)` / `load_latest(id)`: validates the id, then `read_latest(id)[0]`.
  `load_with_revision(id)`: the state and the revision a conditional write of a change must expect.
- `save_latest(diagram, expected=None)`: refuses a diagram with `integrity_errors()`, then
  `commit_latest` without a version; returns the new revision. Without `expected` the write is
  unconditional but never creates a missing diagram.
- `commit(diagram, expected, *, label, source="auto", restored_from=None, details=None)`: integrity
  check, builds the `VersionInfo` (label stripped and cut to 120 characters, fallback `Version N`,
  `created_at`, node/edge counts, fingerprint), `commit_latest` with it, then records the version in
  the Git mirror if one is attached (`details` only goes into the commit body). Returns
  `(revision, VersionInfo)`.
- `update(id, change, attempts=5)`: the read-modify-write every server-side mutation uses. Reads
  `(state, revision)`, calls `change(state)`, which returns a `Change(diagram, result, label=None,
  source="auto", restored_from=None, details=None)` (no version when `label` is `None`) or `None`
  (write nothing), and writes it conditional on the revision. On `ConflictError` it reads again and
  calls `change` again; after `attempts` it raises `ConflictError("… try again.")`. Exceptions of
  `change` propagate with nothing written; an unknown or deleted diagram raises `StoreError`. Returns
  `Updated(diagram, result, version, revision)`.
- `snapshot(diagram, label, source="manual", restored_from=None, details=None)`: appends a version
  without touching latest (`append_version`; tests and tools). The application uses
  `snapshot_latest(id, label)`, an `update` that records the current state as a `manual` version
  atomically with latest.
- `restore(id, n, source="restore", label=None)`: an `update` that writes version `n` (with a fresh
  `updated_at`) as latest and records a new version with `source` (default `restore`),
  `restored_from=n`, label `Restored version n (<old label>)` unless `label` is given. History is
  append-only. `StoreError` for an unknown version.
- `undo(id)` / `redo(id)`: the same with `undo_target` / `redo_target`, `source="undo"` / `"redo"` and
  label `Undo: restored version n (…)` / `Redo: restored version n (…)`; the target is recomputed on
  every attempt, so after a concurrent change undo undoes the newest state; `None` when the target is
  `None`.
- `delete(id, expected=None)`: `purge(id, expected)`, then `mirror.remove(id)` if a mirror is attached.
- `undo_target(id)`, `redo_target(id)`: see below; both are pure functions of the listing (`arcviz/history.py`:
  `undo_target_of`, `redo_target_of`).
- `history(id)`: one `list_versions` and both targets computed from it (`ports.History(versions, undo_target,
  redo_target)`); what `DiagramService.state` reads (#114, ADR-0129).
- After every write that added a version (`commit`, `snapshot`) the retention policy runs (`_retain`, ADR-0130):
  with `retention.keep > 0` and more than `keep + slack` versions, `history.prunable(versions, retention)` names the
  versions beyond the newest `keep` that may go — never the first and the latest version, the version that last
  cleared the redo stack and every undo/redo after it, the undo target and its cursor, the redo stack's entries, the
  `restored_from` of every kept version, and manual snapshots unless `prune_manual` — and `delete_versions` removes
  them. Best effort: a failure is logged and never fails the write. `retention` is the `HistoryConfig` that
  `create_store` reads from the environment (`None` for stores built directly: nothing is pruned). A prune is due
  (`history.prune_due`) from the new version's number alone, once the history can have outgrown `keep + slack` and
  then every `slack` versions, so commits do not list the history in between; it keeps the newest `keep + slack`
  versions (a grace, so a target picked from a list a moment ago survives).
- `load_kept_version(id, n)`: `load_version`, but a version missing between the first and the latest one (pruned) is
  `ports.VersionGone`, a `ConflictError` (HTTP 409 "reload the version list"); `restore`, `undo`/`redo` and
  `DiagramService.version` (preview, comparison) use it.

```python
class VersionInfo(BaseModel):
    version: int
    label: str  # trimmed, max 120 chars, fallback "Version N"
    created_at: str
    source: str  # "initial" | "auto" | "manual" | "restore" | "undo" | "redo"
    restored_from: int | None
    node_count: int
    edge_count: int
    fingerprint: str  # sha256 of the model without timestamps (and without a default direction), first 16 hex chars
    author: str | None = None  # M20: the user id of who caused the version; omitted from dumps while unknown
    author_name: str | None = None  # their display name at that moment
```

The derived operations that record a version take `author: Identity | None` (`create`, `commit`,
`Change.author` for `update`, `snapshot_latest`, `restore`, `undo`, `redo`); the bound `DiagramService` passes its
caller, so every version written for a request or a chat turn names its author, also the versions of the model
propagation (ADR-0086). The Git mirror adds `Arcviz author: <name> (<id>)` to the commit body. Data written before
has no author; the dump stays byte-identical.

### Undo target

`undo_target(id)` returns the version to restore for an undo, or `None`:

1. Needs at least two versions.
2. Find the cursor: walk back from the latest version over the run of versions with the latest
   fingerprint; the first one in that run with `restored_from` (a restore, undo or redo) moves the
   cursor to the version it restored, so that repeated undos walk further back instead of bouncing
   and a manual snapshot or no-op operation after an undo does not hide it. Without one, the
   cursor is the oldest version of that run.
3. Walk backwards and return the first version whose `fingerprint` differs from the cursor's.

Manual snapshots have the same fingerprint as the state they copy and are therefore skipped.
Undo and redo versions set `restored_from`, so undo after a redo walks back again.
`POST /undo` calls `undo()` (409 if there is nothing to undo).

### Redo target

`redo_target(id)` returns the version that the most recent not yet redone undo replaced, or `None`
(design: [Redo after undo](../architecture/specs/redo.md)). It replays all versions in ascending
order with a stack:

| Version source | Effect |
|---|---|
| `undo` | push the version current before it; current = `restored_from` |
| `redo` | pop; current = `restored_from` |
| `manual`, or any version with the same fingerprint as the current state (a no-op operation) | none |
| `initial`, `auto`, `restore` that changed the content | clear the stack; current = `restored_from` or else this version |

Resolving `current` through `restored_from` keeps `undo_target` (which follows one level of it)
correct after redoing past a restore. So any real change, or a restore chosen in the selector,
ends the possibility to redo; undos
recorded before redo existed (source `restore`) are not redoable. `POST /redo` calls `redo()`
(409 if there is nothing to redo).

### Transcript entries

Chat entries are dicts written by the agent (`role`, `content`, and for assistant entries
`operations`, `version`, `error`, `usage`; `proposed: true` for a propose-mode turn, and
`proposal_outcome` (`applied`/`discarded`) on the notes written when a proposal is confirmed or
discarded). The store adds `seq`, a positive integer that strictly increases
per diagram and is never reused within a transcript: after `compact_chat` the summary takes `upto_seq`
and the next append continues from the highest stored value. `clear_chat` starts again at 1, which is
why a transcript also has a *generation* (`chat_generation`): `clear_chat`, `import_chat` and deleting
the diagram replace it, and the agent passes the generation it read before loading the transcript to
`compact_chat`, so a summary of a transcript that no longer exists is never written (#67,
[ADR-0046](../architecture/adr/0046-transcript-generation-guards-compaction.md)). A `summary` entry (see [Agent](agent.md)) is always
the first entry a reader sees; `load_chat` hides anything older, which makes a staged compaction
(summary written, deletes not finished) invisible.

## Application services

Design: [Application services](../architecture/specs/application-services.md), ADR-0050. Tests:
`tests/test_services.py`, `tests/test_layering.py`.

`arcviz/ports.py` is the persistence contract as the application sees it: `DiagramRepositoryPort` (a
`typing.Protocol` with the derived operations above plus `name`, `ping()` and `git_status()`), the
value types `VersionInfo`, `DiagramInfo`, `Change`, `Updated`, the errors `StoreError`,
`ConflictError` and its subclass `RevisionMismatch` (a write on an expected revision found another one; `revision`
holds the current one, HTTP 412), `check_id`/`ID_RE`, `MUTATION_ATTEMPTS` and `Revision` (an opaque `str`).
`RepositoryPort` combines it with `ProjectRepositoryPort` ([Projects](projects.md#persistence)) for the
composition roots. `arcviz.store` and `arcviz.store.base` re-export these names; `fingerprint` lives in `model.py` and is
re-exported as well. Every `DiagramRepository` satisfies the protocol structurally.

`arcviz/services/` holds the services the entry points call. `DiagramService(repository)`:

| Method | Repository calls | Notes |
|---|---|---|
| `storage_name`, `git_status()`, `ping()` | `name`, `git_status`, `ping` | `/api/config`, `/readyz` |
| `list_diagrams()`, `exists`, `get`, `read` | `list_diagrams`, `exists`, `load`, `load_with_revision` | `read` returns `(Diagram, Revision)` |
| `state(id, page=50)` | `load_with_revision`, `history`, `load_chat` | `DiagramState` (with `revision`, the ETag): the newest `page` versions, `versions_total`, `versions_more`, the targets of the whole history |
| `versions(id, before=None, limit=None)`, `version` | `list_versions`, `load_version` | without `before`/`limit` every version, else the newest `limit` (default 50) older than `before` (`ports.page_of`) |
| `create(title, id, diagram, project=None, section=None)` | `exists`, `create` | a new id `<id>-<10 random base32 characters>` (`ports.new_ids`, ADR-0131; the suffix always holds a digit, and a generated suffix at the end of `<id>` is replaced, not extended: `ports.id_base`, #200, ADR-0150); a taken candidate, a lost race or leftovers that cannot be cleared now take a fresh suffix (at most `ports.CREATE_ATTEMPTS` 8, through `ports.id_attempts`, shared with the project service, #195); returns the created diagram |
| `import_new(diagram, title, id=None, …)` | `exists`, `create` | likewise, from `id` or else the document's id |
| `delete(id, expected=None)` | `read_revision`, `delete(expected=…)` | with `expected` (`If-Match`) one conditional attempt, `RevisionMismatch` otherwise (#175) |
| `update(id, change, expected=None)` | `update` | read-modify-write, retried on conflicts; with `expected` one attempt on that revision |
| `apply_operation(id, op, args, label, expected=None)` | `update` | `operations.apply` + one version |
| `snapshot`, `restore`, `undo`, `redo` (each with `expected=None`) | `snapshot_latest`, `restore`, `undo`, `redo` | return the `VersionInfo` (or `None`) |
| `write_through(id, diagram, revision, change, failed)` | `save_latest(expected=…)`, `load_with_revision` | the agent's live write: `LiveWrite(outcome, diagram, revision, result)` with `written`, `rejected`, `deleted` (gone or out of the caller's sight), `forbidden` (the caller's role dropped below editor), `busy` (after 5 conflicts); never raises for access |
| `commit_turn(diagram, revision, label, details)` | `commit` | `None` on a conflict, a deleted or invisible diagram; for a caller demoted during the turn only the revision `write_through` last wrote is versioned (ADR-0090); never raises for access; any other `StoreError` (a real store failure) is logged and raised, and the agents report it in `final.error` |
| `chat`, `chat_generation`, `compact_chat`, `clear_chat` | the chat primitives | `ProjectService` has the same methods for the project transcript ([Projects](projects.md#projectservice)) |
| `append_chat(id, entries)` | `append_chat`, `exists` | `False` when the diagram no longer exists |

With a `ProjectService` (`DiagramService(repository, projects)`), `create`/`import_new` check the
target project and section first, record the target (`ProjectService.intend`, a `placement` document that also
claims the id), create the diagram, place it and remove the record (`settle`); if the placement fails, the
diagram is deleted again (and the record removed) before the error propagates; if even that fails the record stays
and the repair puts the diagram into its project (ADR-0088). `delete` also unplaces the diagram; once the
diagram is gone a failing cleanup is only logged (the project repair drops the dangling membership).

`tests/test_layering.py` enforces the boundary: no module outside `store/`, `gitmirror.py` and
`server.py` imports `arcviz.store`, `sqlite3` or `azure`; `services/` imports only `model`, `ports`,
`operations`, `projects` and `exchange` from the package; `server.py` imports only `create_store` from the store and never calls
the repository it created; `services/` may also import `workspaces` and `live` (the in-process event bus); the server builds no service but the one
`ServiceRoot`, and every `/api` route with data takes the caller's services (`svc: Svc`; chat turns and live streams
`svc: TurnSvc`, whose guard does not cache project documents, ADR-0121).

`ProjectService` (projects, placement of diagrams, membership repair) is specified in
[Projects](projects.md#projectservice).

### Access policy and bound services

Design: [Personal workspaces and direct sharing](../architecture/specs/personal-workspaces.md), ADR-0084, ADR-0101.
Tests: `tests/test_access.py`, `tests/test_access_matrix.py`, `tests/test_layering.py`.

`services/access.py` holds the one access policy: `AccessPolicy(documents, projects)` reads workspaces and
projects through the ports; `guard(identity)` binds it to one caller. A `Guard` answers `own_workspace(id=None)`
(the caller's personal workspace, created if missing; any other id is `WorkspaceNotFound`), `project(id, need)`,
`project_doc(project, need)`, `diagram(id, need)` (the diagram's project), `role_of(project)` (`workspaces.project_role`:
owner of the project's personal workspace → owner, a share → its role, else None) and `owner_of(project)`, raising
`ProjectNotFound` or `DiagramNotFound` (`StoreError`s with the store's not-found message) when the caller may not see
the resource, `Forbidden` when the role is too low. It caches workspaces and the project of each diagram for its
lifetime (one request or chat turn); checks above viewer read the workspace again, and the project (with its shares)
is read with every check. `ensure_personal_workspace(documents, identity)` creates a user's personal workspace
(create-if-absent under `personal_workspace_id(user)`, `user_` + 32 hex digits of the SHA-256 of the id) and refuses a
document under that id that is not theirs.

`DiagramService(repository, projects=None, guard=None)` checks before every call: reading (`get`, `read`, `state`,
`versions`, `version`, `chat`, `role`, `access`) needs viewer; `update`, `apply_operation`, `apply_if_changed`,
`snapshot`, `restore`, `undo`, `redo`, `write_through`, `commit_turn`, `append_chat`, `compact_chat`, `clear_chat`
and `move` need editor (a move into another project also the owner of the source project); `delete` needs the project owner; `create`/`import_new` need editor on the target project
(through `ProjectService.check_target`). `list_diagrams` and `exists` leave out invisible diagrams;
`chat_generation` answers `""` for them, `append_chat` and `commit_turn` treat them as gone. A guarded
`DiagramService` must get the `ProjectService` bound to the same guard. `require(id, need)` is the check alone.

`ServiceRoot(repository, hub=None, admin=None)` (`services/context.py`) builds the user directory, the policy and a
probe for the store facts once, and hands the store's `presence_channel()` to the hub; `for_actor(identity)` returns
`Services(actor, guard, diagrams, projects, models, bundles, workspaces, users, live)` bound to that caller (the
`ProjectService` gets the directory for shares and the admin's personal workspace for the reserved default project);
`signed_in` also ensures the caller's own default project;
`signed_in(identity)` ensures the caller's personal workspace once per process (never raises); `migrate(admin)` runs
the migration to personal workspaces in the system context.

### Live events and `LiveService`

Design: [Collaboration](../architecture/specs/collaboration.md), ADR-0095..0098. Tests: `tests/test_live.py`,
`tests/test_server_live.py`.

`ServiceRoot` hands its hub (`arcviz.live.LiveHub`, an `EventSink`) to `DiagramService` and `ProjectService`
(`events=`), which publish hints after successful writes: `changed` (`diagram`, `author`,
`author_name`, `origin` `user`, or `assistant` for `write_through`/`commit_turn`) after every write of a diagram's
latest state, `chat` after transcript appends, clears and compactions (`diagram` or `project`), `project` after every
project write and deletion (also every share or revoke), `access` with `diagram` after a diagram's move or deletion.
Without a hub (system context, tests) nothing is published.

`LiveService(hub, repository, projects, guard)`: `watch_diagram(id)` / `watch_project(id)` (viewer; the
not-found error otherwise) build a `live.Watch` (diagram, project, workspace, role, revision, project revision);
`recheck(watch)` reads it again (project + revision with its shares, the workspace for the caller's role), locates a diagram that left its project again (`Guard.diagram(…, fresh=True)`)
and reads its revision, returning `None` when anything is gone for the caller (the project, workspace and revision
reads are shared by all streams through `LiveHub.shared` and invalidated by the events; the role is computed per
caller with `workspaces.project_role`); `open_diagram(id, session, loop)` / `open_project(id, session, loop)` (the
only way to open a stream: the watch is built after the access check);
`select(id, session, selection)` (`UnknownSession` without an open stream of the caller; with one, a shared point
read of its project instead of a scan); `chatting(diagram_id=None, project_id=None)` (a context manager; a diagram
the caller may not view, or one outside `project_id`, is not named). The hub, the presence registry and the stream loop
(`live.run_stream`) are specified in [HTTP server](http-server.md#etag-if-match-and-live-updates) and the design spec.

### Personal workspaces and the user directory

`UserService(documents)` (`services/users.py`): `seen(identity)` writes the directory record (`UserRecord(id, name,
email, email_trusted, first_seen, last_seen)`, key `user_key(id)`) when it is new, its name or e-mail changed, or on
the first request of a day; a per-process cache avoids other writes; failures are logged. `get(id)`, `find(id or
e-mail)` (exact; an e-mail matches trusted addresses only, case-insensitive; several matches are a `UserError`),
`records(ids)`.

`WorkspaceService(repository, users, guard)`: `mine()` → `WorkspaceSummary(id, title, owner, project_count,
shared_count, created_at)` of the caller's personal workspace (created if missing).
`migrate_to_personal_workspaces(repository, admin)` → `PersonalMigration(created, moved, reowned, renamed, failed,
shares, indexed, waiting_for_admin, hidden, stranded, repair_error, repair)` and `reassign_workspace(repository, missing, user)` are the migration and
the operator's recovery (system context; see [Migration](#migration) and the
[design](../architecture/specs/personal-workspaces.md#migration-servicesworkspacespy-179)).

## Backends

Selected by `ARCVIZ_STORE` (see [Configuration](../reference/configuration.md)); the factory
`store.factory.create_store()` is the only place that reads store configuration and also attaches
the Git mirror.

### `file` (default)

```
<ARCVIZ_DATA_DIR>/diagrams/<diagram_id>/latest.json          current state, plus its "revision"
<ARCVIZ_DATA_DIR>/diagrams/<diagram_id>/versions/0001.json   immutable snapshots {"meta", "diagram"}
<ARCVIZ_DATA_DIR>/diagrams/<diagram_id>/versions/0001.meta.json   the version's metadata alone (M23, #114)
<ARCVIZ_DATA_DIR>/diagrams/<diagram_id>/chat.json            transcript, every entry carries "seq"
<ARCVIZ_DATA_DIR>/diagrams/<diagram_id>/chat-generation      the transcript's generation (plain text)
<ARCVIZ_DATA_DIR>/projects/<project_id>.json                 a project document plus its "revision"
<ARCVIZ_DATA_DIR>/project-chats/<project_id>/chat.json       the project transcript (project chat)
<ARCVIZ_DATA_DIR>/project-chats/<project_id>/generation      its generation (plain text)
<ARCVIZ_DATA_DIR>/workspaces/<id>.json, users/<key>.json     keyed documents {"document", "revision"}
<ARCVIZ_DATA_DIR>/placements/<diagram_id>.json               the target of a diagram being created (ADR-0088)
```

Writes go through `_atomic_write` (temp file in the same directory, `json.dump(indent=2,
sort_keys=True, ensure_ascii=False)`, `os.replace`), so readers never see a partial file.
`create_latest` claims `latest.json` with `O_CREAT | O_EXCL` (`_claim`, under the diagram lock) and then replaces the
empty file; a claim left empty by a create that died before writing (empty or whitespace only) is reclaimed by the
next create of that id only when the directory holds at most version 1 and no transcript (what a dead create leaves);
those leftovers are moved to `reclaimed/<id>-<token>/`, never deleted or inherited. An empty `latest.json` with more
versions or a transcript is a diagram whose latest state was lost: the create is refused (`StoreError`, restore
`latest.json` from the newest version file) and nothing is touched (#168). `purge` writes a tombstone `.purging`
(`started`) first, records `latest removed` in it once `latest.json` is gone (removing the tombstone again if that
failed) and removes it last: a directory without `latest.json` whose tombstone records `latest removed` holds debris, which a
create moves aside; without it the dead-create rule applies, and a refused create removes its own claim again. `exists()` treats an empty file as absent,
like every reader, undecodable content as present, and raises `StoreError` for any other OS error. `_atomic_write`
(and `_atomic_write_text` for the plain-text transcript generation and the purge tombstone) `fsync`s the file before
the rename and the directory after it (#117);
`append_version` claims `NNNN.json` the same way and retries on collision, which makes numbering
safe across threads and across processes sharing the directory; it starts after the highest stored number, so a
prune never makes a number come back. Version files are ordered by their **number**, never by name: beyond 9999 the
file is `10000.json`, which follows `9999.json` (#117; the names of stored files do not change). After the snapshot it
writes the metadata sidecar `NNNN.meta.json`;
`list_versions` reads only the sidecars, and for a version written before M23 (no sidecar), or whose sidecar is damaged
(cut off, invalid, not UTF-8: it is only a cache), reads its snapshot once and writes the sidecar. `delete_versions` unlinks the snapshot, then the sidecar. `list_versions` and `list_diagrams`
skip files that are still empty (a claim whose writer died, or one still being written); `list_versions` also skips a
damaged snapshot without a sidecar (logged), so the rest of the history stays usable. `load_version` reports an
empty, cut-off or otherwise invalid version file as `StoreError` ("… is incomplete" / "… is damaged", HTTP 404),
never as a decoding error (#117); the next version gets the next free number, never the damaged one. `purge` raises
only `StoreError`: a temporary file that a write outside the diagram lock (a listing's sidecar) renames away or creates
meanwhile, or a subdirectory already gone, is tolerated (up to three passes), any other OS error becomes `StoreError` with the tombstone left in place,
so what is left is debris that the next create of the id moves aside (#117). `commit_latest`, `create_latest`, `write_latest`, `purge` and every
chat write hold a lock per diagram, shared by all `FileStore`s of the same data directory in the
process: `commit_latest` reads `latest.json`, compares the revision, writes latest and claims the
version file under it. Projects ([Projects](projects.md#persistence)): `create_project` claims
`projects/<id>.json` with `O_EXCL` (keyed documents claim `<kind>s/<key>.json` the same way; a claim left empty by
a crash before the write (empty or whitespace only, as readers treat it) is reclaimed by the next create of that id, under the per-key lock, since the data directory
has one writing process; readers treat empty files as absent); `commit_project` and `delete_project` compare the revision under
a per-project lock (key `project:<id>` in the same registry); `list_projects` skips empty and
temporary (`.tmp-*`) files. The project transcript shares the diagram transcript's implementation and
holds the `project:<id>` lock; `create_project` removes a leftover `project-chats/<id>/` after claiming
the id, `delete_project` removes it after the project file. A `chat.json` written before `seq` existed is numbered `1..n` on load. Treat a
data directory as single-writer across processes (the locks are per process).

### `sqlite`

One file, `<ARCVIZ_DATA_DIR>/arcviz.sqlite3`, WAL mode, one connection per thread. The store keeps every
connection it opened and closes all of them in `close()` and when it is garbage-collected (`weakref.finalize`;
connections are opened with `check_same_thread=False` so that this may run on another thread, #182); a thread's
connection is closed when the thread ends (a finalizer on its thread-local holder);
`DiagramRepository.close()` is a no-op for the other backends. Tables
`diagrams(id, title, updated_at, latest, chat_generation, revision)` (`chat_generation` and
`revision` are added to older databases on start-up), `versions(diagram_id, version, meta, snapshot)`,
`chat(diagram_id, seq, entry)`, `projects(id, revision, body, chat_generation)` (`chat_generation` added to
older databases on start-up), `project_chat(project_id, seq, entry)`; JSON stored as text. `delete_project`
and `create_project` delete the project's `project_chat` rows in their transaction. Project writes
are `INSERT` (a primary-key violation is `StoreError`), `UPDATE … WHERE id = ? AND revision = ?` and
`DELETE … WHERE id = ? AND revision = ?` (no row → `ConflictError`), each in `BEGIN IMMEDIATE`. `create_latest` (insert latest and version 1),
`commit_latest` (select the revision, compare, update, insert the version), `append_version`,
`append_chat`, `compact_chat`, `import_chat` and `purge` run inside `BEGIN IMMEDIATE … COMMIT`, which serialises writers across
threads and processes on one host. In `create_latest` the primary-key violation becomes `StoreError`.
Single host.

### `cosmos` (Azure Cosmos DB, NoSQL API)

One container, partition key `/diagram_id`; every item of a diagram lives in one logical partition.
Every request goes through `_MeteredContainer` (#201, ADR-0154): with telemetry on, each read, create, upsert,
replace, delete, query (per page) and batch passes the SDK a `response_hook` that records the response's
`x-ms-request-charge` in the histogram `arcviz.store.request_units` (a failed request's charge comes from the error's
headers, also for a query page that fails while the caller iterates: the results are wrapped by `_metered_pages`); its only attributes are `db.operation.name` (`telemetry.STORE_OPERATIONS`) and `arcviz.store.document`
(`telemetry.store_document`: `diagram`, `project`, `presence`, a keyed document kind, `any` for a query across
partitions, else `other`), never an id or partition key. With telemetry off the container's methods are called as they
are.
Items are distinguished by `id` and `type`: `latest` (with `revision`), `version:NNNN` (`version`, `meta`, `snapshot`;
`list_versions` is the single-partition projection `SELECT c.version, c.meta … ORDER BY c.version`, never the
snapshots, and `delete_versions` deletes in batches of the diagram's partition, #114),
`chat:NNNNNN` (`seq`, `entry`), `chat-generation` (`generation`). A project is one item `project`
(`type: "project"`, `revision`, `project`) in its own partition `project:<project_id>` (no diagram id
contains `:`); diagram listings query `type = "latest"` and never see it. The project transcript lives in the same
partition as `chat:NNNNNN` and `chat-generation` items, with the diagram transcript's implementation and
the `project` item as anchor (an append is a batch of `read project` and `create chat:NNNNNN`);
`delete_project` deletes the remaining transcript items of the partition after the project item, each in a
one-operation batch conditional on the ETag it listed (never a `project` item, nothing when the project was
created again meanwhile, and no item that a new incarnation wrote under a reused id), and
`create_project` removes leftovers of a deleted project before creating the item: after listing them it
checks again that the project is absent (a concurrent create of the same id that won meanwhile makes it fail
without deleting anything), and each delete is again conditional on the listed ETag. `create_project` creates
it (409 → `StoreError`, reconciled after a transient error like every create); `commit_project` and
`delete_project` read it, compare the revision and run a one-operation batch (`replace` or `delete`
with `if_match_etag`), where 412/404 is a `ConflictError` and a replay after a transient error first
checks whether the write landed (own revision present, or the item gone). Version and seq numbers are allocated by querying the current
maximum and creating the deterministic id; a `409 Conflict` means another writer won and the next
number is tried. That is safe across replicas. `commit_latest` reads `latest` (revision and `_etag`),
compares the revision and runs one transactional batch: `replace latest` with `if_match_etag` plus
`create version:NNNN`; 412 or 404 means a conflict (`ConflictError` when the expected revision is gone,
another attempt otherwise), 409 on the version id takes the next number. `create_latest` is a batch of
two `create`s (latest and `version:0001`). `append_chat` writes each entry as a batch of `read latest`
and `create chat:NNNNNN`, so it fails with 404 once the diagram is deleted. After a transient error a
batch is replayed only if the item does not carry the writer's own revision (or content) yet, so a
lost response is not mistaken for a conflict. Every attempt sends fresh option dicts: the SDK pops
`if_match_etag` out of the dict it is given, so replaying the same list would drop the condition.
`purge` deletes `latest` first and alone, then everything a second query finds, so a commit or append
racing with the deletion fails instead of leaving items that a re-created diagram would inherit. `compact_chat` replaces the item at `upto_seq` (a 404 on it means the fold is gone and is skipped) and,
with a generation, also replaces the `chat-generation` item conditional on the ETag read just before
(a 412 means the transcript was cleared or imported meanwhile and the fold is skipped); Every chat item carries the `generation` it was written in (appends, summaries, imports). Every `append_chat` and `compact_chat` adds the same guard to its batch, also without a `generation` (then bound to the one it reads; a changed one makes the append go into the new transcript and the compaction return `False`); a 412 from another write that touched the item re-reads the generation: the same one retries, another one is a `ConflictError` for an append with a `generation`. `load_chat` hides items of another generation (and untagged ones once the generation is `strict`, which a clear or import makes it), and `clear_chat` writes the new generation in one batch with a read of the owner (a `replace` conditional on the ETag of the current one; another write that touched the item only makes it read again) and then sweeps every item of another generation (untagged ones too) in batches guarded by the new generation: an append in the new generation only makes it re-read the item, a newer generation stops it (the transcript of an owner deleted and created again, or a later clear, is never touched) (ADR-0127); it runs as one
transactional batch when the generation guard, the summary and the deletes fit into 100 operations; larger folds are staged (guard and summary first,
then deletes in batches, each again guarded by the generation, so a clear in between stops the cleanup
instead of deleting fresh entries that reuse the ids), which `load_chat`'s hiding rule makes invisible to readers. Reads use the
account's session consistency (read-your-writes per client). Throttling is retried by the SDK; on top
of that every SDK call is wrapped in `_with_retry`, which retries transient failures (HTTP 408, 429,
449, 500, 503 and `azure.core` connection errors) up to six times with exponential backoff, because
25 concurrent writers against the emulator surfaced such errors. A create that is replayed after such
an error may hit a 409 for its own, already landed write; the id is then read back and compared, and
only a different item counts as a conflict (a 409 without a preceding transient error is always a
conflict). Replayed batches tolerate deletes that already happened. Other HTTP errors become `StoreError`. Authentication: `ARCVIZ_STORE_KEY`, or `DefaultAzureCredential`
(managed identity) when unset. With a key, database and container are created if missing; with
managed identity they must exist already (data-plane RBAC cannot create them) and a missing one is
reported as a `StoreError` at startup. The Docker image installs the `cosmos` extra. The dependency is the
optional extra `arcviz-agent[cosmos]`; the backend is written against the subset of
`ContainerProxy` that `tests/cosmos_fake.py` implements, so the contract tests run without a live
account. `tests/test_store_cosmos_live.py` runs the same contract against an emulator or account
when `ARCVIZ_TEST_COSMOS_URL` is set; CI does this in a non-blocking step when the store
changes and in the weekly run.

**Presence items** (#173, ADR-0104). `presence_channel()` returns the store itself (the base class and the file and
sqlite stores return None: presence stays in memory). `write_presence(project, replica, body, ttl)` upserts item
`replica` (type `presence`, `ttl` = ⌈ttl⌉ seconds, body under `presence`) in partition `presence:<project>`;
`read_presence(project)` is one query in that partition and returns the bodies whose `expires` (wall clock) has not
passed, deleting items with `expires + ttl` in the past on the way (conditional on their ETag, best effort);
`delete_presence(project, replica)` ignores a missing item. `connect` creates new containers with `default_ttl=-1`
(TTL on, nothing expires unless the item says so), and `ensure_ttl(database, container)` turns it on for an existing
container at every start (`replace_container(…, default_ttl=-1)` with every other setting the container has passed
back unchanged: partition key with kind and version, indexing and conflict resolution policies, analytical TTL,
computed properties, full-text and vector policies, since a replace resets what it does not carry; a container with a
setting a replace cannot carry (unique keys, client encryption, a secondary index definition) is left alone; a
refusal, e.g. managed identity with data-plane roles only, is logged and the readers clean up instead). Neither listing of diagrams, projects nor documents ever sees
these items (other types).

## Migration

`arcviz-migrate --from <spec> --to <spec> [--diagram <id>]... [--overwrite]` with store specs
`file:<dir>`, `sqlite:<dir-or-file>`, `cosmos:<endpoint>` (key, database and container from the
environment). Copies latest, every version with its original number and timestamp (`append_version`
into an empty diagram, in order) and the chat via `import_chat`, then every project document
(`create_project`, new revision). Existing diagrams and projects on the target are skipped unless
`--overwrite` (projects are then deleted and created again). Afterwards it runs the project repair
(`ProjectService.reconcile`, see [Projects](projects.md#projectservice)) on the target, so every copied
diagram is in a project. `--to file:<dir>` doubles as a human-readable export.

`arcviz-migrate --projects <spec> [--adopt DIAGRAM=PROJECT]...` only runs the repair on one store: diagrams in no
project go into the project they were created for (their placement record) or, without one, into the default
project; dangling and duplicate memberships are resolved; each change is printed (`adopted <id> -> <project>`, …, or
`projects: nothing to repair`). An orphan whose recorded project is gone is printed as `quarantined <id>: …` and
stays in no project; `--adopt DIAGRAM=PROJECT` (repeatable) places it, before the repair runs. `--projects` cannot be combined with
`--from`/`--to`; a `StoreError` prints `error: …` and exits with 1.

`arcviz-migrate --workspaces <spec> [--admin USER] [--reassign MISSING=USER]...` runs the migration to personal
workspaces of [personal workspaces](../architecture/specs/personal-workspaces.md) on one store
(`services.migrate_to_personal_workspaces`, which the server also runs at every start with `ARCVIZ_ADMIN_USER`):
every project that is not in a personal workspace moves, with one conditional write, into the personal workspace of
its M20 workspace's owner, or of its explicit M20 owner among the members (its creator; several: the first by id; the
former workspace owner and other explicit owners become editors; printed as `note: project <id> moved to its explicit
owner …`), with every other member's M20 effective role as a share (roles of non-members are dropped) or, without a workspace (v0.1.8 data, or `default` while it does not exist), of the
admin; the target workspaces are created first; the M20 workspace documents are kept. Then the project repair runs
(best effort) and what it created is moved too. It prints `created the personal workspace of <user>`, `moved project
<id> -> the personal workspace of <user>`, `shares written: <n>`, the repair, a note when projects wait for an admin
(they stay hidden until `--admin` is given), notes for stranded projects, or `workspaces: nothing to migrate`.
Idempotent. `--reassign MISSING=USER` (`services.reassign_workspace`) moves the projects of a workspace that does
not exist into the personal workspace of USER without shares (the default workspace is never "missing": its data
waits for the admin) and prints `reassigned project <id> -> the personal workspace of <user>`. `--set-owner` is gone
with team workspaces. The server runs the same
The copy also covers the keyed documents (workspaces and the user directory, `list_documents`/`create_document`;
`--overwrite` replaces existing ones).

The console script is `arcviz.cli:migrate_main` (a composition root like `server.py`, because the
repair is a service); `arcviz/store/migrate.py` keeps the storage-level `migrate()` copy, which returns
a `MigrationReport(copied, skipped, projects_copied, projects_skipped, documents_copied, documents_skipped)`.

## Git mirror

Source: `arcviz/gitmirror.py`. Tests: `tests/test_gitmirror.py`.

`GitMirrorConfig.from_env()` returns `None` unless `ARCVIZ_GIT_DIR` is set (`ARCVIZ_GIT_PUSH`,
`ARCVIZ_GIT_REMOTE`, `ARCVIZ_GIT_AUTHOR_NAME/EMAIL` are the other knobs). `create_store()` builds a
`GitMirror` from it and attaches it to the repository (`repo.mirror`), whatever the backend.

- Construction `git init`s the directory when it has no `.git`, and records an error when push is
  on but the remote is missing; it starts no thread and pushes nothing (the CLI builds stores too). `start()`, called by
  the server's lifespan (`ServiceRoot.start_mirror()` → `DiagramService.start_mirror` →
  `DiagramRepository.start_mirror`), asks for one push when push is on, the remote is configured and the repository
  has a commit: commits that a previous process could not push before it stopped are pushed without waiting for a new
  commit (#113 review).
- `DiagramRepository.commit(…, details=None)` (and `snapshot`) call `mirror.record(diagram,
  subject=label, body)` after the version is stored; `body` is `details` plus a trailer `Arcviz version N (source)`.
  The agent passes the applied operations (one `- op {json args}` line each) as `details`.
- `record()` writes `diagrams/<id>/diagram.json` (model without `updated_at`, sorted keys) and
  `diagrams/<id>/diagram.mmd` (`mermaid.render`), stages the directory, and commits only if the
  index differs (an unchanged manual snapshot makes no commit). With push enabled the commit only **asks for a
  push** (#113, [ADR-0151](../architecture/adr/0151-git-pushes-on-a-background-worker.md)): a daemon thread
  `arcviz-git-push` (started on the first request, again after `close`) runs `git push -q -u <remote> HEAD` with a
  timeout of `PUSH_TIMEOUT_SECONDS` (60 s); requests made while a push is pending or running are coalesced into one
  more push (each push sends everything committed so far). So a slow or unreachable remote delays no request:
  commits stay on the request path, ordered by the mirror's lock, and only the push waits.
- `drain(timeout)` waits until no push is pending or running; `close(timeout=DRAIN_SECONDS)` (10 s) drains, then stops
  the worker, and logs a warning when a push was still pending. The app's lifespan calls it at shutdown through
  `ServiceRoot.drain_mirror()` → `DiagramService.drain_mirror` → `DiagramRepository.drain_mirror` (true without a
  mirror). **Shutdown semantics:** a push still running after `DRAIN_SECONDS` (a push may take up to
  `PUSH_TIMEOUT_SECONDS`) is abandoned with the process; the commits stay in the local repository and are pushed by the
  next process at start (or by the next commit). On ephemeral storage the mirror directory must outlive the process
  (a volume) for that; otherwise commits not pushed at shutdown are lost from the mirror (the store keeps every version).
- `DiagramRepository.delete()` calls `mirror.remove(id)`, which `git rm`s the directory and commits
  `Delete diagram <id>`.
- All git calls go through `subprocess` with author/committer set from the config,
  `GIT_TERMINAL_PROMPT=0` and a timeout (`GIT_TIMEOUT_SECONDS` 120 s; a push `PUSH_TIMEOUT_SECONDS`). `record`/`remove`
  and the push worker never raise: errors are logged and kept apart, under a lock: `commit_error` (set by a failed
  commit or removal, cleared by a successful one) and `push_error` (set by a failed push or a missing remote, cleared
  only by a successful push), so a push never clears a commit's failure; `last_error` joins the two. `status()`
  (exposed as `git` in `/api/config`) reports `enabled`, `dir`, `push`, `remote`, `commits`, `push_pending`,
  `last_commit`, `last_error`, `commit_error`, `push_error`. The remote and git's output pass through
  `redacted()` (user info and query of every URL become `***`) before they reach a log record, `last_error` or
  `remote`; log records name the exception type, never git's output.
- The mirror has its own `RLock`, so commits are serialised; the push worker does not take it (git's own ref
  locking keeps a push and a concurrent commit apart).

## Concurrency model

Every write of latest by the application is a compare-and-swap on the revision it was computed from
(#65; design [Concurrent mutations](../architecture/specs/concurrent-mutations.md),
[ADR-0044](../architecture/adr/0044-conditional-writes-on-a-latest-revision.md)). `update()` retries a
conflicting change by reading again and applying the change again (up to 5 attempts, then
`ConflictError`, which the server answers with 409); a write that records a version writes latest
and the version atomically. What each path does on a conflict:

| Path | On conflict |
|---|---|
| `POST …/ops`, restore, undo, redo, manual snapshot, proposal confirm | `update()`: re-load, re-apply, at most 5 attempts, then 409; a proposal whose base changed is a 409 `ProposalConflict` |
| The same with `If-Match` (#86, ADR-0094) | `update(…, expected=)`: one attempt; latest at another revision, or a write in between → `RevisionMismatch`, 412, nothing written |
| Agent tool call | re-load latest and apply the call again (up to 5 attempts); not applicable → error tool result |
| Agent finalization | no write: the turn records no version, its operations are already in the newer state |
| Create, import | none needed: `create_latest` is atomic (409 when the id exists) |

Consistency contract: latest is authoritative; no accepted change disappears without an error to its
request; right after a versioned write the newest version equals latest (during an agent turn latest
runs ahead of the versions until finalization); API responses are read after the write and contain the
request's change (and possibly later ones); nothing writes latest, versions or the transcript of a
deleted diagram again. The transcript is not revision-checked (append-only; compaction is guarded by
the generation, #67).

`file` and `sqlite` remain single-host (`file`: one writing process per data directory, because its
locks are per process); `cosmos` is safe with several replicas, also for concurrent edits of one
diagram.
