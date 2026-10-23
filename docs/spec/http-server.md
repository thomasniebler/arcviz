# HTTP server

Source: `arcviz/server.py`. Tests: `tests/test_server.py`. Endpoint-by-endpoint reference:
[HTTP API](../reference/api.md).

## Application factory

```python
create_app(
    store: RepositoryPort | None = None, provider: LLMProvider | None = None, docs_dir: Path | None = None
) -> FastAPI
```

- `docs_dir` is the built documentation site served under `/docs` (default `DOCS_DIR`,
  `arcviz/docs_site`; see [Documentation](#documentation)).
- FastAPI's interactive API documentation is at `/api/docs` (Swagger UI) and `/api/redoc`
  (`docs_url`/`redoc_url`); `/openapi.json` is unchanged.

- `store` defaults to `create_store()` (`arcviz/store/factory.py`): the backend named by `ARCVIZ_STORE`
  (`file`, `sqlite`, `cosmos`), with a `GitMirror` attached when `ARCVIZ_GIT_DIR` is set. The factory
  hands it to one `ServiceRoot(store, hub, admin)` (`app.state.services`; see
  [Persistence: application services](persistence.md#application-services)). At start-up the root runs the
  migration to personal workspaces (`root.migrate(admin)`, ADR-0102; best effort, warnings name stranded projects
  and the projects that stay hidden while `ARCVIZ_ADMIN_USER` is missing in proxy mode). Every route that reads or writes data takes `svc: Svc`, a FastAPI
  dependency (`services_of`) that returns `root.for_actor(identity, per_request=True)` (its guard reads each project
  once per request: viewer checks reuse it, higher checks read again, writes drop it; #166, ADR-0121), or, for the
  chat turns and the live streams, `svc: TurnSvc` (`turn_services_of`: the same without that cache): the `DiagramService`, `ProjectService`,
  `ModelService`, `ProjectExchangeService`, `WorkspaceService` and `UserService` bound to the caller, so the access
  policy checks every call ([personal workspaces](../architecture/specs/personal-workspaces.md)). Routes call
  only these services, never the repository, never build a service and never use the system context (checked by
  `tests/test_layering.py`). A global dependency (`record_caller`) records every authenticated caller in the user
  directory and creates their personal workspace on the first request (`root.signed_in`, cached per process; neither
  ever fails the request). The workspace routes are `GET /api/workspace` (`svc.workspaces.mine()`); sharing is
  `GET`/`POST /api/projects/{pid}/access` and `DELETE /api/projects/{pid}/access/{user}` (`svc.projects.access`,
  `share`, `revoke`, rendered with the directory's names by `access_view`: shares as id and name, `added` as typed plus
name); `project_view` dumps the project without `roles` (and `default_project`), so no project response carries the
share list; `GET /api/projects` adds the owner's
  name and e-mail to every summary.
- `provider` may be injected (tests, the e2e server). Otherwise `get_provider()` creates it lazily
  with `create_provider()` on the first chat request or readiness probe and caches it in
  `app.state.provider`; a failure is stored in `app.state.provider_error` and surfaced as HTTP 503
  and in `/api/config`.
- `ARCVIZ_KINDS_FILE`: when set, `use_kinds(load_kind_catalog(path))`, otherwise
  `use_kinds(BUILTIN_KINDS)`, before anything else; a `KindConfigError` propagates, so the app
  is not created with an invalid vocabulary.
- Configuration (ADR-0106): `create_app` reads every subsystem's variables through a `StartupConfig` collector:
  `limits.LimitConfig.from_env()`, `auth.AuthConfig.from_env()`, `live.LiveConfig.from_env()`,
  `AgentSettings.from_env()` and, unless a provider was injected, `ProviderConfig.from_env()`. Each reads with
  `envconfig.Env`, which records a problem (variable, expected format, value) and returns the default; a
  `ConfigError` (a `ValueError`) of one subsystem, or another `ValueError` (the authentication rules), is
  collected, and after the last one a single `ConfigError` listing every problem stops the start.
- Logs (spec `docs/architecture/specs/operations.md` §4): `observability.LogConfig.from_env()` (`ARCVIZ_LOG_FORMAT`,
  `ARCVIZ_LOG_LEVEL`, `ARCVIZ_TELEMETRY`) in the same collector; after the check `configure_logging` installs the
  JSON or text handler with `ContextFilter` and `start_telemetry` the process-wide OpenTelemetry providers (once).
  `observability.RequestContextMiddleware` is the **outermost** middleware: a new request id (`uuid4().hex`, the only
  `X-Request-ID` of the response; a safe incoming `X-Request-ID` matching `[A-Za-z0-9._:-]{1,128}` becomes
  `client_request_id`), trace id of a valid `traceparent`, a `ContextVar` with both and the ASGI scope for the
  duration of the request (the user is read from `scope["state"]["arcviz_identity"]` as the HMAC pseudonym with
  `ARCVIZ_LOG_PSEUDONYM_KEY` when a record is written, none without the key); in JSON mode one `request` record per
  request (route template from `scope["route"]`, `/static`, `/docs` or `-`; `aborted` when the response did not
  complete; DEBUG for the probes, WARNING for 5xx).
  An unhandled exception is caught there, logged once at ERROR (both formats; in JSON as type, error code and frames
  without the message unless `ARCVIZ_LOG_EXCEPTION_DETAILS`) and answered with
  `500 {"detail": "Internal Server Error", "request_id": …}` when nothing was sent yet; it is never re-raised. After
  a started response the record keeps that status with `aborted=true` and the middleware returns without completing
  the body, so uvicorn closes the connection (one line "ASGI callable returned without completing response.", no
  traceback) instead of the stream ending cleanly; after a complete response nothing is sent. Only the disconnect
  itself (`http.disconnect` and starlette's `ClientDisconnect`, an `OSError` from `receive` or `send`) is a client
  gone: INFO with `aborted=true` and the status sent or `499`, without an exception summary; any other exception,
  a `RuntimeError` of `send` included, is a defect at ERROR. However the request ends, the middleware records
  `telemetry.record_request(route template, method, status, seconds)` (not for the probes; a no-op without telemetry;
  a method outside the standard ones counts as `_OTHER`, #187). `observability.instrument(app)`
  adds the FastAPI instrumentation with `ARCVIZ_TELEMETRY`; `create_app` hands the live hub to
  `telemetry.observe_live_streams` (the `arcviz.live.streams` gauge). `app.state.log_config` keeps the configuration for
  `main()`, which passes `log_config=None, access_log=False` to uvicorn in JSON mode.
- `auth.AuthConfig.from_env()` reads the [authentication settings](../reference/configuration.md#authentication)
  (a `ValueError` stops the start-up) and `auth.AuthMiddleware` is installed as the outermost
  middleware: with `ARCVIZ_AUTH=token` or `proxy` it identifies the request (bearer token or session
  cookie compared in constant time, or the proxy's identity headers) and stores the caller's `Identity` in
  `scope["state"]["arcviz_identity"]` (`off` and `token` mode: the admin identity) and, in proxy mode only, its id
  in `scope["state"]["arcviz_user"]` (in token mode everyone is the admin, so the rate limiter keys on the client
  address); without one it answers 401, or in token mode 303 to `/login?next=…` for a browser navigation. `/healthz`, `/readyz`, `/login`, `/logout` (and `/docs` with `ARCVIZ_AUTH_DOCS=public`)
  are public. `GET /login` serves `auth.login_page` (token mode) or redirects; `POST /login` checks the
  token and sets the cookie `arcviz_session=<issued>.<HMAC-SHA256(token, issued)>` (HttpOnly,
  SameSite=Strict, 7 days, `Secure` per `ARCVIZ_COOKIE_SECURE`); `POST /logout` deletes it. Design:
  [Authentication](../architecture/specs/authentication.md).
- `limits.LimitConfig.from_env()` reads the [request limits](../reference/configuration.md#request-limits)
  (a `ValueError` stops the start-up) and `limits.LimitsMiddleware` is installed: it answers 429 for a
  request whose rate class (`chat`, `review`, `ops`; `limits.rate_class`) has no token left for the
  client, and 413 for a body over the limit (checked from `Content-Length`, else while buffering the
  body, which it then replays to the app). The chat route answers 413 for a message over
  `max_chat_chars`, the proposal route for more than `MAX_PROPOSAL_OPERATIONS` (500) operations. Design:
  [Request size and rate limits](../architecture/specs/request-limits.md).
- `AgentSettings` (`ARCVIZ_MAX_ROUNDS` ≥ 1, default 12; `ARCVIZ_CONTEXT_BUDGET`, 0 = off; `ARCVIZ_HISTORY_TURNS`;
  `ARCVIZ_CHAT_COMPACT_AFTER`; `ARCVIZ_CHAT_KEEP` ≥ 1; `ARCVIZ_TURN_TOKEN_BUDGET`, default 0 = no cap) is passed to
  the `DiagramAgent`, which is also created lazily (`app.state.agent`, likewise `app.state.project_agent`); each
  request uses `agent.bound(svc.diagrams, svc.models)` (the project agent also `svc.projects`), a copy on the
  caller's services that shares the background compaction. The chat routes check the editor role before the
  event stream starts, so a viewer gets a plain 403.
- `main()` runs uvicorn on `ARCVIZ_HOST`/`ARCVIZ_PORT` (defaults `127.0.0.1:8000`, port 1–65535); it is the
  `arcviz` console script. Its own problems and those of `create_app` are printed together to stderr without a
  traceback, exit status 2.

- Agent access ([specification](agents.md)): `AgentAuthConfig.from_env()` reads the MCP variables; with `ARCVIZ_MCP=1`
  `create_app` checks the SDK (`mcp_server.sdk_missing`) and PyJWT (`agentauth.oauth_missing`), builds `AgentGate` for
  `AuthMiddleware(agents=…)` and `mcp_server.build_mcp(root, path, version)`, inserts `Route(path, endpoint)` first in the router
  (exactly the path, no redirect to a slash) and runs the endpoint's lifespan inside the app's. `LimitsMiddleware(agent_path=…)`
  gives `POST` on that path the rate class `agent`. The routes `/api/tokens` and the proposal routes are ordinary routes (`svc: Svc`).

## Language

`i18n.LanguageMiddleware` (pure ASGI, outside the auth and limit middleware, inside the request-context middleware) sets
the request's language from `Accept-Language` (`i18n.parse_accept_language`: q-values honoured, `de`/`en`, German
otherwise) into a context variable that `i18n._()` reads; sync routes and the chat stream run in worker threads that
inherit it. Server texts are German source strings wrapped in `_("…", name=value)`; the English catalog is
`arcviz/locales/en/*.json` (German text → English text, same placeholders), checked by `tests/test_i18n.py`. The prompts get
a reply-language line for English (`prompts.language_line`), the review and analysis calls name the language through
`i18n.language_name()`. Spec: [Internationalization](../architecture/specs/internationalization.md).

## Request models

`CreateDiagramRequest(title, id=None, template="empty", type=None, project=None, section=None)`,
`ChatRequest(message, min_length=1, focus=None, mode: Literal["apply", "propose"] = "apply")`,
`ProposalRequest(operations, base, result, label=None)`,
`SnapshotRequest(label="Manual snapshot")`, `OperationRequest(op, args={}, label=None)`,
`ImportRequest(document: Any, id=None, title=None, project=None, section=None)`; for projects
`CreateProjectRequest(title, id=None, description="")`, `UpdateProjectRequest(title=None,
description=None)`, `ProjectOperationRequest(op, args={})`, `CreateSectionRequest(title, parent=None,
position=None, id=None)`, `UpdateSectionRequest(title=None, parent=None, position=None)` (which fields
were sent decides between rename and move: `model_fields_set`), `MoveDiagramRequest(project=None,
section=None, position=None)`.

## Projects

Routes under `/api/projects` (reference: [HTTP API: projects](../reference/api.md#projects), service:
[Projects](projects.md#projectservice)) are thin wrappers around `ProjectService`; a project view is
`{"project", "tree", "diagrams"}` from `ProjectService.view`. `project_errors()` maps
`OperationError`/`ProjectError` to 400, `ProjectNotEmpty` to 409, `ConflictError` to the 409 handler
and any other `StoreError` (unknown project) to 404. `POST /api/projects` checks an explicit id with
`check_id` (400); the project's id is `<id or title slug>-<10 random base32 characters>` (ADR-0131): the requested id
is only a prefix, so the answer never depends on whether anybody holds it (#167).
`GET /api/projects` repairs nothing (ADR-0088). `PATCH /api/projects/{pid}` applies `rename_project`
and `set_description` for the fields given. The section routes apply `add_section`,
`rename_section`/`move_section` (a move without `parent` keeps the current parent) and
`remove_section`. The move route answers 404 when the diagram is not a member of `{pid}`, keeps the
current section for a move within the project without `section`, refuses a negative position (400)
and calls `ProjectService.place`. `GET /api/projects/{pid}/export` answers the bundle of
`ProjectExchangeService.export` as an attachment `<pid>.arcviz-project.json`; `POST
/api/projects/import` (`ImportProjectRequest(bundle, title=None)`, declared before the `{pid}` routes,
import body limit) answers 400 `Invalid bundle: …` for an `ExchangeError` and 201 with the new
project's view otherwise.

Creating or importing a diagram passes `project`/`section` to `DiagramService.create`/`import_new`,
which check the target before creating anything; `placement_errors()` turns an unknown project
(`ProjectNotFound`) into 404 and an unknown section (`ProjectError`) into 400.

The shared project model ([Projects: shared model](projects.md#shared-model)): `create_app` builds
`ModelService(projects, diagrams)` (`app.state.models`) and passes it to both agents. The element routes
(`GET …/model`, `POST/PATCH/DELETE …/elements`, `POST …/model/sync`) and the section `element` field run under
`project_errors()`; `POST /api/diagrams/{id}/link`, `/unlink` and `/promote` (`LinkRequest`, `UnlinkRequest`,
`PromoteRequest`) answer 404 for an unknown diagram, then map like the project routes and answer the diagram
detail with the touched views highlighted plus `result`. `POST /api/diagrams/{id}/ops` answers 400 for an
operation in `operations.INTERNAL_OPERATIONS`. `/api/config` adds `project_model` and each type's `linkable`.

## Comparison

`GET /api/diagrams/{id}/compare?base=&target=&focus=` loads version `base` and version `target` (or the
latest state when `target` is omitted; a missing version is a 404), runs `diff.compare` and
`diff.merged`, validates `focus` against the merged diagram with `check_focus`, and answers
`{base, target, diff, focus}` plus the render payload of the type (`render.payload(view, focus, marks, edge_marks)`:
Mermaid for deployment and activity diagrams, the own SVG for sequence diagrams, #108). Every type is supported.
Nothing is written.

## Architecture review

`GET /api/diagrams/{id}/checks` loads the latest diagram and answers `{diagram_type, rules, findings}`
from `rules.diagram_type`, `rules.rules_for` and `rules.run_checks` (see [Architecture review](review.md)).
Nothing is written; 404 for an unknown diagram.

`POST /api/diagrams/{id}/review` loads the latest diagram, gets the provider like the chat
(`get_provider()`, 503 when it cannot be created) and answers `review.review(provider, diagram)`. A
failed model call is a 200 whose `llm.ok` is false; nothing is written. It is a plain `def`
endpoint, so the blocking model call runs in FastAPI's thread pool.

## Import and export

`GET /api/diagrams/{id}/export?format=json|plantuml&version=` and `POST /api/diagrams/import` are thin
wrappers around `arcviz/exchange.py` (see [Import and export](exchange.md)). Export loads the latest
state or `DiagramService.version` and answers with a `JSONResponse` or `PlainTextResponse` carrying
`Content-Disposition: attachment; filename="<id>[-v<n>].arcviz.json|.puml"`; an unknown format is a
400. Import runs `exchange.parse_document` (`ExchangeError` → 400 `Invalid document: …`), validates an
explicit id with `check_id` (400) or derives one (`slugify(document id)[:60]`, `diagram` if the id has
no letters or digits), sets `updated_at` to now and persists with
`DiagramService.import_new(diagram, title or document title, id)`: a new id `<id>-<10 random base32 characters>`, a
fresh suffix if a candidate is taken (ADR-0131); a generated suffix at the end of `<id>` is replaced, not extended
(`ports.id_base`, #200, ADR-0150). `POST /api/diagrams` likewise (prefix: the `id`, else the title's
slug; an invalid one is 400); the answer is the detail of the diagram under its new id, which clients must read from
the response. The route is declared before `GET /api/diagrams/{diagram_id}`.

## Focus query parameter

`Focus = Annotated[str | None, Query(...)]` is accepted by every endpoint that returns a render
(`GET /api/diagrams/{id}`, `/mermaid`, `/ops`, `/versions`, `/versions/{n}`, `/restore`,
`/undo`, `/redo`, `/render`). `check_focus()` turns a focus that no longer exists (or one the diagram's renderer cannot show) into `None` so the UI falls back to
the whole diagram; only the bare `/mermaid` endpoint answers 404 for an unknown focus. The chat
body carries `focus` as a field instead, because the agent renders the live updates.

## Detail payload

`detail(svc, diagram_id, highlight=None, focus=None)` is the common response body: `diagram`, `revision` (the latest
revision: the ETag, from `DiagramState.revision`), `access`
(`{"role", "project", "workspace"}`: the caller's role on the diagram's project, from `DiagramService.access`), the
render payload of [`render.payload`](diagram-types.md#rendering) (`renderer`, `mermaid` or `svg` with
highlight and focus, `mermaid_ids`: node id → Mermaid id, for click handling in the UI), `focus`, `breadcrumb`, `versions`, `undo_target`, `redo_target`, `chat`. Mutating endpoints return it
merged with their own fields (`result`, `version`).

## Config

`GET /api/config` reports provider name and model, `provider_ready`/`provider_error`, the running `version` and its
release highlights `whats_new` (`{version, items: [{emoji, text}]}` or `null`, [spec](../architecture/specs/whats-new.md)), the template
names, the Git mirror status (`git`), the kind sets of the active catalogue, built-in plus configured
(`node_kinds`, `container_kinds`, `edge_kinds`), and the layout `directions`. The UI uses the kind sets
for the inspector's selectors and to tell containers from leaves, and `directions` for the canvas
direction selector. The top-level kind lists are the deployment catalogue (compatibility);
`diagram_types` holds per type `uml`, `renderer`, `node_kinds`, `container_kinds`, `edge_kinds`,
`operations`, `templates`, `features` (`focus`, `direction`, `compare`, `plantuml`, `edge_direction`), the typed
fields of the type (`node_fields`, `edge_fields`: by kind a list of `{name, list, max_items, max_length}`, empty
without a [graph profile](../architecture/specs/uml-graph-types.md)) and type-specific enumerations; `template_types` maps template → type (`null`: any). See [Diagram types](diagram-types.md).

## Error mapping

| Condition | Status |
|---|---|
| Unknown diagram / version (`StoreError` on load) | 404 |
| Unknown template | 400 |
| Unknown project (`ProjectNotFound`) / unknown section or invalid project operation (`ProjectError`) | 404 / 400 |
| Deleting a project that still has diagrams (`ProjectNotEmpty`) | 409 |
| Requested diagram or project id held by anybody (create, import) | no error and no difference: every new id is `<id>-<random suffix>` (ADR-0131) |
| `OperationError` from `/ops` | 400 |
| Nothing to undo / nothing to redo | 409 |
| `RevisionMismatch` (a `ConflictError`): `If-Match` named another revision, or a concurrent write came in between (handler; body `{"detail", "revision"}`) | 412 |
| Live stream caps (`StreamLimit`: user / process or shutting down) | 429 / 503 with `Retry-After: 30` |
| Presence update without an open stream of the caller (`UnknownSession`) | 409 |
| `ConflictError`: a mutation (`/ops`, snapshot, restore, undo, redo, proposal confirm) kept conflicting with concurrent writes for 5 attempts (an app-level exception handler) | 409 |
| The diagram was deleted while a mutation was being applied | 404 |
| `ProposalConflict` from `/proposal/apply` (stale base, failing replay, result mismatch) | 409 |
| No identity in token or proxy mode | 401 (`WWW-Authenticate: Bearer` in token mode), or 303 to `/login` for a browser navigation in token mode |
| The caller may not see the project or diagram, or names a workspace that is not their own (`ProjectNotFound`, `DiagramNotFound`, `WorkspaceNotFound`: the store's not-found message) | 404 |
| The caller's role is too low (`Forbidden`, an app-level exception handler) | 403 |
| Invalid sharing input (`ProjectError`): unknown or ambiguous user, the owner, a role other than viewer/editor | 400 |
| Body, chat message or proposal over its limit | 413 |
| Rate limit of the request's class exhausted (`Retry-After`) | 429 |
| Provider cannot be created | 503 |
| Pydantic validation of the body | 422 (FastAPI default) |

## Proposals

`POST /api/diagrams/{id}/chat` passes `propose=req.mode == "propose"` to `agent.chat`.
`POST /api/diagrams/{id}/proposal/apply` and `/proposal/discard` call `proposal.apply_proposal` /
`proposal.discard_proposal` (`arcviz/proposal.py`, no provider needed) and answer with the detail
payload (apply: plus `version` and `changed`, highlighted). Design:
[Propose-then-confirm](../architecture/specs/propose-confirm.md), ADR-0015.

## Chat streaming

`POST /api/diagrams/{id}/chat` and `POST /api/projects/{id}/chat` are `async` routes. Their checks before the stream
(the diagram exists, the caller is an editor, the bound agent) run in one `run_in_threadpool` call, so no store call
blocks the event loop (#112). The response is a `TurnResponse`: it iterates the synchronous generator `agent.chat()`
with `starlette.concurrency.iterate_in_threadpool`, so the blocking provider calls do not stall the event loop either.
Each event is serialised as `data: <json>\n\n` (`ensure_ascii=False`, `default=str`). Response headers:
`Cache-Control: no-cache`, `X-Accel-Buffering: no` (disables proxy buffering).

However the response ends (completed, the client disconnected, a send failed), `TurnResponse` closes the generator on a
worker thread, shielded from cancellation, and waits for it before the request ends; the events generator holds the
agent's generator in `contextlib.closing`, so the agent's `finally` (automatic version, transcript entry with
`Turn interrupted …`) runs at once, never on the event loop and never only at garbage collection
([hardening](../architecture/specs/hardening.md), ADR-0126).

## ETag, If-Match and live updates

Design: [Collaboration](../architecture/specs/collaboration.md) (#86, #171, ADR-0094..0098).

- `if_match(request)` (the `IfMatch` dependency) parses `If-Match` into a tuple of revisions: `"rev"` or a bare
  token, every entry of a list; a weak `W/"rev"` is kept with its prefix, so it never matches (strong comparison);
  `*` anywhere or empty → `None`. The stream routes answer with `LiveResponse`, a `StreamingResponse` that
  unsubscribes however it ends, also when the client was gone before the body started; the lifespan re-opens the
  hub at every start (`LiveHub.open`). The routes `/ops`, `/link`, `/unlink`, `/versions`, `/versions/{n}/restore`, `/undo`,
  `/redo` pass it as `expected` to `DiagramService`/`ModelService`, which pass it to the store's `update(…,
  expected=)` (one attempt, `RevisionMismatch` → 412).
- `GET /api/diagrams/{id}` sets `ETag` from the detail; `/render`, `/mermaid` and `/export` (no `version`) read
  through `DiagramService.read` and set it too.
- `create_app` builds `LiveConfig.from_env()` and `LiveHub(config, limits.max_streams, limits.max_streams_per_user)`,
  hands the hub to `ServiceRoot(store, hub)` and keeps it in `app.state.hub`. `GET /api/diagrams/{id}/live` and
  `GET /api/projects/{pid}/live` are async routes: `svc.live.open_diagram`/`open_project(id, session, loop)` in the
  threadpool (the access check builds the watch: 404; `StreamLimit` → 429/503), then `StreamingResponse` over
  `live.run_stream(hub, sub, svc.live.recheck)` (`None` → `: keep-alive`), headers `Cache-Control: no-cache`,
  `X-Accel-Buffering: no`. `POST /api/diagrams/{id}/presence` calls `svc.live.select` (404 / 409 / 400 / 204).
  `run_stream` re-checks the stream's newest watch and stores the result with `hub.update_watch(sub, checked,
  based_on=<watch_version at the start>)`; a refused update (the watch changed meanwhile, e.g. a selection re-pointed
  a moved diagram's stream) drops that result and re-checks at once (#176, ADR-0105). A change the stream did not
  report before its `bye` (written after its last re-check, or cut short by the end of its lifetime) is in the next
  `hello`'s `revision`, which the client compares with its own (#213; [collaboration](../architecture/specs/collaboration.md)).
  A delete is always reported as `gone` (#218): the re-check of an event gets the full `shared_wait`, also past the
  lifetime (the lifetime only cuts a poll), and events still pending when the lifetime ends get one last re-check
  before `bye expired`. Should a stream still end with `bye` (a failed re-check), the reconnect's 404 tells the
  client the same (frontend: *Live updates*).
- Both chat routes run the turn inside `svc.live.chatting(...)` (presence).
- The app's `lifespan` calls `close_on_shutdown_signals(hub.close)` at start-up: SIGINT/SIGTERM first close the hub,
  then run the handler uvicorn installed, so open streams end before uvicorn drains connections, however it was
  started (ADR-0098); the lifespan shutdown closes the hub again. `main()` is `uvicorn.run(create_app(), …)`.
- The lifespan also starts this process's writer heartbeat (`app.state.heartbeat`, #189, ADR-0133) on a worker thread
  and closes it at shutdown (it writes `stopped` once no placement is in flight); see
  [Projects](projects.md#pending-tokens-of-writers-that-died).

## Static files

`GET /` serves `arcviz/static/index.html`; `/static` mounts the directory. There is no build
step; the files are shipped inside the wheel and the Docker image.

## Documentation

Design: [Documentation served by the app](../architecture/specs/docs-in-app.md) (ADR-0042, ADR-0043).
`create_app` checks once whether `docs_dir/index.html` exists:

- **Built:** `/docs` mounts `DocsFiles`, a `StaticFiles(html=True)` subclass that adds
  `Cache-Control: no-cache` to every response (in `get_response`, so also to the 404 page and redirects) (revalidated through `ETag`/`Last-Modified`).
  Directory URLs serve `index.html`; `/docs` and other directory URLs without a trailing slash redirect;
  unknown paths serve the site's `404.html` with status 404. `StaticFiles` resolves every path with
  `realpath` and refuses anything outside the directory, including symlinks pointing out of it.
- **Not built** (a development checkout before `make docs-bundle`): `GET /docs` and
  `GET /docs/{path}` answer 404 `text/plain` (`DOCS_MISSING`: how to build it, and where the Markdown
  sources are). `make run` builds the site before starting when it is missing; every wheel, sdist and image
  contains it (ADR-0116).

Neither variant appears in the OpenAPI schema. The site's pages load Mermaid from
`/static/vendor/mermaid.min.js` (relative URL), so they need no network access.

## Health and readiness

Design: [Health and readiness probes](../architecture/specs/health-probes.md).

- `GET /healthz` (liveness) returns 200 `{"status": "ok"}` and touches neither the store
  nor the provider; 503 `{"status": "fenced", "error": …}` once the writer heartbeat was fenced by another replica
  (`Heartbeat.ready()`, #189), so the orchestrator restarts the process. The Docker `HEALTHCHECK` uses it.
- `GET /readyz` (readiness) runs every check and returns 200 `{"status": "ready", "checks": …}`
  when all pass, otherwise 503 `{"status": "unavailable", "checks": …}`. Each check is
  `{"ok": true}` or `{"ok": false, "error": "<message>"}`:
    - `store`: `store.ping()` (see [Persistence](persistence.md#repository)); any exception fails it.
    - `provider`: `get_provider()` (creating and caching the provider if needed, which makes no
      network request), then `provider.config_error()` (see [LLM layer](llm-layer.md#provider-contract)).
      A creation failure reports `app.state.provider_error`.
    - `writer`: `Heartbeat.ready()` (#189, ADR-0133): fails once another replica fenced this process (declared it dead
      after its heartbeat stood still for a lease); such a process issues no locator tokens, so it cannot place
      diagrams, and must be restarted.

  No check calls the model. Both routes are synchronous, so FastAPI runs them in the thread
  pool and a slow store probe does not block the event loop.

The CI Docker smoke test still polls `GET /api/config`.
