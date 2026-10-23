# Scale and robustness follow-ups (M22)

Status: implemented in M22 (#166, #168, #174, #175, #181, #182, #183). ADRs: [ADR-0120](../adr/0120-diagram-locator-as-a-re-validated-hint.md),
[ADR-0121](../adr/0121-project-reads-cached-per-request.md), [ADR-0122](../adr/0122-revision-only-read.md),
[ADR-0123](../adr/0123-conditional-deletes.md), [ADR-0124](../adr/0124-stamped-listing-entries-pruned-after-a-grace.md).

## Summary

M20 and M21 made every request authorised in the service layer, but several per-request paths still read more than
they need: finding a diagram's project scans every project (arc42 R21), `GET /api/diagrams` and every project view
list every diagram of the store, a request re-reads the same project document once per service call, and a live
stream reads the whole diagram to learn its revision. On Cosmos DB every one of these is billed, and the scans are
cross-partition queries over all tenants. M22 replaces them with point reads, adds the conditional delete that
`If-Match` on `DELETE` needs, lets stale listing entries go, and fixes two robustness defects (a wedged diagram id
after a crashed create, leaked SQLite connections in the tests).

The rule of M20/M21 stays: **indexes and listings are hints**. Whatever they name is re-read and checked against the
access guard; they never grant access, and a stale or missing hint costs a read, never a leak.

## Goals

- No cross-partition query on a per-request path (diagram requests, project views, `GET /api/diagrams`, live
  polling). Scans remain for the system context (start-up repair, `arcviz-migrate`) and as a bounded fallback.
- A diagram request reads its project once (#166).
- Live polling reads the revision, not the document (#174).
- `If-Match` on `DELETE /api/diagrams/{id}` and `DELETE /api/projects/{id}` (412 on a mismatch, #175).
- Stale per-user listing entries are removed without ever racing a re-share or re-create (#183).
- A crashed diagram create no longer wedges its id in the file store (#168).
- The test suite closes its SQLite connections, and a leak fails the tests (#182).

## Non-goals

- Repartitioning Cosmos DB (R16, ADR-0078).
- Removing the scan from the system-context repairs (`reconcile`, the migration): they run at start-up and from
  the CLI, where a scan is the point.
- `If-Match` on project writes other than delete (renames, sections, shares).
- A UI that sends `If-Match` on deletes: the browser keeps deleting unconditionally (the API accepts the header).

## Design

### Diagram locator (#181, ADR-0120)

A keyed document of the new kind **`location`** per diagram (key: the diagram id; file `locations/<id>.json`, SQLite
table `documents`, Cosmos DB item `location` in partition `location:<id>`), body
`{"diagram": id, "projects": [pid, …], "pending": {pid: [token, …]}, "scanned_at"?: time}`. Module
`arcviz/services/locations.py` (no store imports; ports only).

**Invariant: the entry lists every project that holds a membership of the id** (a superset; usually exactly one).
A writer lists a project *before* it adds the membership, with a `pending` token of its own (`add_location`; the
entry's revision always changes), and clears its token once the membership is written, or once a re-read shows that
its write did not happen (`settle_location`). `remove_location` drops a project only when no writer has it pending and
it does not hold the id (read again); a remover that read the entry before a listing fails its conditional write. No
clock is involved, so skew between replicas cannot break the invariant (a move P→Q racing Q→P keeps both listed). A
crash leaves an extra name or a pending token, never a missing name; an extra name costs one point read. Only a
writer clears its own token: the repair adds names but never removes a pending one, and never deletes an entry that
carries pending tokens (a snapshot cannot tell a crashed writer from one in flight).

**A failed read is never "absent".** Only the store's definite "not found" makes a project count as missing
(`ports.is_missing`/`is_definitely_missing`); any other failure (a throttle, a timeout) is raised, and the decision
that needed the read is not taken: a create or a move is given up before it writes, a membership is never assumed
removed.

| Function | Behaviour |
|---|---|
| `read_location(documents, diagram)` | the `Entry(projects, pending, scanned_at, revision)` or `None`; any other read failure is raised |
| `add_location(documents, diagram, project)` | list `project` with a fresh pending token (returned; conditional read-modify-write, retried; always writes); clears `scanned_at` |
| `settle_location(documents, diagram, project, token)` | clear that token (best effort) |
| `remove_location(documents, projects, diagram, project, strict=False)` | drop `project` when it is not pending and no longer holds the id; the entry goes with its last project; best effort unless `strict` |
| `read_holder(projects, project, diagram)` | the `Located` if the project holds the diagram; `None` only when it does not or is definitely gone; other failures raised |
| `locate_membership(documents, projects, diagram, exists)` | the project holding the diagram and its membership (see below), or `None` |
| `holders(projects, diagram)`, `membership(projects, diagram)`, `newest(found)` | the scan: every holder, and the one that counts (newest `added_at`, ties: lowest project id, the rule of `reconcile`) |
| `repair_locations(documents, projects, exists)` | system context: add every holder to its entry (see *Repair*) |

**Read** (`locate_membership`, used by `AccessPolicy.locate`, `ProjectService.holder`/`locate`):

1. Read the entry and every project it lists (point reads; usually one). The holders among them decide: the newest
   membership wins, so after a crashed move the members of the stale source do not keep access.
2. If none holds it and a scan less than `SCAN_TTL` (10 minutes) ago found it in no project (`scanned_at`), the
   answer is `None` without another scan: an orphan cannot be polled into scans. The listed projects are always read
   first, so this never hides a membership in a listed project; placing the diagram (`add_location`) clears it.
3. If the diagram does not exist: `None`, without a scan (probing unknown ids costs point reads only).
4. Otherwise the projects are scanned, single flight per id and process (concurrent callers wait for the scan in
   flight and share its result), and the holder ids it found are reused by that process for `SCAN_COOLDOWN` (5 s),
   also when the heal write failed; the projects are always read again, so roles are current. Holders found heal the
   entry (one conditional write on the revision read before the scan); no holder records `scanned_at` the same way. A
   failed write records nothing, so a heal that failed never leaves a stamp, and nobody is ever answered "not found"
   for a placed diagram because of a stamp. (`SCAN_TTL` compares wall clocks of replicas; it only bounds cost, never
   the invariant.)

The project found is the truth: the membership in the project document decides, the role check of the guard
follows. The entry never grants access.

**Writes** keep the invariant:

- `ProjectService.place` (create, import, move, adopt): first read every other holder (the source and every project
  the entry lists; a failed read aborts before anything is written) and check that the diagram exists (a definite
  answer; never a membership for a diagram that is gone), then `add_location(target)` (pending), add the
  membership to the target, check again that the diagram exists (if it was deleted meanwhile, its delete may not have
  seen this membership: the move removes its own membership, settles and fails "not found"), `settle_location`, then
  remove the membership from each other holder, conditional on the `added_at` seen, and `remove_location` for each.
  A failed adoption in `reconcile` settles its tokens the same way, once a re-read shows no membership. `reconcile` lists an orphan the same way before adopting it.
- Create (`DiagramService._create_free`): once the id is claimed (no diagram has it, its placement record is
  written), `ProjectService.vacate(id)` removes the id from every project its entry lists *before* the diagram is
  created. Such a membership can only belong to a deleted diagram whose cleanup failed (a crash, also a duplicate
  left by a crashed move); without this, the new diagram, possibly another user's, would show in that project. The
  entry lists every holder, so no scan is needed. It fails closed: a project that cannot be read, or whose membership
  cannot be removed, raises; the id then counts as taken (logged once) and the create takes the next free id
  (`<id>-2`, …), releasing the placement record. The placement record keeps concurrent creates of the id away, so a
  removal (a conditional write) is final. A create given up after placing withdraws its membership
  (`ProjectService.withdraw`). A listed project with a pending token (a placement in flight, or a crashed one) also makes
  it fail closed.
- `DiagramService.delete`: before the store delete, `ProjectService.memberships(id, holder)` collects every membership
  of the id (the project the guard's owner check just read, reused, plus the other projects the entry lists; a scan
  only in the system context without an entry; a failed read gives the delete up before anything is deleted); after it, `unplace(id, observed)` removes exactly those memberships (each only if it still has the
  `added_at` seen, so a membership of a diagram created again under the id meanwhile is never touched) and drops
  them from the entry.
- Renames in the migration and `reconcile`'s moves do not write entries; the readers heal them and `reconcile`
  repairs them.

**Repair** (`repair_locations`, called at the end of `ProjectService.reconcile`, so at every start and from
`arcviz-migrate --projects`/`--workspaces`): every project holding a diagram (after the membership repair) is added to
its entry, conditionally on the revision listed; extra names are kept (a writer between the scan and the write may
have listed a project whose membership is about to be added). Entries of diagrams that no longer exist and that no
project holds are deleted conditionally. Idempotent; a conflict means a writer is maintaining the entry. The count is
`ReconcileReport.located` (printed by the CLI). An upgraded installation is backfilled at its first start; until
then the readers heal what they touch (bounded as above). Data written by an older replica during a rolling upgrade
is not covered by the invariant until the next start.

**`GET /api/diagrams`** for a caller: the diagrams of the projects in the caller's listing (`visible_diagrams`) and
then `DiagramRepositoryPort.diagram_infos(ids)` for exactly those ids. **`ProjectService.view`** asks
`diagram_infos` for its members only. Neither lists the store's diagrams any more.

New store primitive **`diagram_infos(ids) -> list[DiagramInfo]`**: the `DiagramInfo` of every id that exists,
sorted by id (unknown ids are left out). File: `latest.json` per id plus the version files; SQLite: one query;
Cosmos DB: per id two single-partition queries (title and `updated_at` of `latest`, `COUNT` of the versions).

### One project read per request (#166, ADR-0121)

`Guard` gets an optional **project cache** (`cache_projects`). With it, a viewer check (`Guard.project`,
`Guard.diagram` with `need="viewer"` and not `fresh`) reuses the project document the guard read earlier in the
same guard; every check for more than viewer, and every `fresh` check, reads it again and refreshes the cache;
every project write of the guard's `ProjectService` (`_update`, `delete`) drops the cached copy, and a placement's
editor check (`check_target`) reads the project again, so a response
built after a write shows the write. `ServiceRoot.for_actor(actor, per_request=True)` turns it on; `services_of`
(every ordinary route) uses it. The long-lived routes, the two chat streams and the two live streams, get
`turn_services_of` (`TurnSvc`) without the cache, so a chat turn re-reads the project at every step (ADR-0090) and
a live stream never answers from an old project. Tests and the CLI build guards without the cache.

A diagram detail request thus reads: the entry, the project, the workspace (cached already), then the diagram.

### Revision-only read (#174, ADR-0122)

New primitive **`read_revision(diagram_id) -> Revision`**: the latest revision exactly as `read_latest` would return
it (`LEGACY_REVISION` for a state stored without one), `StoreError` for an unknown diagram. File: `stat()` of
`latest.json`; the revision is cached per (inode, mtime, size), which every write changes (`os.replace` of a new
file), so it parses the JSON only after a write (a file written less than 2 s ago is never cached: a rewrite in the
same timestamp tick could reuse inode and size; the cache holds at most 4096 diagrams, least recently used go, and a
purge drops its entry); SQLite: `SELECT revision`; Cosmos DB: one single-partition
projection query (`SELECT VALUE c.revision FROM c WHERE c.id = @id`), which never transfers the diagram.
`LiveService` re-checks use it (through `LiveHub.shared` as before).

### Conditional deletes and `If-Match` on DELETE (#175, ADR-0123)

- Store: `purge(diagram_id, expected=None)` and `delete(diagram_id, expected=None)`: with `expected` the delete
  happens only if the latest revision is `expected`, atomically (file: under the diagram lock; SQLite:
  `DELETE … WHERE revision = ?` in the write transaction; Cosmos DB: the `latest` item is deleted with `If-Match`
  on the ETag of the revision read), else `ConflictError` and nothing is deleted. Unknown diagram: `StoreError` as
  before, also when another request deleted it in between (Cosmos DB reads again after a failed conditional delete). Projects already had `delete_project(id, expected)`.
- Services: `DiagramService.delete(id, expected=None)` and `ProjectService.delete(id, expected=None)` take an
  `Expected` (any of several revisions, RFC 9110). With it there is exactly one attempt; another revision, or a
  write in between, raises `RevisionMismatch` (HTTP 412 with the current revision when known).
- HTTP: `DELETE /api/diagrams/{id}` and `DELETE /api/projects/{id}` honour `If-Match` (`IfMatch` dependency). So that
  a client can send it, `GET /api/projects/{id}` (and every project view response) now carries `ETag: "<revision>"`
  and `revision` (the project revision; diagrams had theirs since M10). Access is checked before the revision: an
  outsider gets 404, a non-owner 403, never 412.

### Pruning stale listing entries (#183, ADR-0124)

Listing entries get a **stamp**: `added` maps `"<field>:<project id>"` to the time the entry was (last) added.
`update_listing(add=…)` always writes the stamps of what it adds (also when the id is listed already), so an add that
precedes a visibility write (share, migration move) always changes the listing revision. An entry is **stale**
when its project does not exist (the store said "not found", not any other failure) or the project is
**foreign**: it is not in the user's personal workspace (`personal_workspace_id(user)`) and has no share for the
user. Both fields' entries of a stale project are removed by `prune_listing(documents, user, stale, revision)`:

- conditional on the listing revision read before the projects were read (a share or create that added
  meanwhile makes the write fail: nothing is removed);
- only entries whose stamp is older than `LISTING_GRACE` (one hour): an add that happened before the read may belong
  to a share or create whose project write is still in flight. A stale entry without a stamp (written before M22, or
  by an older replica during a rolling upgrade) is stamped by the same write instead, so the grace runs from then;
- best effort: a failure or conflict is ignored (the entry is retried next time).

`Guard.listed()` prunes on read (the caller's own listing, a hint document: no membership or access is written,
ADR-0088 is about memberships). `_repair_listings` in the start-up migration prunes every listing the same way (system
context compaction) and reports `PersonalMigration.pruned`.

### Reclaiming a crashed create (#168)

`FileStore.create_latest` claims `latest.json` with `_claim` (the M20 helper): an empty claim file left by a writer
that died is reclaimed under the per-diagram lock, but only when it can be what a dead create leaves: at most
version 1 and no transcript. An empty `latest.json` with more versions or a transcript is a diagram whose latest
state was lost (a file-system crash): the create is refused with a `StoreError` that tells the operator to restore
`latest.json` from the newest version file, and nothing is touched. `purge` writes a tombstone (`.purging`, content
`started`) first, removes `latest.json` (on failure the tombstone goes again), records `latest removed` in the
tombstone, removes the rest and the tombstone last: a directory without `latest.json` whose tombstone records `latest
removed` holds debris, which a create moves aside before taking the id. Without a tombstone, a directory with no usable `latest.json` is reclaimed
only under the dead-create rule above; otherwise the create is refused and removes its own claim again. A reclaimed create's leftovers (its version 1) are
moved to `reclaimed/<id>-<token>/` under the data directory, never deleted, and never inherited.

`exists()` treats an empty `latest.json` as absent, like every reader; content that is not UTF-8 counts as present;
any other OS error (permissions, I/O) is a `StoreError` with the cause, never "absent".

Every `_atomic_write` now `fsync`s the temporary file before the rename and the directory after it (POSIX; skipped
where directories cannot be opened), so after a power loss a file is the old or the new version, not empty. This
costs two syncs per write on the single-instance file store (arc42 R13).

### SQLite connections (#182)

`SqliteStore` keeps every per-thread connection it opens (`check_same_thread=False`, still one connection per
thread for queries) and closes all of them in `close()` and in a `weakref.finalize` when the store is collected. A
thread's connection is also closed when the thread ends (a finalizer on its thread-local holder), so worker threads of
a long-running server do not leak file descriptors. `DiagramRepository.close()` is a no-op elsewhere. The test
fixtures close their stores, and `tests/conftest.py` turns `ResourceWarning` (and the unraisable-exception warning it
surfaces as) into errors, so a leak fails the run (configured in the conftest, not in `pyproject.toml`).

## Security

- The locator and the listings are hints: `Guard.diagram` re-reads the project the locator names and checks the
  membership and the role; `Guard.listed` re-reads every listed project and checks the role. A caller can never
  write an entry that names a project they cannot see in a way that helps them: entries are written by the
  services after authorised writes, healed only from a scan result that is checked by the guard afterwards.
- A missing or stale entry falls back to the pre-M22 scan only for diagrams that exist; an orphan is scanned at most
  once per `SCAN_TTL`, so polling its id cannot amplify scans; outsiders probing unknown ids get 404 after point reads
  only.
- Failed reads are never taken as "not a holder" or "removed": the create, move or delete that needed them is given
  up before it writes.
- Duplicate memberships (a crash) resolve to the newest, so a stale source project grants nothing after a move; a
  create of an id clears every membership the entry lists first, so a stale membership never shows a new diagram (of
  any user) in a project that is not its own.
- The project cache is per guard; a guard serves one caller and one request. Higher-than-viewer checks always
  re-read, chat turns and live streams never cache.
- 404 before 412: a conditional delete checks access first.

## Testing

- Store contract (file, SQLite, Cosmos fake): `read_revision`, `diagram_infos`, conditional `purge`/`delete`.
- `tests/test_locations.py`: the read algorithm (valid entry, stale entry heals, missing entry heals, unknown id does
  not scan), writes by create/move/delete/import, repair (backfill, stale, dangling), on all three backends; a Cosmos
  test asserts that diagram requests, project views and `GET /api/diagrams` issue no cross-partition query.
- `tests/test_access.py`: outsiders still get 404 with a valid, stale or forged entry.
- Guard cache: one project read per detail request; a write in the request is visible; a chat turn re-reads.
- Live: the poll uses `read_revision` (no document read).
- Server: `If-Match` on both deletes (204, 412 with the current revision, 404 for outsiders, 403 for non-owners).
- Listing pruning: stale entries go, fresh ones stay, a concurrent add wins, transient read failures prune nothing.
- File store: a crashed create (empty `latest.json` plus leftover versions) is reclaimed.
- `ResourceWarning` as error in the suite.

## Migration / compatibility

- New document kind `location`; written by M22, backfilled at start-up (best effort) and by `arcviz-migrate
  --projects`. Older versions ignore it. During a rolling upgrade with several replicas, diagrams created or moved by
  an old replica have no or a stale entry; the new replicas heal them on first access (one scan each).
- Listing documents gain `added`; older readers ignore it (and drop it when they write), old entries without a stamp
  are stamped before they can be pruned.
- `GET /api/projects/{id}` and project views gain `revision` and an `ETag` header (additive).
- No change to the diagram or project documents.

## Open questions

None.

## Decisions

- Heal-on-read writes the locator entry (ADR-0120): it is a hint document, not a membership, so ADR-0088 ("a
  caller's read repairs nothing") is not touched.
- The project cache is per request and off for chat turns and live streams (ADR-0121).
- Cosmos DB revision reads use a projection query (ADR-0122).
- Listing pruning runs on read and at start-up, guarded by stamps and the listing revision (ADR-0124).
- The locator lists every holder of an id (a superset) instead of one project, so deletes and creates clean every
  membership without a scan, and fallback scans of orphans are bounded per id (review of PR #184).
- Failed reads are never "absent": vacate, placements and deletes fail closed (third review of PR #184); a vacate
  that fails makes the create take the next free id, and pending tokens instead of clocks protect a project about to
  receive its membership; the membership repair is best effort per item and reports what it skipped (fourth review).
