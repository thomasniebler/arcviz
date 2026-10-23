# ADR-0152: The prompt summary cuts edges, then leaves, to respect its budget

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/robustness.md`
- Refines: [ADR-0093](0093-notes-in-the-prompt-are-budgeted.md)

## Context

`Diagram.summary(budget)` could only shorten notes and collapse containers with children (ADR-0093). A flat diagram
ignored the budget: 2 000 top-level services gave a 147 351-character system prompt at a budget of 12 000, re-sent on
every round (#115, arc42 R8). Sequence diagrams had no shortening at all.

## Options

1. **Cut lines after collapsing: edges first, then leaves, then container lines, each counted with a `get_diagram`
   hint.** Generic for every shape; what the model loses it can fetch.
2. **Group leaves into synthetic buckets.** Keeps more structure, but invents elements the model might reference.
3. **Refuse the turn when the summary is too large.** Unusable for large imported diagrams.

## Decision

Option 1, with the bound `len(summary(budget)) <= budget + SUMMARY_SLACK` (1 000) for every diagram, checked by a
Hypothesis property; a cut summary shows at most 200 characters of the title.

## Consequences

The prompt stays bounded for any diagram. Edges go first because the model can read them with `get_diagram` and they
are the bulk of flat diagrams; the beginning of the summary is unchanged, so prompt caching of the diagram part is not
affected more than before. The top level is hidden only when it does not fit on its own.
