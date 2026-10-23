# Project chat: one conversation across the diagrams of a project

- Status: Implemented (milestone M17)
- Issue: #135
- ADRs: [0057 A project transcript next to the project document](../adr/0057-project-transcript-next-to-the-project-document.md),
  [0058 The active diagram is per-turn state; tools follow its type](../adr/0058-active-diagram-per-turn-tools-follow-its-type.md),
  [0059 A multi-diagram turn writes through per call and versions each diagram](../adr/0059-multi-diagram-turn-versions-each-diagram.md),
  [0060 Propose mode in a project turn covers one existing diagram](../adr/0060-project-propose-mode-one-diagram.md),
  [0061 The project chat is a scope of the chat pane, opened from the navigator](../adr/0061-project-chat-as-a-chat-scope.md)
- Builds on: [Projects](projects.md) (M16), [Application services](application-services.md), the per-diagram
  agent ([agent spec](../../spec/agent.md)), [Chat compaction](chat-compaction.md),
  [Propose and confirm](propose-confirm.md)

## Summary

Every chat today is bound to one diagram: the model sees that diagram and edits it. With projects (M16) a
user describes a system across several diagrams ("lege für den Checkout ein Sequenzdiagramm an und ergänze
im Deployment den Payment-Service"). M17 adds a **project chat**: one transcript per project whose system
prompt carries the project structure (sections, diagrams with type and a short budgeted summary each),
with tools to list, read, create and open diagrams. One diagram at a time is **active**; the model edits it
with the existing per-type tools, through `operations.apply`, written through after every call and
rendered live, exactly like a diagram turn. Per-diagram chats stay unchanged.

## Goals

- `ProjectAgent` (`arcviz/project_agent.py`): a chat turn over a project with the project tools
  `list_diagrams`, `read_diagram`, `create_diagram`, `open_diagram` plus the tools of the active
  diagram's type, re-evaluated every round.
- The same turn semantics as `DiagramAgent.chat` for every diagram the turn edits: `write_through` after
  each successful call (conditional, conflicts re-applied), live `tool_result` events with the rendered
  active diagram, finalization in a `finally` block (`commit_turn` per changed diagram, transcript
  append), disconnect-safe.
- A project transcript with the same seq/generation/compaction semantics as a diagram transcript, stored
  through `ProjectService` and new store primitives on all three backends.
- Propose mode limited to one existing diagram per project turn, clearly reported to the model and the user.
- HTTP: `POST/GET/DELETE /api/projects/{pid}/chat`, `POST …/chat/compact`, `POST …/proposal/apply`,
  `POST …/proposal/discard`.
- UI: a **Projekt-Chat** entry point in the navigator; the chat pane switches between the diagram chat and
  the project chat; the canvas follows the active diagram; changed diagrams are named in the reply and
  marked in the tree.

## Non-goals

- Domain-expert prompts per diagram type (#125) and keeping the chat on architecture topics (#124): the
  per-type system prompts are reused unchanged.
- Project operations (sections, moves, renames of the project) from the chat; the model can create
  diagrams in existing sections only.
- Deleting diagrams from the chat.
- A shared model across diagrams (M18, #137). Nothing here stores cross-diagram references; M18 added element
  tools to this chat ([Shared project model](shared-project-model.md), ADR-0076).
- Exporting the project transcript in a project bundle.

## Design

### Transcript storage (ADR-0057)

The project transcript lives next to the project document, keyed by the project id, with the same
contract as a diagram transcript (`seq` per transcript, never reused after compaction; a *generation*
replaced by a clear; `load` hides entries older than the newest summary). New abstract store primitives,
also on `ProjectRepositoryPort`:

```python
def load_project_chat(self, project_id: str) -> list[dict[str, Any]]: ...  # [] if none
def append_project_chat(self, project_id: str, entries: list[dict[str, Any]]) -> None: ...  # StoreError if no project
def project_chat_generation(self, project_id: str) -> str: ...  # "" for an unknown project
def compact_project_chat(
    self, project_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
) -> bool: ...
def clear_project_chat(self, project_id: str) -> None: ...  # StoreError if no project
```

`delete_project` also removes the transcript; `create_project` never inherits one.

| Backend | Layout |
|---|---|
| `file` | `project-chats/<id>/chat.json` and `project-chats/<id>/generation`, under the project lock `project:<id>`; `create_project` removes a leftover directory after claiming the id; `delete_project` removes it after the project file |
| `sqlite` | table `project_chat(project_id, seq, entry)`; column `projects.chat_generation` (added on start-up for existing databases); deleted in the same transaction as the project |
| `cosmos` | items `chat:NNNNNN` and `chat-generation` in the project's partition `project:<id>`; an append is a batch of "read `project`" and "create `chat:NNNNNN`", so it fails once the project is gone; `delete_project` deletes the remaining items of the partition after the project item (best effort, like `purge`) |

The shared logic (seq allocation, staged-summary hiding, generation guard) is the diagram transcript's,
factored per backend into helpers that take the owner (diagram or project), so both transcripts behave
identically and `tests/store_contract.py` runs the same transcript checks for both.

`ProjectService` gets the transcript methods with the names and signatures of `DiagramService`
(`chat`, `append_chat` → `False` when the project is gone, `chat_generation`, `compact_chat`,
`clear_chat` → `ProjectNotFound`), so the agent's compaction works on either service (a structural
`TranscriptService` protocol in `agent.py`).

### Reading the members

`ProjectService.load_members(project_id) -> tuple[Project, list[tuple[Membership, Diagram]]]` loads every
member diagram that still exists, in membership order (a diagram deleted meanwhile is skipped). The
project prompt and the project review (spec [project-review.md](project-review.md)) use it.

### The agent (`arcviz/project_agent.py`)

`ProjectAgent(provider, diagrams, projects, **DiagramAgent options)` subclasses `DiagramAgent` for the
transcript machinery (`_history`, `compact`, `_maybe_compact`, `drain`, executor); `DiagramAgent` reads
its transcript through `self.transcripts` (the `DiagramService` by default, the `ProjectService` here)
and its summary prompt from `self.summary_prompt`. Nothing changes for diagram turns.

`ProjectAgent.chat(project_id, message, active=None, focus=None, propose=False) -> Iterator[event]`.

**Turn state** (ADR-0058). The active diagram is per-turn state, seeded from the request's `active` (the
diagram the UI shows) when it is a member of the project, else none. It is never stored on the server:
between turns the UI is the source of truth. Each diagram the turn opens gets a `_DiagramTurn` record:
the diagram (read with its revision), the revision of its last write, the content before the turn, the
applied operations, the changed element ids, `created`, `deleted`, and for propose mode the original.

**System prompt.** It starts with the shared blocks of the diagram prompts (persona, topic scope, working
rules, hints, reply style; [assistant prompts](assistant-prompts.md), ADR-0065/0066) and the project part
`arcviz/prompts/project/chat.txt`: how to work across diagrams (open before editing, create
in a section, one change request may touch several diagrams, ask when unsure which diagram is meant);
the project title, description and structure (`project_structure()`: the section tree, each diagram as
`[id] «type» title — brief`); and, when a diagram is active, its type's expert part and state (`prompts.diagram_part`)
as "Active diagram" block. Everything up to the project part is a stable prefix (prompt caching). The brief is `diagram_brief(diagram, limit)`: element and edge counts plus
the top-level names (sequence diagrams: lifelines and message count), cut at `limit`. The structure has
a budget of `PROJECT_CONTEXT_BUDGET` (8 000) characters: the per-diagram limit is
`clamp(budget // n, 60, 400)`; a project too large for even 60 characters per diagram lists ids, types
and titles only and says so (the model reads details with `read_diagram`).

**Tools.** The project tools are always offered; the tools of the active diagram's type
(`build_tools(kinds_for(type), type)`, including its `get_diagram`) are added while a diagram is
active. `run_turn` accepts `tools` as a list or as a callable that is evaluated before every model call,
so the tool set follows `open_diagram`/`create_diagram` from the next round on (ADR-0058).

| Tool | Arguments | Result |
|---|---|---|
| `list_diagrams` | – | the structure as JSON: sections (id, title, parent) and diagrams (id, title, type, section, element and edge counts), plus the active id |
| `read_diagram` | `diagram` | id, title, type, section and `summary()` (budget `context_budget`) of any member; does not change the active diagram |
| `open_diagram` | `diagram` | makes a member active: id, title, type and the type's prompt with the current state (`build_system_prompt`); its editing tools follow in the next round |
| `create_diagram` | `title`, `type`, `section?` | creates an empty diagram of `type` (`DiagramService.import_new` with a free id from the title) in the section (id or title; default the top level) of this project and makes it active; refused in propose mode |

A diagram reference accepts an id or a case-insensitive title (unique within the project); errors list
the member ids, like the operations do. Any other tool name is an editing call for the active diagram:
without one it fails with "No diagram is open; call open_diagram or create_diagram first." `get_diagram`
reads the active diagram as in a diagram turn.

**Membership during a turn.** `read_diagram`, `open_diagram` and every editing call re-check that the
diagram is still a member of the project (it may have been moved or deleted since the turn started); one
that left is a tool error and is not edited any more. The transcript entry is written only if the project
transcript's generation is still the one read at the start (a project deleted and re-created under the same
id, or a cleared transcript, is not continued).

