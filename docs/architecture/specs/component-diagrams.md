# Component diagrams

- Status: Implemented
- Issue: #247
- ADR: [0181 Component diagrams: ports are node fields, interfaces are nodes, relation links come second](../adr/0181-component-diagrams-ports-interfaces-and-relation-links.md)
- Shared design: [More UML diagram types](uml-graph-types.md) (ADR-0177, 0182..0185)
- Builds on: [Model relations](model-relations.md) (ADR-0135..0141), [Elements by default](elements-by-default.md)

## Summary

A diagram type `component` following the UML 2.5.1 **Components** chapter (11) and the ports and interfaces of the
Classification chapter (10, 11): components, subsystems, packages and interfaces, with connectors, interface
realizations (a component provides an interface), usages (it requires one) and dependencies. It answers *which
building blocks does the system have and who depends on what*, the logical view between the use case diagram and the
deployment diagram. The deployment type can already draw components (`component` is a deployment kind, and the C4
kinds file exists), so the new type adds what that cannot say: interfaces with providers and consumers, ports,
a closed UML vocabulary and review rules on those. It is a graph on the node/edge operations through a graph profile,
rendered as a Mermaid flowchart. Connectors can show the project model's relations, the same way deployment edges do.

## Goals

- Components, subsystems (nested components), packages, interfaces; ports on components.
- Provided and required interfaces as realization and usage edges to an interface node; connectors between components
  (optionally between named ports).
- Review findings on interfaces and dependencies (a required interface nobody provides, dependency cycles).
- Linking components and subsystems to project elements, adopted at creation like deployment building blocks; connectors
  linked to relations (second step below).

## Non-goals

- Composite structure diagrams (parts, roles, delegation connectors inside a component), assembly vs delegation
  connectors, port types and behaviours, provided/required interface *sets* on ports, ball-and-socket drawing,
  artifacts and manifestations (deployment diagrams), component instances.
- Replacing the deployment type for architectures that mix cloud resources and components.
- Converting existing deployment diagrams.

## Design

### Kinds (`model.COMPONENT_KINDS`)

| Kind | UML 2.5.1 metaclass | Container | Notation (Mermaid) |
|---|---|---|---|
| `component` | Component | no | rectangle: `«component»`, name; below a rule one line `port <name>` per port |
| `subsystem` | Component stereotyped «subsystem», with nested components | yes | subgraph labelled `«subsystem» Name` |
| `interface` | Interface | no | circle with the name (UML lollipop; the class diagram draws the same kind as a classifier box, ADR-0185) |
| `package` | Package | yes | subgraph labelled with the name |

`component` and `interface` keep the names of the deployment and class kinds (ADR-0185). A container `component` is
not needed: nesting uses `subsystem`.

Edge kinds:

