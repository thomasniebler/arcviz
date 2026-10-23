# ADR-0048: Request size limits on by default, in-process rate limiting off by default

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/request-limits.md`

## Context

The API accepted bodies, chat messages and import documents of any size, and any client could start
paid model calls (chat, review) without limit (#85, arc42 R1). The app runs as one process with the
`file`/`sqlite` stores and possibly as several replicas with `cosmos`; it is often deployed behind a
reverse proxy (Azure Container Apps ingress). CLAUDE.md rule 5 asks for no new runtime dependency
without a reason.

## Options

1. **Leave limits to the proxy** (ingress body size, API management rate limits). Nothing to build, but
   local and Docker deployments stay unprotected, and the proxy cannot tell a chat turn from a read.
2. **Size limits and token-bucket rate limits in an ASGI middleware, in memory.** No dependency, works
   everywhere, knows the request classes; counts per replica.
3. **A rate-limiting library** (`slowapi`/`limits`) with an optional Redis backend. Shared counters
   across replicas, but a new runtime dependency and an external service for the shared case.

## Decision

Option 2. Size limits are on with defaults no UI user reaches (1 MiB body, 10 MiB import, 20000
characters per chat message, 500 proposal operations). Rate limiting is per class (`chat`, `review`,
`ops`) and **off by default**, because a sensible rate depends on the deployment and, behind a proxy
without trusted forwarding headers, every user would share one client key.

## Consequences

- Clear 413/429 responses with the limit in `detail` and `Retry-After`; the limits are listed in
  `/api/config`.
- With several replicas each one counts separately: the effective limit is N per replica. Shared
  counters would need option 3 (follow-up if needed).
- Operators enable rate limits explicitly and should configure `FORWARDED_ALLOW_IPS` behind a proxy;
  with authentication in proxy mode the authenticated user is the key.