**Editing calls** run exactly like `DiagramAgent.chat`'s `execute`: `operations.apply` on the active
diagram's state, and outside propose mode `DiagramService.write_through(...)` conditional on that
diagram's revision (a conflict re-loads and re-applies; `deleted` and `busy` outcomes become tool
errors; a deleted diagram stays closed for the rest of the turn).

**Events.** As in a diagram turn, plus:

- every successful `tool_result` of an editing call, `open_diagram` or `create_diagram` carries
  `diagram_id` and the active diagram (`diagram`, `render.payload` with the turn's changed ids of that
  diagram highlighted, `focus` when it still exists in that diagram, else `null`);
- the `final` event carries `active` (the active diagram id or `null`), the active diagram and its
  rendering (when there is one), `changed` (element ids of the active diagram), `operations` (every
  applied call, each with `diagram`), `diagrams` (one entry per diagram the turn changed or created:
  `{id, title, type, version, created, changed, operations}` with `version` the auto version number or
  `null`), `project_changed` (a diagram was created), `reply`, `error`, `usage` and `proposal`.

**Finalization** (ADR-0059), in the `finally` block, for every diagram with applied operations whose
content changed (in the order they were first changed): `commit_turn(diagram, revision, label, details)`
→ one automatic version per diagram, with the user message as label; skipped (and logged) when that
diagram moved on or was deleted, as for a diagram turn. There is no atomic commit across diagrams; each
diagram's history stays linear and self-contained. Then the transcript append to the project (user entry;
assistant entry with `content`, `operations` (with `diagram`), `diagrams` (as in `final`), `active`,
`error`, `usage`, `proposed` and `proposal_diagram` for a proposal), then `_maybe_compact(project_id)`.
The `final` event re-reads the stored state of the active diagram.

