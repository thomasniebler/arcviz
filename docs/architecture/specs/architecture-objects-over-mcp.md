# Architecture objects over MCP: stakeholders, drivers, scenarios, decisions

- Status: Implemented (M31, with the deviations below)
- Issues: to be created after review
- ADRs: [0186 Agents add and edit staging data directly; binding architecture objects only through proposals](../adr/0186-agents-add-and-edit-but-never-decide.md),
  [0187 Project proposals carry independent items that a person applies in part](../adr/0187-project-proposals-with-items-and-partial-apply.md)
- Builds on: [Agent access](agent-access.md) (ADR-0170..0172), [Agent authentication](agent-authentication.md)
  (ADR-0173), [Decision support](decision-support.md) (ADR-0166..0168), [Architecture drivers](architecture-drivers.md)
- Used by: [Requirements](requirements.md), [Requirements workshop](requirements-workshop.md)

## Summary

An MCP agent can read the architecture context (drivers, core scenarios, accepted decisions, stakeholders) but write
none of it. This change gives agents the missing tools, split by weight (ADR-0186):

- **Stakeholders** (and, in [Requirements](requirements.md), requirements) are **written directly**: add and edit, never
  delete, never decide.
- **Drivers, scenarios and decisions** are **proposed**: a stored *project proposal* with independent items that a person
  applies in whole or in part in the UI (ADR-0187). A decision proposed by an agent is a `draft`.
- Agents get read tools for all of them, so they can reuse what exists instead of duplicating it.

## Goals

- A coding or analysis agent can list and fetch stakeholders, drivers, scenarios and decisions in compact, bounded
  form and propose new or changed ones.
- Nothing binding changes without a person: no tool accepts a decision, applies a proposal or deletes an object.
- The same access rules and the same validation as the UI: every item is checked by `projects.apply` on a copy.
- One proposal mechanism for diagrams and project objects; no second list in the UI.

## Non-goals

- Deleting objects over MCP (a human action in the UI).
- Letting an agent accept or supersede a decision, or set `core` on a scenario that a person did not review: `core` can
  be proposed (it is part of the item) but takes effect only when applied by a person.
- MCP resources, prompts, sampling (as in agent access: tools only).
- Changing the chat assistants' own write behaviour (they keep acting on request of the present user).

## Design

### Write policy

| Object | Agent may | Agent may not |
|---|---|---|
| Stakeholder | `add_stakeholder`, `update_stakeholder` (direct) | delete |
| Requirement | add, edit staging fields, suggest a refinement (direct; [Requirements](requirements.md)) | delete, change `statement`/`kind`/`measure`/`status`/`duplicate_of`, accept |
| Driver, scenario | propose `add_*`/`update_*` | write directly, delete |
| Decision (ADR) | propose `add_decision` (always `draft`) and `update_decision` of text fields | write directly, set `status`, `chosen` of an accepted one, supersede, delete |
| Proposal | create, list, get | apply, discard, accept an item |

Enforcement is in the services (`ProposalService.create_project`, `StakeholderService` direct methods) and checked in
`tests/test_agent_tools.py`; the tool set simply has no tool for the right column. `status` of a proposed decision is
rejected at creation (`AGENT_REFUSED_FIELDS = {"decision": {"status", "superseded_by"}}`); a proposed `add_decision` is
stored with `status: "draft"`.

### Direct writes: stakeholders

`AgentTools.add_stakeholder(project, name, role?, concerns?, influence?, drivers?)` and
`update_stakeholder(project, stakeholder, fields…)` call `ProjectService.apply` with the existing public operations
`add_stakeholder`/`update_stakeholder`. Editor role. They return the stakeholder in the compact read form. The
operations' bounds (50 stakeholders, unique names, driver references) apply; errors list the known ids.

### Project proposals (`proposals.py`, `services/proposals.py`)

The `proposal` document and service of agent access are extended; diagram proposals are unchanged.

```python
class ProposalItem(BaseModel):
    key: str  # local label, [a-z0-9_]{1,24}, unique within the proposal
    op: str  # one of PROPOSABLE_OPERATIONS
    arguments: dict[str, Any]  # as the operation takes them; references to earlier items are "@<key>"
    derived_from: list[str] = []  # requirement ids this item came from (traceability, applied as `requirements`)
    base: str | None = None  # update_*: fingerprint of the target object when the item was made
    state: str = "pending"  # pending | applied | discarded | stale | failed
    created: str | None = None  # the id an applied add_* produced
    error: str | None = None  # why a failed or stale item was not applied
```

