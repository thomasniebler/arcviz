# ADR-0211: The gap analysis is a deterministic check per arc42 section, with an optional model pass that only advises

- Status: Accepted
- Date: 2026-10-28
- Spec: [arc42 gap analysis](../specs/arc42-gap-analysis.md)

## Context

`arc42.build` already marks a section without a source in `gaps`, but the strings carry no code, severity or next step,
and they cannot say "the vision has no goals" or "this quality goal has no scenario". Teams want a pointer to what to do
next. Whether existing prose is *good* is a judgement a model can make but a rule cannot.

## Options

1. **Deterministic only.** Free, testable, reproducible; blind to quality of content.
2. **Model only.** Flexible, but not reproducible, costs tokens on every view and can hallucinate gaps.
3. **Deterministic core, optional advisory model pass.** The status and the next steps come from rules; remarks of the
   model are shown on request, marked as advice, never stored or counted.

## Decision

Option 3. The analysis lives in a pure module over the assembled arc42 document; gap ids are stable codes that the UI
maps to a place that fixes them.

## Consequences

- The completeness number is reproducible and cheap; a model outage affects only the advice.
- `arc42.Section.gaps` carries codes; writers still print the texts.
- A new rule needs an id, severity, action, a test and a row in the spec.
