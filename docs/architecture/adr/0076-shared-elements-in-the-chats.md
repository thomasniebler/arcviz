# ADR-0076: Shared elements in the diagram chat and the project chat

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

The diagram chat edits one diagram, the project chat one active diagram at a time (ADR-0058); propose
mode previews changes of one diagram (ADR-0060). A model rename writes the project and other diagrams.

## Options

1. **Model only through the UI.** The assistants could not use the shared model.
2. **Element tools in both chats**: `list_elements`, `create_element`, `update_element`,
   `link_element`, `unlink_element`, offered when the diagram belongs to a project; the elements are listed
   in the prompt after the stable prefix (budgeted), with a shared block from `arcviz/prompts/`.

## Decision

Option 2. The conservative choices: `create_element`, `update_element` and `link_element` are refused in
propose mode (a proposal is one diagram's operations; model writes and internal operations cannot be part
of it), and after a propagating rename the agent re-reads every diagram it holds instead of patching its
copies.

## Consequences

- The turn's final version of the active diagram is conditional on the re-read revision; the propagation
  wrote its own version before.
