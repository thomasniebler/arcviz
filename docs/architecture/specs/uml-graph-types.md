# More UML diagram types: shared design

- Status: Accepted
- Issues: #243 (foundation), #244 (state machine), #245 (class), #246 (use case), #247 (component)
- ADRs: [0183 Graph types behind one profile table](../adr/0183-graph-types-behind-one-profile-table.md),
  [0184 New graph types render as Mermaid flowcharts](../adr/0184-new-graph-types-render-as-mermaid-flowcharts.md),
  [0185 Kind names may be shared across types](../adr/0185-kind-names-may-be-shared-across-types.md),
  [0177 Typed node and edge fields are stored as properties](../adr/0177-typed-node-and-edge-fields-are-stored-as-properties.md),
  [0182 Which new view elements link to the project model](../adr/0182-which-new-view-elements-link-to-the-project-model.md)
- Type specs: [State machine](state-machine-diagrams.md), [Class](class-diagrams.md), [Use case](use-case-diagrams.md),
  [Component](component-diagrams.md)
- Builds on: [Diagram types](diagram-types.md) (ADR-0030, 0031, 0037), [Activity diagrams](activity-diagrams.md) (ADR-0034),
  [Shared project model](shared-project-model.md), [Elements by default](elements-by-default.md),
  [Agent access](agent-access.md)

## Summary

Arcviz draws three UML diagram types today (deployment, activity, sequence). Requirements engineers, architects and, over
MCP, developers need four more: **state machine**, **class**, **use case** and **component** diagrams. All four are graphs
of nodes and edges, so they reuse the node/edge operations, the Mermaid renderer, the exchange formats, the version
comparison, propose mode, batch, undo/redo and the MCP tools exactly as activity diagrams do. What is new is a small
mechanism that lets a type declare its vocabulary, its well-formedness rules and its typed fields in one place, so the
four types do not add a fifth, sixth, seventh and eighth `if diagram.type == "activity"` to the code.

This page holds what the four types share. Each type has its own spec.

## Goals

- Four new `Diagram.type` values: `state_machine`, `class`, `use_case`, `component`, each following UML 2.5.1
  (ADR-0030): state machines (chapter 14), classes (11), use cases (18), components (11 and 8.3 for ports).
- Every generic feature works for the new types or answers 400 naming the type (the rule of
  [diagram-types.md](diagram-types.md)): operations, batch, propose mode, versions, undo/redo, comparison, JSON and
  PlantUML export, JSON import, templates, review rules, chat tools and prompt, MCP, Git mirror, project exchange.
- UML well-formedness rules that are cheap to check run in the operations and in the importer, as for activity diagrams.
- Cross-diagram value: elements that mean the same thing as a project element link to it (ADR-0182); nothing else
  about the project model changes in stage 1.
- Activity diagrams keep byte-identical Mermaid output, fingerprints, exports and tool schemas.

## Non-goals

- Package, object, composite structure, communication, interaction overview, timing and profile diagrams.
- Changing a diagram's type or converting between types.
- Exact UML notation (hollow triangles, diamonds, lollipops, stick figures): see ADR-0184 and *Rendering*.
- A model-driven link from a state machine to the class or component it describes, and traceability from use cases to
  drivers, scenarios or components (follow-up issues, see *Open questions*).
- Extending the new vocabularies from the kinds file (ADR-0037 stays: deployment only).

## Design

### Types

`model.DIAGRAM_TYPES = ("deployment", "activity", "sequence", "state_machine", "class", "use_case", "component")`.

| Type | UML 2.5.1 | Content | Renderer | German label |
|---|---|---|---|---|
| `state_machine` | 14 State Machines | states, pseudostates, regions; transitions | Mermaid flowchart | Zustandsdiagramm |
| `class` | 11 Classification (classes, interfaces, enumerations, associations, generalizations) | classifiers with members; relationships | Mermaid flowchart | Klassendiagramm |
| `use_case` | 18 Use Cases | actors, use cases, subject; associations, include, extend, generalization | Mermaid flowchart | Anwendungsfalldiagramm |
| `component` | 11 (Components) and 8.3 (Ports, Interfaces) | components, subsystems, interfaces, packages; connectors, realizations, usages | Mermaid flowchart | Komponentendiagramm |