`StoredProposal` gets `kind: str = "diagram"` (`"diagram" | "project"`; absent in stored documents = diagram), and for
`kind == "project"`: `items: list[ProposalItem]`, `diagram = ""`, `base`/`result`/`diff` empty. `PROPOSABLE_OPERATIONS` =
`add_driver`, `update_driver`, `add_scenario`, `update_scenario`, `add_decision`, `update_decision`, `link_requirements` (additive, [Requirements](requirements.md)). An `update_*` item must not carry `requirements` (a replacing list could drop an earlier trace); the trace goes into `link_requirements`, or into `requirements` of an `add_*`.

**Create** (`ProposalService.create_project(project, items, rationale, client, actor)`, editor):

1. Bounds: at most `MAX_ITEMS` (60) items, at most `MAX_PROJECT_PROPOSAL_BYTES` (64 KB) of items, at most
   `MAX_PENDING_PROJECT` (5) pending project proposals per project (else an error that asks to wait for a decision),
   rationale ≤ 2000.
2. Each item is validated on a deep copy of the project, **in order**, through `projects.apply` (so the argument contract,
   bounds, uniqueness and references are those of the UI); `@key` arguments are resolved to the id the earlier item
   *would* get from the sequence counters. One failing item rejects the whole proposal with that item's error (it names
   the key), nothing is stored; the agent resubmits.
