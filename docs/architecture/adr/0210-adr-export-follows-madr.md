# ADR-0210: The exported ADR follows MADR 4

- Status: Accepted
- Date: 2026-10-28
- Spec: [ADR testability and the MADR export](../specs/adr-testability.md)

## Context

The Markdown ADR is a free layout of our fields. Teams that already keep ADRs in a repository expect a known schema,
and the new testability section needs a place in it.

## Options

1. **Keep the current layout** and append the new section.
2. **Nygard's classic ADR** (Title, Status, Context, Decision, Consequences): has no place for options, drivers or
   pros and cons, which we store.
3. **MADR 4** (context and problem statement, decision drivers, considered options, decision outcome, consequences,
   pros and cons of the options): maps almost one to one onto the stored fields.

## Decision

Option 3, with one extension section, *Annahmen und Testbarkeit*, which MADR leaves to "more information".

## Consequences

- The export becomes a documented, widely recognised schema; headings are translated, the structure is not.
- The old layout changes: tests and docs are updated; nobody parses it back (import reads JSON bundles only).
