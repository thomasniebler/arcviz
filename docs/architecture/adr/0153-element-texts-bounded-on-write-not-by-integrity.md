# ADR-0153: Element names and descriptions are bounded on write, not by integrity

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/robustness.md`
- Refines: [ADR-0135](0135-relations-in-the-project-document.md)

## Context

The project item holds the shared model. Relations are bounded (ADR-0135, about 1 MB worst case), element names and
descriptions were not (M18), so 500 elements with long descriptions reach Cosmos DB's 2 MB item limit, after which no
project write succeeds (#212, arc42 R22). Projects stored before any bound must stay usable.

## Options

1. **A bound in `integrity_errors()`.** Every later write of an existing oversize project would fail.
2. **A bound on the values a write sets (add, update, import).** Stored projects stay readable and writable.
3. **A size check of the whole project item before each write.** Exact, but refuses unrelated writes of a large
   project and needs a serialisation per write.

The values: with `MAX_ELEMENT_DESCRIPTION` = 2 000 (like node notes) 500 elements alone take about 3 MB in three-byte
characters; 100 / 300 keep every element and relation at its bounds at 1.67 MB of UTF-8.

## Decision

Option 2 with `MAX_ELEMENT_NAME` = 100 and `MAX_ELEMENT_DESCRIPTION` = 300 characters (stripped), plus a variant of
option 3 for what the bounds cannot cover: the Cosmos SDK escapes non-ASCII characters (six bytes per `€`) and it is not
documented which encoding the 2 MB is counted on, so `ProjectService` measures the document as the SDK sends it and
refuses a write above `MAX_PROJECT_BYTES` (1.9 MB) only when it makes the document larger.

## Consequences

New and changed elements fit the budget with realistic text; imports shorten longer texts (with a warning) instead of
refusing them, so an export of a project stored before the bounds can always be imported again; the guard refuses the
rest with an actionable 400 on every backend. Membership writes may overshoot the budget up to 2.0 MB, so creating,
moving and adopting diagrams, and the repair, never fail on a full project. A project is never locked: older projects with longer texts keep them and take every write that
does not grow them past the budget. One serialisation per project write.
