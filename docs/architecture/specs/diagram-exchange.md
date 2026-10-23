# Diagram import and export

- Status: Implemented (milestone M8; activity and sequence PlantUML: milestone M27)
- Issue: #79, #107 (PlantUML for activity and sequence diagrams)
- ADR: [0018 Exchange formats: versioned JSON both ways, PlantUML export only](../adr/0018-exchange-formats-json-and-plantuml-export.md)

## Summary

Today a diagram can leave the app only as a picture (SVG/PNG) or as Mermaid source, and nothing can
come in except through the chat or single operations. This change adds a versioned JSON document
that round-trips a diagram (export, then import as a new diagram) and a PlantUML export for people
who keep architecture diagrams in PlantUML. Draw.io and Structurizr DSL were evaluated and are not
implemented (ADR-0018).

## Goals

- `GET /api/diagrams/{id}/export?format=json` returns the model (current state or a given version)
  as a self-describing JSON document; importing that document recreates an equal model.
- `POST /api/diagrams/import` creates a **new** diagram from such a document (or from a bare
  diagram JSON such as a stored `latest.json`), after full validation: schema, closed kind sets,
  layout direction, node-key consistency, unique edge ids and `Diagram.integrity_errors()`. Invalid
  documents are rejected with every problem listed and nothing is written.
