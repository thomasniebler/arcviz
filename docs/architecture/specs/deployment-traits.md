# Deployment traits: managed services and built-in redundancy

- Status: Implemented
- Issue: #236
- ADRs: [0169 Kind roles and traits, node-level override](../adr/0169-kind-roles-traits-and-node-override.md)
- Builds on: [Configurable kinds](configurable-kinds.md), [Cloud icons and kind packs](cloud-icons.md),
  [Rule checks](rule-checks.md), [Assistant prompts](assistant-prompts.md)

## Summary

The rule `database-without-replication` reports every `database` node without a `replication` edge or a replication
property as a high risk. That is wrong for a managed, cloud-native store such as Azure Cosmos DB, DynamoDB or
Spanner, which the platform replicates by design. The rules also only know the nine built-in node kinds: the kinds of
the cloud packs (`azure_cosmos_db`, `aws_rds`, …) are invisible to every deployment rule, so a Cosmos DB drawn with
its pack kind gets neither the warning nor the strength.

This change gives kinds a **role** (which built-in kind the rules treat them as) and **traits** (what the platform
guarantees), and gives every node a documented **`redundancy`** property that overrides the kind. The replication rules
read the effective redundancy instead of only edges and a fixed property list.

## Goals

- A managed store with built-in replication is not reported as a single point of failure, whether it is drawn as a
  generic `database` node (with a property) or as a pack kind (by trait).
- Pack kinds take part in the deployment rules through their role.
- The node-level property wins over the kind, in both directions: a single-region or non-redundant tier of a
  built-in-replicated service can say `redundancy=none` and is reported again.
- The assistants know the property and use the pack kind or the property when a user names a managed service.

## Non-goals

- Modelling environments, hosting platforms, SKU tiers or SLAs as first-class concepts (a possible later
  milestone; this change only fixes what the rules assume).
- Geo or zone redundancy rules. `built_in` means "the platform replicates inside its availability boundary"; it makes
  no claim about regions.
- Changing the existing `replication` edge or the `REPLICATION_PROPERTIES` list: both keep working unchanged.
- Rules for activity and sequence diagrams.

## Design

### Kind definition (`kinds.py`, kinds files and packs)

`NODE_KEYS` gains two optional keys; both are validated strictly like the others:

```json
"azure_cosmos_db": {
  "icon": "azure:azure",
  "style": "fill:#eff6fc,stroke:#0078d4,color:#002050",
  "role": "database",
  "traits": { "redundancy": "built_in" }
}
```

- `role`: one of `ROLES`, the built-in leaf kinds the rules reason about: `database`, `storage`, `queue`, `service`,
  `function`, `component`, `gateway`, `external`. Leaf kinds only (containers have no role). Default: none (the kind
  is ignored by the rules, as today).
- `traits`: an object of trait name → value. Only `redundancy` exists, with the values of `REDUNDANCY` below. Unknown
  trait names or values fail the start with the file and kind named (`KindConfigError`).

`KindCatalog` gains `node_roles: dict[str, str]` and `node_traits: dict[str, dict[str, str]]`; `model.py` exposes
`role_of(node)` (a built-in kind is its own role, a configured kind its `role`, else `None`) and
`kind_trait(node, name)`. The catalogue stays process-wide and read at call time, so tests get the built-ins back from
the existing autouse fixture.

### Node property `redundancy`

`redundancy` is a free-form string property like any other (no model change), with documented values:

| Value | Meaning |
|---|---|
| `built_in` | the platform replicates the store; no replica nodes or edges are needed |
| `none` | explicitly not redundant (a single-instance or single-region tier), even for a kind that is `built_in` |

Anything else (or unset) falls back to the kind's trait. The existing replication properties (`replicas`,
`replication`, `ha`, `high_availability`, `zone_redundant`, `geo_redundant`) and `replication` edges keep counting as
redundancy and come first: a store the diagram shows with replicas is replicated, even if it also says
`redundancy=none`.

