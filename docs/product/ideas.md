# Ideas parking lot

Ideas that came up while building and that are **not planned yet**. Nothing here is a commitment; the
[product direction](index.md) and the [roadmap](roadmap.md) decide focus and order. When an idea is picked up it gets
the team problem it solves, acceptance criteria, a design spec under `docs/architecture/specs/` and issues as usual.

## Direction: from diagram editor to architecture decision tool

Since M29 a project holds drivers, quality scenarios, stakeholders and decisions next to its diagrams and shared
model. Arcviz is turning into a tool for *finding and recording* architecture decisions, not only for drawing them.
The roadmap should decide how far to go in that direction and what stays out of scope.

## arc42 documentation from the project

- Generate (and keep in sync) an arc42 document for a project: 1 introduction and goals from the drivers and
  stakeholders, 2 constraints from the constraint drivers, 3 context and 5 building blocks from the diagrams and the
  shared model, 6 runtime views from the sequence and activity diagrams, 7 deployment view from the deployment
  diagrams, 9 decisions from the project's decisions, 10 quality requirements from the quality goals and scenarios,
  11 risks from the risk drivers and the analyses.
- Open: generated read-only view vs. editable chapters; export format (Markdown, AsciiDoc, the app's own docs site).

## Component documentation as wiki pages

- Every project element (component, system, node, data) gets a page of its own: free text, responsibilities,
  interfaces, the diagrams that show it, its relations, the drivers and decisions that concern it.
- Links between pages and from diagram elements to their page (double click in the inspector).

## Git in the background

- Once there are wiki pages and decision records, a project is a set of documents that benefits from Git: history per
  page, diff and blame, branches for alternatives ("what if we went with option B?"), review through pull requests,
  export to and import from a repository.
- The store already has an optional Git mirror (`ARCVIZ_GIT_DIR`, diagrams only); the question is whether Git becomes
  the source of truth for documents or stays a mirror.

## Smaller follow-ups from earlier work

- **Merge duplicate elements**: the review reports near-duplicate element names (`similar-element-names`); a "merge
  into" action would relink every view and remove the duplicate.
- **Switch off user suggestions**: deployments that must not let users find each other by name need a setting for the
  share dialog's suggestions (ADR-0165).
- **Live refresh** of the drivers, scenarios, stakeholders and decisions dialogs while another user edits them.
- **Check a proposal against the decisions** before it is applied (one more LLM call per proposal; deferred in M29).

## More ideas (2026-10-13)

Candidates collected with the roadmap; the roadmap already contains imports, the changeset, impact analysis, the
review report and repository/IaC evidence.

| Idea | Problem | First experiment |
| --- | --- | --- |
| **Decision trace** | Nobody can tell later why a change was approved | Link scenario → decision → elements → views → changeset in the review report |
| **Uncertainty and provenance in the UI** | Inferred facts look as certain as confirmed ones | Mark source and confidence on elements and findings; ask pilot users what they trust |
| **Network and identity rule pack** | Generic reviews miss our zoning and identity conventions | Ten seeded violations and ten valid cases from our own landscape |
| **Owners and contracts** | Impact review lacks owners, API and event contracts | Import a minimal set from Backstage or OpenAPI files |
| **Model or view authority per property** | Model, views and IaC disagree with no rule for which wins | Write down which source owns which property before any drift check |

## Parked

UML diagram types beyond the four of roadmap item C1 (package, object, composite structure, communication, interaction overview, timing, profile), whiteboarding, a layout editor, another diagram DSL, cloud inventory or a CMDB, autonomous
changes to code or infrastructure, compliance claims from diagrams.
