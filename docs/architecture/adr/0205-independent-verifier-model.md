# ADR-0205: A different model verifies what the steward proposes

- Status: Accepted
- Date: 2026-10-26
- Spec: `docs/architecture/specs/project-steward.md`

## Context

LLM output can be wrong with confidence; the existing review is one call of the same model and offers findings only on demand. An unattended process that fills the proposal panel must not spread its own mistakes. The user decided that a different model is fine for verification.

## Options

1. **No verification.** Cheapest; every hallucinated item reaches a person.
2. **Same model checks itself.** Cheap; shares the blind spots of the author.
3. **A second, different model (`ARCVIZ_VERIFIER_PROVIDER`/`_MODEL`) gets the candidate, the cited objects and the digest, not the author's reasoning, and returns supported/unsupported/unclear.** Costs one call per candidate; only `supported` passes.

## Decision

Option 3. Without a configured verifier the steward still produces deterministic (rule-based) items and drops all LLM candidates.

## Consequences

Two models to configure and pay; a rejection rate that we measure in the evals (`scope: steward`); the verifier prompt becomes part of the quality of the product. The verifier equal to the main model stops the start.
