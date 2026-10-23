# ADR-0126: The server closes a chat turn on a worker thread when its response ends

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/hardening.md`

## Context

`DiagramAgent.chat` and `ProjectAgent.chat` are synchronous generators that persist the turn (automatic version,
transcript entry) in a `finally` block. The chat routes iterated them with `iterate_in_threadpool` and never closed
them. When a browser went away mid-turn, Starlette stopped iterating; the `finally` ran only when the garbage
collector found the generator, on whatever thread triggered the collection (#112). Until then undo could not reach
the change and a restart lost the version and the transcript. The routes also read the diagram on the event loop,
where the Cosmos store's retry back-off (`time.sleep`) stalls every request.

## Options

1. **Close the generator from the response** (`TurnResponse.__call__`'s `finally`): on a worker thread, shielded from
   the cancellation, awaited before the request ends. Deterministic, keeps one thread per turn, needs no new
   infrastructure.
2. **Detach the turn from the request**: run it to completion in a background task even after the client left. The
   model would keep being called (cost) for nobody; a turn the user abandoned would still apply its later operations.
3. **Finalize from a disconnect callback** while the generator keeps running: needs cross-thread signalling into the
   agent and still has to wait for the running model call.

## Decision

Option 1. Stopping the model after a disconnect is the documented behaviour (`Turn interrupted …`), and closing the
generator is exactly the signal the agent was written for. Everything before the stream (existence and role checks,
building the agent) runs in one `run_in_threadpool` call.

## Consequences

- A disconnected turn is versioned and transcribed at once; `tests/test_turn_disconnect.py` checks it under a real
  uvicorn with the garbage collector disabled.
- The request (and so a graceful shutdown) waits for the running model call to return before it ends, as it did for a
  completed turn; the model is not called again.
- `anyio` (a hard dependency of Starlette) is used directly for the shielded scope; no new dependency.
