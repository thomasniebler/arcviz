# Requirements

Source: `arcviz/requirement_model.py` (types, operations, coverage, duplicate candidates),
`arcviz/services/requirements.py` (`RequirementService`), the rules in `arcviz/project_review.py`, the bundle part of
`arcviz/exchange.py`. Tests: `tests/test_requirement_model.py`, `tests/test_requirement_service.py`,
`tests/test_requirement_links.py`, `tests/test_requirement_review.py`, `tests/test_requirement_bundles.py`,
`tests/test_server_requirements.py`. Design and decisions: [Requirements](../architecture/specs/requirements.md),
ADR-0188 to ADR-0190 (write policy: ADR-0186).

A project has **requirements**: the input from which drivers and quality scenarios are derived. A requirement keeps the
original wording (`raw`, never changed) next to the sentence a person accepted (`statement`). An assistant's
refinement is only a `suggestion` until a person accepts it. Drivers, scenarios and decisions say which requirements
they address (`requirements`), so traceability and **coverage** are computed.

## Storage

The requirements of a project are one keyed document of kind `requirements` (key = project id; file `requirements/`,
sqlite table `documents`, Cosmos partition `requirements:<key>`), not part of the project document: 200 to 500
entries would crowd the project's 1.9 MB guard and a bulk write would contend on the project revision. The document
carries the project's incarnation, so a project created again under a deleted id starts empty. It is written
conditionally on its revision, at most `MAX_REQUIREMENTS_BYTES` (1 900 000) and at most `MAX_REQUIREMENTS` (600)
entries; a write over either limit is refused with `RequirementError`.

References to stakeholders and elements are checked on write and **tolerated when dangling on read** (the API leaves
them out and counts them in `dangling`). Removing a stakeholder or element cascades into the requirements as a second
write; removing a requirement strips its id from drivers, scenarios and decisions; a failure between the two writes
leaves ids that reads ignore and `RequirementService.sync` removes.

## Model

```python
class Requirement(BaseModel):
    id: str  # req<n>, never reused
    raw: str  # ≤ 1500, one line, immutable
    statement: str = ""  # ≤ 400
    kind: str = "unclear"  # functional | quality | constraint | business | unclear
    status: str = "raw"  # raw | refined | rejected | duplicate
    priority: str = ""  # "" | must | should | could | wont (MoSCoW)
    measure: str = ""  # ≤ 300
    theme: str = ""  # ≤ 80
    source: str = ""  # ≤ 150
    external_id: str = ""  # ≤ 80; (source, external_id) is unique
    stakeholders: list[str] = []  # ≤ 10 ids
    elements: list[str] = []  # ≤ 20 ids
    issues: list[Issue] = []  # ≤ 4: ambiguous | not_measurable | incomplete | duplicate | conflict | out_of_scope
    duplicate_of: str | None = None  # set exactly for status duplicate
    reason: str = ""  # ≤ 250, why rejected
    suggestion: Suggestion | None = None
    author, author_name, client, created_at, updated_at
```

A `Suggestion` holds `statement`, `kind`, `priority`, `measure`, `theme`, `stakeholders`, `issues`, `duplicate_of`, who made
it (`by`, `by_name`, `client`), `at`, a `rev` (fingerprint of the suggested content) and `accepted` (the fields already
in force). Integrity (`requirement_model.integrity_errors`): unique ids, closed sets, non-empty `raw`, refined needs a
statement, `duplicate_of` names another requirement and forms no cycle, `external_id` unique per source.

**Links on the architecture objects** (omitted from dumps while empty, so stored data and fingerprints do not change):
`Driver.requirements`, `Scenario.requirements`, `Decision.requirements` (≤ 20 ids), `Scenario.stakeholders` and
`Decision.stakeholders` (≤ 10 ids, with the cascade of `remove_stakeholder` and a check in `Project.integrity_errors`),
and `origin` (`<proposal>/<key>`, ≤ 40, set by the proposal apply, never exported). `add_`/`update_` of driver, scenario and
decision take `requirements` (a replacement: the human edit); the additive `link_requirements(target_kind, target,
requirements)` adds ids to the union and is idempotent. `ProjectService` checks that every named requirement exists
(`Unknown requirement 'req9' … Requirements: …`); `Project.integrity_errors` cannot, and says so.

## Operations

`requirement_model.apply(box, op, args, known=None, meta=None)`: signature checked, deep copy and rollback on any
exception, integrity asserted, `RequirementError` (an `OperationError`) for bad input. `apply_each` runs many steps with one
snapshot (an import, a bulk accept): a failing step is undone and reported, the others stay.

