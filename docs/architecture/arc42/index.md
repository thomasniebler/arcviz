# Architecture documentation (arc42)

This is the architecture documentation of Arcviz agent, following the twelve sections of
[arc42](https://arc42.org) with [C4](https://c4model.com) context, container and component views.
It describes the system as a whole and **assesses** it: how well it meets its quality goals, and which
risks and technical debt are known. Each risk is linked to the issue that tracks it.

It does not repeat details. Those live in three places, and the sections link to them:

| Where | What |
|---|---|
| [Technical specification](../../spec/index.md) | The system as implemented, one page per subsystem; updated with the code |
| [Design specs](../index.md#specifications) | The design of each change, written before it was implemented |
| [Decision records](../adr/index.md) | One decision each: context, options, choice, consequences |

## Sections

1. [Introduction and goals](01-introduction-and-goals.md): requirements overview, quality goals, stakeholders
2. [Constraints](02-constraints.md)
3. [Context and scope](03-context-and-scope.md): C4 system context
4. [Solution strategy](04-solution-strategy.md)
5. [Building block view](05-building-block-view.md): C4 containers and components
6. [Runtime view](06-runtime-view.md): chat turn, direct edit, propose and confirm, review, undo
7. [Deployment view](07-deployment-view.md)
8. [Crosscutting concepts](08-crosscutting-concepts.md)
9. [Architecture decisions](09-architecture-decisions.md)
10. [Quality requirements](10-quality-requirements.md): quality tree and scenarios with their status
11. [Risks and technical debt](11-risks-and-technical-debt.md)
12. [Glossary](12-glossary.md)

## The diagrams are Arcviz diagrams

The C4 and deployment views are drawn with Arcviz itself
([ADR-0040](../adr/0040-architecture-diagrams-as-arcviz-documents.md)). Each is an ordinary diagram
export in `docs/architecture/diagrams/` (`*.arcviz.json`). `scripts/render_architecture_diagrams.py`
renders it with Arcviz's own Mermaid renderer into the `*.mmd` file that these pages include.
`tests/test_architecture_docs.py` fails when a rendered file is out of date. It also fails when the
server component view no longer matches the imports in the code.

The C4 element kinds (`person`, `software_system`, `container`, boundaries, …) come from the kinds file
`docs/architecture/diagrams/c4-kinds.json`. To edit a view:

```bash
ARCVIZ_KINDS_FILE=docs/architecture/diagrams/c4-kinds.json make run
# Importieren -> docs/architecture/diagrams/c4-containers.arcviz.json, edit (chat, inspector),
# Export -> JSON, save it over the file, then:
make diagrams
```

The runtime views in section 6 are Arcviz sequence diagrams (`runtime-*.arcviz.json`), edited the same way and
rendered by `make diagrams` into SVG files (#120).
