# ADR-0073: Abstract element kinds and which view elements may represent them

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

Diagram kinds are type-specific and disjoint (deployment: service, vnet, …; activity: UML ActivityNodes;
sequence: lifelines), and the deployment catalogue is configurable (ADR-0037). A model element must be
linkable from all three types.

## Options

1. **No kind on elements.** Anything links to anything; a database could be a swimlane.
2. **Reuse a diagram type's kinds** (e.g. deployment kinds) for elements. Couples the model to one type
   and to the configurable catalogue.
3. **A small abstract vocabulary** (actor, system, subsystem, component, node, data) plus a table of which
   view element kinds may represent which element kinds; configured deployment kinds get a permissive
   default (container: node/system/subsystem; leaf: any).

## Decision

Option 3: it catches nonsense links (a data store as an actor) with a clear error, stays independent of
the kinds file, and gives "promote" a sensible default kind. Behavioural activity nodes (actions, control
nodes) are not linkable.

## Consequences

- A kind change of an element is checked against every usage first; a view kind change that breaks
  compatibility later is reported by the review (`incompatible-element-link`).
