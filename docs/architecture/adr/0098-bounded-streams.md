# ADR-0098: Streams are bounded: caps per user and process, a maximum lifetime, an end on shutdown

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`

## Context

An open stream is a long request. Unbounded, a client (or a bug reconnecting in a loop) can exhaust memory or the
threadpool; uvicorn waits for open responses on shutdown, so SSE streams would block a rolling update.

## Decision

- Streams are async; a stream uses a worker thread only for its periodic checks.
- `ARCVIZ_LIVE_MAX_STREAMS_PER_USER` (default 20, 429) and `ARCVIZ_LIVE_MAX_STREAMS` (default 1000, 503) per
  process; a reconnect with the same session replaces its predecessor instead of counting twice.
- Every stream ends after `ARCVIZ_LIVE_MAX_SECONDS` (default 900); the client reconnects.
- A stream's re-check is raced against its close and capped at the presence TTL, and a shared read waits at most
  that long for a read in flight (a stuck one is replaced): a hanging store neither delays shutdown nor queues the
  worker threads.
- The app's lifespan start-up wraps the SIGINT/SIGTERM handlers the server installed, so a shutdown signal closes
  the hub (every stream ends) before the server's own handler starts draining connections. A lifespan *shutdown*
  hook alone would not do: uvicorn runs it only after the drain. `/live` and `/presence` are outside the rate
  classes; selections are throttled per stream and presence bodies are limited to 4 KiB.

## Consequences

Shutdown is not delayed by open streams, however the app is served, as long as the lifespan runs in the main thread
with Python signal handlers (uvicorn, `uvicorn --factory`, a uvicorn worker). Otherwise the lifetime and uvicorn's
`--timeout-graceful-shutdown` bound it (documented).
