# State machine diagrams

- Status: Implemented
- Issue: #244
- ADR: [0178 State machines: simple and composite states, regions as containers](../adr/0178-state-machines-simple-and-composite-states.md)
- Shared design: [More UML diagram types](uml-graph-types.md) (ADR-0177, 0182..0185)

## Summary

A diagram type `state_machine` following the UML 2.5.1 **State Machines** chapter (14): states (simple and composite),
orthogonal regions, pseudostates, final states and transitions with trigger, guard and effect. It describes the
lifecycle of one thing (an order, a session, a device): the behaviour counterpart to the activity diagram, which
describes a flow of work. It is a graph, so it reuses the node/edge operations through a graph profile
(shared design) and renders as a Mermaid flowchart.

## Goals

- The metaclasses of the table below as kinds; transitions with `trigger [guard] / effect`; entry, do and exit
  behaviours of states.
- Composite states with one implicit region or with orthogonal regions.
- Local well-formedness rules in the operations and the importer; review findings for the usual modelling mistakes
  (no initial state, unreachable states, nondeterminism).
- Everything generic works (shared design).

## Non-goals

- Submachine states, entry/exit points, connection point references, deferred triggers, internal and local transitions
  as edges (internal behaviour is the `entry`/`do`/`exit` fields), state invariants, protocol state machines, time and
  change events as structured values (they are text in the trigger).
- Top-level orthogonal regions (the diagram is one implicit region; concurrency needs a composite state).
- Simulation or code generation.
- Linking states or transitions to project elements (ADR-0182; `project_model.NOT_LINKED_TYPES`); a diagram-level link to
  the described class or component is a follow-up (shared design, open question 2).

## Design

### Kinds (`model.STATE_MACHINE_KINDS`)

| Kind | UML 2.5.1 metaclass | Container | Notation (Mermaid) | Fallback |
|---|---|---|---|---|
| `state` | State, simple (14.2) | no | rounded rectangle: name, then `entry / …`, `do / …`, `exit / …` lines below a rule | – |
| `composite_state` | State with one region or several | yes | subgraph labelled with the name | – |
| `state_region` | Region, one of several orthogonal regions | yes (parent: `composite_state` only) | dashed subgraph, label = name | – |
| `initial_pseudostate` | Pseudostate initial | no | filled circle `f-circ` | – |
| `final_state` | FinalState | no | bull's eye `fr-circ` | – |
| `terminate_pseudostate` | Pseudostate terminate | no | circle with a cross `cross-circ` | – |
| `choice_pseudostate` | Pseudostate choice | no | diamond `diam`, no text | – |
| `junction_pseudostate` | Pseudostate junction | no | small filled circle `sm-circ` | `f-circ` |
| `fork_pseudostate` | Pseudostate fork | no | bar `fork` | – |
| `join_pseudostate` | Pseudostate join | no | bar `fork` | – |
| `shallow_history_pseudostate` | Pseudostate shallowHistory | no | circle labelled `H` | – |
| `deep_history_pseudostate` | Pseudostate deepHistory | no | circle labelled `H*` | – |

Edge kind: `transition` (Transition, external kind), drawn as a solid arrow; always directed.

A state becomes composite by changing its kind (`update_node(kind="composite_state")`); the other direction needs
no children. This keeps `Node.is_container` kind-based (ADR-0178).

### Typed fields (ADR-0177)

- On `state` only: `entry`, `do`, `exit`, each one line of at most 120 characters (the behaviours as free text, UML
  OpaqueBehavior). A composite state has no behaviour fields: its subgraph title is one line (Mermaid clips multi-line
  titles) and the behaviours of a state that becomes composite are dropped with the kind change.
- On `transition`: the **trigger** is the edge `label` (UML: the label's first part names the trigger events, `after 30s`
  and `when x > 3` are text); `guard` (the existing property key, here for state machines too); `effect`.
  Drawn as `trigger [guard] / effect`, each part optional.

### Rules (profile `edge_problem`, `parent_problem`, `rule_errors`)

Hard rules, checked by `add_edge`, `update_edge`, `add_node`, `update_node`, `move_node` and the importer with one function:

1. A `state_region` is not a vertex: no transition starts or ends at it.
2. No transition ends at an `initial_pseudostate`; none starts at a `final_state` or `terminate_pseudostate`.
3. A transition that starts at an `initial_pseudostate` or a `fork_pseudostate`, or ends at a `join_pseudostate`,
   has no trigger (`label`) and no guard.
4. A `state_region` is a child of a `composite_state` only. If a composite state has a region child, all its children
   are regions and every state, pseudostate or final state belongs to a region (so adding a state to such a composite
   state names the region as parent).
5. Only `composite_state` and `state_region` have children; a pseudostate or final state is a child of a
   `composite_state`, a `state_region` or of nothing (top level = the implicit region of the machine).
6. Transitions are directed: `direction` must be unset.

7. `remove_node` without `cascade` is refused when the content that moves up a level would break rule 4 (a region would
   become top level, a state would sit next to regions).

Not hard rules (a half-built machine must be allowed): one initial state, reachability, determinism. They are findings.
The functions are `state_machine.edge_problem`, `node_problem` and `rule_errors`.

### Review findings (`rules.RULES["state_machine"]`, German messages)

