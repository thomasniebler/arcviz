# ADR-0220: Existing diagrams are migrated into the model at start-up

- Status: Proposed
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`

## Context

Stores hold diagrams with their own content back to v0.1.7, some linked to model elements and relations, many not.
Exports and bundles of every schema version exist outside the app.

## Options

1. **Lazy conversion on first open.** Spreads the cost, but the model is incomplete until every diagram was opened,
   and rules and agents see a partial model.
2. **Start-up migration per project** (best effort, repeatable with `arcviz-migrate --model`), the same code
   converting old imports.

## Decision

Option 2: the model is complete from the first request; nodes merge by `ref`, else by name and metaclass; conflicts
keep the newest value and are reported, never dropped silently.

## Consequences

- A migration report per project (keyed document `migration`) is shown until dismissed.
- Versions before the migration stay readable as legacy snapshots; restoring one re-imports it as a change set; undo
  never crosses the migration.
- The compat tests of v0.1.7, v0.1.8, M20 and v0.4.0 assert that projections equal the stored diagrams.
