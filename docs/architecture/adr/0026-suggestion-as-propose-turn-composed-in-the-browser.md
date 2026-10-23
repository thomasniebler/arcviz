# ADR-0026: A review suggestion becomes an ordinary propose-mode chat turn composed in the browser

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/review-suggestions.md`

## Context

Issue #97 asks to turn a suggestion of the architecture review into a propose-mode turn (M7) so the
user sees the diff preview and confirms or discards it. Findings come from rules and from the LLM
and carry a German `suggestion` phrased as an instruction naming element ids. Propose mode is a
dry run of a chat turn whose proposal the client holds and sends back (ADR-0015). Reviews are not
stored on the server (ADR-0024). Feature modules of the frontend must not import `chat.js`
(ADR-0020).

## Options

1. **Compose the instruction in the browser and send it through the existing chat endpoint** with
   `mode: "propose"`. No API change; the transcript shows exactly what was asked; the review panel
   gets a callback from `app.js` to start the turn.
2. **A new endpoint `POST /review/apply`** that takes the finding (reviews are not stored, so the
   client would send it back) and builds the instruction on the server before running a propose
   turn. Keeps the wording in Python, but duplicates the chat endpoint's SSE handling for one
   string.
3. **Deterministic fixes**: rules carry the operations that fix them, applied as a proposal without
   the LLM. Precise for some rules (add a replica), impossible for LLM findings and for rules whose
   fix depends on intent (which gateway?), so a second mechanism would still be needed.

## Decision

Option 1, because it reuses propose mode and the chat endpoint unchanged and works for every
finding with a suggestion.

## Consequences

- The wording of the instruction lives in `static/findings.js` (pure, unit-tested); the server sees
  an ordinary chat message.
- Applying always previews, independent of the *Vorschau* checkbox.
- How the suggestion is realised is up to the model; the preview is the safeguard.
- Stale LLM findings (the diagram changed since the review) cannot be applied until the review is
  run again.
