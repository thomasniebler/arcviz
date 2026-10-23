# ADR-0009: Optional edge direction overriding the kind's arrow

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/edge-attributes.md`

## Context

Edge kinds map 1:1 to Mermaid arrows (`connection` `-->`, `association` `---`, `replication`
`<-->`, …), so the arrow heads are implied by the kind. Issue #71 asks for an explicit direction
(one-way, bidirectional, none) and a properties dict such as protocol/port, backward compatible with
stored diagrams. Stored edges have no direction field.

## Options

1. **Optional `direction` (`forward|both|none`, `None` = kind default)** that only changes the
   arrow heads; the kind keeps the line style. Old edges and new edges without a direction render
   unchanged; no migration.
2. **Mandatory direction, filled from the kind on load.** Simpler rendering, but loading would
   rewrite every stored edge and changing an edge's kind would no longer change its arrow.
3. **More edge kinds** (`connection-both`, …). No model change, but multiplies the kind list the
   model and the user must choose from.

Rendering of properties: show all properties on the edge, none, or only the well-known
`protocol`/`port`.

## Decision

Option 1 with the values `forward`, `both`, `none` (plus `default` as an explicit reset in
operations), and only `protocol`/`port` rendered on the edge, because it is invisible for existing
diagrams and keeps edge labels short. The issue's wording "one-way/bidirectional" maps to
`forward`/`both`; `forward` makes explicit that the head is at the target.

## Consequences

- Edge JSON gains `direction` and `properties`; clients that ignore unknown fields are unaffected.
- Mermaid needs the dotted/thick variants with two and no heads (`<-.->`, `-.-`, `<==>`, `===`).
- Configurable edge kinds (#72) can declare a line style and default direction instead of an arrow.
- Other edge properties are only visible in the inspector, the summary and `get_diagram`.
