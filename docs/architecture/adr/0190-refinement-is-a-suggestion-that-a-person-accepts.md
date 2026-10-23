# ADR-0190: A refinement is a suggestion stored on the requirement; only a person accepts it

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/requirements.md`

## Context

Raw requirements are normalised (a clear statement, a kind, a measurable criterion, a theme, issues such as "vague",
"duplicate of req4", "conflicts with req9"). The user wants to bring requirements "into a certain form" before
anything is derived, and wants to feed them in through chat or MCP. Agents may write requirements directly
(ADR-0186), but a normalised statement is the basis of everything derived later.

## Options

1. **The agent overwrites the fields.** Fast; the original meaning can be silently changed in 300 items.
2. **Refinement as a stored proposal** (ADR-0187 shape). Reuses the machinery; a requirement is a small keyed item and
   a proposal per 25 requirements doubles the writes and splits the review between two lists.
3. **A `suggestion` on the requirement itself** (statement, kind, priority, measure, theme, issues, `duplicate_of`,
   who and when). The fields in force stay as they are; the table shows the suggestion next to them; a person accepts
   (all, a selection, or after editing), rejects, or overrides it.

## Decision

Option 3. `status` moves `raw` → `refined` only when a person accepts a suggestion or edits the fields in the UI;
agents (chat or MCP) can write a suggestion but never `statement`, `kind`, `measure`, `status` or `duplicate_of`.
Re-running the refinement replaces an unaccepted suggestion.

## Consequences

- One list, one review surface, bulk accept; nothing changes silently.
- The UI action "accept" is a REST route (`POST …/requirements/accept`) that agent credentials cannot reach
  (ADR-0173).
- A requirement carries at most one open suggestion; the history of rejected suggestions is not kept.
