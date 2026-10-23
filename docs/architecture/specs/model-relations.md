# Model relations and cross-diagram consistency

- Status: Implemented (milestone M25)
- Issues: #203 (relations), #204 (links from view connections), #205 (completeness), #206 (correspondence rules),
  #207 (resolution through proposals), #208 (UI), #209 (agent)
- ADRs: [0135 Relations in the project document, sized by `MAX_RELATIONS`](../adr/0135-relations-in-the-project-document.md),
  [0136 A closed set of relation kinds; containment stays `Element.parent`](../adr/0136-relation-kinds-and-containment.md),
  [0137 Open world; completeness is declared per diagram, hiding is marked per view](../adr/0137-open-world-completeness-and-hidden-relations.md),
  [0138 Views stay primary; findings never pick a winner](../adr/0138-views-primary-findings-resolved-by-people.md),
  [0139 Deployment edges and sequence messages link to relations](../adr/0139-view-connections-link-to-relations.md),
  [0140 The agent links matching relations and proposes missing ones](../adr/0140-agent-links-relations-and-proposes-missing-ones.md),
  [0141 Correspondence rules; `message-without-connection` generalised](../adr/0141-correspondence-rules.md)
- Extends: [Shared project model](shared-project-model.md) (lifts its non-goal "model relations"); builds on
  [Project review](project-review.md), [Propose and confirm](propose-confirm.md),
  [Review suggestions](review-suggestions.md), [Project exchange](project-exchange.md)

## Summary

Since M18 a project shares **elements**; the connections between them exist only inside each diagram. M25 adds
**relations** to the project model: a small closed set of kinds (`communicates`, `deployed_on`, `data_flow`) between
two elements, with a few properties (protocol, port, direction, sync/async) and a description. A deployment edge or a
sequence message may **link** to a relation, the way a node or lifeline links to an element. A diagram may declare
itself **complete** for some relation kinds; only then does a relation it leaves out become a finding, unless the
diagram marks the relation **hidden**. New project rules find **gaps** (a relation no structural view shows, a view
connection with no relation behind it, a complete view missing a relation) and **contradictions** (properties that
differ between views or from the model, an element deployed on two nodes, containment that disagrees). Every such
finding names the ways to resolve it — adapt a view through a proposal, mark the relation hidden in that view, or
fix the model — and none is applied automatically. Stores, fingerprints and exports of data written before M25 stay
unchanged.

## Owner decisions (recorded in ADR-0137 and ADR-0138)

