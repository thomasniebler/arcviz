# Persistence: repository pattern and backends

- Status: Implemented (PR: refactor/store-repository)
- Issue: #31
- ADRs: [0002 Repository pattern](../adr/0002-repository-pattern-for-persistence.md),
  [0003 Snapshots not diffs](../adr/0003-snapshots-not-diffs.md),
  [0004 Cosmos DB NoSQL first](../adr/0004-cosmos-db-nosql-first-database-backend.md)

## Summary

Persistence is one concrete class, `arcviz.store.DiagramStore`, writing JSON files under
`ARCVIZ_DATA_DIR`. That ties deployments to a mounted volume and exactly one replica. This spec
replaces it with a repository: an abstract `DiagramRepository` whose storage primitives are
implemented per backend and whose versioning semantics are implemented once. Backends: `file`
(default, unchanged on disk), `sqlite` and `cosmos` (Azure Cosmos DB, NoSQL API). MongoDB, DynamoDB
and Firestore are designed for and can be added later without changing the contract.

The chat transcript's `seq` numbers and the `compact_chat` primitive defined here are required by the
[chat compaction spec](chat-compaction.md); this spec only provides the storage side.

## Goals

- `server.py` and `agent.py` depend on `DiagramRepository`, not on a backend class.
- Versioning semantics (snapshot sources `initial | auto | manual | restore`, linear history, restore
  recorded as a new version, `undo_target` walk over fingerprints, label trimming, integrity check
  before persisting) are implemented exactly once.
- Backends implement storage primitives only; a backend is roughly 150 lines.
- Version numbers are allocated atomically in every backend. Database backends are safe with several
  replicas; `file` and `sqlite` remain single-host.
- A contract test suite that every backend passes, parametrised over the available backends.
- `arcviz-migrate` copies all data between any two backends and doubles as export/backup.
- The `file` layout stays byte-compatible with today's `data/` directories (new `seq` field aside).

## Non-goals

- Diffs instead of snapshots (ADR-0003).
- Multi-tenancy, users or access control on stored data.
- Changes to the REST API beyond one informational field in `/api/config`.
- Cross-document transactions. Write order stays `save_latest` then `append_version`; a crash between
  the two leaves `latest` ahead of the last version, as today.

## Design

### Package layout

```
arcviz/store/
  __init__.py   # re-exports: DiagramRepository, VersionInfo, DiagramInfo, StoreError, fingerprint,
                #             create_store, store_from_spec, FileStore, DiagramStore (alias, one release)
  base.py       # DiagramRepository ABC: abstract primitives + concrete derived operations, fingerprint()
  file.py       # FileStore
  sqlite.py     # SqliteStore (stdlib sqlite3)
  cosmos.py     # CosmosStore (azure-cosmos, optional extra)
  factory.py    # create_store(env) and parse_store_spec("file:data")
  migrate.py    # arcviz-migrate console script
```

### Interface

`DiagramRepository` is an abstract base class with two groups of methods and a class attribute
`name` (`file` | `sqlite` | `cosmos`) that `/api/config` reports.

**Primitives** (abstract; a backend implements exactly these):

```python
# diagrams
def list_diagrams(self) -> list[DiagramInfo]                       # sorted by id
def exists(self, diagram_id: str) -> bool
def load_latest(self, diagram_id: str) -> Diagram                  # StoreError if missing
def create_latest(self, diagram: Diagram) -> None                  # atomic create-if-absent
#   writes the initial state only if no diagram with that id exists, otherwise raises
#   StoreError("already exists"); check and write are one atomic step in every backend (file:
#   exclusive creation of latest.json; sqlite: plain INSERT; cosmos: create_item / 409), so two
#   replicas creating the same id concurrently cannot overwrite each other.
def write_latest(self, diagram: Diagram) -> None                   # unconditional write
def purge(self, diagram_id: str) -> None                           # latest, versions, chat; StoreError if missing

# versions
def append_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> VersionInfo
#   `info.version` is ignored on input; the backend allocates the next number atomically, writes
#   meta + snapshot in one step and returns `info` with `version` set. Two concurrent callers must
#   never receive the same number.
def list_versions(self, diagram_id: str) -> list[VersionInfo]      # ascending; [] if none
def load_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]   # StoreError if missing

# chat transcript
def load_chat(self, diagram_id: str) -> list[dict[str, Any]]       # ascending by seq; [] if none
def append_chat(self, diagram_id: str, entries: list[dict[str, Any]]) -> None
#   assigns consecutive `seq` values (last seq + 1, …) to the entries and stores them
def compact_chat(self, diagram_id: str, upto_seq: int, summary: dict[str, Any]) -> bool
#   replaces all entries with seq <= upto_seq by `summary` (stored with seq = upto_seq); atomic from
#   a reader's point of view: load_chat never returns both the summary and entries it replaced.
#   Conditional: returns False and writes nothing when the entry with seq == upto_seq no longer
#   exists (the transcript was cleared or compacted past it while the summary was being produced)
def clear_chat(self, diagram_id: str) -> None
def import_chat(self, diagram_id: str, entries: list[dict[str, Any]]) -> None
#   replaces the transcript with `entries` verbatim, keeping their `seq` (used by arcviz-migrate only)
```

