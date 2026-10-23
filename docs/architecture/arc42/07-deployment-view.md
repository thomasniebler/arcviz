# 7. Deployment view

## Reference deployment: Azure Container Apps

```mermaid
--8<-- "docs/architecture/diagrams/deployment.mmd"
```

Source: `docs/architecture/diagrams/deployment.arcviz.json` (built-in deployment kinds). Step by step:
[Deploying to Azure Container Apps](../../deploy-azure.md).

| Element | Mapping |
|---|---|
| Image `ghcr.io/thomasniebler/arcviz:<version>` | Built and pushed by `release-please.yml` on every release. Multi-stage: a docs stage builds `arcviz/docs_site`, and the runtime stage is `python:3.14-slim` plus `git`, with an unprivileged user (uid 10001), volume `/data` and a `HEALTHCHECK` on `/healthz` ([Build, CI and release](../../spec/build-and-release.md#container)). |
| Ingress with Easy Auth | Authentication at the ingress (Entra ID); the app can additionally refuse requests without Easy Auth's user header (`ARCVIZ_AUTH=proxy`), or use its own shared token (`ARCVIZ_AUTH=token`) instead. Without either, anyone who can reach the URL can read, change and delete every diagram and spend the LLM budget. |
| One replica | Required with the `file` and `sqlite` stores (single writer per data directory). |
| Azure Files at `/data` | `file` store; durable across restarts. SMB file semantics (no `fsync` in the file store, #117). |
| Cosmos DB (alternative) | `ARCVIZ_STORE=cosmos`, managed identity or key. Several replicas are possible, also for concurrent edits of one diagram: writes are conditional on the diagram's revision (ETag `If-Match` inside a transactional batch, #65). |
| Secret | LLM API key as a Container Apps secret, passed as an environment variable. |

## Other deployments

| Variant | How | Notes |
|---|---|---|
| Local development | `make install && make run` (`127.0.0.1:8000`) | `/docs` needs `make docs-bundle` first ([ADR-0042](../adr/0042-docs-site-built-into-the-package.md)). |
| Docker Compose | `docker compose up` (`docker-compose.yml`) | Named volume `arcviz-data`; Git mirror variables passed through. Binds `0.0.0.0:8000`: on a shared network set `ARCVIZ_AUTH=token` and a token (passed through), otherwise it is an unauthenticated editor. |
| Wheel from a release | `pip install "arcviz_agent-<v>-py3-none-any.whl[anthropic]" && arcviz` | Contains the docs site. The LLM SDKs are extras (`[anthropic]`, `[openai]`); add `[cosmos]` for Cosmos DB. |
| Local model | `ARCVIZ_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1` | Nothing leaves the machine except voice recognition in some browsers. |

## Operational interfaces

- `GET /healthz`: liveness, no dependencies. `GET /readyz`: store ping and whether the provider can be created,
  without a model call ([Health and readiness probes](../specs/health-probes.md)).
- Logs: Python `logging` to stderr; `ARCVIZ_LOG_FORMAT=json` writes one JSON object per line with request id, trace id,
  pseudonymous user, one record per request and per model turn with token usage ([ADR-0111](../adr/0111-json-logs-with-an-in-house-formatter.md)).
  Traces (and log export) through OpenTelemetry or Azure Monitor with `ARCVIZ_TELEMETRY` (the `telemetry` extra,
  [ADR-0114](../adr/0114-opentelemetry-as-an-optional-extra.md)), with spans inside chat turns and the metrics
  `arcviz.http.server.request.duration`, `arcviz.llm.duration`, `arcviz.llm.tokens`, `arcviz.live.streams`,
  `arcviz.store.request_units` (Cosmos DB, [ADR-0154](../adr/0154-cosmos-request-units-as-a-metric.md)); keyless to
  Azure Monitor with `ARCVIZ_TELEMETRY_AUTH=entra` ([ADR-0134](../adr/0134-own-spans-and-metrics-by-allow-list.md)).
- Configuration: environment variables only ([Configuration](../../reference/configuration.md)).
  An invalid kinds file stops startup. Malformed variables stop it with one message that names every one of them, its
  expected format and its value ([ADR-0106](../adr/0106-configuration-problems-collected-at-start-up.md)).
- Data migration between backends: `arcviz-migrate --from file:data --to sqlite:data`
  ([Persistence](../../spec/persistence.md)). Existing diagrams join the default project on the first
  project listing after an upgrade, or explicitly with `arcviz-migrate --projects file:data`. Existing projects
  move into personal workspaces at every start (data from before workspaces to `ARCVIZ_ADMIN_USER`, which must be
  set in proxy mode; M20 team projects to their workspace's owner, every access kept as a share), or explicitly with
  `arcviz-migrate --workspaces file:data --admin USER` ([personal workspaces](../specs/personal-workspaces.md)).
