# ADR-0191: The elaboration mode is a main view with a requirements table and a scoped chat

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/requirements-workshop.md`

## Context

The elaboration mode works on 200 to 500 requirements in four steps (pour in, refine, derive, check coverage). The
current UI has one main view (the diagram stage) with a chat pane whose scope is `diagram` or `project`
(ADR-0057). The **Architektur** dialog (layer 6) holds the drivers, scenarios, decisions and stakeholders as tabs. The
idea was a "project chat, but as the main view".

## Options

1. **Another tab of the Architektur dialog.** Cheapest; a modal is the wrong place for a working surface of hundreds of
   rows, and the chat is not next to it.
2. **A chat-only main view** (the transcript is the interface). Natural for pouring in text; no sorting, filtering,
   selection or bulk accept, and 500 items cannot be reviewed as chat messages.
3. **A main view "Anforderungen" beside the diagram stage**: a table (filter by status, theme, kind, stakeholder;
   multi-select; a detail panel; suggestions shown next to the fields in force) with the pipeline steps as actions, and
   the existing chat pane with a third scope `requirements` as the steering channel.

## Decision

Option 3. The chat remains where free text is entered and where the assistant explains; the table is where
volume is handled and where people accept. This was a gut feeling of the product owner ("maybe even its own main chat
window"); the product owner confirmed the recommendation on 2026-10-20.

## Consequences

- A new view switch in the top bar, `state.mainView` (`"diagram" | "requirements"`), a feature module with a pure helper
  module, and a new `chatScope` value; the existing scopes are unchanged.
- The Architektur dialog stays the place for drivers, scenarios, decisions and stakeholders; it gains the requirement
  references (a picker) and a coverage column.
- If option 2 wins instead, the table moves into the chat as renderable blocks, which is a much larger frontend
  change; decide before the UI issue starts.