| Id | Category / severity | Condition |
|---|---|---|
| `state-no-initial` | suggestion / low | a scope (top level, composite state, region) holds a state and no initial pseudostate |
| `state-multiple-initial` | risk / medium | a scope holds more than one initial pseudostate |
| `state-initial-targets` | risk / medium | an initial pseudostate has no or more than one outgoing transition |
| `state-unreachable` | risk / medium | a state, pseudostate or final state not reachable from an initial pseudostate of its scope or from a transition into a state of an enclosing scope |
| `state-dead-end` | suggestion / low | a state that is not final has no outgoing transition, though the machine has a final state |
| `state-nondeterministic` | risk / medium | two transitions out of one state with the same trigger where at least one has no guard, or two unguarded transitions without trigger |
| `state-choice-without-else` | risk / medium, low when every transition has a guard | a choice pseudostate whose outgoing transitions include no `else` guard (reuses `model.is_else_guard`) |
| `state-crosses-regions` | risk / low | a transition connects states of two different regions of one composite state without a fork or join |
| `state-final-unreachable` | suggestion / low | a final state without incoming transition |

Findings name node ids (`elements`) and edge ids (`edges`). Linear in nodes and edges (BFS and one pass over edges). They
live in `arcviz/state_rules.py`, which registers them for the type and is imported at the end of `rules.py`.

### Rendering

`mermaid.py` gets `_NODE_SHAPES["state_machine"]` and class styles for the kinds above (UML black control symbols, light
rounded states, dashed region frames). Labels use `escape_text` and, inside `@{ … }`, `shape_label`. Edge text is
`trigger [guard] / effect` through `edge_text` (profile-driven, shared with the activity guard). Swimlane layout is not
used. Focus works on composite states and regions.

### Exchange: PlantUML (`plantuml_graph.state_machine`)

`@startuml`; a named state `state "Name" as n_id` for every vertex (the ids of `mermaid.id_map`, like the deployment writer);
pseudostates as stereotypes: `<<start>>` (initial), `<<end>>` (final), `<<history>>` and `<<history*>>`, `<<choice>>`,
`<<fork>>`, `<<join>>`; behaviours as `n_id : entry / x`; a composite state as `state … { }` with `--` between the bodies of
its regions, each introduced by the comment `' region: name`; every transition at the end as
`A --> B : trigger [guard] / effect`. Terminate and junction have no stereotype of their own: a terminate pseudostate is an
`<<end>>` and a junction a `<<choice>>`, each with the comment line `' terminate: name` / `' junction: name` before it
(a trailing comment is part of the line for PlantUML), and the limitation is stated in the guide. Text through `plain`;
nesting bounded by `MAX_PLANTUML_NESTING`.

Checked against PlantUML 1.2026.8 (`tests/test_plantuml_state_machine.py` with `ARCVIZ_PLANTUML_JAR`), which changed the
first sketch of this section: it cannot address a state of a second concurrent region by its alias, and refuses `[*]`
and `[H]` in several places, so the writer uses stereotyped states and writes a state inside a composite state as
`<outermost composite alias>.<alias>`.

### Tools and prompt

Tools are the graph tools with state-machine descriptions (`agent_types._graph_builder`, shared by the graph types): `add_node`
(kind enum of the table, `fields` with `entry`/`do`/`exit`), `add_edge`/`update_edge` (`label` = trigger, `guard`,
`fields.effect`, no `direction`). The `fields` schema lists the fields of all kinds of the type; the operation checks them
per kind. The prompt
(`prompts/types/state_machine.txt`) explains the notation and rules, starts by asking *which thing's lifecycle*, its
states, events and the end condition, and shows examples (a choice with guards, a composite state with a history
pseudostate).

### Template (`order-lifecycle`)

Initial → *Neu*; *Neu* → (*bearbeiten*) the composite state *In Bearbeitung* with two regions (*Zahlung*:
*Zahlung offen* → (*Zahlung eingegangen*) *Bezahlt* [entry / Rechnung senden]; *Kommissionierung*: *Ware wird gepackt* →
(*Paket bereit*) *Versandbereit*), both regions leaving into a join pseudostate at the top level → *Versandt* →
(*zustellen*) *Zugestellt* [do / Zustellung bestätigen] → final; *Neu* → (*stornieren*) *Storniert* and *In Bearbeitung* →
(*stornieren* [Frist nicht abgelaufen] / Erstattung) *Storniert*, *Storniert* → final. A join pseudostate is a
top-level node because a composite state with regions holds regions only (rule 4).

### Frontend

`typeConfig` gives the kinds, `node_fields` (`entry`, `do`, `exit` for `state`) and `edge_fields` (`effect`). The inspector
keeps the fields as rows of its property table, where `propertyChanges(rows, current, typed)` sends them as `fields`
instead of free properties; a dedicated input per field comes with the first list-valued field (class diagrams). The
"Direction" selector is hidden (`features.edge_direction` false, as for activity).

## Testing

- `tests/test_state_machine.py`: kinds; every hard rule in operations and importer with the same verdict; kind change
  from `state` to `composite_state` and back; typed fields and the reserved keys; Mermaid shapes, labels with hostile
  names, trigger/guard/effect text; tools and prompt; template; PlantUML text for each construct and the JAR test
  (initial, final, choice, fork/join, composite with regions, history).
- `tests/test_rules_state_machine.py`: one test per finding id (positive and negative), linearity on a large machine.
- Property tests: strategies draw states, pseudostates, regions and transitions that satisfy the hard rules; the checks
  assert integrity, no import-rule errors, Mermaid structure and the PlantUML export (balanced braces, one line
  per transition).
- Comparison: a changed guard marks the edge; an added state is marked added.
- e2e: create from the template, rename a state with a hostile name, edit a guard in the inspector.

## Migration / compatibility

New type only.

## Open questions

None specific to this type; see the shared design.

## Decisions

- Simple and composite states are two kinds; regions are containers (ADR-0178, Accepted).
- The trigger is the edge label; guard and effect are typed edge fields (ADR-0177, Accepted).
