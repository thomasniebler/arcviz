# Shared project model: project elements and diagrams as views

- Status: Implemented (milestone M18)
- Issues: #137 (case study), #144..#151
- Case study: [Shared project model case study](shared-project-model-case-study.md)
- ADRs: [0070 Model elements in the project document, referenced from views](../adr/0070-shared-model-elements-in-the-project-document.md),
  [0071 Names live in the model and are propagated into views](../adr/0071-names-propagated-into-views-drift-repaired.md),
  [0072 Deleting from a view or from the model](../adr/0072-deletion-from-view-or-model-dangling-refs-tolerated.md),
  [0073 Element kinds and which view elements may represent them](../adr/0073-element-kinds-and-view-compatibility.md),
  [0074 Sections reference subsystem elements](../adr/0074-sections-reference-subsystem-elements.md),
  [0075 Cross-diagram consistency rules over element references](../adr/0075-cross-diagram-consistency-rules.md),
  [0076 Shared elements in the diagram and project chat](../adr/0076-shared-elements-in-the-chats.md),
  [0077 Project bundles carry the model](../adr/0077-project-bundles-carry-the-model.md),
  [0078 No repartitioning of the stores for the model](../adr/0078-no-repartitioning-for-the-model.md),
  [0079 Links are dropped when a diagram leaves its project](../adr/0079-links-dropped-when-a-diagram-leaves-its-project.md)
- Builds on: [Projects](projects.md), [Project chat](project-chat.md), [Project review](project-review.md),
  [Project exchange](project-exchange.md), [Application services](application-services.md)

## Summary

A project gets a **model**: a list of project-wide **elements** (id, name, abstract kind, description,
optional parent element) stored in the project document. View elements of every diagram type — deployment
and activity **nodes** and sequence **lifelines** — and project **sections** may reference one element
through an optional `ref` (sections: `element`). A linked view element always carries its element's name:
renaming an element in the model renames every view of it (one versioned diagram change per diagram),
and its description is shown wherever it is referenced. Deleting from a view keeps the element; deleting
an element unlinks its views. The project review gains cross-diagram consistency rules over the
references; the diagram and project chats can list, create, rename and link elements; project bundles
carry the model. Nothing in the stores changes: data written by v0.1.7 loads and works unchanged.

## Goals

- Project-wide elements with identity across diagram types: a deployment node, a sequence lifeline and
  an activity partition can refer to the same element.
- Rename or describe once, see it everywhere; delete semantics that never surprise.
- Sections can stand for subsystems (or systems) of the model.
- Cross-diagram consistency rules in the project review.
- The diagram chat and the project chat use and create shared elements.
- Project export/import carries the model.
- Existing diagrams and projects (v0.1.7) load and work unchanged; no data migration.

## Non-goals

- Model relations (an edge or message as an instance of a model-level relation): relations stayed derived in M18;
  M25 lifts this non-goal ([Model relations and cross-diagram consistency](model-relations.md)).
