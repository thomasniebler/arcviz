# ADR-0178: State machines: simple and composite states, regions as containers

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/state-machine-diagrams.md`

## Context

A UML State is simple or composite depending on whether it owns regions; `Node.is_container` is decided by the kind alone, and the
Mermaid renderer draws every container kind as a subgraph, an empty one too. Orthogonal regions need a container between the
composite state and its states. A transition's label is `trigger [guard] / effect`.

## Options

1. **Two state kinds plus a region kind**: `state` (leaf), `composite_state` (container), `state_region` (container, only inside
   a composite state). A state becomes composite through `update_node(kind=…)`.
2. **One `state` kind that is a container.** A simple state would render as an empty subgraph with a title, not as a
   state box, unless the renderer draws containers without children as leaves, a special case that deployment diagrams
   rely on not existing.
3. **Regions as a typed field** (`regions: 2`) with the children assigned by index. Hides structure the operations and the
   importer should validate, and breaks `parent`.

## Decision

Option 1. The trigger is the edge `label`; `guard` is the existing top-level argument; `effect` is a typed field (ADR-0177).
Composite states with several regions hold only regions; with one implicit region they hold states directly.

## Consequences

- A kind change can fail because of children or edges that would break a rule; the message names them.
- Top-level orthogonal regions are not modelled (a composite state wraps them).
- Composite-state behaviours are not drawn (single-line subgraph titles), only exported.
