# Diagram types

- Status: Implemented (milestone M13)
- Issue: #98
- ADRs: [0030 UML 2.5.1 as the reference for diagram types](../adr/0030-uml-2-5-1-as-the-reference-for-diagram-types.md),
  [0031 Diagram type as a field with per-type tables in each layer](../adr/0031-diagram-type-field-with-per-type-tables.md),
  [0032 Rendering of diagram types in M13](../adr/0032-rendering-of-diagram-types-in-m13.md) (implements [ADR-0038](../adr/0038-renderer-per-diagram-type.md), based on the [renderer case study](renderer-case-study.md)),
  [0033 Render payload names its renderer](../adr/0033-render-payload-names-its-renderer.md),
  [0037 Configurable kinds extend deployment diagrams only](../adr/0037-configurable-kinds-extend-deployment-only.md)
- Builds on: [Configurable element kinds](configurable-kinds.md), [Diagram import and export](diagram-exchange.md),
  [Visual comparison of two versions](version-comparison.md), [Frontend split into ES modules](frontend-modules.md)

## Summary

Every diagram has been a UML-style deployment diagram. This change gives a diagram a **type**
(`deployment`, `activity`, `sequence`). Each type brings its own kind catalogue, operation set,
validation rules, tool schemas, system prompt, renderer and starter templates. All types take
UML 2.5.1 as their reference (ADR-0030); existing diagrams are of type `deployment` and keep working
unchanged, including their stored version fingerprints.

The type is a field on the one `Diagram` model. Each layer keeps a table keyed by the type
(kinds in `model`, operations in `operations`, renderers in `render`, tools and prompts in the agent,
rules in `exchange`); a test checks that every table covers every type (ADR-0031). A renderer
interface hides how a type is drawn; responses that carry a rendering name the renderer
(ADR-0032, ADR-0033). Features a type cannot support yet fail with a clear message instead of
producing something half right.

## Goals

- A diagram type chosen at creation, stored with the diagram and immutable afterwards.
- Per type: kind catalogue, operations, rules, tools, system prompt, renderer, templates, and the
  set of UI features that apply (focus, layout direction, version comparison, PlantUML export).
- One mutation path: `operations.apply` with one `OPERATIONS` table; an operation outside the type's
  set is rejected like an unknown operation.
- Batch, propose mode, undo/redo, versions, import/export, the Git mirror and the property tests work
  for every type, or fail with a message naming the type and the follow-up issue.
- Backward compatibility: stored diagrams without a type load as `deployment`; their fingerprints,
  exports and Mermaid output do not change.

## Non-goals

- Changing a diagram's type, or converting between types.
- Further UML diagram types (class, state machine, use case, component). The tables make them
  additive.
- Renaming the deployment kinds to UML deployment terms (ADR-0030).
- Extending activity or sequence vocabularies from the kinds file (ADR-0037).

## Design

### Types

`model.DIAGRAM_TYPES = ("deployment", "activity", "sequence")`; the first is the default.

| Type | UML 2.5.1 chapter | Content | Renderer |
|---|---|---|---|
| `deployment` | 19 Deployments (loosely, see ADR-0030) | nodes (containers and leaves) and edges | Mermaid flowchart |
| `activity` | 15 Activities | ActivityNodes and ActivityPartitions as nodes, ActivityEdges as edges | Mermaid flowchart |
| `sequence` | 17 Interactions | an `Interaction`: Lifelines and an ordered fragment tree | own SVG |

### Model (`model.py`)

- `Diagram.type: str = "deployment"`. A validator rejects unknown types (ValidationError, so the store
  and the importer report it) and creates the type's content (`Interaction` for sequence diagrams).
- Type-specific content lives in its own optional field; `Diagram.interaction` (sequence only, see
  the [sequence spec](sequence-diagrams.md)) is `None` for the other types.
