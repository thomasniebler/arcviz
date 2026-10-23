# Case study: a shared project model (diagrams as views)

- Status: Done (milestone M18); the recommendation is specified in [Shared project model](shared-project-model.md)
- Issue: #137
- ADR: [0070 Shared model elements in the project document, referenced from views](../adr/0070-shared-model-elements-in-the-project-document.md)
  (the recommendation), further decisions in ADR-0071..0078
- Builds on: [Projects](projects.md) (M16, ADR-0051..0056), [Project chat](project-chat.md) and
  [Project review](project-review.md) (M17, ADR-0057..0064), [Assistant prompts](assistant-prompts.md) (M19)

## Question

Projects (M16) group **independent** diagrams (option A): a deployment node "Order Service", a sequence
lifeline "Order Service" and an activity partition "Order Service" are three unrelated strings in three
documents. Renaming the service means three edits; the project review (M17) can only compare titles.
Modelling tools such as Structurizr, Enterprise Architect or MagicDraw keep **one model per project** and
draw diagrams as **views** of it (option B). This study asks how far Arcviz should go towards option B,
how, at what cost, and without breaking the data that releases up to v0.1.7 wrote.

## What exists today (read from the code)

| Area | Today | Relevant for B |
|---|---|---|
| Diagram document | `Diagram` (`model.py`): `nodes` (dict id → `Node{id,name,kind,parent,properties}`), `edges`, `interaction` for sequence diagrams (`sequence.py`: `Lifeline{id,name,type,actor}`, a fragment tree of messages and fragments). Versions are full snapshots with a content `fingerprint` (ADR-0003) that drives undo/redo and the "did the turn change anything" check. | A view element needs a stable link; adding a field changes `model_dump`, therefore fingerprints, unless the field is left out while unset. |
| Mutations | One path: `operations.apply(diagram, op, args)` with signature validation and rollback (ADR-0045); per-type tables `TYPE_OPERATIONS`; `apply_batch`. | Linking must be an operation, so it is validated, versioned, undoable and visible in comparisons. |
| Project document | `Project` (`projects.py`): section tree, ordered memberships; own operations through `projects.apply`; written conditionally on its revision by `ProjectService` (ADR-0051/0052). One item per project in Cosmos (`project` in partition `project:<id>`), `projects/<id>.json` (file), table `projects` (sqlite). | ADR-0051 already reserved the project document as "the natural home for a shared model". |
| Cross-document invariants | "Every diagram in exactly one project" is kept by ordered writes and the idempotent `reconcile()` repair (ADR-0052), not by transactions. | The same pattern (ordered writes plus a repair) is available for propagating renames into views. |
| Agent / project chat | `DiagramAgent` (one diagram, tools of its type), `ProjectAgent` (project tools, the active diagram's tools, write-through per diagram, one version per changed diagram in `finally`, ADR-0059). Prompts from `arcviz/prompts/` (ADR-0065). | Shared elements need read tools, link tools and a model rename that edits other diagrams mid-turn. |
| Project review | `project_review.py`: project rules over a `ProjectSnapshot` (project + member diagrams), aggregated diagram rules, one LLM call; `ProjectFinding` with `diagram`/`diagrams`/`section` (ADR-0062/0063: "M18 can add model-element references as further fields"). | Consistency rules become possible as soon as views reference elements. |
| Exchange | Diagram documents (`schema_version` 1 deployment / 2 others), project bundles (`arcviz-project` v1: project + embedded diagram documents, ADR-0056); import always creates new ids. | The bundle must carry the model; refs inside documents must survive the id remapping. |
| Comparison | `diff.py` compares two versions of one diagram by node/edge ids. | A changed link is a changed node. |
| Property tests | `tests/strategies.py` draws operations against the current diagram; `test_properties.py` checks integrity, rendering structure, exchange round trip, comparison. | New operations must be drawable. |

## Option space

### 1. Element identity across the three diagram types

What a "same element" can be differs by type:

- **Deployment** nodes are structural (services, databases, execution environments, containers such as
  vnet/cluster). Edges are communication paths or dependencies.
- **Activity** partitions (swimlanes) name *who* performs actions: an actor, a system or a component.
  Data store and central buffer nodes name *data*. Actions, control nodes and flows are behaviour of the
  one activity; they have no counterpart in other diagrams.
- **Sequence** lifelines represent participants (an actor, a service, a database). Messages are
  occurrences in one interaction.

So identity is shared by **participants/structural elements** (nodes, partitions, data nodes,
lifelines), not by behaviour (actions, messages, flows). Relations (a deployment edge that "is" the
connection several messages travel over) are a second, harder layer: one structural connection carries
many messages in both directions, the mapping is n:m and needs its own UI. This study keeps relations
**derived** (a consistency rule compares messages with deployment edges between the same elements) and
leaves explicit model relations as a follow-up.

A project element therefore needs: a stable id (slug, unique per project), a name, an abstract **kind**
(actor, system, subsystem, component, node, data), a description, and optionally a parent element
(a component inside a subsystem). Diagram kinds stay as they are; a small compatibility table says
which diagram kinds may represent which element kinds (a lifeline may represent anything, a partition
anything but data, a data store node only data, a vnet a node or system, …, ADR-0073).

### 2. How views reference model elements

| | (a) Optional `ref` on view elements | (b) Full model/view split |
|---|---|---|
| Shape | `Node.ref` / `Lifeline.ref` = project element id; everything else unchanged | Diagrams hold only placements (`{element, parent, position}`), names/kinds live in the model |
| Existing data | Untouched; unset `ref` is omitted from the stored JSON, so documents, fingerprints and exports stay byte-identical until something is linked | Every diagram must be converted (a data migration of every version, or a dual reader forever) |
| Rendering, exchange, PlantUML, comparison, templates, property tests | Unchanged: a diagram stays self-contained | Every renderer and exporter needs the project model next to the diagram; a diagram cannot be rendered, exported or diffed alone |
| Versions/undo | A link or a propagated rename is an ordinary versioned diagram change | Renames change the model, not the diagram: a diagram version no longer shows what it looked like |
| Unlinked elements | Natural (actions, messages, quick sketches) | Need a "local element" escape hatch anyway |
| Cost | Moderate (see effort) | A rewrite of model, operations, rendering, exchange, tools, prompts and tests |

**(a) is sound**: the only thing (b) gives beyond (a) is a single storage place for the name, and (a)
recovers that by propagation (next section). (b) would invalidate ADR-0003 (self-contained snapshots),
ADR-0018 (self-contained exchange documents) and ADR-0019 (comparison of two snapshots) and require a
migration of all existing data. Recommendation: (a), ADR-0070.

### 3. Rename and describe propagation

With (a), a view element keeps its own `name` (needed for self-contained rendering). Three choices:

1. **Resolve at render time** (name from the model when `ref` is set): every renderer, exporter and the
   comparison need the model; old versions would render with today's name. Rejected for the same
   reasons as (b).
2. **Denormalise and propagate**: the name in the model is authoritative; renaming an element writes
   the project (conditional) and then every member diagram that references it through an ordinary
   operation (`sync_element`, one version per diagram labelled "Element … umbenannt"). Diagrams render,
   export and diff unchanged; history shows the rename. Not atomic across documents: a crash or a
   concurrent link can leave a view with the old name (*drift*); a review rule reports drift and
   `POST …/model/sync` (and the review's fix) repairs it, idempotently — the same "ordered writes plus
   repair" pattern as ADR-0052.
3. **No propagation**, only a drift rule: cheap, but misses the point ("rename once").

Recommendation: 2 (ADR-0071). To keep the invariant "a linked view element carries its element's name",
renaming a **linked** view element through `update_node`/`update_lifeline` is refused with a message that
points to the model rename (the UI and the agent rename through the model instead). **Descriptions**
live only in the model: they are not drawn, so nothing needs to propagate; the inspector, the prompts and
the review show the element's description wherever a view references it.

### 4. Deletion semantics

- **Delete from a view** (`remove_node`, `remove_lifeline`): removes the view element only; the model
  element stays (it may be used elsewhere, and an unused element is reported as a low suggestion).
- **Delete from the model**: unlinks every view element first (versioned `clear_element` per diagram; the
  view elements stay as plain, unlinked elements), then removes the element (children move to its parent,
  linked sections lose the link). Never deletes view content — that would be a surprising cross-diagram
  delete. A crash in between leaves an element that nothing references (harmless, reported as unused).
- **Dangling refs** (the element was deleted by a crashed/racing request, a diagram was moved to another
  project, a single diagram was imported into another project) are tolerated everywhere: rendering
  ignores refs, the review reports `dangling-element-ref`, unlinking clears it. ADR-0072.

### 5. Subsystems as model elements

Sections (M16) already group diagrams by subsystem in practice. Two ways to make that explicit:
(i) turn sections into elements (a migration of every project's sections and the navigator), or
(ii) let a section **reference** an element (`Section.element`, normally of kind `subsystem` or
`system`), with the section title following the element's name like any other view. (ii) keeps
sections working unchanged and gives the review a precise basis ("this section describes subsystem X",
ADR-0063 anticipated this). Elements can also nest (`Element.parent`), so components belong to a
subsystem. Recommendation: (ii), ADR-0074.

### 6. Consistency rules

With refs, the project review can check what only the whole project shows (ADR-0075):

| Rule | Finding |
|---|---|
| `dangling-element-ref` | a view element references an element the project does not have (risk, medium) |
| `element-name-drift` | a linked view element or section shows another name than its element (risk, low; fix: sync) |
| `incompatible-element-link` | a linked view element's kind cannot represent its element's kind any more (risk, medium) |
| `unused-element` | an element that no view and no section references (suggestion, low) |
| `element-not-in-structure` | an element shown in behaviour (lifeline/partition) but in no deployment diagram, in a project that has deployment diagrams with linked elements (suggestion, low) |
| `message-without-connection` | a sequence message between lifelines of two elements that both appear in deployment diagrams, while no deployment edge connects them (or anything inside them) (risk, medium) |
| `unlinked-namesake` | an unlinked view element (of a linkable kind) whose name equals an element's name: probably the same thing (suggestion, low) |

### 7. Effect on the other parts

| Part | Change |
|---|---|
| Domain | `project_model.py` (pure): `Element`, element kinds, compatibility table, ref queries over a diagram. `Project.elements`, `Section.element`. `Node.ref`, `Lifeline.ref` (omitted from dumps while unset). |
| Operations | Diagram operations `link_element`, `unlink_element`, `sync_element`, `clear_element` (all types); `update_node`/`update_lifeline` refuse renaming a linked element. The `link`/`sync`/`clear` operations carry the element's name and kind as arguments and are **internal**: callable by the model service only, not by REST `/ops`, `apply_batch`, the LLM or proposals (the caller would otherwise choose the name). `unlink_element` is public. Project operations `add_element`, `update_element`, `remove_element`, `link_section` (internal: the model service drives propagation). |
| Services | `ModelService` (`services/model.py`): CRUD of elements with propagation, link/unlink/promote, usages, sync (repair). Project read-modify-write stays in `ProjectService`, diagram writes in `DiagramService`; no store change. |
| HTTP | `GET/POST /api/projects/{pid}/elements`, `PATCH/DELETE …/elements/{eid}`, `POST …/model/sync`, `POST /api/diagrams/{id}/link` / `unlink` / `promote`, `PATCH …/sections/{sid}` with `element`. Same auth and rate-limit classes as the other project writes. |
| Agent / project chat | Tools `list_elements`, `create_element`, `update_element`, `link_element`, `unlink_element` (shared block in `arcviz/prompts/`); the project's elements are listed in the prompt (budgeted); the diagram summary marks linked elements. Writing element tools are refused in propose mode (a proposal changes one diagram only, ADR-0060). After a propagating rename the project chat re-reads the diagrams it holds. ADR-0076. |
| Review | Rules above; `ProjectFinding.model_element`; the LLM digest lists the model. |
| Exchange | Project bundle `schema_version` 2 when the project has elements (else still 1, so old exports stay identical); refs travel inside the diagram documents (element ids are project-scoped, so the id remapping of diagrams does not touch them). Single-diagram documents keep refs (they dangle until linked again). ADR-0077. |
| Comparison | A changed `ref` is a changed node/lifeline (already covered by "attributes differ"). |
| Property tests | Strategies draw `link_element`/`unlink_element`/`sync_element` against a synthetic element catalogue; invariants: integrity, linked names equal, unset refs absent from dumps. |
| UI | Navigator: a "Modell" panel per project (elements, kinds, usages; add, rename, describe, delete, sync). Inspector: the linked element (name, kind, description), link/unlink, "Als Projektelement anlegen". |

### 8. Migration of existing data

- **Diagram documents and versions** do not change: `ref` is optional and omitted while unset, so a
  v0.1.7 document loads, renders, fingerprints and exports exactly as before. Fingerprints stored in old
  versions stay comparable with fingerprints computed now — this matters for undo/redo and for the
  "content changed" check of a turn.
- **Project documents** get `elements` (default empty) and `Section.element` (default none) through
  pydantic defaults; a v0.1.7 project document loads unchanged and is rewritten with the new fields only
  on its next write. Older code reading a newer project ignores the extra fields (pydantic's default).
- Nothing to run: no `arcviz-migrate` step. The milestone adds compatibility tests with stores written
  by the v0.1.7 code (file, sqlite and the Cosmos fake's items) that must load, render, undo and accept new
  links.

### 9. Storage and partitioning

The model lives **in the project document**: it is read and written with the project's revision, so
element operations are conditional writes like every other project write (ADR-0044), and Cosmos keeps one
item per project in its own partition. Propagation touches each referencing diagram in its own partition
— exactly what a rename does by hand today, and what the project chat already does (ADR-0059: no
cross-diagram atomicity). Repartitioning the container by `/project_id` would buy single-partition
transactional batches across a project's diagrams, but:

- diagram ids are global and moves between projects would then **move items between partitions**
  (Cosmos cannot change an item's partition key: delete + create of every version and transcript entry);
- it needs a new container and a copy of every item (`arcviz-migrate`), downtime or dual writes;
- propagation does not need atomicity: the repair handles partial writes, as for memberships.

So **no repartitioning** (ADR-0078); risk R16 stays, re-evaluated if per-project transactions become a
requirement. File and sqlite stay as they are. Size: a Cosmos item is limited to 2 MB; the model is capped
at `MAX_ELEMENTS = 500` elements per project (about 0.2 MB with long descriptions), enforced by the
operation.

#143 (conditional appends/clears bound to a generation) is unrelated to this design: no new store
primitive is introduced, so it stays open.

### 10. Effort

| Part | Estimate |
|---|---|
| Domain, operations, project operations, model service, HTTP | 2 days |
| Rename/delete propagation and sync repair | 1 day |
| Agent and project chat tools, prompts | 1 day |
| Review rules | 1 day |
| Exchange | 0.5 day |
| UI (model panel, inspector) and e2e | 1.5 days |
| Compatibility fixtures from v0.1.7, property tests, docs | 1.5 days |

About 8–9 days, no data migration. Option (b) would be several weeks plus a migration of every stored
version.

## Recommendation

Implement the incremental option: **project elements in the project document, optional `ref` links from
view elements (deployment and activity nodes, sequence lifelines) and from sections, names denormalised
and propagated through versioned diagram operations, drift detected by the review and repaired by sync;
no storage changes.** Explicit model relations (edges/messages as instances of a model relation) and
carrying elements along when a diagram moves to another project are follow-ups.

## Incremental plan (issues of M18)

1. `docs(architecture)`: this study, the design spec and ADR-0070..0078 (#137).
2. #144 `test(store)`: stores written by v0.1.7 load and work unchanged (fixtures + tests).
3. #145 `feat(model)`: project elements and section links (domain, project operations, `ModelService`, HTTP).
4. #146 `feat(model)`: link view elements of all three diagram types (`ref`, diagram operations, exchange
   documents, comparison, property tests).
5. #147 `feat(model)`: rename/describe propagation, deletion semantics and the sync repair.
6. #148 `feat(agent)`: shared elements in the diagram chat and the project chat.
7. #149 `feat(review)`: cross-diagram consistency rules.
8. #150 `feat(exchange)`: project bundles carry the model.
9. #151 `feat(ui)`: model panel in the navigator and linking in the inspector.

Follow-ups: model relations; elements following a diagram moved to another project.
