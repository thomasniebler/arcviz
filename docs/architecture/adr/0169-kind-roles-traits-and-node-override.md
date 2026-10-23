# ADR-0169: Kinds get a role and traits; a node property overrides them

- Status: Accepted
- Date: 2026-10-19
- Spec: `docs/architecture/specs/deployment-traits.md`

## Context

`database-without-replication` fires for every built-in `database` node without a replication edge or property, also
for managed stores that the platform replicates (Cosmos DB, DynamoDB, Spanner). The rules compare `node.kind` with the
nine built-in kinds, so the cloud-pack kinds (`azure_cosmos_db`, `aws_rds`, …) take part in no deployment rule. Users
reach for a generic `database` node as often as for a pack kind.

## Options

1. **Node property only.** Document `redundancy` and read it in the rules. Fixes the false warning; pack kinds stay
   outside the rules; every user sets it on every node.
2. **Kind roles and traits, plus a node property that overrides them.** Pack kinds say what they are (`role`) and what
   the platform guarantees (`traits`); a node can still say otherwise.
3. **A deployment model** (environments, platforms, SKUs, SLAs as elements). Right answer for "how available is it
   really", but a new concept with its own UI, review rules and migration.

## Decision

Option 2: it fixes the generic-`database` case through the property, brings pack kinds into the rules through the
role, and keeps the guarantee where it is documented (the kind pack). Option 3 stays possible later and would consume
the same trait values.

## Consequences

Pack kinds with a database role are newly reported when they lack redundancy (intended). `redundancy` is a free-form
property with documented values, so no model change or migration. The kinds-file format gains two keys (extend-only,
strict). The `built_in` claim is trusted, never verified.
