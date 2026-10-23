# ADR-0141: Correspondence rules across diagrams; `message-without-connection` generalised

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/model-relations.md` (M25)

## Context

#206 asks for pure project rules over the snapshot: gaps (a relation shown in no structural view, a connection without
a relation, a complete view missing a relation) and contradictions (properties between views, an element deployed on
two nodes, containment), German messages naming both diagrams, and `message_without_connection` generalised onto
relations with a stable finding id or a documented change.

## Decision

- New rules: `relation-not-shown`, `connection-without-relation`, `complete-view-missing-relation`,
  `relation-property-conflict`, `deployed-on-several-nodes`, `containment-conflict`, `invalid-relation-link`.
- The relation gap rules (`relation-not-shown`, `connection-without-relation`, `complete-view-missing-relation`) run
  only when the project has at least one relation: projects that never adopted relations get no new suggestions.
  Contradiction rules over views (`deployed-on-several-nodes` by nesting, `containment-conflict`) run always, like the
  M18 rules.
- `message-without-connection` keeps its id and message; a `communicates` relation between the two elements now counts
  as a connection, and whether a deployment view draws that relation is reported by `relation-not-shown`. A pair
  without relation and deployment connection is reported once (by this rule).
- The LLM review digest lists relations after elements within its own budget.
- `relation-not-shown` is a model-level gap, never a view's omission: a low suggestion naming the deployment views that
  could draw the relation, without views that hide it, and not raised when one of them is complete for the kind
  (`complete-view-missing-relation` reports that). In projects with relations exactly one rule reports a message that
  no relation backs. Edge directions are compared relative to the relation's orientation.

## Consequences

- Finding ids of the M18 rules are unchanged; old projects without relations get exactly the M18 findings plus the
  two view contradiction rules.
