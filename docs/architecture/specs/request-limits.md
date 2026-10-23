# Request size and rate limits

- Status: Implemented
- Issue: #85
- ADR: [0048](../adr/0048-request-limits-and-in-process-rate-limiting.md)
- Depends on: [HTTP server](../../spec/http-server.md), [Authentication](authentication.md) (the client key)

## Summary

Nothing bounded what a client could send: a chat message of any length goes into the prompt of every
model call of the turn, an import document or an operation payload of any size is parsed in memory,
and any client could start chat turns and reviews (each a paid model call) as fast as it liked. This
change adds limits on the request body size (a separate, larger one for imports), on the length of a
chat message and on the number of operations in a proposal, all answered with **413**, and an
optional in-process **rate limit** per client for chat, review and other mutating requests, answered
with **429** and `Retry-After`. Sizes have generous defaults; rate limiting is off unless configured.
No new dependency.

## Goals

- Clear, documented 413 and 429 responses with the limit in `detail`; nothing is processed (and
  nothing written) for a rejected request.
- Defaults that no legitimate use of the UI hits, so existing deployments keep working.
- Rate limits per client and per class (`chat`, `review`, `ops`), configurable per class, off by
  default.
- Configuration read in one place (`limits.LimitConfig.from_env()`), invalid values stop the start-up
  with a message naming the variable.

## Non-goals

- Distributed rate limiting across replicas (each replica counts on its own; see the ADR).
- Limits on the diagram size (number of elements) or on the model's output; the per-turn token
  budget (`ARCVIZ_TURN_TOKEN_BUDGET`) already caps a turn's model usage.
- Abuse protection for the unauthenticated probes (`/healthz`, `/readyz` read nothing expensive).

## Design

### Module

`arcviz/limits.py` (imports nothing from the package) holds the configuration, the rate limiter and an
ASGI middleware. `server.create_app` builds `LimitConfig.from_env()` once and installs
`LimitsMiddleware` inside the authentication middleware, so unauthenticated requests are rejected
before their body is read, and the limiter can key on the authenticated identity.

### Size limits

| Limit | Variable | Default | Applies to | Response |
|---|---|---|---|---|
| Request body | `ARCVIZ_MAX_BODY_BYTES` | 1 MiB (1048576) | every `POST`/`PUT`/`PATCH` except the import, e.g. `/ops`, proposal confirm, chat | 413 `Request body too large (max N bytes).` |
| Import document | `ARCVIZ_MAX_IMPORT_BYTES` | 10 MiB (10485760) | `POST /api/diagrams/import` | 413 as above with the import limit |
| Chat message | `ARCVIZ_MAX_CHAT_CHARS` | 20000 characters | `message` of `POST …/chat` | 413 `Chat message too long (N characters, max M).` |
| Proposal operations | fixed: `MAX_PROPOSAL_OPERATIONS` = 500 | | `operations` of `POST …/proposal/apply` | 413 `Too many operations in the proposal (N, max 500).` |
| Batch steps | fixed: `operations.MAX_BATCH` = 50 (unchanged) | | `apply_batch` (REST and tools) | 400 (an `OperationError`, so the model can split the batch) |

The middleware checks `Content-Length` first and rejects without reading. Without it (chunked
upload) it reads the body up to the limit and rejects as soon as it is exceeded; a body within the
limit is buffered and replayed to the application, which is fine for JSON bodies of this size. A value
of `0` disables a size limit. Operation payloads (`/ops`, proposal confirm) are bounded by the body
limit; their structure is validated by the operations ([operation arguments](operation-arguments.md)).

### Rate limits

| Class | Requests | Variable (requests per minute and client) |
|---|---|---|
| `chat` | `POST …/chat`, `POST …/chat/compact` (each a model call) | `ARCVIZ_RATE_LIMIT_CHAT` |
| `review` | `POST …/review` (a model call) | `ARCVIZ_RATE_LIMIT_REVIEW` |
| `share` | `POST /api/projects/{p}/access`, `DELETE /api/projects/{p}/access/{user}` (M21: sharing would otherwise let anybody probe which users exist) | `ARCVIZ_RATE_LIMIT_SHARE` |
| `ops` | every other `POST`/`PUT`/`PATCH`/`DELETE` under `/api/` (operations, proposals, versions, undo/redo, create, import, delete, chat clear) | `ARCVIZ_RATE_LIMIT_OPS` |

Default `0` = unlimited for every class except `share` (30 per minute and client). Reads (`GET`), the probes, `/login` and the static files are
never limited.

Algorithm: a token bucket per (class, client) with capacity *N* and a refill of *N* per minute, so a
client may burst *N* requests and then continues at *N* per minute. A rejected request gets 429 with
`Retry-After` (seconds until the next token, rounded up) and `detail` `Too many chat requests; try
again in S s.`. The buckets live in memory, guarded by a lock; at most 10000 buckets are kept, full
buckets (idle clients) are dropped first.

The **client key** is the authenticated identity when there is one (the user from the trusted proxy
header, see [Authentication](authentication.md)), else the client address as the ASGI server reports
it. Behind a reverse proxy, run uvicorn with `FORWARDED_ALLOW_IPS` set to the proxy's address so the
address is the client's, not the proxy's; otherwise all clients share one bucket.

### Configuration and errors

`LimitConfig.from_env()` parses every variable as a non-negative integer and raises `ValueError`
(`ARCVIZ_MAX_BODY_BYTES must be a non-negative integer, got 'lots'.`) at start-up otherwise. Since M9 the error is a
`ConfigError` listing every malformed variable, merged with the other subsystems' problems ([operations](operations.md)).
`GET /api/config` reports the effective limits under `limits` (`max_body_bytes`, `max_import_bytes`,
`max_chat_chars`, `max_proposal_operations`, `max_batch`, `rate_limits`), so a client can check before
sending.

## Testing

`tests/test_limits.py`: body over the limit with and without `Content-Length` (chunked) is 413 and
writes nothing; exactly at the limit passes; the import has its own limit; chat message and proposal
length 413; limits of `0` disable; `/api/config` reports the limits; rate limiting off by default
(many requests pass); per class (chat limited, ops not), 429 with `Retry-After`, reads and probes never
limited; the token bucket refills over time and separates clients (fake clock); bucket eviction;
invalid configuration stops `create_app`.

## Migration / compatibility

Additive. A request larger than the defaults (1 MiB of JSON, a 10 MiB import, 20000 characters of
chat) was not realistic in the UI; deployments that need more raise the variable.

## Decisions

- Generous size defaults on, rate limiting off by default (ADR-0048, *Proposed*): limits that no UI
  user hits protect memory at no cost; a rate limit cannot be chosen without knowing the deployment
  (behind a proxy without `FORWARDED_ALLOW_IPS` all users would share one bucket).
- In-process token buckets, no Redis or other store, no new dependency.
- 413 for oversized chat messages and proposals (the body is valid but too large), 400 for an oversized
  batch (it is an operation error the model can correct).

## Open questions

None.
