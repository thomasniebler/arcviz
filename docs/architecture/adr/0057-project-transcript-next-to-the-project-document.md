# ADR-0057: A project transcript next to the project document, through new store primitives

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-chat.md`

## Context

The project chat (#135) needs a transcript with the semantics of a diagram transcript: a `seq` that is
never reused, a generation that a clear replaces, compaction into a cumulative summary guarded by that
generation (ADR-0005, ADR-0046). Diagram transcripts are store primitives keyed by a diagram id that must
exist. Project documents (ADR-0051) are written conditionally on a revision. All persistence goes through
the services (ADR-0050).

## Options

1. **Inside the project document.** No new primitives, but every turn would rewrite the project and
   conflict with structure edits (revision), and the document would grow without bound.
2. **A hidden diagram per project** holding the chat. Reuses everything, but pollutes diagram listings,
   the membership repair and the version history, and couples a project to a fake diagram id.
3. **Own primitives keyed by the project id** (`load/append/compact/clear_project_chat`,
   `project_chat_generation`), implemented per backend with the diagram transcript's helpers, deleted with
   the project, exposed by `ProjectService` with `DiagramService`'s method names.

## Decision

Option 3: the transcript keeps the exact contract of a diagram transcript and the project document stays
small and conflict-free; the cost is five primitives per backend, which share their logic with the diagram
transcript.

## Consequences

- `tests/store_contract.py` covers both transcripts with the same checks, on every backend.
- The agent's compaction works on any service with `chat`/`chat_generation`/`compact_chat`.
- A project re-created under the id of a deleted one starts with an empty transcript (Cosmos DB: best
  effort, like `purge`).
