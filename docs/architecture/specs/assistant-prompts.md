# Domain-expert system prompts per diagram type

- Status: Implemented (milestone M19)
- Issue: #125
- ADR: [0065 System prompts from shared blocks and per-type expert parts in text files](../adr/0065-system-prompts-from-shared-blocks-and-expert-parts.md)
- Builds on: [Diagram types](diagram-types.md), [Offline prompt/tool eval set](agent-evals.md),
  [Applying a review suggestion via propose mode](review-suggestions.md)
- Related: [Topic scope of the assistant](topic-scope.md) (#124, the scope block is one of the shared blocks)

## Summary

Each diagram type has its own system prompt (`agent.SYSTEM_PROMPT` for deployment,
`agent_types.SYSTEM_PROMPTS[type]` for the others). They are tool manuals: notation, how to call the
operations, a few examples. They do not ask the assistant to act as an expert who helps *design* a good
diagram, and the deployment prompt has no UML notation section at all. The common rules (ids, batching,
tool errors, clarifying questions, reply language) are copied three times with small differences.

This change composes every prompt from **shared building blocks** (persona, topic scope, working rules,
modelling hints, reply style) plus a **per-type expert part** (what the diagram is, UML 2.5.1 notation,
modelling guidance, tool tips, examples). The texts move into plain text files under `arcviz/prompts/`, so
they can be reviewed and diffed as prose, and the blocks can be reused by other prompts (for example a
project chat) without copying.

## Goals

- One prompt per type in `DIAGRAM_TYPES`, made of the shared blocks plus the type's expert part; a table
  test checks every type.
- Expert guidance per type: deployment (UML 2.5.1 ch. 19 plus cloud practice), activity (ch. 15), sequence
  (ch. 17), as listed in the issue.
- Proactive but not pushy: after a change at most **one** short, concrete modelling hint, never applied
  unasked.
- Guided start: on an empty diagram the assistant asks the two or three questions that matter for the
  type instead of guessing a large model.
- Prompt caching keeps working: the prompt is a stable prefix (depends only on the type and the kind list)
  followed by the interpolated diagram summary.
- Public entry points stay: `agent.build_system_prompt(diagram, context_budget)` and
  `agent.SUMMARY_SYSTEM_PROMPT`.

## Non-goals

- New tools, operations or provider changes. The Anthropic provider keeps one cached system block.
- A project-chat prompt (milestone M17); this change only provides the reusable blocks.
- Prompt templating features (conditionals, includes) or user-editable prompts.
- Localising the prompts; they stay English, the reply language follows the user.

## Design

### Files and module

```
arcviz/prompts/
  __init__.py            # loads the texts, composes prompts
  shared/persona.txt     # role: software architect and UML 2.5.1 modeller
  shared/scope.txt       # topic scope (#124, spec topic-scope.md)
  shared/working.txt     # tools, ids, batching, tool errors, clarification
  shared/hints.txt       # at most one modelling hint, guided start on an empty diagram
  shared/readability.txt # structure the automatic layout can draw: plan, batch in reading order, grouping, few edges
  shared/reply.txt       # reply language and length
  types/deployment.txt   # expert part per diagram type
  types/activity.txt
  types/sequence.txt
  project/chat.txt       # the project chat's part (M17, spec project-chat.md)
```

`shared/readability.txt` (`prompts.READABILITY`, block 5 of the shared blocks) addresses diagrams that are hard to
read: the automatic layout can only draw the structure the model has, so for what is asked for the assistant plans the
whole structure first, builds several new elements in one `apply_batch` in reading order, groups related new elements
where the type has containers, adds only meaningful edges, picks the direction with `set_direction` where the type has
that tool, and keeps names and labels short. It never overrides the modelling hints or the type's own part (notation, modelling guidance, nesting): it adds no elements to tidy edges,
does not cut a request down or split a diagram (it may suggest a split once) and changes nothing that was not asked for.
It is a prompt rule, not a validation. Its effect is not covered by the eval set yet (a follow-up: cases that count
elements and crossings); it has to be judged on real diagrams.

The files ship in the wheel and the Docker image because they live under `arcviz/` (hatch packages the
whole directory). They are read once at import time with `importlib.resources`.

`arcviz/prompts/__init__.py` exposes:

| Name | Meaning |
|---|---|
| `PERSONA`, `TOPIC_SCOPE`, `WORKING_RULES`, `MODELLING_HINTS`, `READABILITY`, `REPLY_STYLE` | the shared blocks (stripped text, no placeholders) |
| `SHARED_BLOCKS` | the shared blocks in prompt order |
| `EXPERT_PARTS: dict[str, str]` | diagram type → expert part; may contain the placeholder `{kinds}` |
| `STATE_HEADING` | `"Current state:"`, the line before the summary |
| `compose(*blocks) -> str` | joins blocks with one blank line; for other prompts that reuse the blocks |
| `prompt_prefix(diagram_type, kinds) -> str` | the stable part: `compose(*SHARED_BLOCKS, expert)` with `{kinds}` replaced |
| `system_prompt(diagram_type, kinds, summary) -> str` | `prefix + "\n\n" + STATE_HEADING + "\n" + summary + "\n"` |
| `PROJECT_PART`, `project_prompt_prefix()` | the project chat's part (M17) and its stable prefix `compose(*SHARED_BLOCKS, PROJECT_PART)` |
| `diagram_part(diagram_type, kinds, summary) -> str` | expert part plus state without the shared blocks: the active diagram inside the project chat's prompt and the `open_diagram` result |

An unknown type raises `KeyError` (as `agent_types.SYSTEM_PROMPTS[type]` did). Placeholders are replaced
with `str.replace`, not `str.format`, so the prose can contain literal braces (JSON in examples) without
escaping, and the summary (user text) is appended after the replacement, so it is never interpreted.

`agent.build_system_prompt(diagram, context_budget)` becomes
`prompts.system_prompt(diagram.type, ", ".join(sorted(kinds_for(diagram.type).all_kinds)), diagram.summary(budget=context_budget))`.
`agent.SYSTEM_PROMPT` and `agent_types.SYSTEM_PROMPTS` are removed; nothing outside these modules used them.
`agent_types.py` keeps the tool builders only.

The module depends on nothing in `arcviz` (pure text), so any prompt builder may import it. The module
table in `docs/architecture/index.md` gets a row.

### Prompt order and caching

```
PERSONA · TOPIC_SCOPE · WORKING_RULES · MODELLING_HINTS · READABILITY · REPLY_STYLE · <expert part of the type> · Current state: <summary>
```

The shared blocks come first, so every type (and a future project chat) shares the same leading text; the
expert part follows with its notation, guidance and examples close to the state. Everything before
`Current state:` depends only on the type and the active kind catalogue, so within a turn every round sends
an identical system block, which is what the provider's `cache_control` on the system block caches.

The project chat (M17, [project chat](project-chat.md)) uses the same leading blocks:

```
PERSONA · TOPIC_SCOPE · WORKING_RULES · MODELLING_HINTS · READABILITY · REPLY_STYLE · PROJECT_PART · Project: <title, description, structure> · Active diagram: <expert part of its type> · Current state: <summary>
```

so it declines off-topic requests like the diagram chat, and everything up to `PROJECT_PART` is one
prefix for every project.

### Shared blocks (content)

- **Persona:** Arcviz, an experienced software architect and UML 2.5.1 modeller who helps the user design
  and edit a diagram through tools; precise UML terms, practical architecture judgement.
- **Topic scope:** see [topic-scope.md](topic-scope.md).
- **Working rules:** read the request, make the tool calls, independent calls in one round, several related
  elements in one `apply_batch`; address elements by id; prefer modifying over duplicating; ask one short
  clarifying question when ambiguity changes the result; read-only questions need no tools; a tool error
  means "not applied" (fix the call or explain); the state below is current for this turn.
- **Modelling hints:** after a change, at most one short, concrete hint phrased as a question
  ("Die Datenbank ist von außen erreichbar – soll ich sie in ein privates Subnetz verschieben?"), only when
  it matters, never applied in the same turn; a hint the user accepts is an ordinary next turn (and works
  with propose mode and review suggestions, ADR-0026). On an empty diagram with an open-ended request
  ("wo fange ich an?") ask the two or three type-specific questions from the expert part instead of
  building a large model; a concrete build request is executed.
- **Reply style:** the user's language; short (a brief confirmation of what changed or that nothing
  changed); no full model, Mermaid, SVG or JSON unless asked.

