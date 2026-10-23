# LLM layer

Source: `arcviz/llm/base.py`, `arcviz/llm/anthropic_provider.py`, `arcviz/llm/openai_provider.py`,
`arcviz/llm/factory.py`. Tests: `tests/test_agent.py` (through the `ScriptedProvider` in
`tests/conftest.py`), `tests/test_llm_resilience.py`, `tests/test_streaming.py`.

## Neutral types

| Type | Fields | Purpose |
|---|---|---|
| `ToolSpec` | `name`, `description`, `parameters` (JSON schema) | Tool definition handed to the provider |
| `ToolCall` | `id`, `name`, `arguments: dict` | A call the model made |
| `ToolResult` | `tool_call_id`, `content: str`, `is_error` | Result fed back to the model |
| `ChatMessage` | `role` (`user`/`assistant`), `content` | Persisted text-only transcript turn |
| `ModelReply` | `text`, `tool_calls`, `stop_reason`, `native`, `usage` | One model call's result; `native` is the provider payload needed to echo the assistant turn |
| `TextDelta` | `text` | A piece of reply text a streaming provider yields before the round's `ModelReply` |
| `AgentEvent` | `type`, `data` | Streamed to the caller (`text_delta`, `text`, `tool_call`, `tool_result`, `done`, `error`) |

`stop_reason` is normalised to `end_turn`, `tool_use`, `max_tokens`, `refusal` or `other`.

## Provider contract

`LLMProvider` is abstract with five hooks a provider implements:

- `_history_to_native(history)`: persisted transcript → native message list.
- `_user_message(text)`: native user message.
- `_complete(system, messages, tools) -> ModelReply`: one model call.
- `_stream(system, messages, tools) -> Iterator[TextDelta | ModelReply]` (optional): one model call
  that yields text deltas while the model writes and then exactly one `ModelReply`. The default
  yields `_complete(...)`, so a provider that cannot stream needs nothing. Design:
  [Streaming reply text](../architecture/specs/reply-streaming.md), ADR-0014.
- `_assistant_message(reply)`: native assistant message to append (must carry the tool calls).
- `_tool_results_messages(results)`: native message(s) with all results of one round.

Class attributes `name` and `model` are reported by `GET /api/config`.

`config_error() -> str | None` (concrete, default `None`) tells the readiness probe, without any
network request, why the provider cannot make requests. `AnthropicProvider` returns
`"ANTHROPIC_API_KEY is not set."` when its client has neither an `api_key` nor an `auth_token`
(the SDK constructs a client without credentials and only fails on the first request). The OpenAI
SDK already refuses to construct a client without a key, so `OpenAICompatibleProvider` keeps the
default.

## Shared loop: `run_turn()`

```python
run_turn(system, history, user_message, tools, execute, max_rounds=12, token_budget=None) -> Iterator[AgentEvent]
```

