# ADR-0041: arc42 as a linking layer over the specs and ADRs, with an explicit assessment

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/architecture-documentation.md`

## Context

The repository documents the system in three places:
- `docs/spec/` (as implemented, one page per subsystem);
- `docs/architecture/specs/` (designs of changes);
- `docs/architecture/adr/` (decisions).

These answer "how does X work" and "why X", but not "what is the system, how well does it meet its
goals, and where are the risks". The maintainer asked for arc42 with C4 views and an honest assessment
of the design, linking instead of duplicating. M13 is about to add sequence diagrams (#101) that the
runtime views should use, but it is not merged yet.

## Options

1. **One long arc42 page.** Easy to search and print, but a single page of several thousand words,
   and every edit touches one file that other sessions also edit.
2. **One page per arc42 section** under `docs/architecture/arc42/`, each linking to the specs and ADRs
   it relies on. Sections stay short, and a change to one concern edits one page.
3. **Fold arc42 into the existing pages** (for example, extend `docs/architecture/index.md` and `docs/spec/index.md`).
   No new structure, but it mixes design, as-implemented content and assessment, and it collides with
   parallel edits to those pages.

For the runtime views before M13 is merged: wait for M13 (this blocks #110), use Mermaid
`sequenceDiagram` blocks now and convert them later, or write them as prose only.

## Decision

Option 2, with explicit assessment sections:
- section 10 has quality scenarios with a status of met, partly met or not met, and evidence;
- section 11 has risks and debt ranked by severity, each linked to an issue.

Problems found while writing get an issue instead of a sentence. The runtime views are plain Mermaid
sequence blocks, marked as temporary, with follow-up #120 to convert them to Arcviz sequence documents
once #101 is merged.

## Consequences

- arc42 pages hold overview, rationale and assessment. Details (endpoints, data formats, algorithms)
  are linked, not copied, so a change to them updates `docs/spec/` and not arc42, unless it changes a
  quality scenario's status or a risk.
- Sections 10 and 11 go stale when issues are fixed. The PR that fixes a listed risk updates its row
  (the same rule as the spec, CLAUDE.md rule 1).
- The runtime views are not checked by the diagram tests until #120 converts them.
- **To review:** the maintainer should confirm the page split and the assessment format (status
  stays *Proposed* until then).