- Elements following a diagram that moves to another project: since #153 they come along (ADR-0161).
- Resolving names at render time, a model/view split, or a model browser with its own diagrams.
- Linking behaviour (activity actions, control nodes, messages, fragments): they belong to one diagram.
- Repartitioning Cosmos DB by project, new store primitives (#143 stays open).

## Design

### Domain (`arcviz/project_model.py`, pure)

```python
ELEMENT_KINDS = ("actor", "system", "subsystem", "component", "node", "data")
MAX_ELEMENTS = 500  # per project; keeps the project item far below Cosmos DB's 2 MB


class Element(BaseModel):
    id: str  # slug, unique in the project
    name: str
    kind: str = "component"  # one of ELEMENT_KINDS
    description: str = ""
    parent: str | None = None  # the containing element (e.g. a subsystem)
```

`Project.elements: list[Element]` (default empty), `Section.element: str | None` (default none).
`Node.ref: str | None` and `Lifeline.ref: str | None` (default none). An unset `ref` / `element` is
**omitted** from `model_dump` (a wrap serializer on the class), so documents written before M18 dump,
fingerprint and export byte-identically.

Pure helpers: `find_element(project, ref)` (id, case-insensitive name, slug), `view_kind(diagram, view_id)`
(`node.kind`, or `"lifeline"`), `linkable_kinds(diagram_type, view_kind) -> tuple[str, ...]` (empty = the view
element cannot be linked; first = the default kind for "promote"), `view_elements(diagram)` (every linkable
view element as `(id, name, view kind, ref)`), `references(diagram, element_id)`.

Compatibility (ADR-0073):

| View element | May represent |
|---|---|
| sequence `lifeline` | every kind (default: actor for an actor lifeline, else component) |
| activity `activity_partition` | component, actor, system, subsystem, node |
| activity `data_store_node`, `central_buffer_node` | data |
| other activity kinds | — (behaviour) |
| deployment container kinds (`deployment`, `vnet`, `subnet`, `node`, `cluster`, `group`, `region`) | node, system, subsystem |
| deployment `database`, `storage`, `queue` | data, component |
| deployment `external` | system, actor, component |
| other built-in deployment leaves | component, system, subsystem |
| configured kinds (kinds file) | container: node, system, subsystem; leaf: every kind |

### Project operations (`projects.py`, internal)

`add_element(name, kind="component", description="", parent=None, id=None) -> Element`,
`update_element(element, name=None, description=None, kind=None)`, `move_element(element, parent=None)`,
`remove_element(element)` (children move to its parent, sections linked to it lose the link),
`link_section(section, element=None)` (the section title becomes the element's name; `None` unlinks).
`update_element` with a new name also renames the sections linked to it (same document).
`rename_section` refuses a linked section ("rename the element"). All five are in
`INTERNAL_OPERATIONS`: `POST /api/projects/{pid}/ops` refuses them, because only the model service knows
the diagrams a rename or delete must reach. Integrity adds: unique element ids, valid slugs, known kinds,
known parents, no parent cycles, sections link known elements, at most `MAX_ELEMENTS`.

### Diagram operations (`operations.py`, every diagram type)

| Operation | Arguments | Public |
|---|---|---|
| `link_element` | `id`, `element`, `name`, `element_kind` | no |
| `unlink_element` | `id` | yes |
| `sync_element` | `element`, `name` | no |
| `clear_element` | `element` | no |

`link_element` resolves the view element (`find_node`, or a lifeline by id/name), checks
`linkable_kinds`, sets `ref` and renames the view element to `name`. `sync_element` renames every view
element with that `ref`; `clear_element` clears those refs. Internal operations take the element's name
and kind as arguments, so only `ModelService` may call them: the REST `/ops` route, `apply_batch`, agent
tool calls and proposal replays refuse them (`operations.INTERNAL_OPERATIONS`). `update_node` and
`update_lifeline` refuse to change the name of a linked view element (ADR-0071).

### Service (`arcviz/services/model.py`, `ModelService(projects, diagrams)`)

- `model(pid) -> ProjectModel`: elements, each with its `usages` (`diagram`, `title`, `type`, `view`,
  `name`) and linked `sections`, read from the project and its member diagrams.
- `add_element(pid, name, kind, description, parent, id) -> Element` (project write).
- `update_element(pid, element, name?, description?, kind?, parent?) -> ElementChange`: a kind change is
  checked against every usage first (`ProjectError` listing the views that cannot represent the new kind);
  then the project write; then, for a new name, `sync_element` on every member diagram that references the
  element, each a conditional, versioned write labelled `Element „<name>“ umbenannt` (`propagated`); a
  diagram that cannot be written after the retries is listed in `failed` (drift, repaired by `sync`).
- `remove_element(pid, element) -> ElementRemoval`: `clear_element` on every referencing diagram
  (versioned, `Element „<name>“ gelöst`), then the project write.
- `link(diagram_id, view, element)`, `unlink(diagram_id, view)`, `promote(diagram_id, view, kind?,
  description?)` (create an element named like the view element, then link): the diagram's project is
  `ProjectService.locate`; `ProjectError` (400) when the diagram is in no project.
- `link_section(pid, section, element?)`.
- `sync(pid) -> SyncReport`: `sync_element` for every element on every member diagram; idempotent.

Writes that change nothing are skipped (`DiagramService.apply_if_changed(diagram_id, steps, label)`), so a sync of
a consistent project writes nothing; a sync writes at most one version per diagram ("Modell abgeglichen"). An
element PATCH (`update_element` plus a move) and a section PATCH (link, rename, move) are one project write each,
so a 400 means nothing changed; a rename reaches the diagrams only when the name really changed.

### HTTP API

| Route | Body | Answer |
|---|---|---|
| `GET /api/projects/{pid}/model` | | `ProjectModel` |
| `GET /api/projects/{pid}/elements` | | `{project, elements}` (project document only; the inspector's read) |
| `POST /api/projects/{pid}/elements` | `{name, kind?, description?, parent?, id?}` | 201 `{element, model}` |
| `PATCH /api/projects/{pid}/elements/{eid}` | `{name?, description?, kind?, parent?}` (`parent` present = move, `null` = top level) | `{element, propagated, failed, model}` |
| `DELETE /api/projects/{pid}/elements/{eid}` | | `{unlinked, failed, model}` |
| `POST /api/projects/{pid}/model/sync` | | `{changed, failed, model}` |
| `PATCH /api/projects/{pid}/sections/{sid}` | adds `element` (present = link, `null` = unlink) | project view |
| `POST /api/diagrams/{id}/link` | `{view, element}` | diagram detail |
| `POST /api/diagrams/{id}/unlink` | `{view}` | diagram detail |
| `POST /api/diagrams/{id}/promote` | `{view, kind?, description?}` | `{element, ...diagram detail}` |

Errors: unknown project/diagram 404, invalid input (`OperationError`/`ProjectError`) 400, conflicts 409.
All routes are under `/api`: authentication and the `ops` rate-limit class apply unchanged.

### Agent and project chat (ADR-0076)

Tools for a diagram that belongs to a project: `list_elements`, `create_element(name, kind, description?,
parent?)`, `update_element(element, name?, description?, kind?)`, `link_element(id, element)`,
`unlink_element(id)`. The prompt gets the block `prompts/shared/model.txt` and the project's elements
(`model_summary`, budget 4 000 characters) after the stable prefix; `Diagram.summary()` marks linked
elements with `(= element <id>)`. `create_element`, `update_element` and `link_element` are refused in
propose mode (a proposal changes one diagram, ADR-0060). `update_element` propagates through
`ModelService`; afterwards the agent re-reads every diagram it holds (their revisions moved on), so the
turn continues on the stored state and its final version is conditional on the right revision. The
project chat offers the same tools whenever a diagram is active (`list_elements`, `create_element` and
`update_element` also without one).

### Project review (ADR-0075)

`ProjectFinding.model_element: str | None`. New project rules: `dangling-element-ref`,
`element-name-drift`, `incompatible-element-link`, `unused-element`, `element-not-in-structure`,
`message-without-connection`, `unlinked-namesake` (definitions in the ADR). The LLM digest lists the
model (`Model elements:` with kinds, descriptions and usages).

### Exchange (ADR-0077)

Project bundles: `project.elements` and `sections[].element`; `schema_version` 2 when the project has
elements or linked sections, else 1 (an export of a project without model is unchanged). `parse_project`
reads 1 and 2. Refs inside the embedded diagram documents are kept as they are (element ids are
project-scoped, the import creates the elements with their ids in the new project). Diagram documents
carry `ref` like any other field and, since #153, the referenced part of the model under `model`; because element ids
are only unique within a project, an import and a move to another project bring the elements and relations along and
link the views to them (ADR-0161; mapped, copied or, when they do not fit, dropped as in ADR-0079). A document
without `model` drops its refs (ADR-0079, amending ADR-0072/0077).

### Concurrency and errors

- Element operations are project writes conditional on the project revision (ADR-0044).
- Propagation and unlinking are per-diagram conditional writes, ordered *project first* for a rename and
  *diagrams first* for a delete, so a crash leaves at worst drift or an unused element, which the review
  reports and `sync` (or a second delete) repairs. A link racing a rename can link with the old name
  (drift, same repair).
- The `file` and `sqlite` stores keep their single-writer rule.

## Testing

- `tests/test_project_model.py`: domain, compatibility table, project and diagram operations (rollback,
  integrity, refusal of renaming linked elements, internal operations refused by `/ops`/batch/agent).
- `tests/test_model_service.py`: propagation, delete, kind change, promote, sync, failures.
- `tests/test_server_model.py`: every route, status codes.
- `tests/test_project_agent.py` / `tests/test_agent.py`: element tools with `ScriptedProvider`.
- `tests/test_project_review.py`: each new rule.
- `tests/test_project_bundles.py`: round trip with a model, schema versions.
- `tests/test_properties.py`: `tests/strategies.py` draws link/unlink/sync/clear; invariants.
- `tests/test_compat_v017.py`: file, sqlite and Cosmos-fake stores written by v0.1.7 load, render, undo,
  export identically and accept links.
- `tests/js/model.test.mjs` (pure helpers), e2e: create an element, link it in two diagrams, rename it.

## Migration and compatibility

No migration. Unset refs are omitted when dumping, so old documents and their fingerprints are unchanged;
new fields have defaults. `arcviz-migrate` copies project documents as before (they hold the model).

## Plan

1. #137 `docs(architecture)`: case study, this spec, ADR-0070..0078.
2. #144 `test(store)`: stores written by v0.1.7 load and work unchanged.
3. #145 `feat(model)`: project elements and section links.
4. #146 `feat(model)`: link view elements of all three diagram types.
5. #147 `feat(model)`: rename propagation, deletion and the sync repair.
6. #148 `feat(agent)`: shared elements in the diagram and project chat.
7. #149 `feat(review)`: cross-diagram consistency rules.
8. #150 `feat(exchange)`: project bundles carry the model.
9. #151 `feat(ui)`: model panel and linking in the inspector.

## Open questions

None open; the decisions the maintainer should review are the Proposed ADRs above.

## Decisions

- Incremental references, not a model/view split (ADR-0070).
- Names denormalised and propagated, linked view names not editable in the view, drift repaired by
  sync (ADR-0071).
- Delete from view keeps the element; delete from model unlinks, never deletes view content; dangling refs
  tolerated (ADR-0072).
- Abstract element kinds with a compatibility table (ADR-0073).
- Sections reference elements (ADR-0074).
- Consistency rules (ADR-0075); chat tools, refused in propose mode (ADR-0076); bundle schema 2 only with
  a model (ADR-0077); no storage repartitioning (ADR-0078).