`add_requirement`, `update_requirement` (never `raw`; a statement on a raw requirement leaves it raw), `set_status`
(`refined` needs a statement, `duplicate` needs `duplicate_of`), `set_suggestion`, `accept_suggestion(requirement,
expected, fields?)`, `reject_suggestion`, `resolve_issue`, `remove_requirement`.

`accept_suggestion` moves the named fields (default: all not accepted yet) into force and records them in `accepted`;
it is **refused (`StaleSuggestion`) when `expected` is not the suggestion's current `rev`**, so a suggestion replaced by
another refinement or an agent is never accepted unseen. Accepting `statement` makes the requirement `refined`; accepting
`duplicate_of` makes it a `duplicate`; the suggestion is cleared when every field is accepted, otherwise the rest
stays suggested.

**Agent policy** (ADR-0186): `requirement_model.check_agent` allows `add_requirement`, `update_requirement` of `source`,
`theme`, `priority`, `stakeholders`, `elements`, and `set_suggestion`; everything else raises `Rejected` naming what
the agent may do. The service applies it when `agent=True`.

## Coverage

`coverage_map(box, project)` (computed on read, never stored): `decision` when a decision names the requirement,
else `scenario`, else `driver`, else `uncovered`; raw and rejected requirements have `None`; a duplicate counts through the
requirement it duplicates. `traces(project)` is the inverse index. `step_of` gives the pipeline step (`raw`, `suggested`,
`refined`, `derived`, `rejected`, `duplicate`) and `digest` the counts per step, kind and theme (30 themes).

`similarity` is the Jaccard similarity of the casefolded word sets (words of 3+ characters, no stemmer, language neutral);
`duplicate_candidates(box, ids?, threshold=0.7, limit=200)` finds pairs through an inverted index over the words.

## Service

`RequirementService` (viewer reads, editor writes; `Services.requirements`): `page` (filters `status`, `kind`, `theme`,
`stakeholder`, `coverage` (a level or `none`), `step`, `q`, `suggested`; `limit` 1 to 200; answer with `total`, `digest`,
`dangling`), `get` (with the inverse trace), `traceability`, `box`, `apply`, `import_items` (idempotent by
`(source, external_id)` and normalised text, at most 500 per call; rejected rows are reported, not fatal), `suggest_many`,
`accept` (`{accepted, stale, skipped}`), `set_status_many`, `remove`, `restore` (a bundle import), `sync`.

## HTTP

| Route | Need | Notes |
|---|---|---|
| `GET /api/projects/{pid}/requirements` | viewer | filters as above, `{requirements, total, digest, dangling}` |
| `POST /api/projects/{pid}/requirements` | editor | body = `add_requirement` arguments, 201 |
| `GET`, `PATCH`, `DELETE /api/projects/{pid}/requirements/{rid}` | viewer / editor | PATCH body = `update_requirement` fields |
| `POST /api/projects/{pid}/requirements/accept` | editor | `{items: [{id, expected, fields?}]}` → `{accepted, stale, skipped}` |
| `POST /api/projects/{pid}/requirements/status` | editor | `{ids, status, duplicate_of?, reason?}` → `{changed, skipped}` |
| `POST /api/projects/{pid}/requirements/delete` | editor | `{ids}` (at most 500) → `{removed, skipped: [{id, reason}]}`: one requirement write, one strip of the references from drivers, scenarios and decisions (`RequirementService.remove_many`, ADR-0198); registered before `/requirements/{rid}` |
| `POST /api/projects/{pid}/requirements/{rid}/reject-suggestion` | editor | |
| `PATCH /api/projects/{pid}/requirements/{rid}/issues/{index}` | editor | `{resolved}` |
| `GET /api/projects/{pid}/traceability` | viewer | `{rows, objects}` for the matrix |

Invalid input is a 400 with a message written for the caller; strangers get 404, viewers 403 on writes
(`tests/test_access_matrix.py`).

## Project review

Nine deterministic rules read the box once per run (`ProjectSnapshot.requirements`; `ProjectFinding.requirements` names the
requirement ids): `requirement-unrefined` (raw for more than a day), `requirement-uncovered` (refined, no driver, scenario
or decision; one finding per theme), `requirement-no-stakeholder`, `quality-requirement-no-measure`,
`requirement-conflict-open`, `requirement-duplicate-open` (an open `duplicate` issue, or two refined statements above the
similarity threshold), `scenario-no-requirement`, `decision-no-basis` (accepted, no driver and no requirement) and
`stakeholder-no-requirement` (influence high). Details in [Architecture review](review.md).

## Repair

