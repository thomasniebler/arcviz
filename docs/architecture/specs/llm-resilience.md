# LLM call resilience: timeouts, retries and a per-turn token budget

- Status: Implemented (milestone M7)
- Issue: #77
- ADRs: [0012 Use the SDKs' retry and timeout handling](../adr/0012-sdk-retries-and-timeouts.md),
  [0013 Per-turn token budget checked before each model call](../adr/0013-per-turn-token-budget.md)

## Summary

Provider calls currently run with whatever the Anthropic and OpenAI SDKs default to (10-minute
request timeout, 2 retries) and a chat turn may make up to `max_rounds` model calls regardless of what
they cost. This change makes the request timeout and the retry count configurable, and adds an
optional cap on the tokens one chat turn may consume. All three are off (SDK defaults, no cap) unless
configured, so existing deployments behave as before.

## Goals

- `ARCVIZ_LLM_TIMEOUT` sets the per-request timeout of both providers' SDK clients.
- `ARCVIZ_LLM_MAX_RETRIES` sets how often a failed request is retried with exponential backoff
  (connection errors, timeouts, 408, 409, 429, 5xx; `Retry-After` honoured).
- `ARCVIZ_TURN_TOKEN_BUDGET` caps the input + output tokens of all model calls of one chat turn; once
  reached, the turn ends with an `error` event instead of calling the model again.
- Testable without network: the factory wiring and the budget with `ScriptedProvider`; the retry
  behaviour against the real SDK client with an in-process mock transport.

## Non-goals

- Our own retry loop around `_complete` (see ADR-0012).
- Shrinking `max_tokens` of a call to the remaining budget, or the Anthropic `task_budget` beta (the
  model pacing itself). The budget is a hard stop between calls, nothing more.
- A budget for chat compaction (`complete_text`); it is one call per compaction and bounded by the
  summary input limit.
- Per-user or per-diagram budgets; the budget is process-wide configuration like `ARCVIZ_MAX_ROUNDS`.

## Design

### Configuration

| Variable | Default | Read in | Passed to |
|---|---|---|---|
| `ARCVIZ_LLM_TIMEOUT` | unset = SDK default (600 s) | `llm/factory.py` (`ProviderConfig.timeout: float \| None`) | SDK client `timeout=` |
| `ARCVIZ_LLM_MAX_RETRIES` | unset = SDK default (2) | `llm/factory.py` (`ProviderConfig.max_retries: int \| None`) | SDK client `max_retries=` |
| `ARCVIZ_TURN_TOKEN_BUDGET` | `0` = no cap | `server.py` | `DiagramAgent(token_budget=…)` → `run_turn(token_budget=…)` |

A value that is not a number (or negative) makes `ProviderConfig.from_env()` / `create_app()` raise
`ValueError`; for the provider variables this surfaces like any other provider construction error
(HTTP 503 on `/chat`, `provider_error` in `/api/config`).

### Providers

`AnthropicProvider` and `OpenAICompatibleProvider` take `timeout: float | None = None` and
`max_retries: int | None = None` and pass each to their SDK client constructor only when it is not
`None`, so the SDK default stays in force otherwise. An injected `client` is used as is.

Because the SDK retries a timed-out request too, the wall-clock worst case of one model call is about
`timeout × (max_retries + 1)` plus backoff. With streaming (#74, [spec](reply-streaming.md)) the SDK
retries only the request that opens the stream; a stream that breaks after deltas were emitted is not
retried and ends the turn with an `error` event, so no text is ever duplicated.

### Token budget

`LLMProvider.run_turn(..., token_budget: int | None = None)`. The loop already sums `usage` over the
calls of the turn. Before every model call **except the first**, if `token_budget` is set (> 0) and
`usage["input_tokens"] + usage["output_tokens"] >= token_budget`, the loop emits

```json
{"type": "error", "message": "Token budget of 20000 tokens for this turn exhausted (21234 used); stopped before the next model call.", "usage": {...}}
```

and returns. The first call always runs (a budget below one call's cost would otherwise make the
agent unusable without a message). The tool calls of the reply that crossed the budget have already
been executed and their results persisted, exactly like the operations before a `max_rounds` stop;
the agent finalises the turn as usual (version, transcript with the error).

`DiagramAgent(token_budget: int | None = None)` stores it and passes it to `run_turn`.

### Error handling

Exceptions the SDK raises after its retries are exhausted keep the existing behaviour: an `error`
event `"<ExceptionType>: <message>"` with the usage so far.

## Testing

- `tests/test_llm_resilience.py`: `ProviderConfig.from_env` parses the variables (unset → `None`,
  invalid → `ValueError`); `create_provider` passes them to the SDK clients (checked on the client's
  `timeout`/`max_retries`); an Anthropic and an OpenAI client on an `httpx2.MockTransport` that
  answers 500 once then 200 succeed with `max_retries=1` and end in an `error` event with
  `max_retries=0`.
- Budget with `ScriptedProvider`: the second call is skipped once the first reply's usage reaches the
  budget, the error carries the usage, the tool calls of the first reply were applied and versioned;
  no budget → unchanged behaviour; the first call always runs.
- Server: `ARCVIZ_TURN_TOKEN_BUDGET` reaches the agent.

## Migration / compatibility

No behaviour change without the new variables. `run_turn` and `DiagramAgent` get keyword arguments
with defaults; existing callers are unaffected.

## Open questions

None open; the decisions below were taken without the maintainer and are marked *Proposed* for review.

## Decisions

- Retries and timeouts are delegated to the SDKs (ADR-0012, Proposed).
- Defaults keep the SDK behaviour; nothing changes unless configured (conservative choice, Proposed).
- The budget counts input + output tokens of every call and is checked between calls; the first call
  always runs (ADR-0013, Proposed).
