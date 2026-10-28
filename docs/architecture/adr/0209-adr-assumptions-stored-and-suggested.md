# ADR-0209: Assumptions and a falsification criterion are stored on the decision, suggested by the model and confirmed by a person

- Status: Accepted
- Date: 2026-10-28
- Spec: [ADR testability and the MADR export](../specs/adr-testability.md)

## Context

An ADR names options and a choice, but the worth of a choice depends on assumptions that could often be checked
cheaply. Teams rarely write those down, and a model can propose assumptions and tests from the decision text, but may
also invent nonsense or tests that cannot decide anything.

## Options

1. **Compute at export time.** A model derives the section whenever an ADR is exported. Nothing to review or edit, the
   export is not reproducible, costs tokens on every export.
2. **Stored fields, written by hand.** Reviewable and reproducible, but a blank field is rarely filled.
3. **Stored fields, with an on-demand model suggestion a person confirms.** Same pattern as ADR-0202.
4. **Let agents write the fields without a person.**

## Decision

Option 3: `Decision.assumptions` (statement, test, effort, status, evidence) and `Decision.falsification` are stored,
validated by the operations, and omitted from dumps while empty. The model only suggests, on demand
(`POST …/decisions/testability`, validated, never stored); the person edits and saves.

## Consequences

- The export is deterministic and the section is reviewable; old data and fingerprints are unchanged.
- Invalid model output costs nothing: unusable items are dropped and counted.
- Bundles that use the fields are `schema_version` 10.
- The project can count accepted decisions without assumptions (gap analysis, ADR-0211).