Effective redundancy of a data store node `n`, in order:

1. a `replication` edge at `n`, or a truthy replication property → **replicated** (as today);
2. `n.properties["redundancy"]` is `built_in` → **built-in**; is `none` → **none**;
3. the kind trait `redundancy` → **built-in** if `built_in`;
4. else **none**.

### Rules (`rules.py`)

- The helpers `_databases`, `COMPUTE_KINDS`, `DATA_STORE_KINDS` compare `role_of(node)` instead of `node.kind`, so pack
  kinds with a role join `database-without-replication`, `replicated-database`, `public-entry-without-gateway`,
  `external-direct-to-data-store` and the other deployment rules.
- `database-without-replication` skips *replicated* and *built-in* databases.
- `replicated-database` (strength) also reports *built-in* databases, with the message
  "Die Datenbank „X“ wird von der Plattform repliziert (managed)." and no edges.
- A new low-severity suggestion is not added: `built_in` on a node asserts the architect's knowledge and is trusted.

### Kind packs

Roles and traits are added to the pack kinds where the platform guarantee is documented and stable, and left out where
redundancy is optional:

| Kind | role | redundancy |
|---|---|---|
| `azure_cosmos_db`, `aws_dynamodb`, `gcp_spanner` | `database` | `built_in` |
| `azure_sql`, `azure_postgresql`, `aws_rds`, `aws_aurora`, `gcp_cloud_sql`, `gcp_bigtable` | `database` | – (zone/multi-AZ is a configuration; set `redundancy=built_in` on the node when it is on) |
| queues, object storage and function kinds of the packs | their built-in counterpart | decided in the issue, per kind |

The exact list is fixed during implementation from the providers' documentation and recorded in
`docs/guide/cloud-icons.md`.

### Assistants

`prompts/types/deployment.txt` gets one bullet: managed stores are drawn with the provider's pack kind where one
exists, else as `database` with `redundancy=built_in`; a managed store gets no replica nodes. `/api/config` lists
each kind's role and traits so the prompt and the UI can show them.

### Documentation

`docs/guide/elements.md` (property `redundancy`), `docs/guide/cloud-icons.md` (roles and traits),
`docs/guide/elements.md` (kinds file keys `role`, `traits`), `docs/spec/` pages for kinds, rules and agent.

## Testing

- `tests/test_kinds.py`: valid `role`/`traits`, every invalid form names file and kind; leaf-only roles.
- `tests/test_rules.py`: table over (kind, property, edge) → finding; a generic `database` with `redundancy=built_in`
  is not reported and is a strength; `azure_cosmos_db` is not reported; `azure_cosmos_db` with `redundancy=none` is
  reported; `azure_sql` is reported until the property is set; a `replication` edge still works.
- `tests/test_properties.py`: the strategy may draw `redundancy` values; rules never raise.
- `tests/test_diagram_types.py` and the eval cases: one case where the assistant is asked for Cosmos DB.
- Backwards compatibility: no stored data changes; `tests/test_compat_*` stay green.

## Migration and compatibility

No migration. Existing diagrams keep their findings except pack-kind databases, which are now seen by the rules
(a `aws_rds` that has no replica is newly reported; this is intended and noted in the release notes).

## Open questions

1. Should pack kinds with a compute role (e.g. an Azure Function) take part in `public-entry-without-gateway`? Proposed:
   yes, via `role`, no extra rule.
2. Should `redundancy=built_in` on a built-in `database` of a diagram without any `region` be accepted silently
   (proposed: yes)?

## Decisions

ADR-0169. Open questions: both proposals taken (pack compute kinds take part through `role`; `redundancy=built_in` is accepted on any database without a region check). The shipped list is in `arcviz/kindpacks/*.json`: roles for databases, storage, queues, functions, gateways and services; `built_in` only for Cosmos DB, DynamoDB and Spanner.