**Derived operations** (concrete in the base; what the app calls):

```python
def create(self, title, diagram_id=None, diagram=None) -> Diagram   # validates id, integrity check,
                                                                     # create_latest (refuses duplicates
                                                                     # atomically) + "Initial state" version
def load(self, diagram_id) -> Diagram                                # validates id, then load_latest
def save_latest(self, diagram) -> None                               # integrity check, then write_latest
def snapshot(self, diagram, label, source="manual", restored_from=None, details=None) -> VersionInfo
                                                                     # builds VersionInfo (trimmed label,
                                                                     # now_iso, counts, fingerprint), append_version,
                                                                     # then mirror.record(details in the commit body)
def delete(self, diagram_id) -> None                                 # purge, then mirror.remove if a Git mirror is attached
def restore(self, diagram_id, version) -> tuple[Diagram, VersionInfo]
                                                                     # load_version, touch updated_at,
                                                                     # save_latest, snapshot(source="restore")
def undo_target(self, diagram_id) -> int | None                      # unchanged algorithm, over list_versions
```

Behaviour pinned by the contract tests and unchanged from today: ids must match
`^[a-z0-9][a-z0-9_-]{0,63}$`; `create` on an existing id raises `StoreError`; labels are stripped and
cut to 120 characters, empty labels become `Version N`; `save_latest` refuses a diagram with
`integrity_errors()`; `restore` and `undo` produce new versions; `undo_target` skips manual
snapshots with an unchanged fingerprint and follows `restored_from`.

`VersionInfo` and `DiagramInfo` keep their current fields. `fingerprint()` moves to `base.py`
unchanged.

**Chat entries** are plain dicts (the agent owns their shape). The store adds and preserves one field:
`seq`, a positive integer, strictly increasing per diagram, never reused after `compact_chat` (the
summary takes `upto_seq`; the next append continues from the highest stored seq). Entries without
`seq` in existing `chat.json` files are assigned `1..n` on first load by the file backend.

### Concurrency

