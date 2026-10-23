# ADR-0070: Model elements in the project document, referenced from views by an optional ref

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

Milestone M18 asks for one model per project with diagrams as views (option B of M16): project-wide
elements with identity across deployment, activity and sequence diagrams. Today every diagram is a
self-contained document with full-snapshot versions and content fingerprints (ADR-0003); rendering,
exchange, comparison and the property tests all work on one diagram alone. ADR-0051 reserved the project
document for a shared model. Data written by v0.1.7 must keep working unchanged.

## Options

1. **Full model/view split.** Diagrams hold only placements of model elements; names and kinds live in the
   model. Every renderer, exporter and the comparison need the model; old versions render with today's
   names; every stored diagram and version must be converted. Weeks of work plus a data migration.
2. **Optional references.** Elements live in the project document (`Project.elements`); a view element
   (deployment/activity node, sequence lifeline) may carry `ref` = element id and otherwise stays as it
   is. Unset refs are omitted when dumping, so old documents, fingerprints and exports are byte-identical.
   Names are kept in sync by propagation (ADR-0071).
3. **A separate model document per project.** Like 2, but a second document with its own revision; a
   section linked to an element (ADR-0074) would then span two documents.

## Decision

Option 2: it delivers element identity, rename-everywhere and consistency checks while every diagram stays
self-contained, needs no migration, and keeps the model and the section tree in one conditional write.

## Consequences

- `project_model.py` (pure), `Node.ref`, `Lifeline.ref`, `Project.elements`, `Section.element`; a
  `ModelService` orchestrates writes across the project and its diagrams.
- Behaviour (actions, messages, flows) is never linked; model relations stay derived (follow-up issue).
- A diagram moved to another project keeps refs that dangle there (ADR-0072; follow-up issue).
