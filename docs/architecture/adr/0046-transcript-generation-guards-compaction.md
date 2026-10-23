# ADR-0046: A transcript generation guards chat compaction

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/chat-compaction.md` (section *Transcript generation*)

## Context

Compaction reads the transcript, asks the model for a summary (seconds), and then calls
`compact_chat(id, upto_seq, summary)`, which only checks that an entry with `seq == upto_seq` still
exists. `clear_chat` restarts `seq` at 1 in every backend (file and SQLite allocate `max + 1`, Cosmos DB
queries the maximum). A clear followed by new messages while the summary is being produced therefore
recreates `upto_seq`, and the stale summary replaces the fresh messages (#67, reproduced with the file
and SQLite stores). Deleting a diagram and creating it again under the same id has the same effect.

## Options

1. **Monotonic `seq` across clears**, with a persistent counter per diagram. Fixes the clear, but the
   counter is deleted with the diagram, so delete and re-create still collide; Cosmos needs a counter
   item updated on every append (a hot item and an extra write per message).
2. **A transcript generation**: an opaque random token per transcript that `clear_chat`,
   `import_chat` and deletion replace; `compact_chat` takes the generation read before the transcript
   and refuses a mismatch atomically with the fold. One small value per diagram, one extra read per
   compaction, nothing on the append path.
3. **Compare the content of the anchor entry.** No storage change, but identical messages ("ok") after
   a clear would still match.

## Decision

Option 2: it covers the clear, the import and delete/re-create with one mechanism, keeps `seq`
semantics (and the existing contract test that a cleared chat starts at 1), and costs nothing per
message.

## Consequences

- New repository primitive `chat_generation(id)`; `compact_chat` gains an optional `generation`.
  Every backend stores the token next to the transcript and creates one lazily for old data (SQLite
  adds a nullable column on start-up).
- The agent must read the generation *before* the transcript; reading it after could pair a new
  generation with old entries.
- A compaction that races with a clear is skipped and retried after the next turn, as before.
