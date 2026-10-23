# ADR-0018: Exchange formats: versioned JSON both ways, PlantUML export only

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/diagram-exchange.md`

## Context

Issue #79 asks for JSON import/export of the diagram and an evaluation of further formats (draw.io,
PlantUML, Structurizr DSL). The model is a containment tree of typed elements plus typed edges, with
no coordinates: layout happens in the browser (Mermaid). Imports are a new way for data to enter the
system, so they must not bypass the validation that operations otherwise guarantee. No new runtime
Python dependency should be needed (CLAUDE.md rule 5).

## Options

Formats:

1. **JSON envelope (`format`, `schema_version`, `diagram`).** Lossless, trivially produced with
   Pydantic, the only format that round-trips. Needed anyway.
2. **PlantUML deployment diagram (export).** Text, no coordinates needed (PlantUML lays out itself),
   nesting and UML stereotypes map directly onto our kinds. Loses properties. No dependency.
3. **draw.io (mxGraph XML).** Every cell needs absolute geometry; we have none on the server, so we
   would either compute a layout (a layout engine in Python) or export positions from the browser's
   Mermaid SVG (fragile coupling to Mermaid internals). High cost.
4. **Structurizr DSL.** C4 model: deployment environments, deployment nodes, infrastructure nodes and
   container instances that must reference a software-system model we do not have. A faithful
   mapping needs modelling concepts Arcviz lacks; a shallow one produces DSL that is valid but
   misleading.
5. **Imports of 2–4.** Parsing other tools' languages, each with its own semantics. Out of scope.

Import target:

6. **Import always creates a new diagram** (via `DiagramRepository.create`).
7. **Import may replace an existing diagram** (a new version of it). Needs a replace operation and a
   decision how the transcript relates to replaced content.

## Decision

Options 1 + 2 + 6: versioned JSON export and import, PlantUML export, imports create new diagrams
only. This is the conservative subset: no new dependency, no layout computation, no destructive
import path. Draw.io and Structurizr DSL are not implemented.

## Consequences

- New module `arcviz/exchange.py` (imports `model`, `mermaid`); two endpoints; two export menu
  entries and an import button.
- The validation an import needs beyond Pydantic (closed kind sets, container parents, unique edge
  ids) lives in `exchange.parse_document`; `integrity_errors()` stays the store's last line of
  defence.
- `schema_version` lets the format evolve; readers must accept every older version.
- **To review:** the format choice (no draw.io/Structurizr) and "import creates a new diagram only"
  stay *Proposed* until the maintainer confirms. Replacing an existing diagram from a file can be a
  follow-up issue built on a dedicated operation.
