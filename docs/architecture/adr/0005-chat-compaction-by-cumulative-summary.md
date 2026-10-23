# ADR-0005: Compact the chat transcript with a cumulative LLM summary

- Status: Accepted
- Date: 2026-10-04
- Spec: `docs/architecture/specs/chat-compaction.md`

## Context

The per-diagram chat transcript is persisted in full and fed back to the model on every turn,
truncated to the last `history_turns` (30) messages in `DiagramAgent._history()`. Two problems:
the stored transcript grows without bound (a cost and a document-size concern once it lives in a
database), and everything older than the cut is invisible to the model, so long sessions lose the
user's earlier intent and naming conventions.

## Options

1. **Truncate the stored transcript** to the last N entries. Bounded storage, no extra LLM call, but
   history is simply lost, for the user (UI) and the model alike.
2. **Keep everything, summarise on the fly** per turn. No storage saving; one extra LLM call on
   every turn; summaries are not stable across turns.
3. **Compact: periodically replace the older entries with one summary entry written by the LLM,
   cumulatively** (the previous summary is an input to the next). Bounded storage, one extra LLM
   call per compaction (not per turn), the model gets a stable summary of everything before the kept
   tail, the UI can show the summary in place of the folded turns.
4. **Semantic memory / retrieval over the transcript.** Powerful, but a different subsystem (embeddings,
   index) for a problem that a summary solves at this scale.

## Decision

Option 3. Compaction runs after a turn when the transcript exceeds a threshold, off the request's
critical path, and never fails the turn. It affects the transcript only; the version history stays
complete.

## Consequences

- The store contract needs `compact_chat(diagram_id, upto_seq, summary)` and a per-entry `seq`
  (specified in the persistence spec so every backend supports it).
- `LLMProvider` gains a plain text completion without tools for the summary call.
- A new entry role `summary` appears in `/api/diagrams/{id}`; the UI renders it collapsed.
- Two configuration variables (`ARCVIZ_CHAT_COMPACT_AFTER`, `ARCVIZ_CHAT_KEEP`).
- The summary's quality depends on the model; the prompt is fixed and testable with the scripted
  provider, but a bad summary silently degrades later turns. The kept verbatim tail limits the damage.