The wire names are snake_case type ids (`state_machine`, `use_case`); `UML_REFERENCES` and `/api/config`
`diagram_types[type].uml` carry the chapter. The UI shows the German label.

### Graph profiles (`arcviz/graph_types.py`, ADR-0183)

Today the activity type is wired in by conditions: `operations.GUARD_TYPES`, `DIRECTED_TYPES`, `DEFAULT_NODE_KINDS`,
`DEFAULT_EDGE_KINDS`, `_check_edge_rules`, `_check_kind_change`, `model.activity_edge_problem`, `exchange._rule_errors`,
`mermaid.edge_text`, `mermaid._label`. A new module `arcviz/graph_types.py` (domain layer, imports only `model`) replaces
them with one registry of **profiles** of the types whose content is `Diagram.nodes` and `Diagram.edges`:

```python
@dataclass(frozen=True)
class Field:                       # a typed value stored as a property (ADR-0177)
    name: str                      # the property key and the key in the `fields` argument
    max_items: int = 1             # 1 = one line of text, >1 = a list of lines (stored newline-joined)
    max_length: int = 120          # per line, after single_line()

@dataclass(frozen=True)
class GraphProfile:
    type: str
    kinds: KindCatalog
    default_node_kind: str
    default_edge_kind: str
    directed_noun: str | None = None                 # set: edges are always directed (`direction` must be unset)
    guard: bool = False                              # edges take the top-level `guard` argument (ADR-0034)
    node_fields: Mapping[str, tuple[Field, ...]] = {}    # by node kind
    edge_fields: Mapping[str, tuple[Field, ...]] = {}    # by edge kind
    node_problem: Callable[[Diagram, Node], str | None]  # why this candidate node may not have this kind/parent
    edge_problem: Callable[[Diagram, Edge], str | None]  # why this candidate edge may not join its ends
    rule_errors: Callable[[Diagram], list[str]]      # whole-diagram rules of an import

PROFILES: dict[str, GraphProfile]                    # activity + the four new types
def profile_of(diagram_type) -> GraphProfile | None  # None for deployment (configurable catalogue) and sequence
```

`default_direction` (layout direction of a new diagram; use case and component start at `LR`) arrives with its first user.
The automatic edge text (`«realizes»`, `«include»`, …) is `model.EDGE_STEREOTYPES`, a table by edge kind rather than a profile
member, because kind names are global (ADR-0185). Typed fields have two more options than the sketch shows: `flag` (true or
absent) and `check` (a function that says why a text is not allowed, used for multiplicities).

- `operations.add_node/update_node/add_edge/update_edge` look the profile up instead of testing for `activity`:
  the default kinds, the typed fields (`guard` and the `fields` argument below; a field that the type or kind does
  not define is an `OperationError` naming the type), `edge_problem` and `parent_problem` (also on `move_node`),
  and, on a kind change, the rules re-checked for the edges and children that stay.
- `exchange._rule_errors` calls `profile.rule_errors` and `graph_types.field_errors` (stored typed fields within their bounds, one-line text) plus the generic checks (kinds of the type, parents, edge kinds).
- `rules.RULES[type]` (review findings) stays separate: findings are advice, profile rules are hard rules.
- The activity type registers a profile with its current behaviour (`guard`, directed, `activity_edge_problem`,
  partition parents). **Acceptance: every existing activity, deployment and sequence test passes unchanged, and the
  golden outputs listed under *Testing* are byte-identical.** This refactor is the first commit and lands alone.
- Deployment keeps the configurable catalogue and its code path; it has no profile. Sequence has its own model.

