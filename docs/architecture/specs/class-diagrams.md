# Class diagrams

- Status: Implemented
- Issue: #245
- ADR: [0179 Class members are node fields, not child nodes](../adr/0179-class-members-are-node-fields.md)
- Shared design: [More UML diagram types](uml-graph-types.md) (ADR-0177, 0182..0185)

## Summary

A diagram type `class` following the UML 2.5.1 **Classification** and **Simple Classifiers** chapters (11, 10): classes,
interfaces, enumerations and data types with attributes, operations and literals, packages, and the relationships
association, aggregation, composition, generalization, realization and dependency. Requirements engineers use it for
the domain model and the glossary, architects for the logical data and service model. It is a graph, so it reuses the
node/edge operations through a graph profile and renders as a Mermaid flowchart with the classifier's
compartments written into its label.

## Goals

- The kinds and relationships below with UML rules for which kinds connect.
- Members as text in the UML textual notation (`- id: UUID [1]`, `+ place(items: Item[*]): Order`), kept in
  compartments; multiplicities and role names on associations; abstract classes.
- Packages as containers, with `dependency` between packages (layering).
- Review findings that matter to architects (package cycles, multiple wholes) and to requirements engineers (empty
  classes, associations without multiplicities).

## Non-goals

- Parsing members: a member is a bounded line of text, not a structured Property or Operation (no type checking
  against other classes, no refactoring). A follow-up can add structure without changing the stored text.
- Association classes, n-ary associations, qualified associations, derived properties, templates, nested classifiers,
  signals, stereotypes and profiles, constraints (OCL), notes as nodes (the node `description` is the note).
- Object diagrams (instance specifications).
- Round-trip with code (the MCP code links of project elements cover navigation to code).

## Design

### Kinds (`model.CLASS_KINDS`)

| Kind | UML 2.5.1 metaclass | Container | Notation |
|---|---|---|---|
| `class` | Class | no | rectangle: `«abstract»` (when abstract), name, rule, attributes, rule, operations |
| `interface` | Interface | no | rectangle: `«interface»`, name, rule, operations |
| `enumeration` | Enumeration | no | rectangle: `«enumeration»`, name, rule, literals |
| `data_type` | DataType | no | rectangle: `«dataType»`, name, rule, attributes, rule, operations |
| `package` | Package | yes | subgraph labelled with the name |

Edge kinds, with the drawing and the model orientation. **An edge always points from the element that refers to the
other to the element it refers to** (the end marker sits at the target end, which is all Mermaid can draw; ADR-0184):

| Kind | UML | Source → target | Mermaid | Notes |
|---|---|---|---|---|
| `association` | Association | either classifier → either | `---` (no head); `direction: forward` = navigable to the target, `-->` | roles and multiplicities |
| `aggregation` | Association with shared aggregation | **part → whole** | `--o` | circle at the whole (UML: hollow diamond) |
| `composition` | Association with composite aggregation | **part → whole** | `==o` | thick line, circle at the whole (UML: filled diamond) |
| `generalization` | Generalization | specific → general | `-->` | open arrow head at the general classifier (UML: hollow triangle) |
| `realization` | InterfaceRealization | class → interface | `-.->` with the text `«realizes»` | UML: dashed line, hollow triangle |
| `dependency` | Dependency | client → supplier | `-.->` | the deployment kind with the same meaning (ADR-0185) |

The tool descriptions, the prompt and the inspector state the orientation in these words, the PlantUML export swaps it
back (`Whole *-- Part`).

### Typed fields (ADR-0177, ADR-0179)

Passed in the `fields` argument of `add_node`/`update_node`/`add_edge`/`update_edge`; stored as properties.

| Where | Field | Value |
|---|---|---|
| `class`, `data_type` | `attributes`, `operations` | list of lines, ≤ 30 each, ≤ 120 characters per line |
| `interface` | `operations` | list of lines as above |
| `enumeration` | `literals` | list of lines, ≤ 30, ≤ 60 characters |
| `class` | `abstract` | `true` or unset (`false` removes it) |
| `association`, `aggregation`, `composition` | `source_multiplicity`, `target_multiplicity` | `1`, `*`, `0..1`, `1..*`, `2..5` (lower ≤ upper; checked by the profile) |
| same | `source_role`, `target_role` | text ≤ 40 characters |

Lines are normalised with `single_line`, stripped, empty lines dropped; a list replaces the previous one (an empty
list removes the field). A line should start with a UML visibility sign (`+ - # ~`); the tools and the prompt say so,
the operations do not enforce it. The edge `label` is the association name.

### Rules (profile; operations and importer share one function)

1. A `package` takes part only in `dependency` edges.
2. `generalization`: source and target are of the same kind (`class`–`class`, `interface`–`interface`,
   `data_type`–`data_type`), not the same node, and the edge creates no generalization cycle.
3. `realization`: source is a `class`, target an `interface`.
4. `association`, `aggregation`, `composition`: both ends are `class` or `interface` (a `data_type` and an
   `enumeration` are used through attribute types, not associations); aggregation and composition between `class` nodes
   only. `direction` is allowed on `association` (`forward` or unset), nowhere else; `both` is not drawn in UML class
   diagrams and is refused.
5. Only a `package` has children; a package may contain classifiers and packages.
6. `abstract` only on a `class`; `literals` only on an `enumeration`; multiplicity syntax; bounds of the fields.

### Review findings (`rules.RULES["class"]`)