`arcviz-migrate --requirements STORE` (`cli._sync_requirements`) runs `RequirementService.sync` for every project of the store:
stakeholder and element references that no longer exist are removed from the requirements, requirement ids that no longer exist from
drivers, scenarios and decisions; it prints one line per repaired project and is safe to repeat. The start-up does not run it
(the references are tolerated when dangling, ADR-0188).

## Bundles

A project bundle is `schema_version` 7 whenever it holds a `project.requirements` list (with `requirement_seq`) or any
`requirements`/`stakeholders` link on a driver, scenario or decision; otherwise it keeps its earlier version, so an older
version refuses what it would silently drop. The import validates everything first, renumbers the requirements `req1…` in
bundle order, makes links of drivers, scenarios and decisions and `duplicate_of` follow, drops (and reports) links to
requirements the bundle does not hold, never imports `origin`, and writes the box under the new project's incarnation
(`RequirementService.restore`).

## Agents

The MCP tools for requirements and the architecture objects, and project proposals, are described in [Agent access](agents.md).

## Workshop: import, refine, derive, chat

Design: [Requirements workshop](../architecture/specs/requirements-workshop.md), ADR-0191 to ADR-0193. Tests:
`tests/test_requirement_import.py`, `tests/test_requirement_refine.py`, `tests/test_requirement_derive.py`,
`tests/test_requirements_agent.py`, `tests/js/requirements.test.mjs`, e2e step 20.

**Import** (`requirement_import.py`, pure, no model). `parse(text, fmt, source=, max_items=500)`: `lines` strips bullets, numbers and
an id prefix (`REQ-12:` becomes the external id) and joins indented continuation lines; `markdown` takes the list items, headings
become the theme and prose between lists is ignored; `csv` needs a header with a text column (`text`, `requirement`,
`anforderung`, `beschreibung`, `raw`, `description`, `aussage`), optional id, source, theme, priority (MoSCoW or German
muss/soll/kann) and stakeholder (names, matched to existing stakeholders; unknown ones are problems, never created) columns,
delimiter `, ; \t`; `auto` picks csv for a header, markdown for a heading, else lines. Texts over 1500 characters are shortened,
more than 500 items cut, each reported as a problem with its row. `prepare` maps stakeholder names to ids.

**Refine** (`requirement_refine.py`). `select_chunk` (the named ones, or the raw ones without a suggestion in table order, at most
`MAX_CHUNK` 25), `refine_input` (stakeholders, elements, themes, drivers, the duplicate candidates of the chunk, the chunk),
`parse_refinements` (unknown ids, empty statements, values outside the closed sets, unknown stakeholders and partners are dropped and
counted; one suggestion per requirement), `refine` (one `complete_tool` call `submit_refinements`, never raises). The result is
stored by `RequirementService.suggest_many` as suggestions; nothing in force changes.

**Derive** (`requirement_derive.py`). `select_chunk` (refined requirements that nothing covers and no pending project proposal serves,
by theme then number, at most `MAX_CHUNK` 30; a named raw requirement is refused), `derive_input` (drivers with core scenarios, other
scenarios, stakeholders, elements, the accepted decisions that bind, the chunk), `parse_derivation` (items `add_driver`, `add_scenario`,
`link_requirements`; at most 20; every item names requirements of the chunk; a driver or scenario whose title exists becomes a
`link_requirements` to it; importance follows the MoSCoW priority; `core` only with a response measure; `@key` drivers only from earlier
`add_driver` items), `derive`. `requirement_workshop.derive_chunk` stores the items as a project proposal (client `derive`); a
`ProposalError` such as five pending project proposals is a 400.

**Chat** (`requirements_agent.py`, `services/requirement_chat.py`). Tools: the MCP read tools and writes of `AgentTools(svc, "write",
client="chat")` (same write policy, ADR-0186) plus `refine_requirements` and `derive_requirements` (`requirement_workshop`); no tool
accepts, applies, deletes or sets a status. The prompt is `prompts.requirements_prompt_prefix()`, the architecture block (drivers, core
scenarios, stakeholders, accepted decisions) and the project's counts per step and themes; the requirements are read with tools. The
transcript is the keyed document `reqchat` per project (ADR-0193): the newest `MAX_ENTRIES` 60 entries, each cut to 8000 characters,
the project's incarnation and a `generation` that a clear replaces; written in a `finally` block (a closed stream keeps its entries), not
written when cleared meanwhile. `operations` of an entry are counts per write tool, never texts. Routes in the [API reference](../reference/api.md).

**View** (`static/requirementview.js`, pure `requirements.js`; `chat.js` `setMainView`): the nav button **Anforderungen** (and **Zum
Diagramm**) switch `state.mainView`; the view replaces the diagram stage (`.layout.requirements-mode`) and the chat pane takes the scope
`requirements`. See the [frontend](frontend.md) page for the modules.
