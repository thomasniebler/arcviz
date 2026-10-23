# Chat compaction

- Status: Implemented (PR: feat/chat-compaction)
- Issue: #36
- ADR: [0005 Compact the chat transcript with a cumulative LLM summary](../adr/0005-chat-compaction-by-cumulative-summary.md),
  [0046 Transcript generation guards compaction](../adr/0046-transcript-generation-guards-compaction.md) (#67)
- Depends on: [Persistence spec](persistence.md) (`seq` on chat entries, `compact_chat`)

## Summary

The per-diagram transcript is appended on every turn and never shrinks. `DiagramAgent._history()`
feeds the model only the last `history_turns` (30) entries, so the model forgets everything older
while the store keeps growing. Compaction folds the older part of the transcript into one summary
entry written by the LLM, cumulatively, after a turn and off the critical path. The model then sees
"summary + recent turns", the UI shows the summary collapsed, and storage per diagram stays bounded.

## Goals

- A transcript never holds more than `compact_after` entries for long; after compaction it holds
  one summary entry plus the newest `keep` entries.
- The summary is cumulative: an existing summary is an input to the next one, so nothing is lost
  across compactions.
- Compaction never delays or fails a chat turn and never touches versions.
- The agent's history sent to the model begins with the summary when one exists.
- The UI shows the summary as a collapsed block in place of the folded turns.
- Testable without network using `ScriptedProvider`.

## Non-goals

- Compacting the system prompt or the diagram description (#15 covers the diagram side).
- Retrieval or embeddings over past turns.
- Per-user settings; thresholds are process-wide configuration.

## Design

### Trigger and execution

- After a turn's transcript has been appended (end of `DiagramAgent.chat`, inside the existing
  `finally`), the agent calls `self._maybe_compact(diagram_id)`.
- `_maybe_compact` loads the chat, and if `len(entries) > compact_after` schedules
  `self.compact(diagram_id)`. With `background=True` (production default) it runs on a single-worker
  `ThreadPoolExecutor` owned by the agent so at most one compaction runs at a time; the stream is
  not delayed. With `background=False` (tests) it runs inline.
- `compact(diagram_id)` is also the body of `POST /api/diagrams/{id}/chat/compact` (manual trigger,
  always inline, returns the new chat; a provider or store error becomes HTTP 502). It is a no-op
  (returns `False`) when there is nothing to fold.
- `drain()` waits for pending background compactions (tests and shutdown).
- Errors inside `compact` (provider error, store error) are logged with the diagram id and swallowed;
  the next turn retries. `compact_after = 0` disables automatic compaction.

### What is folded

Given the entries `e[0..n)` ascending by `seq`:

1. The tail starts at index `n - keep`, moved backwards to the nearest entry with `role == "user"`
   so the kept tail begins with a user message (if there is none before, nothing is compacted).
2. Everything before the tail is folded: an existing `summary` entry, user entries and assistant
   entries (including their `operations`, `version`, `error`). The fold must contain at least one
   user/assistant pair, otherwise compaction is skipped.
3. `upto_seq` = `seq` of the last folded entry.

### Summary call

- `LLMProvider.complete_text(system: str, user_text: str) -> str` is added to the base class, built
  from the existing hooks (`_user_message`, `_complete` with an empty tool list). A `stop_reason` of
  `refusal` or `max_tokens` (a truncated summary would silently lose the omitted part) or empty text
  raises `RuntimeError`; providers need no changes.
- System prompt (fixed, in `arcviz/agent.py`): the model is asked to write a compact summary of the
  earlier conversation about a deployment diagram: what the user asked for, what was built or changed
  (element names and kinds), naming conventions and decisions, open requests or errors. Plain text,
  no headings, at most about 250 words, in the language of the conversation.
- User text: the previous summary (if any) under "Previous summary:", then each folded entry as
  `user: …` / `assistant: …`; assistant entries append a compact operation list
  `[add_node service-x, move_node service-a → prod-vnet, …]` derived from `operations` so the summary
  knows what exists. The text is capped at 60 000 characters: if the fold does not fit, it is
  **shortened from its newer end** to the nearest user boundary until it fits (keeping at least one
  complete turn). Nothing is dropped from the prompt, so nothing is deleted unsummarised; the entries
  left out stay in the transcript and are folded by the next compaction.

### Stored shape

```json
{"role": "summary", "seq": 42, "content": "<summary text>",
 "covers": {"from_seq": 1, "to_seq": 42, "turns": 21}, "created_at": "<iso>"}
```

Written with `repo.compact_chat(diagram_id, upto_seq, summary, generation)`. `turns` counts the
folded user entries (cumulative, including the previous summary's `turns`). The write is conditional
on the entry at `upto_seq` still existing *and* on the transcript's generation being the one `compact`
read before loading the entries (`repo.chat_generation`, #67). The summary call can take seconds and
the user may press "Chat leeren" meanwhile; because `clear_chat` restarts `seq` at 1, a cleared and
refilled transcript can have an entry at `upto_seq` again, so the `seq` check alone would let the old
summary replace fresh messages. `clear_chat`, `import_chat` and deleting the diagram replace the
generation, so such a summary is refused (`compact_chat` returns `False`) and the fresh transcript stays
as it is; so is a summary computed for a diagram that was deleted and created again under the same id.
Appends and compactions keep the generation, so messages appended while summarising survive as before.

### Agent history

`_history()` becomes: if the first entry is a summary, emit
`ChatMessage("user", "Summary of the earlier conversation:\n<content>")` followed by
`ChatMessage("assistant", "Understood.")` (keeps roles alternating for providers that require it),
then the user/assistant entries of the tail, still limited to the last `history_turns`. Summary
entries anywhere else are ignored (there is only ever one, at the front).

### API and UI

- `GET /api/diagrams/{id}` returns the summary entry like any other chat entry.
- `POST /api/diagrams/{id}/chat/compact` runs `compact` inline and returns `{"compacted": bool, "chat": [...]}`.
- `DELETE …/chat` (`clear_chat`) removes the summary as well.
- `static/app.js` renders `role === "summary"` as a `<details class="msg summary">` with the summary
  `Earlier conversation (N turns summarised)` and the text inside; collapsed by default.

### Configuration

Read in `server.create_app` (the agent receives plain arguments).

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_CHAT_COMPACT_AFTER` | `60` | compact when the transcript holds more than this many entries; `0` disables |
| `ARCVIZ_CHAT_KEEP` | `30` | entries kept verbatim after compaction (same unit as `history_turns`) |

Constructor: `DiagramAgent(provider, store, max_rounds=12, history_turns=30, compact_after=60, keep=30, background=True)`.
`keep` is clamped to at least 1.

## Testing

All with `ScriptedProvider` (its `_complete` serves the summary call too; a reply with `text` and no
tool calls is the summary).

- Below the threshold nothing is compacted; above it the chat becomes `[summary] + tail`, the tail
  starts with a user entry, `seq` values are preserved, the summary has `seq == upto_seq` and correct
  `covers`.
- Cumulative: a second compaction's prompt contains "Previous summary:" and the stored `turns` adds up.
- `_history()` emits summary + "Understood." + tail; a provider request records it.
- Provider failure during compaction leaves the chat unchanged and the turn's events intact.
- Server: manual endpoint returns `compacted` and the chat; `GET` shows the summary entry; clear
  removes it.
- Contract tests for `compact_chat` live in the persistence suite.
- e2e: a diagram with a seeded long transcript shows the collapsed summary block.

## Decisions

1. Summary is cumulative (ADR-0005).
2. Background execution via a single-worker executor in the agent; inline in tests and for the
   manual endpoint.
3. The kept tail always starts with a user entry.
4. Added during implementation: `drain()`, the 502 for a failed manual compaction, `keep >= 1`.
5. Review findings on #35: `complete_text` rejects `max_tokens`; the prompt cap shortens the fold
   instead of dropping entries.

### Transcript generation (#67)

`DiagramRepository.chat_generation(id) -> str` is an opaque random token per transcript (`""` for an
unknown diagram, which creates nothing). Backends store it next to the transcript: `file` in
`diagrams/<id>/chat-generation`, `sqlite` in the column `diagrams.chat_generation` (added to older
databases on start-up), `cosmos` in the item `chat-generation` of the diagram's partition. A
transcript from before generations existed gets one on first read. The check is atomic with the fold:
under the chat lock (`file`), in the `BEGIN IMMEDIATE` transaction (`sqlite`), or as a replace of the
generation item conditional on its ETag inside the fold's transactional batch (`cosmos`; for a staged
fold in the first batch, together with the summary). Rejected alternative: keeping `seq` monotonic
across a clear with a persistent counter; it does not cover delete and re-create, whose counter starts
again (ADR-0046).

## Open questions

None.