1. **Open world.** A view that leaves something out says nothing about it. Omission is a finding only where a diagram
   declares itself complete (#205).
2. **No automatic winner.** A finding is resolved by a person: through a proposal, by marking the relation hidden in
   that view, or by fixing the model.
3. **Views stay primary.** People and the agent edit diagrams; the model is reconciled with them. Views are never
   generated from the model, and the model never overwrites what a view draws (except the names of linked elements,
   ADR-0071, which M25 does not change).
4. **Small and closed.** Three relation kinds, no OWL/RDF tooling, no new runtime dependency; an export can come later.

## Goals

- Relations with identity in the project model, their kinds checked against the kinds of their ends.
- Deployment edges and sequence messages that say which relation they show.
- Completeness as an explicit, per-diagram statement; a per-view "hidden here" marker.
- Rules that report gaps and contradictions across diagrams with German messages naming both diagrams.
- A resolution flow in the project review panel that always ends in a person's decision.
- The agent uses relations and does not invent them silently.
- Old data loads, renders, fingerprints and exports unchanged (`tests/test_compat_v040.py` and the earlier compat
  tests).

## Non-goals

- Activity diagrams: their edges are behaviour (control and object flows between actions, ADR-0073); they are not
  linked to relations and do not declare completeness (ADR-0139).
- Generating views from the model, or layout from relations.
- Relations between relations, n-ary relations, multiplicities beyond the one cardinality rule.
- Renaming propagation for relations: relations have no name of their own; edge labels and message names belong to
  their view.
- An RDF/OWL or ArchiMate export (possible later from the same data).
- Atomic cross-document writes (as in M18: ordered writes, drift repaired by `sync`).

## Design

### Domain (`arcviz/project_model.py`, pure)

```python
RELATION_KINDS = ("communicates", "deployed_on", "data_flow")
MAX_RELATIONS = 500  # per project, see "Size budget"
MAX_RELATION_DESCRIPTION = 300
MAX_PROPERTY_VALUE = 64
RELATION_PROPERTIES = ("protocol", "port", "direction", "mode")  # direction: forward|both|none, mode: sync|async


class Relation(BaseModel):
    id: str  # slug of at most 64 characters, unique in the project ("r1", "r2", … unless given; never reused)
    kind: str  # one of RELATION_KINDS
    source: str  # element id
    target: str  # element id
    properties: dict[str, str] = {}  # keys of RELATION_PROPERTIES allowed for the kind
    description: str = ""
```

`Project.relations: list[Relation]` (default empty, **omitted from dumps while empty**, so projects written before M25
dump, export and compare unchanged). Generated ids are `r<n>` with `n` one above `Project.relation_seq` (the highest
number ever issued, omitted while 0) and the highest `r<n>` present: an id is **never reused**, so a link or hidden
marker that an undo or a restore brings back never attaches to another relation; it is reported as
`invalid-relation-link` and cleared by `sync`. Relation ids and the element ids at their ends are at most 64
characters (`MAX_ID_LENGTH`, like every stored id); explicit element ids are bounded the same way. Only ids of the
form `r<digits>` that fit move the counter; an explicit `r<digits>` id whose successor would not fit is refused, and
when no further `r<n>` fits (a counter at the end) a new relation needs an explicit id: generation never ends in an
integrity failure.

**Kind rules** (`RELATION_RULES`, ADR-0136):

| Kind | Source kinds | Target kinds | Properties | Orientation |
|---|---|---|---|---|
| `communicates` | every element kind | every element kind | protocol, port, direction, mode | either (a reply runs backwards) |
| `deployed_on` | component, subsystem, system, data, node | node | — | source → target |
| `data_flow` | actor, system, subsystem, component, data | same | protocol, port, mode | source → target |

A relation never connects an element with itself. A second relation of the same kind between the same ends (for
`communicates` in either orientation) is refused: one relation, many views.

**Containment stays `Element.parent`** (ADR-0136). It is a tree property of elements ("part of"), already edited,
shown and checked since M18; a `contains` relation would duplicate it and allow graphs where a tree is meant.
`deployed_on` is not containment: it says where something runs, so an element may have a parent *and* be deployed on a
node. In a deployment diagram, nesting a linked node inside a linked container reads as `deployed_on` when the
container's element is a `node`, and as containment otherwise.

### Size budget (ADR-0135)

Relations live in the project document next to the elements, so in Cosmos DB they share the one project item
(limit 2 MB, measured in UTF-8 bytes of the JSON). Every relation field is bounded:

| Field | Bound | ASCII bytes | Worst UTF-8 bytes |
|---|---|---:|---:|
| `id`, `source`, `target` (slugs, enforced) | 64 each | 192 | 192 |
| `kind` | 13 | 13 | 13 |
| four property values | 64 each | 256 | 768 |
| `description` | 300 | 300 | 900 |
| keys, quotes, commas (all fields set) | | 110 | 110 |
| **one relation** | | **≈ 0.87 KB** | **≈ 1.98 KB** |

`MAX_RELATIONS = 500` gives at most 435 KB (ASCII) and 990 KB (worst case, every free-text character three bytes).
`tests/test_m25_review.py` builds that worst case next to 500 typical elements and 1 000 memberships and checks that
the project serialises below 2 MB.
Elements are counted as M18 did: 500 elements with typical names and descriptions (100 + 500 characters) take about
400 KB; memberships and sections of a large project (1 000 diagrams) about 120 KB. Together that is ≈ 1 MB typical and
≈ 1.5 MB worst case for relations, still below 2 MB. Element names and descriptions had no bound of their own (M18);
M26 bounds them for new, changed and imported elements (#212, ADR-0153): `MAX_ELEMENT_NAME` = 100 and
`MAX_ELEMENT_DESCRIPTION` = 300 characters, so one element at its bounds (id and parent 64, kind, name and description
in three-byte characters) takes about 1.4 KB and 500 of them about 0.7 MB. The test now builds every element and every
relation at its bounds in three-byte characters next to 1 000 memberships: **1.67 MB**, below 2 MB. The bound is not an
integrity rule, so a project stored before it stays readable and writable. Open: the Cosmos DB SDK escapes non-ASCII
characters in request bodies by default (`\u20ac`, six bytes for `€`); whether the 2 MB limit counts that encoding or
the stored UTF-8 is not documented, and with six-byte escapes the same worst case is about 2.9 MB (relations alone
about 1.8 MB). So the per-field bounds cover realistic text, and a size guard covers the rest: the project service
refuses a write that makes the document, measured as the SDK sends it, larger than 1.9 MB (never one that shrinks it;
[robustness](robustness.md), ADR-0153). A separate keyed document for relations was rejected: it would add
a second conditional write (and a second revision) to every relation change that also touches elements (deleting an
element deletes its relations), for space we do not need.

The diagram side adds at most `MAX_RELATIONS` hidden relation ids per diagram (≈ 33 KB) and one `ref` per edge or
message.

### Project operations (`projects.py`, internal)

| Operation | Arguments |
|---|---|
| `add_relation` | `kind`, `source`, `target`, `properties?`, `description?`, `id?` → the relation |
| `update_relation` | `relation`, `kind?`, `source?`, `target?`, `properties?`, `remove_properties?`, `description?` |
| `remove_relation` | `relation` |

Element references resolve like everywhere (id, name, slug). All three are `INTERNAL_OPERATIONS`
(`projects.RELATION_OPERATIONS`): `POST /api/projects/{pid}/ops` refuses them, because only `ModelService` knows the
diagrams a change must reach. Existing element operations change as follows: `remove_element` **removes the element's
relations** with it (reported as `removed_relations`; a relation without an end means nothing), and `update_element`
refuses a kind change that a relation's kind rule forbids ("remove or change relation r3 first"). `Project.integrity_errors`
adds: unique relation ids, valid slugs, known kinds, known ends, kind rules, no self relation, no duplicate, allowed
property keys and values, bounded description, at most `MAX_RELATIONS`.

### Diagram side (`model.py`, `sequence.py`, `operations.py`)

- `Edge.ref: str | None` and `Message.ref: str | None`: the relation the connection shows. Omitted while unset.
- `Diagram.complete: list[str]`: the relation kinds this diagram declares complete (sorted, unique). Omitted while
  empty (open world).
- Only deployment and sequence diagrams (`model.RELATION_DIAGRAM_TYPES`) carry relation refs, `complete` and
  `hidden_relations`: `Diagram.integrity_errors` refuses them on other types (so does the importer), and the model
  service never sends a relation operation to such a diagram (`remove_element` clears only the element there).
- `Diagram.hidden_relations: list[str]`: relation ids this diagram intentionally does not show (sorted, unique).
  Omitted while empty. The marker is **diagram side** (ADR-0137): it is a statement of this view, versioned, undone
  and restored with it, and it does not grow the project item; a move to another project or an import carries the
  relation along and renames the marker (ADR-0161; an import without `model` loses it with its links, ADR-0079).

`fingerprint()` is unchanged: unset fields are not in the dump, so stored fingerprints stay valid; a set field is
content and changes the fingerprint.

Which connections may show which relation kinds (`relation_kinds_for(diagram_type, view_kind)`, ADR-0139):

| Diagram type | View connection | Relation kinds |
|---|---|---|
| deployment | edge `connection` | communicates, data_flow |
| deployment | edge `dependency`, `association`, `replication` | communicates |
| deployment | edge `deploys` | deployed_on |
| deployment | edge of a configured kind (kinds file) | communicates, data_flow |
| sequence | complete message (sender and receiver) | communicates |
| sequence | found or lost message | — |
| activity | — | — |

The kinds a diagram can show are the union of its column (`shown_relation_kinds(type)`): deployment all three,
sequence `communicates`, activity none.

Operations (deployment and sequence only; `TYPE_OPERATIONS`):

| Operation | Arguments | Public |
|---|---|---|
| `link_relation` | `id` (edge or message), `relation`, `kind`, `source`, `target` | no |
| `unlink_relation` | `id` | yes |
| `sync_relation` | `relation`, `kind`, `source`, `target` | no |
| `clear_relation` | `relation` | no |
| `set_relation_hidden` | `relation`, `hidden` | no |
| `set_completeness` | `kinds` (list of relation kinds, or `["all"]`; `[]` = open world) | yes |

`link_relation` checks that the connection may show the kind and that its ends are **views linked to the relation's
ends**: the edge's source and target nodes (the message's sender and receiver lifelines) have `ref`s equal to the
relation's `source` and `target`, in either orientation for `communicates` and `deployed_on` (a `deploys` edge is
drawn either way in practice; the element kinds already say which end is which) and in the relation's orientation
for `data_flow`. `sync_relation` clears the links of the relation that no longer fit after its kind or ends changed;
`clear_relation` clears its links and its hidden marker (the relation is being removed). `set_relation_hidden` adds or
removes the id (internal: the model service checks the relation exists). `set_completeness` stores the kinds sorted;
`["all"]` stands for the type's `shown_relation_kinds` at that moment (a kind added later is not declared
complete by an old diagram).

