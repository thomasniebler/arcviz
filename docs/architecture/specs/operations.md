# Operations: configuration errors, optional SDKs, reproducible builds, structured logs, keyless Azure OpenAI

- Status: Implemented
- Issues: #118, #84, #116, #83, #156, #188 (milestone M9 *Operations & deployment*)
- ADRs: [0106](../adr/0106-configuration-problems-collected-at-start-up.md),
  [0107](../adr/0107-llm-sdks-as-extras-checked-at-start-up.md),
  [0108](../adr/0108-the-image-installs-every-runtime-extra.md),
  [0109](../adr/0109-uv-lock-for-every-python-install.md),
  [0110](../adr/0110-base-images-pinned-by-digest.md),
  [0111](../adr/0111-json-logs-with-an-in-house-formatter.md),
  [0112](../adr/0112-logs-carry-pseudonymous-users-and-route-templates.md),
  [0113](../adr/0113-request-ids-and-trace-ids.md),
  [0114](../adr/0114-opentelemetry-as-an-optional-extra.md),
  [0115](../adr/0115-entra-id-tokens-for-azure-openai.md),
  [0116](../adr/0116-documentation-only-in-the-project.md)
- Depends on: [Health and readiness probes](health-probes.md), [LLM call resilience](llm-resilience.md),
  [Authentication](authentication.md), [Personal workspaces](personal-workspaces.md)

## Summary

