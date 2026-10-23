# ADR-0093: Notes in the assistant's prompt are budgeted before containers collapse

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`

## Context

The diagram summary in the system prompt has a character budget (`ARCVIZ_CONTEXT_BUDGET`); over budget, the
largest containers collapse. Notes can be long (up to 2000 characters each) and would otherwise push structure out
of the prompt.

## Options

1. **Full notes always.** Simple; a few long notes collapse the structure the model needs most.
2. **No notes in the summary, only via `get_diagram`.** The model would not know notes exist.
3. **Stepwise:** each note at most 200 characters; over budget shorten to 40, then replace by a hint to call
   `get_diagram`, and only then collapse containers.

## Decision

Option 3: structure beats prose, and the model always learns that a note exists.

## Consequences

The full text is one `get_diagram` call away. The summary of a diagram without notes is unchanged.
