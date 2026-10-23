# Product direction

**Status:** working hypotheses, not a committed plan · **Reviewed:** 2026-10-13 ·
**Baseline:** [main at 88321f6](https://github.com/thomasniebler/arcviz/commit/88321f625a4ee7694e3942651575855f7968702b)
(release 0.4.1 plus the M29 decision support merged after it)

Arcviz is an **internal tool**: it is run for the architects and engineering teams of our own organisation, not sold.
This section records *why, for whom and in what order* we develop it. Designs, decisions and issues stay under
`docs/architecture/`, `docs/spec/` and GitHub Issues.

- [Alternatives and build vs. buy](alternatives.md): what existing tools cover and why we build anyway.
- [Roadmap](roadmap.md): validation with internal teams, then the change-review slice and integrations.
- [Ideas parking lot](ideas.md): ideas that are not planned yet.

## Thesis

Arcviz helps a team **review a planned architecture change** against its views, quality drivers, accepted decisions
and, later, implementation evidence, before the change is built. It answers *what changes, why, what could break,
and how do we know the implementation matches the decision?* Drawing diagrams is the means, not the goal: AI diagram
generation is well served by other tools (see [alternatives](alternatives.md)).

This is a hypothesis. It holds only if internal teams actually bring their changes to Arcviz instead of to slides,
wiki pages and review meetings.

## Users and the job

- **Users:** software architects, tech leads and platform engineers; secondary: security reviewers and developers who
  need architecture context (also in coding agents).
- **Sponsor:** whoever owns the architecture review process (architecture board, platform or engineering lead).
- **Job:** prepare and review a risky change (service split, network or identity boundary, migration, integration,
  resilience redesign) without losing the rationale, the affected elements, the constraints or the diagrams.
- **Current substitutes:** draw.io/Visio and wiki pages, Mermaid/PlantUML in repositories, slide decks for review
  boards, an LLM in the IDE.

## What exists on main

| Capability | Status |
| --- | --- |
| LLM-driven typed operations on deployment, activity and sequence diagrams, live rendering, PlantUML export | Implemented ([agent](../spec/agent.md), [diagram types](../spec/diagram-types.md)) |
| Shared project elements and relations, linked views, consistency findings, propose/confirm | Implemented ([model relations](../architecture/specs/model-relations.md)) |
| Versions, undo/redo, conditional writes per diagram, live collaboration, sharing | Implemented ([persistence](../spec/persistence.md)) |
| Drivers, stakeholders, quality scenarios, what-if analysis, ADR decisions (M29) | Implemented ([decision support](../architecture/specs/decision-support.md)) |
| Coding agents read components, responsibilities, relations and code links over MCP and propose changes that people apply; links from components to code paths | Implemented ([agent access](../spec/agents.md)); no repository connection yet |
| Accepted decisions guide agents and reviews | Prompt guidance only, no deterministic check |
| One reviewable change across several diagrams and the shared model | Not implemented; a project chat turn is not atomic across diagrams ([risk R17](../architecture/arc42/11-risks-and-technical-debt.md)) |
| Import from draw.io, PlantUML, Structurizr or LikeC4; links to code or IaC | Not implemented ([exchange](../architecture/specs/diagram-exchange.md) lists imports as out of scope) |
| Hosting in our Azure tenant with Cosmos DB and Azure OpenAI | Open in [PR #217](https://github.com/thomasniebler/arcviz/pull/217) |

Views stay primary: findings are resolved by people, the model never overwrites a view
([ADR-0138](../architecture/adr/0138-views-primary-findings-resolved-by-people.md)). Any roadmap item has to respect
that or replace the ADR.

## Boundaries

**Emphasise:** architecture changes and decisions, cross-view consistency, reviewable proposals with human approval,
links to our repositories and infrastructure, self-hosting with the LLM endpoint our organisation approves.

**Leave out unless a team needs it:** more UML diagram types, whiteboarding, pixel-perfect layout, an enterprise
architecture repository or CMDB, cloud discovery, automatic changes to code or infrastructure, compliance claims.

## Assumptions to test

1. Teams spend real effort on architecture changes and would use a tool that shortens preparation and review.
2. Impact analysis is only trusted when it rests on a model the team accepted (and later on repository or IaC
   evidence), not on what the LLM guesses.
3. Existing diagrams can be brought in cheaply enough; nobody redraws a landscape to try a tool.
4. Licensed tools (see [alternatives](alternatives.md)) do not already cover the need well enough to stop building.

If these do not hold after the first round with internal teams, change the direction before building more of the
roadmap.
