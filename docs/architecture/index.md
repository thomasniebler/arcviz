# Architecture

This section is the **design** documentation of Arcviz agent: an overview of the system with its
module boundaries, the design specifications of changes (`specs/`, written before the change is
implemented) and the record of the decisions behind them (`adr/`). The
[technical specification](../spec/index.md) describes the system as it is implemented and is updated
together with the code; a design spec here says where the implementation should go and why.
Changes to the code that touch a boundary, an interface or a decision update these documents in the
same pull request; the workflow is described in `.claude/skills/implement/SKILL.md`.

The [architecture documentation (arc42)](arc42/index.md) describes the whole system with C4 views and
assesses it: quality scenarios with their status, risks and technical debt.

## System overview

A chat message is turned by an LLM (tool calling) into validated operations on a UML 2.5.1 diagram of
one of several [types](specs/diagram-types.md), which is persisted, versioned and rendered live by the
type's renderer (Mermaid, or an own SVG renderer).

```
chat message ─▶ LLMProvider.run_turn ─▶ DiagramAgent.chat ─▶ operations.apply ─▶ Diagram
                  (arcviz/llm)            (arcviz/agent.py)    (arcviz/operations.py)  (arcviz/model.py)
                                               │                                          │
                                               ├─▶ DiagramService (arcviz/services) ─▶ ports ◀─ store  persist + version
                                               └─▶ mermaid.render (arcviz/mermaid.py)        render
                                                        │
                                       server.py (FastAPI, SSE) ─▶ static/ (browser, ES modules, Mermaid 11)
```

## Modules and allowed dependencies

Each module has one job. The table says what a module may import from inside the package; anything
not listed is a boundary violation and needs a spec change first.

