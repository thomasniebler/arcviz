# Roadmap

**As of:** 2026-10-20 · **Status:** proposal; no milestones or issues assigned yet ·
**Based on:** [product direction](index.md), [alternatives](alternatives.md), [ideas](ideas.md)

Goal: turn Arcviz from a diagram and decision tool into an **architecture change-review workflow** for our teams:
change intent → affected views, elements, drivers and decisions → reviewable proposal → approval → (later)
implementation evidence. Two tracks run side by side: **validation** decides whether and for whom, **engineering**
does the items that are worth having either way. Order and gates matter more than dates.

## Track A: validation with internal teams

| Step | What | Done when |
| --- | --- | --- |
| **A1 Operate** | Merge and run the Azure deployment ([PR #217](https://github.com/thomasniebler/arcviz/pull/217)); name an operator; agree data classification, retention and the allowed LLM endpoint with IT/security | A team can sign in through the proxy and use it with approved data |
| **A2 Talk** | 5–8 conversations with architects, tech leads and platform engineers about their **last real architecture change**: documents used, who approved, hours spent, what was missed | We know which changes are frequent and costly, and who owns the review process |
| **A3 Replay** | Two or three of those changes rebuilt in Arcviz (and, for comparison, in one alternative, see [alternatives](alternatives.md)) | Measured: time to a usable model, missed impacts, invented facts, review effort |
| **A4 Pilot** | Two or three teams use Arcviz for real changes over a few weeks | Repeat use after the first week; feedback on what they would drop from their current process |

Stop or change direction when: changes are rare or the current process is good enough; an alternative does the job
comparably; teams stop using it after the novelty; no one can operate it.

## Track B: engineering

### B1 Bring existing diagrams in (first)

Teams will not redraw their landscape. Import whatever A2 shows they have, most likely **draw.io** and
**PlantUML**, possibly Structurizr or LikeC4 DSL. One-way import with documented loss and stable ids beats a
"lossless sync" that is not. Today imports of other formats are out of scope
([diagram exchange](../architecture/specs/diagram-exchange.md)); this needs a spec and an ADR.

### B2 One reviewable changeset across views

`apply_batch` and propose mode work per diagram; a project chat turn is not atomic across diagrams
([risk R17](../architecture/arc42/11-risks-and-technical-debt.md)). A changeset holds the operations on several
diagrams and the shared model against recorded revisions, shows one diff, and is approved or rejected as a whole.

Store constraint: the file, SQLite and Cosmos stores write documents one by one; Cosmos transactions cover one
partition, and every diagram and project has its own. Realistic semantics are therefore *check all revisions first,
apply in a fixed order, compensate (roll back) on failure, report what could not be undone*, not a database
transaction. Design that first; do not promise "no partial state" until it is shown.

- Two diagrams and a shared relation changed in one turn appear as one review.
- A concurrent writer makes the apply fail with a conflict and a fresh preview, never a silent partial apply.
- Rejecting changes nothing persistent.

### B3 Deterministic impact analysis

Reachability over shared elements and relations and the views linked to them, with relation kind and direction. Keep
model facts and LLM explanations apart: the LLM explains and suggests, it never proves. Respect the open world
([ADR-0137](../architecture/adr/0137-open-world-completeness-and-hidden-relations.md)): a missing edge in an
incomplete view is unknown, not absent.

- A service move or interface split lists the affected elements, views, drivers and accepted decisions, each with the
  path by which it was reached.
- Ambiguous mappings show as unknown, not as violations.

### B4 Review report

A Markdown/JSON report per changeset for the review board: intent, baseline revisions, before/after, impact,
relevant drivers, scenarios and accepted decisions, open questions, the reviewer's decision and recorded exceptions.
Reproducible for the same changeset; readable without the chat transcript. Builds on M29 data and
`decision_markdown`.

### B5 Evidence from our repositories and infrastructure (after the pilot shows value)

Read-only links from model elements to our Git repositories and **one** infrastructure format we actually use
(Terraform or Bicep for Azure). Each finding names repository, revision and path; intended configuration is kept
apart from deployed state. Needs a security review of repository credentials first. A second source only after the
first is used.

### C1 UML diagram types for requirements and architecture (maintainer decision, 2026-10-20)

Four more diagram types, so requirements engineers, architects and (through MCP) developers can state goals,
structure and lifecycles in the notation they already know: **use case**, **component**, **class** and **state machine**
diagrams, each on the existing node/edge operations, exports, review and agent tools. This is added beside the tracks above
by decision of the maintainer; it does not change their order. Design:
[More UML diagram types](../architecture/specs/uml-graph-types.md) and the four type specs it links
([ADR-0183](../architecture/adr/0183-graph-types-behind-one-profile-table.md)). Done when each type can be created from a
template, edited by chat and by hand, reviewed, compared, exported to JSON and PlantUML and used over MCP, and the
existing types are byte-identical. Not part of C1: package, object, composite structure, communication, interaction
overview, timing and profile diagrams, and traceability between use cases, drivers and components.

### Later, only when pilots ask for it

- A non-blocking architecture report on pull requests (read-only MCP context for coding agents exists since [agent access](../spec/agents.md)).
- Deterministic checks of accepted decisions (today prompt guidance only); a small rule pack for our network and
  identity conventions.
- arc42 export, component pages, Git as document store (see [ideas](ideas.md)).
- Owners and components from Backstage, if we run it.

## Order

`A1` → `A2` → `B1` alongside `A3` → `B2` + `B3` → `B4` → `A4` → `B5` → later items. `C1` runs beside this order; no
wiki or layout work in parallel.

Cross-cutting: keep stable ids and compatibility with 0.4.x stores and bundles; an ADR for every change to the
consistency or data model; every user-visible change with spec, tests and an eval case.

## How we know it works

| Measure | How |
| --- | --- |
| Adoption | Teams and real changes reviewed per month; use after the first weeks |
| Time to a usable model | From import or first chat to a model the team accepts |
| Review effort | Comparable changes before and with Arcviz, correction time included |
| Impact completeness | Affected elements found vs. a list the team agrees on |
| Unsupported claims | Statements without a model fact, source or "hypothesis" label |
| LLM cost | Cost per turn and per review from `arcviz.llm.tokens` |

Take a baseline in A3 before setting any target.
