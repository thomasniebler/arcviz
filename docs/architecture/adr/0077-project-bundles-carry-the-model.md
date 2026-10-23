# ADR-0077: Project bundles carry the model; schema version 2 only when there is one

- Status: Proposed (amended by ADR-0079: a single-diagram import drops its links)
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

Project bundles (ADR-0056, `schema_version` 1) embed the project structure and diagram documents. The
import creates fresh diagram ids. Readers before M18 ignore unknown fields and would silently drop a model.

## Options

1. **Always write version 2.** Old readers refuse every new export, also of projects without a model.
2. **Version 2 only when the project has elements or linked sections**, else version 1 (byte-identical to
   before). Version 1 and 2 are both read.
3. **Keep version 1** and add fields. Old readers silently lose the model.

## Decision

Option 2: an old reader refuses exactly the bundles it cannot represent. Element ids are project-scoped,
so the import creates the elements with their ids and the refs inside the diagram documents stay valid;
the import tolerates dangling refs (an exported project may contain them, ADR-0072).

## Consequences

- Single-diagram documents carry `ref` like any other field and keep `schema_version` 1/2 by type.