| Kind | UML | Source → target | Mermaid |
|---|---|---|---|
| `connection` | Connector (assembly) | component or subsystem → component or subsystem, requirer to provider | `-->` (deployment's `connection`; `direction` may be `both` or `none`) |
| `realization` | InterfaceRealization (provided interface) | component or subsystem → interface | `-.->` with the text `«realizes»` |
| `usage` | Usage (required interface) | component or subsystem → interface | `-.->` with the text `«use»` |
| `dependency` | Dependency | any → any | `-.->` |

### Typed fields (ADR-0177, ADR-0181)

- `ports` on `component`: list of at most 10 lines of at most 40 characters; the port's name is the text before an
  optional `:` (`orders-in: OrderApi`). Names are unique per component.
- `source_port` and `target_port` on `connection`, `source_port` only on `realization` and `usage` (their target is an
  interface, which has no ports): the name of a port of that end, which must be a `component`. Renaming or removing a port
  that an edge names is refused (`port 'orders-in' is used by edge e4`); `update_node` checks the node after any field change.
  A property key that a field of the type owns on a kind without that field is an import error.
- `protocol` and `port` stay free edge properties as in deployment diagrams (drawn next to the label, `port` here
  is the network port; the typed `source_port`/`target_port` are not).

### Rules (profile)

1. A `package` takes part only in `dependency` edges.
2. `connection` joins two nodes of kind `component` or `subsystem`, different nodes; `realization` and `usage` go from a
   `component` or `subsystem` to an `interface`; an `interface` is never a source.
3. Only `subsystem` and `package` have children. A `subsystem` holds components, interfaces and subsystems; a `package` also
   holds packages. No edge joins a node with one of its own ancestors or descendants (a subgraph and its content).
4. Port fields: `ports` only on `component`; port references resolve; names unique; bounds.

### Review findings (`rules.RULES["component"]`)

| Id | Category / severity | Condition |
|---|---|---|
| `component-interface-unprovided` | risk / medium | an interface that a component uses has no realization |
| `component-interface-unused` | suggestion / low | an interface that is realized and used by nobody |
| `component-dependency-cycle` | risk / medium | components depend on each other in a cycle; edges: `connection` (source depends on target), `dependency`, and `usage` followed by `realization` (the user depends on the provider); strongly connected components, linear |
| `component-fan-out` | suggestion / low | a component with more than 7 distinct dependencies |
| `component-isolated` | suggestion / low | a component without any edge in a diagram with at least three |
| `component-port-unconnected` | suggestion / low | a port no edge names, while the component has edges that name other ports |
| `component-subsystem-empty` | suggestion / low | a subsystem without components |

### Rendering

`mermaid._node_line(node, mid, diagram_type)` draws a component as a rectangle `«component»`, the name and, below a rule, one
line `port <port line>` per port. `interface` in a component diagram is `@{ shape: circle, label: … }` with
the name inside, which grows with long names; names over 18 characters switch to the stadium shape with `«interface»`. The
notation of a kind may depend on the diagram type (ADR-0185) while colours (`classDef`) are per kind. The automatic
`«realizes»`/`«use»` come from `model.EDGE_STEREOTYPES`. Port references are shown at the end of the last label line as
`(source_port → target_port)` with the missing side blank (`mermaid.edge_lines(edge, "component")`). Focus on subsystems and packages, direction, comparison marks and `mermaid_ids` are
unchanged. New diagrams start with direction `LR`.

### Exchange: PlantUML (`plantuml_graph.component_diagram`)

`component "Name" as n_id { portin "port" as n_id_p0 … }` (the ids of `mermaid.id_map`; PlantUML cannot tell in from out, so every
port is a `portin`), `component "Name" as n_id <<subsystem>> { … }` for subsystems, `interface "Name" as n_id`,
`package "Name" as n_id { … }`; `A --> B : label` for connections, `A ..|> I` for a realization, `A ..> I : <<use>>` for a
usage, `A ..> B` for a dependency. A connector that names a port goes from or to the port alias, else the component (checked
with PlantUML 1.2026.8: port aliases are addressable, also inside a subsystem; `tests/test_plantuml_component.py`). Text through
`plantuml_text.plain`.

### Tools and prompt

Graph tools with component descriptions and the `fields` object per kind and edge kind. The prompt
(`prompts/types/component.txt`) asks for the boundary of the system and the main responsibilities first, names components by
responsibility (nouns, not technologies), puts every dependency between components behind an interface when the user wants
a strict design (and says plainly when it does not), and explains when to use the deployment type instead.

### Template (`webshop-components`)

Subsystem *Webshop* with *Web-UI*, *Bestellservice* (ports `orders-in`, `payments-out`), *Katalogservice*; interfaces
*OrderApi* and *CatalogApi* (Web-UI uses `OrderApi`, Bestellservice realizes it and uses `CatalogApi`, Katalogservice
realizes it); outside the subsystem the component *Zahlungsdienst* with a connection from `payments-out`
(protocol `https`).

### Project model (ADR-0182)

`component` links to an element of kind `component`; `subsystem` to `subsystem` or `system`; `interface` and `package`
are not linkable. `component` and `subsystem` are adopted when created (ADR-0164: `adoptable` returns true), so the same
service in a deployment and a component diagram is one element.

**Relation links (second step, own commit).** `component` joins `model.RELATION_DIAGRAM_TYPES`. `connection` and
`dependency` edges may show `communicates` (and `connection` also `data_flow`, as in deployment diagrams; `usage` and
`realization` end at an interface, which is no element, so they are never linked). This adds the type to
`project_model.relation_kinds_for` and `relation_table`, to `shown_relation_kinds`, to the diagram operations
`link_relation`/`unlink_relation`/`set_completeness` of `TYPE_OPERATIONS["component"]`, to the agent's `link_relation`
tool descriptions and to the correspondence rules of `project_review` that today name deployment and sequence. Verified by
a test per touched place and by a project in which deployment and component diagrams show the same relation.

### Frontend

The inspector shows `ports` as a textarea and, on a connector, `source_port`/`target_port` as selects filled from the
ports of the two ends. The relation link section of the inspector appears for connectors after the second step.

## Testing

- `tests/test_component_diagram.py`: kinds, rules in operations and importer, port references (rename refused, removal
  refused, edge rewiring), shared kinds render as before in deployment and class diagrams (golden), Mermaid with hostile
  names and ports, tools and prompt, template, adoption and links, PlantUML text and JAR test with ports.
- `tests/test_rules_component.py`: one test per finding id; cycle through `usage`→`realization`.
- Relation step: `tests/test_component_relations.py` (link, unlink, hidden, completeness, sync rename, correspondence
  rules); `tests/test_correspondence.py` extended.
- Properties and e2e as in the shared design (create from the template, add a port, link a component to an element).

## Migration / compatibility

New type only; the deployment `component` and `dependency` kinds and their rendering are unchanged (golden test). Adding
the type to `RELATION_DIAGRAM_TYPES` changes no stored data.

## Open questions

1. **Does the project model need an interface element?** Linked relations end at components; a provided interface is a
   property of the relation (`protocol`). Proposed: no, until the architecture dialog needs it.
2. **Port directions** (`in`/`out`) as a typed value. Proposed: not now; the name carries it.

## Decisions

- Component diagrams are a new type, not a kinds pack (decided by the maintainer in this session; ADR-0181 records it).
- Ports are node fields; interfaces are nodes; provided/required are edges to the interface (ADR-0181, Accepted).
- Relation links follow in a second commit (ADR-0181, Accepted).
