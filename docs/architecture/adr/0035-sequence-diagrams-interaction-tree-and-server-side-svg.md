# ADR-0035: Sequence diagrams as an ordered interaction tree, drawn server-side as SVG

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/sequence-diagrams.md`

## Context

Sequence diagrams (#101) follow the UML 2.5.1 Interactions chapter: lifelines ordered left to right,
messages and fragments ordered top to bottom, combined fragments nesting operands that contain further
fragments, activations spanning messages. The maintainer decided on an own SVG renderer behind the
per-type renderer interface (ADR-0032). Two questions remain: how to store the content, and where to
render. The graph model (`nodes` dict, `edges` list) has no order for nodes, edges cannot nest, and an
edge needs two endpoints (found and lost messages have one). Fingerprints and equality ignore dict
order.

## Options

Content:

1. **Encode in nodes and edges** (lifelines as nodes, messages as edges ordered by list position,
   fragments as container nodes, membership in properties). Reuses generic code, but nesting of
   messages, lifeline order and one-ended messages need encodings that every generic function would
   misread.
2. **An `Interaction` field**: ordered lifelines, an ordered fragment tree (discriminated union on
   `element`), executions; nodes and edges stay empty. Explicit, matches the specification, and order
   is part of the data (so fingerprints see reordering).

Rendering:

1. **Server-side SVG (Python)** from the model. One code path for the UI, the Git mirror and exports;
   testable with property tests in Python; escaping in one place. Text widths must be estimated.
2. **Client-side SVG (JS)** from the diagram JSON. Exact text metrics, but the Git mirror and other
   clients get nothing, and layout logic would be tested only in Node.

## Decision

Content option 2 and rendering option 1. The renderer estimates text widths generously; the
frontend inserts the SVG after stripping active content as a second line of defence.

## Consequences

- Generic node/edge features see empty collections for sequence diagrams; features that cannot work
  on the interaction (comparison) answer 400 until implemented (#108).
- The SVG is self-contained (scoped `<style>`, markers), so the export downloads it as is.
- If the renderer case study (#105) recommends another engine, only `render.RENDERERS["sequence"]` and
  the renderer module change; the model stays.
