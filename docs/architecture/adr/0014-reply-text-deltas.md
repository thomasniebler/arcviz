# ADR-0014: Stream reply text as `text_delta` events next to the round's `text`

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/reply-streaming.md`

## Context

Issue #74 asks to stream the assistant's text through the existing SSE channel. `run_turn` is a
generator that yields `AgentEvent`s; providers implement `_complete`, which blocks until a round is
finished. The SSE protocol has a `text` event per round that the UI concatenates. Two choices: how the
deltas get out of the provider into the generator, and how they appear on the wire.

## Options

How deltas leave the provider:

1. **Generator hook** `_stream()` yielding `TextDelta`s and finally the `ModelReply`, with a default
   that wraps `_complete`. Fits the existing generator pipeline (no threads), providers opt in.
2. **Callback** `on_delta` passed to `_complete`. A callback cannot yield from the `run_turn`
   generator; it would need a queue and a worker thread per call.
3. **Make `_complete` itself a generator.** Breaks every provider, including `complete_text`.

How they appear on the wire:

A. **New event `text_delta`**, and the existing `text` event still carries the whole round text.
   Additive; old clients unchanged; the round text can correct the streamed text (server-side
   fallback switching models mid-stream, a client that dropped a chunk).
B. **Change `text` to carry deltas.** One event type less, but a silent semantic change for every
   consumer and no authoritative text.

## Decision

Option 1 with A: a `_stream` generator hook and an additive `text_delta` event, because it needs no
threads, keeps every existing provider and client working, and keeps a single authoritative text.

## Consequences

- The UI must replace the streamed text of a round with the `text` event (done in `reply.js`).
- Each round's text crosses the wire twice (deltas plus `text`); negligible for chat replies.
- Streaming is on by default (`ARCVIZ_STREAM=0` disables it), which changes the request shape sent to
  OpenAI-compatible servers (`stream`, `stream_options`).