| Module | Responsibility | May import |
|---|---|---|
| `arcviz/model.py` | `Diagram`, `Node`, `Edge`, diagram types, the kind catalogues per type, slugs, integrity rules | `sequence` |
| `arcviz/graph_types.py` | [Graph types](specs/uml-graph-types.md): the `GraphProfile` of every node/edge type with a fixed UML vocabulary (default kinds, typed fields and their bounds, edge/node/whole-diagram rules); activity is the first | `model`, `state_machine`, `class_diagram`, `use_case`, `component_diagram` |
| `arcviz/state_machine.py` | [State machine diagrams](specs/state-machine-diagrams.md): the hard rules (edges, parents), the transition text | `model` |
| `arcviz/state_rules.py` | Review findings of state machine diagrams, registered in `rules.RULES` | `model`, `rules`, `state_machine` |
| `arcviz/class_diagram.py` | [Class diagrams](specs/class-diagrams.md): the hard rules, member and multiplicity text | `model` |
| `arcviz/class_rules.py` | Review findings of class diagrams, registered in `rules.RULES` | `model`, `class_diagram`, `rules` |
| `arcviz/use_case.py` | [Use case diagrams](specs/use-case-diagrams.md): the hard rules, the text of an extend | `model` |
| `arcviz/use_case_rules.py` | Review findings of use case diagrams, registered in `rules.RULES` | `model`, `rules`, `use_case` |
| `arcviz/component_diagram.py` | [Component diagrams](specs/component-diagrams.md): the hard rules, the port names and text | `model`, `class_diagram` |
| `arcviz/component_rules.py` | Review findings of component diagrams, registered in `rules.RULES` | `model`, `component_diagram`, `rules` |
| `arcviz/plantuml_text.py` | Text helpers of the PlantUML writers: `plain`, the nesting bound | nothing from the package |
| `arcviz/plantuml_graph.py` | PlantUML writers of the graph types with a profile (state machine, class, use case, component) | `mermaid`, `model`, `plantuml_text`, `state_machine`, `class_diagram`, `use_case`, `component_diagram` |
| `arcviz/sequence.py` | Sequence-diagram content: the UML `Interaction` model, queries, integrity rules, summary | nothing from the package |
| `arcviz/sequence_ops.py` | Sequence-diagram operations, registered in `operations.OPERATIONS` (called only through `operations.apply`) | `model`, `sequence`, `operations` |
| `arcviz/sequence_svg.py` | Sequence diagram → SVG in UML notation | `model`, `sequence` |
| `arcviz/kinds.py` | Loading and validating kinds files (`ARCVIZ_KINDS_FILE`, the kind packs in `arcviz/kindpacks/` named by `ARCVIZ_KIND_PACKS`) into a `KindCatalog`; vendored icon packs | `model` |
| `arcviz/operations.py` | The single mutation path: validated operations with rollback on error; linking view elements to project elements ([shared project model](specs/shared-project-model.md)); the rules and typed fields of a graph type come from its profile | `model`, `graph_types`, `project_model`, `sequence` |
| `arcviz/project_model.py` | Shared project model ([shared project model](specs/shared-project-model.md)) and [model relations](specs/model-relations.md): `Element`, `Relation`, element and relation kinds with their rules, which view elements and connections may represent them, queries over the references of a diagram | `model`, `sequence` |
| `arcviz/decision_model.py` | [Decision support](specs/decision-support.md): `Stakeholder`, `Scenario` (six ATAM parts), `Decision` (ADR), their bounds, the prompt lines of drivers with core scenarios, stakeholders and accepted decisions, the Markdown ADR | `model`, `project_model` |
| `arcviz/requirement_model.py` | [Requirements](specs/requirements.md): `Requirement`, `Suggestion`, `RequirementBox` (own keyed document), operations with the agent policy, coverage, duplicate candidates | `model`, `operations` |
| `arcviz/services/requirements.py` | `RequirementService`: the requirements document, bulk import, accept with the suggestion revision, cascades and `sync` | `requirement_model`, `projects`, `ports`, `services/projects` |
| `arcviz/requirement_import.py` | [Workshop](specs/requirements-workshop.md) import: lines, Markdown and CSV parsed without a model (pure) | `requirement_model` |
| `arcviz/requirement_refine.py`, `arcviz/requirement_derive.py` | One chunk of the model steps: `submit_refinements` (suggestions) and `submit_derivation` (project proposal items), validated item by item, never raising | `requirement_model`, `llm`, `decision_model`, `proposals` |
| `arcviz/requirement_workshop.py` | `refine_chunk` and `derive_chunk` over a caller's services (routes and chat share them) | `requirement_refine`, `requirement_derive`, `services` |
| `arcviz/requirements_agent.py`, `arcviz/services/requirement_chat.py` | The requirements chat: the MCP tool set plus the chunk steps; its transcript is a keyed document (ADR-0193) | `agent_tools`, `requirement_workshop`, `llm`, `services` |
| `arcviz/projects.py` | `Project`: section tree and ordered diagram membership, integrity rules, project operations with validation and rollback ([projects](specs/projects.md)); the project's model elements and section links | `model`, `operations`, `project_model` |
| `arcviz/workspaces.py` | Users, personal workspaces and project roles ([personal workspaces](specs/personal-workspaces.md)): `Identity`, `UserRecord`, `Workspace` (`personal_workspace_id`), the effective role of a user on a project (owner → share → none) and the M20 role the migration keeps | `projects` |
| `arcviz/whatsnew.py` | Curated release highlights (`whatsnew.json`) shown as a banner ([spec](specs/whats-new.md)); no package imports | none |
| `arcviz/templates.py` | Starter diagrams per diagram type (`TEMPLATE_TYPES`) | `model`, `operations` |
| `arcviz/envconfig.py` | Reading start-up variables: `Env` (typed reads that record problems naming the variable, format and value), `ConfigError` ([operations](specs/operations.md), ADR-0106) | nothing from the package |
| `arcviz/llm/` | Provider-neutral tool loop (`base.py`), concrete providers, `create_provider()` factory, Entra ID tokens for Azure OpenAI (`entra.py`) | only `envconfig` and `telemetry` (its dataclasses are the contract) |
| `arcviz/ports.py` | Persistence ports: `DiagramRepositoryPort` protocol, value types (`VersionInfo`, `DiagramInfo`, `Change`, `Updated`), errors, id rule and new ids (`new_ids`, the bounded `id_attempts`), opaque `Revision` ([application services](specs/application-services.md)); `ProjectRepositoryPort`; `DocumentRepositoryPort` (keyed documents: workspaces, users); `presence_channel()` (the `live.PresenceChannel` replicas share presence through); authors are `Identity`s | `model`, `projects`, `workspaces`, `live` |
| `arcviz/services/` | Application services: every read-modify-write of the entry points with conditional writes (`DiagramService`, `ProjectService` with placement and membership repair, `ProjectExchangeService` for bundles, `ModelService` for the shared project model (with `adoption.py`: new structural views become project elements, [elements by default](specs/elements-by-default.md)), `WorkspaceService`, `UserService` (`users.py`) and the migration to personal workspaces); the access policy (`access.py`: `AccessPolicy`, `Guard`, `ensure_personal_workspace`), the hints it re-validates (`listing.py`: per-user project listings; `locations.py`: the diagram→project locator, [scale and robustness](specs/scale-and-robustness.md); `writers.py`: the writer heartbeat that settles the locator tokens of writers that died, [follow-ups](specs/follow-ups.md)) and `ServiceRoot.for_actor`, which binds them to one caller ([personal workspaces](specs/personal-workspaces.md)); `LiveService` (live streams and presence of a caller) and the events every write publishes ([collaboration](specs/collaboration.md)) | `model`, `ports`, `operations`, `projects`, `project_model`, `exchange`, `workspaces`, `live`, `envconfig` |
| `arcviz/store/` | Persistence: `DiagramRepository` base implementing the ports, shared versioning logic and the conditional read-modify-write (`update`), backends, `create_store()` factory (also attaches the Git mirror), migration CLI; project documents; keyed documents (workspaces, users); Cosmos DB request units (#201) | `model`, `ports`, `projects`, `gitmirror`, `workspaces`, `telemetry` |
| `arcviz/gitmirror.py` | Optional Git mirror of every version (commit per snapshot, optional push) | `model`, `render` |
| `arcviz/proposal.py` | Propose mode: diff of two diagrams, confirming (replay through `operations`) and discarding a proposal | `model`, `operations`, `sequence`, `ports`, `services` |
| `arcviz/review.py` | LLM architecture review: one structured model call (`complete_tool`), validated into findings next to the rule checks | `model`, `llm`, `rules` |
| `arcviz/agent.py` | One chat turn: system prompt (composed by `prompts`), tool execution, live events, persistence through the services, transcript; deployment tools; the link take-back both agents share (`take_back_invalid_link`) | `model`, `operations`, `llm`, `ports`, `services`, `render`, `proposal`, `agent_types`, `agent_model`, `prompts`, `projects`, `project_model`, `telemetry` |
| `arcviz/agent_model.py` | Element and relation tools of both chats ([shared project model](specs/shared-project-model.md), [model relations](specs/model-relations.md)): list, create, update, link, unlink | `model`, `operations`, `llm`, `ports`, `projects`, `project_model`, `services` |
| `arcviz/project_agent.py` | One project chat turn ([project chat](specs/project-chat.md)): project prompt and tools, the active diagram per turn, per-diagram write-through and versions, the project transcript | `model`, `operations`, `projects`, `sequence`, `llm`, `ports`, `services`, `render`, `proposal`, `agent`, `agent_model`, `prompts`, `telemetry` |
| `arcviz/project_review.py` | Project review ([project review](specs/project-review.md)): project rules (also the consistency rules of the [shared model](specs/shared-project-model.md)), aggregated diagram findings, one LLM review of the whole project | `model`, `projects`, `project_model`, `decision_model`, `sequence`, `llm`, `rules`, `review` |
| `arcviz/scenario_analysis.py` | What-if analysis of a quality scenario ([decision support](specs/decision-support.md)): one LLM call over the project digest and the architecture context, validated, never stored | `decision_model`, `llm`, `project_review` |
| `arcviz/mcp_server.py` | MCP endpoint for agents ([agent access](specs/agent-access.md)): registers the tools on the SDK's server, POST only, stateless | `agent_tools`, `services`, `telemetry` |
| `arcviz/agent_tools.py` | What an agent may ask and do: bounded read tools, code links, proposals; never applies a proposal | `services`, `project_review`, `agent`, `project_model` |
| `arcviz/agentauth.py`, `arcviz/oidc.py` | Credentials of agents on the MCP path ([agent authentication](specs/agent-authentication.md)): `AgentGate`, personal access tokens, OIDC access tokens | `auth`, `limits`, `services` |
| `arcviz/tokens.py`, `arcviz/proposals.py` | Domain types of personal access tokens and stored proposals (shape, bounds, retention, the proposal diff) | `workspaces`, `model` |
| `arcviz/agent_types.py` | Tools of the diagram types other than deployment | `model`, `operations`, `sequence`, `llm` |
| `arcviz/prompts/` | System prompt texts (shared blocks, one expert part per diagram type) and their composition | nothing (pure text) |
| `arcviz/render.py` | The renderer of each diagram type behind one interface; the render payload | `model`, `mermaid`, `sequence_svg` |
| `arcviz/mermaid.py` | Diagram → Mermaid source | `model` |
| `arcviz/rules.py` | Deterministic architecture checks registered per diagram type, and the `Finding` schema shared with the LLM review | `model`, `mermaid` |
| `arcviz/diff.py` | Difference of two diagram states and a merged, marked view of it for rendering | `model` |
| `arcviz/exchange.py` | Whole-diagram import/export: versioned JSON document (validated parse), PlantUML export; project bundles ([project exchange](specs/project-exchange.md)) with the shared model | `model`, `mermaid`, `operations` (`sync_element` for shortened imported elements, #212), `projects`, `project_model` |
| `arcviz/limits.py` | Request size limits and in-process rate limiting (configuration, token buckets, ASGI middleware); the caps on live streams | `envconfig` |
| `arcviz/history.py` | The version history as metadata ([hardening](specs/hardening.md), #114): `undo_target_of`/`redo_target_of`/`history_of` over one listing, `HistoryConfig` (`ARCVIZ_VERSIONS_*`: response page, opt-in retention), `prunable` (ADR-0129, ADR-0130) | `ports`, `envconfig` |
| `arcviz/live.py` | Live updates and presence ([collaboration](specs/collaboration.md)): the in-process event bus (`LiveHub`, `EventSink`), the presence registry and its relay across replicas (`PresenceChannel`, `sync_presence`), the event loop of one stream (`run_stream`), `LiveConfig` | `envconfig` |
| `arcviz/observability.py` | Logs and telemetry ([operations](specs/operations.md), ADR-0111..0114): `LogConfig`, the JSON formatter and context filter, `RequestContextMiddleware` (request id, trace id, request records), path redaction, exception summaries, keyed pseudonyms, the optional OpenTelemetry / Azure Monitor wiring (span attribute allow-list, event scrubbing, metric reader, Entra ID credential for the exporter) | `envconfig`, `telemetry` |
| `arcviz/telemetry.py` | The app's own spans and metrics ([follow-ups](specs/follow-ups.md), ADR-0134): `span`/`start` (LLM calls, tool executions, store writes), `record_request`, `record_llm`, `record_request_units`, `observe_live_streams`, the attribute allow-lists; a no-op until `observability.start_telemetry` enables it | none |
| `arcviz/auth.py` | Optional authentication: configuration (also the admin identity), the ASGI middleware (bearer token, session cookie, identity headers of an authenticating proxy with presets) that puts the caller's `Identity` into the request, login page and session helpers | `limits` (ASGI helpers), `workspaces` |
| `arcviz/cli.py` | `arcviz-migrate`: storage copy between backends plus the project repair, the migration to personal workspaces and the recovery of stranded projects (system context); a composition root like `server.py` | `ports`, `services`, `store`, `workspaces` |
| `arcviz/server.py` | HTTP/SSE API, wiring of store, services, provider, agent, kind catalogue, limits and authentication from the environment; routes call only the services bound to the caller (`Svc` dependency) | everything above (from `store` only `create_store`) |
| `arcviz/static/` | Browser UI; talks to `server.py` only through `/api/*`. Native ES modules in layers (pure → base → views → `diagram.js` → features → `chat.js` → `app.js`), see [Frontend split into ES modules](specs/frontend-modules.md) | n/a (inside: lower layers only, checked by `tests/js/modules.test.mjs`) |

Rules that follow from the table:

- **Configuration** is read from the environment only in factories (`llm/factory.py`,
  `store/factory.py`) and in `server.py`. Modules receive their dependencies as constructor arguments.
- **Business logic never sees the store.** Entry points call `services/`, which depend on the
  ports in `ports.py`; only `store/` knows backends, file layouts, tables and partition keys, and
  `server.py` only hands the repository from `create_store()` to the services
  (`tests/test_layering.py`, ADR-0050).
- **Every entry point acts for a caller.** Routes and both agents get the services bound to the caller
  (`ServiceRoot.for_actor`); the services ask one access policy before they read or write. Only the composition
  roots use the system context, for the migration and the CLI (`tests/test_layering.py`, ADR-0084).
- **Persistence knows nothing about the LLM**, and the LLM layer knows nothing about diagrams. The
  agents (`agent.py`, `project_agent.py`) and the reviews (`review.py`, `project_review.py`, read-only,
  ADR-0024) are the only modules that see both.
- **The UI has no business logic.** Everything it shows comes from `/api/*` responses or SSE events.

## Specifications

| Spec | Status | Issue |
|---|---|---|
| [Persistence: repository pattern and backends](specs/persistence.md) | Implemented | #31 |
| [Chat compaction](specs/chat-compaction.md) | Implemented | #36 |
| [Atomic batch operation](specs/batch-operations.md) | Implemented | #69 |
| [Redo after undo](specs/redo.md) | Implemented | #70 |
| [Edge direction and properties](specs/edge-attributes.md) | Implemented | #71 |
| [Configurable element kinds](specs/configurable-kinds.md) | Implemented | #72 |
| [More starter templates](specs/starter-templates.md) | Implemented | #73 |
| [Health and readiness probes](specs/health-probes.md) | Implemented | #81 |
| [LLM call resilience: timeouts, retries, token budget](specs/llm-resilience.md) | Implemented | #77 |
| [Streaming reply text deltas](specs/reply-streaming.md) | Implemented | #74 |
| [Markdown in assistant replies](specs/chat-markdown.md) | Implemented | #223 |
| [Architecture drivers](specs/architecture-drivers.md) | Implemented | #224 |
| [Project elements by default](specs/elements-by-default.md) | Implemented | #225 |
| [Decision support: stakeholders, scenarios, analysis, decisions](specs/decision-support.md) | Implemented | #226, #231, #232, #233 |
| [Deployment traits: managed services and built-in redundancy](specs/deployment-traits.md) | Implemented | #236 |
| [Agent access: MCP endpoint, code links, stored proposals](specs/agent-access.md) | Implemented | #237 |
| [Agent authentication: PATs and OIDC bearer tokens](specs/agent-authentication.md) | Implemented | #238 |
| [Onboarding: starters and a guided tour](specs/onboarding.md) | Implemented | n/a |
| [Release highlights in the app](specs/whats-new.md) | Implemented | n/a |
| [Menu bar and personal settings](specs/personal-settings.md) | Implemented | UI |
| [Architecture objects over MCP: stakeholders, drivers, scenarios, decisions](specs/architecture-objects-over-mcp.md) | Implemented | n/a |
| [Requirements: raw input, refinement, traceability](specs/requirements.md) | Implemented | n/a |
| [Requirements workshop: from requirements to drivers and scenarios](specs/requirements-workshop.md) | Implemented | n/a |
| [Propose-then-confirm mode with diff preview](specs/propose-confirm.md) | Implemented | #75 |
| [Offline prompt/tool eval set](specs/agent-evals.md) | Implemented | #76 |
| [Layout direction](specs/layout-direction.md) | Implemented | #78 |
| [Diagram import and export](specs/diagram-exchange.md) | Implemented | #79 |
| [Visual comparison of two versions](specs/version-comparison.md) | Implemented | #80 |
| [Frontend split into ES modules](specs/frontend-modules.md) | Implemented | #88 |
| [Property-based tests and broader server coverage](specs/property-tests.md) | Implemented | #89 |
| [Rule-based architecture checks](specs/rule-checks.md) | Implemented | #95 |
| [LLM architecture review with findings linked to elements](specs/llm-review.md) | Implemented | #96 |
| [Applying a review suggestion via propose mode](specs/review-suggestions.md) | Implemented | #97 |
| [Renderer case study: Mermaid vs PlantUML vs own SVG with ELK.js](specs/renderer-case-study.md) | Case study | #105 |
| [Diagram types](specs/diagram-types.md) | Implemented | #98 |
| [Activity diagrams](specs/activity-diagrams.md) | Implemented | #99 |
| [Sequence diagrams](specs/sequence-diagrams.md) | Implemented | #101 |
| [More UML diagram types: shared design](specs/uml-graph-types.md) | Accepted | #243 |
| [State machine diagrams](specs/state-machine-diagrams.md) | Implemented | #244 |
| [Class diagrams](specs/class-diagrams.md) | Implemented | #245 |
| [Use case diagrams](specs/use-case-diagrams.md) | Implemented | #246 |
| [Component diagrams](specs/component-diagrams.md) | Implemented | #247 |
| [Cloud icons and kind packs](specs/cloud-icons.md) | Implemented | #100 |
| [Architecture documentation (arc42 with C4 views)](specs/architecture-documentation.md) | Implemented | #110 |
| [Documentation served by the app](specs/docs-in-app.md) | Implemented | #111 |
| [Operation arguments: validation before mutation](specs/operation-arguments.md) | Implemented | #66 |
| [Concurrent mutations: conditional writes on a latest revision](specs/concurrent-mutations.md) | Implemented | #65 |
| [Request size and rate limits](specs/request-limits.md) | Implemented | #85 |
| [Authentication](specs/authentication.md) | Implemented | #82 |
| [UI skins](specs/ui-skins.md) | Implemented | #127 |
| [App shell: icon rail, grouped sidebar, Architektur page](specs/app-shell.md) | Implemented | n/a |
| [Architektur workspace: two-pane lists, components, motion](specs/architecture-workspace.md) | Implemented | n/a |
| [Command palette and slim top bar](specs/command-palette.md) | Implemented | n/a |
| [Internationalization](specs/internationalization.md) | Accepted | n/a |
| [Application services that hide persistence](specs/application-services.md) | Implemented | #130 |
| [Projects: sections, diagram membership, API and migration](specs/projects.md) | Implemented | #131, #132 |
| [Project navigator with sections](specs/project-navigator.md) | Implemented | #133 |
| [Export and import of whole projects](specs/project-exchange.md) | Implemented | #134 |
| [Project chat with cross-diagram tools](specs/project-chat.md) | Implemented | #135 |
| [Project review](specs/project-review.md) | Implemented | #136 |
| [Domain-expert system prompts per diagram type](specs/assistant-prompts.md) | Implemented | #125 |
| [Topic scope of the assistant](specs/topic-scope.md) | Implemented | #124 |
| [Shared project model: case study](specs/shared-project-model-case-study.md) | Case study | #137 |
| [Shared project model: project elements and diagrams as views](specs/shared-project-model.md) | Implemented | #144..#151 |
| [Model relations and cross-diagram consistency](specs/model-relations.md) | Implemented | #203..#209 |
| [Users, workspaces and project sharing](specs/workspaces-and-sharing.md) | Superseded (M21) | #158..#164 |
| [Personal workspaces and direct sharing](specs/personal-workspaces.md) | Implemented | #177, #178, #179 |
| [Collaboration: notes, live updates with optimistic concurrency, presence](specs/collaboration.md) | Implemented | #86, #87, #171, #173, #176 |
| [Operations: configuration errors, optional SDKs, reproducible builds, structured logs, keyless Azure OpenAI](specs/operations.md) | Implemented | #83, #84, #116, #118, #156 |

## Decisions

See the [decision records](adr/index.md).

## Operational documentation

- [Technical specification](../spec/index.md) (as implemented), [configuration](../reference/configuration.md), [HTTP API](../reference/api.md)
- [Deploying to Azure Container Apps](../deploy-azure.md)
