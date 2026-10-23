# 3. Context and scope

## Business context (C4 level 1)

```mermaid
--8<-- "docs/architecture/diagrams/c4-context.mmd"
```

Source: `docs/architecture/diagrams/c4-context.arcviz.json`.

| Partner | Exchanged | Notes |
|---|---|---|
| Architect or developer | Instructions (text or voice), direct edits, imports and exports; gets live diagrams, replies, versions, review findings | German UI in a browser. Voice uses the browser's Web Speech API, which may send audio to the browser vendor's service ([Voice](../../guide/voice.md)). |
| Operator | Environment variables, a volume or Cosmos DB, the image | [Configuration](../../reference/configuration.md), [Deploy to Azure](../../deploy-azure.md). |
| LLM API | System prompt with the diagram summary, chat history, tool schemas; gets tool calls, text and token usage | Anthropic (default) or any OpenAI-compatible server (OpenAI, Ollama, vLLM, LM Studio). **Diagram content and instructions leave the system here**, unless a local model is used. |
| Azure Cosmos DB | Diagrams, versions, chat entries | Optional. The only store that supports several replicas ([ADR-0004](../adr/0004-cosmos-db-nosql-first-database-backend.md)). |
| Git remote | Commits of every version | Optional (`ARCVIZ_GIT_PUSH`). The mirror is a second record and is never read back. |
| Identity-aware proxy | Authenticated requests, the user in a header | Optional, **not part of Arcviz.** With `ARCVIZ_AUTH=proxy` the app trusts the configured header; the deployment guide puts Container Apps Easy Auth in front. Without a proxy, `ARCVIZ_AUTH=token` protects the app with a shared token (#82). |

## Technical context

| Channel | Protocol | Content |
|---|---|---|
| Browser ↔ app | HTTP/1.1, JSON; chat as server-sent events (`text/event-stream`) | [HTTP API](../../reference/api.md). Static UI under `/static`, documentation under `/docs`. |
| App → LLM API | HTTPS via the official `anthropic` and `openai` SDKs (timeouts and retries from the SDK, [ADR-0012](../adr/0012-sdk-retries-and-timeouts.md)) | Tool-calling requests, optionally streamed |
| App → store | Local file I/O (`file`), SQLite (`sqlite`), HTTPS (`cosmos`) | [Persistence](../../spec/persistence.md) |
| App → Git | `git` CLI as a subprocess; `git push` to the configured remote | [Git mirror](../../spec/persistence.md#git-mirror) |
| Orchestrator → app | `GET /healthz`, `GET /readyz` | [Health and readiness](../specs/health-probes.md) |

## Scope

In scope: one diagram editor with an agent, versions and review, served by one process.

Out of scope:
- authentication beyond a shared token (delegated to the deployment's proxy, which passes each user's identity;
  users with personal workspaces, isolated from each other in one store and sharing per project, are in scope since
  M21); separate stores or encryption per organisation;
- merging simultaneous edits of the same field (M10 brings live updates, presence and an explicit choice on a
  conflict instead: [collaboration](../specs/collaboration.md)); presence across several replicas;
- rendering on the server (the browser renders the Mermaid source);
- hosting the LLM.
