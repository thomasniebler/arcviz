# ADR-0012: Use the SDKs' retry and timeout handling for provider calls

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/llm-resilience.md`

## Context

Issue #77 asks for configurable request timeouts and retries with backoff for provider calls. Both
SDKs the providers use (`anthropic`, `openai`) already retry connection errors, timeouts, 408, 409,
429 and 5xx responses with exponential backoff and jitter, honour `Retry-After`, and default to 2
retries and a 10-minute timeout. Neither value is configurable in Arcviz today.

## Options

1. **Configure the SDKs.** Pass `timeout` and `max_retries` to the SDK clients from two environment
   variables. A few lines in the providers and the factory; the retry policy is the one the API
   vendors maintain (which status codes, `Retry-After`, `x-should-retry`). With streaming, the SDK
   retries only the request that opens the stream, which is the safe point. Retries are invisible to
   the chat stream.
2. **Own retry loop in `LLMProvider.run_turn`** around `_complete`, with the SDK retries set to 0.
   Provider-neutral and testable with `ScriptedProvider`, and could emit a "retrying" event to the UI,
   but duplicates the SDK logic, needs its own list of retryable exceptions per SDK, and would retry
   a stream after deltas were already sent unless carefully guarded.
3. **Both** (SDK retries plus an outer loop). Multiplies attempts and wait times; no gain.

## Decision

Option 1: configure the SDKs' own retry and timeout handling, because it is the smallest change and
the vendors' policy is more complete than one we would write.

## Consequences

- Retries cannot be shown in the UI (the stream is just slower). Acceptable; a retrying status could
  be added later with an SDK hook.
- The behaviour is tested against the real SDK clients with an in-process mock transport, not with
  `ScriptedProvider`.
- A future provider must map `timeout` and `max_retries` onto its own client.
