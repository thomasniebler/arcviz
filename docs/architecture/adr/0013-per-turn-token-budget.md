# ADR-0013: Per-turn token budget checked before each model call

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/llm-resilience.md`

## Context

A chat turn may call the model up to `ARCVIZ_MAX_ROUNDS` (12) times, and every call resends the system
prompt, the history and the tool results so far, so a turn's cost grows roughly quadratically with its
rounds. Issue #77 asks for an optional per-turn token cap. `run_turn` already sums the `usage` of all
calls of a turn.

## Options

1. **Hard stop between calls.** Before each model call after the first, stop with an `error` event
   when input + output tokens so far reach the budget. Simple and provider-neutral; may overshoot by
   one call's cost.
2. **Shrink `max_tokens` of the next call** to the remaining budget. Caps output only (input is the
   larger part), and a reply cut at `max_tokens` already ends the turn with an error; worse result
   for the same cost.
3. **Anthropic `task_budget` (beta)**: the model sees a countdown and paces itself. Anthropic only,
   advisory, and counts differently (not the resent history).
4. **Count output tokens only.** Easy to reason about but ignores the dominant cost.

## Decision

Option 1, counting input + output tokens, checked before every call except the first, off by default:
it is the only option that bounds the real cost on both providers.

## Consequences

- A turn can exceed the budget by the cost of the call that crossed it; the budget is a stop
  condition, not a hard ceiling. Documented.
- The tool calls of the crossing reply are executed (as with `max_rounds`), so the diagram may be
  partially changed; the turn is versioned and the transcript shows the error.
- Cache-read tokens are not reported separately by the providers today and count as input tokens if
  the SDK includes them there.
