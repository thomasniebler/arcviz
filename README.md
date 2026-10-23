# Arcviz agent

[![CI](https://github.com/thomasniebler/arcviz/actions/workflows/ci.yml/badge.svg)](https://github.com/thomasniebler/arcviz/actions/workflows/ci.yml)

Chat interface that edits UML 2.5.1 diagrams (deployment, activity, sequence, state machine, class, use case and component diagrams) through an
LLM with typed tools and renders the resulting diagram live in the same view. Example: *"Add a vnet to the deployment and move
services a, b and c into it"* → the model creates the vnet, moves the three existing services, every
edge stays intact, and the diagram on the right updates after each applied operation.

![Screenshot: chat on the left, live diagram with the new vnet on the right](docs/assets/screenshot.png)

## Documentation

User documentation (quick setup, user guide, configuration reference, technical specification) lives
under [`docs/`](docs/) in this repository and is built into the app: a running Arcviz serves the documentation of
its own version under `/docs/` (menu **Hilfe**), offline. The Docker image, the release wheel and the sdist always
contain it; `make run` builds it on the first start of a development checkout (or `make docs-bundle`). Build the
plain site with `make docs`, preview it with `make docs-serve`. The
[architecture documentation](docs/architecture/arc42/index.md) (arc42 with C4 views, drawn with Arcviz itself)
assesses the design, including known risks.

## How it works

```
chat message ─▶ LLM (tool calling) ─▶ validated operations ─▶ Diagram model ─▶ Mermaid ─▶ browser
                                            │
                                            └─▶ data/diagrams/<id>/latest.json  (always current)
                                                data/diagrams/<id>/versions/NNNN.json (snapshots)
                                                data/diagrams/<id>/chat.json  (transcript)
```

- **Diagram types** (`docs/architecture/specs/diagram-types.md`): every diagram is a `deployment`,
  `activity`, `sequence`, `state_machine`, `class`, `use_case` or `component` diagram with its own UML vocabulary, operations, tools and renderer.
- **Model** (`arcviz/model.py`): nodes with a *kind* (deployment, vnet, subnet, service, database, queue,
  gateway, …; more via a JSON kinds file, `ARCVIZ_KINDS_FILE`, or the AWS/Azure/GCP kind packs with
  icons, `ARCVIZ_KIND_PACKS`), optional *parent* (containers nest), free-form string properties, and typed edges with an optional direction and their own properties
  (protocol, port, …).
- **Operations** (`arcviz/operations.py`): `add_node`, `update_node`, `move_node`, `remove_node`,
  `add_edge`, `update_edge`, `remove_edge`, `rename_diagram`, plus the all-or-nothing `apply_batch`;
  sequence diagrams have their own (`add_lifeline`, `add_message`, `add_combined_fragment`, …). Every operation validates (containment
  rules, cycles, unknown ids) and leaves the model untouched on error.
- **LLM layer** (`arcviz/llm/`): provider-neutral tool loop; providers for **Anthropic Claude**
  (official SDK, default model `claude-opus-5-5`) and any **OpenAI-compatible** endpoint
  (OpenAI, Ollama, vLLM, LM Studio …).
- **Agent** (`arcviz/agent.py`): puts the current model state into the system prompt, lets the model
  call tools, streams every applied operation to the browser (server-sent events), and persists. Long
  transcripts are compacted after a turn: older entries are replaced by a cumulative LLM-written summary
  that the model sees in place of them (`docs/architecture/specs/chat-compaction.md`).
- **Persistence** (`arcviz/store/`): a `DiagramRepository` with interchangeable backends, `file` (default,
  human-readable JSON under `ARCVIZ_DATA_DIR`), `sqlite` and `cosmos` (Azure Cosmos DB). The current state
  is rewritten after *every* change. Each chat turn that changed something creates an immutable version
  automatically; versions can also be created manually, previewed, restored, undone and redone. Restores are
  recorded as new versions so the history stays linear and survives across sessions.
  `arcviz-migrate --from file:data --to sqlite:data` copies everything between backends (and doubles as
  export). Design: `docs/architecture/specs/persistence.md`.
- **Rendering**: deployment, activity, state machine, class, use case and component diagrams as a Mermaid flowchart generated server-side
  (containers → subgraphs, kinds → shapes, icons and colours, changed elements highlighted), sequence
  diagrams as SVG by an own renderer in UML notation; Mermaid is rendered client-side with a vendored, pinned Mermaid 11 build (`arcviz/static/vendor`, updated with
  `scripts/vendor_mermaid.py`), so the UI needs no CDN.
- **Coding agents** (`arcviz/mcp_server.py`, `ARCVIZ_MCP=1`): an MCP endpoint that answers which components exist, what they are
  responsible for, how they connect and which code owns them (code links on project elements), and takes change *proposals* that
  people apply or discard. Agents authenticate with a personal access token or, behind an identity provider, an OIDC access token
  (`docs/guide/agents.md`, `docs/spec/agents.md`).
- **Working together** (`docs/architecture/specs/collaboration.md`): nodes and lifelines carry notes the assistant
  reads; every open browser follows changes live (server-sent events), sees who else is there and what they have
  selected, and an edit of a field someone changed meanwhile is never saved over it silently (`ETag`/`If-Match`,
  412, the user chooses).

## Run

```bash
uv venv && uv pip install -e ".[dev]"      # or: pip install -e ".[dev]"
# Only running it: pip install ".[anthropic]" (or ".[openai]"); the LLM SDKs are optional extras.

# Claude (default)
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/arcviz                            # http://127.0.0.1:8000

# OpenAI-compatible instead, e.g. Ollama
ARCVIZ_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1 ARCVIZ_MODEL=qwen2.5:14b .venv/bin/arcviz
```

On first start a demo diagram is created (a deployment with gateway, three services, database, queue and
an external identity provider) so the vnet example above can be tried immediately.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_PROVIDER` | `anthropic` | `anthropic` or `openai` (any OpenAI-compatible server) |
| `ARCVIZ_MODEL` | `claude-opus-5-5` / `gpt-4o` | model id passed to the provider |
| `ARCVIZ_EFFORT` | unset | Anthropic `output_config.effort` (`low` … `max`) |
| `ARCVIZ_ANTHROPIC_FALLBACKS` | `1` | server-side fallback to another Claude model if a request is declined for policy reasons |
| `ARCVIZ_STORE` | `file` | persistence backend: `file`, `sqlite` or `cosmos` (needs `pip install "arcviz-agent[cosmos]"`) |
| `ARCVIZ_DATA_DIR` | `data` | `file`: root directory; `sqlite`: directory of `arcviz.sqlite3` |
| `ARCVIZ_STORE_URL` | | `cosmos`: account endpoint, e.g. `https://<account>.documents.azure.com:443/` |
| `ARCVIZ_STORE_KEY` | | `cosmos`: account key; unset = managed identity (`DefaultAzureCredential`) |
| `ARCVIZ_STORE_DATABASE` / `ARCVIZ_STORE_CONTAINER` | `arcviz` / `diagrams` | `cosmos`: database and container (created if missing) |
| `ARCVIZ_MAX_ROUNDS` | `12` | max tool-calling rounds per chat turn |
| `ARCVIZ_CHAT_COMPACT_AFTER` | `60` | fold the older transcript into an LLM-written summary once it holds more than this many entries (`0` disables) |
| `ARCVIZ_CHAT_KEEP` | `30` | entries kept verbatim after a compaction |
| `ARCVIZ_HOST` / `ARCVIZ_PORT` | `127.0.0.1` / `8000` | bind address |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` | | provider credentials / endpoint |

## API

| Method & path | Purpose |
|---|---|
| `GET /api/diagrams` · `POST /api/diagrams` · `GET/DELETE /api/diagrams/{id}` | list / create (`type`: `deployment`, `activity`, `sequence`, `state_machine`, `class`, `use_case`, `component`; `template`: `empty`, `demo`, `hub-spoke`, `aks-database`, `event-driven`, `order-process`, `order-lifecycle`, `webshop-domain`, `webshop-use-cases`, `webshop-components`, `checkout`) / read / delete |
| `POST /api/diagrams/{id}/chat` | chat turn; responds with an SSE stream of `text`, `tool_call`, `tool_result` (with intermediate `diagram` and the rendering: `renderer` + `mermaid` or `svg`), `done`, `final` events |
| `POST /api/diagrams/{id}/chat/compact` · `DELETE …/chat` | fold the older transcript into a summary entry now / clear the transcript |
| `POST /api/diagrams/{id}/ops` | apply one operation directly, without the LLM |
| `GET /api/diagrams/{id}/versions` · `POST …/versions` | list versions / manual snapshot |
| `GET …/versions/{n}` · `POST …/versions/{n}/restore` · `POST …/undo` · `POST …/redo` | preview, restore, undo, redo |
| `GET /api/diagrams/{id}/render` · `…/mermaid` | current rendering (Mermaid source or SVG) / Mermaid source of Mermaid-rendered types |
| `GET /api/diagrams/{id}/live` · `GET /api/projects/{id}/live` · `POST /api/diagrams/{id}/presence` | live updates and presence as server-sent events / report the selected element |

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and the rule that every change updates the
technical specification under `docs/spec/`; `CLAUDE.md` states the same for the coding agent.

```bash
make install      # venv + dev deps + Playwright Chromium
make lint         # ruff check + format check
make typecheck    # mypy
make test         # pytest with coverage
make e2e          # Playwright browser smoke test (scripted LLM, no API key)
make check        # all of the above
make docs         # build the documentation site (strict)
make docs-serve   # preview the documentation on http://127.0.0.1:8001
```

Unit tests use a scripted provider, so they run without API keys or network access. The browser smoke
test (`tests/e2e/`) boots the app with the same scripted provider, drives the chat and asserts on the
rendered SVG. Mermaid is served from the vendored bundle, so the test needs no network access.

## CI

`.github/workflows/ci.yml` runs on pull requests into `main` (not on drafts), weekly and on demand. GitHub
Actions bills every job rounded up to a full minute, so all checks are steps of **one job, `CI`**. The PR
title check is reported as the separate commit status `PR title` (re-checked by `pr-title.yml` when the
title is edited); `CI` and `PR title` are the checks to require in branch protection. A pull request only runs what its changed
paths need (`scripts/ci_scope.py`):

| Step | Runs when |
|---|---|
| `ruff check`, `ruff format --check`, `mypy`, PR title (Conventional Commits, status `PR title`) | always |
| `pytest` (3.14), `node --test`, Playwright smoke test | code, tests or scripts changed |
| strict Zensical docs build | docs, `CHANGELOG.md`, `CONTRIBUTING.md` or `zensical.toml` changed |
| Docker build + smoke test of `/api/config` | `Dockerfile`, `pyproject.toml` or backend code changed |
| store contract against the Cosmos DB emulator (non-blocking) | store code or store tests changed |
| `pytest` on 3.11 and 3.13 | weekly and manual runs only |

Changes under `.github/` and the weekly run run everything. Run `make lint typecheck test` (and
`make docs` / `make e2e` where relevant) locally before pushing; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Releases

`release-please.yml` runs on pushes to `main`. [release-please](https://github.com/googleapis/release-please)
keeps a release PR up to date (changelog, version bump in `pyproject.toml` and `arcviz/__init__.py`).
Merging that PR creates the tag and GitHub release; the workflow then publishes
`ghcr.io/thomasniebler/arcviz:<version>` / `:latest` and attaches the wheel to the release.

One-time repository setup:

1. *Settings → Actions → General → Workflow permissions*: allow GitHub Actions to create and approve
   pull requests.
2. Optional but recommended: add a fine-grained PAT (contents + pull requests: write) as the
   `RELEASE_PLEASE_TOKEN` secret. Without it the release PR is created with `GITHUB_TOKEN` and CI will not
   run on that PR.
3. Make sure the package `arcviz-agent` on GHCR is linked to the repository and set to public if the
   image should be pullable without a token.

## Deployment

The app is a single long-running process: it holds SSE connections open for the duration of a chat turn
(often 10–60 s) and needs an LLM API key. With the default `file` store (or `sqlite`) it also needs a
persistent volume and exactly one instance; with `ARCVIZ_STORE=cosmos` state lives in Azure Cosmos DB, no
volume is needed and several replicas are safe. Reasonable targets, roughly in order of effort:

| Target | Fit | Notes |
|---|---|---|
| **Docker Compose on a small VM** (Hetzner, DigitalOcean, your own box) | ★★★ | `docker compose up -d` with the provided `docker-compose.yml`, a named volume for `/data`, Caddy or Traefik in front for TLS. Cheapest and simplest. |
| **Fly.io** | ★★★ | `fly launch` from the Dockerfile, a Fly Volume mounted at `/data`, secrets via `fly secrets set ANTHROPIC_API_KEY=…`. SSE works out of the box; scale to exactly one machine (file store is single-writer). |
| **Azure Container Apps** | ★★ | Image from GHCR, state either on an Azure Files volume at `/data` (one replica) or in a serverless Cosmos DB account via `ARCVIZ_STORE=cosmos` (several replicas, managed identity), Key Vault reference for the API key. Built-in authentication (Entra ID "Easy Auth") is a good way to put a login in front. Step-by-step guide: [docs/deploy-azure.md](docs/deploy-azure.md). |
| **Render / Railway** | ★★ | Deploy from the repo (Dockerfile detected), attach a persistent disk at `/data`, one instance. |
| **Kubernetes** | ★ | Deployment with `replicas: 1`, `strategy: Recreate`, a PVC at `/data`, Secret for the key, Ingress with long timeouts for SSE. Only if you already run a cluster. |

Things to decide before exposing it publicly:

- **Authentication.** Off by default: anyone who can reach the URL can spend your API budget and edit
  diagrams. Set `ARCVIZ_AUTH=token` with a long random `ARCVIZ_AUTH_TOKEN` (the browser shows a login
  page, scripts send a bearer token), or put an identity-aware proxy in front (Azure Easy Auth,
  Cloudflare Access, oauth2-proxy) and let the app take each user's identity from its headers with
  `ARCVIZ_AUTH=proxy` (presets `easyauth`, `oauth2-proxy`). Behind a proxy Arcviz knows its users: everyone keeps
  their own projects in a **personal workspace**; only a project's owner shares it with others as viewer or editor
  ([own and shared projects](docs/guide/workspaces.md)). The app must then be reachable only through the
  proxy, and `ARCVIZ_ADMIN_USER` names the owner of the data that existed before. See
  the [configuration reference](docs/reference/configuration.md#authentication). Request size limits are
  on by default; rate limits per client can be enabled
  ([request limits](docs/reference/configuration.md#request-limits)).
- **Single writer.** The `file` and `sqlite` stores are safe for several threads in one process, but not
  for several replicas on a shared volume. Run exactly one instance, or use the `cosmos` store before
  scaling out. With replicas, live updates and presence reach every browser within about one poll interval
  (`ARCVIZ_LIVE_POLL_SECONDS`).
- **Backups.** State is just files under `/data`; snapshot the volume or sync it to object storage.