- `format=plantuml` returns a PlantUML diagram of the same model in its type's notation (deployment; activity and
  sequence since M27, see [below](#activity-diagrams-107)).
- The UI offers both downloads in the *Export* menu and an *Importieren* button.

## Non-goals

- Importing into (replacing) an existing diagram, merging, or importing chat transcripts and
  version histories. An import is a new diagram with one `initial` version.
- Importing PlantUML, draw.io, Structurizr or Mermaid (parsing other tools' languages).
- Draw.io and Structurizr DSL export (ADR-0018).

## Design

### Module `arcviz/exchange.py`

Pure functions, imports `model` and `mermaid` (for the collision-free `id_map`):

```python
FORMAT = "arcviz-diagram"
SCHEMA_VERSION = 1

class ExchangeError(ValueError):
    errors: list[str]          # every problem found, message = "; ".join(errors)

def export_document(diagram: Diagram) -> dict[str, Any]
def parse_document(document: Any) -> Diagram
def export_plantuml(diagram: Diagram) -> str
```

**Document** (`export_document`):

```json
{
  "format": "arcviz-diagram",
  "schema_version": 1,
  "exported_at": "2026-10-06T12:00:00+00:00",
  "diagram": { "id": "demo", "title": "Demo", "nodes": { … }, "edges": [ … ], "direction": "TB", "created_at": "…", "updated_at": "…" }
}
```

**Parsing** (`parse_document`) collects errors instead of stopping at the first:

1. The document must be an object. With a `format` key it is an envelope: `format` must be
   `arcviz-diagram`, `schema_version` must be an integer ≤ `SCHEMA_VERSION` (a newer one is
   rejected as "written by a newer Arcviz"), and `diagram` is the payload. Without a `format` key
   the object itself is the payload (bare diagram, as stored by the `file` backend).
2. The payload is validated with `Diagram.model_validate`; Pydantic errors become
   `"<location>: <message>"`.
3. Model rules the Pydantic types do not enforce: every `nodes` key equals its node's `id`; ids and
   names are non-empty; node and edge kinds are in the active kind catalogue (built-in plus configured),
   an edge `direction` is `None` or in `EDGE_DIRECTIONS`, `direction` in `DIRECTIONS`; edge ids are non-empty and unique; then `integrity_errors()` (parents exist and are
   acyclic, edge endpoints exist) plus "parent must be a container" (the rule operations enforce).
4. Any error raises `ExchangeError(errors)`. Unknown extra keys are ignored (Pydantic default).

**PlantUML** (`export_plantuml`): `@startuml`, `title`, `top to bottom direction` (`TB`, `BT`) or
`left to right direction` (`LR`, `RL`; PlantUML has no reversed directions), then the containment
tree as nested blocks and the edges:

| Arcviz kind | PlantUML element | | Arcviz kind | PlantUML element |
|---|---|---|---|---|
| `deployment`, `node`, `cluster` | `node` | | `service`, `component`, `function`, `gateway` | `component` |
| `vnet` | `cloud` | | `database` | `database` |
| `subnet`, `region` | `frame` | | `queue` | `queue` |
| `group` | `package` | | `storage` | `storage` |
| | | | `external` | `cloud` |
| | | | `artifact` | `artifact` |

Each element is `<element> "<name>" <<<kind>>> as <id>` (a container opens `{` and closes `}`),
using the Mermaid identifiers from `mermaid.id_map` (valid PlantUML aliases). Edges:
`connection` `-->`, `dependency` `..>`, `deploys` `-[bold]->`, `association` `--`,
`replication` `<-->`, label as `: <label>`. In names and labels `"` becomes `'` and line breaks
become spaces (PlantUML has no escape for quotes inside quoted names). Properties are not exported
(a PlantUML deployment diagram has no attribute compartment); this is stated in the user guide.

### Activity diagrams (#107)

PlantUML's activity syntax is structured (`if`/`switch`, `fork`, swimlanes), an Arcviz activity diagram is any
graph of ActivityNodes and ActivityEdges. `_ActivityWriter` translates it by walking the flows:

- Order: from every `initial_node` (`start`), then from nodes without incoming flow, then from any node not drawn
  yet (loops nothing leads into), each walk drawing what it reaches.
- Nodes: `opaque_action` → `:<name>;`, `data_store_node` / `central_buffer_node` → `:«datastore»\n<name>; <<object>>`
  / `«centralBuffer»` (PlantUML draws `<<object>>` as a rectangle; the `]` shape is refused by 1.2026.8),
  `activity_final_node` → `stop`, `flow_final_node` → `end`. Decision, merge, fork and join nodes are the structure
  itself and draw no line. ActivityPartitions become swimlanes `|<name>|` (nodes outside every partition, once there
  are partitions, `| |`), switched before the first drawn node of another partition; the first one is declared before
  any structure.
- A node with one outgoing flow continues with its target; the flow's guard (`[g]`) and label become `-> <text>;`.
- A `decision_node` with several outgoing flows becomes `if (<name>) then (<guard>) … else (<guard>) … endif` (two;
  an `else` guard is drawn last) or `switch (<name>) case (<guard>) … endswitch` (more). Any other node with several
  outgoing flows (a `fork_node`, or an action with several flows, UML's implicit fork) becomes `fork … fork again …
  end fork`, the flow labels at the start of each branch. A structure is closed at the node's immediate
  post-dominator (computed once for all nodes, Cooper, Harvey and Kennedy over the reversed flows from a virtual exit
  after every node without outgoing flow) when every branch reaches an end and that node is not drawn yet; the walk
  continues there. Otherwise each branch ends on its own. The export is linear in practice (a chain of 3000
  decisions takes well under a second); structures nested more than `MAX_PLANTUML_NESTING` (150) deep (also containers of deployment
  diagrams and combined fragments of sequence diagrams) are refused with an `ExchangeError` (HTTP 400), and a
  `RecursionError` of any export becomes one too. Any other exception is a defect: a 500 with its traceback logged
  by the request middleware, like everywhere else.
- A flow to a node that is already drawn, or that ends an enclosing structure from deeper inside, becomes a pair of
  UML connectors (15.2.4): `(<n>)` and `detach` where it leaves, `(<n>)` right before the target's drawing. Loops
  are drawn this way (no `repeat`/`while`, which would need the loop's shape to be recognised).
- A flow that ends without a final node ends with `detach` when PlantUML accepts it there (after an action or a
  connector; elsewhere the branch just ends).

Lost: partition nesting (the innermost partition is the lane), flows that cross from one branch into another (as
connectors), the position of decision and merge nodes that merely pass flows on.

### Sequence diagrams (#107)

- Lifelines in order: `participant "<name> : <type>" as L_<id>` (`actor` for actors), aliases sanitised and unique.
- Messages, with the arrows the own SVG renderer draws: `synchCall` and `deleteMessage` `->`, `asynchCall` and
  `asynchSignal` `->>`, `reply` and `createMessage` `-->>`; found `[o<arrow> L : name`, lost `L <arrow>o] : name`.
  The lifeline a `createMessage` creates (its first message) gets `create L` before it (not when it sends it to
  itself, which PlantUML refuses); a `deleteMessage` is followed by `destroy <receiver>`, a destruction by
  `destroy L` (after a hidden self message if no message came before, as PlantUML requires).
- Combined fragments: `alt`, `opt`, `loop`, `par`, `break`, `critical` with their keyword and the first operand's
  guard, later operands `else <guard>`, then `end`. `neg`, `assert`, `strict`, `seq`, `ignore`, `consider` have no
  keyword and become `group <operator>[ {<messages>}][ [<guard>]]` with `else` between operands. `covered` is not
  exported: PlantUML sizes a fragment by its contents.
- Interaction uses: `ref over L1, L2 : <refers_to>`.
- Execution specifications: `activate L` after their start message and `deactivate L` after their finish message
  (without `finish`: after the lifeline's last message, as drawn); per message all activations first.

Text goes through `_plain` as for deployment, plus numeric entities for characters the construct would read
(`;` in actions and arrow labels, `(`/`)` in conditions and guards, `|` in lanes, `[`/`]` in group labels). `%`
became an entity for every type (PlantUML's preprocessor calls `%date()` and friends inside names), and the title
line writes punctuation as entities (and all of a title without letters or digits), because PlantUML 1.2026.8
refuses titles such as `;`, `-->` or a lone emoji. The translation was checked by rendering several hundred random
reachable diagrams with PlantUML 1.2026.8; `tests/test_plantuml_behaviour.py` renders its constructs when
`ARCVIZ_PLANTUML_JAR` names a PlantUML jar.

### HTTP

- `GET /api/diagrams/{id}/export?format=json|plantuml&version=<n>`: `version` optional (default:
  current state). `json` → `application/json`, filename `<id>[-v<n>].arcviz.json`; `plantuml` →
  `text/plain; charset=utf-8`, filename `<id>[-v<n>].puml`; both with
  `Content-Disposition: attachment`. Unknown diagram or version → 404, unknown format → 400.
- `POST /api/diagrams/import` with `{"document": {...}, "id": "optional", "title": "optional"}`:
  `ExchangeError` → 400 with the joined errors. The title defaults to the document's title. An
  explicit `id` must be a valid, free id (otherwise 400/409). Without one the id is derived from the
  document's id (slugified, at most 60 characters, `diagram` if empty) and made unique with `-2`,
  `-3`, … so importing the same file twice yields two diagrams; when a concurrent create takes the
  chosen id first, the next suffix is tried instead of answering 409. The diagram is persisted with the
  store's derived operation `create()` (which re-checks `integrity_errors()` and records the
  `initial` version); `updated_at` is set to the import time. Response: 201 with the detail
  payload of the new diagram.

### UI

- *Export* menu: **JSON herunterladen** and **PlantUML herunterladen**. They export the whole
  diagram (not the focused view, since the document is the model) of the shown state: the previewed
  version during a preview, otherwise the current state.
- Topbar **Importieren**: file picker (`.json`), the file is parsed in the browser
  (`JSON.parse`; a syntax error is reported without a request), posted to `/api/diagrams/import`
  and the new diagram is opened. Errors are shown with `alert()` like the other topbar actions.

## Testing

- `tests/test_exchange.py`: round trip (export → parse equals the model, including direction and
  properties); bare diagram accepted; wrong format, newer schema, non-object, Pydantic errors,
  unknown kinds, unknown direction, key/id mismatch, duplicate edge ids, unknown parent, leaf as
  parent, cycle, dangling edge (all errors reported together); PlantUML structure, nesting,
  escaping, arrows, direction mapping.
- `tests/test_server.py`-style tests in the same file: export JSON/PlantUML (current and version,
  headers), 404/400 cases; import (201, derived unique ids, explicit id conflict 409, invalid id 400,
  invalid document 400 with nothing persisted, title override).
- e2e: JSON download is a valid document; importing it opens a new diagram.

## Migration/compatibility

None; new endpoints and module. `schema_version` allows future changes: a later version must keep
reading version 1 documents.

## Open questions

None.

## Decisions

- ADR-0018 (status *Proposed*): JSON both ways, PlantUML export only, import always creates a new
  diagram.
