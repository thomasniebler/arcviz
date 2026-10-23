# ADR-0053: Existing diagrams move into a default project, lazily and by CLI

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/projects.md`

## Context

Installations have diagrams but no projects. Every diagram must belong to a project after the upgrade.
The maintainer proposed (without choosing) that all existing diagrams go into one default project, from
where users move them. `arcviz-migrate` exists for copying between backends.

## Options

1. **One default project, created on first use.** The first project listing (the UI does one on start)
   adopts every diagram without a project into `default` ("Standardprojekt"); `arcviz-migrate --projects`
   does the same explicitly. No downtime, no required manual step.
2. **One project per diagram.** Nothing to sort out later, but dozens of one-diagram projects defeat the
   purpose and are tedious to merge.
3. **Explicit migration only.** Predictable, but a forgotten step leaves an empty navigator.

## Decision

Option 1, as proposed: it is cheap, automatic and reversible by moving diagrams, and it is the same
repair step that recovers orphans (ADR-0052).

## Consequences

- The default project is an ordinary project; deleting it is allowed when empty, and it is recreated
  when the next orphan or unplaced new diagram needs it.
- The CLI's entry point moves to `arcviz/cli.py`; `arcviz-migrate --from/--to` also copies projects.
