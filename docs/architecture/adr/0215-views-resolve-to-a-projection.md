# ADR-0215: A view resolves to today's `Diagram` as a read-only projection

- Status: Proposed
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`

## Context

Mermaid rendering, the sequence SVG renderer, about 60 rules, the PlantUML and JSON writers, version comparison and
the prompt summary all read `model.Diagram` (nodes, edges, `interaction`). Rewriting them against a view-plus-model
pair would roughly double the milestone.

## Options

1. **Rewrite every consumer against `View` + model.**
2. **Resolve** a view against the structure and behaviour documents into a frozen `Diagram` (node id = element id,
   edge id = relation id) that every consumer reads unchanged.

## Decision

Option 2: consumers stay as they are; `resolve` is the single place that knows how a view maps to the model.

## Consequences

- A resolved diagram is never saved (`save_latest` of one is a defect, asserted).
- Id shapes change (project-unique element ids, `r<n>` relation ids); consumers that assumed diagram-local slugs or
  `e<n>` must be checked.
- Resolution is memoised per triple of revisions.
