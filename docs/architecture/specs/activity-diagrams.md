# Activity diagrams

- Status: Implemented (milestone M13)
- Issue: #99
- ADR: [0034 Activity diagrams reuse the node and edge operations](../adr/0034-activity-diagrams-reuse-node-and-edge-operations.md)
- Rendering: [ADR-0032 Rendering of diagram types in M13](../adr/0032-rendering-of-diagram-types-in-m13.md), implementing [ADR-0038](../adr/0038-renderer-per-diagram-type.md)
- Builds on: [Diagram types](diagram-types.md) (ADR-0030, ADR-0032)

## Summary

A diagram type `activity` following the UML 2.5.1 **Activities** chapter (15): actions, control nodes,
object nodes and activity partitions (swimlanes), connected by control and object flows with guards.
An activity diagram is a graph like a deployment diagram, so it reuses the node and edge operations
with its own kind catalogue, a few UML well-formedness rules and its own tools and prompt. It is
rendered as a Mermaid flowchart with UML notation for each node (ADR-0032).

## Goals

- The UML metaclasses listed in #99 as kinds: OpaqueAction, InitialNode, ActivityFinalNode,
  FlowFinalNode, DecisionNode, MergeNode, ForkNode, JoinNode, ActivityPartition (container),
  ControlFlow with guards; ObjectFlow and the object nodes CentralBufferNode and DataStoreNode (cheap,
  so included).
- UML notation in the rendering as far as Mermaid allows.
- Rules that keep the model well-formed in the UML sense where it is cheap to check.
- Everything generic works: focus on partitions, layout direction, inspector, batch, propose mode,
  versions, comparison, JSON import/export, property tests.

## Non-goals

- Other action kinds (CallBehaviorAction, AcceptEventAction, SendSignalAction, …), pins, exception
  handlers, interruptible regions, expansion regions, edge weights, join specifications, multi-dimensional
  partitions. They are additive later.
- True swimlane layout: partitions are drawn as nested boxes (Mermaid subgraphs).
- Token-flow semantics or simulation; fork/join balance checks.
- PlantUML export (follow-up #107, done in M27: [diagram exchange](diagram-exchange.md#activity-diagrams-107)).

## Design

### Kinds (`model.ACTIVITY_KINDS`)

| Kind | UML 2.5.1 metaclass | Container | Notation (Mermaid) |
|---|---|---|---|
| `opaque_action` | OpaqueAction (16.3) | no | rounded rectangle with the name |
| `initial_node` | InitialNode (15.3) | no | filled circle (`f-circ`, styled black) |
| `activity_final_node` | ActivityFinalNode | no | bull's eye (`fr-circ`, black fill with a white ring) |
| `flow_final_node` | FlowFinalNode | no | circle with a cross (`cross-circ`) |
| `decision_node` | DecisionNode | no | diamond (`diam`), name inside if given |
| `merge_node` | MergeNode | no | diamond |
| `fork_node` | ForkNode | no | bar (`fork`) |
| `join_node` | JoinNode | no | bar (`fork`) |
| `central_buffer_node` | CentralBufferNode (15.4) | no | rectangle `«centralBuffer»` + name |
| `data_store_node` | DataStoreNode | no | rectangle `«datastore»` + name |
| `activity_partition` | ActivityPartition (15.6) | yes | subgraph labelled with the name |

Edge kinds: `control_flow` (ControlFlow), `object_flow` (ObjectFlow); both drawn as solid arrows as in
UML. The guard (`ActivityEdge::guard`) is shown as `[guard]` before the edge's name (`label`).

### Guards

`add_edge` and `update_edge` take an optional `guard` (string) for activity diagrams. It is stored as
the edge property `guard` (an empty string removes it), so the inspector's property editor, the diff
and the summary show it without new fields. For other types `guard` is an error.

### Rules (operations and import)

Checked by `add_edge`, `update_edge`, `update_node` (kind changes) and by `exchange._rule_errors`:

1. An ActivityPartition is not an ActivityNode: no edge may start or end at a partition.
2. An InitialNode has no incoming edges; ActivityFinalNode and FlowFinalNode have no outgoing edges.
3. ActivityEdges are always directed: `direction` must be unset (`default`).

Partitions may contain activity nodes and partitions; everything else is unchanged from the generic
node/edge operations (ADR-0034).

### Operations

`TYPE_OPERATIONS["activity"]` = the node/edge operations of deployment diagrams plus
`rename_diagram`, `set_direction`, `apply_batch`.

### Tools and prompt (`agent_types.py`)

The tools are the deployment tools with UML descriptions ("Add an ActivityNode … or an
ActivityPartition …"), the activity kind enums, `guard` on `add_edge`/`update_edge`, and no
`direction` on edge tools. The prompt explains the notation, the rules and gives examples
(decision with guards, fork/join, partitions).

### Rendering (`mermaid.py`)

`mermaid.render` picks the node line per type: activity nodes use the shapes above (Mermaid ≥ 11.3
`@{ shape: … }` syntax); labels inside `@{ label: "…" }` escape backslashes as `#92;` in addition to
`escape_text`, because the label is a YAML string. Activity kinds get class styles (filled black
circles and bars). Focus, highlight and comparison marks work unchanged. When the diagram has
ActivityPartitions, the source starts with the front matter `config: layout: swimlane`
(`mermaid.SWIMLANE_FRONT_MATTER`), so Mermaid draws the top-level partitions as lanes (ADR-0038,
ADR-0032); without partitions the default (dagre) layout is used.

### Templates

`order-process` (activity, drawn with swim lanes): partitions *Kunde*, *Shop*, *Lager*; initial node → place order →
decision *Artikel verfügbar?* with guards `[ja]`/`[nein]` → fork → payment and picking in parallel →
join → shipping → final node; `[nein]` → inform customer → flow final; an object flow into a data store.

### Frontend

The inspector works as for deployment (kinds from `typeConfig`); for types whose edges are always
directed (`features.edge_direction` false) the edge direction selector is hidden. The guard is edited
as the `guard` property.

## Testing

- `tests/test_activity.py`: kinds, guard handling, the three rules in operations and import, Mermaid
  shapes and labels (hostile names, guards), tools and prompt, template.
- Property tests run from the activity template too: integrity, rule errors empty, Mermaid structure.
- e2e: create an activity diagram from the template, check the shapes render and a hostile name is
  shown literally.

## Migration / compatibility

New type only; nothing changes for deployment diagrams.

## Open questions

None open; see Decisions.

## Decisions

- Activity diagrams reuse the node/edge operations; guards are the edge property `guard` set through
  a `guard` argument (ADR-0034, Proposed).
- Object nodes: CentralBufferNode and DataStoreNode are included (cheap); pins are not.
- Decision node names are shown inside the diamond (common practice, not required by UML).