**Propose mode** (ADR-0060): the turn is a dry run on in-memory copies. `create_diagram` is refused ("not
available in preview mode"). The first diagram that gets a successful editing call becomes the
proposal's diagram; an editing call on another diagram is refused with an error naming the proposal's
diagram. `final.proposal` is `{diagram_id, base, result, operations, diff}` (as for a diagram turn plus
`diagram_id`). It is confirmed or discarded through the project:

- `POST /api/projects/{pid}/proposal/apply` `{diagram, operations, base, result, label?}`: 404 when the
  diagram is not a member, otherwise `proposal.apply_proposal(...)` on that diagram (409 on a stale base or
  a mismatching replay); the outcome note goes into the **project** transcript. Answers the diagram
  detail plus `version` and `changed`.
- `POST /api/projects/{pid}/proposal/discard` `{diagram?}`: records the discard in the project
  transcript; answers `{"chat": [...]}`.

`proposal.apply_proposal`/`discard_proposal` get an optional `note` callable (default: the diagram
transcript) so the project routes can record the outcome in the project transcript.

### Compaction

As for diagrams (spec [chat-compaction.md](chat-compaction.md)): after every project turn `_maybe_compact`
checks the transcript length against `ARCVIZ_CHAT_COMPACT_AFTER` and folds older entries into a
cumulative summary via `PROJECT_SUMMARY_SYSTEM_PROMPT`, guarded by the transcript generation;
`_summary_input` names the diagram of each operation. No new configuration.

### HTTP API

| Route | Body | Answer |
|---|---|---|
| `POST /api/projects/{pid}/chat` | `{message, active?, focus?, mode: apply\|propose}` | SSE stream (404 unknown project, 413 too long, 503 no provider) |
| `GET /api/projects/{pid}/chat` | | `{"chat": [...]}` |
| `DELETE /api/projects/{pid}/chat` | | 204 |
| `POST /api/projects/{pid}/chat/compact` | | `{"compacted", "chat"}`; 502 when the model call fails |
| `POST /api/projects/{pid}/proposal/apply` | `{diagram, operations, base, result, label?}` | diagram detail + `version`, `changed`; 404/409/413 |
| `POST /api/projects/{pid}/proposal/discard` | `{diagram?}` | `{"chat": [...]}` |

`active` naming a diagram outside the project is ignored (the turn starts without an active diagram).
The limits middleware classifies the chat and compact routes as `chat` (path suffix), the proposal
routes as `ops`; the chat length limit applies as for diagram chats.

### Frontend (ADR-0061)

- `state.chatScope` (`"diagram"` | `"project"`) and `state.projectId` (set by the navigator).
- Navigator: a **Projekt-Chat** button in the navigator's action row (`#btn-project-chat`,
  `aria-pressed`), a hook `onProjectShown(fn)` that runs after a project view is shown, `openInProject(id)`
  (open a diagram and update the tree) and `markChanged(ids)`, which marks tree rows of diagrams the last
  project turn changed ("geändert" badge, cleared when the diagram is opened).