### Expert parts (content)

Each starts with what the diagram is and the available kinds, then:

- **Deployment** (UML 2.5.1 ch. 19 plus cloud practice): nodes (devices, execution environments) vs.
  artifacts deployed on them vs. communication paths; how the Arcviz kinds map to UML (`node`, `cluster`
  as execution environments, `artifact`, `deploys` as Deployment); network segmentation with
  `region`/`vnet`/`subnet`; ingress and egress through gateways; redundancy and availability zones; data
  stores and their access paths (private, through the service that owns them); protocol and port as edge
  properties, not in names. Guided start: cloud or on-premises (which provider), which environments, the
  main services and data stores. The existing examples stay.
- **Activity** (ch. 15): start from a use case or trigger and its result; one swimlane per responsible
  role or system; every decision has complete guards (including `else`); fork/join in pairs; avoid
  spaghetti flows (merge before continuing, one level of abstraction per diagram, refine with a separate
  diagram). Guided start: the trigger and the result, the participants (swimlanes), the main path. The
  existing notation rules and examples stay.
- **Sequence** (ch. 17): one scenario per diagram; choose the participants (actors left); synchronous calls
  answered by replies; `alt` for alternatives, `opt` for optional parts, `loop` for repetition; extract a
  reused part as `ref`; keep activations meaningful. Guided start: the scenario (trigger and outcome),
  the participants, the happy path vs. error cases. The existing notation rules and examples stay.

