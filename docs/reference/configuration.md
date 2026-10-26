# Configuration

Arcviz is configured through environment variables only. All of them are optional; the defaults
run Arcviz locally against Anthropic.

Every variable is checked when the server starts. A malformed value stops the start with one message that lists
**every** problem, each naming the variable, the expected format and the value, for example:

```
Invalid configuration (2 problems):
  - ARCVIZ_MAX_ROUNDS must be an integer of at least 1, got 'twelve'.
  - ARCVIZ_PORT must be an integer from 1 to 65535, got '80a'.
```

`arcviz` prints it without a traceback and exits with status 2.

## Server

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_HOST` | `127.0.0.1` | Bind address. The Docker image sets `0.0.0.0`. |
| `ARCVIZ_PORT` | `8000` | Port (1–65535). |
| `ARCVIZ_DATA_DIR` | `data` | `file` store: directory for diagrams, versions and transcripts; `sqlite` store: directory of `arcviz.sqlite3`. The Docker image sets `/data`. |

## Persistence backend

See [Persistence](../spec/persistence.md). The `file` and `sqlite` backends need a persistent volume
and a single instance; `cosmos` needs neither.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_STORE` | `file` | `file`, `sqlite` or `cosmos` (Azure Cosmos DB, NoSQL API; needs `pip install "arcviz-agent[cosmos]"`). An unknown value or a missing extra fails at startup with a clear `StoreError`. |
| `ARCVIZ_STORE_URL` | unset | `cosmos`: account endpoint, e.g. `https://<account>.documents.azure.com:443/`. Required for `cosmos`. |
| `ARCVIZ_STORE_KEY` | unset | `cosmos`: account key. Unset = `DefaultAzureCredential` (managed identity; needs the *Cosmos DB Built-in Data Contributor* role). |
| `ARCVIZ_STORE_DATABASE` | `arcviz` | `cosmos`: database name (created if missing). |
| `ARCVIZ_STORE_CONTAINER` | `diagrams` | `cosmos`: container name (created if missing, partition key `/diagram_id`). |

**Cosmos DB Time to Live.** Presence is shared across replicas through short-lived items (ADR-0104), which Cosmos DB
removes only when the container's Time to Live is on. The app creates new containers with it and, at every start,
turns it on for an existing container (`default_ttl = -1`: nothing expires unless an item says so; every other
container setting, such as a custom indexing policy, is passed back unchanged, and a container with unique keys is left
alone). With managed
identity and data-plane roles only, that is not permitted: the start-up log says so, and the readers remove stale
items themselves. Set it in your infrastructure code instead: Bicep/ARM `resource.defaultTtl: -1` on the
`sqlContainers` resource, Terraform `default_ttl = -1` on `azurerm_cosmosdb_sql_container`, or once with
`az cosmosdb sql container update --account-name … --database-name … --name … --ttl -1`.

