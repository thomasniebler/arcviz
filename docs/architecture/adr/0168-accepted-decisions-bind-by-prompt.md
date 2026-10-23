# ADR-0168: Accepted decisions bind the assistants and the reviews by prompt

- Status: Accepted
- Date: 2026-10-13
- Spec: `docs/architecture/specs/decision-support.md`

## Context

A later redesign should not silently contradict an accepted decision. Decisions are free text, so a deterministic
check cannot tell whether a diagram contradicts one.

## Options

1. **Prompt context**: both chat assistants and the AI reviews get the accepted decisions and are told to name a
   conflicting decision (and ask, in the chat) instead of deviating silently.
2. **An extra LLM check** of every proposal against the decisions before it is applied.
3. **Structured constraints** in decisions (e.g. "element X must not talk to Y") checked by rules.

## Decision

Option 1 now (the maintainer chose chat and review). Option 2 is recorded as an idea (`docs/product/ideas.md`);
option 3 would need a constraint language that decisions do not have.

## Consequences

- Only accepted decisions bind; drafts, rejected and superseded ones are left out of the prompts.
- The warning is as good as the model's reading; the review makes conflicts visible after the fact.
