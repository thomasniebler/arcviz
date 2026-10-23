# ADR-0038: Renderer per diagram type: Mermaid for deployment and activity, own SVG for sequence

- Status: Accepted
- Date: 2026-10-06
- Spec: `docs/architecture/specs/renderer-case-study.md`

## Context

Milestone M13 adds activity (#99) and sequence (#101) diagrams next to deployment diagrams, each type
rendered through a per-type renderer interface (#98). Today everything is rendered by the vendored
Mermaid 11.17.2 in the browser (`htmlLabels`, `securityLevel: "strict"`), and the UI depends on
mapping rendered SVG elements back to element ids (click → inspector, highlights of changed elements
during an agent turn, comparison marks) and on re-rendering after every tool call. The diagram types
are built on UML 2.5.1. Issue #105 asked which renderer each type should use long-term.

The case study rendered the same deployment, activity and sequence example with Mermaid, PlantUML
1.2026.8 (MIT build, SANDBOX profile) and an own SVG renderer (ELK.js layout for deployment/activity,
own row layout for sequence). Key measurements (details and screenshots in the spec):

- Element ids recoverable from the SVG (deployment / activity / sequence): Mermaid 18/18, 36/36,
  16/19 (12 of them only by document order, operands missing); PlantUML 18/18 (6 only by document
  order), 0/36, 0/19; own SVG 18/18, 36/36, 19/19, all by id.
- Warm re-render: Mermaid 85 / 118 (174 with swimlanes) / 40 ms; own SVG 23 / 20 / 1.6 ms; PlantUML
  57 / 15 / 12 ms server time plus network, about one second per call without a long-running JVM.
- UML notation: PlantUML draws all tested constructs; the own renderer draws all of them; Mermaid
  approximates nodes, artifacts and fork bars, loses partitions without its `swimlane` layout, and
  has no found/lost messages or interaction uses.
- PlantUML's preprocessor evaluates user text (`%getenv`, `!include`) unless every character is
  escaped and the SANDBOX profile is on; it needs a JVM service (~300 MB RSS).

## Options

1. **Mermaid for every type.** No new dependency, everything shipped today. Sequence diagrams lose
   found/lost messages and interaction uses, and messages map back only by document order.
2. **PlantUML server-side for every type** (or for activity and sequence). Best UML notation out of
   the box, but no ids in activity and sequence SVGs (the click/highlight/comparison features would
   not work for those types), a JVM service to operate and secure, and an activity syntax that is
   either block-structured (current; experimental `goto` did not draw its edge) or without lanes
   and ids (legacy).
3. **Own SVG renderer for every type**, ELK.js for deployment and activity. Best id mapping and
   speed, full control over UML notation; costs +0.47 MB gzip (ELK), an own swimlane router, and an
   estimated 12–18 developer-days for deployment and activity on top of sequence.
4. **Per type: Mermaid for deployment and activity (with Mermaid's built-in `swimlane` layout),
   own SVG for sequence, PlantUML for export only.**

## Decision

Option 4. Each type gets the cheapest renderer that maps every element back to its id and draws the
type's UML constructs acceptably: Mermaid already does this for deployment and, with
`layout: swimlane`, for activity; only the sequence diagram needs constructs and ids that Mermaid
cannot provide, and its layout is simple enough to own.

## Consequences

- The per-type renderer interface from #98 stays, and M13 proceeds as planned (Mermaid for
  deployment/activity, own SVG for sequence). The activity generator emits `layout: swimlane` front
  matter when the diagram has partitions.
- ELK stays not vendored (ADR-0017 unchanged). Moving deployment or activity to the own renderer is
  a later, per-type change behind the same interface, triggered by a requirement for UML node
  notation or icons on containers, or by unacceptable swimlane routing on real diagrams.
- PlantUML remains an export format (ADR-0018); exporting activity and sequence diagrams is #107. Any
  future server-side PlantUML must escape every non-alphanumeric character as `<U+XXXX>` and run
  with `PLANTUML_SECURITY_PROFILE=SANDBOX`.
- Cloud icons for deployment (#100) come from a vendored Iconify subset (`logos`, CC0-1.0, AWS
  products and Kubernetes); Azure and GCP product icons need a licence check of the vendors' official
  sets before they are vendored.
- Line breaks in names break Mermaid's `sequenceDiagram` and are not shown literally in flowcharts
  (#103); the own sequence renderer is not affected.
- **To review:** the maintainer should confirm the per-type choice and the use of Mermaid's
  `swimlane` layout (status stays *Proposed* until then).
