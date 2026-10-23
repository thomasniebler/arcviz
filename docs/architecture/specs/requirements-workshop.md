# Requirements workshop: from a pile of requirements to drivers and scenarios

- Status: Implemented (M31, with the deviations below)
- Issues: to be created after review
- ADRs: [0191 The elaboration mode is a main view with a requirements table and a scoped chat](../adr/0191-elaboration-as-a-main-view-with-table-and-chat.md) (Accepted),
  [0192 Import is deterministic; refinement and derivation run in chunks driven by the client](../adr/0192-bulk-steps-are-chunked-and-client-driven.md),
  [0193 The requirements chat keeps its transcript in its own keyed document](../adr/0193-requirements-chat-transcript-in-its-own-document.md)
- Builds on: [Requirements](requirements.md) (model, coverage, review rules), [Architecture objects over MCP](architecture-objects-over-mcp.md)
  (proposals, write policy), [Decision support](decision-support.md) (`scenario_analysis`), [Project chat](project-chat.md),
  [Architecture drivers](architecture-drivers.md)

## Summary

The **workshop** is the elaboration mode: a person (or an agent) pours a list of 200 to 500 requirements into a project,
Arcviz brings them into a uniform form, and from the cleaned-up requirements it **proposes** quality scenarios and
architecture drivers. Every step produces something a person reviews; nothing derived is stored without a decision.

```
pour in      →   refine              →   derive                →   check coverage
(raw)            (suggestions)           (project proposals)       (computed, review rules)
text/CSV/MCP     LLM, 25 per call        LLM, 30 per call          traceability matrix
                 person accepts          person applies in part    gaps are findings
```

The UI is a main view **Anforderungen** next to the diagram stage: a table of requirements as the working surface and the
existing chat pane with a third scope `requirements` as the steering channel (ADR-0191, to be confirmed).

## Goals

- Pour in a list from anywhere (pasted text, file, chat, MCP agent) without losing the original wording.
- Make volume manageable: filter, sort, select, accept in bulk, see how many requirements are in which step.
- Refine to a fixed target form: one sentence statement, kind, measurable criterion, theme, priority, issues (vague,
  duplicate, conflict), suggested stakeholders.
- Derive scenarios (six ATAM parts) and drivers as **proposals** that name the requirements they came from, take
  existing drivers and stakeholders into account, and apply in part.
- Everything is traceable and reviewable afterwards (coverage, matrix, review rules of [Requirements](requirements.md)).

## Non-goals

- A background job runner (ADR-0192): the client (or an agent) drives chunks.
- A global consistency pass (conflicts across all 500 at once). Duplicates and conflicts are found per chunk with
  deterministic candidates as hints; a per-theme consistency run is a follow-up.
- Deriving decisions (ADRs): they stay a human act on a scenario's analysis ([decision support](decision-support.md));
  the workshop only lets a decision name requirements.
- Import from named ticket systems, attachments, OCR. Plain text, Markdown lists, CSV.
- Several people elaborating the same list live (no presence, no locking beyond the revision).

## Design

### Pipeline state

Per requirement the step is derived, not stored:

| Step | Condition |
|---|---|
| **Roh** | `status == raw` and no suggestion |
| **Vorschlag** | `status == raw` and a suggestion exists |
| **Verfeinert** | `status == refined`, coverage `uncovered` |
| **Abgeleitet** | refined with coverage `driver`/`scenario`/`decision` |
| **Verworfen / Duplikat** | the statuses `rejected`, `duplicate` |

`digest` (a field of `GET …/requirements`) carries counts for each step and per theme/kind; the table header shows them as
the pipeline.

### Import

Pure parser `requirement_import.py` (no LLM, no package imports besides `requirement_model`):

```python
def parse(text: str, fmt: str = "auto", *, source: str = "", max_items: int = 500) -> ParseResult: ...
```

`fmt` is `lines` (each non-empty line is one requirement; leading bullets `-`, `*`, `•`, numbering `1.`, `1)`, `REQ-12:`
are stripped; an indented line continues the previous one), `markdown` (headings become the `theme`, list items the
requirements), `csv` (header row decides: `text|requirement|beschreibung|raw` required; optional `id|key|external_id`,
`source|quelle`, `theme|thema|kategorie`, `priority|priorität|prio` (MoSCoW or must/should/could and German
muss/soll/kann), `stakeholder`), `auto` (CSV when the first line has a recognised header and delimiter, Markdown when a
heading or list starts it, else lines). Delimiter detection `, ; \t`, UTF-8 with BOM, at most 1 MB. The result lists
`items` (≤ `max_items`) and `problems` (row, reason: empty text, too long (> 1500 is cut and flagged), unknown priority
is dropped and flagged, too many items).

