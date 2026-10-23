# ADR-0040: Architecture diagrams as Arcviz documents with C4 kinds from a kinds file

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/architecture-documentation.md`

## Context

The arc42 documentation (#110) needs C4 context, container and component views and a deployment
view. The existing docs draw diagrams as hand-written Mermaid or ASCII, which drifts from the code
without anyone noticing. Arcviz edits exactly this kind of diagram and has a versioned JSON exchange format
(ADR-0018) and a renderer. Its built-in kinds are deployment-oriented (`service`, `database`, `vnet`, …);
C4 needs person, software system, container and boundary elements. A kinds file (ADR-0010) can add
kinds at startup without code changes.

## Options

1. **Hand-written Mermaid (or C4-PlantUML) in the pages.** No tooling, but nothing keeps the
   diagrams correct, they cannot be edited in Arcviz, and C4-PlantUML would need a renderer the site does
   not have.
2. **Arcviz documents with new built-in C4 kinds.** This dogfoods the app, but it adds C4 vocabulary to every
   user's tool enums, system prompt and inspector, and M13 is about to make kinds per diagram type
   (#98). Built-in kinds are hard to remove later.
3. **Arcviz documents with a C4 kinds file shipped with the docs**
   (`docs/architecture/diagrams/c4-kinds.json`). The documents are ordinary exports, and the generator
   activates the kinds file while rendering. To edit them, start the app with
   `ARCVIZ_KINDS_FILE` pointing to the file.

The rendered output can either be built while the site is built (a Zensical hook) or be generated
and checked in, with a test that fails when it is stale. Zensical has no plugin API for this yet,
and checked-in output also keeps `make docs` a pure Zensical run.

## Decision

Option 3, with checked-in `*.mmd` renders and a staleness test. This dogfoods Arcviz without changing
its vocabulary for everyone, and the test (plus an import check against the code for the server
component view) makes drift visible in CI.

## Consequences

- `scripts/render_architecture_diagrams.py` (`make diagrams`) must be run after editing a document.
  `tests/test_architecture_docs.py` fails otherwise. It also fails when the renderer's output
  changes (for example a new `classDef`), so renderer changes regenerate the docs diagrams in the same PR.
- The server component view must be updated when imports between its components change. A new module is
  not required in the view, but a changed import between drawn components fails the test. That
  is intended, and it will affect M13 when it changes imports between existing modules.
- C4 notation is approximated by Mermaid flowchart shapes and colours (stadium for persons,
  dashed subgraphs for boundaries), with the kind shown as `«kind»`. Technology lives in the element name.
- When per-type kinds arrive (#98), the C4 kinds may move to a C4 diagram type. The documents stay
  importable as long as their kinds exist.
- **To review:** the maintainer should confirm the kinds file over built-in kinds, and checked-in
  renders over a build hook (status stays *Proposed* until then).