- Kind catalogues per type: `kinds_for(diagram_type) -> KindCatalog`. `deployment` returns the
  active catalogue (built-in plus kinds file and kind packs, ADR-0037); `activity` and `sequence`
  return fixed UML catalogues. Kind names are disjoint across types (checked by a test and by
  `kinds.py`, which also reserves the other types' names), so `Node.is_container` can test against the
  union of all container kinds without knowing its diagram.
- `Diagram.summary()` and `Diagram.subset()` include the type and dispatch to type-specific text where
  the content differs (sequence).

### Fingerprints and stored data

`store.fingerprint` drops `type` when it is `deployment` and `interaction` when it is `None`, as it
already drops the default layout direction. Stored diagrams and versions written before this change
therefore keep their fingerprints, so undo/redo targets and proposal checks are unaffected.

### Operations (`operations.py`)

- `OPERATIONS` stays the one table of operation functions (name → function); it now holds the
  operations of every type. `TYPE_OPERATIONS[type]` lists the names each type offers:
  `deployment` and `activity` share the node/edge operations; `sequence` has its own (UML terms);
  `rename_diagram` and `apply_batch` are shared by all.
- `apply(diagram, op, args)` rejects a known operation that is not in the diagram type's set:
  `Operation 'add_message' is not available for deployment diagrams. Available: …`. Rollback,
  `touched` and the result shape are unchanged.
- `apply_batch` checks each step's name against the diagram's type before the first step runs.
- Kind validation uses `kinds_for(diagram.type)`; an error lists the type's kinds.
- Sequence operations live in `arcviz/sequence_ops.py`, registered in `OPERATIONS`; they are called
  only through `operations.apply` (CLAUDE.md rule 2 is unchanged in substance).

### Validation rules

`exchange._rule_errors(diagram)` dispatches per type (kinds of the type, parent kinds, edge kinds,
and for sequence diagrams the interaction rules). `Diagram.integrity_errors()` stays the store's
structural check and includes the interaction's referential checks. The review rule registry of M12
can key its rules by `diagram.type`.

### Renderers (`render.py`)

```python
class Renderer(Protocol):
    name: str                 # "mermaid" | "svg": the format of the source
    supports_focus: bool      # drill-down into containers
    def render(self, diagram, *, highlight=(), focus=None, marks=None, edge_marks=None) -> str: ...
    def ids(self, diagram) -> dict[str, str]: ...   # model id -> id in the rendering ({} if identical)

RENDERERS: dict[str, Renderer]          # by diagram type
def renderer_for(diagram) -> Renderer
def payload(diagram, **kwargs) -> dict  # {"renderer", "mermaid", "svg", "mermaid_ids"}
```

- `deployment` and `activity` use `MermaidRenderer` (the existing `mermaid.render`, which picks the
  node notation per type); `sequence` uses `SequenceSvgRenderer` (`sequence_svg.render`).
- A renderer that does not support focus renders the whole diagram; the server reports `focus: null`.
- The Git mirror writes `diagram.mmd` or `diagram.svg` depending on the renderer.

### HTTP API (`server.py`)

- Every response and SSE event that carried `mermaid` now carries `renderer` (`"mermaid"` or
  `"svg"`), `mermaid` (Mermaid source or `null`) and `svg` (SVG document or `null`) (ADR-0033).
  For Mermaid types the existing keys are unchanged.
- `GET /api/diagrams/{id}/render?focus=` returns the payload of a clean render (no highlight).
  `GET /api/diagrams/{id}/mermaid` keeps working for Mermaid types and answers 400 for others.
- `POST /api/diagrams` accepts `type` (default: the template's type, else `deployment`). A template
  of another type is a 400. `empty` creates an empty diagram of any type.
- `GET /api/config` keeps its top-level kind lists (the deployment catalogue) and adds
  `diagram_types`: for each type `{uml, renderer, node_kinds, container_kinds, edge_kinds,
  operations, templates, features, …}` with `features = {focus, direction, compare, plantuml}` and
  type-specific enumerations (for example the message sorts of sequence diagrams); `template_types`
  maps template name → type.
- A feature a type does not support yet answers **400** with a message naming the type and the
  follow-up issue: PlantUML export for activity and sequence diagrams, version comparison for
  sequence diagrams (both implemented in M27, #107 and #108; no such feature is left).

### Agent (`agent.py`, `agent_types.py`, `prompts/`)

- `build_tools(catalog, diagram_type="deployment")`: deployment returns the existing `TOOLS`
  (unchanged); other types get their tool list from `agent_types.py`. The `apply_batch` enum lists the
  type's operations. `get_diagram` returns the type-specific JSON.
- `build_system_prompt(diagram)` composes the prompt by type (milestone M19,
  [assistant-prompts.md](assistant-prompts.md), ADR-0065): the shared blocks of `arcviz/prompts/shared/`
  (persona, topic scope, working rules, modelling hints, reply style) followed by the type's expert part
  `arcviz/prompts/types/<type>.txt` (UML notation, modelling guidance, guided-start questions, examples)
  and the diagram summary. A new type adds its expert part; `tests/test_prompts.py` checks that every type
  in `DIAGRAM_TYPES` has one and that its prompt contains every shared block.
- Live and final renders use `render.payload`.

### Exchange (`exchange.py`)

- The JSON document carries `type`. Deployment diagrams are still written as `schema_version` 1;
  other types as **2**, so an older Arcviz refuses them ("written by a newer Arcviz") instead of
  silently importing an empty deployment diagram. The reader accepts versions 1 and 2.
- `export_plantuml` raised `ExchangeError` for types other than `deployment` (server: 400) until M27 added the
  activity and sequence exports (#107, [diagram exchange](diagram-exchange.md#activity-diagrams-107)).

### Frontend

- `state.config.diagram_types` and a pure helper `typeConfig(config, type)` give the inspector, the
  view selector, voice commands and the toolbar the kinds and features of the shown diagram.
- `canvas.renderView({renderer, mermaid, svg})` renders Mermaid as before or inserts the SVG
  (parsed with `DOMParser`, scripts, `foreignObject` and event attributes stripped as a second line
  of defence). Clicks map back to model ids through `mermaid_ids` (Mermaid) or `data-id` (SVG).
- The new-diagram dialog has a type selector; the template list follows it.
- Controls for features the type does not support are disabled with an explaining tooltip
  (direction, view selector, comparison, PlantUML export, Mermaid source copy).
- New pure module `types.js` (layer *pure*); `modules.test.mjs` is updated.

### Configuration

No new variables for this issue. The kinds file keeps describing deployment kinds (ADR-0037).

### Error handling

- Unknown type on create or import: 400 listing the types.
- Operation not available for the type: `OperationError` (tool error for the model, 400 for `/ops`).
- Unsupported feature: 400 with the follow-up issue.

## Testing

- `tests/test_diagram_types.py`: every per-type table covers every type; kind names disjoint;
  default type and fingerprint compatibility (a stored deployment diagram and its fingerprint are
  unchanged); `apply` rejects other types' operations; batch checks the type; `/api/config`
  `diagram_types`; create with type and template; unsupported features answer 400.
- Agent: tools and prompt per type (ScriptedProvider records them); `tests/test_prompts.py` is the table
  test of the prompts (shared blocks plus expert part for every type).
- Exchange: schema versions per type; v2 documents import; PlantUML refuses non-deployment types.
- Property tests run over the templates of every type (see the type specs).
- JS: `types.test.mjs` for `typeConfig`, `viewOf`; module rules updated.
- e2e: create an activity and a sequence diagram from the dialog, render them, hostile names shown
  literally.

## Migration / compatibility

Stored diagrams have no `type` and load as `deployment`. API responses gain keys; none is removed or
changed for deployment diagrams. JSON exports of deployment diagrams stay `schema_version` 1 (with an
additional `type` that older versions ignore).

## Open questions

None open; decisions taken without the maintainer are *Proposed* ADRs and listed in the PR.

## Decisions

- UML 2.5.1 is the reference for all diagram types; deployment kinds keep their names (ADR-0030,
  Accepted by the maintainer).
- The type is a field on `Diagram`; each layer has a per-type table checked by a coverage test
  (ADR-0031, Proposed).
- Mermaid for deployment and activity, own SVG for sequence, behind a per-type renderer interface
  (ADR-0032, Accepted by the maintainer); responses name the renderer and carry the source under a
  key per format (ADR-0033, Proposed).
- The kinds file and kind packs extend deployment diagrams only (ADR-0037, Proposed).
- Exchange documents of new types are `schema_version` 2; deployment stays 1 (ADR-0031, Proposed).
- Features that cannot support a type yet answer 400 and are follow-up issues (ADR-0031, Proposed).
