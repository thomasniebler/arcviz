# ADR-0140: The agent links matching relations and proposes missing ones

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/model-relations.md` (M25)

## Context

#209: when the agent draws an edge or message between two linked elements and a matching relation exists, it links it;
when none exists, the spec decides whether it creates one or proposes it in its reply.

## Decision

- The agent links a matching relation in the same turn (`link_relation`); the prompt says so and the eval set checks
  it. The link is not made by code after `add_edge`: which relation fits is a judgement when several kinds could
  (a `connection` may show `communicates` or `data_flow`).
- When no relation exists, the agent does not create one silently: it says in its reply which relation it would add and
  creates it (`create_relation`) when the user asked for it or agrees. The model is shared by every diagram of the
  project; a relation invented in one chat would raise findings in other people's diagrams.
- `create_relation`, `update_relation` and `link_relation` are refused in propose mode (a proposal changes one diagram,
  ADR-0060, ADR-0076); `set_completeness` is an ordinary diagram operation.
- Relations appear in the prompt after the elements, within the model summary budget.

## Consequences

- An edge drawn without its relation is reported by `connection-without-relation` (only in projects with relations).
