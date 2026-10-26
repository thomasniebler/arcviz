# ADR-0206: Project proposals carry the operations that fix findings

- Status: Accepted
- Date: 2026-10-26
- Spec: `docs/architecture/specs/project-steward.md`

## Context

`PROPOSABLE_OPERATIONS` covers drivers, scenarios, decisions and `link_requirements` only. Fixes for model-level review findings (elements, relations) and the vision cannot be proposed today, and at most five project proposals may be pending per project.

## Options

1. **Keep the set.** No change; the steward can propose only objects, fixes stay manual buttons.
2. **Extend the set** by `set_vision`, `add_element`, `update_element`, `add_relation`, `update_relation`, `remove_relation` (never deletion of elements) and give the steward one replaceable pending proposal.
3. **Also LLM-written diagram edits as diagram proposals.** Most automation; floods the capped pending list and needs per-diagram freshness.

## Decision

Option 2; diagram-level LLM edits are a follow-up (open question in the spec).

## Consequences

The proposal panel shows more kinds of items; `projects.apply` still validates at apply time, so a wrong item fails alone. Deleting stays a person's act (ADR-0198).
