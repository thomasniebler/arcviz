# ADR-0167: An analysis is kept only as the decision it led to

- Status: Accepted
- Date: 2026-10-13
- Spec: `docs/architecture/specs/decision-support.md`

## Context

A what-if analysis is one LLM call; its result changes with every run and with the model. Kept analyses would pile up
and go stale as the diagrams change. What matters later is what was decided and why.

## Options

1. **Store every analysis** with its scenario. Full history, mostly noise, stale quickly.
2. **Store nothing.** Simple, but the reasoning behind a choice is lost.
3. **Store an analysis only when the user decides**, as an ADR draft built from it (context, options with pros and
   cons, decision, consequences), edited and accepted by the user.

## Decision

Option 3 (the maintainer's choice). The analysis answer is shown and discarded; choosing an option creates a draft
decision whose text the user owns.

## Consequences

- Re-running an analysis can give other options; the decision records the ones that were weighed.
- The decision is the reviewable artefact (status, Markdown export), not the model's raw answer.
