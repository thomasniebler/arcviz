# ADR-0181: Component diagrams: ports are node fields, interfaces are nodes, relation links come second

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/component-diagrams.md`

## Context

The maintainer chose a new `component` type over a kinds pack for the deployment type (a pack cannot say what a provided or
required interface is, nor check it). UML draws a provided interface as a lollipop and a required one as a socket on a port
of a component; Mermaid cannot draw either. Ports are owned by their component and named in connectors. Connectors can show
the project model's relations (ADR-0139), but only between linked ends, and an interface is no element.

## Options

1. **Interfaces as nodes, provided/required as edges (`realization`, `usage`) to them; ports as a node field on the component,
   referenced by name from connectors.**
2. **Ports as child nodes of a component** (kind `port`). Own ids and shape, but a component becomes a container (and a
   container `component` conflicts with the shared leaf kind of ADR-0185), and subgraph titles are single-line.
3. **Ports and interfaces as edge properties only** (`interface: OrderApi` on a connector). Smallest, but no interface
   element to review (unprovided, unused) and no place for its description.
4. **Relation links together with the type** versus **in a second commit.** Linking touches `project_model`, the agent's
   link tools and the correspondence rules of the project review; a type without links is already usable.

## Decision

Options 1 and 4's second commit: interfaces are nodes, `realization` and `usage` are edges to them, ports are the field `ports` of
a component that connectors name through `source_port`/`target_port`; relation links for `connection` and `dependency` follow in
a commit of their own.

## Consequences

- The picture shows interfaces as circles with edges, not as lollipops and sockets.
- Port names are checked on every rename and removal (a rename that would orphan a connector is refused).
- A new type in `RELATION_DIAGRAM_TYPES` must be checked in every place that names deployment and sequence there.