`arcviz-migrate --from file:data --to sqlite:data` copies all data (diagrams, projects, workspaces and users)
between backends, `arcviz-migrate --projects file:data` puts every diagram without a project into the default
project, and `arcviz-migrate --workspaces file:data --admin USER` moves data written before workspaces (owner: USER)
and the projects of M20 team workspaces into personal workspaces, keeping every access as a share (the server does
this at every start; `--reassign MISSING=USER` recovers projects of a workspace that no longer exists); see
[Persistence: migration](../spec/persistence.md#migration). `arcviz-migrate --settle-pending file:data` clears the
pending locator tokens of writers that provably stopped or died (it watches the writer heartbeats for one lease first;
`--all` in a maintenance window with every replica stopped also clears tokens written before M24); see
[Projects: pending tokens](../spec/projects.md#pending-tokens-of-writers-that-died).

## Git mirror (optional)

When `ARCVIZ_GIT_DIR` is set, every version is also committed to a Git repository (see
[Versions](../guide/versions.md#git-history)). The file store stays the primary record; the mirror
never blocks the application.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_GIT_DIR` | unset (mirror off) | Working tree of the mirror repository. Created and `git init`ed if it does not exist. In the container, use a path on the `/data` volume, e.g. `/data/git`. |
| `ARCVIZ_GIT_PUSH` | `0` | `1`/`true`/`yes`: push after every commit to `ARCVIZ_GIT_REMOTE` (`git push -u <remote> HEAD`). The remote must already be configured in the repository; credentials come from the environment (SSH agent, credential helper, token in the URL). |
| `ARCVIZ_GIT_REMOTE` | `origin` | Remote name (or URL) used for pushing. Credentials in a URL (`https://user:token@host/…`, a query) are shown as `***` in logs and in `GET /api/config`, also in git's output. |
| `ARCVIZ_GIT_AUTHOR_NAME` / `ARCVIZ_GIT_AUTHOR_EMAIL` | `Arcviz` / `arcviz@localhost` | Author and committer of the mirror commits. |

The `git` binary must be on `PATH` (the Docker image includes it). Problems (missing binary,
unconfigured remote, failed push) are logged and reported in `GET /api/config` under `git.last_error`.

## Element kinds (optional)

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_KINDS_FILE` | unset (built-in kinds only) | Path of a JSON file that adds node kinds (container or leaf, with an optional Mermaid style and shape) and edge kinds (line style, default direction) to the built-in ones. Read once at startup; an invalid file stops the server with a message naming the kind. Format and example: [Elements: custom kinds](../guide/elements.md#custom-kinds). In the container, mount the file and point the variable at it. Node kinds may have an `icon` from the vendored packs. Extends deployment diagrams only. |
| `ARCVIZ_KIND_PACKS` | unset (no pack) | Comma-separated list of shipped kind packs to activate: `aws`, `azure`, `gcp` (cloud service kinds with icons, see [Cloud icons and kind packs](../guide/elements.md#cloud-icons-and-kind-packs)). Loaded before `ARCVIZ_KINDS_FILE`; an unknown pack or a kind defined twice stops the server. |

## LLM provider

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_PROVIDER` | `anthropic` | `anthropic`, or `openai` for any OpenAI-compatible server. `openai-compatible` and `ollama` are accepted aliases for `openai`. The provider's SDK is an extra (`pip install "arcviz-agent[anthropic]"` or `[openai]`; the Docker image has both); an unknown value or a missing SDK stops the start with a message naming the extra. |
| `ARCVIZ_MODEL` | `claude-opus-5-5` for Anthropic, `gpt-4o` for OpenAI | Model id passed to the provider. |
| `ARCVIZ_MAX_ROUNDS` | `12` | Maximum number of model calls per chat turn (at least 1). Each round can contain several tool calls. When the limit is reached the turn ends with an error event. |
| `ARCVIZ_STREAM` | `1` | Stream the reply text to the browser while the model writes (`text_delta` events). `0`, `false` or `no` switch it off, e.g. for a gateway or OpenAI-compatible server that does not support streaming or `stream_options`. |
| `ARCVIZ_TURN_TOKEN_BUDGET` | `0` (no cap) | Maximum input + output tokens of all model calls of one chat turn. Checked before every model call after the first: once reached, the turn ends with an error event instead of calling the model again. A turn can exceed it by the cost of the call that crossed it. |
| `ARCVIZ_LLM_TIMEOUT` | SDK default (600 s) | Timeout in seconds for one request to the provider (both providers). A timed-out request is retried like other transient errors, so the worst case is roughly timeout × (retries + 1). |
| `ARCVIZ_LLM_MAX_RETRIES` | SDK default (2) | How often a failed provider request is retried with exponential backoff: connection errors, timeouts, HTTP 408, 409, 429 and 5xx (`Retry-After` is honoured). `0` disables retries. A streamed reply is never retried once text has been sent. |
| `ARCVIZ_HISTORY_TURNS` | `30` | Transcript entries (user and assistant messages) fed back to the model as conversation history, after the compaction summary if one exists. |
| `ARCVIZ_CHAT_COMPACT_AFTER` | `60` | Compact the chat transcript once it holds more than this many entries: everything before the newest `ARCVIZ_CHAT_KEEP` entries is replaced by a cumulative summary written by the model (see [Agent: chat compaction](../spec/agent.md#chat-compaction)). `0` disables automatic compaction. |
| `ARCVIZ_CHAT_KEEP` | `30` | Entries kept verbatim after a compaction (at least 1; the tail always starts with a user message). |
| `ARCVIZ_CONTEXT_BUDGET` | `12000` | Maximum size (characters) of the model summary in the system prompt. Larger diagrams get their biggest containers collapsed to one line each until the summary fits; the model fetches details with `get_diagram(focus=…)`. `0` disables collapsing. |

### Anthropic

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | | API key. Required unless the SDK finds credentials another way. |
| `ANTHROPIC_BASE_URL` | SDK default | Alternative endpoint, for example a gateway. |
| `ARCVIZ_EFFORT` | unset | Passed as `output_config.effort` (`low`, `medium`, `high`, `max`). |
| `ARCVIZ_ANTHROPIC_FALLBACKS` | `1` | Server-side fallback to another Claude model if a request is declined for policy reasons. `0`, `false` or `no` disable it. |

### OpenAI-compatible

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | | API key. Local servers such as Ollama ignore it, but the SDK requires a value; any string works. |
| `OPENAI_BASE_URL` | SDK default (api.openai.com) | Endpoint, including the `/v1` suffix, for example `http://localhost:11434/v1`. Azure OpenAI: `https://<account>.openai.azure.com/openai/v1/`. |
| `ARCVIZ_OPENAI_AUTH` | `key` | `key`: send `OPENAI_API_KEY`. `entra`: send an **Entra ID token** instead (keyless Azure OpenAI): obtained with azure-identity's `DefaultAzureCredential` (the container app's managed identity, workload identity, `az login` locally; `AZURE_CLIENT_ID` selects a user-assigned identity) and refreshed five minutes before it expires; `OPENAI_API_KEY` is ignored. Needs `ARCVIZ_PROVIDER=openai`, `OPENAI_BASE_URL` and `pip install "arcviz-agent[azure-openai]"` (the Docker image has it); otherwise the start stops naming what is missing. See [Deploy to Azure](../deploy-azure.md#keyless-azure-openai-entra-id). |
| `ARCVIZ_OPENAI_TOKEN_SCOPE` | `https://cognitiveservices.azure.com/.default` | `entra`: the scope of the token. |

## Authentication

Off by default. See [Authentication](../architecture/specs/authentication.md) for the design and
[Deploy to Azure](../deploy-azure.md#6-put-a-login-in-front-entra-id-easy-auth) for a proxy setup. An invalid combination
stops the start-up with an error naming the variable.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_AUTH` | `off` | `off`: no authentication. `token`: a shared secret, sent by API clients as `Authorization: Bearer <token>`; the browser shows a login page once and then keeps an HttpOnly session cookie (7 days). `proxy`: an authenticating reverse proxy passes the user in a header; requests without it are refused. |
| `ARCVIZ_AUTH_TOKEN` | unset | `token` mode: the secret, at least 16 characters, e.g. `openssl rand -hex 32`. Changing it ends every session. |
| `ARCVIZ_AUTH_PROXY_PRESET` | unset | `proxy` mode: default header names for `easyauth` (Azure Container Apps / App Service Easy Auth: id `X-MS-CLIENT-PRINCIPAL-ID`, name from the `X-MS-CLIENT-PRINCIPAL` claims or `X-MS-CLIENT-PRINCIPAL-NAME`) or `oauth2-proxy` (`X-Forwarded-User`, `X-Forwarded-Preferred-Username`, `X-Forwarded-Email`). |
| `ARCVIZ_AUTH_PROXY_HEADER` | from the preset | `proxy` mode: the header with the **stable user id** (required unless a preset gives it). Requests without a valid id get 401. **Only safe if nothing but the proxy can reach the app** and the proxy overwrites or strips the identity headers on every request; anyone who can reach the app directly can claim any identity. |
| `ARCVIZ_AUTH_PROXY_NAME_HEADER` | from the preset | `proxy` mode: the header with the display name (default: the id). |
| `ARCVIZ_AUTH_PROXY_EMAIL_HEADER` | from the preset | `proxy` mode: the header with the e-mail address. Only an address from this header (which the proxy must vouch for) lets a project owner share with the user by e-mail; addresses from Easy Auth claims or the name header are shown but never identify anyone (ADR-0091). |
| `ARCVIZ_ADMIN_USER` | `admin` (`off`, `token`); unset (`proxy`) | The admin identity: in `off` and `token` mode every request acts as this user; in every mode it owns the data written before workspaces existed and the default project (diagrams created without a project), in its personal workspace. In `proxy` mode set it to the id header's value of the person who should own that data; until then it stays hidden (see [Own and shared projects](../guide/workspaces.md)). |
| `ARCVIZ_ADMIN_NAME` / `ARCVIZ_ADMIN_EMAIL` | `Administrator` / unset | Display name and e-mail of the admin identity in `off` and `token` mode. |
| `ARCVIZ_AUTH_DOCS` | `protected` | `public`: serve the documentation under `/docs` without authentication. |
| `ARCVIZ_COOKIE_SECURE` | `auto` | `auto`: the session cookie is `Secure` when the request came over HTTPS (behind a TLS-terminating proxy, set uvicorn's `FORWARDED_ALLOW_IPS` so that the scheme is seen, or set `true`). `true`/`false` force it. |

`/healthz` and `/readyz` never need authentication.

## Agent access (MCP)

Off by default. The endpoint, the tools and the credentials are described in [Coding agents](../guide/agents.md) and, as
implemented, in the [agent access specification](../spec/agents.md). It needs `pip install "arcviz-agent[mcp]"` (the Docker
image has it: the official MCP SDK and PyJWT with cryptography); a missing package or a malformed value stops the start-up
with an error naming the variable or the extra.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_MCP` | `0` | `1`/`true`/`yes` serves the MCP endpoint (Streamable HTTP, `POST` only, stateless) at `ARCVIZ_MCP_PATH`. Agents authenticate there with a personal access token (created by each user in the UI, valid only on this path), with an OIDC access token when `ARCVIZ_MCP_OAUTH_ISSUER` is set, in `token` mode with the shared secret, and without credentials in `off` mode. Proxy identity headers and the session cookie are never accepted on this path. |
| `ARCVIZ_MCP_PATH` | `/mcp` | The path, absolute without trailing slash, at most four segments of letters, digits, `.`, `_`, `~`, `-`; not under `/api`, `/docs`, `/static`, `/.well-known`, `/login`, `/logout`, `/healthz`, `/readyz`. |
| `ARCVIZ_PUBLIC_URL` | derived from each request | The origin clients reach the app at (`https://arcviz.example.org`, no path). Used in the OAuth metadata and the `WWW-Authenticate` challenge; without it the scheme and host of the request (`X-Forwarded-Proto`, `X-Forwarded-Host`) are used. Set it behind a proxy that does not forward them. |
| `ARCVIZ_MCP_OAUTH_ISSUER` | unset | An OpenID Connect issuer (`https://login.microsoftonline.com/<tenant>/v2.0` for Entra ID; `http` only for `localhost`). Turns on OIDC access tokens on the MCP path and the [protected resource metadata](https://www.rfc-editor.org/rfc/rfc9728) at `/.well-known/oauth-protected-resource`. Needs `ARCVIZ_MCP=1` and the audience. Keys are found by OpenID discovery and cached. |
| `ARCVIZ_MCP_OAUTH_AUDIENCE` | unset | The `aud` an access token must carry (for Entra ID the application (client) id or the `api://…` URI of the app registration). Required with the issuer. |
| `ARCVIZ_MCP_OAUTH_REQUIRED_SCOPE` | unset | A scope that must be in the token's `scp` or `scope` claim. |
| `ARCVIZ_MCP_OAUTH_SCOPES` | unset | Comma-separated scopes advertised as `scopes_supported` in the metadata. |
| `ARCVIZ_PROPOSAL_RETENTION_DAYS` | `30` | How long decided proposals of agents stay listed (also at most the newest 20 per project). Pending proposals are never dropped. |

## Project steward (optional)

The background check that proposes fixes and next steps ([design](../architecture/specs/project-steward.md)). Off by default; a
project opts in itself (owner, *Architektur* page). Everything the steward makes is a pending project proposal; nothing is applied
without a person.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_STEWARD` | `0` | `1` starts the worker and offers the switch and **Jetzt prüfen**. Off: the routes answer, the run route with 409. |
| `ARCVIZ_VERIFIER_PROVIDER` | unset | The provider of the **second model** that verifies what the main model proposes (`anthropic` or `openai`). Set together with `ARCVIZ_VERIFIER_MODEL`; its key and endpoint are the vendor's usual variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`). Must not be the chat's provider *and* model, else the start stops. Without it the steward proposes only what the review rules settle. |
| `ARCVIZ_VERIFIER_MODEL` | unset | The verifier's model id. |
| `ARCVIZ_STEWARD_DEBOUNCE_SECONDS` | `300` | A project is checked this long after its last change. |
| `ARCVIZ_STEWARD_SWEEP_SECONDS` | `3600` (at least 60) | Every enabled project is checked at the latest this long after its last check (changes other replicas made). |
| `ARCVIZ_STEWARD_MAX_RUNS_PER_DAY` | `4` | Runs per project and UTC day, manual runs included. |
| `ARCVIZ_STEWARD_MAX_ITEMS` | `15` (1 to 60) | Items of one run. |

`POST /api/projects/{id}/steward/run` is in the rate class of `ARCVIZ_RATE_LIMIT_REVIEW`.

## Request limits

See [Request size and rate limits](../architecture/specs/request-limits.md). Values are non-negative
integers; anything else stops the start-up with an error naming the variable. `0` disables a limit.
`GET /api/config` reports the effective values under `limits`.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_MAX_BODY_BYTES` | `1048576` (1 MiB) | Maximum request body of every `POST`/`PUT`/`PATCH` except the import (operations, proposals, chat, …). Larger requests get **413**. |
| `ARCVIZ_MAX_IMPORT_BYTES` | `10485760` (10 MiB) | Maximum body of `POST /api/diagrams/import` and `POST /api/projects/import`. |
| `ARCVIZ_MAX_CHAT_CHARS` | `20000` | Maximum length of a chat message in characters; longer ones get **413** before any model call. |
| `ARCVIZ_RATE_LIMIT_CHAT` | `0` (off) | Chat turns and manual compactions (model calls) per minute and client. Over the limit: **429** with `Retry-After`. |
| `ARCVIZ_RATE_LIMIT_REVIEW` | `0` (off) | Architecture reviews (model calls) per minute and client. |
| `ARCVIZ_RATE_LIMIT_SHARE` | `30` | Sharing and revoking (`POST`/`DELETE /api/projects/{p}/access…`) per minute and client; on by default because every attempt reveals whether a user id or e-mail address exists (`0` = unlimited). |
| `ARCVIZ_RATE_LIMIT_OPS` | `0` (off) | Other mutating API requests (operations, proposals, versions, undo/redo, create, import, delete, chat clear) per minute and client. |
| `ARCVIZ_RATE_LIMIT_AGENT` | `0` (off) | Requests to the MCP endpoint (`POST` on `ARCVIZ_MCP_PATH`) per minute and client. |

Fixed limits: 50 steps per `apply_batch` (400), 500 operations per proposal confirm (413).

A rate limit allows a burst of *N* requests and then *N* per minute (token bucket). The client is the
authenticated user in [proxy mode](#authentication), otherwise the client address. Behind a reverse
proxy, set uvicorn's `FORWARDED_ALLOW_IPS` to the proxy's address (for example `*` when only the proxy
can reach the container) so that the address is the client's; otherwise all clients share one
budget. Counters are kept in memory per process: with several replicas each one counts separately.

## Logs and telemetry

See [Operations](../architecture/specs/operations.md), section 4 (ADR-0111..0114).

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_LOG_FORMAT` | `text` | `text`: human-readable lines (uvicorn's own lines unchanged; the app's lines carry the request id in brackets). `json`: one JSON object per line on stderr for every record, uvicorn's too, for a log collector such as Log Analytics; uvicorn's access log is replaced by one `request` record per request. |
| `ARCVIZ_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`, for the app's own loggers (`arcviz.*`). Probe requests (`/healthz`, `/readyz`) are logged at `debug`. |
| `ARCVIZ_LOG_PSEUDONYM_KEY` | unset | A secret of at least 16 characters (store it as a secret, e.g. `openssl rand -hex 32`; it is never logged). The `user` field of log records is `u-` + 32 hex digits of the HMAC-SHA256 of the caller's id with this key, so it cannot be reversed by guessing ids; an operator with the key can compute a user's pseudonym. Unset: records have no `user` field. Changing the key changes every pseudonym. |
| `ARCVIZ_LOG_EXCEPTION_DETAILS` | `0` | `1`/`true`: JSON and exported records carry full tracebacks **with exception messages, which may contain user content** (ids from paths, names, e-mail addresses). For a debugging session only. Default: the exception type, a stable `error_code` and the frames (`exc_frames`), no message. |
| `ARCVIZ_TELEMETRY` | `off` | `otlp`: export request traces and the app's log records with OpenTelemetry over OTLP/HTTP, configured by the standard `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` … `azure-monitor`: export them to Application Insights with the Azure Monitor distro, configured by `APPLICATIONINSIGHTS_CONNECTION_STRING` (required). Both need `pip install "arcviz-agent[telemetry]"` (the Docker image has it). Besides request spans, the export carries spans inside chat turns (model calls, tool executions, store writes) and the metrics `arcviz.http.server.request.duration` (per route template, method and status), `arcviz.llm.duration`, `arcviz.llm.tokens`, `arcviz.live.streams` and, with `ARCVIZ_STORE=cosmos`, `arcviz.store.request_units` (Cosmos DB request charge per request, by operation and document kind); all with allow-listed attributes only (no ids, partition keys, paths, users or content). |
| `ARCVIZ_TELEMETRY_AUTH` | `connection-string` | With `ARCVIZ_TELEMETRY=azure-monitor`: `entra` sends telemetry with an Entra ID token from `DefaultAzureCredential` (managed identity, workload identity, Azure CLI; `AZURE_CLIENT_ID` picks a user-assigned identity, which needs the role *Monitoring Metrics Publisher* on the Application Insights resource) instead of the key in the connection string. `APPLICATIONINSIGHTS_CONNECTION_STRING` is still required: it names the resource and the ingestion endpoint (disable local authentication on the resource so its key grants nothing). Any other telemetry mode with `entra` is a start-up error. |

**JSON records.** Every record has `time` (UTC), `level`, `logger` and `message`; records written while a request is
served also `request_id`, `trace_id` (when the request carried a valid W3C `traceparent` or telemetry is on),
`client_request_id` and `user`. `request_id` is generated by the server for every request and returned in the
response's `X-Request-ID`; a safe incoming `X-Request-ID` (letters, digits, `._:-`, at most 128; the Container Apps
ingress sets one) is logged as `client_request_id`. `user` is the keyed pseudonym (see `ARCVIZ_LOG_PSEUDONYM_KEY`),
never the id, name or e-mail address from the proxy. Exceptions appear as `exc_type`, `error_code` and `exc_frames`
(see `ARCVIZ_LOG_EXCEPTION_DETAILS`). An unhandled error is logged once and answered with
`500 {"detail": "Internal Server Error", "request_id": "…"}`. Structured records add their fields at top level:

| `event` | Fields |
|---|---|
| `request` | `method`, `route` (the route template, e.g. `/api/diagrams/{diagram_id}/chat`; `/static`, `/docs` or `-` for other paths), `status`, `duration_ms`, `aborted` (`true` when the response did not complete: the client went away, logged at INFO with the status sent so far or `499`, or the stream broke after it started) |
| `llm_turn` | `provider`, `model`, `outcome` (`done`, `error`, `aborted`), `error_kind` (`budget`, `max_rounds`, `refusal`, `max_tokens`, `no_reply`, `exception`) and `error_type` on errors, `rounds`, `tool_calls`, `input_tokens`, `output_tokens` (and any other usage counter the provider reports), `duration_ms` |
| `llm_call` | the same for one call without the tool loop (compaction summary `purpose: text`, reviews `purpose: submit_review` …) |

Log records never contain request paths with ids, query strings, tokens, API keys, cookies, e-mail addresses, display
names, chat messages, model replies or diagram content. Warnings carry an exception's type, not its message (the Git mirror's output stays in `git.last_error` of
`GET /api/config`). Warnings about the data itself (migration, repair) name
diagram, project and user ids; with `oauth2-proxy`, whose `X-Forwarded-User` is often an e-mail address, set
`ARCVIZ_AUTH_PROXY_HEADER` to a stable non-e-mail claim if that matters. In `text` mode (local use) exceptions are
logged with their full traceback.

Started with `uvicorn --factory arcviz.server:create_app` instead of `arcviz`, pass `--no-access-log` (and
`--log-config` of your own if uvicorn's lines should be JSON too).

**Telemetry.** Request spans come from the FastAPI instrumentation (probes excluded, no headers captured). A span
keeps only method, route template, status code, scheme, protocol version and the path with `{id}` for every id; the
client address, user agent, host and full URL are removed (also attributes added late), span events keep only the
exception type, the span status only the exception type, and exported log records get the same request context and
exception summary as the JSON lines (a scrubbed copy: the local stderr output is unchanged). The instrumentation
records no request metrics. The Azure Monitor distro's other instrumentations
(outgoing HTTP, Azure SDK) stay off because their spans carry document URLs.

## Version history

See [Hardening](../architecture/specs/hardening.md) (#114, ADR-0129, ADR-0130). Invalid values stop the start-up.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_VERSIONS_PAGE` | `50` | How many of the newest versions every diagram response carries (1..500); older ones are fetched page by page (`GET /api/diagrams/{id}/versions?before=`). Undo and redo always see the whole history. |
| `ARCVIZ_VERSIONS_KEEP` | `0` | Retention: `0` keeps every version (the default). `N` (at least 10) lets each diagram keep at least its newest `N` versions: once a diagram has `N + SLACK` versions, every `SLACK` new versions the ones older than the newest `N + SLACK` are pruned (a grace for lists open in a browser), except the first version, everything undo and redo still need, and manual snapshots (see below). A version pruned while somebody looks at it answers 409 "reload". |
| `ARCVIZ_VERSIONS_PRUNE_SLACK` | `20` | A prune runs only every `SLACK` versions once a diagram has more than `KEEP + SLACK` (decided from the version number, so other writes cost nothing extra), and keeps `SLACK` versions of grace. |
| `ARCVIZ_VERSIONS_PRUNE_MANUAL` | off | `1`/`true`: manual snapshots (**Version sichern**) may be pruned too. Off, they are always kept. |

Pruned versions are gone from the store (the Git mirror, if any, keeps its history); undo and redo work within the
versions that are kept. With Cosmos DB every kept version is an item of the diagram's partition, so the retention
bounds both its size and the cost of listing it.

## Live updates

See [Collaboration](../architecture/specs/collaboration.md) (ADR-0095, ADR-0098). Invalid values stop the start-up.

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_WRITER_HEARTBEAT_SECONDS` | `30` | How often every process renews its writer heartbeat document (seconds, at least 1; #189). Another process declares a writer dead after its document stood still for 10 heartbeats (the lease) on its own clock and then settles that writer's pending locator tokens; a process that could not renew for half a lease places no diagrams until it can. Use the same value on every replica. |
| `ARCVIZ_LIVE_POLL_SECONDS` | `5` | Every open live stream re-checks the caller's access and polls the diagram's revision this often (seconds, decimals allowed, at least 0.1). Changes made on other replicas arrive after at most this long; each open stream costs three point reads per interval. |
| `ARCVIZ_LIVE_MAX_SECONDS` | `900` | A live stream ends after this long and the browser reconnects (bounds a stream's lifetime when the app is not started with `arcviz`). |
| `ARCVIZ_LIVE_MAX_STREAMS` | `1000` | Open live streams per process; more get **503** (`0` = unlimited). |
| `ARCVIZ_LIVE_MAX_STREAMS_PER_USER` | `20` | Open live streams per user and process (one per browser tab); more get **429** (`0` = unlimited). |

Presence (who views a diagram, their selection, who chats) is kept in the memory of each process. With several
replicas, changes reach every browser through the poll, but a browser sees only the people connected to the same
replica. On SIGTERM/SIGINT the app ends open streams before the server drains connections (also with `uvicorn
--factory` or a uvicorn worker); where that hook cannot run, set uvicorn's `--timeout-graceful-shutdown`.

## Precedence and validation

Variables are read and checked when the server starts (a malformed `ARCVIZ_LLM_TIMEOUT` or
`ARCVIZ_LLM_MAX_RETRIES` stops the start like every other malformed number); the provider itself is created on the
first chat message. Two failure modes look different:

- **The provider cannot be constructed** (no key at all for the OpenAI SDK, or a provider created later than the
  start-up check): `/chat` answers
  HTTP 503 and `/api/config` reports `provider_ready: false` with the reason in `provider_error`.
- **The key is present but invalid or revoked**: construction succeeds (`provider_ready: true`, no
  `provider_error`); the failed API request is reported inside the chat stream as an `error` event
  (for example `AuthenticationError: …`) and shown as a red bubble in the UI.

## Docker Compose

The shipped `docker-compose.yml` passes `ARCVIZ_PROVIDER`, `ARCVIZ_MODEL`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ARCVIZ_GIT_DIR`, `ARCVIZ_GIT_PUSH`, `ARCVIZ_GIT_REMOTE`,
`ARCVIZ_AUTH`, `ARCVIZ_AUTH_TOKEN`, `ARCVIZ_LOG_FORMAT`, `ARCVIZ_LOG_LEVEL` and `ARCVIZ_LOG_PSEUDONYM_KEY` through from the host environment or a `.env` file, and mounts a
named volume at `/data`. It binds port 8000 on all interfaces: on a shared network, set
`ARCVIZ_AUTH=token` and `ARCVIZ_AUTH_TOKEN` (for example in `.env`).