Links stay consistent locally: when a node or lifeline's `ref` changes (`link_element` to another element,
`unlink_element`, `clear_element`), the relation links of the connections touching it are cleared in the same
operation (its result lists them as `unlinked_relations`); `update_message` changing a linked message's sender or
receiver clears its link the same way; `update_edge` refuses a kind change of a linked edge to a kind that cannot show
every relation kind its current kind can. What is not visible from inside a diagram (a relation changed or removed in
the model) is repaired by the model service and reported by the review until then.

### Service (`ModelService`)

- `model(pid)` adds `relations` (each with `usages`: `diagram`, `title`, `type`, `view`, `label`; and `shown`: the
  unlinked connections of member diagrams between views of its ends, which could be linked) and `diagrams`
  (`id`, `title`, `type`, `complete`, `hidden`): everything the matrix needs, from the reads it already does.
- `add_relation(pid, kind, source, target, properties, description, id)` (project write).
- `update_relation(pid, relation, …) -> RelationChange`: project write; if kind or ends changed, `sync_relation` on
  every member diagram that links it (one conditional, versioned write each, `Beziehung „…“ geändert`), listing
  `unlinked` and `failed` diagrams like `update_element`; the member diagrams are read only then, after the
  conditional project write. Property and description changes read and reach no diagram (views stay primary): a
  property now differing from a view is a finding.
