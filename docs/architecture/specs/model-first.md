# Model first: diagrams are views of the project model

- Status: First stage implemented (see [As implemented](#as-implemented)); change sets (ADR-0218) and separate model
  documents (ADR-0214) are follow-ups
- Issues: to be opened per phase (see [Phases](#phases))
- ADRs: [0213 The project model is the source of truth; diagrams are views](../adr/0213-model-is-the-source-of-truth.md),
  [0214 One structure document and one document per behaviour, versioned like diagrams](../adr/0214-model-documents.md),
  [0215 A view resolves to today's `Diagram` as a read-only projection](../adr/0215-views-resolve-to-a-projection.md),
  [0216 Relations among shown elements are shown unless hidden](../adr/0216-relations-shown-by-default.md),
  [0217 View operations keep the tool vocabulary; a removal names its scope](../adr/0217-view-operations-and-removal-scope.md),
  [0218 Change sets make one edit across documents undoable](../adr/0218-change-sets.md),
  [0219 Agents write the model directly or through proposals, per project](../adr/0219-agent-model-writes-per-project.md),
  [0220 Existing diagrams are migrated into the model at start-up](../adr/0220-migration-into-the-model.md)
- Supersedes: [ADR-0070](../adr/0070-shared-model-elements-in-the-project-document.md) (optional references),
  [ADR-0071](../adr/0071-names-propagated-into-views-drift-repaired.md) (propagation and drift repair),
  [ADR-0138](../adr/0138-views-primary-findings-resolved-by-people.md) (views stay primary),
  [ADR-0164](../adr/0164-structural-views-become-project-elements.md) (adoption at creation);
  amends [ADR-0137](../adr/0137-open-world-completeness-and-hidden-relations.md) (completeness is dropped, hiding
  stays) and [ADR-0172](../adr/0172-agent-changes-are-stored-proposals.md) (direct writes become an owner's choice)
- Replaces in part: [Shared project model](shared-project-model.md), [Model relations](model-relations.md),
  [Elements by default](elements-by-default.md); touches nearly every other spec (see [Impact](#impact))

## Summary

The project model becomes the single source of truth. Every element, feature, relation and behaviour exists exactly
once, in the project's model; a diagram is a **view** that stores only *what* it shows (ids of model elements, or the
behaviour it shows) and *how* (direction, hidden items, display options). Names, kinds, properties, descriptions,
members, ports and relations are never stored in a diagram. A rename, a new relation, a changed protocol shows in
every view at once because there is nothing to propagate.

All seven diagram types become views, including the behaviour types: the model gains **behaviours** (activities,
state machines, interactions), each its own model document, which an activity, state machine or sequence diagram
shows. Lifelines, partitions and other behaviour parts that stand for a structural thing refer to model elements.

Removing something in a view asks each time whether to hide it in this view or delete it from the model. The chat
assistant only hides and proposes model deletions. Whether MCP agents write the model directly or only through stored
proposals is the project owner's choice, off (proposals) by default.

Rendering, rules, export and comparison keep working on today's `Diagram` object: a view is **resolved** against the
model into a read-only `Diagram` projection. That keeps the renderers, the rule set and the PlantUML/Mermaid writers
largely unchanged and confines the rewrite to the model, the operations, persistence, history and the agents.

## Goals

1. One source of truth: no diagram stores a name, kind, property, description, member, port or relation. Drift
   between views is impossible by construction; the drift repair (`ModelService.sync`, `sync_element`,
   `sync_relation`) and the correspondence rules that only exist because of drift are removed.
2. Every diagram type is a view over the model, behaviour included.
3. Editing a diagram edits the model. The existing operation and tool vocabulary (`add_node`, `add_edge`,
   `update_node`, `add_message`, …) stays, with model semantics, so prompts, evals and the UI keep their verbs.
4. A model element can be shown in any number of views; adding an existing element to a view is a first-class act
   (UI: **Vorhandenes Element zeigen…**; tool `show_element`).
5. Removal in a view always names its scope: `view` (hide) or `model` (delete everywhere). The UI asks each time and
   says in how many other views the item appears.
6. An edit that touches several documents (model, behaviour, view) is one undoable step for the person who made it.
7. MCP agents can create and change elements, features, relations, behaviours and views: directly when the project
   owner allows it, otherwise as stored proposals. No agent deletes from the model directly.
8. Existing data (every version back to v0.1.7) is migrated without loss of content; what cannot be merged
   losslessly is reported, never silently dropped.

## Non-goals

- Manual layout. Views keep automatic layout (Mermaid, own SVG renderers); positions are still not stored.
- Packages and namespaces as a model hierarchy beyond `Element.parent`; profiles and stereotypes beyond the kind
  catalogue.
- Cross-project models or element reuse between projects (`carry_model` on a move stays, ADR-0161).
- Messages that call class operations (`Message.operation`) and activity actions that call behaviours
  (`CallBehaviorAction`) as typed references: follow-ups once behaviours exist.
- Multi-document atomic transactions. Change sets are ordered writes with tolerant readers (ADR-0218).

## Design

### Model content

#### Structure (`arcviz/uml/structure.py`, replaces the model part of `project_model.py`)

```python
class Feature(BaseModel):  # attribute, operation, port, literal, … per the kind's feature types
    id: str  # unique within its element
    type: str  # one of the kind's FEATURE_TYPES
    name: str
    text: str = ""  # the typed signature as written today in class/component `fields`


class Element(BaseModel):
    id: str  # slug, unique in the project (unchanged ids from M18)
    name: str
    kind: str  # a model kind of the active catalogue (see below)
    parent: str | None = None  # ownership / containment
    description: str = ""  # replaces both Element.description and the per-diagram notes
    properties: dict[str, str] = {}
    features: list[Feature] = []  # omitted while empty
    code_refs: list[CodeRef] = []  # unchanged (ADR-0171)


class Relation(BaseModel):
    id: str  # r<n>, never reused (Structure.relation_seq)
    kind: str  # a relation kind of the catalogue
    source: str  # element id (or "<element>.<feature>" for a port end)
    target: str
    label: str = ""
    direction: str | None = None  # forward | both | none (today's Edge.direction)
    properties: dict[str, str] = {}
    description: str = ""


class Structure(BaseModel):  # the structure document of a project
    elements: list[Element]
    relations: list[Relation]
    relation_seq: int = 0
    behaviours: list[BehaviourInfo]  # index: id, kind, name, context element; the content is its own document
```

**One kind catalogue for the whole model** (`arcviz/uml/kinds.py`). Today each diagram type has its own vocabulary
(`model.kinds_for(type)`, `graph_types.GraphProfile`), with names shared by several types listed in `SHARED_KINDS`.
The model needs one catalogue in which a name means one thing: every kind gets a **metaclass** (`node`, `artifact`,
`component`, `interface`, `class`, `datatype`, `enumeration`, `actor`, `use_case`, `subject`, `package`, `group`) and
the feature types it may own. The M18 abstract `ELEMENT_KINDS` (`actor`, `system`, `subsystem`, `component`, `node`,
`data`) become a derived **category** (`kinds.category_of(kind)`) so drivers, rules and the MCP filter keep working.
The configurable deployment kinds (`ARCVIZ_KINDS_FILE`, kind packs) extend the catalogue as today; their `role`
(ADR-0169) names the metaclass. A diagram type lists which metaclasses and relation kinds its views can show
(`views.SHOWABLE[type]`), replacing `project_model.linkable_kinds`.

Relation kinds are the union of today's edge kinds and model relation kinds (`connection`, `dependency`, `deploys`,
`association`, `aggregation`, `composition`, `generalization`, `realization`, `usage`, `include`, `extend`,
`replication`, `communicates`, `deployed_on`, `data_flow`), with the edge rules of the graph profiles becoming
relation rules on metaclasses (`graph_types` edge rules move to `uml/rules.py`). Today's M25 kinds stay as kinds;
`connection` and `communicates` are **not** merged automatically (open question 5).

Bounds: the structure document gets the project document's old byte budget (`MAX_PROJECT_BYTES` 1.9 MB) to itself.
`MAX_ELEMENTS` rises from 500 to 1000, `MAX_RELATIONS` from 500 to 2000, `MAX_FEATURES` 50 per element; the size
arithmetic (as in the model-relations spec) is part of phase 1 and decides the final numbers.

#### Behaviours (`arcviz/uml/behaviour.py`)

A behaviour is one model document: `Activity` (today's activity nodes, edges, partitions), `StateMachine` (states,
regions, transitions) or `Interaction` (today's `sequence.Interaction`: lifelines, fragments, executions). Its
content keeps today's shape and rules (`model.activity_edge_problem`, `state_machine.py`, `sequence.integrity_errors`),
with three changes:

- Every part that stands for a structural thing refers to a model element instead of carrying its own name:
  `Lifeline.represents` (required, replaces `name`/`type`/`actor`/`ref`), activity partition `represents`
  (required), state machine `context` (optional, on the behaviour), activity object nodes/data stores `type`
  (optional element id).
- `Message.relation` (optional, replaces `Message.ref`) names the relation it travels on.
- A behaviour has a `context` element (optional): the classifier it belongs to; `get_component` lists it.

Names of behaviour-local parts (actions, states, messages, guards) live in the behaviour document; they are model
content too, shown identically in every view of the behaviour.

### Views (`arcviz/views.py`, replaces the content of `model.Diagram`)

```python
class View(BaseModel):
    id: str
    title: str
    type: str  # DIAGRAM_TYPES, immutable
    behaviour: str | None = None  # behaviour types: the behaviour shown (required for them, None otherwise)
    elements: list[str] = []  # structure types: the element ids shown, in display order
    hidden: list[str] = []  # ids not drawn although they would be: relations among shown elements,
    # behaviour parts, features
    options: ViewOptions  # direction (today's Diagram.direction), show_features, nest_deployed (bool)
    created_at: str
    updated_at: str
```

- **Structure views** show the listed elements. Nesting follows `Element.parent` when the parent is shown; in a
  deployment view a single shown `deployed_on` target nests the element too (`options.nest_deployed`, default on).
- **Relations are derived** (ADR-0216): every relation of a kind the view type can show whose two ends are shown is
  drawn, unless its id is in `hidden`. A new relation therefore appears in every view that shows both ends.
  `Diagram.complete` is dropped: a view can no longer be incomplete except through `hidden`.
- **Behaviour views** show the whole behaviour minus `hidden` parts.
- Dangling ids (an element deleted while a view was being written, ADR-0218) are ignored when resolving and removed
  by the next write of the view.

#### Resolution (`views.resolve(view, structure, behaviour) -> Diagram`)

`resolve` builds today's `model.Diagram` (nodes, edges, `interaction`) from the view and the model documents, with
`Node.id` = element id, `Edge.id` = relation id, and the behaviour's parts verbatim. The result is **read-only** (a
frozen copy, never saved; `save_latest` of a resolved diagram is a defect). Every consumer that reads a diagram today
— `render.payload`, `mermaid`, `sequence_svg`, `rules.run_checks`, `exchange` writers, `diff.compare`, `Diagram.summary`
for the prompt — reads the projection and stays unchanged except for the id-shape assumptions listed in
[Impact](#impact). The projection is memoised per (view revision, structure revision, behaviour revision).

### Operations

Rule 2 of CLAUDE.md stays: every mutation goes through one `apply`, now in three layers.

1. **Model operations** (`arcviz/uml/operations.py`): `add_element`, `update_element`, `remove_element`,
   `add_feature`, `update_feature`, `remove_feature`, `add_relation`, `update_relation`, `remove_relation`,
   `add_behaviour`, `rename_behaviour`, `remove_behaviour`, plus today's behaviour content operations (activity
   node/edge operations, `sequence_ops`, state machine operations) applied to a behaviour document. Same contract as
   `operations.apply`: signature check, deep copy, restore on any exception, integrity asserted, messages written
   for the LLM.
2. **View operations** (`arcviz/views.py`): `show_element`, `hide`, `unhide`, `set_view_options`, `set_title`.
3. **Diagram operations** (`arcviz/operations.py`, same names as today): each is a *compound* of 1 and 2 planned on
   the current view and model and returned as a list of steps per document. Examples:
   - `add_node(name, kind, parent?)` in a structure view: reuse an element of the same name and metaclass
     (`Adopter`'s matching moves here), else `add_element`; then `show_element`.
   - `add_edge(source, target, kind, label?)`: `add_relation` (or reuse an identical one), nothing to show
     (derived).
   - `update_node(node, name=…)`: `update_element` — the rename shows everywhere; the refusal "renaming a linked
     element" (ADR-0071) is gone.
   - `remove_node(node, scope)` / `remove_edge(edge, scope)` / `remove_message(…, scope)`: `scope` is required,
     `view` → `hide` (structure: drop from `elements`), `model` → `remove_element`/`remove_relation` (cascades:
     relations of the element, features, references in behaviours become refusals, see below).
   - behaviour views: `add_node`, `add_message`, … are behaviour operations on the shown behaviour; `add_lifeline`
     resolves or creates the represented element.
   - `apply_batch` stays and may mix all three layers.

   `link_element`, `unlink_element`, `sync_element`, `clear_element`, `link_relation`, `sync_relation`,
   `clear_relation`, `set_relation_hidden`, `set_completeness` are removed (there is nothing to link or sync).

`remove_element` refuses an element that a behaviour part represents (a lifeline, a partition) and names those
behaviours; a person removes the parts first, or the UI offers to. Drivers, decisions, requirements that name the
element keep the dangling id as today (ADR-0072, tolerated on read).

### Persistence (ADR-0214)

- The structure is one versioned document per project, stored through the existing diagram primitives
  (`create_latest`, `commit_latest`, versions, fingerprints, Git mirror) under the reserved id `model_<32 hex>` derived
  from the project id (like `home_`), so history, undo, retention and the Cosmos partition-per-document layout are
  reused. It is never listed as a diagram (`diagram_infos` and the navigator skip the prefix).
- Each behaviour is a versioned document `bhv_<10 base32>` stored the same way, placed in no section.
- A view is the diagram document it replaces (same id, same partition, same versions), its content now a `View`.
  `Membership`, sections, the locator (ADR-0120) and access checks are unchanged: a view is a diagram for every
  service that does not look inside it.
- `Project.elements`, `Project.relations`, `relation_seq` move out of the project document into the structure
  document; the project document keeps sections, memberships, drivers, decisions, requirements links, vision.
- Access: model documents inherit the project's roles (`Guard.project`); there is no separate share.

### Change sets, history and undo (ADR-0218)

An edit is planned as steps per document and written in a fixed order: **structure → behaviour → view**. Every
version it writes carries the same `changeset` (`cs_<random>`), the `context` (the view it was made in, or `model`),
the author and the ids it touched. Readers tolerate a partially written change set (a view may name an element not yet
created: ignored; a model change without its view step: the element simply is not shown yet).

- **View history** (versions panel, comparison): the change sets that touched the view itself or an id the view
  showed at that time, read from the version metadata of the view, its behaviour and the structure document
  (`history()` already lists metadata without snapshots). A view "version" is a change set; its projection is
  resolved from the documents at those versions.
- **Undo/redo in a view** reverts the newest change set made **in that view** (its `context`) by writing, per
  document, the previous content of the touched items as a new version (history stays linear, ADR-0003). When a
  touched item changed since (another change set wrote it), undo answers 409 and names what changed; there is no
  silent overwrite of someone else's work. The undo/redo stack rules of `history.py` apply to change sets.
- **Model history**: the structure document's own versions panel in the **Modell** view lists all change sets.
- Retention (`ARCVIZ_VERSIONS_KEEP`) applies per document as today; a change set whose versions were pruned in one
  document is `VersionGone` (409) for undo.

### Live updates

A change set publishes one hint per document it wrote. A diagram's live stream subscribes to its view, its behaviour
and the structure document and re-resolves on any of them; the structure hint carries the touched ids so a stream
whose view shows none of them sends nothing. Presence is unchanged.

### Agents

- **Chat assistants** (`DiagramAgent`, `ProjectAgent`): the same diagram tools with model semantics; `remove_*` tools
  only accept `scope: "view"`, and a model deletion is offered as a proposal (`propose_model_change` with
  `remove_element`/`remove_relation`). New tools `show_element`, `list_elements` (exists), `create_behaviour`. The
  prompt shows the model part first (elements, relations, behaviours, budgeted) and the active view as the
  projection's summary. Propose mode works as today on copies of all touched documents.
- **MCP** (ADR-0219): new tools `create_element`, `update_element`, `add_feature`, `update_feature`,
  `create_relation`, `update_relation`, `create_behaviour`, `edit_behaviour` (behaviour operations), `create_view`,
  `show_in_view`, `hide_in_view`. With `Project.agent_writes == "direct"` they write (editor role required, versions
  and change sets recorded with the token's user and client `mcp`); with `"propose"` (default) each call stores a
  project proposal item (the M31 project proposal, extended with model and view operations) that a person applies.
  No MCP tool removes from the model; hide in a view is allowed in both modes. The owner sets the mode with
  `PUT /api/projects/{pid}/agent-writes` (`{"mode": "direct"|"propose"}`), shown in the project settings.
- The steward (ADR-0204) proposes model operations as today; its correspondence candidates disappear with drift.

### HTTP API

- `GET /api/projects/{pid}/model` (structure, revision as ETag), `POST /api/projects/{pid}/model/ops` (model
  operations, `If-Match`), `GET /api/projects/{pid}/behaviours[/{bid}]`.
- `GET /api/diagrams/{id}` answers the projection (as today) plus `view` (the stored view) and the revisions of all
  three documents; `ETag` becomes the view's revision, `If-Match` on `/ops` checks the view and the API returns 412
  with the document that moved when the structure or behaviour changed under a conditional request.
- `POST /api/diagrams/{id}/ops`: diagram operations as today, `remove_*` require `scope`.
- Removed: `/link-relation`, `/unlink-relation`, `/hidden-relations` (→ `hide`/`unhide` ops), the element
  link/unlink routes, `/api/projects/{pid}/adoption`.
- `PUT /api/projects/{pid}/agent-writes`.

### UI

- Removing anything in a diagram opens a dialog: **Nur in dieser Ansicht ausblenden** / **Aus dem Modell löschen**,
  with "auch in N weiteren Ansichten sichtbar: …" (from `GET /api/projects/{pid}/model/usages/{id}`). No default
  button for the model deletion; keyboard Delete opens the dialog.
- The **Modell** panel becomes the primary editor of elements, features and relations; each item lists the views that
  show it and offers **In Ansicht zeigen**.
- **Vorhandenes Element zeigen…** in every structure view (palette entry, ⋯ menu): pick elements, optionally with
  their relations' other ends.
- The inspector edits the model element (no separate "Projektelement" section); hidden relations are listed per view
  with **Einblenden**.
- The **Vollständigkeit** menu, link/unlink buttons and the "Unverknüpfte übernehmen…" action are removed.

### Review and rules

- Diagram rules run on the projection, unchanged.
- Project rules: `relation-not-shown`, `connection-without-relation`, `complete-view-missing-relation`,
  `relation-property-conflict`, `invalid-relation-link`, `similar-element-names` (as a duplicate detector it stays),
  `containment-conflict` are removed or rewritten as model rules; new model rules: `element-not-shown` (an element no
  view shows), `behaviour-not-shown`, `relation-violates-kind-rule` (from the graph profiles).

### Exchange

- Diagram export (`arcviz-diagram`): the projection plus the referenced model subset (as ADR-0079 does today),
  `schema_version` 3 for every type. Import creates elements in the target project's model (reuse by name and
  metaclass) and a new view; older schema versions are converted with the migration's code.
- Project bundles (`arcviz-project`, `schema_version` 10): structure, behaviours, views, everything else as today;
  versions 1–9 are converted on import.
- PlantUML/Mermaid exports are written from the projection, unchanged.

## Migration and compatibility (ADR-0220)

Run once at start-up per project (best effort like ADR-0103, never stops the start; repeatable with
`arcviz-migrate --model [--project P]`), and on import of an old bundle or diagram:

1. Create the structure document from `Project.elements`/`relations`.
2. For every structural diagram, map each node to an element: its `ref` if set; else an element with the same name
   (case-insensitive) and the same metaclass; else a new element. Node `properties` and notes (`description`) merge
   into the element; on a conflicting value the most recently updated diagram wins and the other values are kept in
   the migration report. Edges become relations: their `ref` if set, else an existing relation with the same kind,
   ends and label, else a new one.
3. Every behaviour diagram becomes one behaviour document with its content; lifelines and partitions get a
   `represents` element by the same rule as nodes. The diagram becomes a view of it.
4. Every diagram document is rewritten as a `View` (a new version `source: "migration"`); `hidden_relations` move to
   `hidden`; relations that the old diagram did *not* draw between two shown elements are added to `hidden`, so no
   view shows more than before (ADR-0216 applies to new relations only).
5. The migration report (keyed document `migration`, one per project, shown in the **Modell** view until dismissed)
   lists merged elements, conflicting values and anything that could not be mapped.

Versions written before the migration stay readable as **legacy snapshots**: the versions panel shows them, renders
them as stored (they still hold a full `Diagram`) and compares them with each other; restoring one runs the import
path (step 2–4 for that one diagram) as a new change set. Undo never crosses the migration version.

Compatibility tests: `tests/test_compat_v017.py`, `test_compat_v018.py`, `test_compat_m20.py`, `test_compat_v040.py`
migrate their fixtures and assert the projections equal the stored diagrams (same nodes, names, kinds, edges, labels,
modulo ids of merged nodes, which the report lists).

## Phases

One milestone, one branch `milestone/m32-model-first`, one Conventional Commit per issue, one PR (`feat!:`).

| # | Issue | Content |
|---|---|---|
| 1 | `feat(model)!: one kind catalogue and the structure document` | `uml/kinds.py`, `uml/structure.py`, model operations, structure document in the store, `ModelService` rewritten; size arithmetic |
| 2 | `feat(model): behaviours as model documents` | `uml/behaviour.py`, behaviour documents and operations for activity, state machine, interaction |
| 3 | `feat(views)!: diagrams are views resolved against the model` | `views.py`, `resolve`, diagram documents hold `View`s, renderers fed the projection |
| 4 | `feat(ops)!: diagram operations as compounds with removal scope` | compound planning, `scope`, removal of link/sync operations, REST changes |
| 5 | `feat(history): change sets, view history, undo and redo` | change set metadata, history merge, undo/redo semantics, comparison |
| 6 | `feat(live): views follow model changes` | live streams over three documents |
| 7 | `feat(migrate)!: existing diagrams into the model` | start-up migration, report, legacy snapshots, exchange schema versions, compat tests |
| 8 | `feat(agent): assistants edit the model` | tools, prompts, evals, propose mode |
| 9 | `feat(mcp): model tools and per-project agent writes` | MCP tools, `agent_writes`, proposals with model/view items |
| 10 | `feat(ui): model-first editing` | removal dialog, show existing element, model panel, inspector |
| 11 | `refactor(review): model rules replace correspondence rules` | rules, steward, arc42 figures |
| 12 | `docs: model first` | `docs/spec/`, guide, CLAUDE.md, arc42 section 4/8/9 |

Phases 1–7 must land together before a release (the store format changes); 8–12 may follow in the same milestone.

## Impact

Modules that change substantially: `model.py` (Diagram becomes the projection type), `operations.py`,
`sequence_ops.py`, `project_model.py`, `projects.py`, `services/model.py`, `services/diagrams.py`,
`services/adoption.py` (removed), `services/projects.py`, `agent.py`, `agent_model.py`, `agent_types.py`,
`project_agent.py`, `agent_tools.py`, `mcp_server.py`, `proposal.py`, `proposals.py`, `exchange.py`, `templates.py`,
`diff.py`, `history.py`, `live.py`, `project_review.py`, `steward.py`, `static/inspector.js`, `static/modelpanel.js`,
`static/sharedmodel.js`, `static/relations.js`, `static/versions.js`. Assumptions to check while resolving: node ids
are diagram-unique slugs today and become project-unique element ids (Mermaid `id_map` already de-duplicates);
edge ids `e<n>` become relation ids `r<n>` (`findings.edgeSvgKeys` keys on Mermaid's `L_<from>_<to>_<n>`, not on ids).

The property tests (`tests/strategies.py`) draw model and view operations and assert: every view resolves without
integrity errors, a rename is visible in every view showing the element, hide never changes the model, model removal
never leaves a dangling id in a projection.

## Testing

- Unit: kind catalogue (every built-in kind of every type has exactly one metaclass, no name with two meanings),
  model operations (contract of `apply`), resolution (table per type), compound planning (one test per diagram
  operation), removal scopes, change set ordering and tolerant reads (crash between documents simulated), undo with
  and without intervening change sets (409).
- Store contract: structure and behaviour documents on all backends; reserved ids never listed.
- Server: every new/changed route in `tests/test_access_matrix.py`; 412 semantics over three documents.
- Agent: `ScriptedProvider` turns that add, rename and remove (scope view only) across two views of one element.
- MCP: both `agent_writes` modes; no removal tool exists (`tests/test_agent_tools.py`).
- Migration: the compat fixtures above plus a fixture with conflicting duplicates across diagrams.
- e2e: rename in one diagram shows in a second open diagram (two contexts); the removal dialog; show existing element.
- Evals: the existing cases rerun; new cases "reuse the existing element", "remove only from this view".

## As implemented

The first stage keeps the model where it is and puts the whole split into one place, the repository:

- **`arcviz/views.py`** (pure): `resolve(stored, project)` and `decompose(edited, previous, project)` as designed for
  structure views. Elements gained `notation` (diagram type → view kind) and `properties`; view connections are the
  new `Project.connections` (`project_model.Connection`: kind, ends, label, direction, properties, optional M25
  `relation`), ids `e<n>` never reused (`connection_seq`). Element descriptions hold the notes (up to 2000 characters);
  `MAX_ELEMENTS` is 1000; `ELEMENT_KINDS` gained `interface`, `use_case` and `package`.
- **`arcviz/store/modelbacked.py`**: `ModelBackedStore` wraps every backend (`create_store`, also `create_store(backend=)`
  for a given one). Reads resolve against the project that holds the diagram (locator entry, else the placement record
  of a create in flight); writes decompose, commit the project first (conditional) and then the view; version snapshots
  are replaced by the resolved diagram. The revision is `<backend revision>~<fingerprint of the resolved diagram>`, so
  a model change another view made changes the revision of every view that shows it (live polling, `If-Match`).
  Every service, agent, route and the MCP path go through it unchanged; diagrams outside every project pass through.
- **Behaviour views** keep their content in their diagram document (one view per behaviour): lifelines, partitions
  and data stores always stand for elements (created on write, by name), and show the element's name and note.
- **Operations**: `remove_node`, `remove_edge`, `remove_lifeline` take `scope` (`view`, the default, or `model`);
  `add_node` reuses the element of the same name that the view does not show; `add_edge` shows a hidden connection
  again instead of duplicating it; new `show_element` (deployment, class, use case, component); renaming a view element
  renames the element (the ADR-0071 refusal is gone). The adopter (ADR-0164) is switched off: the repository makes every
  view element an element.
- **Chat assistants** get `scope` on the removal tools and `show_element`; a removal with scope `model` is refused
  outside a preview turn (`agent.MODEL_REMOVAL_REFUSAL`).
- **MCP**: `create_component`, `update_component`, `create_relation`, `show_in_diagram`, `hide_in_diagram`, writing
  directly when `Project.agent_writes == "direct"` (owner: `PUT /api/projects/{pid}/agent-writes`), else storing
  proposals; no tool deletes from the model.
- **UI**: removing a node or edge of a structure diagram, a linked node, or a lifeline opens the dialog **Nur in
  dieser Ansicht ausblenden** / **Aus dem Modell löschen** (`#removal-dialog`, `inspector.askRemovalScope`).
- **Migration**: `services/model_first.migrate_to_model` at every start (best effort) writes each member diagram still
  stored as a full document once through the repository; the first write of any diagram converts it as well.

### Deviations from the design

1. **Storage (ADR-0214).** Elements and connections stay in the project document (guarded by `MAX_PROJECT_BYTES`);
   behaviours stay in the document of the diagram that shows them, so a behaviour has exactly one view. Separate
   structure and behaviour documents follow when the byte budget demands them.
2. **History (ADR-0218).** Change sets are not implemented. A version snapshot is the resolved diagram, so history,
   comparison and restore show what the view showed then; undo, redo and restore of a view write that older state
   back through the model, which also reverts later changes other views made to the elements this view shows.
3. **Removal scope** has the default `view` instead of being required: hiding is the safe default for every existing
   caller; the UI always asks, the chat may only hide.
4. **Element kinds.** No new unified kind catalogue yet: an element keeps its abstract kind and records its view kind
   per diagram type (`notation`); `views.view_kind` maps an abstract kind to a view kind for types it has no notation
   for yet.
5. Element names are unique per project, so a new view element merges with the element of the same name whatever
   its kind; an element is shown once per diagram (a second node of that name is refused). `add_element` with the name
   of an existing element returns that element (its kind unchanged).
6. **Linking.** `link_element` makes the view element that element (a structure node takes the element's id).
   Unlinking is refused for people and assistants in a project (`operations.unlink_refusal`; the inspector offers to
   point a node at another element instead); moves, imports and take-backs still unlink. A take-back of an invalid
   link restores the name the view had, so it stands for its previous element again.
7. **Removing an element** through the model (panel, REST, agents) is refused while a lifeline, partition or data store
   stands for it; structure views just stop showing it. A rename writes no diagram (nothing is propagated).
8. **Moving a diagram to another project** writes the view as a full document first (`Diagram._detach`), moves the
   membership, then takes it into the target's model (`DiagramService._into_model`): nothing it showed is lost, also
   when the target cannot take every element (it then stays a full document until the migration takes it). A move
   writes up to two more versions than before.
9. Deployment grouping containers (`deployment`, `vnet`, `subnet`, `region`, `group`) become `package` elements, never
   execution `node`s, so they do not count as `deployed_on` hosts.

## Decisions

Answered by the owner on 2026-11-10:

- Diagrams store only placements: which model items they show and view settings (no copies). → ADR-0213, 0215
- Removing in a view asks each time between hiding in the view and deleting from the model; the chat assistant hides
  and proposes model deletions. → ADR-0217
- All diagram types become views now, behaviour included. → ADR-0214
- MCP agents may write the model directly, switchable per project by the owner, default off (proposals). → ADR-0219

The owner asked to implement without answering the open questions; the recommendations were taken:

1. The agent-write switch governs MCP clients only; the chat writes directly and has preview turns.
2. Undo: deferred with change sets (see deviation 2).
3. Migration merges by name (element names are unique per project).
4. Relations (connections) among shown elements are derived and shown unless hidden. → ADR-0216
5. View connections and model relations stay separate kinds; a connection may name the relation it shows.
6. A breaking release (`feat!`).