## Testing

- `tests/test_prompts.py` (new): a table test over `DIAGRAM_TYPES`: every type has an expert part, and
  `build_system_prompt` for an empty diagram of the type contains every shared block verbatim, in order,
  followed by the expert part and `Current state:`; no `{kinds}` placeholder is left; the prefix before
  `Current state:` is identical for two different diagrams of the same type (caching) and the summary is
  the last part; literal braces survive; the per-type guidance keywords are present (ch. 19/15/17,
  availability zones, guards, `ref`).
- Existing tests (`test_agent.py`, `test_activity.py`, `test_sequence.py`, `test_kinds.py`,
  `test_diagram_types.py`) keep passing unchanged.
- A `ScriptedProvider` test: the system prompt sent to the provider is `build_system_prompt` of the
  diagram at the start of the turn and is the same in every round of the turn.
- Eval cases (`tests/evals/cases.json`, run with `make eval`) per type for design help on an empty
  diagram: the diagram stays unchanged, the reply asks a question and names a type-relevant topic. One
  case checks that a requested change is done without unrequested extras (`max_operations`). The harness
  gains an optional case key `type` (diagram type of the `empty` template).

## Migration / compatibility

No stored data changes. `agent.SYSTEM_PROMPT` and `agent_types.SYSTEM_PROMPTS` disappear (internal names);
`build_system_prompt` keeps its signature. Prompts get longer (roughly 1k more tokens per type, about 7 000 characters before the summary); the stable prefix
is cached by the Anthropic provider.

## Open questions

None open; the decisions below were made without the issue author (ADR-0065 is *Proposed*).

## Decisions

- Text files under `arcviz/prompts/` rather than Python constants (the issue's suggestion; ADR-0065).
- Shared blocks before the expert part (common prefix across types and future prompts).
- `str.replace` instead of `str.format` (no brace escaping in prose).
- Hints and guided start are prompt rules only; no server-side detection of empty diagrams.
- An open-ended "where do I start" request on an empty diagram leaves the diagram unchanged (asks
  questions); a concrete build request is executed as before (eval `build-from-empty` stays).