- `remove_relation(pid, relation) -> RelationRemoval`: `clear_relation` on every diagram that links or hides it
  (versioned), then the project write; `ConflictError` with the relation kept when a diagram kept changing.
- `remove_element`: also `clear_relation` for each of its relations in the same per-diagram write, then the project
  write (which removes the element and its relations).
- `link_relation(diagram_id, view, relation, expected?)`, `unlink_relation(diagram_id, view, expected?)`,
  `hide_relation(diagram_id, relation, hidden, expected?)`.
- `sync(pid)` additionally clears, per diagram in its one version, relation links that no longer fit their relation
  and links and hidden markers of relations that no longer exist.

Every method checks the caller through the bound services (`require` before the first read or write); writes are
conditional (project revision, diagram revision) and publish live events through the services they use. A link racing
a relation change can leave a link that no longer fits (no compensation as in ADR-0128: the link names a relation by
id only, so nothing wrong is drawn): the review reports it (`invalid-relation-link`), `sync` repairs it.

### HTTP API

| Route | Body | Answer |
|---|---|---|
| `GET /api/projects/{pid}/model` | | adds `relations`, `diagrams` |
| `POST /api/projects/{pid}/relations` | `{kind, source, target, properties?, description?, id?}` | 201 `{relation, model}` |
| `PATCH /api/projects/{pid}/relations/{rid}` | `{kind?, source?, target?, properties?, remove_properties?, description?}` | `{relation, unlinked, failed, model}` |
| `DELETE /api/projects/{pid}/relations/{rid}` | | `{relation, unlinked, failed, model}` |
| `POST /api/diagrams/{id}/link-relation` | `{view, relation}` (`If-Match`) | diagram detail |
| `POST /api/diagrams/{id}/unlink-relation` | `{view}` (`If-Match`) | diagram detail |
| `POST /api/diagrams/{id}/hidden-relations` | `{relation, hidden}` (`If-Match`) | diagram detail |
| `POST /api/diagrams/{id}/ops` | `set_completeness`, `unlink_relation` like any operation | |

