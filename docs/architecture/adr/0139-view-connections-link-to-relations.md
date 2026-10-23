# ADR-0139: Deployment edges and sequence messages link to relations

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/model-relations.md` (M25)

## Context

#204: `Edge.ref` and a message `ref` link a view connection to a relation, valid only when the connection's ends are
linked to the relation's ends; the spec decides whether activity edges can stand for `data_flow` and the direction
rules.

## Decision

- Deployment edges and complete sequence messages link; activity edges do not (they connect actions and control nodes,
  which are behaviour and never linked, ADR-0073; a flow between partitions does not exist in UML).
- Edge kinds map to relation kinds: `connection` → communicates, data_flow; `dependency`, `association`,
  `replication` → communicates; `deploys` → deployed_on; configured kinds → communicates, data_flow. Messages →
  communicates.
- Orientation: `communicates` either way (requests and replies), `deployed_on` either way (the element kinds already
  say which end runs where), `data_flow` only in the relation's direction.
- Internal operations take the relation's kind and ends from the model service (`link_relation`, `sync_relation`,
  `clear_relation`); `unlink_relation` is public. When a view's ends change (a node relinked or unlinked, a message's
  sender or receiver changed), the relation links touching it are cleared in the same operation. A kind change of a
  linked edge that could not show the relation is refused.
- No verify-after-write as in ADR-0128: a link names its relation by id only, so a link racing a relation change draws
  nothing wrong; the review reports it (`invalid-relation-link`) and `sync` clears it.

- Nesting counts as showing `deployed_on` when the target is the nearest ancestor standing for a `node` element,
  through any other containers. One pure definition (`project_model.shown_views`) serves the review and the model
  answer; the UI mirrors it and both are tested against one fixture.

## Consequences

- Moving a diagram to another project or importing it alone drops its relation links and hidden markers with its
  element links (ADR-0079).
