# 5. Building block view

## Level 1: containers (C4 level 2)

```mermaid
--8<-- "docs/architecture/diagrams/c4-containers.mmd"
```

Source: `docs/architecture/diagrams/c4-containers.arcviz.json`.

| Container | Responsibility | Interface | Spec |
|---|---|---|---|
| Web UI (`arcviz/static/`) | Chat, canvas (Mermaid rendering, pan and zoom, drill-down), inspector, versions, comparison, review panel, voice. **No business logic**: everything it shows comes from `/api` responses and SSE events. | `/api/*` JSON and SSE | [Frontend](../../spec/frontend.md) |
| Application server (`arcviz/`) | HTTP and SSE API, agent loop, operations, versioning, rendering to Mermaid source, rule checks, LLM review, import and export. Serves the UI and the docs. | [HTTP API](../../reference/api.md) | [HTTP server](../../spec/http-server.md) |
| Documentation site (`arcviz/docs_site/`) | User guide, reference, spec and this documentation for the running version, offline | `/docs/` | [Documentation served by the app](../specs/docs-in-app.md) |
| Diagram store (`ARCVIZ_DATA_DIR`) | Per diagram: `latest`, immutable versions, chat transcript. `file`: JSON files; `sqlite`: one database file. | `DiagramRepository` | [Persistence](../../spec/persistence.md) |
| Git mirror (optional) | One commit per version with the instruction as subject; never read back | `GitMirror.record/remove` | [Git mirror](../../spec/persistence.md#git-mirror) |

The browser and the server are the only runtime containers. The store, the mirror and the docs site
are data that the server process owns, so there is exactly **one process per data directory**
(section 8, *Concurrency*).

## Level 2: application server components (C4 level 3)

```mermaid
--8<-- "docs/architecture/diagrams/c4-components-server.mmd"
```

Source: `docs/architecture/diagrams/c4-components-server.arcviz.json`. Dotted arrows are Python
imports. The view draws exactly the imports between these components (checked by
`tests/test_architecture_docs.py`), except imports **of `model.py`**: every module but `llm/` imports
it, so those edges are left out. `server.py` is the composition root and imports everything it serves.

The allowed dependencies are the [module table](../index.md#modules-and-allowed-dependencies). The
code follows it today. Notable properties of this structure:

- **Business logic never sees the store** (ADR-0050). `server.py` routes, `agent.py`, `project_agent.py` and `proposal.py`
  call the application services (`services/`), which depend only on the ports in `ports.py`; the
  store implements the ports. `server.py` imports only `create_store` from the store and hands the
  repository to the services. `tests/test_layering.py` enforces both rules.
- **`llm/` knows nothing about diagrams**, and the store knows nothing about the LLM. Only the agents
  (`agent.py`, `project_agent.py`) and the reviews (`review.py`, `project_review.py`) see both.
- **`operations.py` is the only writer** of a `Diagram`. `agent.py`, `proposal.py`, `templates.py`
  and `server.py` (`/ops`) all call it.
- **Persistence depends on rendering** through the Git mirror (`store/ → gitmirror.py → mermaid.py`),
  because the mirror commits the Mermaid source next to the JSON. This is a small coupling: a renderer
  change touches the mirror's output. M13's per-type renderers (#98) will have to be reachable from
  there too.
- **`rules.py` imports `mermaid.py`** only for `edge_direction`, a model-level concept that lives in
  the renderer.

| Component | Responsibility | Spec | Key decisions |
|---|---|---|---|
| `server.py` | Routes, SSE, wiring from the environment, static files and docs | [HTTP server](../../spec/http-server.md) | [ADR-0043](../adr/0043-serving-the-documentation-under-docs.md) |
| `auth.py` | Optional authentication (ASGI middleware, login page, session cookie) and the caller's identity (proxy headers with presets, or the admin identity) | [Authentication](../specs/authentication.md) | [ADR-0047](../adr/0047-authentication-off-by-default-shared-token-or-trusted-proxy.md), [ADR-0080](../adr/0080-identities-from-the-authenticating-proxy.md) |
| `workspaces.py` | Users, personal workspaces, the effective role of a user on a project (owner → share → none) | [Personal workspaces](../specs/personal-workspaces.md) | [0100](../adr/0100-one-personal-workspace-per-user.md)–[0103](../adr/0103-no-access-outside-personal-workspaces.md) |
| `limits.py` | Request size limits and rate limiting (ASGI middleware) | [Request limits](../specs/request-limits.md) | [ADR-0048](../adr/0048-request-limits-and-in-process-rate-limiting.md) |
| `agent.py` | One chat turn: system prompt with the diagram summary, tools, live persistence, finalization (version, transcript), propose mode, compaction | [Agent](../../spec/agent.md) | [0005](../adr/0005-chat-compaction-by-cumulative-summary.md), [0013](../adr/0013-per-turn-token-budget.md), [0015](../adr/0015-client-held-proposals.md) |
| `project_agent.py` | One project chat turn (M17): project structure in the prompt, project tools, the active diagram per turn with its type's tools, per-diagram write-through and versions, the project transcript | [Agent: project chat](../../spec/agent.md#project-chat) | [0057](../adr/0057-project-transcript-next-to-the-project-document.md)–[0061](../adr/0061-project-chat-as-a-chat-scope.md) |
| `prompts/` | System prompt texts: shared blocks (persona, scope, working rules, hints, reply style) and one expert part per diagram type | [Agent: system prompt](../../spec/agent.md#system-prompt) | [0065](../adr/0065-system-prompts-from-shared-blocks-and-expert-parts.md), [0066](../adr/0066-topic-scope-in-the-prompt-without-classifier-or-switch.md) |
| `llm/` | Provider-neutral tool loop (`run_turn`), Anthropic and OpenAI-compatible providers, factory | [LLM layer](../../spec/llm-layer.md) | [0012](../adr/0012-sdk-retries-and-timeouts.md), [0014](../adr/0014-reply-text-deltas.md) |
| `operations.py` | Validated mutations with rollback; `apply_batch` | [Operations](../../spec/operations.md) | [0007](../adr/0007-atomic-batch-as-an-ordinary-operation.md), [0009](../adr/0009-optional-edge-direction.md) |
| `model.py`, `kinds.py` | Diagram, nodes, edges, kind catalogue (built-in plus kinds file), integrity rules, summary | [Domain model](../../spec/domain-model.md) | [0010](../adr/0010-kinds-extended-from-a-json-file.md), [0017](../adr/0017-layout-direction-on-the-diagram-elk-not-vendored.md) |
| `projects.py` | Project domain type: section tree, ordered membership, the shared model's elements and section links, project operations with rollback | [Projects](../../spec/projects.md) | [0051](../adr/0051-projects-as-own-documents-diagrams-unchanged.md), [0054](../adr/0054-section-removal-keeps-contents.md), [0074](../adr/0074-sections-reference-subsystem-elements.md) |
| `project_model.py` | Shared project model (M18): `Element`, element kinds, which view elements may stand for which kinds, queries over a diagram's references, the prompt summary | [Projects: shared model](../../spec/projects.md#shared-model) | [0070](../adr/0070-shared-model-elements-in-the-project-document.md)–[0073](../adr/0073-element-kinds-and-view-compatibility.md), [0078](../adr/0078-no-repartitioning-for-the-model.md) |
| `agent_model.py` | Element tools of the diagram chat and the project chat (M18) | [Agent: shared project model](../../spec/agent.md#shared-project-model) | [0076](../adr/0076-shared-elements-in-the-chats.md) |
| `services/`, `ports.py` | Application services (every read-modify-write with conditional writes; projects with placement and membership repair; the shared model with rename propagation and sync, `ModelService`; personal workspaces, the user directory, sharing and the migration into personal workspaces) over the persistence ports (protocol, value types, errors), bound per request to the caller and checked by one access policy (`services/access.py`, M20) | [Persistence: application services](../../spec/persistence.md#application-services), [personal workspaces](../specs/personal-workspaces.md) | [0050](../adr/0050-application-services-over-repository-ports.md), [0044](../adr/0044-conditional-writes-on-a-latest-revision.md), [0084](../adr/0084-access-policy-in-the-service-layer.md) |
| `store/`, `gitmirror.py` | `DiagramRepository`: versioning semantics once, backends `file`, `sqlite`, `cosmos`; migration CLI | [Persistence](../../spec/persistence.md) | [0002](../adr/0002-repository-pattern-for-persistence.md), [0003](../adr/0003-snapshots-not-diffs.md), [0004](../adr/0004-cosmos-db-nosql-first-database-backend.md), [0008](../adr/0008-redo-from-undo-redo-version-sources.md) |
| `mermaid.py`, `diff.py` | Diagram to Mermaid flowchart (escaping, focus, marks); version comparison | [Rendering](../../spec/rendering.md) | [0019](../adr/0019-compare-versions-as-one-merged-render.md), [0038](../adr/0038-renderer-per-diagram-type.md) |
| `rules.py`, `review.py` | Rule checks per diagram type, one `Finding` schema, LLM review as one validated tool call | [Architecture review](../../spec/review.md) | [0022](../adr/0022-rule-checks-registered-per-diagram-type.md)–[0026](../adr/0026-suggestion-as-propose-turn-composed-in-the-browser.md) |
| `project_review.py` | Project review (M17): project rules (M18: consistency rules of the shared model, [ADR-0075](../adr/0075-cross-diagram-consistency-rules.md)), the diagrams' rule findings aggregated with their diagram id, one LLM review of the whole project | [Architecture review: project review](../../spec/review.md#project-review) | [0062](../adr/0062-project-findings-reference-diagrams.md)–[0064](../adr/0064-project-llm-review-one-call-budgeted-digest.md) |
| `exchange.py` | Versioned JSON document (export, validated import), PlantUML export, project bundles ([project exchange](../specs/project-exchange.md), [ADR-0056](../adr/0056-project-bundle-embeds-diagram-documents.md)) | [Import and export](../../spec/exchange.md) | [0018](../adr/0018-exchange-formats-json-and-plantuml-export.md) |
| `proposal.py`, `templates.py` | Confirming or discarding a proposal; starter diagrams | [Agent](../../spec/agent.md), [Domain model](../../spec/domain-model.md#templates) | [0015](../adr/0015-client-held-proposals.md), [0011](../adr/0011-starter-templates-as-code.md) |

## Level 2: web UI components (C4 level 3)

```mermaid
--8<-- "docs/architecture/diagrams/c4-components-web-ui.mmd"
```

Source: `docs/architecture/diagrams/c4-components-web-ui.arcviz.json`. A module imports only modules
of **lower** layers (not only the next one). Features react to a newly shown diagram through hooks of
the hub `diagram.js`, never by being imported from below. The project navigator `navigator.js` (M16,
[ADR-0055](../adr/0055-navigator-column-and-layer.md)) sits between the hub and the features: it owns
the list of projects, sections and diagrams, and features refresh it through its functions. `tests/js/modules.test.mjs` enforces the
layering, and the pure modules are unit-tested with `node --test`. Design:
[Frontend split into ES modules](../specs/frontend-modules.md),
[ADR-0020](../adr/0020-frontend-native-es-modules.md).

`state.js` holds the UI state as one shared, mutable module object. That is simple, but every feature
can write to it, so the layering rule is the only thing that keeps the data flow understandable.