`POST /api/projects/{pid}/requirements/import` (editor), body `{text, format?: "auto", source?: "", dry_run?: false}`;
answer `{added, skipped_duplicates, problems, requirements?}`; `dry_run` returns what would happen (counts, the first 20)
and writes nothing. Stakeholder names in a CSV column are matched case-insensitively to existing stakeholders; unknown
names are reported as problems (never created implicitly). Idempotent by `(source, external_id)` and by the exact
normalised `raw`, so importing a file twice adds nothing.

UI: **Importieren…** in the view: paste or choose a file (read in the browser, sent as text), format select, a
preview from the dry run (counts, problems, first rows) and **Übernehmen**.

**Chat and MCP.** A pasted list in the requirements chat is split by the assistant into `add_requirements` calls of at
most 50 (same checks and idempotence). For more than 50 lines the assistant tells the user to use **Importieren…** (it
is faster, cheaper and does not depend on a model copying 300 lines). An MCP agent calls `add_requirements` repeatedly.

### Refine (`arcviz/requirement_refine.py`)

`refine(provider, snapshot, requirements, candidates, instructions?) -> RefineResult`: one
`LLMProvider.complete_tool` call offering only `submit_refinements` over:

- the chunk (at most 25 requirements: id, raw, source, theme),
- the project's vocabulary: stakeholders (id, name, role), element names, existing themes (so that themes converge),
  existing drivers' titles,
- **duplicate candidates**: pairs from `duplicate_candidates` that involve the chunk (any requirement of the box, not just the chunk;
  id and statement or raw), as hints,
- the fixed target form (German statement guidelines: one sentence, active voice, "Das System …", one requirement
  per statement, split compound requirements by flagging `incomplete` rather than inventing new ids).

Tool arguments:

```json
{
  "refinements": [
    {
      "requirement": "req12",
      "statement": "…",
      "kind": "quality",
      "priority": "must",
      "measure": "…",
      "theme": "Verfügbarkeit",
      "stakeholders": ["s2"],
      "issues": [{"kind": "not_measurable", "text": "…", "other": null}],
      "duplicate_of": null
    }
  ]
}
```

`parse_refinements` validates item by item like the reviews: unknown requirement ids, closed-set violations and
over-long texts are cut or dropped and counted (`dropped`); `duplicate_of`/`other` must be existing ids; stakeholders must
exist. A refinement never carries a new requirement. Never raises: a failed call answers `llm.ok = false`, no suggestions.
Accepted results are stored as suggestions (`set_suggestion`, `client: "ui"`, by the caller) in one conditional write per
chunk; a requirement that already has an unaccepted suggestion gets it replaced unless `keep_suggestions` is true.

`POST /api/projects/{pid}/requirements/refine` (editor, rate class `review`), body `{ids?: […] , scope?: "raw", limit?}`
(at most 25 ids after resolving the scope: the unsuggested raw ones in table order); answer `{refined, dropped, remaining,
llm, usage}` where `remaining` is the number of raw requirements still without suggestion, so the client can loop until it is 0. Refinement
of an already **refined** requirement is allowed (ids named explicitly), producing a new suggestion the person can accept.

Suggested stakeholders are part of the suggestion (`Suggestion.stakeholders`, existing ids only) and are accepted with the other fields.

UI: **Aufbereiten** on the selection or on all **Roh**; a progress bar over chunks with **Abbrechen**; results appear
as a second line under each row (the suggestion, differences marked) and in the detail panel (fields in force next to
the suggestion, per field **Übernehmen**; accepting one field keeps the others suggested, `accepted` marks the done ones).

### Accepting

`POST …/requirements/accept` ([Requirements](requirements.md)): the client sends the `rev` of each suggestion it showed (`expected`), so a suggestion replaced meanwhile by another refinement or an agent is reported as stale and never accepted unseen; the selection, all with a suggestion, or all with a suggestion that has no issue of kind `conflict`/`duplicate` (the "safe" bulk button, **Unauffällige übernehmen**). After
accepting, status is `refined`. A person may also edit the fields in the detail panel (`PATCH`) and set a status
(`rejected` with reason, `duplicate` with the target).