`tools` is a list, or a callable evaluated before every model call (the project chat offers the tools of
the diagram that is active at that point, [agent](agent.md#project-chat), ADR-0058).

For up to `max_rounds` rounds:

0. Token budget (design: [LLM call resilience](../architecture/specs/llm-resilience.md), ADR-0013):
   before every call except the first, if `token_budget` is set and the summed `input_tokens +
   output_tokens` of the turn reach it, emit `error` "Token budget of N tokens for this turn
   exhausted (M used); stopped before the next model call." with the usage and end the turn.
1. Iterate `_stream`: every non-empty `TextDelta` becomes a `text_delta` event (`{"text": …}`). Any
   exception, also after deltas were emitted, becomes an `error` event
   (`"<ExceptionType>: <message>"`) and ends the turn; a stream without a `ModelReply` becomes
   `error` "The provider stream ended without a reply.".
2. If the reply has text, emit `text` (the round's complete text, authoritative over the deltas) and
   remember it.
3. `refusal` → `error` "The model declined to answer this request."; `max_tokens` → `error`
   "The model response was cut off (max_tokens)."; both end the turn.
4. No tool calls → leave the loop.
5. Otherwise append the assistant message, then for each call emit `tool_call`, run `execute`,
   emit `tool_result` (`id`, `name`, `is_error`, `content`), and append all results.

If the loop exhausts `max_rounds` an `error` "Stopped after N tool rounds without a final answer."
is emitted. Unless an error ended the turn early, the loop finishes with `done` carrying the text
parts joined by blank lines and `usage`, the per-key sum of every `ModelReply.usage` of the turn.
Every `error` event carries the same `usage` sum for the model calls that completed before it, so
the agent can account for the tokens of a failed turn as well.

`complete_text(system, user_text)` is one call without tools (chat compaction): refusal,
`max_tokens` and an empty text raise `RuntimeError`. `complete_tool(system, user_text, tool)` is one
call offering only `tool` (the architecture review's `submit_review`) and returns
`(arguments, usage)`: the arguments of the first call of that tool, else a JSON object that the reply
text consists of or carries in a ```` ```json ```` fence; refusal, `max_tokens` or neither raise
`RuntimeError`. Neither streams nor forces the tool (there is no `tool_choice` in the hooks).

`parse_arguments(raw)` accepts a dict or a JSON string for tool arguments and raises
`ValueError` for anything else; providers use it instead of string matching.

### Turn and call records

`run_turn` delegates to `_turn` and, in a `finally`, writes one record to the logger `arcviz.llm` (spec
`docs/architecture/specs/operations.md` §4, ADR-0112): `event=llm_turn`, `provider` (`name`), `model`, `outcome`
(`done`; `error` with `error_kind` = `budget` | `exception` | `no_reply` | `refusal` | `max_tokens` | `max_rounds`
and, for `exception`, `error_type` = the class name; `aborted` when the generator was closed before it ended),
`rounds` (model calls made), `tool_calls`, the summed usage counters and `duration_ms`; INFO for `done`, WARNING
otherwise. `complete_text` and `complete_tool` write `event=llm_call` with `purpose` (`text` or the tool's name), the
call's usage, `duration_ms` and `outcome` (`error` with `error_type` when they raise). The message text is a summary
of the same numbers; no record carries a prompt, a message, a reply, tool arguments or an exception message. The
fields are passed as `extra` attributes plus a `fields` tuple naming them (see `observability.fields`).

### Spans and metrics (#187, ADR-0134)

With telemetry on (`arcviz/telemetry.py`; no-ops otherwise): every model call of a turn is a span `arcviz.llm.call`
(`gen_ai.system` = `name`, `gen_ai.request.model`, `arcviz.llm.round`, `arcviz.llm.outcome` `ok`/`error`, the
reply's `gen_ai.usage.input_tokens`/`output_tokens`; an exception sets the error status to its type only), parented
to the context current when the turn started and never made current itself (the streamed call yields in between).
Every tool execution is a current span `arcviz.tool` (`arcviz.tool.name`: the name if the round offered that tool,
else `unknown`; `arcviz.tool.outcome`), so the agents' store writes inside it (`arcviz.store.write` with
`arcviz.store.operation` `write_through`/`commit_turn`/`append_chat` and `arcviz.store.outcome`, `agent.store_span`)
are its children. `complete_text`/`complete_tool` are `arcviz.llm.call` spans too. Every `llm_turn`/`llm_call`
record also feeds `telemetry.record_llm`: the histogram `arcviz.llm.duration` (s; `gen_ai.system`,
`gen_ai.request.model`, `arcviz.llm.kind` `turn`/`call`, `arcviz.llm.outcome`) and the counter `arcviz.llm.tokens`
(`gen_ai.token.type` `input`/`output`).

## Timeouts and retries

Both providers take `stream: bool = True` (from `ARCVIZ_STREAM`); with `False`, `_stream` yields the
`_complete` result. `complete_text` (chat compaction) never streams.

Both providers take `timeout: float | None` and `max_retries: int | None` and pass each to their SDK
client only when set, so the SDK defaults (600 s, 2 retries) apply otherwise (ADR-0012). The SDKs
retry connection errors, timeouts, 408, 409, 429 and 5xx with exponential backoff and honour
`Retry-After`; an exception left after the retries becomes the `error` event of step 1. An injected
`client` is used unchanged.

## Anthropic provider

- SDK: `anthropic`; default model `claude-opus-5-5`; `max_tokens=16000`.
- System prompt is sent as one text block with `cache_control: ephemeral` (prompt caching).
- Tools map 1:1 to `input_schema` tools.
- `_params` builds the request and `_to_reply` converts the response, shared by both paths.
  `_stream` uses `client.messages.stream(**params)` (with fallbacks
  `client.beta.messages.stream(betas=[…], fallbacks="default", **params)`), yields
  `TextDelta(event.text)` per `text` event and converts `stream.get_final_message()`.
- `effort` (from `ARCVIZ_EFFORT`) is passed as `output_config.effort`.
- With `use_fallbacks` (default on) the call goes through
  `client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default")`,
  otherwise `client.messages.create`.
- History: consecutive same-role turns are merged because the API requires alternation; if the
  history ends with a user turn, "(continued)" and a stub assistant "OK." are appended.
- The assistant turn is echoed with the full native `content` (including any thinking blocks).
- Tool results are sent as `tool_result` blocks in one user message.
- Usage: `input_tokens`, `output_tokens`.

## OpenAI-compatible provider

- SDK: `openai`; default model `gpt-4o`; `max_tokens` unset unless given.
- With `base_url` set and no key, the key `not-needed` is used (local servers ignore it).
- `api_key` may be a callable (`str | Callable[[], str]`); the SDK (≥ 1.106) calls it before every request and sends
  `Authorization: Bearer <value>`. **Entra ID** (`ARCVIZ_OPENAI_AUTH=entra`, ADR-0115): `create_provider` passes
  `entra.BearerTokenProvider(entra.default_credential(), config.token_scope)`: azure-identity's
  `DefaultAzureCredential` (lazy import, `ConfigError` naming the `azure-openai` extra without it), scope
  `ARCVIZ_OPENAI_TOKEN_SCOPE` (default `https://cognitiveservices.azure.com/.default`). The provider caches the
  `AccessToken` and fetches a new one when fewer than 300 s are left, with a single refresh in flight (outside the
  lock, in a worker thread abandoned after `REFRESH_TIMEOUT`, 30 s, as a failed attempt (`TimeoutError`): callers
  holding a still-valid token never wait, callers without one wait for the fetch, at most until that deadline). Every failed refresh blocks new attempts for `REFRESH_BACKOFF` (30 s):
  while the old token is still valid it logs one warning (exception type only) and keeps it; otherwise the error
  propagates to the refreshing caller; the callers that waited for it and, within the backoff, every later caller at
  once get a new `TokenRefreshError` raised `from` it, into the chat stream as an `error` event. A token without value or expiry counts as a failure; the
  in-flight flag is cleared on every exit, `BaseException` included. The token is never logged (`repr` shows only the scope).
- System prompt is the first `system` message; tools are `function` tools with `tool_choice: auto`.
- `finish_reason` mapping: `length` → `max_tokens`, `content_filter` → `refusal`, tool calls
  present → `tool_use`, else `end_turn`.
- Tool results are `role: tool` messages with `tool_call_id`.
- `_stream` sends `stream=True, stream_options={"include_usage": True}`, yields every
  `delta.content`, accumulates `delta.tool_calls` by `index` (id, name and argument fragments, parsed
  with `parse_arguments`; a missing id becomes `call_<index>`), and takes `finish_reason` and the
  final usage chunk. The stop reason is mapped by `_stop_reason`, the same as for `_complete`.
- Usage: `prompt_tokens` → `input_tokens`, `completion_tokens` → `output_tokens`.

## Factory

`ProviderConfig.from_env()` reads `ARCVIZ_PROVIDER` (default `anthropic`), `ARCVIZ_MODEL`,
`ARCVIZ_EFFORT`, `ARCVIZ_ANTHROPIC_FALLBACKS` (`0`/`false`/`no` disable), `ARCVIZ_STREAM` → `stream`
(default on; `0`/`false`/`no` disable), `ARCVIZ_LLM_TIMEOUT` →
`timeout` and `ARCVIZ_LLM_MAX_RETRIES` → `max_retries` (unset → `None`; non-numeric or negative →
`envconfig.ConfigError`, a `ValueError` naming the variable, the expected format and the value; `create_app` checks
this at start-up, [ADR-0106](../architecture/adr/0106-configuration-problems-collected-at-start-up.md)), and the provider's key
and base URL (`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` or `OPENAI_API_KEY`/`OPENAI_BASE_URL`), `ARCVIZ_OPENAI_AUTH`
→ `openai_auth` (`key`|`entra`; with `entra` the key is dropped, and a provider other than the OpenAI-compatible one or
a missing `OPENAI_BASE_URL` is a problem) and `ARCVIZ_OPENAI_TOKEN_SCOPE` → `token_scope`. `ProviderConfig.api_key`
is left out of the dataclass `repr`.
An `ARCVIZ_PROVIDER` other than `anthropic`, `openai`, `openai-compatible` or `ollama` is a problem of the
same `ConfigError`. The SDKs are extras (`anthropic`, `openai`; ADR-0107): `check_provider(config)` returns the
problem `ARCVIZ_PROVIDER=<p> needs the <SDK>: pip install "arcviz-agent[<extra>]".` when the provider's module is not
importable (`importlib.util.find_spec`, nothing is imported), plus
`ARCVIZ_OPENAI_AUTH=entra needs azure-identity: pip install "arcviz-agent[azure-openai]".` with `entra`, and
`create_app` runs it at start-up unless a provider was injected. `create_provider(config)` returns `AnthropicProvider` for `anthropic` and
`OpenAICompatibleProvider` for `openai`, `openai-compatible` or `ollama` (the provider modules import their SDK at
module level; a `ModuleNotFoundError` of the SDK becomes the same `ConfigError`); anything else raises
`ValueError`. The provider is created lazily by the server on the first chat request or
`GET /readyz` probe; a missing or rejected key is not a start-up problem.

## Adding a provider

Implement the five hooks (and `_stream` to stream text), set `name` and `model`, map the native stop reason to the normalised
set, and register it in `create_provider`. The loop, the agent and the UI need no changes. Add a
unit test that drives `run_turn` with a fake client.
