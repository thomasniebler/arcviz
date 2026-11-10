# ADR-0217: View operations keep the tool vocabulary; a removal names its scope

- Status: Proposed
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`

## Context

Prompts, tools, evals, the REST `/ops` route and the UI use operations such as `add_node`, `update_node`,
`remove_node`, `add_edge`, `add_message`. In a model-first design removing something from a diagram can mean hiding
it in that view or deleting it from the model. The owner decided that the UI asks each time and that the chat
assistant only hides and proposes model deletions.

## Options

1. **New vocabulary** (`create_element` + `show_element` only). Clean, but every prompt, eval and UI action changes.
2. **Keep the vocabulary as compounds** planned on view and model; `remove_*` require `scope: view|model`.

## Decision

Option 2: `operations.py` keeps its names and plans each call into model and view steps; `scope` has no default.

## Consequences

- Removal without `scope` is a 400; the UI dialog always asks, the chat tools accept only `view` and offer a
  proposal for the model deletion; MCP tools have no model removal (ADR-0219).
- `add_node` reuses an element of the same name and metaclass before creating one (the matching of the former
  adopter).
- `link_*`, `sync_*`, `clear_*`, `set_relation_hidden`, `set_completeness` are removed.