- `chat.js` owns the scope: `setChatScope(scope)` loads the project transcript (`GET …/chat`) or the
  diagram's and shows a scope bar above the messages ("Projekt-Chat · <Projekt>", button **Zum
  Diagramm-Chat**); `diagram.js` leaves the transcript alone while the scope is `project`; **Chat leeren**
  clears the transcript of the current scope.
- A project turn posts to `/api/projects/{pid}/chat` with `active: state.diagramId`. A `tool_result`
  whose `diagram_id` differs from the shown diagram opens that diagram first (canvas, versions, tree),
  then renders the event's rendering. Tool lines of the project tools have German descriptions
  (`projectchat.js`, pure). The `final` event shows the active diagram with the turn's highlight, lists
  the changed diagrams below the reply as buttons ("Geändert: Checkout (neu, v1) · Deployment (v7)"),
  marks them in the tree and reloads the project view.
- A proposal of a project turn is confirmed or discarded through the project routes.
- In an empty project the project scope hides the empty-project placeholder and makes only the canvas
  inert (`navigator.applyEmpty`), so the project chat can create the first diagram.
- Persisted project entries show the same diagram list (`projectchat.changedDiagramsLabel`).

## Testing

- Store contract (`tests/store_contract.py`, all backends incl. the Cosmos fake): project transcript
  append/seq, hide staged, compaction with generation guard, clear, unknown project, delete removes it,
  re-created project starts empty.
- `tests/test_project_service.py`: transcript methods (`append_chat` False after delete,
  `clear_chat` 404), `load_members` order and skipping vanished diagrams.
- `tests/test_project_agent.py` (ScriptedProvider only): prompt contents and budget; tool set changes
  after `open_diagram`; editing without an active diagram; `create_diagram` (section, type, unknown
  section); edits on two diagrams → two versions, transcript entry with `diagrams`; write-through and
  conflict re-apply; a disconnect mid-turn persists; diagram deleted during the turn; propose mode
  (single diagram proposal, second diagram refused, create refused, nothing written); compaction of
  the project transcript; `active` outside the project ignored.
- `tests/test_server_projects.py`: the new routes (SSE, 404s, 413, proposal apply/discard and 409,
  clear, compact).
- `tests/js/projectchat.test.mjs`: tool descriptions, the changed-diagrams label, event routing helpers.
- e2e: open the project chat from the navigator, a scripted turn creates a diagram and edits another,
  the canvas follows, the tree marks both, the reply lists them, back to the diagram chat.

## Migration / compatibility

Additive: new tables/columns/directories are created on demand; existing transcripts, diagram routes and
events are unchanged. A database without `projects.chat_generation` gets the column on start-up.

## Open questions

None open; the conservative choices are recorded as Proposed ADRs (0058, 0060, 0061) for review.

## Decisions

- Transcript storage: own primitives per backend, not inside the project document (ADR-0057).
- Active diagram per turn, seeded by the client; tools follow its type each round (ADR-0058).
- One auto version per changed diagram, no cross-diagram atomicity (ADR-0059).
- Propose mode: one existing diagram per turn, no creation (ADR-0060).
- UI: a scope of the existing chat pane, entered from the navigator (ADR-0061).
