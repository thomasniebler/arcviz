# Agent

Source: `arcviz/agent.py`. Tests: `tests/test_agent.py`.

## Tools

The tools and the system prompt depend on the diagram type: `build_tools(catalog, diagram.type)` and
`build_system_prompt(diagram)`; this section describes the deployment tools (`TOOLS`), other types'
tools live in `arcviz/agent_types.py` (see [Diagram types](diagram-types.md#per-type-tables)), all prompts in
`arcviz/prompts/` (see [System prompt](#system-prompt)). Live and
final events carry the render payload of `render.payload` (`renderer`, `mermaid`, `svg`, `mermaid_ids`).

`TOOLS` is the list of `ToolSpec`s offered to the model. Ten map 1:1 to operations
(`add_node`, `update_node`, `move_node`, `remove_node`, `add_edge`, `update_edge`,
`remove_edge`, `rename_diagram`, `set_direction`, `apply_batch`) with JSON schemas that enumerate the
allowed kinds (and directions) and set `additionalProperties: false`. `apply_batch` takes `operations`, a list (1 to 50 items) of
`{"op", "args"}` objects whose `op` enum is every other operation; a batch is one tool call, one
`save_latest`, one entry in `applied` and one `tool_result` event, and is all or nothing. The eleventh,
`get_diagram`, returns diagram JSON via `Diagram.subset()`: the whole diagram
without arguments, a container's subtree with `focus`, or selected elements (plus ancestors) with
`ids`; an unknown `focus` is a tool error. The description tells the model to use it for
containers marked as collapsed in the summary and whenever the summary is not detailed enough.

`TOOLS` describes the built-in kinds. `build_tools(catalog)` returns it unchanged for
`BUILTIN_KINDS`, otherwise a deep copy whose `kind` enums (node tools: `all_kinds`, edge tools:
`edge_kinds`) and kind lists in the descriptions come from the catalogue; `DiagramAgent.chat`
passes `build_tools(active_kinds())` to the provider and the system prompt lists
`active_kinds().all_kinds`.

`apply_batch`'s `op` enum is `operations.batch_operations(type)`, the same list a REST batch accepts: every
operation of the type except `apply_batch` and the internal model operations (`unlink_element` may be a step).

Tool descriptions carry the behavioural hints the model needs: containers vs leaves, "move keeps
edges", idempotent edges, cascade semantics. `add_edge` and `update_edge` take `direction`
(`enum: forward, both, none, default`; it overrides the kind's arrow heads) and `properties`
(protocol, port, …, shown on the edge); `update_edge` also `remove_properties`.

## System prompt

Source: `arcviz/prompts/` (`__init__.py` plus the texts in `shared/*.txt` and `types/<type>.txt`).
Tests: `tests/test_prompts.py`. Design: [Domain-expert system prompts](../architecture/specs/assistant-prompts.md),
ADR-0065; [Topic scope](../architecture/specs/topic-scope.md), ADR-0066. The scope is enforced by the prompt
only: there is no classifier call and no configuration switch.

`build_system_prompt(diagram, context_budget)` returns
`prompts.system_prompt(diagram.type, kinds, diagram.summary(budget=context_budget))`, where `kinds` is the
comma-separated `kinds_for(diagram.type).all_kinds` (empty for sequence diagrams). The summary uses
`context_budget` (default `DEFAULT_CONTEXT_BUDGET` = 12 000 characters, from `ARCVIZ_CONTEXT_BUDGET`;
`None`/`0` disables collapsing); see [Domain model: budget](domain-model.md#budget) for the collapse and cut rules.
Since #115 the budget holds for every diagram shape: what collapsing cannot shorten (a flat diagram, a sequence) is
cut with counts and a hint to call `get_diagram`, so the summary is at most `context_budget + SUMMARY_SLACK`
characters.

Every prompt has the same structure, blocks joined by a blank line (`prompts.compose`):

| Order | Block | Content |
|---|---|---|
| 1 | `PERSONA` (`shared/persona.txt`) | Arcviz as an experienced software architect and UML 2.5.1 modeller who helps design and edit the diagram through tools |
| 2 | `TOPIC_SCOPE` (`shared/scope.txt`) | topic scope (#124, [design](../architecture/specs/topic-scope.md), ADR-0066): in scope are the diagram, software/system architecture, UML 2.5.1, cloud/infrastructure/deployment, modelling questions and Arcviz itself, including short explanations of architecture concepts without a change; anything else is declined in one short sentence in the user's language, **without tool calls**, with an offer to get back to the model; an off-topic request disguised as a change is declined as a whole (diagram unchanged); a mixed message gets its in-scope part done; when unsure, in scope |
| 3 | `WORKING_RULES` (`shared/working.txt`) | make the tool calls; independent calls in one round; related elements in one `apply_batch` (all or nothing); address elements by id (square brackets in the state); prefer modifying over duplicating; one short clarifying question when ambiguity changes the result; read-only questions need no tools; a tool error means "not applied" |
| 4 | `MODELLING_HINTS` (`shared/hints.txt`) | change only what the request is about; after a change at most one short, concrete modelling hint as a question, never applied in the same turn; *guided start*: on an empty diagram with an open-ended request ask the type's two or three questions and make no tool calls; a concrete build request is carried out |
| 5 | `READABILITY` (`shared/readability.txt`) | applies only to what is asked for and never overrides the rules above: plan the structure first and build several new elements in one `apply_batch` in reading order (containers where the type has them, then elements upstream to downstream, then edges); build what was asked, group related new elements, at most three levels, may suggest once to split past about 15 elements, never splits itself; only meaningful edges, no elements added to tidy edges; `set_direction` only where the type has it; short names and edge labels, details in properties or notes |
| 6 | `REPLY_STYLE` (`shared/reply.txt`) | the user's language; short confirmations and answers; no model dump, Mermaid, SVG or JSON unless asked; Markdown where it helps reading (the chat renders it, [chat-markdown](../architecture/specs/chat-markdown.md)), never HTML |
| 7 | `EXPERT_PARTS[type]` (`types/<type>.txt`) | what the diagram is and `Available kinds: {kinds}`, the UML 2.5.1 notation (deployment ch. 19, activity ch. 15, sequence ch. 17), modelling guidance, tool tips, the type's *Guided start* questions and examples |
| 8 | `Current state:` + summary | the diagram summary at the start of the turn |

`prompts.SHARED_BLOCKS` holds blocks 1–6 in this order, so other prompts can reuse them with `compose`.
`prompt_prefix(type, kinds)` is blocks 1–7; it depends only on the type and the kind list, so every round
of a turn sends an identical system prompt and the Anthropic provider's `cache_control` on the system block
applies. Placeholders (`{kinds}`) are replaced with `str.replace`; the summary is appended afterwards and
never interpreted, and the texts may contain literal braces. An unknown type raises `KeyError`.

Per-type modelling guidance:

- **Deployment:** nodes and execution environments vs. deployed artifacts (`deploys`), communication
  paths, segmentation region > vnet > subnet, ingress through gateways and explicit egress, redundancy
  across availability zones and replication, data stores reached privately through their owning service,
  protocol/port as edge properties. Examples: container plus `move_node` in the next round, `update_node`
  with name and property, `remove_node` with and without `cascade`, a clarifying question, a read-only
  answer. Guided start: cloud or on-premises, environments, main services and data stores.
- **Activity:** start from a use case or trigger and its result, one swimlane per responsibility,
  complete guards, fork/join in pairs, merge instead of spaghetti flows, one level of abstraction. Guided
  start: trigger and result, participants, main steps.
- **Sequence:** one scenario per diagram, deliberate participants (initiating actor left), synchronous
  calls answered by replies, `alt`/`opt`/`loop`/`break`, reused parts as `ref`. Guided start: scenario,
  participants, normal case only or also error cases.

The current state is always the state at the start of the turn; intermediate changes reach the model
through tool results.

## `DiagramAgent`

```python
DiagramAgent(
    provider,
    diagrams,  # DiagramService (arcviz/services/), never the repository
    max_rounds=12,
    history_turns=30,
    context_budget=12_000,
    compact_after=60,
    keep=30,
    background=True,
    token_budget=None,
)
agent.chat(diagram_id, user_message, focus=None, propose=False)
agent.bound(diagrams, models)  # a copy on the services of one caller
```

**Callers** ([personal workspaces and sharing](../architecture/specs/personal-workspaces.md)): the server keeps one agent
and uses `agent.bound(svc.diagrams, svc.models)` per request, a shallow copy on the services bound to the caller
that shares the provider, the configuration and the background compaction (`_Compaction`: one single-worker
executor and its futures). `chat` first checks the editor role (`diagrams.require(id, "editor")`), so a viewer's
turn, also in propose mode, raises `Forbidden` before the first event; an invisible diagram `DiagramNotFound`.

`token_budget` (`ARCVIZ_TURN_TOKEN_BUDGET`, `0`/`None` = no cap) is passed to `run_turn` (see
[LLM layer](llm-layer.md)); a budget stop is an ordinary `error` of the turn.

`focus` is the node the UI is zoomed into; every `mermaid` in the stream is rendered with it
(falling back to the whole diagram if the node disappears during the turn) and the events carry
the effective `focus`.

### History

`_history(diagram_id)` converts the stored transcript into `ChatMessage`s (user/assistant with
non-empty content only) and keeps the last `history_turns` entries (`ARCVIZ_HISTORY_TURNS`,
default 30). If the transcript starts with a
`summary` entry (see [Chat compaction](#chat-compaction)), it is emitted first as a user message
`Summary of the earlier conversation:\n<content>` followed by an assistant `Understood.`, so the roles
keep alternating.

### Chat compaction

Design and decision: [Architecture: chat compaction](../architecture/specs/chat-compaction.md),
ADR-0005. Tests: `tests/test_compaction.py`.

After the transcript append at the end of a turn, `_maybe_compact(diagram_id)` checks
`len(chat) > compact_after` (`ARCVIZ_CHAT_COMPACT_AFTER`, `0` disables) and runs `compact(diagram_id)`
on a single-worker `ThreadPoolExecutor` owned by the agent (`background=True`; tests use
`background=False` and `drain()` waits for pending runs). A failure is logged and never affects the
turn; the next turn retries.

`compact(diagram_id)` returns `False` when there is nothing to fold, otherwise:

1. The kept tail starts `keep` entries (`ARCVIZ_CHAT_KEEP`, at least 1) from the end, moved back to
   the nearest `user` entry; the fold before it must contain at least one complete turn.
2. The fold is rendered as `user: …` / `assistant: … [op target, …]` lines (operations compressed
   via `_describe_op`), preceded by `Previous summary:` when the fold starts with a summary entry. If
   the text exceeds `SUMMARY_INPUT_LIMIT` (60 000 characters) the fold is shortened from its newer
   end until it fits, so nothing is deleted unsummarised.
3. `provider.complete_text(agent.summary_prompt, text)` (`SUMMARY_SYSTEM_PROMPT`; the project agent's
   `PROJECT_SUMMARY_SYSTEM_PROMPT`) produces the summary; `refusal`,
   `max_tokens` or an empty reply raise and leave the chat unchanged.
4. `transcripts.compact_chat(diagram_id, upto_seq, summary, generation)` (`agent.transcripts` is the
   `DiagramService`; for the project agent the `ProjectService`, see [Project chat](#project-chat)) replaces the fold with
   `{"role": "summary", "content", "covers": {"from_seq", "to_seq", "turns"}, "created_at"}`;
   `turns` and `from_seq` accumulate across compactions. `generation` is `diagrams.chat_generation()`,
   read before the transcript was loaded. The write is conditional: if the fold is gone by then, or
   the transcript was cleared (even if new messages reused the same `seq` numbers), imported, or the
   diagram deleted and created again (#67), the store writes nothing and `compact()` returns `False`,
   so a cleared chat is never resurrected and fresh messages are never replaced by an old summary.

Versions are never touched. `POST /api/diagrams/{id}/chat/compact` runs `compact()` inline and
returns the new transcript; `DELETE …/chat` removes summaries as well. The UI renders a summary
entry as a collapsed `<details class="msg summary">`.

### `chat(diagram_id, user_message)`

Yields JSON-able event dicts (`{"type": ..., **data}`):

1. Load the diagram and its opaque revision (`diagrams.read`); remember `before` (dump without
   `updated_at`).
2. `execute(call)`: `get_diagram` returns `diagram.subset(focus, ids)` (focus resolved with
   `find_node`, unknown focus → tool error). Every other call goes to
   `operations.apply()`. On `OperationError` (including wrongly typed, missing or unknown
   arguments, #66) the result is `{"ok": false, "error": "<message>"}` with `is_error=True`. Any
   other exception is logged and returned the same way as `Internal error while applying <op>
   (<ExceptionType>); nothing was changed.` (`apply` has restored the diagram), so the turn goes on and
   ends with `final`. Outside propose mode the call runs inside `diagrams.write_through(…)`, which
   commits the state at once, conditional on the turn's revision, and returns the new revision (#65); the
   service announces every such write (and the turn's version) to the live streams with `origin: "assistant"`
   ([Collaboration](../architecture/specs/collaboration.md)).
   On a conflict (another request wrote meanwhile) the service re-loads latest, which already holds
   this turn's earlier calls and the other change, and applies the call again, up to 5 attempts; from then
   on the turn, its live renders and `final` work on that state. A call that no longer applies is an
   error tool result; if the diagram was deleted (or moved out of the caller's sight), the call and every later one
   return `The diagram was deleted meanwhile; …` and nothing is written any more; if the caller's role dropped below
   editor during the turn (`forbidden`), they return `Your role on this project was lowered during this turn: …`
   and nothing is changed any more (ADR-0090). Neither raises out of the tool executor. Then it records the touched ids in `changed`
   and the call in `applied`, and returns the operation result as JSON.
3. For every provider event (`text_delta` events pass through unchanged): a successful `tool_result` is enriched with `diagram`, `mermaid`
   (rendered with `highlight=changed` and the effective focus) and `focus`; `done` captures the
   final text; `error` captures the message.
4. **`finally`**: if the loop did not complete and no error was recorded, the error becomes
   "Turn interrupted before the model finished (client disconnected)." Then `_finalize` runs
   regardless of how the turn ended. On a disconnect the server closes the generator on a worker thread as soon as
   the response ends (`TurnResponse`, ADR-0126), so this happens at once.
5. Outside propose mode the diagram is re-read from the store first, so `final` shows the stored
   state (after a skipped finalization it contains the newer edit or restore); if the diagram was
   deleted during the turn, `error` is `The diagram was deleted during this turn.` Then emit `final` with `reply`, `error`, `changed`, `operations`, `version` (or `null`),
   `diagram`, `mermaid`, `focus`, `usage` and `proposal` (see [Propose mode](#propose-mode)).

### Propose mode

Design: [Propose-then-confirm](../architecture/specs/propose-confirm.md), ADR-0015. Tests:
`tests/test_proposal.py`. With `propose=True` the turn is a dry run on the loaded in-memory diagram:

- a deep copy of the start state is kept; `execute` applies operations as usual but skips
  `diagrams.write_through`, so `get_diagram` and later rounds see the proposed state;
- `_finalize(..., propose=True)` writes neither latest nor a version; the assistant transcript entry
  gets `"proposed": true` when operations were applied;
- the `final` event carries `proposal = {"base": fingerprint(start), "result": fingerprint(proposed),
  "operations": [{"op", "arguments"}], "diff": proposal.diagram_diff(start, proposed)}`, or `null` when
  nothing was applied. In apply mode `proposal` is always `null`.

`arcviz/proposal.py` holds `diagram_diff`, `apply_proposal(diagrams, …)` (inside `DiagramService.update`: base check, replay
through `operations.apply`, result check, then latest and an `auto` version labelled with the given
label or "Proposal applied" in one conditional write; a concurrent write in between re-runs the replay,
whose base check then fails if the content changed (#65), and the transcript note `Proposal applied as version vN.` with
`proposal_outcome: "applied"`; any mismatch raises `ProposalConflict` before anything is written) and
`discard_proposal` (transcript note `Proposal discarded; the diagram was not changed.`,
`proposal_outcome: "discarded"`). In `diagram_diff`, an edge id whose endpoints differ counts as
removed plus added, because edge ids are reused.

### `_finalize`

- If operations were applied and the diagram differs from `before` (ignoring `updated_at`):
  `diagrams.commit_turn(diagram, revision, …)` writes latest (unchanged content) and an `auto` version
  labelled with the first line of the user message (max 100 chars, fallback "Chat change")
  atomically, conditional on the revision of the turn's last committed call; the applied operations
  are passed as `details` for the Git mirror's commit body. When latest moved on (a newer direct edit,
  restore or undo, or a deletion) nothing is written and `version` is `null`: the newer state already
  contains the turn's operations, and writing would overwrite it (#65). A caller whose role dropped below editor
  during the turn still gets this version, of exactly the state the turn wrote (ADR-0090). A real store failure
  while writing it is logged and reported in `final.error` and the transcript entry (`VERSION_FAILED`: *The changes
  of this turn are saved, but their version could not be written: …*).
- Append the transcript: the user entry, and an assistant entry with the reply text (or
  `(error: …)`), the applied operations, the version number, the error and the token `usage` of
  the turn (from the `done` event; also part of the `final` event). If there is neither
  text nor error, no assistant entry is written. The append is conditional on the transcript generation the
  turn read at its start (`diagrams.append_chat(…, generation)`, ADR-0127): if the diagram was deleted (and maybe
  created again) or its chat cleared or imported during the turn, the transcript is not written
  (`append_chat` returns `False`; the store refuses appends to unknown diagrams and to another generation). If the caller's role dropped below editor, `append_chat` raises `Forbidden`, which
  `_finalize` catches: no transcript entry, `final.error` = `ROLE_LOST_TURN` (ADR-0090). The bound service stamps both entries with `author`/`author_name` of the caller, and the
  version with its author (ADR-0086).

### Guarantees

- Nothing the user saw applied is ever lost: latest is written after every successful
  operation, and the version plus transcript are written in `finally`, so a browser refresh or a
  dropped connection mid-turn still yields a consistent, versioned state.
- A turn never overwrites a concurrent change: every write is conditional on the revision the turn
  last saw, and a deleted diagram is never recreated ([concurrent mutations](../architecture/specs/concurrent-mutations.md)).
- A failed operation never changes the model (see [Operations](operations.md)).

## Shared project model

Design: [Shared project model](../architecture/specs/shared-project-model.md), ADR-0076. Source:
`arcviz/agent_model.py`. Tests: `tests/test_agent_model.py`.

`DiagramAgent(..., models=ModelService)` (the server passes it to both agents): when the turn's diagram
belongs to a project (`models.project_of`), `ModelTools(models, project_id, propose)` adds `MODEL_TOOLS` to
the type's tools, and when the project has elements the system prompt gets `prompts.model_part(...)` (the
block `arcviz/prompts/shared/model.txt`, `Project elements:` and `model_summary(elements)`, at most 4 000
characters) between the stable prefix and `Current state:` (`prompts.system_prompt(type, kinds, summary,
model)`).

A written `link_element` call is verified (`ModelTools.verify_link`): the element is read again, and when its kind no
longer suits the view (changed by a concurrent request), it is gone, or it cannot be read, the link is taken back
through the turn's write-through (only while the view still stands for that element) and the call is a tool error
(#155, ADR-0128). If that write does not happen (the diagram keeps changing, it was deleted, or the caller lost
editor), the tool error says the link could not be taken back, and the link, which stays stored, is recorded among
the turn's operations; the project review reports it. A take-back that found the diagram deleted or the caller's role
lowered ends the turn's changes like a refused write. Both agents use the one helper `agent.take_back_invalid_link`
(returns a `LinkTakeBack` with the tool error, the state to continue on, `kept`, `deleted`, `lost`; #195).

| Tool | Behaviour |
|---|---|
| `list_elements` | `ModelService.model(pid)` as JSON (elements with usages and sections); read-only |
| `create_element(name, kind, description?, parent?)` | `add_element`; result `{element}` |
| `update_element(element, name?, description?, kind?)` | `update_element` (rename propagation, kind check); result `{element, renamed_in, not_yet_renamed_in?}` |
| `link_element(id, element)` | `link_args` from the model, then the internal `link_element` operation on the turn's diagram inside `write_through` (re-applied on a conflict) |
| `unlink_element(id)` | the ordinary operation, like any editing call |
| `create_relation(kind, source, target, properties?, description?)` | `add_relation` ([model relations](../architecture/specs/model-relations.md)); result `{relation}` |
| `update_relation(relation, kind?, source?, target?, properties?, remove_properties?, description?)` | `update_relation`; result `{relation, unlinked_in, not_yet_checked_in?}` |
| `link_relation(id, relation)` | the relation's kind and ends from the model, then the internal `link_relation` operation on the turn's diagram inside `write_through` (no verify-after-write: a link racing a relation change is drift that `sync` repairs, ADR-0139) |
| `unlink_relation(id)` | the ordinary operation |

`list_elements` also returns the relations (and `diagrams`). The prompt's model block lists them after the elements
under `Relations:` (`model_summary(elements, relations=…)`: descriptions dropped first, then relations counted, then
elements). `prompts/shared/model.txt` tells the model to link a matching relation after drawing a connection between
two linked elements and, when none exists, to name the relation it would add in its reply and create it only on the
user's request (ADR-0140). The relation link tools are offered only for deployment, sequence and component diagrams
(`agent_model.model_tools_for(type)`); `create_relation` and `update_relation` are project-level tools like
`create_element`. `set_completeness` is an ordinary editing tool of deployment, sequence and component diagrams (component: `communicates`, `data_flow`).

`create_element`, `update_element`, `link_element`, `create_relation`, `update_relation` and `link_relation` are
refused in propose mode ("…changes the shared project
model, which preview mode does not do…"); `list_elements` and `unlink_element` are not (an unlink is part of
the proposal). `create_element` and `update_element` are recorded in `applied` (and the transcript's
`operations`) without changing the diagram. When an `update_element` renamed views of the turn's diagram (or an `update_relation`
unlinked connections there, `agent_model.propagated`), the turn reads it again (the rename reached it as a version of its own, which also holds the turn's earlier calls),
resets its start content to that state and marks the renamed views as changed; the turn's own automatic version
then holds only what followed. A model change that wrote nothing into the diagram (a description, a kind, an
element it does not show, a failed write) leaves the turn's state alone. Calls named like an internal operation (`sync_element`, …) are tool errors.

### Project elements by default

Design: [Elements by default](../architecture/specs/elements-by-default.md), ADR-0164. Tests: `tests/test_adoption.py`.

The model block is in the prompt whenever the diagram (or the project chat) belongs to a project: without elements it
reads `(none yet: every structural node or lifeline you create becomes one)` (`agent_model.NO_ELEMENTS`).
`prompts/shared/model.txt` says that structural nodes, lifelines, partitions and data stores become project elements
when they are created, that a node named exactly like an element is linked to it and any other name creates one
(so: check the elements first and use exactly their names, never a variant), that a new diagram is built from the
existing elements where they fit, and that the reply names the elements a turn created ("Neu im Projektmodell: …").

Every editing call outside propose mode goes through `ModelTools.adopting(diagram, apply_call, name)`
(`services/adoption.adopting`): the call is probed on a copy; when it creates adoptable views, the missing elements are
created (project writes) and the call's change applies the operation and then one internal `link_element` per new view
inside the same `write_through`; the tool result carries `adoption` (`linked`, `created`, `skipped`). After the write,
elements no diagram refers to are removed again. Then `ModelTools.settle` reads the adopted elements again; links to
an element removed or re-kinded meanwhile are taken back through the write-through and move to `adoption.skipped`
(ADR-0128). The model's own tools and propose mode are not adopted. `list_elements` and `list_drivers`
(`READ_MODEL_TOOLS`) are never recorded in `applied`, so they never become operations of a proposal.

### Architecture drivers

Design: [Architecture drivers](../architecture/specs/architecture-drivers.md), ADR-0163. Tests: `tests/test_drivers.py`.

`ModelTools.context()` reads the project document once per turn and returns the model summary and the
`agent_model.ArchitectureContext` (`architecture_context(project)`, M29): the drivers with their core scenarios
(`decision_model.drivers_with_core_scenarios`: one line per driver, most important first,
`- [d3] quality goal (importance high, risk medium): Title — description (concerns: a, b)`, each followed by
`  core: [q2] stimulus → response measure` lines; descriptions shortened to 200 characters, then dropped, then the
lowest drivers counted, at most 5 000 characters), the stakeholders (`stakeholder_lines`, most influential first,
3 000 characters) and the **accepted** decisions (`decision_lines`, newest first, decision text shortened to 300,
3 000 characters). `prompts.architecture_part(drivers, stakeholders, decisions)` builds the block (`shared/drivers.txt`
under `Architecture drivers (most important first):`, `Stakeholders (most influential first):`, `shared/decisions.txt`
under `Accepted architecture decisions (binding, newest first):`; each only with content), which follows the model part
(`prompts.system_prompt(type, kinds, summary, model, architecture)`; `prompts.project_part`). `decisions.txt` makes
accepted decisions binding: a request that would contradict one is answered by naming it (ADR-<n>) and asking, never by
a silent change ([decision support](../architecture/specs/decision-support.md), ADR-0168). The project chat adds the same block after its model part (`build_project_prompt`, from the
project it already read). The block tells the model to weigh proposals against the highest-rated drivers, to name the
drivers a trade-off serves or hurts, never to break a constraint silently, and to change drivers only on request.

| Tool | Behaviour |
|---|---|
| `list_drivers` | the drivers by priority (`ModelService.ranked_drivers`); read-only, also in propose mode |
| `create_driver(title, kind, importance, risk, description?, elements?)` | `ModelService.driver_operation(pid, "add_driver", …)`; result `{driver}` |
| `update_driver(driver, title?, kind?, importance?, risk?, description?, elements?)` | `update_driver`; result `{driver}` |
| `remove_driver(driver)` | `remove_driver`; result `{driver}`; refused unless the user retired the driver (ADR-0198). `status` is no argument of the tools: a call that passes it is refused (retiring and reactivating are the user's) |
| `list_scenarios`, `list_decisions`, `list_stakeholders` | the project's lists in full (decisions also drafts); read-only, also in propose mode (`READ_MODEL_TOOLS`) |
| `create_scenario(title, kind, stimulus, source?, environment?, artifact?, response?, response_measure?, driver?, core?, qualities?, elements?)` | `ModelService.driver_operation(pid, "add_scenario", …)`; only on request; result `{scenario}` |

They are project-level tools (`PROJECT_LEVEL_TOOLS`): offered in the diagram chat and in the project chat, also without
an active diagram, recorded in `applied` without changing a diagram. Every tool but `list_drivers` is refused in propose
mode ("…changes the project's architecture drivers, which preview mode does not do…").

## Project chat

Design: [Project chat](../architecture/specs/project-chat.md), ADR-0057–0061. Tests:
`tests/test_project_agent.py`, `tests/test_server_projects.py`. `arcviz/project_agent.py`:

```python
ProjectAgent(provider, diagrams, projects, **DiagramAgent options)  # projects: ProjectService
agent.chat(project_id, user_message, active=None, focus=None, propose=False)
agent.bound(diagrams, models, projects)  # a copy on the services of one caller, like DiagramAgent.bound
```

`chat` first checks the editor role on the project (`projects.require(id, "editor")`): a viewer's turn raises
`Forbidden`, an invisible project `ProjectNotFound`, before the first event. Every diagram the turn reads or writes
is checked by the bound services; diagrams created by the turn are owned by the project, the caller is recorded as
the author of every version and transcript entry.

`ProjectAgent` subclasses `DiagramAgent` for history and compaction: `transcripts` is the
`ProjectService` (`chat`, `chat_generation`, `compact_chat`, `append_chat` on the project transcript,
store primitives `*_project_chat`, see [Persistence](persistence.md)) and `summary_prompt` is
`PROJECT_SUMMARY_SYSTEM_PROMPT`. `_describe_op` prefixes an operation with `[diagram]` when it names one.

**Start.** `projects.load_members(project_id)` (raises `ProjectNotFound` before the first event) gives
the project and its existing member diagrams. `active` names the first active diagram when it is a
member (else the turn starts without one). The system prompt is `build_project_prompt`:
`prompts.project_prompt_prefix()` (the shared blocks of [System prompt](#system-prompt) and
`prompts.PROJECT_PART`, `arcviz/prompts/project/chat.txt`: the same stable prefix for every project, so the
topic scope applies and prompt caching shares it), then `PROJECT_CONTEXT_TEMPLATE` with title, description and `project_structure()` (sections as
`- Section: <title> [<id>]`, diagrams as `- [id] «type» title — brief`; `diagram_brief` = element and edge
counts (sequence: lifelines and messages) and the top-level names; brief length `PROJECT_CONTEXT_BUDGET`
(8 000) divided by the number of diagrams + 1, clamped to 60–400, left out with a note to use
`read_diagram` when below 60), then either "No diagram is open. …" or `Active diagram: [id] «type» title`
followed by `active_diagram_part(diagram)` = `prompts.diagram_part(type, kinds, summary)` (its type's expert
part and `Current state:` with its summary, without repeating the shared blocks).

**Tools** (`run_turn` gets a callable, evaluated before every model call): `PROJECT_TOOLS` always,
plus `build_tools(kinds_for(type), type)` of the active diagram while one is active and not deleted. With a
`ModelService` (`ProjectAgent(provider, diagrams, projects, models, …)`), `list_elements`, `create_element`
and `update_element` are offered always and `link_element`/`unlink_element` with an active diagram (see
[Shared project model](#shared-project-model)); after an `update_element` every diagram the turn holds that the
rename was written into is read again (the others keep their state). The project's elements (`prompts.model_part`) follow the project structure in the prompt when it
has any.

| Tool | Behaviour |
|---|---|
| `list_diagrams` | the current project (re-read): `sections` (id, title, parent), `diagrams` (id, title, type, section, element and edge counts; the turn's in-memory state for opened diagrams), `active` |
| `read_diagram(diagram)` | `summary(budget=context_budget)` of a member, with id, title, type, section; opens nothing |
| `open_diagram(diagram)` | makes the member active; result: `active`, `title`, `type`, `instructions` (`active_diagram_part` of it: expert part and state) |
| `create_diagram(title, type, section?)` | `DiagramService.import_new(Diagram(id=slug(title), title, type), title, None, project_id, section, incarnation=…)` (free id; placed only into the project incarnation the turn loaded, else the new diagram is removed again and the call fails, ADR-0127; unknown section → tool error with the known ids); the new diagram becomes active; refused in propose mode and when the project transcript's generation is no longer the turn's (the project was deleted or re-created, or its chat cleared) |

Diagram references are an id or a unique case-insensitive title of a member; otherwise the tool error
lists the member ids. `read_diagram`, `open_diagram` and every editing call first re-read the project and
check that the diagram is still a member: a diagram moved to another project (or deleted) meanwhile is a
tool error (`… is no longer in this project; nothing was changed.`) and is not edited any more in this turn. Every other tool is an editing call on the active diagram: without one it fails
with `No diagram is open; call open_diagram or create_diagram first, then edit it.`; `get_diagram`
reads the active diagram (`get_diagram_result`); everything else runs exactly as in a diagram turn
(`apply_tool_call` inside `diagrams.write_through` on that diagram's revision; `deleted` closes the
diagram for the rest of the turn, `busy` is a tool error; `forbidden` (also from a link take-back) stops the writes
to that diagram: every later editing call on it answers the role error without writing, while other diagrams of the
turn the caller may still edit stay editable; a `create_diagram` refused for the role stops further creates; either
way `final.error` is the role-lost turn error (#195, coordinator review of #199); the model tools answer a lowered
role with a tool error too). Each opened diagram keeps its own state,
revision, start content, applied operations and changed ids for the turn.

**Events.** As for a diagram turn. A successful `tool_result` of anything but `list_diagrams`,
`read_diagram` and `get_diagram` carries the active diagram: `diagram_id`, `diagram`, the render payload
with that diagram's changed ids highlighted, and `focus` (the request's, if the node exists in it). The
`final` event carries `reply`, `error`, `active`, `changed` (of the active diagram), `operations` (every
applied call with `diagram`), `diagrams` (per changed or created diagram, in first-change order:
`id`, `title`, `type`, `version` (number or `null`), `created`, `changed`, `operations` (count)),
`project_changed`, `version` (the active diagram's, or `null`), `usage`, the active diagram's
`diagram_id`/`diagram`/render payload/`focus` (or `diagram_id`/`diagram` `null`) and `proposal`.

**Finalization** (`finally`): for every changed diagram, `commit_turn(diagram, revision, label=first
line of the message, details)` when operations were applied, the content differs from the start and it
was not deleted (outside propose mode); a `None` result (the diagram moved on) is logged. Then the
project transcript, appended conditionally on the generation read at the start of the turn
(`projects.append_chat(…, generation)`, atomic in the store, ADR-0127; a project deleted and created again under the
same id, or a transcript cleared meanwhile, is not continued): the user entry and an assistant entry with `content`, `operations`, `diagrams`,
`active`, `error`, `usage` and, for a proposal, `proposed: true` and `proposal_diagram`. A caller whose role on the
project dropped below editor during the turn gets the versions but no transcript entry, and `final.error` says so
(ADR-0090). Every diagram whose version could not be written (a store failure) is logged and listed; the same text
goes into `final.error` and the transcript entry. Outside propose
mode the active diagram is re-read for `final` (deleted → `active: null`). Then `_maybe_compact(project_id)`.

**Propose mode** (ADR-0060): edits apply to the in-memory copies only; `create_diagram` is refused; the
first diagram with a successful edit is the proposal's, an edit of another diagram is refused with a
tool error naming it. `final.proposal = {diagram_id, base, result, operations, diff}`. It is confirmed
with `POST /api/projects/{pid}/proposal/apply` (`proposal.apply_proposal(..., note=…)` writes the
outcome note into the project transcript, conditional on the generation read before the membership check, and only
while the diagram is still a member of the project, re-checked after the replay with `ProjectService.holds`; a diagram
moved away meanwhile gets no note there, ADR-0127) or discarded with `…/proposal/discard`.