| Id | Category / severity | Condition |
|---|---|---|
| `class-package-cycle` | risk / medium | packages (or classifiers in different packages, lifted to their top-level packages) depend on each other in a cycle (strongly connected component of the package dependency graph) |
| `class-multiple-wholes` | risk / medium | a node is the source (the part) of more than one `composition` |
| `class-composition-cycle` | risk / medium | compositions form a cycle |
| `class-multiple-inheritance` | suggestion / low | a class has more than one `generalization` parent |
| `class-interface-unrealized` | suggestion / low | an interface has no incoming `realization` |
| `class-abstract-leaf` | suggestion / low | an abstract class has no incoming `generalization` |
| `class-isolated` | suggestion / low | a classifier has no relationship at all in a diagram with at least three |
| `class-empty` | suggestion / low | a class or interface without attributes and operations (the glossary case, finding is low) |
| `class-association-without-multiplicity` | suggestion / low | an association, aggregation or composition without multiplicity at either end |
| `class-duplicate-member` | risk / low | two attributes of one compartment with the same name (text between the visibility sign and `:`, `(` or a space), or two operations with the same name and parameter list (spaces and case ignored); overloads are fine |
| `class-enumeration-empty` | suggestion / low | an enumeration without literals |

### Rendering

Rectangle node whose label is the lines of the table above joined by `<br/>`, every line through `escape_text` (which turns
`:` into the entity `#58;`, so a member reads as typed in the browser); the rule is a line of `U+2500`. Compartment lines are centred (Mermaid flowchart labels cannot be left-aligned, verified with the
vendored Mermaid 11.17.2). A class with more than 8 members per compartment shows the first 8 and `… (+n)`; the full
list is in the inspector, summary and export. Association text: first line the name, second line
`source_role source_multiplicity – target_role target_multiplicity` (parts that are unset are left out). Packages are
subgraphs. Container labels stay single-line. `mermaid.edge_lines` gives the lines of an edge's text: the keyword of its kind
(`model.EDGE_STEREOTYPES`, a table by edge kind because kind names are global, not a profile member), its text and the ends of
an association; the end markers of aggregation and composition are `mermaid._CIRCLE_ENDS`. The style class of the kind `class`
is `class_kind` (`mermaid._style_name`) because `class` is a Mermaid keyword. Highlight, focus, comparison marks and `mermaid_ids` are unchanged.

### Exchange: PlantUML (`plantuml_graph.class_diagram`)

`class "Name" as n_id { … }`, `abstract class`, `interface`, `enum`, and `class … <<dataType>>` for data types (the ids of
`mermaid.id_map`); members as written, one per line; packages as `package "Name" as n_id { … }` (bounded nesting); relationships
`n_whole "mult" *-- "mult" n_part : label`, `o--`, `n_general <|-- n_specific`, `n_interface <|.. n_class`, `..>`, `--` and
`-->`; roles are not written (PlantUML has no role syntax; they follow the label as `name (role ↔ role)`), which the guide
lists as a loss. Text through `plantuml_text.plain`. Checked with PlantUML 1.2026.8 (`tests/test_plantuml_class.py`).

### Tools and prompt

Tools: graph tools with class descriptions, the kind enums, the `fields` object with exactly the allowed keys per kind.
Prompt (`prompts/types/class.txt`): ask for the domain, the glossary terms and who reads the model before drawing;
say *a class is a noun the users of the system use, an operation is something it does*; explain the orientation of
edges and multiplicities with a worked example (Kunde bestellt Bestellung, Bestellung besteht aus Positionen).

### Template (`webshop-domain`)

Package *Bestellwesen* with `Kunde`, `Bestellung` (attributes, operation `+ aufgeben()`), `Position`, `Artikel`, the
enumeration `Bestellstatus`, the interface `Zahlungsverfahren` realized by `Kreditkarte`; `Position` composed into
`Bestellung` (1..* / 1), `Kunde` 1 – 0..* `Bestellung`, `Artikel` abstract with the specialization `Medienartikel`.

### Project model (ADR-0182)

`class`, `enumeration` and `data_type` may link to an element of kind `data`; `interface` links to `component`.
They are **not** adopted at creation (a domain model of 40 classes would flood the project model); **Unverknüpfte
übernehmen…** adopts them on request. Relationships carry no relation links (UML associations are not architecture
relations).

### Frontend

The inspector's property table shows a typed field as a row whose key is the field name, a list field as a textarea (one
member per line), and a row for every field of the kind that is not set yet; `propertyChanges` sends typed values as
`fields` (`types.typedValues`: lists, booleans). The orientation of aggregation and composition (part → whole) is in the tool
descriptions, the prompt and the guide.

## Testing

- `tests/test_class_diagram.py`: kinds; each rule in operations and importer; fields (bounds, lists replace, empty list
  removes, reserved keys); orientation; Mermaid labels with hostile members and names, compartments, truncation; edge
  ends; tools and prompt; template; PlantUML text and JAR test.
- `tests/test_rules_class.py`: one test per finding id, the package-cycle detector on a layered and a cyclic diagram,
  linearity.
- Properties: strategies draw classes with members and valid relationships (no cycles for generalization);
  invariants as in the shared design.
- Comparison: a changed attribute marks the class changed.
- e2e: create from the template, edit an attribute and a multiplicity in the inspector, hostile member text.

## Migration / compatibility

New type only. The shared `dependency`, `association` and `interface` names keep the deployment rendering.

## Open questions

1. **Orientation of aggregation and composition** is part → whole, decided by the maintainer: the price of Mermaid's end
   markers. The alternative (the UML reading, whole → part, with the line emitted reversed) would change the edge ids that
   the frontend derives from the model ids and costs a mapping in `findings.edgeSvgKeys`.
2. **Members as structured data** later: the stored text stays valid.

## Decisions

- Members are node fields, not child nodes (ADR-0179, Accepted).
- Typed values go through one `fields` argument (ADR-0177, Accepted).
- Classes are linkable, not adopted by default (ADR-0182, Accepted).
