# Diagram types

Source: `arcviz/model.py` (`DIAGRAM_TYPES`, `UML_REFERENCES`, `kinds_for`), `arcviz/operations.py`
(`TYPE_OPERATIONS`), `arcviz/render.py`, `arcviz/agent.py` + `arcviz/agent_types.py` + `arcviz/prompts/`,
`arcviz/exchange.py`, `arcviz/diff.py`, `arcviz/templates.py`, `arcviz/static/types.js`.
Tests: `tests/test_diagram_types.py`, `tests/js/types.test.mjs`. Design:
[diagram types spec](../architecture/specs/diagram-types.md), ADR-0030 to ADR-0037; ADR-0032 implements the accepted [ADR-0038](../architecture/adr/0038-renderer-per-diagram-type.md)
of the [renderer case study](../architecture/specs/renderer-case-study.md).

## Review (M12)

Rule checks are registered per type (`rules.RULES`): deployment, activity and sequence diagrams each have
their own rules ([Architecture review](review.md#rule-checks); activity and sequence since M27, #123). Findings of
sequence diagrams name interaction element ids in `elements`. The LLM review works for every type; for sequence diagrams element references are validated against
the interaction (`Diagram.has_element`). Review highlights use the Mermaid ids for Mermaid renders
(including `g.icon-shape`) and `data-id` for the sequence SVG.

## Types

`Diagram.type` is one of `model.DIAGRAM_TYPES`; the first, `deployment`, is the default, so stored
diagrams without the field load as deployment diagrams. An unknown type fails model validation
(`Unknown diagram type '…'. Available: …`), which the store, the importer and `POST /api/diagrams`
report. The type is set at creation and no operation changes it.

| Type | UML 2.5.1 reference (`UML_REFERENCES`) | Content | Renderer |
|---|---|---|---|
| `deployment` | 19 Deployments, loosely (cloud-oriented kinds; ADR-0030) | `nodes`, `edges` | `mermaid` |
| `activity` | 15 Activities (plus OpaqueAction from 16 Actions) | `nodes` (ActivityNodes, ActivityPartitions), `edges` (ActivityEdges) | `mermaid` |
| `sequence` | 17 Interactions | `interaction` (lifelines, fragment tree, executions); `nodes`/`edges` empty | `svg` |
| `state_machine` | 14 State Machines | `nodes` (states, pseudostates, regions), `edges` (transitions) | `mermaid` |
| `class` | 11 Classification (and 10 Simple Classifiers) | `nodes` (classifiers, packages), `edges` (relationships) | `mermaid` |
| `use_case` | 18 Use Cases | `nodes` (actors, use cases, subjects), `edges` (relationships) | `mermaid` |
| `component` | 11 Components (with ports and interfaces of 10 and 11) | `nodes` (components, subsystems, interfaces, packages), `edges` (connectors, realizations, usages) | `mermaid` |

All types follow UML 2.5.1 (ADR-0030): kind, operation and tool names use the specification's terms.

## Per-type tables

Each layer keeps a table keyed by type; `tests/test_diagram_types.py` fails when a type is missing
from one of them, when an operation belongs to no type, or when a type's tools do not match its
operations.

| Layer | Table | Content |
|---|---|---|
| `model` | `kinds_for(type)` | the kind catalogue: deployment → the active catalogue (built-in, kinds file, kind packs); other types → `TYPE_KINDS[type]` (fixed) |
| `model` | `UML_REFERENCES` | the UML chapter, reported in `/api/config` |
| `operations` | `TYPE_OPERATIONS` | the operation names a type offers (all of them keys of `OPERATIONS`) |
| `render` | `RENDERERS` | the renderer |
| `agent` | `build_tools(catalog, type)`, `build_system_prompt(diagram)` | tools: deployment in `agent.py`, others in `agent_types.TOOL_BUILDERS`; prompt: shared blocks plus `prompts.EXPERT_PARTS[type]` (`arcviz/prompts/types/<type>.txt`) |
| `exchange` | `_rule_errors`, `PLANTUML_TYPES` | import rules per type; types with a PlantUML export |
| `diff` | `SUPPORTED_TYPES` | types whose versions can be compared |
| `templates` | `TEMPLATE_TYPES` | the type of each template (`None` for `empty`: any type) |

Kind names are disjoint across types, so `Node.is_container` checks the union of all container
kinds (`model.container_kinds()`) without knowing its diagram.

## Operations

`apply(diagram, op, args)` raises `OperationError("Operation '<op>' is not available for <type>
diagrams. Available: …")` for a known operation outside `TYPE_OPERATIONS[diagram.type]`, before
anything changes. `apply_batch` checks every step's name the same way before the first step runs.
Kind errors list the kinds of the diagram's type. Every type also has the shared-model operations
(`link_element`, `unlink_element`, `sync_element`, `clear_element`, see [Operations](operations.md#shared-project-model)).

## Rendering

`render.Renderer` is the interface (ADR-0032):

```python
class Renderer(Protocol):
    name: str  # "mermaid" | "svg": the format of the source
    supports_focus: bool

    def render(self, diagram, *, highlight=(), focus=None, marks=None, edge_marks=None) -> str: ...
    def ids(self, diagram) -> dict[str, str]: ...  # model id -> id in the rendering
```

`render.payload(diagram, highlight=, focus=, marks=, edge_marks=)` returns
`{"renderer": name, "mermaid": str | None, "svg": str | None, "mermaid_ids": {...}}` (ADR-0033): the
source sits under its format's key, the other key is `null`; `mermaid_ids` is empty for SVG. A
renderer without focus support renders the whole diagram. `render.file_name(diagram)` is the Git
mirror's file (`diagram.mmd` or `diagram.svg`).

## Features per type

`/api/config` derives each type's `features` from the tables: `focus` (renderer supports focus),
`direction` (`set_direction` in the type's operations), `compare` (`diff.SUPPORTED_TYPES`),
`plantuml` (`exchange.PLANTUML_TYPES`). Since M27 every type exports PlantUML (#107).

| Feature | `deployment` | `activity` | `sequence` |
|---|---|---|---|
| `focus` (drill-down) | yes | yes (partitions) | no |
| `direction` | yes | yes | no |
| `compare` (version comparison) | yes | yes | yes (interaction elements, #108) |
| `plantuml` (export) | yes (deployment syntax) | yes (activity syntax) | yes (sequence syntax) |
| rule checks (`rules.RULES`) | yes | yes | yes |

## Activity diagrams

Design: [activity diagrams spec](../architecture/specs/activity-diagrams.md), ADR-0034. Tests:
`tests/test_activity.py`.

- Kinds (`model.ACTIVITY_KINDS`): leaves `opaque_action`, `initial_node`, `activity_final_node`,
  `flow_final_node`, `decision_node`, `merge_node`, `fork_node`, `join_node`, `central_buffer_node`,
  `data_store_node`; container `activity_partition`; edges `control_flow`, `object_flow`.
- Operations: the node/edge operations of deployment diagrams. Without `kind`, `add_node` uses
  `opaque_action` and `add_edge` `control_flow` (`graph_types.GraphProfile.default_node_kind` /
  `default_edge_kind`; deployment: `component`, `connection`).
- Guards: `add_edge(..., guard=)` and `update_edge(..., guard=)` store the stripped guard as the edge
  property `guard`; an empty guard removes it. `guard` on a type whose profile does not set `guard` (`graph_types.guard_types()`) is an
  `OperationError`.
- Rules (`model.activity_edge_problem`, `model.activity_rule_errors`, wired in through the activity
  `graph_types.GraphProfile`), enforced by `add_edge`, `update_edge`, `update_node` (kind change) and by the importer: no edge at an
  `activity_partition`; no incoming edge at an `initial_node`; no outgoing edge at a final node;
  `direction` must be unset (`GraphProfile.directed_noun`).
- Mermaid (`mermaid._ACTIVITY_SHAPES`, `_ACTIVITY_KEYWORDS`): `opaque_action` `id("name")`;
  `initial_node` `@{ shape: f-circ }` styled black; `activity_final_node` `@{ shape: fr-circ }` (black
  fill, white ring: a bull's eye); `flow_final_node` `cross-circ`; `decision_node` `diam` labelled with
  the name, `merge_node` `diam` without text; `fork_node`/`join_node` `fork` (black bar); object nodes
  `["«centralBuffer»<br/>name"]` / `["«datastore»<br/>name"]`; partitions are subgraphs labelled with
  the name only; with at least one visible partition the source starts with
  `mermaid.SWIMLANE_FRONT_MATTER` (`config: layout: swimlane`), so Mermaid draws the top-level partitions
  as lanes (ADR-0038, ADR-0032). Labels in `@{ label: "…" }` go through `mermaid.shape_label` (`escape_text` plus `\` →
  `#92;`). Edge text: `[guard] label`. Focus stubs use the same node lines.
- Tools (`agent_types._activity_tools`): the deployment tools with UML descriptions, the activity
  enums, `guard` on `add_edge`/`update_edge`, no `direction`; prompt `arcviz/prompts/types/activity.txt` (see [Agent: system prompt](agent.md#system-prompt)).
- Template `order-process`; `/api/config` feature `edge_direction` is `false`, so the inspector hides
  the edge direction selector.

## State machine diagrams

Design: [state machine diagrams spec](../architecture/specs/state-machine-diagrams.md), ADR-0178 (types and fields:
[uml-graph-types](../architecture/specs/uml-graph-types.md), ADR-0177, 0183..0185). Tests: `tests/test_state_machine.py`,
`tests/test_rules_state_machine.py`. Sources: `arcviz/state_machine.py` (rules, transition text),
`arcviz/state_rules.py` (review findings), `arcviz/plantuml_graph.py` (export).

- Kinds (`model.STATE_MACHINE_KINDS`): leaves `state`, `initial_pseudostate`, `final_state`, `terminate_pseudostate`,
  `choice_pseudostate`, `junction_pseudostate`, `fork_pseudostate`, `join_pseudostate`, `shallow_history_pseudostate`,
  `deep_history_pseudostate`; containers `composite_state`, `state_region`; edge kind `transition`. A state becomes
  composite through `update_node(kind="composite_state")`.
- Profile (`graph_types.PROFILES["state_machine"]`): default kinds `state`/`transition`, `guard` (top-level argument, as
  for activity), always directed (`Transitions are always directed …`), typed fields `entry`, `do`, `exit` on `state`
  and `effect` on `transition` (`fields` argument, [Operations](operations.md)). The trigger is the edge `label`.
- Hard rules (`state_machine.node_problem` / `edge_problem` / `rule_errors`, the same functions in operations and
  importer): no transition at a `state_region`; none into an `initial_pseudostate`; none out of a `final_state` or
  `terminate_pseudostate`; no trigger and no guard on a transition out of an initial or fork pseudostate or into a join;
  a `state_region` only inside a `composite_state`, which then holds regions only; `remove_node` without `cascade` is
  refused when the content that moves up would break these rules.
- Mermaid (`mermaid._STATE_SHAPES`, `_state_label`): `state` `id("name<br/>──────────<br/>entry / x")`; initial `f-circ`,
  final `fr-circ`, terminate `cross-circ`, choice `diam`, junction `sm-circ`, fork/join `fork`, history circles labelled
  `H` / `H*`; composite states and regions are subgraphs labelled with the name (regions dashed). Edge text
  `trigger [guard] / effect` (`state_machine.transition_text`).
- Review: nine rules (`state-no-initial`, `state-multiple-initial`, `state-initial-targets`, `state-unreachable`,
  `state-dead-end`, `state-nondeterministic`, `state-choice-without-else`, `state-crosses-regions`,
  `state-final-unreachable`), linear in nodes and edges ([Architecture review](review.md#rule-checks)).
- PlantUML (`plantuml_graph.state_machine`): every vertex is a named `state "Name" as n_id` (the ids of `mermaid.id_map`),
  pseudostates carry the stereotypes `<<start>>`, `<<end>>`, `<<history>>`, `<<history*>>`, `<<choice>>`, `<<fork>>`,
  `<<join>>`; composite states are `state … { }` with `--` between regions and a comment `' region: name`; behaviours are
  `n_id : entry / …`; all transitions follow at the end as `A --> B : trigger [guard] / effect`. A state inside a composite
  state is written `<outermost composite>.<alias>`, because PlantUML 1.2026.8 cannot address the states of a second parallel
  region by their alias (and refuses `[*]` there). A terminate pseudostate is an `<<end>>` and a junction a `<<choice>>`,
  each with a comment line before it. `tests/test_plantuml_state_machine.py` renders the exports with a real PlantUML
  (`ARCVIZ_PLANTUML_JAR`).
- Project model: nothing is linkable (`project_model.NOT_LINKED_TYPES`), so no state is adopted; the type counts as behaviour
  in the project review (`project_review.BEHAVIOUR_TYPES`).
- Tools (`agent_types._graph_builder`): the graph tools with state machine descriptions, the kind enums, `guard`,
  `fields` listing `entry`/`do`/`exit` and `effect` (a union per tool, checked per kind by the operation), no `direction`;
  prompt `arcviz/prompts/types/state_machine.txt`. Template `order-lifecycle`.
- Frontend: typed fields are rows of the inspector's property table; `propertyChanges(rows, current, typed)` sends them
  as `fields` (`types.typedFieldNames`), never as free properties.

## Class diagrams

Design: [class diagrams spec](../architecture/specs/class-diagrams.md), ADR-0179 (types and fields:
[uml-graph-types](../architecture/specs/uml-graph-types.md), ADR-0177, 0182..0185). Tests: `tests/test_class_diagram.py`,
`tests/test_rules_class.py`, `tests/test_plantuml_class.py`. Sources: `arcviz/class_diagram.py` (rules, member and
multiplicity text), `arcviz/class_rules.py` (review findings), `arcviz/plantuml_graph.py` (export).

- Kinds (`model.CLASS_KINDS`): leaves `class`, `interface`, `enumeration`, `data_type`; container `package`; edges
  `association`, `aggregation`, `composition`, `generalization`, `realization`, `dependency`. `association` and `dependency`
  are also deployment kinds with the same meaning and notation (`model.SHARED_KINDS`, ADR-0185).
- **Orientation**: an edge points from the element that refers to the other to the element it refers to. Generalization:
  specific → general; realization: class → interface; aggregation and composition: **part → whole** (Mermaid draws a
  marker only at the target end, ADR-0184).
- Profile (`graph_types.PROFILES["class"]`): default kinds `class`/`association`, no `guard`, typed fields on nodes
  `attributes`, `operations` (≤ 30 lines of ≤ 120 characters; `class`, `data_type`; `interface`: operations only),
  `literals` (≤ 30 lines of ≤ 60; `enumeration`), `abstract` (flag, only `class`); on `association`, `aggregation` and
  `composition`: `source_multiplicity`, `target_multiplicity` (`1`, `*`, `0..1`, `1..*`, `2..5`: `class_diagram.multiplicity_problem`)
  and `source_role`, `target_role` (≤ 40). A flag takes `true` or `false` (stored as `true`, `false` removes it).
- Hard rules (`class_diagram.edge_problem`, shared by operations and importer): a package only in `dependency` edges;
  `generalization` between two classes, two interfaces or two data types, not the same node, no cycle (`_reaches`);
  `realization` class → interface; `association` between classes or interfaces (recursive allowed), `aggregation` and
  `composition` between classes; `direction` only `forward`/`none` on an association, never on the other kinds except
  `dependency`.
- Mermaid (`mermaid._class_label`, `edge_lines`): a classifier is a rectangle whose label is its keyword
  («abstract», «interface», «enumeration», «dataType»), its name, then a rule (`──────────`) and one compartment per kind of
  member (at most 8 lines, then `… (+n)`); packages are subgraphs labelled with the name. End markers: association `---`
  (`-->` with `direction: forward`), aggregation `--o`, composition `==o`, generalization `-->`, realization `-.->` with
  the text `«realizes»` (`model.EDGE_STEREOTYPES`), dependency `-.->`. An association's text is its name, then a line
  `source_role source_multiplicity – target_role target_multiplicity`. The Mermaid style class of the kind `class` is
  `class_kind` (`mermaid._style_name`): `class` is a Mermaid keyword.
- Review: eleven rules (`class-package-cycle`, `class-multiple-wholes`, `class-composition-cycle`, `class-duplicate-member`,
  `class-multiple-inheritance`, `class-interface-unrealized`, `class-abstract-leaf`, `class-isolated`, `class-empty`,
  `class-association-without-multiplicity`, `class-enumeration-empty`), linear ([Architecture review](review.md#rule-checks)).
- PlantUML (`plantuml_graph.class_diagram`): `class`/`abstract class`/`interface`/`enum "Name" as n_id { members }`,
  data types as `class … <<dataType>>`, packages as `package "Name" as n_id { }`; relationships `n_whole "m" *-- "m" n_part`
  (orientation swapped back), `o--`, `n_general <|-- n_specific`, `n_interface <|.. n_class`, `..>`, `--`/`-->`; role names
  follow the name in parentheses (PlantUML has no role syntax).
- Project model: `class`, `enumeration`, `data_type` link to `data`, `interface` to `component`; none is adopted when created
  (`project_model.adoptable`), **Unverknüpfte übernehmen…** adopts them on request; no relation links.
- Tools (`agent_types._graph_builder`): the graph tools with class descriptions, the kind enums and `fields` listing
  attributes, operations, literals, `abstract` (boolean) and the four association fields; prompt
  `arcviz/prompts/types/class.txt`. Template `webshop-domain`.
- Frontend: the property table shows a typed list field as a textarea (one entry per line) and lists every field of the kind,
  also when it is not set (`inspector.fillPropRows`); `types.typedValues` turns the texts into lists and booleans for `fields`.

## Use case diagrams

Design: [use case diagrams spec](../architecture/specs/use-case-diagrams.md), ADR-0180 (types and fields:
[uml-graph-types](../architecture/specs/uml-graph-types.md), ADR-0177, 0182..0185). Tests: `tests/test_use_case.py`,
`tests/test_rules_use_case.py`, `tests/test_plantuml_use_case.py`. Sources: `arcviz/use_case.py` (rules, extend text),
`arcviz/use_case_rules.py` (review findings), `arcviz/plantuml_graph.py` (export).

- Kinds (`model.USE_CASE_KINDS`): leaves `actor`, `use_case`; container `subject`; edges `association`, `include`, `extend`,
  `generalization` (`association` and `generalization` are shared kinds, `model.SHARED_KINDS`). Edges point from the element
  that refers to the other to the element it refers to: include base → included, extend extension → base, generalization
  specific → general.
- A new diagram starts with layout direction `LR` (`model.TYPE_DIRECTIONS`; a diagram that is given a direction keeps it).
- Profile (`graph_types.PROFILES["use_case"]`): default kinds `use_case`/`association`; typed fields only on `extend`:
  `condition` (≤ 120) and `extension_point` (≤ 60). The specification of a use case is the node's note (`description`).
- Hard rules (`use_case.edge_problem` / `node_problem` / `rule_errors`, shared by operations and importer): a subject takes
  part in no edge; an association joins an actor and a use case (`direction` unset, `forward` or `none`); include and extend
  join two different use cases, an include never forms a cycle; a generalization joins two actors or two use cases, not the
  same node, no cycle; include, extend and generalization take no `direction`; an actor and a subject stand at the top level.
- Mermaid: actor `["«actor»<br/>name"]`, use case a stadium `(["name"])` (Mermaid 11.17 has no ellipse), subject a subgraph
  labelled with the name. `include` and `extend` are dotted arrows with the automatic keyword (`model.EDGE_STEREOTYPES`); an
  extend reads `«extend» [condition] (extension point)` and its name on the next line (`mermaid.edge_lines`).
- Review: nine rules (`usecase-without-actor`, `usecase-extend-cycle`, `usecase-duplicate-name`, `usecase-actor-unused`,
  `usecase-without-specification`, `usecase-extend-without-condition`, `usecase-include-used-once`, `usecase-outside-subject`,
  `usecase-subject-empty`), linear ([Architecture review](review.md#rule-checks)). The type counts as behaviour in the project
  review (`project_review.BEHAVIOUR_TYPES`).
- PlantUML (`plantuml_graph.use_case`): `actor "Name" as n_id`, a subject as `rectangle "Name" as n_id { usecase … }`,
  associations `--` (`-->` when forward), include and extend `n_a ..> n_b : <<include>>` / `<<extend>> [condition] (point)`,
  generalization `n_general <|-- n_specific`, and the note of a use case as `note right of n_id : …` (its first 200
  characters on one line; the rest is a loss).
- Project model: `actor` links to an element of kind `actor`, `subject` to `system` or `subsystem`; both are adopted when
  created (`project_model.adoptable`); a use case is behaviour and not linkable.
- Tools (`agent_types._graph_builder`): the graph tools with use case descriptions, the kind enums and `fields`
  (`condition`, `extension_point`) on edges only; prompt `arcviz/prompts/types/use_case.txt` (written for requirements
  engineers). Template `webshop-use-cases`.

## Component diagrams

Design: [component diagrams spec](../architecture/specs/component-diagrams.md), ADR-0181 (types and fields:
[uml-graph-types](../architecture/specs/uml-graph-types.md), ADR-0177, 0182..0185). Tests: `tests/test_component_diagram.py`,
`tests/test_rules_component.py`, `tests/test_plantuml_component.py`. Sources: `arcviz/component_diagram.py` (rules, port text),
`arcviz/component_rules.py` (review findings), `arcviz/plantuml_graph.py` (export).

- Kinds (`model.COMPONENT_KINDS`): leaves `component`, `interface`; containers `subsystem`, `package`; edges `connection`,
  `realization`, `usage`, `dependency`. Six of them are shared kinds (`model.SHARED_KINDS`, ADR-0185): `component` and
  `connection` with deployment diagrams, `interface`, `package` and `realization` with class diagrams, `dependency` with both.
  A new diagram starts with layout direction `LR` (`model.TYPE_DIRECTIONS`).
- Edges point from the element that refers to the other to the element it refers to: a connection from the requirer to the
  provider, a realization (provided interface) and a usage (required interface) from the component to the interface.
- Profile (`graph_types.PROFILES["component"]`): default kinds `component`/`connection`; typed fields: `ports` on `component`
  (≤ 10 lines of ≤ 40 characters; the name is the text before an optional `:`, unique per component), `source_port` and
  `target_port` on `connection`, `source_port` on `realization` and `usage` (≤ 40, the name of a port of that end).
- Hard rules (`component_diagram.edge_problem` / `node_problem` / `rule_errors`, shared by operations and importer): a package
  only in `dependency` edges; a connection joins two different components or subsystems; a realization and a usage go from a
  component or subsystem to an interface; no edge between a node and its own container (any kind); a port reference names a
  port of a `component` end; a package stands in a package or at the top level; port names are unique and a port that an edge
  names cannot be renamed or removed (`update_node` checks the node after a field change).
- Mermaid (`mermaid._component_label`, `_node_line(…, diagram_type)`): a component is a rectangle `«component»`, the name, and
  below a rule one line `port <line>` per port; a subsystem a subgraph `«subsystem» Name`; a package a subgraph with the name; an
  interface a circle with the name (`@{ shape: circle }`, UML's lollipop; a name over 18 characters a stadium with `«interface»`).
  The notation of a shared kind depends on the diagram type, the colour class on the kind. Edges: connection `-->`, realization
  `-.->` with `«realizes»`, usage `-.->` with `«use»`, dependency `-.->`; the ports of the ends follow the text as
  `(source → target)` (`component_diagram.port_text`, `mermaid.edge_lines(edge, "component")`).
- Review: seven rules (`component-interface-unprovided`, `component-dependency-cycle`, `component-interface-unused`,
  `component-fan-out`, `component-isolated`, `component-port-unconnected`, `component-subsystem-empty`), linear
  ([Architecture review](review.md#rule-checks)). The type counts as structure in the project review.
- PlantUML (`plantuml_graph.component_diagram`): `component "Name" as n_id { portin "port" as n_id_p0 }` (PlantUML cannot tell
  in from out), a subsystem as `component … <<subsystem>> { }`, `interface`, `package`; connectors `a --> b` between the
  port aliases when they name ports, realization `..|>`, usage `..> : <<use>>`, dependency `..>`. Checked with PlantUML 1.2026.8
  (`tests/test_plantuml_component.py`).
- Project model: `component` links to `component`, `system` or `subsystem`, `subsystem` to `subsystem` or `system`; both are
  adopted when created; `interface` and `package` are not linkable. A key that a typed field of the type owns on a kind without
  that field is an import error (`graph_types.field_errors`).
- Relation links (ADR-0181): `component` is in `model.RELATION_DIAGRAM_TYPES`; `connection` shows `communicates`/`data_flow`,
  `dependency` shows `communicates`, `usage`/`realization` are never linked (`project_model.relation_kinds_for`,
  `view_connections`); `TYPE_OPERATIONS["component"]` has `RELATION_OPERATIONS` and `set_completeness`; `relation-not-shown`
  considers deployment and component diagrams (`tests/test_component_relations.py`).
- Tools (`agent_types._graph_builder`): the graph tools with component descriptions, the kind enums and `fields` (`ports` on
  nodes, the port names on edges); prompt `arcviz/prompts/types/component.txt`. Template `webshop-components`.

## Sequence diagrams

Design: [sequence diagrams spec](../architecture/specs/sequence-diagrams.md), ADR-0035. Source:
`arcviz/sequence.py` (model, queries, integrity), `arcviz/sequence_ops.py` (operations),
`arcviz/sequence_svg.py` (renderer). Tests: `tests/test_sequence.py`, property tests.

- Model: `Diagram.interaction: Interaction | None`; a validator creates it for `sequence` and rejects it
  for other types. `Interaction` = `lifelines` (left to right), `fragments` (top to bottom; a
  discriminated union on `element`: `message`, `combined_fragment`, `interaction_use`,
  `destruction_occurrence_specification`; combined fragments hold `operands`, which hold `fragments`),
  `executions`. Enumerations (`MESSAGE_SORTS`, `MESSAGE_KINDS`, `INTERACTION_OPERATORS`,
  `MULTI_OPERAND_OPERATORS` = alt/par/strict/seq, `MESSAGE_SET_OPERATORS` = ignore/consider) use the UML
  literals; `canonical()` matches input case- and underscore-insensitively.
- `sequence.integrity_errors()` is part of `Diagram.integrity_errors()` (so the store refuses broken
  interactions): unique non-empty ids across the interaction, known enumeration values, message kind
  consistent with its ends, known lifelines, non-empty known `covered`, operand counts, `messages` only on
  ignore/consider, one destruction per lifeline (a deleteMessage destroys its receiver), no message
  involving a lifeline after its destruction, executions on known lifelines whose start/finish messages
  exist, involve the lifeline and are in order.
- Operations (`TYPE_OPERATIONS["sequence"]`): `add_lifeline`, `update_lifeline`, `move_lifeline`,
  `remove_lifeline`, `add_message`, `update_message`, `add_combined_fragment`, `update_combined_fragment`,
  `add_interaction_operand`, `update_interaction_operand`, `add_interaction_use`, `update_interaction_use`,
  `add_execution_specification`, `add_destruction_occurrence_specification`, `move_fragment`,
  `remove_fragment`, `rename_diagram`, `apply_batch`. They return `{"element", "touched"}` (removals:
  `{"removed", "touched": []}`); `Diagram.has_element(id)` covers interaction ids, and batch/proposal
  `touched` lists use it. `add_message`, `update_message`, `add_combined_fragment` (with `contents`),
  `add_destruction_occurrence_specification` and `move_fragment` re-check the time order (executions,
  destructions) after the change and raise
  `OperationError` (rolled back by `apply`). Generated ids: slug of the name for lifelines; `m`, `cf`,
  `op`, `ref`, `x`, `ex` plus the first free number.
- Summary (`sequence.summary_lines`): lifelines with ids (and their notes, cut like node notes), the fragment
  tree with ids, operands with guards, executions. `get_diagram` returns `{"id", "title", "type", "interaction", "partial": false}`.
- Rendering (`render.SequenceSvgRenderer` → `sequence_svg.render(diagram, highlight, marks)`): one `<svg
  class="arcviz-seq">` with a scoped `<style>` (light and `prefers-color-scheme: dark` colours), two
  markers (`arcviz-seq-filled`, `arcviz-seq-open`, `markerUnits=userSpaceOnUse`), and layers
  `lifelines`, `executions`, `frames`, `messages`. Every element is `<g data-id data-element class>`;
  highlight adds `changed`, marks add `added`/`removed`/`changed`. Message classes `message sort-<sort>
  [kind-<kind>]`; line class `msg` with `marker-end` and `stroke-dasharray` for reply and createMessage;
  `hit` lines widen the click target; found/lost messages have a `dot` circle; deleteMessage and
  destructions draw `destroy` crosses and end the lifeline. A createMessage that is the first message of
  its receiver draws the receiver's head at its height. Text widths are estimated (`text_width`); the
  `viewBox` is the bounding box of everything drawn plus 16 px. All text goes through `escape()`.
- `/api/config` adds `message_sorts`, `message_kinds`, `interaction_operators`,
  `multi_operand_operators` to the sequence type (`server.TYPE_EXTRAS`).
- `proposal.diagram_diff` adds `elements: {added, removed, changed}` (`{id, element, description}`);
  position changes count relative to the elements both versions share.
- Template `checkout`.

## Exchange

Deployment documents are written with `schema_version` 1 (older Arcviz versions read them and ignore
`type`), other types with 2 (`SCHEMA_VERSION`, the newest version read).

## Fingerprints

`store.fingerprint` omits `type` when it is `deployment` (as it omits the default direction), so
fingerprints of versions stored before diagram types existed are unchanged.

## Frontend

`types.js` (pure): `typeConfig(config, type)` (falls back to the top-level kind lists of an older
server), `viewOf(payload)` → `{renderer, source}`, `TYPE_LABELS`, `unsupportedTitle(type)`,
`templateChoices(config, type, labels)`. `state.type` is the shown diagram's type; `ui.applyFeatures()`
disables the view selector, direction selector, comparison button, PlantUML download and Mermaid copy
when the type does not support them (tooltip: "Für … noch nicht verfügbar") and is called by
`setBusy` and `showDiagram`. `canvas.renderView(view)` renders either format (see
[Frontend](frontend.md#rendering)).
