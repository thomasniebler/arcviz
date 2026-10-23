# ADR-0065: System prompts from shared blocks and per-type expert parts in text files

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/assistant-prompts.md`

## Context

Three diagram types each have a system prompt as a Python string (`agent.SYSTEM_PROMPT`,
`agent_types.SYSTEM_PROMPTS`). The common rules are copied into each one and already differ in wording.
Issue #125 asks for a persona and real modelling guidance per type, built from shared building blocks, and
suggests moving the prompts into `arcviz/prompts/` so they can be reviewed as prose. A project chat
(milestone M17) will need the same persona, scope and working rules. The Docker image copies only
`arcviz/`. The prompts are `str.format` templates, so every literal brace in an example must be doubled.

## Options

1. **Keep Python constants, factor out shared strings.** No packaging question; prose stays inside string
   literals with escaped braces and line-length constraints, and diffs mix code and text.
2. **Plain text files under `arcviz/prompts/` with a small composing module.** Prose is reviewable as
   text, blocks are reusable by name, placeholders are replaced explicitly (`str.replace`), so braces need
   no escaping. Costs one resource-loading module and the rule that the files must ship with the package
   (they do: hatch packages the whole `arcviz/` directory).
3. **A template engine (Jinja) for includes and conditionals.** More power than needed and a new runtime
   dependency (CLAUDE.md rule 5).

## Decision

Option 2: shared blocks (persona, topic scope, working rules, modelling hints, reply style) and one expert
part per diagram type as text files in `arcviz/prompts/`, composed in a fixed order with the shared blocks
first and the diagram summary last.

## Consequences

- Every type's prompt shares one leading text; a new type adds one `types/<type>.txt` and the table test
  fails until it exists.
- Other prompts (the project chat) reuse the blocks through `prompts.compose(...)`.
- The stable prefix (type + kind list) stays in front of the per-turn summary, so prompt caching behaves
  as before.
- `agent.SYSTEM_PROMPT` and `agent_types.SYSTEM_PROMPTS` are removed; `build_system_prompt` is the entry
  point.
- Status *Proposed*: decided without the issue author while implementing M19.