`/api/config` adds `project_model.relation_kinds`, `relation_rules`, `relation_properties`, `max_relations`, and per
diagram type `relations` (view connection → relation kinds) and `completeness` (the kinds it can declare). Viewers
read, editors write; every route takes `svc: Svc` and is listed in `tests/test_access_matrix.py`.

### Exchange

Project bundles carry `project.relations` and `project.relation_seq` (omitted while 0) when there are any and then
have `schema_version` 3. The import restores the counter as the highest of `relation_seq`, every `r<n>` of the
relations and every `r<n>` that a diagram's links or hidden markers name, so no id is issued again in the copy
either; element ids longer than 64 characters are refused (new data only). (2 with elements only,
1 without a model: older exports are unchanged); `parse_project` reads 1 to 3 and validates relations through the
project's integrity rules. Diagram documents carry `ref` on edges and messages, `complete` and `hidden_relations`
like other fields; `parse_document` checks the completeness kinds against the diagram type and the ids' format. A
single-diagram import and a move to another project carry the relations along (ADR-0161; a document without `model`
drops edge and message refs and hidden markers with the element links, ADR-0079); the completeness declaration stays
(it names kinds, not ids).

### What "shown" means

One definition, `project_model.shown_views(diagram, relation, element_kinds)` (`shows` for the boolean), used by the
review rules and the model answer, mirrored by `static/relations.js` `showsRelation` for the inspector; both are
checked against the same cases in `tests/fixtures/relations_shown.json`. A diagram shows a relation through a
connection linked to it, an unlinked connection that fits it, or, for `deployed_on` in a deployment diagram, by
nesting the source's node under the target's: the target must be the **nearest ancestor standing for a `node`
element**, through any other containers (a service in a subsystem group on a server runs on the server; a service in
a pod in a cluster runs on the pod). `project_model.nested` gives both readings of nesting per node: the nearest
linked ancestor (containment, compared with `Element.parent`) and the nearest node (deployment).

### Correspondence rules (`project_review.py`, ADR-0141)

All pure functions of the `ProjectSnapshot`. The relation rules (1–3) run only when the project has at least one
relation: a project that never adopted relations gets no new suggestions after the upgrade. A connection between two
elements "is shown" in a deployment diagram by an edge between views of them (linked or not) and, for `deployed_on`
and containment, by nesting (a node standing for the element inside a node standing for the other); in a sequence
diagram by a message between lifelines standing for them.

| Rule | Kind | Finds |
|---|---|---|
| `relation-not-shown` | gap, suggestion/low | no deployment diagram shows the relation; names the deployment diagrams that show both ends and do not hide it (none: no finding); not raised when one of them is complete for the kind (that is `complete-view-missing-relation`) |
| `connection-without-relation` | gap, suggestion/low | a deployment edge or message between two linked elements that links no relation: link the matching relation, or create one |
| `complete-view-missing-relation` | gap, risk/medium | a diagram complete for the relation's kind shows both ends but not the relation, and does not mark it hidden |
| `relation-property-conflict` | contradiction, risk/medium | a property (protocol, port, direction, sync/async) that differs between two views of the relation, or between a view and the model |
| `deployed-on-several-nodes` | contradiction, risk/medium | an element deployed on two different nodes (by relations or by nesting in deployment views) |
| `containment-conflict` | contradiction, risk/medium | an element nested inside different elements in two views, or inside another element than its `parent` |
| `invalid-relation-link` | drift, risk/medium | a link to a relation that no longer exists, or whose kind or ends the connection no longer fits, or a hidden marker of a relation that no longer exists (`sync` repairs) |
| `message-without-connection` | gap, risk/medium | (generalised) messages between two elements that no deployment diagram connects **and** no `communicates` relation relates |

**Why `relation-not-shown` is not an omission** (open world): it never blames a view. It says that the model states
something that no structural view documents, which is a gap of the documentation as a whole, and it only names the
views that could draw it, as a low suggestion. A view that hides the relation is not a candidate; a view declared
complete for the kind is reported by `complete-view-missing-relation` instead (a risk, because there the view did
make a claim). **Exactly one rule** reports a message between two linked elements that no relation backs: in a project
with relations, `message-without-connection` when both elements appear in deployment diagrams and none connects them,
`connection-without-relation` otherwise.

