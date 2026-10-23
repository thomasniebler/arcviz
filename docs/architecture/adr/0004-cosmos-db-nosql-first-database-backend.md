# ADR-0004: Azure Cosmos DB (NoSQL API) as the first database backend

- Status: Accepted
- Date: 2026-10-04
- Spec: `docs/architecture/specs/persistence.md`

## Context

ADR-0002 makes backends pluggable. The first database backend decides which cloud gets a volume-free,
multi-replica deployment first and which SDK the team learns. The current deployment target is Azure
(Container Apps, see `docs/deploy-azure.md`). Candidates all fit the contract: Cosmos DB NoSQL,
Cosmos DB via its MongoDB API, MongoDB (Atlas), DynamoDB, Firestore, Postgres/JSONB.

## Options

1. **MongoDB driver (`pymongo`).** One backend covers MongoDB Atlas, Cosmos DB (Mongo API) and AWS
   DocumentDB. The Cosmos Mongo API is a compatibility layer: not every operator is supported, request
   units are accounted differently, and the emulator story is weaker than for the native API.
2. **Cosmos DB NoSQL API (`azure-cosmos`).** Native SDK, transactional batches within a partition,
   managed-identity auth, Linux emulator, serverless billing. Azure only.
3. **Postgres with JSONB.** Portable across clouds, mature; but a managed instance costs a fixed
   monthly fee even when idle, which the serverless document stores do not.

## Decision

Option 2. The only concrete deployment target today is Azure, and a native SDK avoids debugging a
compatibility layer. MongoDB, DynamoDB and Firestore remain sketched in the spec so the contract is
known to fit them; they are implemented when a deployment needs them.

## Consequences

- New optional extra `arcviz-agent[cosmos]` (`azure-cosmos`, `azure-identity`).
- Data model: one container, partition key `/diagram_id`, item ids `latest`, `version:NNNN`,
  `chat:NNNNNN`; version allocation via deterministic id + `409 Conflict` retry.
- Tests: contract tests run against an in-process fake of the container API in every CI run, and
  against the Cosmos DB Linux emulator or a real serverless account when `ARCVIZ_TEST_COSMOS_URL` is
  set. The emulator has been slow and flaky on GitHub runners; its job is non-blocking until proven
  stable.
- `docs/deploy-azure.md` gets a Cosmos DB variant that drops the Azure Files volume and the
  one-replica limit.
