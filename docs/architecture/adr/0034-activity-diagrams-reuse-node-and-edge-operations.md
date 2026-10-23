# ADR-0034: Activity diagrams reuse the node and edge operations

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/activity-diagrams.md`

## Context

Activity diagrams (#99) are graphs: ActivityNodes connected by ActivityEdges, grouped by
ActivityPartitions. The node/edge operations of deployment diagrams (`add_node`, `move_node`,
`add_edge`, …) already validate kinds per diagram type (ADR-0031). UML calls the elements
ActivityNode and ActivityEdge, so the generic names are UML terms too. ActivityEdge has a `guard`;
`Edge` has a free-form `properties` dict but no guard field. Adding a field to `Edge` changes the JSON
of every deployment edge and therefore every stored fingerprint unless special-cased.

## Options

1. **Reuse the node/edge operations** with the activity catalogue, a `guard` argument stored as the
   edge property `guard`, and a few UML rules checked when the diagram is an activity diagram.
2. **Own operations** (`add_action`, `add_control_node`, `add_control_flow`, …) and own model fields.
   Most explicit, but duplicates validation, rollback-sensitive code, tools and inspector support.
3. **A `guard` field on `Edge`.** Typed, but changes every serialised edge; fingerprints need a
   special case for edges.

## Decision

Option 1. The operations, inspector, focus, comparison, exchange and property tests work unchanged;
the tool descriptions and enums say ActivityNode/ActivityPartition/ControlFlow; the guard is a
first-class tool argument even though it is stored as a property.

## Consequences

- The property key `guard` is reserved on activity edges; the inspector shows it among the
  properties.
- UML rules (no edges at partitions, no incoming edge at an initial node, no outgoing edge at final
  nodes, directed edges) live in `operations` and `exchange._rule_errors` and must stay in sync; the
  property tests check that every reachable state passes the import rules.
