# ADR-0193: The requirements chat keeps its transcript in its own keyed document

- Status: Accepted
- Date: 2026-10-21
- Spec: `docs/architecture/specs/requirements-workshop.md`

## Context

The design said the requirements chat would use the project transcript with a `scope` tag and per-scope history, compaction
and clearing, to be checked against `compact_chat` when the chat was built. The check: the transcript primitives
(`append_project_chat`, `compact_project_chat`, `chat_generation`) fold and clear the **whole** project transcript, and the
generation counter is shared; a requirements chat in the same transcript would be folded into (and cleared with) the project
chat's summary, and the project chat's history would show requirement turns.

## Options

1. **A `scope` tag in the project transcript.** No new document; compaction, clearing and history would all need a scope
   argument in three backends' primitives and the generation guard (ADR-0046).
2. **New store primitives for a second transcript.** Clean; every backend and the store contract get new methods.
3. **A keyed document `reqchat` per project** (the generic document primitives every backend has): the newest 60 entries,
   each cut to 8 000 characters, the project's incarnation, and a `generation` that a clear replaces.

## Decision

Option 3. The requirements chat's turns are short (tool results are not stored), 60 entries bound the document far below the
item limit, and no store changes are needed. There is no LLM summary: the oldest entries are dropped when the document is
full.

## Consequences

- `RequirementChatService` (`services/requirement_chat.py`); `reqchat` is a new document kind (store, telemetry list).
- A requirements conversation older than 60 entries is forgotten (the requirements themselves, the suggestions and the
  proposals are not); the project chat keeps its own compaction.
- If a later change wants the summary, it belongs in the same document.
