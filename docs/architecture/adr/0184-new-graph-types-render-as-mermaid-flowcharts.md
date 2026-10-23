# ADR-0184: The new graph types render as Mermaid flowcharts

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/uml-graph-types.md`

## Context

Activity diagrams already render as Mermaid flowcharts with UML shapes (ADR-0032, ADR-0038); sequence diagrams have their own SVG
renderer because Mermaid cannot express them. The four new types differ in how much of their UML notation a flowchart
can draw. A probe of the vendored Mermaid 11.17.2 (see the shared design) shows: no ellipse, no markers at the source end of an
edge, no hollow triangle or diamond, no left-aligned label lines, no stick figure; but `circle`, `stadium`, `f-circ`, `fr-circ`,
`sm-circ`, `cross-circ`, `fork`, `diam`, multi-line labels, `--o`, `==o`, `-.->`, edge text and subgraphs.

## Options

1. **Mermaid flowchart for all four** (as activity). One pipeline: ids, focus, highlight, comparison marks, sanitising, frontend
   mapping, Git mirror `diagram.mmd` all exist. UML notation is approximated.
2. **Native Mermaid `classDiagram` and `stateDiagram-v2`**, flowchart for the others. Closer to UML for two types, but each
   needs its own id mapping, highlight, comparison marks, focus and sanitising path, and the state diagram cannot show typed
   guard/effect text the way the flowchart can; use case and component still need the flowchart.
3. **An own SVG renderer with an own layout** (as sequence). Exact UML notation; a layout engine to write and maintain for
   graphs, which the renderer case study (#105) judged the most expensive option.

## Decision

Option 1, decided by the maintainer for this work. Where the flowchart cannot draw a UML symbol the profile uses the nearest
shape and, for edges, an automatic stereotype text; the guide and each type spec list the approximations.

## Consequences

- All generic features work on day one for every new type.
- Aggregation and composition edges point from part to whole, because Mermaid can only mark the target end; the PlantUML export
  swaps the orientation back (class spec, open question 1).
- A later move to an own renderer for one type (ADR-0038 allows one renderer per type) does not change the model.
- A Mermaid upgrade may change the probe results: a rendering test per notation row fails visibly.