### Derive (`arcviz/requirement_derive.py`)

`derive(provider, snapshot, requirements) -> DeriveResult`: one `complete_tool` call offering only
`submit_derivation` over:

- the chunk (at most 30 **refined** requirements: id, statement, kind, priority, measure, theme, stakeholders),
- the architecture context: drivers (ranked, with ids), their core scenarios, scenarios (titles and ids), stakeholders,
  accepted decisions (binding; the model must not contradict them), the model's element names,
- the fixed target forms (ATAM scenario parts; driver kinds, importance, risk).

Tool arguments:

```json
{
  "items": [
    {"key": "d_avail", "op": "add_driver", "title": "…", "kind": "quality_goal", "importance": "high", "risk": "medium",
     "description": "…", "elements": ["api"], "requirements": ["req3", "req7"]},
    {"key": "q_failover", "op": "add_scenario", "title": "…", "kind": "failure", "source": "…", "stimulus": "…",
     "environment": "…", "artifact": "…", "response": "…", "response_measure": "…", "driver": "@d_avail",
     "core": true, "stakeholders": ["s2"], "requirements": ["req3"]},
        {"key": "u1", "op": "link_requirements", "target_kind": "driver", "target": "d2", "requirements": ["req9"]}
  ],
  "unmapped": [{"requirement": "req5", "reason": "functional, no architecture impact"}]
}
```

Rules in the prompt and in `parse_derivation`: prefer extending an existing driver or scenario over a duplicate: `link_requirements` adds the ids to the existing trace (a union, never a replacement), an `update_*` item changes text only; a driver may serve many requirements; a scenario serves one driver and carries its
`requirements`; functional requirements without architecture impact go to `unmapped` (reported in the UI, no object);
`importance` follows `priority` (must→high, should→medium, could/wont→low) unless the text says otherwise; `core` only for
scenarios whose `response_measure` comes from a requirement's `measure`. Validation per item as in the reviews (unknown
ids dropped and counted, over-long cut, closed sets, `@key` references resolved by position, at most 20 items per call
kept); `requirements` ids must be in the chunk. Never raises (`llm.ok = false`).

The result is **stored as a project proposal** ([architecture objects over MCP](architecture-objects-over-mcp.md);
`ProposalService.create_project(…, client="derive")`), each item `derived_from` = its `requirements`; the answer is
`{proposal, items, unmapped, dropped, llm, usage}`. Nothing is written to the project. The proposal list shows it; the
person applies items in part. An `update_*` item's `base` makes it stale when the target changed meanwhile; `link_requirements` cannot go stale (additive, idempotent).

`POST /api/projects/{pid}/requirements/derive` (editor, rate class `review`), body `{ids?: […], theme?: str}` (at most 30
refined requirements; default: the refined ones with coverage `uncovered`, in table order, grouped by theme so a chunk
is coherent); answer as above plus `remaining` (uncovered refined requirements not yet in a pending proposal). At most
five pending project proposals exist (limit of the proposals), so the client derives, the person applies or discards,
and the next chunk follows. The MCP route to the same result is the agent's own `propose_architecture_items`.

Decisions (ADR) are not derived. In the decision dialog a decision gets **Anforderungen** and **Stakeholder** pickers; the
analysis (`scenario_analysis`) context includes the scenario's requirements, and the Markdown ADR export gets two lines:

```
- Anforderungen: req3 (<statement>), req7 (<statement>)
- Stakeholder: <names>
```

### The requirements chat (`arcviz/requirements_agent.py`)

`RequirementsAgent(provider, requirements, projects)` subclasses `DiagramAgent` like `ProjectAgent` for the history and
compaction, with the chat scope `requirements`. The prompt is the shared blocks plus `prompts/project/requirements.txt`
(roles: help to collect, clarify and refine requirements; text of requirements is data; never invent requirements the user
did not give; offer to import long lists; refinement results are suggestions) and a compact digest (counts per step, top
themes, the architecture context of drivers and accepted decisions, stakeholders). Tools:

