# Import and export

Source: `arcviz/exchange.py`. Tests: `tests/test_exchange.py`. Design:
[diagram exchange spec](../architecture/specs/diagram-exchange.md), ADR-0018. HTTP endpoints:
[HTTP server](http-server.md#import-and-export).

## JSON document

```python
FORMAT = "arcviz-diagram"
SCHEMA_VERSION = 2  # newest version read; written for non-deployment types
DEPLOYMENT_SCHEMA_VERSION = 1  # written for deployment diagrams, so older versions keep reading them

export_document(diagram) -> {"format": FORMAT, "schema_version": 1 | 2, "exported_at": now_iso(), "diagram": diagram.model_dump(mode="json")}
parse_document(document: Any) -> Diagram  # raises ExchangeError(errors: list[str])
```

`parse_document` accepts the envelope or a bare diagram object (no `format` key). Checks, all
collected before raising:

1. Envelope: object; `format == "arcviz-diagram"`; `schema_version` a positive integer
   `<= SCHEMA_VERSION` ("written by a newer Arcviz" otherwise); `diagram` present.
2. `Diagram.model_validate`; each Pydantic error becomes `"<loc>: <msg>"`.
3. Model rules (`_rule_errors`, per [diagram type](diagram-types.md)): `nodes` keys equal node ids; non-empty ids and names; node kinds in the
   catalogue of the diagram's type (`kinds_for(type)`; for deployment built-in plus configured); a parent must be a container of that
   catalogue; edge ids non-empty and unique; edge kinds in the catalogue; an edge `direction` is `None`
   or in `EDGE_DIRECTIONS`; `direction` in `DIRECTIONS`; then `Diagram.integrity_errors()` (unknown parents, containment
   cycles, dangling edges). `Diagram.ancestors()` stops at a cycle, so cyclic input terminates.

`ExchangeError` is a `ValueError` whose message is the errors joined with `; `.

## Project bundle

Design: [Export and import of whole projects](../architecture/specs/project-exchange.md), ADR-0056.
Tests: the bundle part of `tests/test_exchange.py`, `tests/test_project_bundles.py`, the round-trip
property in `tests/test_properties.py`.

`export_project(project, diagrams)` returns
`{"format": "arcviz-project", "schema_version": 1, "exported_at", "project": {"id", "title",
"description", "sections", "diagrams": [{"diagram", "section"}]}, "diagrams": [export_document(…)]}`;
members whose diagram is not in `diagrams` are left out of both lists. A project with a shared model
(elements, or a section standing for one) adds `project.elements` (and `sections[].element`) and is written
as `schema_version` 2 (`PROJECT_SCHEMA_VERSION`); without one the bundle stays version 1
(`PLAIN_PROJECT_SCHEMA_VERSION`), byte-identical to before (ADR-0077). Diagram documents carry `ref` on linked
nodes and lifelines; `parse_document` refuses a `ref` that is not a slug or sits on a view element that
cannot stand for an element, and keeps one that names no element (dangling). A project bundle keeps them. Every name, title, label and guard of an imported document, bundle or embedded model is
made one line first (`_one_line`, `operations.ONE_LINE_ARGUMENTS`, #103); descriptions and user `properties` keep
theirs, except an activity edge's `properties.guard`, which is a label.

**The model in a single document** (#153, ADR-0161): `export_document(diagram, model)` adds the optional key
`model: {"elements", "relations"}` when the diagram links anything: `project_model.referenced_model` gives the
elements it refers to (views, and the ends of the relations it refers to) with their ancestors, and the relations
between those referenced elements, in project order (`DiagramService.model_for_export`, viewer). A version export
(perhaps written while the diagram was in another project) carries only the elements whose current name and kind
still match every view of them in that version, and the relations between those; the other refs stay in the document
and an import reports them as dropped. Documents without links are written as before. The schema version is unchanged: every
release (v0.1.2 to v0.4.0 checked) reads only `format`, `schema_version` and `diagram` and ignores other keys, so they
import such a document as before, without its model. It holds only what a viewer of the diagram's project could
read there anyway, so the export exposes nothing new. `parse_document_model(document)` returns a `DocumentModel(elements,
relations, warnings)` or None and validates like `parse_project` does for a bundle's model: the fields,
`Project.integrity_errors()` (ids, kinds, parents, relation rules and ends, id lengths), element id lengths,
`MAX_ELEMENTS` and unique names (`duplicate_element_names`); problems are prefixed `model`. Names and descriptions
over the element bounds are shortened like a bundle's (`shorten_element_texts`, #212); the warning is answered as
`warnings`, and the views take the shortened names when they are linked. `POST /api/diagrams/import` runs `DiagramService.import_carrying`:
with a model, `ProjectService.carry` brings it into the target project with the planner of the move (mapped, copied
or dropped), then the diagram is created linked to the target's ids; without one its refs are dropped (ADR-0079) and
reported as dropped with the reason "the document carries no model". The answer carries `carried` whenever the
document linked anything. A failed create takes the copies back (`ProjectService.uncarry`, only untouched and unused
ones); after the create every linked view is checked against the target once more (a copy removed meanwhile is
unlinked). The caller's title wins, else the document's; a document without either is imported as "Unbenanntes Diagramm" with a
warning, a bundle the same way.

`parse_project(bundle)` returns `ProjectBundle(title, description, sections, members, diagrams, elements, relations,
relation_seq, warnings)` (element names and descriptions are stripped, those over `MAX_ELEMENT_NAME` /
`MAX_ELEMENT_DESCRIPTION` cut to the bound ending in `…`, a cut name made unique with `… (2)`, `… (3)`, …; sections and
the linked nodes and lifelines of the bundle's diagrams take the cut name through `sync_element`; `warnings` says how
many were cut: an export of a project stored with longer texts can always be imported again, #212; names that were
equal already, case-insensitive, are an error) or
raises `ExchangeError` with every problem: not an object; unknown `format` (`PROJECT_FORMAT =
"arcviz-project"`); `schema_version` not a positive integer or newer than `PROJECT_SCHEMA_VERSION`
(2); `project` or `diagrams` missing or of the wrong type (these stop the check); every diagram document
through `parse_document`, its problems prefixed `diagram <n> (<id>)`; duplicate diagram ids; the
project structure validated as a `Project` (pydantic errors as `project.<path>: …`, an empty title,
`integrity_errors()` as `project: …` (also the model's: element ids, kinds, parents, sections standing for unknown
elements), section ids that are not slugs, more than `MAX_ELEMENTS` elements); membership entries without a
diagram in the bundle and diagrams not in the membership. A section standing for an element takes the
element's name as its title.

`ProjectExchangeService(diagrams, projects)` (`arcviz/services/bundles.py`): `export(project_id)` reads
the project and its member diagrams, leaving out only those confirmed deleted (`exists()` false); any
other `StoreError` fails the export instead of producing an incomplete bundle; `import_bundle(bundle, title=None)` parses first, then
`ProjectService.create_with_sections(title or bundle title, description, sections, elements)` (a new id `<slug>-<random
suffix>`, ADR-0131; the section tree is checked with `integrity_errors()`), then
`DiagramService.import_new(diagram, diagram.title, None, project, section)` per member in order. Any
exception deletes the diagrams created so far and the project (failures logged) and propagates.

## PlantUML

`export_plantuml(diagram) -> str` writes every diagram type (`PLANTUML_TYPES` = `DIAGRAM_TYPES`): `@startuml`, the
`title`, the type's body, `@enduml`. Deployment: `top to bottom direction` (`TB`, `BT`) or
`left to right direction` (`LR`, `RL`), the containment tree (children sorted by name, containers
with children as `{ … }` blocks), then the edges in model order, `@enduml`. Element lines are
`<element> "<name>" <<<kind>>> as <mermaid id>` with the identifiers of `mermaid.id_map`.

| Kinds | Element |
|---|---|
| `deployment`, `node`, `cluster` | `node` |
| `vnet`, `external` | `cloud` |
| `subnet`, `region` | `frame` |
| `group` | `package` |
| `service`, `component`, `function`, `gateway` | `component` |
| `database`, `queue`, `storage`, `artifact` | same name |

| Edge kind | Arrow |
|---|---|
| `connection` | `-->` |
| `dependency` | `..>` |
| `deploys` | `-[bold]->` |
| `association` | `--` |
| `replication` | `<-->` |

Text (title, names, labels) goes through `_plain`: whitespace collapsed to single spaces;
`<`, `&`, `"`, `~` become `&#60;`, `&#38;`, `&#34;`, `&#126;`; `\` becomes `\\`; doubled markup
characters (`**`, `//`, `__`, `--`, `[[`) get PlantUML's `~` escape and `==` becomes `&#61;&#61;`.
`%` becomes `&#37;` (the preprocessor would call `%date()` and the like). This was checked against PlantUML
1.2026.8 so that names render literally. Properties are not exported. The title line also writes ASCII punctuation
as entities, and every character of a title without a letter or digit (PlantUML refuses titles like `;`, `-->`,
`%%` or a lone emoji).

### Activity diagrams

`_ActivityWriter(diagram).write()` walks the flows from every `initial_node`, then from nodes without incoming flow,
then from any node not drawn yet (design: [diagram exchange](../architecture/specs/diagram-exchange.md#activity-diagrams-107)).

| Model | PlantUML |
|---|---|
| `initial_node`, `activity_final_node`, `flow_final_node` | `start`, `stop`, `end` |
| `opaque_action` | `:<name>;` |
| `data_store_node`, `central_buffer_node` | `:«datastore»\n<name>; <<object>>`, `:«centralBuffer»\n<name>; <<object>>` |
| partition of a drawn node | swimlane `\|<name>\|` when it changes (`\| \|` outside every partition); the first one also as the first line |
| one outgoing flow with guard or label | `-> [<guard>] <label>;` before the target |
| `decision_node` with two / more outgoing flows | `if (<name>) then (<guard>)` … `else (<guard>)` … `endif` (`else` guard last) / `switch (<name>)`, `case (<guard>)` …, `endswitch` |
| any other node with several outgoing flows | `fork`, `fork again` per flow (its label first), `end fork` |
| flow to a node drawn already, or out of an inner structure to an outer one's end | `(<n>)`, `detach`; `(<n>)` before the target (the flow's guard and label as `-> …;` first, also on a flow that ends a structure) |
| flow ending without a final node | `detach` after an action or connector |

A structure ends at the node's immediate post-dominator (one pass over all nodes, Cooper/Harvey/Kennedy on the reversed
flows from a virtual exit after every node without outgoing flow), if every branch reaches an end and that node is not
drawn yet; the walk continues there. More than `MAX_PLANTUML_NESTING` (150) nested structures, containers (deployment) or
combined fragments (sequence), or a `RecursionError` of any export, raise `ExchangeError` (HTTP 400); any other
exception is a defect and a 500 like elsewhere. Reserved characters become entities: `;` in actions and arrow labels, `(` `)` in conditions and
guards, `|` in lane names.

### Sequence diagrams

| Model | PlantUML |
|---|---|
| lifeline | `participant "<name>[ : <type>]" as L_<id>` (`actor` for actors), in order; aliases sanitised to `[A-Za-z0-9_]` and made unique |
| message `synchCall`, `deleteMessage` / `asynchCall`, `asynchSignal` / `reply`, `createMessage` | `L_a -> L_b : name` / `->>` / `-->>` |
| found / lost message | `[o<arrow> L_b : name` / `L_a <arrow>o] : name` |
| `createMessage` creating its receiver (its first message, not to itself) | `create L_b` before it |
| `deleteMessage` | `destroy L_b` after it |
| destruction | `destroy L` (preceded by `L -[hidden]> L` if no message came before) |
| `alt`, `opt`, `loop`, `par`, `break`, `critical` | `<operator> <guard>`, `else <guard>` per further operand, `end` |
| `neg`, `assert`, `strict`, `seq`, `ignore`, `consider` | `group <operator>[ {<messages>}][ [<guard>]]`, `else`, `end` |
| interaction use | `ref over L_a, L_b : <refers_to>` |
| execution specification | `activate L` after its start message, `deactivate L` after its finish (none: the lifeline's last message); activations of a message before its deactivations |

`covered` of combined fragments is not exported. `tests/test_plantuml_behaviour.py` pins every construct and, with
`ARCVIZ_PLANTUML_JAR`, renders them with PlantUML.
