# ADR-0179: Class members are node fields, not child nodes

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/class-diagrams.md`

## Context

A class diagram needs attributes, operations and literals per classifier, drawn as compartments. The graph model has nodes with
`properties`, a `description` note, and parent/child containment. The diff, summary, comparison marks and the focus
navigation work on nodes.

## Options

1. **Members as text lines in typed node fields** (`attributes`, `operations`, `literals`), bounded per list and line, drawn
   as compartments in the class label.
2. **Members as child nodes** (kinds `attribute`, `operation`) of a classifier that renders as one box. Each member has an id,
   so comparison, links and per-member operations come for free; but the node count and the summary budget grow by an
   order of magnitude, containers render as subgraphs unless special-cased, a classifier is both container and leaf, and
   the agent needs one tool call per member.
3. **A structured `Member` model** (visibility, name, type, multiplicity, default) on the node. Exact and checkable; the cost is a
   model change, new operations and a parser for notation users type in, long before anyone needs the checks.

## Decision

Option 1. Members are one-line text in UML notation; the bounds protect the project document; a later structured model can
read the stored text.

## Consequences

- Comparison marks the class as changed, not the member; the comparison view shows the changed field text.
- No type checking of member types against other classes; the review rule `class-duplicate-member` reads the member
  name from the text.
- The inspector needs a textarea per list field (a list is one member per line).