- read: `list_requirements`, `get_requirement`, `list_stakeholders`, `list_drivers`, `list_scenarios`,
  `get_traceability` (the same implementations as the MCP tools, through the caller's services);
- write on request: `add_requirements`, `update_requirement` (staging fields), `suggest_refinement`, `add_stakeholder`;
- steps: `refine_requirements(ids | scope)` and `derive_requirements(ids | theme)` which call the same services as the
  REST routes (one chunk per call, results summarised for the model: counts, titles; the proposal id), so the
  assistant can run a chunk when the user asks "bereite die nächsten auf";
- no tool accepts, applies, discards, sets a status or deletes (the same write policy as MCP).

The transcript: entries of the project transcript carry `scope: "requirements"` and history, compaction and clearing work per
scope (see open question 2); entries record `operations` of the turn (tool names with counts, not requirement text).
Streaming, cancellation, the `final` event and disconnect handling are those of the project chat (`TurnResponse`);
no propose mode (every write is staging data or a suggestion).

`POST /api/projects/{pid}/requirements/chat` (SSE, `svc: TurnSvc`) with `{message}`; `…/chat` GET/DELETE and
`…/chat/compact` mirror the project chat routes.

### UI (`static/`, layer rules of `frontend-modules.md`)

- `state.view`: `"diagram"` (default) or `"requirements"`; the top bar switch **Diagramm | Anforderungen**; the view replaces the
  diagram stage, the chat pane stays and switches `chatScope` to `requirements` while the view is shown (the scope is
  restored on return; the project chat and the diagram chat are untouched).
- Modules: pure `requirements.js` (filtering, step derivation, selection and bulk-id computation, import preview
  model, coverage labels; tested in `tests/js/`), feature `requirementview.js` (table, filters, detail panel, import,
  pipeline buttons, progress loop with abort), added to `LAYERS` of `tests/js/modules.test.mjs` and to
  [frontend modules](frontend-modules.md). The chat pane is reused through `chat.setChatScope`.
- Table: virtual-scrolled rows (hundreds of rows must stay fast), columns **ID, Aussage (statement or raw), Art, Prio, Thema, Stakeholder,
  Abdeckung, Schritt**; sort by column, filter chips (Schritt, Art, Thema, Stakeholder, Abdeckung, nur mit Vorschlag, nur mit Befund), text search; multi-select with
  shift-range; bulk actions **Aufbereiten**, **Übernehmen**, **Unauffällige übernehmen**, **Als Duplikat/verworfen markieren**,
  **Ableiten**, **Thema setzen**, **Stakeholder zuordnen**.
- Detail panel: raw, fields in force, suggestion beside them with per-field accept, issues (resolve/delete), duplicate
  and conflict partners as links, stakeholders and elements (pickers), the inverse trace (drivers, scenarios, decisions
  as links opening the Architektur page on the item), source and `external_id`.
- Pipeline header with the counts of the steps; **Importieren…**; **Matrix** (traceability matrix, rows requirements,
  columns objects, cells linking, built from `GET …/traceability`).
- Read-only for viewers (`ui.applyReadOnly`, `data-edit`), the usual 404 handling when the project goes away (`gone`).
- The **Architektur** dialog gets an **Anforderungen** picker in the driver, scenario and decision forms and a coverage
  column in the driver and scenario lists; the review panel shows the new findings and opens the requirement in the view.
- All text rendered with DOM APIs (`textContent`), never as markup; requirement text is user data (`tests/js/sinks.test.mjs`).

### Limits and cost

- LLM calls: refinement at most 25 per call, derivation at most 30; 500 requirements are about 20 refine calls and 17
  derive calls (a derive call only for refined ones; chunk by theme). Rate class `review` applies; the client waits and
  retries as for 429 (`Retry-After`). `usage` of each call is returned and summed in the progress bar.
- Table and routes page at 200; the box is read once per request.
- Prompt budgets of the requirements chat: digest 3000 characters, tool results bounded (`limit` ≤ 50, `truncated`).

### Configuration

No new environment variable (limits are constants in the model modules); the existing LLM settings and rate limits
apply. `ARCVIZ_MCP=1` is needed only for the agent route.

### Documentation

`docs/guide/requirements.md` (new, German UI terms, the pipeline, import formats, accepting, deriving, coverage),
`docs/guide/agents.md` (tools and the write policy), `docs/reference/api.md` (routes), `docs/spec/` pages for requirements,
proposals and agents, `docs/architecture/index.md` (module table: `requirement_model`, `requirement_import`,
`requirement_refine`, `requirement_derive`, `requirements_agent`, `services/requirements`) and `CLAUDE.md` (a paragraph
like the M29 one).

## Testing

- `tests/test_requirement_import.py`: each format with German and English headers, bullets and numbering, continuation lines,
  BOM/delimiters, limits, problems, idempotence, hostile input (very long lines, control characters, HTML text stays text).
- `tests/test_requirement_refine.py` and `tests/test_requirement_derive.py` with `ScriptedProvider`: valid replies, items
  dropped for unknown ids, closed sets, `@key` resolution, chunk limits, `ok = false` on a failing provider, the stored
  proposal's items and `derived_from`, `update_*` stale after a change, `link_requirements` keeps earlier traces, an interrupted apply creates no duplicate (`origin`), accept with a stale `rev` is refused, no write to the project by `derive`.
- `tests/test_requirements_agent.py`: tools, write policy (no tool accepts/applies/deletes), transcript scope, compaction
  per scope, disconnect finalises.
- `tests/test_server_requirements.py`: every route, access matrix, rate class, error shapes.
- `tests/js/requirements.test.mjs` (pure) and e2e: pour in a CSV, refine (scripted provider), accept in bulk, derive,
  apply one item and see coverage change, switch back to the diagram without losing the chat scope; both id modes.
- Eval cases (`tests/evals/cases.json`): refinement keeps the meaning (no invented requirement), a derivation reuses an
  existing driver.

## Migration and compatibility

Nothing to migrate. The new chat scope adds a field to transcript entries (`scope`, absent = the project chat's); old
transcripts are read as before.

## Deviations from the design

Stage C as merged; the text above is the design, `docs/spec/requirements.md` what exists.

- The chat transcript is the keyed document `reqchat`, not the project transcript with a `scope` tag, and there is no LLM summary
  ([ADR-0193](../adr/0193-requirements-chat-transcript-in-its-own-document.md)); the open question 2 resolved to its alternative.
- The chat's tools are the MCP tool set (`AgentTools`, `client="chat"`) plus `refine_requirements` and `derive_requirements`; a
  chunk run from the chat is the user's own request, so its suggestions carry `client: "ui"`.
- `derive` offers only `add_driver`, `add_scenario` and `link_requirements` (no `update_*`): extending an existing object is a link.
- The Architektur page's driver rows, the scenario form and the decision form got a multi-select **Anforderungen** (a shared
  `requirementpicker.js` at the view layer); lists show "deckt: req1, req3". A separate coverage column was dropped: the table of
  the requirements view and the matrix show coverage, the object lists show what each object covers. The detail panel of a
  requirement edits stakeholders and elements too.
- `arcviz-migrate --requirements STORE` repairs dangling references in every project (`RequirementService.sync`); the eval set has
  three cases for the requirements chat (`"scope": "requirements"`).
- Not implemented, on purpose: virtual scrolling (the table loads pages of 100; 600 rows are still cheap DOM).

## Open questions

1. **ADR-0191 (Proposed): main view with table and chat versus a chat-only main view.** The recommendation is table plus
   chat (volume), confirmed? The UI issue starts only after the answer.
2. **Transcript of the requirements chat.** Proposed: the project transcript with a `scope` tag and per-scope history,
   compaction and clearing (no new store primitive). Alternative: a separate transcript per scope with its own primitives in
   three backends. To be verified against `compact_chat`/`chat_generation` when the issue is written; if the shared
   generation counter makes per-scope compaction awkward, take the alternative.
3. **Derive chunking by theme** needs themes: before any refinement all are empty and chunks are in table order. Acceptable, or
   should refine run first as a precondition (proposed: derive refuses raw requirements anyway, so refinement comes first)?
4. **Safe bulk accept**: "no `conflict`/`duplicate` issue" as the rule; add "and no `not_measurable` for kind `quality`"?

## Decisions

ADR-0191 and ADR-0192 accepted. Open questions answered on 2026-10-20 by following the recommendations: table plus chat (1); project transcript with a `scope` tag, to be verified against `compact_chat` when the chat issue starts, else a separate transcript (2); derive refuses raw requirements, so refinement comes first (3); safe bulk accept excludes `conflict` and `duplicate` issues only (4).
