# ADR-0064: The project LLM review is one tool call over a budgeted project digest

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-review.md`

## Context

The diagram review is one `complete_tool` call with only `submit_review`, validated finding by finding,
never raising (ADR-0024). A project may hold many diagrams; the prompt must stay bounded; the review must
judge the whole, not repeat per-diagram reviews.

## Options

1. **One diagram review per member plus a project call.** n + 1 paid calls per click, slow, and the
   per-diagram reviews already exist.
2. **One call over a digest**: structure plus each diagram's summary with an equal share of a fixed
   budget (100 000 characters, at least 2 000 each), the rule findings marked as already reported, one tool
   `submit_project_review` whose findings name a diagram (id or unique title), optional elements/edges of
   it and an optional section; validated like the diagram review.
3. **A tool loop** in which the model fetches diagrams on demand. Better for huge projects, but not
   bounded in cost and unlike every other review path.

## Decision

Option 2: one bounded, predictable call; collapsed summaries are flagged as `partial` and the prompt
forbids findings about hidden contents, as in the diagram review.

## Consequences

- Very large projects get collapsed diagram summaries; `llm.partial` tells the UI.
- Invalid references are dropped instead of failing the review.