> Superseded in part by [Concurrent mutations](concurrent-mutations.md) (#65): latest now has a
> revision, `commit_latest` is a conditional write atomic with the version, and nothing is
> last-writer-wins any more. The points below describe the original design.

- No store-wide lock in the base class. Atomicity is a backend concern and limited to
  `create_latest`, `append_version`, `append_chat` and `compact_chat`.
- `compact_chat` in a backend without multi-item transactions may be *staged*: write the summary
  first, then delete the replaced entries. Readers must then hide the staged state: `load_chat`
  returns no entry with a smaller `seq` than the newest summary entry. Leftovers are removed by the
  next `compact_chat` (its range includes them) or by `clear_chat`.
- `write_latest` is last-writer-wins.
- `append_chat` must not lose entries under concurrent calls to the same diagram (file and sqlite:
  a lock / transaction; cosmos: one item per entry with a deterministic id and conflict retry).

### Backends

**file** (`FileStore(root)`), layout unchanged:

```
diagrams/<id>/latest.json
diagrams/<id>/versions/NNNN.json   {"meta": VersionInfo, "diagram": Diagram}
diagrams/<id>/chat.json            [entry, …] with "seq" on every entry
```

`append_version` keeps the `O_EXCL` reservation of `NNNN.json`; `create_latest` creates `latest.json`
with `O_EXCL` and then replaces the empty file atomically with the content; `write_latest` and the
chat files use the existing atomic temp-file + rename. A process-local `RLock` serialises chat writes. Single writer
per data directory across processes (as today).

**sqlite** (`SqliteStore(path)`, `path` = `<ARCVIZ_DATA_DIR>/arcviz.sqlite3`), WAL mode, one
connection per thread:

```sql
CREATE TABLE IF NOT EXISTS diagrams (id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL,
                                     latest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions (diagram_id TEXT NOT NULL, version INTEGER NOT NULL,
                                     meta TEXT NOT NULL, snapshot TEXT NOT NULL,
                                     PRIMARY KEY (diagram_id, version)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chat (diagram_id TEXT NOT NULL, seq INTEGER NOT NULL, entry TEXT NOT NULL,
                                 PRIMARY KEY (diagram_id, seq)) WITHOUT ROWID;
```

`append_version`, `append_chat` and `compact_chat` run inside `BEGIN IMMEDIATE … COMMIT`, which
serialises writers across threads and processes on one host. `create_latest` is a plain `INSERT`; the
primary-key violation becomes `StoreError("already exists")`. `delete` removes the three row sets in
one transaction. JSON is stored as text (`json.dumps(sort_keys=True)`).

**cosmos** (`CosmosStore(container)`), Azure Cosmos DB NoSQL API, one container, partition key
`/diagram_id`:

| Item id | `type` | Other fields |
|---|---|---|
| `latest` | `latest` | `diagram_id`, `title`, `updated_at`, `diagram` |
| `version:NNNN` | `version` | `diagram_id`, `version`, `meta`, `snapshot` |
| `chat:NNNNNN` | `chat` | `diagram_id`, `seq`, `entry` |

- `append_version`: query `SELECT VALUE MAX(c.version) … WHERE c.type = "version"` within the
  partition, `create_item` with id `version:NNNN`; on `CosmosResourceExistsError` (409) increment and
  retry. `append_chat` allocates `seq` the same way with `chat:NNNNNN`.
- `compact_chat`: the summary *replaces* the item `chat:<upto_seq>` (`replace`, not `upsert`), so a
  fold whose anchor item is gone fails with 404 and is skipped (`False`). When the replace and all
  deletes fit into one transactional batch (at most 100 operations within the partition) they run as
  one atomic batch. Larger folds are staged: the summary item (`replace_item`) is written first, then
  the folded items are deleted in batches of 100. `load_chat` hides every chat item whose `seq` is below the newest
  summary's, so a failure between steps leaves hidden leftovers, never a visible duplicate or a gap;
  the next `compact_chat` or `clear_chat` removes them.
- `create_latest`: `create_item` of the `latest` item; `409 Conflict` becomes `StoreError("already exists")`.
- `list_diagrams`: cross-partition query on `type = "latest"`, then `list_versions` per diagram for
  the count (N+1 queries; fine at this scale). `list_versions`: partition query ordered by `version`.
  `delete`: delete all items of the partition in transactional batches of 100.
- Consistency: the account's session consistency gives read-your-writes per client. Throttling is
  retried by the SDK (`retry_total=9`); other `CosmosHttpResponseError`s become `StoreError`.
- Auth: `ARCVIZ_STORE_KEY` if set, otherwise `DefaultAzureCredential` (needs the *Cosmos DB Built-in
  Data Contributor* role). With a key the database and container are created if missing (serverless
  account, no throughput setting); data-plane RBAC cannot create them, so with managed identity they
  are created up front and `connect()` only verifies they are reachable.
- The class is written against the subset of `azure.cosmos.ContainerProxy` it uses (`read_item`,
  `create_item`, `upsert_item`, `delete_item`, `query_items`, `execute_item_batch`) so the contract
  tests can run against an in-process fake implementing that subset.

**Later backends** (sketch; not implemented):
MongoDB: collections `diagrams`, `versions`, `chat`, `append_version` = `insert_one` with
`_id = "<diagram_id>:<version>"` and retry on `DuplicateKeyError`. DynamoDB: one table,
`PK = diagram_id`, `SK ∈ {LATEST, V#NNNN, C#NNNNNN}`, `PutItem` with `attribute_not_exists(SK)`.
Firestore: `diagrams/{id}` with subcollections, allocation in a transaction. Git (#14): the file
backend plus a commit per version.

### Configuration

Read only in `arcviz/store/factory.py` (`create_store()`), called by `server.create_app` when no
store is injected.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_STORE` | `file` | `file`, `sqlite`, `cosmos` |
| `ARCVIZ_DATA_DIR` | `data` | root for `file`; directory of `arcviz.sqlite3` for `sqlite` |
| `ARCVIZ_STORE_URL` | | `cosmos`: account endpoint, e.g. `https://<account>.documents.azure.com:443/` |
| `ARCVIZ_STORE_KEY` | | `cosmos`: account key; unset = `DefaultAzureCredential` |
| `ARCVIZ_STORE_DATABASE` | `arcviz` | `cosmos`: database name |
| `ARCVIZ_STORE_CONTAINER` | `diagrams` | `cosmos`: container name |

A missing optional dependency raises `StoreError("… install arcviz-agent[cosmos]")` at startup. An
unknown `ARCVIZ_STORE` raises `StoreError` naming the valid values. `/api/config` gains
`"store": "<backend name>"` (never a URL or key).

**Store specs** for the CLI use the same information in one string: `file:<dir>`,
`sqlite:<dir-or-file>`, `cosmos:<endpoint>` (key, database and container from the environment).

### Migration tooling

Console script `arcviz-migrate --from <spec> --to <spec> [--diagram <id>]... [--overwrite]`. Copies
diagrams, all versions with their original numbers and timestamps (`append_version` into an empty
diagram, in order), the latest state, and the chat via `import_chat` so `seq` values are identical.
Idempotent on the target: existing diagram ids are skipped unless `--overwrite` is given (then deleted
and re-copied). Exit code 0 and a one-line summary per diagram. `--to file:<dir>` is the export/backup
path.

### Compatibility

- `arcviz.store.DiagramStore` stays importable as an alias of `FileStore` for one release.
- Existing `data/` directories keep working. The only on-disk change is that `chat.json` entries
  gain `seq`.
- README configuration table and CLAUDE.md ("No database" paragraph, store section) are updated in
  the implementation PR.

## Testing

- `tests/store_contract.py` defines the contract as functions taking a `repo` fixture; `tests/test_store.py`
  parametrises them over `file`, `sqlite` and `cosmos` (fake container). The suite includes a
  concurrency test: N threads call `snapshot()` on the same diagram and the resulting versions are
  exactly `1..N`.
- Cosmos against a real endpoint when `ARCVIZ_TEST_COSMOS_URL` (+ `ARCVIZ_TEST_COSMOS_KEY`,
  `ARCVIZ_TEST_COSMOS_INSECURE=1` for the emulator's self-signed certificate) is set
  (`tests/test_store_cosmos_live.py`); skipped otherwise. CI runs this against the Linux emulator in
  a non-blocking job (ADR-0004). `azure-cosmos` is part of the `dev` extra so the fake-based tests can
  import its exception types.
- File-layout assertions stay in a file-only test module.
- `tests/conftest.py`'s `store` fixture is parametrised over `file` and `sqlite` so the agent and
  server tests exercise both.

## Decisions

1. First database backend is Cosmos DB NoSQL (ADR-0004).
2. `seq` is stored explicitly in every backend (not derived from position), so it survives compaction.
3. `DiagramStore` stays as an alias for one release.
4. Cosmos unit tests use an in-process fake of the container API; a real endpoint is optional.
5. `import_chat` was added during implementation so the migration can reproduce `seq` values without
   replaying appends; it is the only primitive not used by the application itself.
6. `create_latest` (review finding on #35): `exists()` + `write_latest()` in the base class was not
   atomic across replicas; creation is now a backend primitive.
7. Staged `compact_chat` with reader-side hiding (review finding on #35) instead of claiming an
   atomicity Cosmos cannot provide for folds above 100 items.
8. The Git mirror from #37 (`arcviz/gitmirror.py`) hangs off the base class (`repo.mirror`, attached
   by `create_store()`), so every backend gets it: `snapshot(details=…)` records, `delete` removes.
   The former `delete` primitive is therefore named `purge`, and `delete` is a derived operation.

## Follow-ups

- `docs/deploy-azure.md` (PR #28) gets the Cosmos variant once that PR is merged.

## Open questions

None.
