# ADR-0019: Compare versions on the server as one merged render

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/version-comparison.md`

## Context

Issue #80 asks to render the difference between two versions with added, removed and changed
elements highlighted, reusing the existing highlight mechanism. Versions are full snapshots
(ADR-0003), so any two states can be loaded and compared. The UI has no business logic by design
(architecture overview), Mermaid source is produced on the server, and the highlight is a
`classDef` emitted by `mermaid.render`.

## Options

1. **Two renders side by side** (old and new), each with its own highlights. No merge logic, but
   the layouts differ (dagre lays out each graph independently), so the eye has to match boxes
   across two pictures; removed elements are only visible on the left.
2. **Diff in the browser** from the two JSON snapshots, then patch the SVG. Puts model logic into
   `app.js` and couples it to Mermaid's SVG structure.
3. **Diff on the server, one merged render.** A pure `diff` module computes the change sets and a
   merged diagram (new state plus removed elements in their old place); `mermaid.render` marks them
   with additional classes and `linkStyle`. One picture, one layout, testable in Python, the UI only
   displays.

## Decision

Option 3: it reuses the render path and the highlight classes, keeps logic out of the browser, and
shows removed elements in context.

## Consequences

- New module `arcviz/diff.py` (imports `model`), new endpoint `GET /compare`, two optional render
  parameters (`marks`, `edge_marks`).
- The merged diagram is a render-only object; it is never persisted or passed to operations.
- Removed nodes whose old container no longer exists are drawn at the top level, which can look
  surprising; the legend and banner explain the colours.
- **To review:** status *Proposed* until the maintainer confirms the merged view (vs. side by side).
