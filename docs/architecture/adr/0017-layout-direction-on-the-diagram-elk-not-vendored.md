# ADR-0017: Layout direction on the diagram, ELK not vendored

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/layout-direction.md`

## Context

Diagrams are always rendered `flowchart TB`. Issue #78 asks for a selectable direction per diagram
and, optionally, the ELK layout engine for large nested diagrams. Mermaid 11 ships ELK as a separate
package (`@mermaid-js/layout-elk`, registered with `mermaid.registerLayoutLoaders`). Measured for
this decision: the self-contained minified ESM build of `@mermaid-js/layout-elk@1.0.1` is about
2.5 MB (`elk-*.mjs` 2.25 MB plus a bundled KaTeX chunk of 0.27 MB), on top of the 3.57 MB
`mermaid.min.js` we already vendor, i.e. roughly +70 % static payload in the wheel, the Docker image
and every first page load. Its compatibility with the pinned Mermaid 11.17.2 would also have to be
tracked separately by `scripts/vendor_mermaid.py`.

## Options

1. **Direction as a view setting** (query parameter / browser storage). No model change, but the
   agent cannot set it, it is not versioned, and exports and the Git mirror would not see it.
2. **Direction as a model field changed by an operation.** Versioned, undoable, available to the
   agent and every render path; costs one field, one operation and one tool.
3. **Additionally vendor ELK** and add a `layout` field (`dagre | elk`). Better layouts for big
   nested diagrams at +2.5 MB per load and a second vendored artefact to keep compatible.
4. **Load ELK from a CDN on demand.** No payload cost, but breaks the offline guarantee the vendored
   Mermaid exists for.

## Decision

Option 2. ELK (options 3 and 4) is not adopted now: the size cost is not justified while no
user-reported diagram is large enough for dagre to fail, and option 4 contradicts the offline goal.

## Consequences

- `Diagram` gains `direction` (default `TB`); `operations.set_direction` and an agent tool exist;
  `fingerprint()` ignores the default so stored fingerprints remain valid.
- Revisit ELK when large diagrams become a real problem; then add a `layout` field next to
  `direction` and extend `scripts/vendor_mermaid.py` to pin the matching layout package.
- **To review:** the maintainer should confirm not vendoring ELK (status stays *Proposed* until then).