Property values compared: an edge's `protocol` and `port` properties and its explicit `direction` (relative to the
relation: an edge drawn from the relation's target to its source turns `forward` into an arrow against the model's
direction, which no relation states, so it always differs, and the message says so in words ("ein Pfeil entgegen der
Modellrichtung"); `both` and `none` read the same either way; no model change to it is offered);
a message's sort
(`synchCall` → sync, `asynchCall`/`asynchSignal` → async; replies and create/delete messages say nothing); the
relation's `properties`. Values compare case-insensitively after trimming.

`message-without-connection` keeps its finding id (`message-without-connection:<diagram>:<a>+<b>`) and its message;
the only change is that a `communicates` relation between the two now also counts as a connection (whether a
deployment view draws it is `relation-not-shown`'s business, so the same gap is not reported twice). A message pair
with no relation and no deployment connection is reported by this rule only, not also by
`connection-without-relation`.

Findings gain `relation` (the relation concerned) and `resolutions` (below). Messages are German and name both
diagrams where two are involved ("„Plattform“ sagt HTTPS, „Checkout“ sagt gRPC"). The LLM project review digest lists
the relations after the elements (`Model relations:`, within `RELATION_DIGEST_BUDGET` = 6 000 characters, then
counted), and the rule findings as before.

### Resolution flow (#207, ADR-0138)

Each correspondence finding carries `resolutions`, a list of what a person can do; nothing is applied by the review:

| Action | Fields | What the UI does |
|---|---|---|
| `propose` | `diagram`, `prompt` | opens the diagram and sends `prompt` as a propose-mode chat turn (preview, then **Übernehmen** or **Verwerfen**) |
| `hide` | `diagram`, `relation` | `POST /api/diagrams/{diagram}/hidden-relations {relation, hidden: true}` |
| `link_relation` | `diagram`, `view`, `relation` | `POST /api/diagrams/{diagram}/link-relation` |
| `update_relation` | `relation`, `args` | `PATCH /api/projects/{pid}/relations/{relation}` with `args` (e.g. the property value one view uses) |
| `create_relation` | `args` | `POST /api/projects/{pid}/relations` |
| `remove_relation` | `relation` | `DELETE …/relations/{relation}` after a confirmation |
| `sync` | | `POST /api/projects/{pid}/model/sync` |

Per rule: `relation-not-shown` → propose (in a deployment diagram that shows both ends), remove_relation;
`connection-without-relation` → link_relation or create_relation, propose (remove the connection);
`complete-view-missing-relation` → propose, hide, remove_relation; `relation-property-conflict` → update_relation
with each value seen (one resolution per value), propose (adapt each view to the model's value);
`deployed-on-several-nodes` and `containment-conflict` → propose per diagram, update the model (remove a relation,
move the element); `invalid-relation-link` → sync. The project review panel shows them as buttons under the selected
finding, editors only (viewers see the list), and reloads the findings after the action (for a proposal: after it was
applied or discarded).

### Agent (#209, ADR-0140)

- `list_elements` also returns the relations. New tools: `create_relation(kind, source, target, properties?,
  description?)` and `update_relation(relation, …)` (project level), `link_relation(id, relation)` and
  `unlink_relation(id)` (the active diagram), `set_completeness(kinds)` (deployment and sequence diagrams).
  `create_relation`, `update_relation` and `link_relation` are refused in propose mode, like the element tools.
- The prompt's model block lists relations after the elements (`- [r1] communicates: shop -> order-service
  {protocol=HTTPS}`), within the model summary budget (elements first; relations are counted when they do not fit).
- `prompts/shared/model.txt`: when the agent draws an edge or message between two linked elements and a matching
  relation exists, it links it with `link_relation` in the same turn. When none exists it does **not** create one
  silently: it says in its reply which relation it would add and creates it when the user agrees (or asked for it in
  the first place). It never resolves a consistency finding on its own initiative.
- Eval cases (`tests/evals/cases.json`): link a matching relation after drawing an edge; mention, not create, a
  missing relation; create a relation when asked.

### UI (#208)

- **Modell** panel: a second list **Beziehungen** (kind, source → target, properties, description, where shown) with
  create, edit and delete for editors; read-only for viewers. A **Matrix** view: relations × diagrams with the cell
  states *verknüpft*, *gezeigt (nicht verknüpft)*, *ausgeblendet*, *fehlt (vollständige Sicht)* and empty; a cell with
  a connection opens the diagram and highlights it.
- Inspector: a **Projektbeziehung** section for edges (deployment) and messages, the counterpart of **Projektelement**:
  the linked relation, or a choice of the relations that fit the connection's ends, link/unlink. The diagram section
  of the inspector gets **Vollständigkeit** (one checkbox per kind the type can declare) and, for a complete diagram,
  the relations between shown elements that it does not draw, each with **hier bewusst ausgeblendet**.
- Pure helpers in `static/relations.js` (pure layer, `tests/js/relations.test.mjs`): kind labels, choices of relations
  for a connection, the matrix cells, the PATCH body of a relation edit, resolution labels.

### Concurrency and errors

- Relation operations are project writes conditional on the project revision; propagation and clearing are per-diagram
  conditional writes, ordered *project first* for a change and *diagrams first* for a delete (as for elements), so a
  crash leaves at worst a link that no longer fits or a relation without views, which the review reports and `sync`
  (or a second delete) repairs.
- Unknown project, diagram, relation: 404 for what the caller may not see, 400 (`ProjectError`) for an unknown
  relation id of a visible project; 409 on conflicts; 412 on a stale `If-Match`.

## Testing

- `tests/test_relations.py`: domain, kind rules, integrity, project operations (rollback, cascade on element delete,
  kind change refused), diagram operations (link rules, orientation, clearing on end changes, internal refusals),
  completeness and hidden markers, fingerprints, `ModelService` (propagation, removal, sync, access), REST routes.
- `tests/test_project_review.py`: each correspondence rule, the generalised `message-without-connection`, resolutions.
- `tests/test_project_bundles.py`: schema version 3 round trip; `tests/test_exchange.py`: diagram fields.
- `tests/test_agent_model.py`, `tests/test_agent.py`, `tests/test_project_agent.py`: relation tools with
  `ScriptedProvider`; `tests/test_evals.py` checks the new cases offline.
- `tests/test_properties.py`: the strategy draws link/unlink of relations, completeness and hidden markers.
- `tests/test_compat_v040.py`: file, sqlite and Cosmos-fake stores written by v0.4.0 (`tests/fixtures/v0_4_0`) load,
  dump, fingerprint, export and answer the model unchanged; v0.1.7, v0.1.8 and M20 compat tests stay green.
- `tests/test_access_matrix.py`, `tests/test_access.py`: new routes and service methods.
- `tests/js/relations.test.mjs`; e2e: a property contradiction resolved end to end from the project review.

## Migration and compatibility

No migration. New fields have defaults and are omitted while unset, so documents written before M25 dump, fingerprint
and export unchanged. Bundles without relations keep schema versions 1 and 2.

## Plan

1. `docs(architecture)`: this spec, ADR-0135..0141, the v0.4.0 compatibility fixtures.
2. #203 `feat(model)`: relations in the project model.
3. #204 `feat(model)`: edges and messages link to relations.
4. #205 `feat(model)`: completeness declaration and hidden markers.
5. #206 `feat(review)`: correspondence rules.
6. #207 `feat(review)`: resolutions through proposals.
7. #208 `feat(ui)`: relations in the model panel, matrix, inspector.
8. #209 `feat(agent)`: relation tools.

## Decisions taken in this spec (not settled by the issues)

- Containment stays `Element.parent`; nesting under a `node` element reads as `deployed_on` (ADR-0136).
- Deleting an element deletes its relations (ADR-0135).
- Relations stay in the project item, `MAX_RELATIONS` = 500 with bounded fields and ids, ids never reused (ADR-0135).
- One definition of "shown"; nesting reads deployed_on through other containers up to the nearest node (ADR-0139).
- The hidden marker is diagram side (ADR-0137).
- Activity edges are not linked (ADR-0139); `communicates` and `deployed_on` links accept either orientation.
- Relations have no name; nothing but link validity propagates into views (ADR-0138, ADR-0139).
- Relation gap rules only for projects that have relations; `message-without-connection` keeps its id (ADR-0141).
- The agent links matching relations itself and proposes missing ones in its reply (ADR-0140).