3. `update_*` items record `base` (the target object's fingerprint). `derived_from` ids must exist in the requirement
   document; unknown ids are an error.
4. Items of `add_decision` get `status: "draft"`; `AGENT_REFUSED_FIELDS` and `requirements` on `update_*` are refused.

**Apply** (`apply(project, proposal, accept: list[str] | None, actor)`, editor, REST only): `accept` = item keys, `None` =
all pending items. Dependencies (an item that references `@key`) must be accepted too or already applied; otherwise the
call is refused with the missing keys. The service claims the proposal (`status: applying`, conditional on the
revision, as agent access does), then applies the selected items **in order** through `ProjectService.apply` on the real
project; each item that succeeds becomes `applied` and records `created`; an `update_*` whose target fingerprint changed
becomes `stale` and its dependents `failed`. `derived_from` of an `add_*` item is written as `requirements` of the created object, of an `update_*` item as a `link_requirements` (union, ADR-0189). Items are independent writes (project revision conditional
each); **idempotence across crashes:** every `add_*` replay sets `origin = "<proposal id>/<key>"` on the object it creates, in the same conditional project write; before replaying a pending `add_*` item the service looks for an object with that `origin` (a scan of the project's own lists, no extra read) and, if found, records it as `created` instead of creating a second one. An interrupted apply is therefore completed by calling apply again (applied items are skipped by `created`, unrecorded ones are found by `origin`, `link_requirements` is idempotent). The proposal is
`applied` when no item is `pending`.

**Discard** (`discard(project, proposal, keys | None, actor)`, REST only): the named items (all when `None`) become
`discarded` (with their dependents).

**Size.** A decided project proposal keeps per item only `key`, `op`, `state`, `created`, `error` and the item's title
(≤ 160): about 400 bytes per item, at most 24 KB. Pending ones: at most 5 × 64 KB = 320 KB, plus the diagram
proposals' bounds of agent access (about 1 MB at the extremes); the document stays below the 2 MB item limit. The
retention sweep and `KEEP_DECIDED` apply to both kinds.

REST (all `svc: Svc`, entries in `tests/test_access_matrix.py`):

| Method | Path | Need | Notes |
|---|---|---|---|
| GET | `/api/projects/{pid}/proposals` | viewer | lists both kinds, `kind` in each row |
| GET | `/api/projects/{pid}/proposals/{id}` | viewer | items with `state`, titles resolved for the UI |
| POST | `/api/projects/{pid}/proposals/{id}/apply` | editor | body `{accept?: [key]}`; diagram proposals ignore it |
| POST | `/api/projects/{pid}/proposals/{id}/discard` | editor | body `{keys?: [key]}` |

### New MCP tools (`agent_tools.py`, registered in `mcp_server.build_mcp`, listed in `TOOL_NAMES`)

All results are bounded JSON in the style of agent access (`limit`, `truncated`, `next`).

| Tool | Access | Result |
|---|---|---|
| `list_stakeholders(project)` | viewer | id, name, role, influence, driver ids |
| `list_drivers(project, kind?, limit?)` | viewer | ranked: id, title, kind, importance, risk, scenario count, requirement ids |
| `get_driver(project, driver)` | viewer | the driver with its scenarios, stakeholders, decisions that name it |
| `list_scenarios(project, driver?, limit?)` | viewer | id, title, kind, driver, core, measure |
| `get_scenario(project, scenario)` | viewer | the six parts, driver, stakeholders, requirements, decisions |
| `list_decisions(project, status?, limit?)` | viewer | id, title, status, driver ids, requirement ids |
| `get_decision(project, decision)` | viewer | the whole ADR (options, chosen, texts, links) |
| `add_stakeholder`, `update_stakeholder` | editor | the stakeholder |
| `propose_architecture_items(project, items, rationale)` | editor | the stored proposal: id, items with keys and state, the problems if rejected |

`list_proposals`/`get_proposal` of agent access also show project proposals (`kind`, item states and `created` ids), so an
agent learns what a person took. `get_architecture_context` additionally returns counts of requirements by status and the
number of uncovered refined ones (pointer to `list_requirements`, [Requirements](requirements.md)).

Tool descriptions state the rule once: *"Proposals are applied by a person; accepted decisions bind, do not contradict
them. Reuse an existing driver or scenario (use its id in `update_*` or `@key` references) instead of adding a duplicate."*

### Chat assistants

No change to their write behaviour. `propose_architecture_items` is **not** a chat tool (the chat has `create_scenario` and
the dialogs). The proposal list in the UI shows both kinds.

### UI

The **Vorschläge** list of the navigator shows project proposals with a count of pending items; opening one shows the
items as cards (operation, title, the fields that differ, `derived_from` as requirement titles) with a checkbox per item,
**Ausgewählte übernehmen**, **Alle übernehmen** and **Verwerfen** (all or selected). Items with unmet dependencies are
disabled with the reason. Pure helpers in `static/proposal.js` (selection closure over dependencies) with tests in
`tests/js/`.

### Observability and limits

The span and metric of agent access cover the new tools (tool names are added to `TOOL_NAMES`, no other attribute). Logs
never contain item arguments. Rate class `agent`.

## Testing

- `tests/test_proposals.py` (service, all store backends): project proposal create/list/get/apply/discard, `@key`
  resolution, dependency refusal, partial apply, stale `update_*`, interrupted apply completed by a second call,
  caps (`MAX_ITEMS`, bytes, pending), retention, `AGENT_REFUSED_FIELDS`, access (viewer cannot create, other user 404),
  diagram proposals unchanged (golden documents without `kind`).
- `tests/test_agent_tools.py` and `tests/test_mcp_server.py`: every tool against a seeded project, errors name known ids,
  `truncated`, **no tool accepts, applies, discards or deletes**, an agent cannot set a decision's status.
- `tests/test_access_matrix.py`, `tests/test_access.py`, `tests/test_layering.py` updated.
- `tests/js/proposal.test.mjs` for the selection closure; e2e: an MCP client proposes two items with a dependency, a
  user applies one, the agent reads `applied` and `created`.
- Eval case: the assistant in the MCP guide reuses an existing driver id instead of adding a duplicate.

## Migration and compatibility

No migration. `kind`, `items` are new fields of stored proposals; `kind` defaults to `"diagram"`. A downgrade loses the
project proposals' items when the older version rewrites the document (it ignores unknown fields): state it in the
release notes; pending project proposals are best applied or discarded before a downgrade.

## Deviations from the design

Implemented in stage B; the text above is the design, `docs/spec/agents.md` what was merged.

- Project proposals use the existing `proposal` document, service and routes; `StoredProposal.kind` and `items` are omitted from
  dumps for diagram proposals. The apply body is `{"accept": [keys]}`, the discard body `{"keys": [keys]}`.
- A decided project proposal also becomes `stale` (only stale or failed items) besides `applied` and `discarded`.
- `ProposalItem` carries a `title` for lists. The `update_*` fingerprint (`base`) is recorded only for items whose arguments name no
  `@key`; an item on an object created in the same proposal cannot go stale.
- `requirements` on an `update_*` item is refused (use `link_requirements`), as the review of the design asked.
- The tools `add_requirements`, `update_requirement`, `suggest_refinement` and the requirement reads belong to this change as well
  (they need the requirement service of [Requirements](requirements.md)).
- Not implemented: per-item editing before apply (open question 2 stays "no"). The eval set covers the requirements chat (same tool set, `"scope": "requirements"`), not the MCP guide.

## Open questions

1. **Cap of pending project proposals** (5) and items (60): sized for a derivation in chunks of 30 requirements
   ([workshop](requirements-workshop.md)); raise if a derivation regularly needs more.
2. **Should a person be able to edit an item before applying it?** Proposed: not in this change (apply or discard; the
   edit happens afterwards in the dialog), to keep the stored proposal immutable.

## Decisions

ADR-0186 and ADR-0187, accepted.
