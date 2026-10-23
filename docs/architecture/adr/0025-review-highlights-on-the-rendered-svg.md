# ADR-0025: Review panel highlights findings on the rendered SVG

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/llm-review.md`

## Context

Issue #96 asks for a UI panel that lists findings and highlights the elements they concern on the
canvas. The server renders Mermaid source and already knows how to mark nodes (`classDef`) and
edges (`linkStyle`) for live changes and version comparisons; the browser renders that source with
the vendored Mermaid 11 and already maps clicked SVG elements back to node ids via the
`mermaid_ids` map of the detail payload. A user clicks through findings quickly; each selection
should show at once.

## Options

1. **Server re-render with marks** for the selected finding (a new query parameter or endpoint).
   Exact and reuses `render(marks=, edge_marks=)`, but costs one request and one full Mermaid
   layout per click, and the layout can shift between selections.
2. **Add a CSS class to the rendered SVG in the browser.** Nodes are found with the same id
   mapping clicks use; edges by the id Mermaid 11 gives edge paths (`L_<from>_<to>_<n>`). Instant,
   no layout change, no API; depends on Mermaid's SVG ids, which are already relied on for clicks
   and are pinned by the vendored build.
3. **Highlight only nodes**, list edges as text. Simplest, but edge findings (a direct external
   access) are exactly the ones that need pointing at.

## Decision

Option 2, because selecting findings must be instant and must not move the diagram; the dependency
on Mermaid's SVG ids is covered by the e2e test.

## Consequences

- `canvas.js` gets an `onRendered` hook so the highlight survives re-renders.
- A Mermaid update that changes edge ids breaks edge highlighting; the e2e test catches it when
  `make vendor` refreshes the build.
- The UI keeps "no business logic": the mapping is presentation of ids the server sent.
