# ADR-0007: Atomic batch as an ordinary operation

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/batch-operations.md`

## Context

The agent builds multi-element structures with many tool calls; each is applied and saved on its
own, so a failure halfway leaves a partial structure and every call costs a model round trip. Issue
#69 asks for an all-or-nothing batch. All mutations must go through `operations.apply` (CLAUDE.md
rule 2), which already restores the diagram on `OperationError`/`TypeError`. #66 (typed argument
validation, rollback on every exception) is open and not part of this change.

## Options

1. **`apply_batch` as an entry of `OPERATIONS`** whose steps call `apply()`. The tool, the REST
   endpoint, the agent's save/version logic and the transcript need no special cases; each step keeps
   the single operation's validation and messages. Costs one dict-shaped result and a `touched` rule
   in `apply()`.
2. **A separate `apply_many()` function and a new endpoint/tool path.** Same semantics, but a second
   entry point next to `apply` that the agent and server must call explicitly; more surface.
3. **Let the model call tools in parallel and roll back the whole turn on error.** No new tool, but
   changes the turn semantics (partial progress is a feature for long turns) and still costs one save
   per call.

Sub-decisions with alternatives: nesting (allowed vs. rejected) and the size limit (none vs. a cap).

## Decision

Option 1, flat (a batch cannot contain `apply_batch`) and capped at 50 steps, because it reuses the
single mutation path unchanged and keeps tool results and version details bounded. The batch restores
the pre-batch state on **any** exception from a step and re-raises non-`OperationError` exceptions
unchanged, so it is atomic even before #66 lands.

## Consequences

- One new tool and operation; no new endpoint; stored data unchanged.
- A failed batch reports the 1-based step and the original message; the model fixes and resends the
  whole batch.
- The limit of 50 is arbitrary; raising it is a constant change.
- When #66 lands, the batch's own restore may become redundant.
