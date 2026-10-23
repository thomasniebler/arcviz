# 1. Introduction and goals

## Requirements overview

Arcviz agent lets an architect or developer edit a UML-style deployment diagram by **chat**, by
**voice** or **directly**, and shows the result **live**. An LLM turns an instruction such as *"add a
vnet to the deployment and move services a, b and c into it"* into tool calls. Each tool call is a validated
operation on a typed model (nodes with kinds, nesting and properties; typed edges). Each applied
operation is rendered and streamed to the browser while the turn is still running.

Main capabilities (details: [user guide](../../guide/index.md)):

| Capability | Where it is specified |
|---|---|
| Chat-driven editing with live rendering, streamed reply text | [Agent](../../spec/agent.md), [LLM layer](../../spec/llm-layer.md) |
| Direct edits without the LLM (inspector, REST operations, atomic batches) | [Operations](../../spec/operations.md) |
| Versions: automatic per turn, manual snapshots, restore, undo/redo, comparison | [Persistence](../../spec/persistence.md), [Rendering](../../spec/rendering.md#comparison-marks) |
| Propose-then-confirm mode with a diff preview | [Propose then confirm](../specs/propose-confirm.md) |
| Architecture review: deterministic rule checks plus an LLM review, suggestions applied as proposals | [Architecture review](../../spec/review.md) |
| Import/export (versioned JSON, PlantUML export), SVG/PNG export, drill-down, voice input and output | [Import and export](../../spec/exchange.md), [Frontend](../../spec/frontend.md) |
| Configurable element kinds, starter templates | [Domain model](../../spec/domain-model.md) |
| Pluggable persistence (files, SQLite, Azure Cosmos DB), optional Git mirror | [Persistence](../../spec/persistence.md) |
| Its own documentation served offline under `/docs` | [Documentation served by the app](../specs/docs-in-app.md) |

Diagram types beyond deployment (activity, sequence) are being added in M13 (#98, #99, #101).

## Quality goals

The five most important quality goals, in priority order. Section 10 refines them into
scenarios and states how well each is met today.

| # | Quality goal | Motivation |
|---|---|---|
| 1 | **Model integrity** (correctness) | Whatever the LLM or a client sends, the stored diagram is always valid: known kinds, no dangling edges, no containment cycles, and every change all-or-nothing. A chat tool is only useful if it can't corrupt your model. |
| 2 | **Recoverability** | Any change, including a bad LLM turn, can be undone or rolled back, and the history survives restarts. Safety comes from versions, not from forbidding changes. |
| 3 | **Live feedback** (usability, responsiveness) | The user watches the diagram change operation by operation and reads the reply as it streams. A turn that takes 20 s must not feel like a 20 s freeze. |
| 4 | **Self-containment and portability** | One process, one image, one wheel. Works offline (vendored Mermaid, docs served by the app), with a local model if wanted, and on a laptop or Azure Container Apps. |
| 5 | **Changeability** | New LLM providers, storage backends, element kinds, diagram types and review rules can be added behind stable interfaces, with offline tests (no API key in CI). |

**Not a quality goal today: security for internet deployments or strict multi-tenancy.** Authentication is
optional and off by default (a shared token, or an authenticating proxy, #82). Behind a proxy, Arcviz knows its
users and keeps them apart: every user has a personal workspace with their own projects, and only a project's owner
shares it with others as viewer or editor (M21, [personal workspaces](../specs/personal-workspaces.md)).
Section 11 lists what remains.

## Stakeholders

| Role | Expectations |
|---|---|
| Architect or developer (user) | Fast, trustworthy diagram editing in natural language. Nothing gets lost, and undo is always possible. German UI. |
| Operator | One container with few, documented environment variables, health probes, persistent data on a volume or in Cosmos DB, and no surprises when scaling. |
| Maintainer and contributors (people and coding agents) | Clear module boundaries, a single mutation path, specs and ADRs that match the code, cheap CI ([ADR-0006](../adr/0006-ci-minutes-single-job-and-milestone-branches.md)), tests without network access. |
| LLM provider (external) | Well-formed tool-calling requests within token budgets. |
