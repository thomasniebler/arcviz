# ADR-0061: The project chat is a scope of the chat pane, opened from the navigator

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-chat.md`

## Context

The UI has one chat pane next to the canvas (layout and splitter, ADR-0020) and a navigator column
(ADR-0055). A project turn changes the shown diagram and possibly others. The frontend layers forbid the
navigator from importing `chat.js`.

## Options

1. **A second chat pane** for the project. Doubles the composer, voice input and proposal handling.
2. **A scope of the existing pane**: a **Projekt-Chat** button in the navigator switches the pane between
   the diagram transcript and the project transcript (`state.chatScope`); a scope bar shows which one is
   active; the canvas follows the active diagram; changed diagrams are listed under the reply and marked in
   the tree until opened. `chat.js` binds the navigator's button by id and uses navigator hooks.
3. **A modal dialog** for project conversations. Hides the canvas, so live renders are not visible.

## Decision

Option 2 (Proposed): one composer, voice and proposal flow for both chats, and the live canvas stays
visible.

## Consequences

- Opening a diagram does not replace the transcript while the project scope is active.
- In an empty project the project scope keeps the chat usable (only the canvas is inert), so the chat can
  create the first diagram.
- **Chat leeren** clears the transcript of the current scope.
- The e2e test switches scopes through the navigator button.
