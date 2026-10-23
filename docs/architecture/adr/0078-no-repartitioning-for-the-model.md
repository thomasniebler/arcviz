# ADR-0078: No repartitioning of the stores for the shared model

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

Cosmos DB partitions by `/diagram_id`; a project is one item in partition `project:<id>` (ADR-0051,
risk R16). The shared model could motivate partitioning by `/project_id` to get transactional batches
over a project's diagrams.

## Options

1. **New container partitioned by `/project_id`** with an `arcviz-migrate` step. Moving a diagram between
   projects would then move every item of it between partitions (delete + create); a new container, a copy
   of every item and downtime or dual writes.
2. **Keep the layout.** The model is part of the project item; propagation writes each diagram in its own
   partition with conditional writes, and the repair (sync) covers partial writes.

## Decision

Option 2: propagation does not need atomicity, and the existing pattern (ordered writes plus an idempotent
repair, ADR-0052) covers it. No store primitive changes; file and sqlite stay as they are; no migration.

## Consequences

- `MAX_ELEMENTS` = 500 per project keeps the project item far below the 2 MB item limit.
- R16 stays open; it is re-evaluated if per-project transactions become a requirement. #143 is unaffected.
