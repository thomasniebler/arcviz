# Streaming reply text deltas

- Status: Implemented (milestone M7)
- Issue: #74
- ADR: [0014 Stream reply text as `text_delta` events next to the round's `text`](../adr/0014-reply-text-deltas.md)
- Related: [LLM call resilience](llm-resilience.md) (retries never repeat a stream that already sent text)

## Summary

Today a provider call returns only when the model has finished the round, so the chat bubble shows
"…" until the whole reply exists. With streaming, both providers hand the reply text to the loop
piece by piece while it is generated; the loop emits each piece as a `text_delta` event through the
existing SSE channel, and the browser appends it to the reply bubble. The round's full text still
arrives as the existing `text` event, which stays authoritative.

## Goals

- Text deltas from the Anthropic and the OpenAI-compatible provider reach the browser while the
  model is still writing.
- The SSE protocol stays backwards compatible: existing events keep their meaning, clients that
  ignore the new event see exactly what they saw before.
- `ScriptedProvider` can stream, so unit, server and e2e tests cover the path without network.
- Streaming can be switched off for endpoints that do not support it.

## Non-goals

- Streaming tool-call arguments (`eager_input_streaming`) or thinking blocks. Tool calls are still
  shown when complete; their inputs are small.
- Streaming chat compaction summaries (`complete_text`), which nobody watches.
- Persisting deltas: the transcript stores the final text as before.

## Design

### LLM layer

- New neutral type `TextDelta(text: str)` in `arcviz/llm/base.py`.
- New provider hook with a default implementation:

  ```python
  def _stream(self, system, messages, tools) -> Iterator[TextDelta | ModelReply]:
      yield self._complete(system, messages, tools)
  ```

  A streaming provider overrides it: it yields any number of `TextDelta`s and then exactly one
  `ModelReply` (the same one `_complete` would have returned). `_complete` stays and is still used by
  `complete_text`.
- `run_turn` calls `_stream` instead of `_complete`. Every `TextDelta` becomes
  `AgentEvent("text_delta", {"text": delta})`. When the `ModelReply` arrives, the loop continues as
  before: the round's full text is emitted as `text`, then refusal / max_tokens / tool calls. An
  exception while iterating (also after some deltas) becomes the usual `error` event; a stream that
  ends without a `ModelReply` is an `error` "The provider stream ended without a reply.".
- Providers take `stream: bool = True`. `ProviderConfig.stream` is read from `ARCVIZ_STREAM`
  (default on; `0`, `false`, `no` switch it off) and passed by `create_provider`.

### Anthropic provider

Request parameters are built once (`_params`) and the response is converted once (`_to_reply`), shared
by `_complete` and `_stream`. `_stream` uses the SDK helper `client.messages.stream(**params)` (with
fallbacks: `client.beta.messages.stream(betas=[…], fallbacks="default", **params)`), yields
`TextDelta(event.text)` for every `text` event and converts `stream.get_final_message()`. If the
server-side fallback switches models mid-stream, the deltas may contain text the final message does
not; the authoritative `text` event corrects the bubble (ADR-0014).

### OpenAI-compatible provider

`_stream` calls `chat.completions.create(..., stream=True, stream_options={"include_usage": True})`,
yields every `delta.content` as a `TextDelta`, accumulates `delta.tool_calls` by `index` (id, name,
argument fragments, parsed with `parse_arguments` at the end), takes `finish_reason` and the usage
chunk, and maps the stop reason exactly like `_complete`. A server that rejects streaming or
`stream_options` is handled by `ARCVIZ_STREAM=0`.

### Agent and server

`DiagramAgent.chat` passes `text_delta` events through unchanged (`{"type": "text_delta", "text": …}`);
nothing else changes. `server.py` already serialises every event as one SSE `data:` line.

### Event order of a round (SSE)

```
text_delta* → text? → (tool_call → tool_result)* | error
```

…and the turn ends with `done` (or `error`) and `final` as before.

### Frontend

A small pure module `arcviz/static/reply.js` (`createReplyBuffer()`) keeps the finished rounds' texts
and the partial text of the running round: `delta(text)` appends to the partial text, `round(text)`
replaces the partial text with the authoritative round text, `value()` joins the non-empty parts with
blank lines. `app.js` shows `value()` in the pending bubble on every `text_delta` and `text`.

### Error handling and retries

The SDKs retry only the request that opens a stream (ADR-0012). A stream that breaks after deltas were
emitted is not retried: the turn ends with an `error` event and the UI turns the bubble red with the
error message, so partial text is never presented as a reply and nothing is duplicated.

## Testing

- `tests/test_streaming.py`: `run_turn` with a streaming `ScriptedProvider` emits `text_delta`s whose
  concatenation equals the following `text`, in the order above; a provider without `_stream` behaves
  as before; an exception after deltas yields `error`; a stream without reply yields `error`; the
  Anthropic `_stream` against a fake client (text events + final message with a tool call); the
  OpenAI `_stream` against fake chunks (content deltas, split tool-call arguments, usage chunk);
  `ARCVIZ_STREAM` parsing and wiring; `stream=False` uses the non-streaming call.
- Server: the SSE stream contains `text_delta` events before `text`.
- `tests/js/reply.test.mjs`: the reply buffer.
- e2e: the scripted server streams; the smoke test asserts the reply appears once, complete.

## Migration / compatibility

The new event type is additive. Default on: deployments behind gateways that cannot stream set
`ARCVIZ_STREAM=0`. Custom providers keep working through the default `_stream`.

## Open questions

None open; decisions taken without the maintainer are *Proposed* in the ADR.

## Decisions

- A separate `text_delta` event; `text` keeps carrying the full round text and wins (ADR-0014, Proposed).
- Generator hook `_stream` rather than a callback or a thread (ADR-0014, Proposed).
- Streaming on by default with `ARCVIZ_STREAM=0` as the escape hatch; OpenAI requests ask for the
  usage chunk via `stream_options` (Proposed).
