# Alternatives and build vs. buy

**Research date:** 2026-10-13 · **Status:** desk research from vendor documentation, not a hands-on evaluation

For an internal tool the question is not "is there a market gap" but **"why build this instead of licensing or
adopting an existing tool?"** This page records what the alternatives cover and what would decide the question.
Capabilities change quickly; re-check before relying on a row.

## What the alternatives cover

| Tool | Published capabilities | Relevance for us | Sources |
| --- | --- | --- | --- |
| **Archyl** (SaaS) | C4 model with AI repository discovery, model as YAML, ADRs, change requests, deterministic conformance rules, GitHub Actions drift checks, MCP | Closest overlap with the planned change review; check data residency and hosting options | [Docs](https://www.archyl.com/docs), [change requests](https://www.archyl.com/docs/features/requests), [conformance rules](https://www.archyl.com/docs/features/conformance-rules) |
| **IcePanel** (SaaS) | Collaborative C4 model, views, drafts, ADRs, API, MCP | Strong on model-first collaboration and drafts | [Model and drafts](https://icepanel.io/blog/death-of-architecture-diagrams), [API](https://developer.icepanel.io/) |
| **LikeC4** (open source, MIT) | Architecture-as-code DSL, one model with many views, query API, MCP | Free, self-hosted, Git-native; a candidate to integrate with rather than replace | [DSL](https://likec4.dev/dsl/intro/), [MCP](https://github.com/likec4/likec4/blob/main/packages/mcp/README.md) |
| **Structurizr** (open source / on-prem) | C4 model and DSL, views generated from one model | Mature C4 authoring, self-hostable | [Docs](https://docs.structurizr.com/) |
| **Eraser** (SaaS) | Prompt-to-diagram, repository context, Git sync, MCP | Covers AI diagram generation; little on decisions | [Docs](https://docs.eraser.io/what-is-eraser) |
| **Backstage** (open source) | Service catalog, ownership, relations, docs-as-code | If we run it, it is the natural source for owners and components | [Catalog](https://backstage.io/docs/features/software-catalog/creating-the-catalog-graph/) |
| **draw.io / Visio, wiki** | Free-form diagrams and pages | What most teams use today; the baseline to beat | — |

Holori and Ilograph (cloud discovery, interactive walkthroughs) were looked at and are not close alternatives.

## What speaks for building

- **Already built:** typed operations on three diagram types, a shared model with relations, drivers, scenarios and
  decisions (M29). The marginal cost is the roadmap, not a new tool.
- **Data stays in our tenant:** self-hosted, with the LLM endpoint our organisation approves (Azure OpenAI with Entra,
  PR #217). SaaS tools need a data-processing and security review before architecture data goes there.
- **Fits our process:** we can shape review, approval and integrations around how our architecture board works.

## What speaks against

- **Maintenance:** someone has to run, upgrade and support the service; that is a standing cost, not a one-off.
- **Overlap:** Archyl and IcePanel already offer change requests, ADRs and conformance checks; LikeC4 and Structurizr
  are free and self-hosted.
- **Bus factor:** a tool built mostly by one person is a risk for teams that come to depend on it.

## What would decide it

1. A security and data-classification answer on whether SaaS tools may hold our architecture data at all. If not,
   only the self-hosted options (LikeC4, Structurizr, Arcviz) remain.
2. A short hands-on comparison on **two real recent changes** (for example a service split and a network boundary
   change) in Arcviz and in one or two alternatives: time to a usable model, missed impacts, invented facts, effort
   to review. Record the artifacts; do not score from vendor pages.
3. An owner for operating Arcviz if we keep building.

If a licensed or open-source tool does the job comparably, prefer it, or integrate with it (LikeC4/Structurizr
import) instead of competing.

## Wording to avoid

Do not describe Arcviz internally as the only tool with multiple views, MCP, ADRs or AI review, and do not claim
compliance or that diagrams match production. Several alternatives already offer the former; the latter cannot be
shown.