### Kinds

Each type's vocabulary is a closed `KindCatalog` in `TYPE_KINDS`, with UML metaclass names in snake_case (ADR-0030).
Names may repeat across types when they denote the same UML metaclass with the same container-ness (ADR-0185); the
notation may depend on the diagram type (an `interface` is a classifier box in a class diagram and a circle in a
component diagram), the colour class is per kind: `interface` (class, component), `component` (deployment's loose `component`, component), `association`,
`dependency` (deployment, class, use case, component), `generalization` (class, use case), `realization` (class,
component), `package` (class, component). `Node.is_container` stays kind-based and context-free.

### Typed fields (ADR-0177)

A type may give a node kind or edge kind typed values, stored as properties (the pattern of the activity `guard`).
They are passed in one new optional argument `fields` (an object, `{"attributes": ["- id: UUID"]}`) of `add_node`, `update_node`,
`add_edge` and `update_edge`; the profile validates it, the per-type tool schema lists exactly the allowed keys of the kind
(`additionalProperties: false`), so the model sees them and the REST `/ops` and MCP proposals use the same shape. `guard`
stays the top-level argument it is for activity diagrams (golden tool schemas) and is also the top-level argument of
state machines; every other typed field goes through `fields` (ADR-0177):

| Type | Node fields | Edge fields |
|---|---|---|
| activity (unchanged) | – | `guard` |
| state_machine | `entry`, `do`, `exit` on `state` | `guard`, `effect` on `transition` (the trigger is the edge `label`) |
| class | `attributes`, `operations` on `class`/`interface`/`data_type`, `literals` on `enumeration`, `abstract` on `class` | `source_multiplicity`, `target_multiplicity`, `source_role`, `target_role` on the association kinds |
| use_case | – | `extension_point`, `condition` on `extend` |
| component | `ports` on `component` | `source_port`, `target_port` on connectors, `protocol` stays a free property |

A reserved key cannot be set through `properties`/`remove_properties`: the error names the typed field
(`use fields.attributes`). A list value replaces the stored list; an empty list or an empty string removes the field. The inspector shows typed fields as dedicated inputs (a textarea for a list) and keeps the free
property editor for the rest. Diff, summary, fingerprint and export see them as properties.

### Rendering (ADR-0184, `mermaid.py`)

All four render as Mermaid flowcharts through `MermaidRenderer`; `render.RENDERERS` gets four more rows that point at it.
The node notation per kind comes from the profile's tables (`_NODE_SHAPES` by type, like `_ACTIVITY_SHAPES`), edge ends from
`_EDGE_LINES` and `_CIRCLE_ENDS`, automatic edge texts from `model.EDGE_STEREOTYPES`. Everything the flowchart pipeline offers works unchanged: focus on containers, layout direction, highlight,
`mermaid_ids`, comparison marks, hostile names shown literally (`escape_text`, `shape_label`).
UML notation is approximated where Mermaid has no equivalent; each type spec lists its table. Anything the
browser's SVG sanitiser would drop (`<i>`, `<b>`, `<u>`) is not used: emphasis is a «stereotype» line.

**Rendering probe (done while writing these specs, vendored Mermaid 11.17.2 in the Playwright Chromium).** Available:
`f-circ`, `fr-circ`, `cross-circ`, `sm-circ`, `diam`, `fork`, `circle` with a label, `stadium`, `rounded`, `hex`;
multi-line labels with `<br/>` and a rule of `U+2500`; edge text with `«stereotypes»`; target-end markers `-->`, `--o`,
`--x`, thick `==o`, dotted `-.->`, and `o--o`/`x--x`/`<-->` on both ends. **Not available:** `ellipse` and `oval` shapes
(use cases are drawn as stadiums), markers at the *source* end only (`o--`, `x--`, `<--` are syntax errors, so
aggregation and composition point from part to whole), hollow triangles and diamonds (the circle marker renders
filled), left-aligned label lines (compartments are centred), a stick figure. The first implementation task repeats the
probe in `tests/e2e` as a rendering test per notation row so a Mermaid upgrade that changes it fails visibly.

### Operations

`TYPE_OPERATIONS[type] = _GRAPH_OPERATIONS` for all four new types (state machines too: every type offers the model-link
operations, `test_diagram_types` enforces it, they just never find a linkable kind there), plus the type's own additions only if its spec
lists them (none do). `RELATION_OPERATIONS` and `set_completeness` for `component` only (see its spec).
`operations.apply` rejects other types' operations as before; `apply_batch` enum per type.

### Rules (review findings)

Each type registers findings in `rules.RULES[type]` (German messages, categories strength/risk/suggestion, element and
edge ids), pure and linear in nodes and edges like the activity rules. The type specs list them. Project review
(`project_review.py`): `PROJECT_RULES` that look at diagram types treat `state_machine` and `use_case` as behaviour
(`BEHAVIOUR_TYPES`) and `class` and `component` as structure; the "missing diagram types" rule does not demand any
of the new types.

### Agent, prompts and MCP

- `agent_types.py` builds the tool list of each type from its profile (kind enums, typed arguments, UML descriptions);
  `apply_batch` lists the type's operations; `get_diagram` returns the type-specific JSON.
- `arcviz/prompts/types/<type>.txt` per type: notation, modelling guidance, guided-start questions, examples (the
  conversation start of a requirements engineer differs from an architect's: the use-case and class prompts ask
  for goals, actors and glossary terms before drawing). `tests/test_prompts.py` covers every type.
- `AgentTools`/MCP need no new tool: `list_operations`-style tool lists follow `TYPE_OPERATIONS` and the profile, and
  proposals (`propose_change`) validate through `operations.apply`. `mcp_server` docstrings name the new types.
  Code links (`add_code_ref`) work on project elements, so a linked component or class is reachable for developers.

### Exchange (`exchange.py`, new `arcviz/plantuml_graph.py`)

- JSON: `type` is carried, `schema_version` 2 as for every non-deployment type (older Arcviz refuses the document).
  `parse_document` checks the profile's rules and the typed fields (bounds, key reservations).
- PlantUML: one writer per type in `arcviz/plantuml_graph.py`, imported by `exchange` (the module keeps `exchange`
  from growing past 1 500 lines); all text through `_plain(text, reserved)`; nesting bounded by
  `MAX_PLANTUML_NESTING`; limitations documented in each spec (for example terminate pseudostates, qualified
  associations). `PLANTUML_TYPES` lists all seven types. `ARCVIZ_PLANTUML_JAR` renders the constructs in
  `tests/test_plantuml_behaviour.py`.
- Project bundles embed the diagram documents unchanged.

### Version comparison

`diff.SUPPORTED_TYPES` lists all types; the node/edge comparison works as for activity diagrams. Changed typed
fields mark the node or edge as changed.

### Templates and UI

- One German starter template per type (listed in each spec), `TEMPLATE_TYPES` rows, `tests/test_templates.py` rows.
- `types.js`/`typeConfig` carry `node_fields`/`edge_fields` and the kinds; the new-diagram dialog lists the types in
  the order deployment, component, class, use case, activity, state machine, sequence (structure first); the
  inspector renders typed fields; features that a type does not support are disabled with a tooltip as before.
- New pure helpers go into pure modules with tests (`tests/js/`); `modules.test.mjs` `LAYERS` is updated if a module
  is added. `docs/guide/` gets one page per type, `docs/guide/elements.md` the new kinds.

### Configuration

None. `ARCVIZ_KINDS_FILE` and `ARCVIZ_KIND_PACKS` keep extending deployment diagrams only; their reserved names now include
the new built-in kinds (`kinds.BUILTIN` is computed from `TYPE_KINDS`).

### Error handling

Unknown type: 400 listing the types. Operation outside the type: `OperationError`. Reserved property key, field out of
bounds, edge or parent rule violation: `OperationError` with the allowed alternatives (messages are written for the model).
A feature a type cannot support: 400 naming the type and the follow-up issue.

## Delivery

One topic per issue and per commit (CLAUDE.md rule 6; this session works on one branch, one PR):

| Order | Topic | Contents |
|---|---|---|
| 0 | Foundation | `graph_types.py` with the activity profile (no behaviour change), the `fields` argument, the shared-kinds rule (ADR-0185), roadmap and ideas update, rendering test |
| 1 | State machine | this design's first type: smallest vocabulary, exercises regions, typed edge fields, rules |
| 2 | Class | typed list fields, multiplicities, many edge kinds |
| 3 | Use case | subject container, include/extend, requirements-engineering prompt |
| 4 | Component | ports, interfaces, relation links to the project model |

Each commit carries code, tests, spec/ADR status, `docs/spec/`, `docs/reference/`, `docs/guide/` and `make docs`.

## Testing

- `tests/test_diagram_types.py` (existing): every table covers every type; kind-name rule of ADR-0185 (disjoint
  except the allow-list, same container-ness); defaults and unchanged fingerprints of deployment diagrams.
- `tests/test_graph_types.py` (new): profile registry; typed arguments (accepted, bounds, reserved keys, wrong type);
  parent and edge rules in operations and in the importer give the same verdict (shared function).
- Golden tests before the refactor: Mermaid source, fingerprint, JSON export, PlantUML export, tool schemas and prompt of
  the activity template and the deployment templates are captured in `tests/fixtures/` and compared after it.
- Per type: kinds, rules, Mermaid shapes with hostile names, tools and prompt, template, PlantUML export (and the JAR
  test when `ARCVIZ_PLANTUML_JAR` is set), comparison, exchange round trip, review rules, MCP operation listing.
- Property tests: `tests/strategies.py` draws operations of every new type; the structural checks assert
  integrity, no import-rule errors and Mermaid structure (ids, subgraph balance, one line per edge).
- Evals: `tests/evals/cases.json` gets cases per type (offline structure check in `tests/test_evals.py`; real runs
  are manual: `make eval`).
- JS: `types.test.mjs`, typed-field helpers, `modules.test.mjs`.
- e2e: per type create from the template, check the rendering, edit a typed field in the inspector, hostile name
  shown literally; the smoke test never names a created id (runs with `ARCVIZ_E2E_REAL_IDS=1`).
- Local gate for every commit: `make lint typecheck test test-js e2e docs`. GitHub CI is not used for this work.

## Migration / compatibility

New types only. Stored data, fingerprints, exports and tool schemas of the existing types do not change (golden tests).
`DIAGRAM_TYPES` gains values: an older Arcviz refuses documents of the new types (`schema_version` 2 rule) instead of
loading them as deployment diagrams. No store change, no migration.

## Open questions

1. **Traceability use case → driver/scenario/component.** Valuable for requirements engineers, but it adds a link kind to
   the project model (ADR-0166 territory). Proposed: follow-up issue after stage 4.
2. **State machine owner.** A state machine describes the behaviour of a class or component. A diagram-level
   `context` link to a project element would let the review check events against operations. Proposed: follow-up.
3. **Adopt classes into the project model by default?** Proposed: no (ADR-0182), they are linkable and adoptable on request.
4. **Interface notation.** Lollipop and socket are not drawable in a flowchart; the proposal draws an `interface` node
   with realization (provided) and usage (required) edges. If the spike shows a usable approximation, the component spec
   says so.

## Decisions

Accepted by the maintainer (2026-10-20); the roadmap decision (diagram types move from *Parked* into the roadmap) and
the rendering decision (Mermaid flowcharts for all four types) are taken: see ADR-0183 and ADR-0184.
