# Projects

Source: `arcviz/projects.py` (domain model and operations), `arcviz/services/projects.py`
(`ProjectService`), the project primitives of every backend in `arcviz/store/`. Tests:
`tests/test_projects.py`, `tests/test_project_service.py`, the project part of
`tests/store_contract.py`, the project property test in `tests/test_properties.py`. Design and
decisions: [Projects](../architecture/specs/projects.md), ADRs 0051 to 0054.

A project groups diagrams: a title, a description, a tree of sections (folders), the ordered membership
of diagrams and the shared model (project-wide elements the diagrams' nodes and lifelines may stand for,
see [Shared model](#shared-model)). Every diagram belongs to exactly one project and optionally one
section. Diagram documents do not know their project; the project document holds the membership.

## Model

```python
class Section(BaseModel):
    id: str
    title: str
    parent: str | None = None  # None = top level
    element: str | None = None  # the model element it stands for; omitted from dumps while unset


class Membership(BaseModel):
    diagram: str
    section: str | None = None
    added_at: str


class Project(BaseModel):
    id: str
    title: str
    description: str = ""
    sections: list[Section] = []
    diagrams: list[Membership] = []
    elements: list[Element] = []  # the shared model
    created_at: str
    updated_at: str
    workspace: str | None = None  # the owner's personal workspace (M21); omitted from dumps while unset (v0.1.8 data)
    roles: dict[str, str] = {}  # the shares: user id -> viewer | editor; omitted while empty
    incarnation: str | None = None  # random token set by new_project (M23, ADR-0127); omitted while unset
```

`workspace` and `roles` (spec [personal workspaces](../architecture/specs/personal-workspaces.md)) are not part of
project bundles: an import creates the project in the importer's personal workspace, unshared.

- Siblings (sections with the same `parent`, members with the same `section`) are ordered by their
  position in the lists.
- `find_section(ref)`: by id, then case-insensitive title or slugified title; `None` otherwise.
- `member(diagram_id)`, `children(section_id)`, `members_of(section_id)`, `descendants(section_id)`
  (terminates on cyclic data).
- `tree()`: `{"diagrams": [ids], "sections": [{"id", "title", "diagrams", "sections"}]}`.
- `find_element(ref)` (id, case-insensitive name, slug), `element_children(element_id)`.
- `integrity_errors()`: duplicate section ids, unknown parents, parent cycles, duplicate members,
  members in unknown sections; for the model: duplicate or invalid element ids, empty names, unknown kinds,
  unknown parents, parent cycles, sections standing for unknown elements.
- `new_project(title, project_id, description="", workspace=None)`: stripped title (empty → `ProjectError`), a fresh
  random `incarnation`. `incarnation_of(project)` is that token, or `created_at` for a project written before M23: a
  project deleted and created again under the same id is another incarnation (ADR-0127).

## Operations

`apply(project, op, args)` looks the operation up in `OPERATIONS` (unknown → `ProjectError` listing
the available ones), validates `args` against the function's signature with
`operations.validate_arguments` (the check behind `operations.check_arguments`, raising
`ProjectError`), deep-copies the project, runs the operation, asserts `integrity_errors()` is empty,
sets `updated_at`, and restores every field on any exception. It returns
`{"ok": True, "op": op, "result": …}` (a `Section` is dumped). `ProjectError` subclasses
`OperationError`.

| Operation | Arguments | Result |
|---|---|---|
| `rename_project` | `title: str` | `{"title"}`; empty → error |
| `set_description` | `description: str` | `{"description"}` |
| `add_section` | `title: str`, `parent: str \| None`, `id: str \| None`, `position: int \| None` | the `Section`; id = slug of the title made unique with `-2`, `-3`, …; an explicit `id` must be a slug and unused |
| `rename_section` | `section: str`, `title: str` | `{"section", "title"}` |
| `move_section` | `section: str`, `parent: str \| None`, `position: int \| None` | `{"section", "parent"}`; into itself or a descendant → error |
| `remove_section` | `section: str` | `{"section", "moved_sections", "moved_diagrams"}`; subsections take its place among its siblings, its diagrams move to its parent |
| `move_diagram` | `diagram: str`, `section: str \| None`, `position: int \| None` | `{"diagram", "section"}`; not a member → error |
| `add_diagram` | `diagram: str`, `section: str \| None`, `position: int \| None` | `{"diagram", "section"}`; already a member → error; `added_at` = now with microseconds (`placement_time()`) |
| `remove_diagram` | `diagram: str` | `{"diagram"}`; not a member → error |

Section references (`section`, `parent`) resolve with `find_section`; `None` or `""` is the top level
for `parent`/`section` targets, and an error for the section an operation acts on. An unknown
reference lists the known section ids. `position` is the index among the target siblings without the
moved item; `None` or a value past the end appends, a negative value is an error.

| `add_element` | `name: str`, `kind: str = "component"`, `description: str = ""`, `parent: str \| None`, `id: str \| None` | the `Element`; id = slug of the name (at most 60 characters) made unique; a name used by another element (case-insensitive) → error; at most `MAX_ELEMENTS` (500); name at most `MAX_ELEMENT_NAME` (100), description at most `MAX_ELEMENT_DESCRIPTION` (300) characters after stripping (#212, ADR-0153) |
| `update_element` | `element: str`, `name`, `description`, `kind` (each `str \| None`) | `{"element", "renamed_from", "sections"}`; sections standing for the element take the new name; a new name or description is bounded like `add_element` (checked before anything changes); fields the update does not set are never checked, so an element stored before the bounds keeps its long text and takes every other change (#212). Every project write of `ProjectService` (and a bundle import) also passes `check_document_size`: a document larger than `MAX_PROJECT_BYTES` (1.9 MB, measured as compact JSON with non-ASCII escaped, as the Cosmos SDK sends it) is refused with `ProjectError` unless the write does not make it larger. Membership writes (`place`, the repair's adoption) only add a membership and may go up to `MAX_MEMBERSHIP_BYTES` (2.0 MB, a bounded overshoot), so creating, moving or adopting a diagram never fails on a full project; `reconcile` treats a refused write like a store failure (the item is skipped, logged and retried by the next run; its placement record stays), and adopts the orphans of a target one by one when they do not fit together |
| `revert_element` | `element: str`, `before: dict`, `written: dict` (each `name`, `description`, `kind`, `parent`), `fields: list[str]` | `{"element", "reverted": [fields]}`: each of `fields` (the ones the update changed) goes back to `before` only while it still has the `written` value; untouched fields are never written (sections follow the name; the compensation of a rejected update, #155, ADR-0128) |
| `move_element` | `element: str`, `parent: str \| None` | `{"element", "parent"}`; into itself or a part of it → error |
| `remove_element` | `element: str` | `{"element", "name", "moved_elements", "unlinked_sections"}`; its parts move to its parent, sections standing for it lose the link |
| `link_section` | `section: str`, `element: str \| None` | `{"section", "element", "title"}`; the title becomes the element's name; `None` unlinks |

`rename_section` refuses a new title for a section that stands for an element ("rename the element").

`ELEMENT_OPERATIONS` are the five element operations; `INTERNAL_OPERATIONS` = `("add_diagram",
"remove_diagram", *ELEMENT_OPERATIONS)`; `PUBLIC_OPERATIONS` are the others. `ProjectService.apply` refuses
an element operation without `internal=True` with a pointer to the model routes.

## Persistence

`ProjectRepositoryPort` (`arcviz/ports.py`), implemented by every `DiagramRepository`:

| Method | Contract |
|---|---|
| `list_projects()` | `[(Project, revision)]` sorted by id |
| `read_project(id)` | `(Project, revision)`; `StoreError("Project '…' not found.")` |
| `create_project(project)` | create-if-absent (id checked with `check_id`); `StoreError("… already exists.")`; returns the revision |
| `commit_project(project, expected)` | writes with a new revision if the project exists at `expected`; otherwise `ConflictError`, nothing written, never creates |
| `delete_project(id, expected)` | deletes if the project exists at `expected` (and its transcript); otherwise `ConflictError` |
| `load_project_chat`, `append_project_chat`, `project_chat_generation`, `compact_project_chat`, `clear_project_chat` | the project transcript of the project chat, with the diagram transcript's semantics ([Persistence](persistence.md#repository), [project chat](../architecture/specs/project-chat.md)) |

Backend layouts are listed under [Persistence: backends](persistence.md#backends).

## `ProjectService`

`ProjectService(projects_port, diagrams_port, guard=None, events=None, users=None, default_workspace=None)`. With a
`Guard` (the access policy bound to the caller, [personal workspaces](../architecture/specs/personal-workspaces.md))
every method checks the caller's role first: reading needs viewer, `apply`/`apply_all`/`append_chat`/`compact_chat`/
`clear_chat`/`check_target`/`place` editor, `delete`/`share`/`revoke` project owner; `create`/`create_with_sections`
always write into the caller's personal workspace (the caller owns the project through it); `list_projects` and `locate` leave out what the caller may not see; `reconcile`, `adopt` (both refuse a
guarded call) and `ensure_default` are repairs in the system context (ADR-0088). Without a guard (tests, CLI) nothing is checked. Project writes
go through `_update(id, change)`: read
with revision, run `change` on the copy, skip the write when nothing changed, `commit_project`
conditional on the revision, retried up to `MUTATION_ATTEMPTS` (5) times, then `ConflictError`.

| Method | Behaviour |
|---|---|
| `get(id)` | the project; `StoreError` if unknown |
| `list_projects()` | for a caller the projects of their listing (`services/listing.py`: own and shared ids, one point read each, re-validated; never a scan of other tenants; entries of gone or foreign projects are pruned once older than one hour, conditionally, #183); in the system context `reconcile()` first (a caller's read never repairs, ADR-0088) and every project; then `ProjectSummary(id, title, description, diagram_count, updated_at, workspace, role, owner, shared)` of the visible projects (own: `shared` False; shared with the caller: True), sorted by case-folded title, then id |
| `require(id, need)`, `visible_diagrams()` | the caller's check: `ProjectNotFound`/`Forbidden`; the member diagrams of every visible project |
| `access(id)`, `share(id, user, role)`, `revoke(id, user)` | sharing (viewer / owner / owner): `ProjectAccess(project, owner, role, shares, added)` with every `ProjectShare(id, role)` for owners and only the caller's own for others; `share` resolves `user` by id or trusted unambiguous e-mail (`UserService.find`; unknown, ambiguous, the owner or a role other than viewer/editor → `ProjectError`) and sets the share; `revoke` removes it (the owner → `ProjectError`, a missing share: no change); both re-check the owner role on the document they write and publish a `project` event |
| `view(id, infos=None)` | `ProjectView(project, tree, diagrams, role, revision)` (`revision`: the project's, the ETag of `GET /api/projects/{id}`); `diagrams` are `ProjectDiagram(id, title, updated_at, version_count, section)` for members whose diagram exists, in membership order |
| `locate(diagram_id)` | `(project id, section id)` of the project holding it, or `None` (also when the caller may not see it): through the diagram locator (`services/locations.py`, #181, ADR-0120): the `location` entry lists the projects that may hold it, which are read (the newest membership wins); no holder among them falls back to a scan for a diagram that exists, which heals the entry (or, for an orphan, records `scanned_at`: not scanned again for ten minutes); an unknown id never scans; a failed read is raised, never "not a holder" |
| `create(title, project_id=None, description="", workspace=None)` | id = `<check_id(project_id or slugify(title))>-<10 random base32 characters>` (`ports.new_ids`, ADR-0131; a generated suffix ending the base is replaced, `ports.id_base`, #200, ADR-0150; a taken or reserved candidate takes a fresh suffix, at most `ports.CREATE_ATTEMPTS` through `ports.id_attempts`, #195), in the caller's personal workspace (`workspace`, if given, must be it: `WorkspaceNotFound`; in the system context it names the workspace, default `default_workspace`) |
| `ensure_default()` | project `default` titled `Standardprojekt`, created when missing in `default_workspace` (the admin's personal workspace; without an admin the reserved id `default`, which nobody sees until the migration moves it); a concurrent creation is tolerated |
| `apply(id, op, args, internal=False)` | `projects.apply` inside `_update`; an internal operation without `internal=True` → `ProjectError` |
| `delete(id, expected=None)` | `ProjectNotEmpty` (a `StoreError`) while members exist; else `delete_project` conditional on the revision read, retried. With `expected` (HTTP `If-Match`, #175) one attempt: another revision, or a write in between, is `RevisionMismatch` (412) |
| `check_target(project_id, section, incarnation=None)` | `(project id, section id)`; with `incarnation` `ProjectNotFound` unless the project is that incarnation (`projects.incarnation_of`: the random `Project.incarnation` set by `new_project`, else `created_at` for projects written before M23; ADR-0127); `None` = the caller's own default project (`ensure_own_default`; the system context: the default project `default`, `ensure_default`); unknown project → `StoreError`, unknown section → `ProjectError` |
| `ensure_own_default()` | the caller's own default project (`personal_project_id(user)`, *Standardprojekt*, in their personal workspace), created if missing with create-if-absent (only "already exists" is a concurrent create); under an id taken by a project outside their workspace it creates `<home id>-<random suffix>` and records it as `Workspace.default_project` (logged once); ids of the forms `home_`/`user_` + 32 hex are reserved for `create` (a new id never has that form: it carries a suffix) |
| `place(diagram_id, project_id=None, section=None, position=None, new=False, source=None, incarnation=None)` | target resolved by `check_target` (with `incarnation` checked again inside the conditional membership write); the source is `source` (the `Located` a caller found already) or located here (not for `new`: a just-created diagram or an orphan); every other holder (the source and every project the entry lists) is read first, a failed read aborts before anything is written; a diagram that does not exist (checked before the writes and again after the membership write, which is then undone) is "not found"; the target is listed in the locator entry with a pending token (`add_location`), then `move_diagram` when already a member there, else `add_diagram`, then the token is cleared (`settle_location`, which lists the target again should it have been dropped meanwhile; after a failure once a re-read shows whether the membership was written, `settle_after_failure`; a token that cannot be cleared now is retried by the writer's heartbeat); then `remove_diagram` from the source and from every other project the entry lists that holds it (duplicates left by a crash), each only if its membership still has the `added_at` seen, and those dropped from the entry (`remove_location`). The entry lists every project holding the id (ADR-0120) |
| `holder(diagram_id)` | the `Located(project, member, revision)` of the diagram, or `None` (also when the caller may not see it); `locate` is its `(project id, section id)` |
| `vacate(diagram_id)` | before `DiagramService` creates an id it claimed (no diagram has it): remove the id from every project its locator entry lists (memberships of a deleted diagram whose cleanup failed); fails closed (a failed read or write raises; `DiagramService` then takes the next free id, logged once) |
| `memberships(diagram_id, holder=None)` | `(project id, added_at)` of every membership of a diagram about to be deleted: `holder` (the project the caller's check read, reused) plus the other projects its entry lists; in the system context a scan when there is no entry; a failed read raises |
| `withdraw(diagram_id, project_id)` | removes the membership of a diagram whose creation was given up after it was placed (`DiagramService` deleted it again) |
| `unplace(diagram_id, observed)` | after a delete: `remove_diagram` of each membership `memberships` observed before it (only if it still has that `added_at`), then the project dropped from the locator entry; a project deleted meanwhile is skipped |
| `intend(diagram_id, project_id)`, `settle(diagram_id, revision)` | the placement record (keyed document `placement`, `{"project", "at"}`) that `DiagramService.create`/`import_new` write before creating a diagram and remove after placing it; `intend` answers `None` when another create claimed the id (it counts as taken) |
| `reconcile()` | see below; returns `ReconcileReport(adopted, dropped, deduplicated, quarantined, into, located, skipped)` with `changed`; best effort per item (a store failure on one diagram is logged, the diagram reported in `skipped` and retried next run); system context only |
| `adopt(diagram_id, project_id)` | an admin places a diagram that is in no project (`arcviz-migrate --projects STORE --adopt DIAGRAM=PROJECT`); system context only, `ProjectError` when it already is in one |
| `load_members(id)` | `(project, [(Membership, Diagram)])`: every member diagram that still exists, in membership order (read by the project chat and the project review) |
| `chat(id)`, `chat_generation(id)`, `compact_chat(id, upto_seq, summary, generation)` | the project transcript (same signatures as `DiagramService`, so the agent's compaction runs on either) |
| `append_chat(id, entries, generation=None)` | `False` when the project no longer exists or, with `generation`, when the transcript has another one (cleared, or the project created again; ADR-0127) |
| `clear_chat(id)` | `ProjectNotFound` when the project does not exist; reads the generation between two editor checks and clears conditionally on it (retried on a change), so it acts on the incarnation the check approved |
| `holds(id, diagram_id)` | whether the diagram is a member now (editor, read again): the re-check before a confirmed proposal's note (ADR-0127) |

`reconcile()`:

1. Read the diagram ids (`list_diagrams`) and all projects.
2. For every diagram listed by more than one project, keep the membership with the newest `added_at`
   (ties: the lowest project id) and remove the others (`deduplicated`); a removal only happens if the
   membership still has the `added_at` of the first read, so a placement made meanwhile survives.
3. For every listed diagram that is not in the diagram list and for which `exists()` is false, remove
   its memberships (`dropped`).
4. Diagrams in no project are re-checked against a fresh read of all projects and `exists()`. Each remaining one
   goes into the project its placement record names (a create that crashed before placing it); without a record it
   is data from before projects and goes into the default project (after `ensure_default()`); appended in id order
   (`adopted`, `into` = diagram → project). One whose recorded project no longer exists stays in no project
   (`quarantined`): nobody sees it until an admin places it with `adopt`. Without diagrams no default project is
   created.
5. Placement records whose diagram is placed now, or that have no diagram and are older than one hour (a create
   that never happened; a younger one may be in flight), are removed.
6. The diagram locator is repaired (`repair_locations`, #181): every project holding a diagram is added to its
   `location` entry (extra names are kept), entries of diagrams that no longer exist and that no project holds are
   dropped; each write conditional on the revision listed (`located`: the number written or dropped). This is the backfill of an installation upgraded from v0.2.0
   and runs at every start and with `arcviz-migrate --projects`.

### Pending tokens of writers that died

Design: [follow-ups](../architecture/specs/follow-ups.md#pending-locator-tokens-of-crashed-writers-189), ADR-0133;
source `arcviz/services/writers.py`, `locations.Writer`; tests `tests/test_pending_tokens.py`.

- A pending token is `<boot>.<12 hex>`: `boot` (16 hex) names the writer incarnation, `locations.Writer`, one per
  process and store, new on every start (`writer_of(store)`). Tokens of M22 (16 hex, no dot) name none.
- `Heartbeat` (started and closed by the app's lifespan, `ServiceRoot.heartbeat(config)`): keyed document of kind
  `writer` per incarnation, `{"boot", "state": "alive"|"stopped"|"dead", "beats"}`, created at start-up and renewed
  every `ARCVIZ_WRITER_HEARTBEAT_SECONDS` (default 30) by a write conditional on its last revision. A failed renewal
  because the document changed means another process fenced it: the writer is `fenced` for good (no tokens, so no
  placements; `/readyz` and `/healthz` fail, so the liveness probe restarts the process). A writer that has not renewed
  for half a lease issues no tokens until it does, and the same check (`Writer.check`) runs again inside the conditional
  membership write of `place` and of the repair's adoption. The beat thread also retries the writer's abandoned tokens
  (placements that ended but could not clear theirs); reaper passes run on a thread of their own. On shutdown the
  writer stops issuing tokens, waits up to 5 s for placements in flight and then writes `stopped` (with placements
  still in flight it stays `alive`); the next lifespan starts a new incarnation.
- `Reaper.observe(healthy)`: only while this process's own heartbeat is fresh; after its own outage, a failed listing
  or a gap of more than half a lease between passes every timer starts over (a store outage fences nobody). It lists
  the `writer` documents (a background cross-partition query, never on a request path); an `alive` one whose revision
  this process saw unchanged for a whole lease (10 heartbeats) on its own monotonic clock is set to `dead` by a write
  conditional on exactly that revision (the fence). The tokens of `stopped` and `dead` incarnations are settled in one
  listing of the `location` entries per pass, only for incarnations not settled yet, and once more a lease later
  (`settle_tokens`: removes their tokens with conditional writes, then drops each such project from its entry only if
  it does not hold the diagram, `remove_location`, read again); a listing or write that fails settles nothing for that
  entry and keeps the writer document for the next pass. The document is deleted after that final pass found nothing
  left (a placement paused before the fence may still publish its token, which must find a known incarnation; a late
  token gives another lease); `settle_pending` never deletes. Registering adopts an
  existing document of the process's own boot id (a create whose answer was lost), or fences the writer if it is no
  longer `alive`.
- `arcviz-migrate --requirements STORE` repairs the references between requirements and the project (see [Requirements](requirements.md#repair)).
- `arcviz-migrate --settle-pending STORE [--wait SECONDS] [--all]` (`settle_pending`): watches every writer document
  for `--wait` seconds (default and minimum one lease), then settles stopped and dead incarnations and fences those
  that stood still for the whole wait. `--all` is for a maintenance window with every replica stopped: at least two
  heartbeats of waiting, it refuses (exit 1, nothing settled) if any heartbeat moved or a new writer appeared, fences
  every writer that stood still and also clears tokens of M22 and of unknown incarnations.

## Shared model

Design and decisions: [Shared project model](../architecture/specs/shared-project-model.md) (case study
[here](../architecture/specs/shared-project-model-case-study.md)), ADRs 0070 to 0078. Source:
`arcviz/project_model.py`, the element operations above, the model operations in
[Operations](operations.md#shared-project-model), `arcviz/services/model.py` (`ModelService`). Tests:
`tests/test_project_model.py`, `tests/test_server_model.py`, `tests/test_project_bundles.py`,
`tests/test_compat_v017.py`.

```python
ELEMENT_KINDS = ("actor", "system", "subsystem", "component", "node", "data")


class Element(BaseModel):
    id: str  # slug, unique in the project
    name: str
    kind: str = "component"
    description: str = ""
    parent: str | None = None
```

Deployment and activity `Node`s and sequence `Lifeline`s have `ref: str | None` (the element id they stand
for), omitted from every dump while unset, so documents written before M18 keep their JSON, their
fingerprints and their exports. A ref that names no element of the diagram's project is *dangling*: it is
kept (rendering ignores refs), the project review reports it, `unlink_element` clears it. Because element ids are
only unique within a project, a diagram that moves to another project brings its elements and relations along
(#153, ADR-0161, superseding ADR-0079 for moves): `DiagramService.move(diagram_id, project_id, section, position)` (the
move route) returns `(project, section, report)`. Into another project it first lets `ProjectService.carry` run
`projects.carry_model` on the target in one conditional write: every element the diagram refers to (views, ends of the
relations it refers to) and its ancestors is mapped to the target element with the same id, kind and name
(case-insensitive), else copied under the same id when that is free, else under a new id; relations the diagram
refers to (connections, `hidden_relations`), and those between carried elements of which one end was copied, are
mapped to the same relation between the carried ends or copied with an id from `relation_seq`. What an operation
refuses (a name taken by another element, the limits, the element bounds of #212) or what the size guard refuses
(`MAX_PROJECT_BYTES`, tracked by `carry_model` per element and relation from one measure before plus each item's
serialised length, with one full measure after that takes back the last additions if needed, so the rest still comes
along and the write itself never trips the guard) is left out and reported. Then one version "Verknüpfungen angepasst (anderes Projekt)" unlinks every view and connection whose id
is not the same in both projects, the membership moves, and one version "Verknüpfungen übernommen (anderes Projekt)",
written against the target as read after the membership, unlinks every link whose element or relation is gone from it
or no longer fits (`linkable_kinds`, `project_model.fits`; kept ids are checked like new ones)
and links the others to their new ids (views of elements that keep their id take the target element's name: another spelling,
or a shortened one). The report (`CarriedModel.report()`) lists
`elements` and `relations`, each `copied`/`mapped` (`{id, as}`) and `dropped` (`{id, reason}`); the move route
answers it as `carried`. If the unlink version or `place` fails, the target is read first, fresh
(`ProjectService.fresh`: never the request's cached copy, as the failed write may have landed): a written membership
means the move is done (finished against the target, the source membership withdrawn best-effort, then the failure is
raised); a definite `ProjectNotFound`/`Forbidden` means nothing moved; any other failed read means nothing is
compensated. Otherwise a version "Verknüpfungen wiederhergestellt (Verschieben fehlgeschlagen)"
links the views again against the source project as read then (only to what still exists and fits), and
`ProjectService.uncarry` takes back the copies that are still exactly what the carry added (every field) and that nothing links to
or hangs on (others are left in the target and logged; best effort). `ModelService.sync` also clears element refs
that name no element of the project (`clear_element`). `DiagramService.import_new` unlinks unless `keep_refs` (project bundles, and
`import_carrying`, which first brings a document's embedded model along the same way; see
[exchange](exchange.md)).

**Which view elements may stand for which element kinds** (`project_model.linkable_kinds(type, view_kind)`;
the first kind is the default of *promote*; `/api/config` lists it per type as `linkable`):

| View element | Element kinds |
|---|---|
| lifeline (`LIFELINE`) | component, system, subsystem, node, data, actor (actor for an actor lifeline) |
| `activity_partition` | component, actor, system, subsystem, node |
| `data_store_node`, `central_buffer_node` | data |
| other activity kinds | none |
| `component` (component diagrams) | component, system, subsystem |
| `subsystem` (component diagrams) | subsystem, system |
| `actor` (use case diagrams) | actor |
| `subject` (use case diagrams) | system, subsystem |
| `class`, `enumeration`, `data_type` (class diagrams) | data |
| `interface` (class diagrams) | component |
| every state machine kind | none |
| built-in deployment containers | node, system, subsystem |
| `database`, `storage`, `queue` | data, component |
| `external` | system, actor, component |
| other built-in deployment leaves | component, system, subsystem |
| configured container / leaf kinds | node, system, subsystem / every kind |

Pure queries: `view_elements(diagram)`, `find_view(diagram, ref)`, `references(diagram, element_id)`,
`default_kind(diagram, view)`, `find_element(elements, ref)`, `model_summary(elements, usages, budget)` (the
prompt lines `- [id] «kind» name (part of p) — description (in: …)`, descriptions dropped first, then the
rest counted).

### `ModelService`

`ModelService(projects, diagrams)`; element writes are `ProjectService.apply(…, internal=True)` (conditional on
the project revision), diagram writes go through `DiagramService` (conditional, versioned).

| Method | Behaviour |
|---|---|
| `model(pid)` | `ProjectModel(project, elements)`: per element its fields, `usages` (`Usage(diagram, title, type, view, name)`) and the `sections` standing for it |
| `elements(pid)`, `element(pid, ref)` | the elements; one element (`ProjectError` when unknown) |
| `project_of(diagram_id)` | the diagram's project (`locate`); `ProjectError` when it is in none |
| `add_element(pid, name, kind, description, parent, element_id)` | `add_element` |
| `update_element(pid, element, name?, description?, kind?, parent?)` | a new kind is checked against every view first (`ProjectError` naming the views that cannot stand for it); `update_element` (and `move_element` when `parent` is given, also `None`) as one project write (`ProjectService.apply_all`), so an error changes nothing; after a kind change the member diagrams are read again, and a link that cannot stand for the new kind and appeared meanwhile makes it undo the update (`revert_element`: only the fields this update changed, each only while it still has the value written, so a concurrent change is never undone; on a `ProjectError` the kind alone; retried, and a lasting failure is said in the refusal; diagrams that cannot be read count as blocking, fail closed; no rename reaches the diagrams) and raise the same `ProjectError` (#155, ADR-0128); when the name really changed, `sync_element` on every member diagram referencing the element, each through `apply_if_changed` labelled `Element „<name>“ umbenannt`. Returns `ElementChange(element, propagated, failed)`; `failed` lists diagrams that kept conflicting (drift until `sync`); deleted diagrams are skipped |
| `move_element(pid, element, parent)` | `move_element` |
| `remove_element(pid, element)` | `clear_element` on every referencing diagram (`Element „<name>“ entfernt`), then `remove_element`; `ConflictError` and the element kept when a diagram kept conflicting. Returns `ElementRemoval(element, unlinked, failed)` |
| `link_section(pid, section, element)` | `link_section` |
| `link_args(pid, view, element)` | the arguments of the internal `link_element` (element id, name, kind) |
| `link(diagram_id, view, element)` | `link_element` through `apply_operation` (label `Mit Element „<name>“ verknüpft`); then `link_problem` reads the element again, and when its kind no longer suits the view (changed meanwhile), it is gone, or it cannot be read (fail closed), the link is taken back (`unlink_element` labelled `Verknüpfung zurückgenommen`, only while the view still stands for that element; retried up to `COMPENSATION_ATTEMPTS` 3) and `ProjectError` is raised; if the take-back cannot be written, the error says the link stays (the project review reports it) and a warning is logged (#155, ADR-0128). The agents' `link_element` does the same through the turn's write-through (`ModelTools.verify_link`, a tool error) |
| `link_problem(pid, element, diagram_type, view_kind)` | after a link was written: why the view cannot stand for the element now (read again, never the request's cached copy), else `None` |
| `unlink(diagram_id, view)` | `unlink_element` (label `Verknüpfung gelöst`) |
| `promote(diagram_id, view, kind?, description="")` | an element named like the view element (default kind from `default_kind`), then `link`; the element is removed again when the link fails |
| `sync(pid)` | per diagram one write with a `sync_element` step for every element whose views differ (`Modell abgeglichen`); `SyncReport(changed, failed)`; writes nothing when consistent |

`DiagramService.apply_if_changed(diagram_id, steps, label)` applies `[(op, args)]` as one versioned write and writes
nothing (and returns `None`) when they leave the content unchanged. `ProjectService.apply_all(pid, steps,
internal=False)` applies several project operations as one conditional write (all or none); the section PATCH
uses it for its link, rename and move.

### HTTP

| Route | Body | Answer |
|---|---|---|
| `GET /api/projects/{pid}/model` | | `ProjectModel` (reads every member diagram for the usages) |
| `GET /api/projects/{pid}/elements` | | `{project, elements}`: the elements only, from the project document |
| `POST /api/projects/{pid}/elements` | `{name, kind?, description?, parent?, id?}` | 201 `{element, model}` |
| `PATCH /api/projects/{pid}/elements/{eid}` | `{name?, description?, kind?, parent?}` (`parent` present = move) | `{element, propagated, failed, model}` |
| `DELETE /api/projects/{pid}/elements/{eid}` | | `{element, unlinked, failed, model}` |
| `GET /api/projects/{pid}/elements/{eid}/code-refs` | | `{element, code_refs}` |
| `POST /api/projects/{pid}/elements/{eid}/code-refs` | `{path, repo?, symbol?, note?}` (unknown fields 422) | 201 `{element, code_refs}` |
| `DELETE /api/projects/{pid}/elements/{eid}/code-refs?path=&repo=&symbol=` | | `{element, code_refs}`, 400 no such link |
| `GET /api/projects/{pid}/code-owners?path=&repo=` | | `{path, owners: [{element, code_ref}]}` |
| `POST /api/projects/{pid}/model/sync` | | `{changed, failed, model}` |
| `PATCH /api/projects/{pid}/sections/{sid}` | `element` (present = link, `null` = unlink; applied before a title) | project view |
| `POST /api/diagrams/{id}/link` | `{view, element}` | diagram detail plus `result` |
| `POST /api/diagrams/{id}/unlink` | `{view}` | diagram detail plus `result` |
| `POST /api/diagrams/{id}/promote` | `{view, kind?, description?}` | 201 `{element, …detail, result}` |

Unknown project or diagram 404, `ProjectError`/`OperationError` 400 (unknown element or view, incompatible
kind, a diagram in no project), `ConflictError` 409. `POST /api/projects/{pid}/ops` refuses the element
operations (400). The routes are under `/api`: authentication and the `ops` rate-limit class apply.

### Relations

Design and decisions: [Model relations](../architecture/specs/model-relations.md), ADRs 0135 to 0141. Tests:
`tests/test_relations.py`, `tests/test_project_bundles.py`, `tests/test_compat_v040.py`.

```python
RELATION_KINDS = ("communicates", "deployed_on", "data_flow")
MAX_RELATIONS = 500  # per project
MAX_RELATION_DESCRIPTION = 300
MAX_PROPERTY_VALUE = 64


class Relation(BaseModel):
    id: str  # slug of at most 64 characters, unique; "r1", "r2", ... unless given, never reused
    kind: str
    source: str  # element id
    target: str  # element id
    properties: dict[str, str] = {}
    description: str = ""
```

`Project.relations` (omitted from dumps while empty). Kind rules (`RELATION_RULES`, `relation_problem(kind,
source_kind, target_kind)`, `property_problem(kind, key, value)`):

| Kind | Source kinds | Target kinds | Properties |
|---|---|---|---|
| `communicates` | every kind | every kind | `protocol`, `port`, `direction` (`forward`/`both`/`none`), `mode` (`sync`/`async`) |
| `deployed_on` | component, subsystem, system, data, node | node | none |
| `data_flow` | actor, system, subsystem, component, data | the same | `protocol`, `port`, `mode` |

No relation connects an element with itself; a second relation of the same kind between the same ends is refused
(`communicates` in either orientation, `same_relation`). Project operations (internal, `RELATION_OPERATIONS`):
`add_relation(kind, source, target, properties?, description?, id?)`, `update_relation(relation, kind?, source?,
target?, properties?, remove_properties?, description?)` (result `{relation, ends_changed}`),
`remove_relation(relation)`. Generated ids continue after `Project.relation_seq` (highest number issued, omitted while
0) and the highest `r<n>` present, so an id is never reused; relation ids, the element ids at their ends and explicit
element ids are at most `MAX_ID_LENGTH` (64) characters. Ends resolve like elements (id, name, slug); relations by id
(`find_relation`, case-insensitive). `remove_element` also removes the element's relations (`removed_relations`);
`update_element` refuses a kind that a relation's rule forbids. `integrity_errors` checks ids, kinds, ends, rules,
duplicates, properties, description length and `MAX_RELATIONS`. Element containment stays `Element.parent`.

`ModelService`: `relations(pid)`, `relation(pid, ref)` (`ProjectError` when unknown), `add_relation(pid, kind,
source, target, properties, description, relation_id)`, `update_relation(pid, relation, …) -> RelationChange(relation,
unlinked, failed)`, `remove_relation(pid, relation) -> RelationRemoval(relation, unlinked, failed)`. `model(pid)` adds
`relations` (`RelationView`: the fields plus `usages` and `shown`). `/api/config` `project_model` adds
`relation_kinds`, `max_relations`, `relation_rules` (`{kind: {source, target}}`), `relation_properties` and
`property_values`. Project bundles carry `project.relations` and `project.relation_seq` and are `schema_version` 3 when
there are any; `parse_project` restores the counter as the highest of the bundle's counter and every `r<n>` of its
relations, links and hidden markers (`ProjectBundle.relation_seq`), and refuses element ids longer than 64 characters.
Elements by default ([design](../architecture/specs/elements-by-default.md), ADR-0164):
`project_model.adoptable(type, view_kind)` (deployment leaf kinds and `node`/`cluster`, not `deployment`, `vnet`,
`subnet`, `region`, `group` or a kinds file's containers; every lifeline; `activity_partition`, `data_store_node`),
`loose_key(name)` (letters and digits, case-folded), `adoption_target(elements, name)` (the same `name_key`, else the
only element with the same loose key), `adoptable_views(diagram, known)`. `services/adoption.py`: `Adopter(projects,
project_id)` with `prepare(before_ids, after) -> AdoptionPlan` (creates missing elements of `default_kind`, one project
write each; an element created meanwhile is matched instead; a refused create skips the view with its reason),
`steps(before_ids, diagram, plan)` (internal `link_element` per new unlinked view whose element fits), `outcome(...)
-> Adoption(linked, created, skipped)`, `finish(plan, steps)` (removes created elements that no member diagram or section refers to, read again, best effort);
`adopting(adopter, diagram, apply, failed)` wraps a change. `ModelService`: `adopter(diagram_id)` (None outside a
project), `project_adopter(pid)`, `adoption_problems(pid, diagram, adoption)` and `settle_adoption(diagram_id, adoption)`
(adopted links whose element was removed or re-kinded meanwhile are taken back and reported as skipped, ADR-0128), `apply_operation(diagram_id, op, args, label, expected)` (the `/ops` route: operation
and links in one version), `adoption_plan(pid)` and `adopt_all(pid) -> (done, failed)` (one version "Projektelemente
übernommen" per diagram). `proposal.apply_proposal(…, models=)` adopts after the preview check and settles.
Decision support ([design](../architecture/specs/decision-support.md), M29, ADR-0166..0168): `Project.stakeholders`,
`Project.scenarios`, `Project.decisions` and `stakeholder_seq`/`scenario_seq`/`decision_seq` (all omitted while empty /
0) with the types and bounds of `decision_model.py` (`MAX_STAKEHOLDERS` 50, `MAX_SCENARIOS` 200, `MAX_DECISIONS` 100;
ids `s<n>`, `q<n>`, `adr<n>`, never reused); integrity checks ids, closed sets and references. Public operations
`add/update/remove_stakeholder|scenario|decision` (`projects.DECISION_SUPPORT_OPERATIONS`): references by id or title/name
(a decision also as `ADR-<n>`), a core scenario needs its driver, `update_decision(status="accepted")` sets `decided_at`,
`superseded` needs `superseded_by`. Cascades in the same write: `remove_driver` unassigns its scenarios (no longer core)
and drops it from stakeholders and decisions (`unassigned_scenarios`), `remove_element` drops it from scenarios and
decisions, `remove_scenario` clears the decisions' `scenario`, `remove_decision` turns a decision it superseded into
rejected. Bundles carry the lists and counters as `schema_version` 5 (`DRIVERS_PROJECT_SCHEMA_VERSION` 4 with drivers
only); `ProjectBundle.decision_support` passes them to `create_with_sections(…, decision_support=)`.
Graded deletion ([design](../architecture/specs/graded-deletion.md), ADR-0198): `Driver.status` and `Scenario.status` are
`active` (default, omitted from dumps) or `retired` (`project_model.OBJECT_STATUSES`, set by `update_driver|update_scenario(
status=)`; helpers `is_retired`, `active`). `remove_driver` and `remove_scenario` refuse an object that is not retired, and
`remove_decision` refuses an `accepted` one (drafts, rejected and superseded decisions may go): `ProjectStateError`
(a `ProjectError`; REST 409, exported by `services`). The cascades above are unchanged. A scenario or decision may not be
newly linked to a retired driver (`_live_driver`; an existing link stays). Retired drivers and scenarios are left out of
`drivers_summary`, `drivers_with_core_scenarios`, `core_scenario_lines` (both assistants, the project chat, the scenario
analysis and its `driver` ids), the requirement refine vocabulary and derive context, the `scenario-no-requirement` review
rule, the retired drivers named in `stakeholder_lines(…, drivers=)`, the MCP `get_architecture_context` and the default of `list_drivers`/`list_scenarios` (`include_retired`), and
`requirement_model.traces` (so a requirement covered only by a retired object is `uncovered`). REST lists, exports and
bundles keep them with their `status`.
Architecture drivers ([design](../architecture/specs/architecture-drivers.md), ADR-0163): `Project.drivers`
(`project_model.Driver`: `id` `d<n>` from `Project.driver_seq`, never reused; `title` ≤ `MAX_DRIVER_TITLE` 120, unique
case-insensitively; `kind` of `DRIVER_KINDS` quality_goal/constraint/requirement/risk; `importance` and `risk` of
`DRIVER_LEVELS` high/medium/low; `description` ≤ 1000; `elements`: ids of project elements), at most `MAX_DRIVERS` 100,
both fields omitted from dumps while empty / 0. Public project operations `add_driver(title, kind, importance, risk,
description, elements, position)`, `update_driver(driver, …)`, `move_driver(driver, position)`, `remove_driver(driver)`
(`projects.DRIVER_OPERATIONS`; `driver` is an id or a title); `remove_element` also drops the element from every
driver's `elements` (result `drivers`). Integrity: unique ids, kinds and levels from the sets, known elements.
`project_model.ranked_drivers` orders by importance, then risk, then list order (`priority_key`). `ModelService`:
`project(pid)`, `ranked_drivers(pid)`, `driver_operation(pid, op, args)`. `/api/config` `project_model` adds
`driver_kinds`, `driver_levels`, `max_drivers`, `max_driver_title`, `max_driver_description`. Project bundles with
drivers carry `project.drivers` and `project.driver_seq` and are `schema_version` 4 (`RELATIONS_PROJECT_SCHEMA_VERSION`
3 otherwise with relations); `parse_project` checks them like the operations and restores the counter as the highest
of the bundle's and every `d<n>` (`ProjectBundle.drivers`, `driver_seq`), and the import keeps them
(`ProjectService.create_with_sections(…, drivers=, driver_seq=)`).
`relation_number(id)`/`next_relation_number(seq, ids)`: only `r<digits>` ids that fit count; an explicit one whose
successor would not fit is refused, and generation that would not fit is a `ProjectError`, never an integrity failure.

**Links from views** ([operations](operations.md#model-relations)): `Edge.ref` and `Message.ref` (omitted while
unset). Pure queries: `view_connections(diagram)` (`ViewConnection(id, kind, source, target, source_ref, target_ref,
ref, label)`), `fits(type, connection, relation)`, `relation_kinds_for`, `relation_table(type)` (`/api/config`
`diagram_types[type].relations`), `shown_relation_kinds(type)`.

| Method | Behaviour |
|---|---|
| `link_relation(diagram_id, view, relation, expected?)` | `link_relation` with the relation's kind and ends (label `Mit Beziehung <id> verknüpft`) |
| `unlink_relation(diagram_id, view, expected?)` | `unlink_relation` (label `Beziehungsverknüpfung gelöst`) |
| `update_relation` | after the project write, when kind or ends changed: `sync_relation` on every member diagram that links it (`Beziehung <id> geändert`); `unlinked`/`failed` list the diagrams. Properties and description reach no view (ADR-0138) |
| `remove_relation` | `clear_relation` on every diagram that links (or hides) it (`Beziehung <id> entfernt`), then the project write; `ConflictError` and the relation kept when a diagram kept changing |
| `remove_element` | also `clear_relation` for each of the element's relations in the same per-diagram write |
| `sync(pid)` | also clears links that no longer fit their relation and links (and hidden markers) of relations that no longer exist, in the diagram's one version |
| `model(pid)` | `relations[].usages` (`RelationUsage(diagram, title, type, view, label)`: linked connections) and `relations[].shown` (unlinked connections that fit) |

A move to another project and a single-diagram import also unlink every edge and message (`_unlink_all`).

**Completeness and hidden relations** (ADR-0137), deployment, sequence and component diagrams only
(`model.RELATION_DIAGRAM_TYPES`; `integrity_errors` refuses relation refs, `complete` and `hidden_relations` on other
types, and the model service sends them no relation operation): `Diagram.complete` (relation kinds the diagram declares complete)
and `Diagram.hidden_relations` (relation ids it intentionally leaves out), both kept sorted and unique (a field
validator normalises any document, also an import), both omitted from dumps while empty, both
content (fingerprint, versions, undo). `set_completeness` is a public operation (REST `/ops`, agent tool);
`ModelService.hide_relation(diagram_id, relation, hidden, expected?)` runs the internal `set_relation_hidden` for a
relation of the diagram's project (labels `Beziehung <id> hier ausgeblendet` / `… nicht mehr ausgeblendet`).
`remove_relation` clears the marker with the links; `sync` clears markers of relations that no longer exist; a move to
another project and a single import drop the markers (relation ids are project-scoped) and keep `complete`.
`model(pid).diagrams` lists every member `{id, title, type, complete, hidden}`; `GET /api/projects/{pid}/elements` also
returns `relations`; `/api/config` `diagram_types[type].completeness` lists the kinds a type may declare.
`parse_document` refuses completeness kinds the type cannot show, invalid hidden ids, relation refs on connections
that cannot show a relation or whose ends do not stand for elements.

### Code links

([Design](../architecture/specs/agent-access.md), ADR-0171.) `Element.code_refs` is a list of `CodeRef` (`path`, `repo`,
`symbol`, `note`), omitted from dumps while empty. `path` is stored normalised by `project_model.normalise_code_path`
(repository-relative, `/` separators, `./` and trailing `/` dropped, `.` for the repository root; empty, absolute,
drive-lettered, backslashed, control-character and `..` paths are refused, at most `MAX_CODE_PATH` 200 characters);
`repo` is a label of letters, digits, `.`, `_`, `-` (≤ 64), `symbol` ≤ 64, `note` ≤ 200, all one line. An element holds
at most `MAX_CODE_REFS` 20; links are keyed by (path, repo, symbol). The public project operations `add_code_ref`
(the same key again only updates the note) and `remove_code_ref` (`projects.CODE_REF_OPERATIONS`) touch only the
project document, so `ModelService.code_ref_operation` is one conditional write. `project_model.owner_of_path` gives the
elements whose link is the longest prefix of a path (ties keep all; `.` owns everything; with a `repo` only links of
that repository or without one count). `remove_element` removes the links with the element; `update_element` never
touches them. `ProjectModel.to_dict` adds `code_refs` to an element only when it has links. Project bundles with links
are `schema_version` 6. Both assistants have the tools `add_code_ref` and `remove_code_ref` (writes on request, refused
in preview mode); `list_elements` shows the links.

### Compatibility

Nothing is migrated (ADR-0078): the model is part of the project document (Cosmos item `project` in partition
`project:<id>`, `projects/<id>.json`, table `projects`), new fields have defaults, unset refs are not
written (also an empty `relations` list, M25). `tests/test_compat_v040.py` does the same for stores written by v0.4.0
(`tests/fixtures/v0_4_0/`): dumps, fingerprints, exports, the model answer and the bundle are unchanged.
`tests/test_compat_v017.py` loads file, sqlite and Cosmos stores written by the v0.1.7 code
(`tests/fixtures/v0_1_7/`) and checks content, fingerprints, version history, undo/redo targets, exports and
transcripts against what v0.1.7 answered, then edits them.