The deployment target is one service, scaled out to a few replicas, on Azure Container Apps with Cosmos DB, behind
an authenticating proxy (Easy Auth or oauth2-proxy). Operating it needs five things the app does not have yet:
start-up errors that name every misconfigured variable at once instead of a bare `ValueError` traceback (#118);
LLM SDKs that are installed only where they are used, with a clear start-up error when the configured provider's
SDK is missing (#84); builds that install the same dependencies and base images every time (#116); logs that a log
workspace can query (JSON, one record per request and per model turn with token usage, request and trace ids, a
pseudonymous user, never an e-mail address, a secret or diagram content), with an optional OpenTelemetry or Azure
Monitor export (#83); and access to Azure OpenAI with the container app's managed identity instead of an API key
(#156).

None of this adds a mandatory runtime dependency (CLAUDE.md rule 5): the LLM SDKs, `azure-identity` and the
OpenTelemetry packages are extras, imported lazily.

## Goals

- **#118** One helper reads every numeric variable of the server and its subsystems and names the variable, the
  value and the expected format; start-up collects the problems of all subsystems and stops with one message that
  lists them all.
- **#84** `anthropic` and `openai` are extras. Start-up checks that the configured provider's SDK is importable and
  says which extra to install. The Docker image installs every runtime extra.
- **#116** One committed lock (`uv.lock`) used by CI, the Docker build, the documentation builds and the release
  (which also attaches a hashed requirements file for installing the wheel reproducibly). Base images pinned by
  version and digest. A documented update path.
- **#83** Opt-in JSON logs (`ARCVIZ_LOG_FORMAT=json`): every record carries the request id, the trace id and the
  pseudonymous user of the request that caused it; one `request` record per HTTP request and one `llm_turn` /
  `llm_call` record per model turn or call with token usage. Optional export through OpenTelemetry (OTLP) or Azure
  Monitor as the `telemetry` extra. `/healthz` and `/readyz` unchanged. Tests prove that no secret, e-mail address
  or diagram content reaches a log record.
- **#156** `ARCVIZ_OPENAI_AUTH=entra`: the OpenAI-compatible provider sends `Authorization: Bearer <Entra token>`
  for the Cognitive Services scope, obtained from `azure-identity`'s `DefaultAzureCredential` (managed identity in
  the container app) and refreshed before it expires. Tested with a fake credential.

## Non-goals

- Metrics (counters, histograms). The `llm_turn` records carry the numbers a log query can aggregate; OpenTelemetry
  metrics can follow when someone needs dashboards (follow-up).
- Spans inside a turn (one per model call or tool). Requests are traced by the FastAPI instrumentation; turn
  records correlate through the trace id.
- Entra authentication of the Azure Monitor exporter itself (`APPLICATIONINSIGHTS_CONNECTION_STRING` is the only
  supported way; follow-up).
- Infrastructure as code. The repository has none; the deployment guide gets the `az` commands.
- Rewriting the free-text warnings that already exist (they name diagram, project and user ids for the operator,
  see *Privacy* below).

## Design

### 1. Configuration problems (#118)

New module `arcviz/envconfig.py` (stdlib only, no package imports; the module table allows `limits`, `live`,
`llm/` and `observability` to import it):

```python
class ConfigError(ValueError):
    problems: tuple[str, ...]  # one sentence per problem, each naming its variable


class Env:
    def __init__(self, environ: Mapping[str, str] | None = None): ...  # default: os.environ
    def text(self, name, default="") -> str  # stripped
    def int(self, name, default, *, minimum=0, maximum=None) -> int
    def float(self, name, default, *, minimum=0.0) -> float
    def optional_int(self, name, *, minimum=0) -> int | None  # None when unset
    def optional_float(self, name, *, minimum=0.0) -> float | None
    def choice(self, name, choices, default) -> str  # lower-cased
    def problem(self, message) -> None  # a problem found by the caller (a combination of variables)
    def check(self) -> None  # ConfigError with every problem so far, or nothing
```

A malformed value is recorded as a problem and the default is returned, so parsing goes on and finds the next
problem. Messages name the variable, the expected format and the value, for example
`ARCVIZ_MAX_ROUNDS must be an integer of at least 1, got 'twelve'.` (values of these variables are never secrets).
`ConfigError` subclasses `ValueError`, so existing callers that catch `ValueError` keep working.

Users of the helper: `LimitConfig.from_env` (replaces `limits.env_int`), `LiveConfig.from_env` (replaces
`live._env_seconds`), `ProviderConfig.from_env` (replaces `factory._env_number`), the new `LogConfig.from_env`, and
the agent settings in `server.py` (`AgentSettings.from_env`):

| Variable | Rule (unchanged default) |
|---|---|
| `ARCVIZ_MAX_ROUNDS` | integer ≥ 1 (12) |
| `ARCVIZ_CONTEXT_BUDGET` | integer ≥ 0, 0 = no collapsing (12000) |
| `ARCVIZ_HISTORY_TURNS` | integer ≥ 0 (30) |
| `ARCVIZ_CHAT_COMPACT_AFTER` | integer ≥ 0, 0 = off (60) |
| `ARCVIZ_CHAT_KEEP` | integer ≥ 1 (30) |
| `ARCVIZ_TURN_TOKEN_BUDGET` | integer ≥ 0, 0 = no cap (0) |
| `ARCVIZ_PORT` | integer 1–65535 (8000) |
| `ARCVIZ_LLM_TIMEOUT` / `ARCVIZ_LLM_MAX_RETRIES` | number ≥ 0 / integer ≥ 0 (SDK defaults) |
| limits and live variables | as before (non-negative integers; seconds ≥ 0.1) |

**Collecting across subsystems.** `create_app` builds every configuration (kinds catalogue first, as before, then
limits, authentication, live, logging, agent settings and, when no provider was injected, the provider
configuration and its SDK check) inside one collector: each `ConfigError` contributes its problems, any other
`ValueError` of a `from_env` (the authentication rules) its message. Afterwards one `ConfigError` lists them all:

```
Invalid configuration (2 problems):
  - ARCVIZ_MAX_ROUNDS must be an integer of at least 1, got 'twelve'.
  - ARCVIZ_LIVE_POLL_SECONDS must be a number of seconds, got 'often'.
```

`main()` adds its own variables (`ARCVIZ_PORT`) to the same list, prints the message to stderr without a
traceback and exits with status 2. Started with `uvicorn --factory arcviz.server:create_app`, the message ends
uvicorn's traceback.

### 2. LLM SDKs as extras (#84)

`pyproject.toml`: the core dependencies are `fastapi`, `uvicorn[standard]`, `pydantic`. Extras:

| Extra | Packages | For |
|---|---|---|
| `anthropic` | `anthropic>=0.50` | `ARCVIZ_PROVIDER=anthropic` (the default) |
| `openai` | `openai>=1.106` | `ARCVIZ_PROVIDER=openai` (1.106 accepts a callable API key, §5) |
| `azure-openai` | `openai>=1.106`, `azure-identity>=1.17` | `ARCVIZ_OPENAI_AUTH=entra` |
| `cosmos` | `azure-cosmos>=4.9`, `azure-identity>=1.17` | `ARCVIZ_STORE=cosmos` (unchanged) |
| `telemetry` | `azure-monitor-opentelemetry>=1.6`, `opentelemetry-exporter-otlp-proto-http>=1.27` | `ARCVIZ_TELEMETRY` (§4) |
| `dev` | test tools plus every runtime extra | tests, CI |

`arcviz/llm/factory.py`:

- `SDK_EXTRAS = {"anthropic": ("anthropic", "anthropic"), "openai": ("openai", "openai")}`: per provider the module
  to import and the extra that provides it.
- `ProviderConfig.from_env(environ=None)` collects its problems (`Env`): unknown `ARCVIZ_PROVIDER`, the numeric
  variables, `ARCVIZ_OPENAI_AUTH`, `entra` without `OPENAI_BASE_URL` or with a provider other than `openai`.
- `check_provider(config) -> list[str]`: the importability problems (`importlib.util.find_spec`, no import), e.g.
  `ARCVIZ_PROVIDER=anthropic needs the Anthropic SDK: pip install "arcviz-agent[anthropic]".`; with `entra` also
  `azure.identity` (`arcviz-agent[azure-openai]`).
- `create_provider` turns a `ModuleNotFoundError` of the SDK into the same message (a `ConfigError`), so the lazy
  path (`get_provider`, HTTP 503) explains it too.

Start-up (`create_app` without an injected provider) runs `ProviderConfig.from_env` and `check_provider` in the
collector of §1, so a missing SDK, an unknown provider and a malformed `ARCVIZ_LLM_*` value stop the start. A
missing or rejected API key does not (it stays a readiness and chat-stream error, as before): the provider is
still created on the first model call. The Docker image installs all runtime extras (ADR-0108,
`ARG ARCVIZ_EXTRAS`).

### 3. Reproducible builds (#116)

- **Lock.** `uv.lock` (uv's universal lock with hashes for every distribution) is committed. `uv sync --locked`
  fails when `pyproject.toml` and the lock disagree, so CI catches a forgotten `uv lock`.
- **Docs toolchain** moves from `requirements-docs.txt` (deleted) into PEP 735 dependency groups, which are not
  package metadata and may therefore use a Git source: `docs = ["zensical>=0.0.60"]` and
  `docs-publish = ["mike"]` with `[tool.uv.sources] mike = { git = "https://github.com/squidfunk/mike.git" }`
  (locked to a commit).
- **Users of the lock:**

  | Where | Command |
  |---|---|
  | `make install` | `uv sync --locked --extra dev --group docs --group docs-publish` (into `.venv`) |
  | CI setup action | `uv sync --locked --extra dev` (uv version pinned, cache keyed on `uv.lock`) |
  | CI docs step | `uv sync --locked --extra dev --group docs` |
  | CI 3.11/3.13 | `UV_PROJECT_ENVIRONMENT=<venv> uv sync --locked --extra dev --python <v>` |
  | Docker `docs` stage | `uv sync --locked --only-group docs --no-install-project` |
  | Docker runtime stage | `uv sync --locked --no-dev --no-editable` with the image's extras into `/opt/venv` |
  | `docs.yml` | `uv sync --locked --only-group docs --only-group docs-publish --no-install-project` |
  | release (`publish-wheel`) | docs bundle as in Docker; `uv build`; `uv export --frozen --no-emit-project` of the runtime extras with hashes, attached as `arcviz-agent-<version>-requirements.txt` next to the wheel |

  The wheel's metadata keeps lower bounds (it is installed into other environments); the attached requirements file
  reproduces the tested set: `pip install --require-hashes -r arcviz-agent-<v>-requirements.txt` then
  `pip install --no-deps arcviz_agent-<v>-py3-none-any.whl`.
- **Build backend** pinned exactly (`hatchling==<version>` in `[build-system]`), so `uv build` and the image build
  the same wheel.
- **Base images** pinned by version and digest: `python:3.14.<z>-slim-trixie@sha256:…` (both stages) and
  `ghcr.io/astral-sh/uv:<x.y.z>@sha256:…`; the uv in the image, in CI and in `make install` is the same release.
  Debian packages (`git`, `ca-certificates`) still come from the distribution's current archive (security fixes),
  the one documented exception.
- **Update path.** `make lock` (re-lock after editing `pyproject.toml`), `make upgrade` (`uv lock --upgrade`, then
  `make check`), and for the images the steps in `docs/spec/build-and-release.md` (`docker buildx imagetools
  inspect <image>:<tag>` and edit the `FROM` lines). The owner enabled Dependabot (`.github/dependabot.yml`:
  `uv`, `docker`, `github-actions`, monthly, grouped; about one to three pull requests and CI runs a month); the
  GitHub Actions are pinned by commit SHA with the version as a comment so it can bump them.
- `scripts/ci_scope.py`: `uv.lock` turns on the `python`, `docker` and `cosmos` scopes (like `pyproject.toml`); the
  docs scope watches `pyproject.toml` and `uv.lock` instead of `requirements-docs.txt`.

### 4. Structured logging and telemetry (#83)

Since M24 (#187, [follow-ups](follow-ups.md), ADR-0134) the export also carries the app's own spans inside chat turns
and four metrics, all by allow-list, and can authenticate to Azure Monitor with Entra ID (`ARCVIZ_TELEMETRY_AUTH`).

New module `arcviz/observability.py` (imports `envconfig` only):

**Configuration** (`LogConfig.from_env`):

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_LOG_FORMAT` | `text` | `text`: as before (uvicorn's own log lines; the app's records with a request id when there is one). `json`: one JSON object per line on stderr for every record, also uvicorn's; uvicorn's access log is replaced by the app's `request` records. |
| `ARCVIZ_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` for the app's loggers (`arcviz.*`). |
| `ARCVIZ_TELEMETRY` | `off` | `otlp`: traces and log records through OTLP/HTTP, configured by the standard `OTEL_*` variables (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, …). `azure-monitor`: Azure Monitor / Application Insights through `APPLICATIONINSIGHTS_CONNECTION_STRING` (required). Both need the `telemetry` extra. |
| `ARCVIZ_LOG_PSEUDONYM_KEY` | unset | Secret (at least 16 characters, never logged) of the HMAC that turns the caller's id into the `user` field. Unset: records have no `user` field. |
| `ARCVIZ_LOG_EXCEPTION_DETAILS` | `0` | `1`: JSON and exported records carry full tracebacks with exception messages, which may contain user content (ids from paths, names, e-mail addresses); for debugging only. Default: type, error code and frames. |

**Setup.** `configure_logging(config)` (idempotent, called by `create_app`; it replaces only the handler it
installed itself): in `json` mode the root logger gets one `StreamHandler(stderr)` with `JsonFormatter` and the
`ContextFilter`; in `text` mode a handler is added only when the root logger has none (an embedding server or pytest
keeps its own), with the format `%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s`. The `arcviz`
logger gets the configured level. `main()` passes `log_config=None, access_log=False` to uvicorn in `json` mode so
that uvicorn's records propagate to the root handler and the access log comes from the middleware.

**Record format** (`JsonFormatter`):

```json
{"time": "2026-10-08T12:00:00.123Z", "level": "INFO", "logger": "arcviz.llm", "message": "LLM turn done: 2 rounds, 3 tool calls, 5400 tokens in 4210 ms",
 "request_id": "6f1c…", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "user": "u-1a2b…",
 "event": "llm_turn", "provider": "anthropic", "model": "claude-opus-5-5", "outcome": "done", "rounds": 2, "tool_calls": 3,
 "input_tokens": 5000, "output_tokens": 400, "duration_ms": 4210}
```

`time` (UTC, milliseconds), `level`, `logger`, `message` always; `request_id`, `client_request_id`, `trace_id`,
`user` when the record was written while serving a request; the record's structured fields (`observability.fields`,
only JSON scalars) at top level. Exceptions (`exception_summary`): `exc_type`, `error_code` (12 hex of the SHA-256
over the type and the frames' `file:function`, stable across line changes) and `exc_frames` (the innermost 20
frames as `file:line:function`, the last two path components), never the message; `exc` (the formatted traceback)
only with `ARCVIZ_LOG_EXCEPTION_DETAILS`. uvicorn's records go through the same formatter. Field names of the record
never override the fixed ones.

**Request context.** `RequestContextMiddleware` (pure ASGI, the outermost middleware, so 401 and 413 answers are
covered too):

- **Request id** (ADR-0113): always a new `uuid4().hex`, sent as the only `X-Request-ID` of the response (one the
  app set is replaced). An incoming `X-Request-ID` matching `[A-Za-z0-9._:-]{1,128}` (the Container Apps ingress,
  Envoy, sets one) is kept as `client_request_id`; another is dropped.
- **Unhandled exceptions**: caught here, logged once (the `request` record at ERROR with the exception summary, in
  both formats) and never re-raised, so neither Starlette's error middleware nor uvicorn logs a second, unscrubbed
  traceback. In this order:
    1. The response was complete: nothing is gone; the defect is logged with the status sent, nothing more is sent.
    2. The exception is the disconnect itself (below): the client is gone.
    3. The response had started: the record keeps the started status with `aborted=true` and the middleware returns
       without completing the body. uvicorn then closes the connection, so the client sees a broken stream, not a
       clean end, and notes `ASGI callable returned without completing response.` (one line without a traceback or
       content; a client that is already gone gets none).
    4. Otherwise `500 {"detail": "Internal Server Error", "request_id": …}`.
- **Client gone**: an `http.disconnect` (starlette then raises `ClientDisconnect`) or an `OSError` from `receive` or
  `send` (uvicorn's `ClientDisconnected` is one). The `request` record then has `aborted=true`, the status sent so
  far or `499` (nginx's "client closed request"; never sent) and level INFO, without an exception summary: a closed
  tab is not a defect. Nothing is sent to a client that is gone. Any other exception is the app's defect, also a
  `RuntimeError` of `send` and an exception raised after a disconnect was seen.
- **Trace id**: the trace id of a valid W3C `traceparent` header (32 hex, not all zero), else none; with telemetry
  on, the instrumentation continues the same trace, so log records and spans correlate.
- A `contextvars.ContextVar` holds the request id, trace id and the ASGI scope for the duration of the request;
  starlette copies the context into the thread pool, so records written by routes, services, agents and the LLM
  layer carry it. `ContextFilter` adds the fields to every record (empty strings in `text` mode outside requests).
- **User**: read at emit time from `scope["state"]["arcviz_identity"]` (set by `AuthMiddleware`, inner) as
  `pseudonym(identity.id, key)` = `u-` + 32 hex of the HMAC-SHA256 with `ARCVIZ_LOG_PSEUDONYM_KEY`; without the key
  there is no `user` field (ADR-0112). The user directory keeps its own key (`workspaces.user_key`).
- **`request` record** (`json` mode only; in `text` mode uvicorn's access log stays): at the end of the response,
  `event=request`, `method`, `route` (the route template such as `/api/diagrams/{diagram_id}/chat`, else
  `/static`, `/docs` or `-` for an unmatched path), `status`, `duration_ms`, `aborted` (true when the response
  did not complete); level INFO, DEBUG for `/healthz` and `/readyz` (probes run every few seconds and are not logged
  at the default level), WARNING for 5xx (not for 499), ERROR with an exception. Raw paths and
  query strings are never logged: they contain diagram and project ids (slugs of titles) and user ids.

**Model turns** (`arcviz/llm/base.py`, logger `arcviz.llm`): `run_turn` writes one `llm_turn` record when the turn
ends (in a `finally`, so a closed stream writes `outcome=aborted`): `provider`, `model`, `outcome`
(`done|error|aborted`), `error_kind` (`budget`, `max_rounds`, `refusal`, `max_tokens`, `no_reply`, `exception`) and
`error_type` (the exception's class name) on errors, never the error message, `rounds`, `tool_calls`, every usage
counter (`input_tokens`, `output_tokens`, cache counters when the provider reports them), `duration_ms`.
`complete_text` and `complete_tool` (compaction, reviews) write `llm_call` with `purpose`
(`text`/the tool name), the usage, `duration_ms`, `outcome`. Levels: INFO, WARNING for errors.

**Telemetry** (`start_telemetry(config, app)`, called by `create_app` after the app exists; once per process):

- `azure-monitor`: `configure_azure_monitor(logger_name="arcviz", instrumentation_options={"fastapi": {"enabled":
  False}})` (log records of the `arcviz` loggers only; requests, urllib and other client libraries instrumented by
  the distro; `span_processors=[event_scrubber()]`), then `FastAPIInstrumentor.instrument_app(app,
  excluded_urls=EXCLUDED_URLS, server_request_hook=…, exclude_spans=["receive", "send"],
  meter_provider=NoOpMeterProvider())` (no `http.server.*` metrics: their attributes would carry the host, user agent
  and raw target, and the issue asks for traces only; the probe paths anchored: `^https?://[^/]+/healthz(\?.*)?$`,
  likewise `readyz`, so `/api/diagrams/healthz-x` is traced). The explicit call is needed because `server.py` imported `FastAPI` before the distro
  could patch it.
- `otlp`: a `TracerProvider` (first the `event_scrubber()`, then a batch processor with the OTLP/HTTP exporter) and
  a `LoggerProvider` with the OTLP log exporter (resource from the `OTEL_*` variables), the `LoggingHandler` of
  `opentelemetry-instrumentation-logging` on the `arcviz` logger, the same `instrument_app` call.
- Every export handler on the `arcviz` logger gets the `ContextFilter` and is given a scrubbed copy of each record
  (`protect_export_handlers`, `scrubbed(record)`: the exception summary instead of `exc_info`, unless
  `ARCVIZ_LOG_EXCEPTION_DETAILS`). The original record is never changed, so the local stderr handler, which may run
  after the export handler, keeps the full traceback.
- The `server_request_hook` keeps only `SPAN_ATTRIBUTES` on the server span (`http.method`/`http.request.method`,
  `http.route`, the status code, scheme, protocol version, `http.target`/`url.path`), dropping the client address,
  user agent, host, port and full URL, and sets `http.target`/`url.path` to the path whose id segments are replaced
  by `{id}` (`redact_path`: in `/api/<collection>/<id>/<sub>/<id>…` every second segment after `api`). The SDK has
  no public way to remove an attribute, so the hook edits the span's attribute mapping (tested against the pinned
  SDK). `event_scrubber()` runs when the span ends, after every hook and late `set_attribute`: it keeps only
  `SPAN_ATTRIBUTES`, reduces span events to `exception.type` and the status description to the exception type
  (`"LookupError: no user …"` becomes `"LookupError"`). No request or response headers are captured.
- A missing extra is a configuration problem at start-up (`ARCVIZ_TELEMETRY=otlp needs pip install
  "arcviz-agent[telemetry]".`), as is `azure-monitor` without `APPLICATIONINSIGHTS_CONNECTION_STRING`.

**Privacy rules** (ADR-0112), enforced by tests: no record written by the new code contains a token, an API key, a
cookie, an e-mail address, a display name, a chat message, a model reply, a diagram title, element names or notes,
or a raw request path. Warnings never interpolate an exception's message: compaction, the Git mirror (its output
stays in `git.last_error` of `GET /api/config`), Cosmos DB retries and its Time to Live check and start-up migration
problems log the exception type only; `tests/test_operations_review3.py` fails for a log call in an `except … as exc`
block that names `exc` other than as `type(exc).__name__`. `ARCVIZ_GIT_REMOTE` may carry a token
(`https://user:token@host/…`): `gitmirror.redacted` replaces the user info and query of every URL with `***` in the
log records, `git.last_error` and `git.remote` of `GET /api/config`, including git's own output. Existing operator warnings (migration, repair, user directory) keep naming diagram, project and user ids;
with the `easyauth` preset user ids are Entra object ids; with `oauth2-proxy` the id header may carry an e-mail
address (documented; choose a non-e-mail claim as the id header to avoid it). In `text` mode tracebacks are logged
as before.

### 5. Entra ID for Azure OpenAI (#156)

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_OPENAI_AUTH` | `key` | `key`: `OPENAI_API_KEY` as before. `entra`: a bearer token from `azure-identity`'s `DefaultAzureCredential` (managed identity, workload identity, Azure CLI …); `OPENAI_API_KEY` is ignored. Needs `ARCVIZ_PROVIDER=openai`, `OPENAI_BASE_URL` and the `azure-openai` extra. |
| `ARCVIZ_OPENAI_TOKEN_SCOPE` | `https://cognitiveservices.azure.com/.default` | Scope of the token. |

`DefaultAzureCredential` reads `AZURE_CLIENT_ID` for a user-assigned identity (azure-identity's own variable).

New module `arcviz/llm/entra.py`:

```python
class BearerTokenProvider:
    """A callable returning a valid access token; refreshed `refresh_margin` seconds before it expires."""

    def __init__(
        self,
        credential,
        scope: str,
        *,
        refresh_margin: float = 300.0,
        backoff: float = 30.0,
        refresh_timeout: float = 30.0,
        clock=time.time,
    ): ...
    def __call__(self) -> str: ...


def default_credential() -> Any:  # azure.identity.DefaultAzureCredential(); ConfigError without the extra
    ...
```

`__call__` is thread-safe with a single refresh in flight: it returns the cached token while
`expires_on - refresh_margin > clock()`; otherwise one caller fetches (`credential.get_token(scope)` outside the
lock) while every other caller with a still-valid token gets that token at once, and callers without one wait for
the fetch. The fetch runs in a worker thread with a deadline of `refresh_timeout` (30 s): a credential that hangs
(an unreachable identity endpoint) is abandoned then (its late result is dropped), the attempt counts as a failure
(`TimeoutError`) and the slot is free again; waiters wait at most until that deadline. Every failed refresh blocks
further attempts for
`backoff` (30 s): with a still-valid cached token it logs one warning (exception type only) and returns that token;
without one the error propagates to the refreshing caller; every caller that waited for that round, and every
caller within the backoff at once instead of a new fetch, gets a new `TokenRefreshError` naming the exception type,
raised `from` the failure (the shared exception object is never raised twice) (the chat stream reports it as an
`error` event, like any provider error). A token without a value or expiry is an error too. The in-flight flag is
always cleared and the waiters woken, also on `BaseException` (a cancelled thread), so no caller waits for a
refresh that is no longer running. The token is never logged.

`OpenAICompatibleProvider(api_key=...)` accepts `str | Callable[[], str]`; `create_provider` passes
`BearerTokenProvider(default_credential(), scope)` with `entra`. The OpenAI SDK (≥ 1.106) calls a callable key before
every request and sends `Authorization: Bearer <token>`. No change for the Anthropic provider.

Deployment guide: role *Cognitive Services OpenAI User* for the app's identity on the Azure OpenAI account,
`OPENAI_BASE_URL=https://<account>.openai.azure.com/openai/v1/`, `ARCVIZ_OPENAI_AUTH=entra`, no key secret, and
`disableLocalAuth` on the account once it works.

### 6. The documentation only in the project (#188)

Added during the milestone by the owner (ADR-0116): no GitHub Pages; every artifact contains the bundled site.

- Removed: `.github/workflows/docs.yml`, the release job *Publish versioned docs*, the `docs-publish` dependency
  group (mike, its Git source) and the version selector in `zensical.toml`, whose `site_url` goes too. The
  `gh-pages` branch stays until the owner deletes it.
- `hatch_build.py` (`[tool.hatch.build.hooks.custom]`): a non-editable build (wheel, sdist) without
  `arcviz/docs_site/index.html` fails with the instructions (`make docs-bundle`); editable installs are exempt. The
  Dockerfile copies the hook next to `pyproject.toml`.
- `scripts/check_wheel_docs.py <wheel>…`: the wheel contains `arcviz/docs_site/index.html` and the arc42 index.
  The release runs it after `uv build`; CI runs a step *Wheel with the documentation site* (docs group, bundle,
  `uv build --wheel`, check) in the `docker` scope, which `hatch_build.py` and the script join.
- `make run` builds the bundle first when it is missing; `DOCS_MISSING` names `make docs-bundle` and the Markdown
  sources in the repository instead of the Pages URL. README and CONTRIBUTING point to `/docs` and `docs/`.

## Testing

- `tests/test_envconfig.py`: every rule of `Env` (unset, blank, malformed, below minimum, above maximum, choices),
  problems collected and the default returned, `ConfigError` text and `problems`.
- `tests/test_startup_config.py`: one parametrised case per numeric variable of the server (malformed → the message
  names it and the value); several problems in different subsystems reported together; `main()` prints them
  without a traceback and exits 2; the provider SDK check (a missing module simulated through `find_spec`); an
  injected provider skips the provider checks.
- `tests/test_providers.py` / `tests/test_entra.py`: `BearerTokenProvider` with a fake credential and a fake clock
  (cached, refreshed before expiry, failure with a valid token keeps it, failure without one raises, one fetch
  under concurrency, a slow failing refresh blocks no holder of a valid token, one attempt and one warning per
  backoff window); the OpenAI provider built by `create_provider` with `entra` sends `Authorization: Bearer t1`,
  then `Bearer t2` after the clock passed the refresh point (httpx `MockTransport`, no network); configuration
  problems of `entra`.
- `tests/test_observability.py`: JSON format of records (fixed fields, structured fields, exceptions); request id
  generated, taken from a valid header, refused when invalid, echoed; trace id from `traceparent`; `request`
  records with route templates and levels; `llm_turn`/`llm_call` records for done, error and aborted turns with
  usage; text mode unchanged for uvicorn; `redact_path`; telemetry setup with the real OpenTelemetry SDK and
  in-memory exporters (`otlp` path wiring, span attributes redacted) and with a fake `configure_azure_monitor`;
  missing extra and missing connection string as start-up problems.
- `tests/test_observability_hardening.py`: exceptions as type, code and frames (stable code, opt-in details,
  uvicorn's records too); an unhandled exception answers 500 with the request id and is logged exactly once; spans
  carry only allowed attributes (no user agent, host or client address), span events only `exception.type`; the
  probe exclusion is anchored; the HMAC pseudonym and its absence without a key; the server's own request id with
  `client_request_id`; `envconfig.installed`.
- **Privacy test**: a token-mode app and a proxy-mode app with JSON logs at DEBUG; requests carry a token, an API
  key in the environment, an e-mail header, a display name; the session creates a diagram with a distinctive title,
  nodes, a note, chats with `ScriptedProvider`, shares by e-mail and revokes, and hits a defect whose message
  contains the title and an e-mail address; then no captured line contains any of those strings (nor the pseudonym
  key), and every line is valid JSON.
- `tests/test_docs_in_every_build.py`: the hook refuses a missing site and is configured (editable exempt, in the
  image), the wheel check, CI and the release run it, no Pages workflow, mike group or `site_url`, `make run`.
- Build: `uv lock --check` in CI through `uv sync --locked`; the Docker build and smoke test (CI scope `docker`)
  exercises the locked install with every extra; `tests/test_ci_scope.py` for `uv.lock`.

## Migration and compatibility

- **Installing from the wheel without extras** now starts only with a provider SDK installed:
  `pip install "arcviz-agent[anthropic]"` (or `[openai]`). The error says so. The Docker image is unchanged in
  behaviour (it installs every runtime extra).
- **Stricter values**: `ARCVIZ_MAX_ROUNDS=0`, `ARCVIZ_CHAT_KEEP=0`, negative values of the agent variables and an
  out-of-range `ARCVIZ_PORT` used to start (with nonsensical behaviour) and now stop the start with a message.
- **Lazy provider errors that are now start-up errors**: an unknown `ARCVIZ_PROVIDER` and a malformed
  `ARCVIZ_LLM_TIMEOUT`/`ARCVIZ_LLM_MAX_RETRIES` used to start the app and answer 503 on the first chat; now they stop
  the start (only when the app creates its provider itself).
- Logs in the default `text` mode: uvicorn's lines are unchanged; the app's own INFO records (turn summaries,
  migration notes) now appear where a root handler was missing before.
- `requirements-docs.txt` is gone: `make install` (or `uv sync --group docs`) installs the docs toolchain.

## Open questions

None left; the choices are recorded under Decisions.

## Decisions

1. One collector over all subsystems, values parsed by one `Env` helper; malformed provider configuration and a
   missing SDK stop the start; a missing key does not (ADR-0106, ADR-0107).
2. The image installs every runtime extra; `ARCVIZ_EXTRAS` slims it (ADR-0108).
3. `uv.lock` everywhere, docs toolchain in dependency groups, hashed requirements attached to releases (ADR-0109);
   base images pinned by version and digest, actions by commit SHA, Dependabot monthly and grouped (ADR-0110).
4. JSON logs with an in-house formatter (no `structlog`/`python-json-logger` dependency) and opt-in; `text` stays the
   default (ADR-0111).
5. The user in log records is a keyed HMAC of the id (`ARCVIZ_LOG_PSEUDONYM_KEY`, omitted without it); requests are
   logged by route template; exceptions without messages; spans by attribute allow-list (ADR-0112). Request ids are
   always generated, a safe `X-Request-ID` is logged as `client_request_id`; the trace id comes from `traceparent`
   (ADR-0113).
6. OpenTelemetry and Azure Monitor through one optional `telemetry` extra, chosen by `ARCVIZ_TELEMETRY` (ADR-0114).
7. Entra tokens through an in-house `BearerTokenProvider` over any `azure-identity` credential, so the refresh logic
   is testable without Azure (ADR-0115).
