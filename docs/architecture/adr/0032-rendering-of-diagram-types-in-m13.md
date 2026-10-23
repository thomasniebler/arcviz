# ADR-0032: Rendering of diagram types in M13

- Status: Accepted
- Date: 2026-10-06
- Spec: `docs/architecture/specs/diagram-types.md`, `docs/architecture/specs/activity-diagrams.md`,
  `docs/architecture/specs/sequence-diagrams.md`, `docs/architecture/specs/cloud-icons.md`
- Implements: [ADR-0038 Renderer per diagram type](0038-renderer-per-diagram-type.md) (Accepted), based on the
  [renderer case study](../specs/renderer-case-study.md) (#105)

## Context

M13 adds activity (#99) and sequence (#101) diagrams next to deployment diagrams and cloud icons for
deployment diagrams (#100). The UI needs every rendered element to map back to its model id (click →
inspector, highlight of changed elements during an agent turn, review highlights, comparison marks)
and re-renders after every tool call. The maintainer decided that all types follow UML 2.5.1
(ADR-0030) and that every renderer sits behind a per-type interface (#98). The renderer case study
(#105) rendered the same deployment, activity and sequence example with Mermaid 11.17.2, PlantUML
1.2026.8 and an own SVG renderer; the maintainer accepted its recommendation as ADR-0038. Its measured
results, which this ADR applies to M13:

| | Deployment | Activity | Sequence |
|---|---|---|---|
| Element ids recoverable from the SVG: Mermaid | **18/18** by id | **36/36** by id (also with `layout: swimlane`) | 16/19, 12 of them only by document order, operands missing |
| … PlantUML | 18/18, 6 only by order | **0/36** | **0/19** |
| … own SVG | 18/18 | 36/36 | **19/19** by id |
| Warm re-render: Mermaid | 85 ms | 118 ms (174 ms with swimlanes) | 40 ms |
| … own SVG | 23 ms | 20 ms | **1.6 ms** |
| … PlantUML | 57 ms + network | 15 ms + network | 12 ms + network; ~1 s per call without a long-running JVM |

UML notation: Mermaid draws deployment diagrams acceptably, draws real activity partitions only with
its `swimlane` layout (with dagre they become free-floating boxes), and has no found/lost messages or
interaction uses in sequence diagrams. PlantUML draws every tested construct but writes no element
ids into activity and sequence SVGs, needs a JVM service (~300 MB RSS) and evaluates user text in its
preprocessor (`%getenv`, `!include`) unless every character is escaped and the SANDBOX profile is on.

## Options

1. **Mermaid for every type.** Sequence diagrams would lose found/lost messages and interaction uses,
   and messages would map back only by document order.
2. **PlantUML server-side for activity and sequence.** Best notation out of the box, but 0/36 and 0/19
   ids (clicks, highlights, review and comparison would not work), a JVM to operate and secure.
3. **Own SVG for every type** (ELK.js for deployment and activity). Best ids and speed, but +0.47 MB
   gzip for ELK, an own swimlane router and an estimated 12–18 developer-days beyond sequence.
4. **Per type, as recommended by the case study (ADR-0038).**

## Decision

Option 4, implemented in M13 behind `render.Renderer` / `render.RENDERERS` (ADR-0033 for the payload):

- **Deployment: Mermaid flowchart** (`mermaid.render`), unchanged; cloud icons through Mermaid's icon
  shape and vendored Iconify packs (`@{ icon: … }`, `registerIconPacks`; ADR-0036). Icon nodes keep
  their ids (`g.icon-shape`).
- **Activity: Mermaid flowchart with UML shapes** (`f-circ`, `fr-circ`, `cross-circ`, `diam`, `fork`);
  when the diagram has ActivityPartitions the source starts with the front matter
  `config: layout: swimlane`, so top-level partitions are drawn as lanes (ADR-0038); otherwise dagre.
- **Sequence: own SVG renderer** (`sequence_svg.render`, server-side, ADR-0035): every UML construct of
  #101 in UML notation, 19/19-style id mapping through `data-id` on every element.
- **PlantUML only as an export format** (ADR-0018): deployment today, activity and sequence in #107.
  No server-side PlantUML rendering.

## Consequences

- One renderer per type can be swapped later without touching the others; the triggers named in
  ADR-0038 (UML node notation or container icons for deployment, unacceptable swimlane routing) would
  move deployment or activity to the own renderer.
- The SVG export inserts its init directive after any front matter, because Mermaid requires front
  matter to come first.
- Mermaid's swimlane lanes carry ids without the render prefix; the click mapping (`nodeIdForSvgId`)
  accepts both forms.
- Review highlights (ADR-0025) work on Mermaid renders as before and on the own SVG through `data-id`.
