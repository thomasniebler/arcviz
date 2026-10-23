# Projects: sections, diagram membership, API and migration

- Status: Implemented (milestone M16)
- Issues: #131 (model and persistence), #132 (API and migration)
- ADRs: [0051 Projects as their own documents, diagrams unchanged](../adr/0051-projects-as-own-documents-diagrams-unchanged.md),
  [0052 Membership across documents: ordered writes and a repair step](../adr/0052-membership-ordered-writes-and-repair.md),
  [0053 Existing diagrams move into a default project, lazily and by CLI](../adr/0053-default-project-lazily-and-by-cli.md),
  [0054 Removing a section keeps its contents; only empty projects can be deleted](../adr/0054-section-removal-keeps-contents.md)
- Builds on: [Application services](application-services.md) (#130)

## Summary

Every diagram stands alone today: the UI offers one flat drop-down of all diagrams. Real architecture
work produces several diagrams per system (a deployment view, the order process as an activity
diagram, a checkout sequence), so M16 groups them into **projects**. A project has a title, a
description, a folder-like **tree of sections** and an **ordered membership** of diagrams; every
diagram belongs to exactly one project and optionally one section. Diagrams stay independent
documents (maintainer decision, option A): a project does not share elements between its diagrams.

Projects are stored as their own documents next to the diagrams, through a second repository port,
on all three backends. Diagram documents, their versions and their ids do not change, so the existing
`/api/diagrams/{id}` routes keep working and no diagram data is migrated. New `/api/projects…` routes
manage projects, sections and membership; on the first project read every existing diagram lands in
one default project, and `arcviz-migrate --projects` does the same explicitly.

## Goals

- A `Project` domain type (`arcviz/projects.py`) with its own operations through one mutation path,
  validated against the operation signatures and rolled back on any exception (as ADR-0045 does for
  diagrams).
- A `ProjectRepositoryPort` in `arcviz/ports.py`, implemented by the `file`, `sqlite` and `cosmos`
  backends with conditional writes on a project revision (#65), covered by `tests/store_contract.py`
  (including `tests/cosmos_fake.py`).
- A `ProjectService` owning every project read-modify-write, the placement of diagrams, and the
  repair of membership across documents.
- HTTP routes for projects, sections, membership moves and the diagrams of a project; creating or
  importing a diagram can name its project and section. Authentication and limits (M15) apply.
- Migration of existing data into a default project without touching diagram documents.
- A shape that a shared project model (M18, #137) can extend without migrating diagram data again.

## Non-goals

- Shared elements across diagrams, cross-diagram references or consistency rules (M18; done in
  [Shared project model](shared-project-model.md), which adds the model to the project document as planned below).
- A project chat or project review (M17, #135, #136): they will read the project structure through
  `ProjectService`, which this milestone provides.
- Version history of projects, Git mirroring of projects.
- Repartitioning Cosmos DB by project (an M18 question; see *Cosmos DB* below).
- Per-project permissions; the app has one user group (ADR-0047).

## Design

### Domain model (`arcviz/projects.py`)

```python
class Section(BaseModel):
    id: str  # slug, unique within the project
    title: str
    parent: str | None = None  # None = top level


class Membership(BaseModel):
    diagram: str  # a diagram id (globally unique, as today)
    section: str | None = None  # None = directly in the project
    added_at: str  # when the diagram was placed here (repair tie-breaker)


class Project(BaseModel):
    id: str  # same id rule as diagrams (check_id)
    title: str
    description: str = ""
    sections: list[Section] = []
    diagrams: list[Membership] = []
    created_at: str
    updated_at: str
```

Order is list order: the sections with the same `parent` are siblings in the order of `sections`, the
members with the same `section` in the order of `diagrams`. `Project.integrity_errors()` reports
duplicate section ids, a missing parent, a cycle in the parent chain, a duplicate diagram member and a
member in an unknown section. Queries: `find_section(ref)` (id or case-insensitive title, like
`Diagram.find_node`), `member(diagram_id)`, `children(section_id)`, `members_of(section_id)`,
`descendants(section_id)`, and `tree()`, the nested form
`{"diagrams": [ids], "sections": [{"id", "title", "diagrams": [...], "sections": [...]}]}`.

### Project operations

All mutations go through `projects.apply(project, op, args)`: unknown operation or arguments of the
wrong JSON type are rejected before anything changes (the signature check of `operations.py`, shared
via `operations.validate_arguments`), the project is deep-copied, and every field is restored on any
exception. `ProjectError` is a subclass of `OperationError`, so routes answer it with 400 like a bad
diagram operation. A successful operation sets `updated_at`.

| Operation | Arguments | Effect |
|---|---|---|
| `rename_project` | `title` | non-empty, stripped |
| `set_description` | `description` | stripped, may be empty |
| `add_section` | `title`, `parent=None`, `id=None`, `position=None` | new section (id from the title, made unique) under `parent` at `position` among its siblings (end when `None`) |
| `rename_section` | `section`, `title` | |
| `move_section` | `section`, `parent=None`, `position=None` | new parent and position; never into itself or a descendant |
| `remove_section` | `section` | its subsections and diagrams move to its parent, in its place (ADR-0054) |
| `move_diagram` | `diagram`, `section=None`, `position=None` | within the project: new section and position |
| `add_diagram` | `diagram`, `section=None`, `position=None` | *internal*: membership of a diagram not yet in this project |
| `remove_diagram` | `diagram` | *internal*: drops the membership |

`position` is a zero-based index among the target siblings without the moved item; larger values
append, negative values are a `ProjectError`. Section references accept an id or a title; `parent` /
`section` `None` (or `""`) mean the top level. Error messages list the known section ids, like the
diagram operations do for node ids.

`PUBLIC_OPERATIONS` are all but the two internal ones: `add_diagram` and `remove_diagram` change
which project a diagram belongs to, which is a cross-document invariant only `ProjectService`
maintains (see below), so `POST /api/projects/{id}/ops` refuses them.

### Persistence port

```python
class ProjectRepositoryPort(Protocol):
    def list_projects(self) -> list[tuple[Project, Revision]]: ...  # sorted by id
    def read_project(self, project_id: str) -> tuple[Project, Revision]: ...  # StoreError if unknown
    def create_project(self, project: Project) -> Revision: ...  # StoreError("... already exists.")
    def commit_project(self, project: Project, expected: Revision) -> Revision: ...  # ConflictError
    def delete_project(self, project_id: str, expected: Revision) -> None: ...  # ConflictError
```

`commit_project` writes only if the stored revision is `expected` and the project exists, with a new
revision; `delete_project` likewise. They are abstract primitives of `DiagramRepository` (one store
object implements both ports), implemented per backend:

| Backend | Layout | Conditional write |
|---|---|---|
| `file` | `projects/<id>.json` with the project and `"revision"` | create with `O_EXCL`; commit/delete under a per-project lock of the process (same registry as the diagram locks, key `project:<id>`) |
| `sqlite` | table `projects(id PRIMARY KEY, revision, body)` | `INSERT` (integrity error = exists); `UPDATE … WHERE id = ? AND revision = ?` / `DELETE` in `BEGIN IMMEDIATE` |
| `cosmos` | one item per project in the same container: `id = "project"`, `type = "project"`, partition key value `diagram_id = "project:<id>"` | create (409 = exists); replace/delete in a transactional batch with `If-Match` on the item's ETag, replayed safely after transient errors |

The `project:` prefix cannot collide with a diagram id (`check_id` forbids `:`), and the
`latest`-typed queries that list diagrams do not see project items.

### `ProjectService` (`arcviz/services/projects.py`)

`ProjectService(projects: ProjectRepositoryPort, diagrams: DiagramRepositoryPort)`. Every write is a
read-modify-write conditional on the revision it read, retried up to `MUTATION_ATTEMPTS` times
(`ConflictError` after that, HTTP 409).

| Method | Semantics |
|---|---|
| `list_projects()` | runs `reconcile()`, then `ProjectSummary(id, title, description, diagram_count, updated_at)` sorted by title (case-insensitive), then id |
| `get(id)` / `view(id)` | the project; `view` adds the members' `DiagramInfo` with their section in membership order (members whose diagram no longer exists are left out of the answer) |
| `create(title, id=None, description="")` | id from the title when not given; `StoreError` when taken |
| `apply(id, op, args, internal=False)` | one project operation; only `PUBLIC_OPERATIONS` unless `internal` |
| `delete(id)` | refuses a project that still has members with `ProjectNotEmpty` (409, ADR-0054) |
| `check_target(project_id, section)` | the project exists (`StoreError`, 404) and the section resolves (`ProjectError`, 400); `None` means the default project, which is created if missing |
| `place(diagram_id, project_id=None, section=None, position=None)` | add the membership to the target project (or move it there when it is already a member), **then** remove it from every other project |
| `unplace(diagram_id)` | remove the membership from every project |
| `locate(diagram_id)` | `(project_id, section)` or `None` |
| `reconcile()` | the repair step below; returns what it changed |
| `ensure_default()` | the default project (`id = "default"`, title `Standardprojekt`), created when missing |

**Membership invariant.** "Every diagram belongs to exactly one project" spans the diagram document
and one or two project documents, which no backend can write atomically (Cosmos DB batches are per
partition). The service orders its writes so that a crash leaves only states the repair step can
resolve (ADR-0052):

- *Create* a diagram: validate the target (`check_target`), create the diagram, then `place` it; a
  placement that fails is answered by deleting the diagram again. A crash in between leaves an
  **orphan** (a diagram in no project).
- *Move* across projects: add to the target with a fresh `added_at`, then remove from the source. A
  crash in between leaves a **duplicate** whose newer membership is the intended one.
- *Delete* a diagram: delete the diagram, then `unplace` it. A crash in between leaves a **dangling**
  membership.

`reconcile()` resolves all three: duplicates keep the membership with the newest `added_at` (ties:
the lowest project id) and drop the others, each removal conditional on the `added_at` it observed; dangling memberships are dropped after `exists()`
confirms the diagram is gone (so a diagram created between the listing and the check is not lost);
orphans, confirmed again against a fresh read of all projects (so a diagram being moved is not
taken), are appended to the default project. Every repair is an ordinary conditional project write
and idempotent, so concurrent repairs (several Cosmos DB replicas) converge.

`DiagramService` gets the project service as an optional collaborator,
`DiagramService(repository, projects=None)`; with it, `create`/`import_new` accept `project` and
`section` (checked before the diagram is created, placed after), and `delete` unplaces the diagram.

### HTTP API

All routes are under `/api`, so the authentication and limits middleware (M15) apply unchanged;
writes are rate-limited in the `ops` class like every other non-chat write.

| Route | Body | Answer |
|---|---|---|
| `GET /api/projects` | | `[ProjectSummary]` (runs the repair) |
| `POST /api/projects` | `{title, id?, description?}` | 201, project view; 409 id taken, 400 invalid id |
| `GET /api/projects/{pid}` | | project view; 404 |
| `PATCH /api/projects/{pid}` | `{title?, description?}` | project view |
| `DELETE /api/projects/{pid}` | | 204; 409 while it has diagrams |
| `POST /api/projects/{pid}/ops` | `{op, args}` | `{result, ...view}`; 400 for internal or invalid operations |
| `POST /api/projects/{pid}/sections` | `{title, parent?, position?, id?}` | 201, `{section, ...view}` |
| `PATCH /api/projects/{pid}/sections/{sid}` | `{title?, parent?, position?}` | view; `parent`/`position` present means move (`parent: null` = top level) |
| `DELETE /api/projects/{pid}/sections/{sid}` | | view; contents move to the parent |
| `GET /api/projects/{pid}/diagrams` | | the members' `DiagramInfo` plus `section`, in order |
| `POST /api/projects/{pid}/diagrams/{did}/move` | `{project?, section?, position?}` | view of the target project; 404 when `did` is not in `pid` |

A **project view** is `{"project": Project, "tree": tree(), "diagrams": [DiagramInfo + section]}`.

`POST /api/diagrams` and `POST /api/diagrams/import` accept optional `project` and `section`: an
unknown project is a 404, an unknown section a 400, both before anything is created. Without them the
diagram goes into the default project. All existing diagram routes, their bodies and answers are
unchanged; `DELETE /api/diagrams/{id}` also removes the membership.

### Migration

No diagram document changes. Projects appear in two ways (ADR-0053):

- **Lazily**: the first `GET /api/projects` (which the UI calls on start) runs `reconcile()`, which
  creates the default project and adopts every diagram that is in no project, in diagram-id order.
- **Explicitly**: `arcviz-migrate --projects <store-spec>` runs the same repair on a store and prints
  what it adopted, dropped and de-duplicated. Copying between backends
  (`arcviz-migrate --from … --to …`) copies the project documents as well (skipped when present on
  the target unless `--overwrite`), then runs the repair on the target.

The CLI moves from `arcviz/store/migrate.py` (which keeps the storage-level `migrate()` copy
function) to `arcviz/cli.py`, a second composition root next to `server.py`, because the repair is
business logic of `ProjectService`; the console script name `arcviz-migrate` stays.

### Shared project model later (M18)

M18 followed this path: see [Shared project model](shared-project-model.md) (ADR-0070, no repartitioning:
ADR-0078).

The design keeps the M18 options open without another diagram migration:

- Diagram ids stay globally unique and diagram documents carry no project field, so turning diagrams
  into views of a shared model can add data to the **project** document (a model section next to
  `sections`) and reference diagrams by the ids they already have.
- Projects are versionless documents with a revision; adding fields is backward compatible (pydantic
  defaults).
- Repartitioning Cosmos DB by project (`/project_id`) would move items, not change them; it is
  recorded as an M18 question in the arc42 risks.

### Concurrency and errors

- Project writes are conditional on the project revision, retried by the service, and the repair is
  idempotent. The `file` and `sqlite` backends keep their single-writer rule.
- `StoreError` "not found" → 404, `ConflictError` → 409 (existing handler), `ProjectError` → 400,
  `ProjectNotEmpty` → 409.
- A placement that names a project which disappears between the check and the write leaves an orphan
  that the next repair adopts into the default project; the diagram is never lost.

## Testing

- `tests/test_projects.py`: every operation, its errors and rollback, `integrity_errors`, `tree()`,
  argument validation.
- `tests/test_properties.py` with `tests/strategies.py` (`project_operations`): random sequences of
  project operations keep `integrity_errors()` empty, a failing operation leaves the project unchanged,
  every member appears exactly once in `tree()`.
- `tests/store_contract.py`: create/read/list/commit/delete projects, conditional writes and
  conflicts, deleted projects, isolation from diagram listings; all backends including the Cosmos DB
  fake.
- `tests/test_project_service.py`: placement, moves across projects, unplace, delete refusal, the
  three repair cases with the race guards, default project creation, concurrent repair.
- `tests/test_server_projects.py`: every route, auth and rate limits on the new routes, creation and
  import into a project and section, 404/400 before creation, delete removing the membership.
- `tests/test_migrate.py`: `--projects`, project copy between backends.

## Migration / compatibility

Additive. Stored diagrams, their versions and transcripts are untouched; `GET /api/diagrams` still
lists every diagram. A store written by this version and read by an older one ignores the project
documents (`file`: an extra directory; `sqlite`: an extra table; `cosmos`: items of an unknown type in
their own partitions).

## Open questions

None open; decisions taken without the maintainer are *Proposed* ADRs (0051–0054) and listed in the
milestone PR under "Decisions to review".

## Decisions

- Projects are their own documents; membership lives in the project, diagrams are unchanged (ADR-0051,
  Proposed; option A and global diagram ids were the maintainer's decision).
- Ordered writes plus an idempotent repair step for the cross-document invariant (ADR-0052, Proposed).
- Existing diagrams go into one default project, lazily on first read and by CLI (ADR-0053, Proposed:
  the maintainer's stated default).
- Removing a section moves its contents up; a project must be empty to be deleted (ADR-0054, Proposed).
- Cosmos DB keeps the `/diagram_id` partition key; projects are their own items (maintainer decision).
