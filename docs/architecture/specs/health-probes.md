# Health and readiness probes

- Status: Implemented
- Issue: #81
- ADR: none (the choices under Decisions are recorded there; none moves a module boundary)
- Depends on: [Persistence spec](persistence.md) (`DiagramRepository`)

## Summary

Container orchestrators (Azure Container Apps, Kubernetes, Docker) need two cheap HTTP endpoints: a
*liveness* probe that says the process is serving requests, and a *readiness* probe that says the
instance can do useful work. Until now the Docker `HEALTHCHECK` and the deployment guide used
`GET /api/config`, which is neither: it never fails for a broken store or a missing API key. This
change adds `GET /healthz` (liveness, no dependencies) and `GET /readyz` (store reachable, LLM
provider configured), backed by a new repository method `ping()` and a new provider method
`config_error()`. Neither probe makes a paid model call.

## Goals

- `GET /healthz` answers 200 as long as the event loop serves requests; it touches no store, no
  provider and no network.
- `GET /readyz` answers 200 only when the store answers a cheap probe and an LLM provider can be
  built and has credentials; otherwise 503 with the failing check named.
- The store probe is one cheap call per backend (file: a directory check, SQLite: one indexed
  `SELECT`, Cosmos DB: one point read) and is covered by the store contract for all backends.
- No model call, no token usage, no new runtime dependency.
- The Docker `HEALTHCHECK` and the Azure deployment guide use the new endpoints.

## Non-goals

- Verifying that the API key is *accepted* by the provider (that needs a request to the provider).
- Metrics, structured logging or OpenTelemetry (other M9 issues).
- Authentication of the probe endpoints; the app has none (see the deployment guide for Easy Auth).
- A startup probe; the app starts in well under the default probe delays.

## Design

### Endpoints

Both are plain synchronous FastAPI routes (FastAPI runs them in the thread pool, so a slow store
probe does not block the event loop). They live at the root, not under `/api/`, because that is
where orchestrators and operators expect them.

`GET /healthz`

```json
{ "status": "ok" }
```

Always 200. No dependencies.

`GET /readyz`

```json
{ "status": "ready", "checks": { "store": { "ok": true }, "provider": { "ok": true } } }
```

200 when every check passes. Otherwise 503 with

```json
{
  "status": "unavailable",
  "checks": {
    "store": { "ok": true },
    "provider": { "ok": false, "error": "ANTHROPIC_API_KEY is not set." }
  }
}
```

Every check always runs (no short-circuit), so one response shows all problems. `error` is the
exception message (or the provider's `config_error()`), the same level of detail that
`GET /api/config` already exposes in `provider_error`.

### Store check: `DiagramRepository.ping()`

```python
def ping(self) -> None:
    """Cheap reachability probe for readiness checks; raises if the backend cannot serve requests."""
    self.exists(PING_ID)
```

A concrete method on the base class (not abstract, so third-party backends keep working) with
`PING_ID = "readiness-probe"`, a valid diagram id. It reads, never writes, and does not care whether
the diagram exists. Any exception means "not reachable".

- `sqlite` and `cosmos` use the default: one indexed `SELECT` respectively one point read in the
  probe's own partition (404 is the normal answer; other HTTP errors become `StoreError`, connection
  errors propagate).
- `file` overrides it: `FileStore.exists()` cannot fail (a missing directory just reads as "no
  diagram"), so `ping()` checks that `<root>/diagrams` is a directory the process can write to and
  raises `StoreError` otherwise. It does not write a file, so the probe causes no I/O on the share
  beyond a `stat`/`access`.

### Provider check: `LLMProvider.config_error()`

```python
def config_error(self) -> str | None:
    """Reason the provider cannot make requests, determined locally (no network); None if usable."""
    return None
```

- `AnthropicProvider` returns `"ANTHROPIC_API_KEY is not set."` when its client has neither an
  `api_key` nor an `auth_token`: the Anthropic SDK constructs a client without credentials and only
  fails on the first request.
- `OpenAICompatibleProvider` keeps the default: the OpenAI SDK already refuses to construct a client
  without a key, which surfaces as a creation error.

The readiness check gets the provider through the same lazy path as the chat (`get_provider()`,
extracted from `get_agent()` in `server.py`): an injected or already created provider is reused,
otherwise `create_provider()` runs once, its result is cached in `app.state.provider`, and a failure
is recorded in `app.state.provider_error` as before. A readiness probe therefore warms the provider,
and `provider_ready` in `/api/config` becomes `true` after the first successful probe. Creating a
provider makes no network request.

### Docker and deployment

- The Dockerfile `HEALTHCHECK` calls `/healthz` instead of `/api/config` (liveness only: a missing
  API key must not mark the container unhealthy and get it restarted).
- `docs/deploy-azure.md` configures a liveness probe on `/healthz` and a readiness probe on `/readyz`.
  Container Apps sends probes to the container directly, not through the ingress, so Easy Auth does
  not intercept them.

## Testing

- Store contract (`tests/store_contract.py`, all backends): `ping()` returns on a fresh and a
  populated store and does not create a diagram.
- Backend specifics: `FileStore.ping()` raises `StoreError` when `<root>/diagrams` is gone; a Cosmos
  container whose `read_item` raises a non-404 HTTP error makes `ping()` raise `StoreError`.
- LLM layer: `AnthropicProvider` without credentials reports a `config_error()`, with an API key it
  reports `None` (no network: the client is constructed, never called).
- Server (`tests/test_server.py`): `/healthz` is 200 even when the store is broken; `/readyz` is 200
  with a working store and provider; 503 naming `store` when `ping()` raises; 503 naming `provider`
  when the provider reports a `config_error()` or cannot be created (and the creation error lands in
  `/api/config`'s `provider_error`); a provider created by the probe is reused by the chat.

## Migration / compatibility

Additive. `ping()` and `config_error()` have default implementations, so existing or third-party
backends and providers keep working. `/api/config` is unchanged and still usable as a liveness
check for existing deployments.

## Decisions

- **Missing LLM credentials make the instance not ready** (as the issue asks), although diagram
  editing without the LLM still works. The alternative, reporting the provider check without
  failing readiness, would keep a misconfigured deployment serving traffic with a broken chat.
  Recorded as a decision to review.
- **The Docker `HEALTHCHECK` uses liveness, not readiness**, so a configuration problem does not
  turn into a restart loop.
- **`ping()` is concrete with a default**, not abstract, so adding it is not a breaking change for
  backends outside this repository.
- **No "valid key" check**: verifying the key would need a request to the provider; the issue
  excludes paid calls, and a free endpoint (model listing) is not available on every
  OpenAI-compatible server.

## Open questions

None.
