# ADR-0136: A closed set of relation kinds; containment stays `Element.parent`

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/model-relations.md` (M25)

## Context

The owner decided to start with a small, closed set of relation kinds and no OWL/RDF tooling (#203). The issue leaves
open whether containment becomes a relation or stays `Element.parent`.

## Options

1. `contains` as a fourth relation kind, `Element.parent` derived or removed.
2. **Containment stays `Element.parent`**; relations are `communicates`, `deployed_on`, `data_flow`.
3. Both, kept in sync.

## Decision

Option 2. Containment is a tree ("part of"), edited, shown and checked since M18; a relation would allow graphs where
a tree is meant, and option 3 would store the same fact twice. Each kind has rules for the kinds of its ends:
`communicates` connects any two elements, `deployed_on` something that runs (component, subsystem, system, data,
node) with a `node`, `data_flow` any two non-node elements. Allowed properties: protocol, port, direction and mode
(sync/async) for `communicates`, protocol, port and mode for `data_flow`, none for `deployed_on`. No relation connects
an element with itself, and there is at most one relation of a kind between two elements (`communicates` in either
orientation): many views, one relation.

In a deployment view, nesting a linked node inside a linked container reads as `deployed_on` when the container's
element is a `node`, else as containment, which the rules compare with `Element.parent`.

## Consequences

- An element kind change that a relation's rule forbids is refused, like one its views cannot show (ADR-0073).
- A new kind is a code change (table in `project_model.py`, labels in the UI), as for element kinds.
