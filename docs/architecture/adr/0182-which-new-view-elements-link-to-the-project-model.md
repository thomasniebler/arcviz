# ADR-0182: Which new view elements link to the project model

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/uml-graph-types.md`

## Context

The shared project model (ADR-0070..0078) links view elements to project elements; ADR-0164 adopts structural views when they are
created. `project_model.linkable_kinds` and `adoptable` say which kinds link and which are adopted, and project elements have
the abstract kinds actor, system, subsystem, component, node, data. Behaviour (activity actions, messages) is never linked.
The new types mix structure (classes, components, actors, subjects) and behaviour (states, use cases). A project of 40 classes
must not flood the 500-element model.

## Options

1. **Link and adopt every structural kind** of the new types.
2. **Link structural kinds, adopt only the ones that other diagrams mention**: actors, subjects, components, subsystems; classes
   are linkable and adopted on request (`adopt_all`).
3. **Link nothing in the new types** until traceability is designed.

## Decision

Option 2: `actor` ↔ element `actor`, `subject` ↔ `system`/`subsystem`, `component` ↔ `component`, `subsystem` ↔ `subsystem`/`system`,
`class`/`enumeration`/`data_type` ↔ `data`, `interface` (class diagram) ↔ `component`; not linkable: states and pseudostates, use cases,
the component diagram's interface and package, class packages. Adopted at creation: actors, subjects, components and subsystems.
Relation links: component diagrams only, second step of ADR-0181.

## Consequences

- Actors, systems and components are one element across diagrams from the first diagram on; domain classes are not
  forced into the project model.
- Class diagrams carry no relation links; UML associations are not architecture relations.
- Traceability (use case to driver or component) remains a follow-up.
