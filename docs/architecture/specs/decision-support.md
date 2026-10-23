# Decision support: stakeholders, quality scenarios, what-if analysis, decisions

- Status: Implemented (milestone M29)
- Issues: #231 (stakeholders), #232 (quality scenarios), #226 (what-if analysis and decisions), #233 (decisions guide the
  assistants and the reviews)
- ADRs: [0166 Stakeholders, scenarios and decisions live in the project document](../adr/0166-decision-support-in-the-project-document.md),
  [0167 An analysis is kept only as the decision it led to](../adr/0167-analyses-are-kept-only-as-decisions.md),
  [0168 Accepted decisions bind the assistants and the reviews by prompt](../adr/0168-accepted-decisions-bind-by-prompt.md)
- Builds on: [Architecture drivers](architecture-drivers.md) (ADR-0163), [LLM review](llm-review.md),
  [Project review](project-review.md), [Assistant prompts](assistant-prompts.md)

## Summary

M27 gave a project prioritised architecture drivers. M29 completes the ATAM-style loop around them:

- **Stakeholders**: who has a stake in the architecture, their concerns, influence and the drivers they care about.
- **Quality scenarios** in the six parts of ATAM (source, stimulus, environment, artifact, response, response
  measure). A driver has several scenarios that describe it; a scenario serves at most one driver. Scenarios marked
  **core** state the driver's core goals.
- **What-if analysis** of a scenario: one LLM call rates it against the drivers, stakeholders and accepted decisions,
  names affected elements, sensitivity points and trade-offs, and proposes 2–4 options with pros and cons. The analysis
  is shown, **not stored**.
- **Decisions**: choosing an option turns the analysis into an **ADR draft** (context, options with pros and cons, the
  decision with its rationale, consequences) that the user edits and accepts. Decisions have a status and export as
  Markdown ADRs. **Accepted** decisions bind: both chat assistants and the AI reviews see them and point out when a
  change or a diagram contradicts one.

The UI gathers drivers, scenarios, decisions and stakeholders in one **Architektur** dialog with tabs.

## Goals

- Record stakeholders, scenarios and decisions per project with bounded sizes; data written before stays identical.
- Analyse a scenario on demand without storing anything; keep only what was decided, in a reviewable form.
- Make accepted decisions part of every later design step (chat, review) without a separate checker.

## Non-goals

- A rule-based check of diagrams against decisions (decisions are free text; the LLM review reads them).
- A check of a proposal against the decisions before it is applied (one more LLM call; recorded in
  `docs/product/ideas.md`).
- Generating arc42 chapters, wiki pages per element, Git-backed history (ideas parking lot).
- Live refresh of the dialog while another user edits.

## Design

### Model (`arcviz/decision_model.py`, used by `projects.py`)

```python
INFLUENCE = DRIVER_LEVELS  # high, medium, low


class Stakeholder(BaseModel):
    id: str  # s<n> from Project.stakeholder_seq
    name: str  # ≤ 120, unique (case-insensitive)
    role: str = ""  # ≤ 120
    concerns: str = ""  # ≤ 1000
    influence: str = "medium"  # INFLUENCE
    drivers: list[str] = []  # ids of drivers they care about, ≤ 20


SCENARIO_KINDS = ("usage", "growth", "change", "failure", "threat", "cost", "other")


class Scenario(BaseModel):
    id: str  # q<n> from Project.scenario_seq
    title: str  # ≤ 160, unique (case-insensitive)
    kind: str = "usage"  # SCENARIO_KINDS
    source: str = ""  # who or what causes the stimulus           \
    stimulus: str = ""  # the event ("Was, wenn …")                 |
    environment: str = ""  # the conditions (normal load, failover) | each ≤ 500
    artifact: str = ""  # what is stimulated (system, component)    |
    response: str = ""  # how the system responds                   |
    response_measure: str = ""  # how the response is measured     /
    driver: str | None = None  # the one driver it serves
    core: bool = False  # a core scenario: a core goal of its driver
    elements: list[str] = []  # project elements it concerns, ≤ 20


DECISION_STATUSES = ("draft", "accepted", "superseded", "rejected")


class DecisionOption(BaseModel):
    title: str  # ≤ 160
    description: str = ""  # ≤ 1000
    pros: list[str] = []  # ≤ 6 × 300
    cons: list[str] = []  # ≤ 6 × 300


class Decision(BaseModel):
    id: str  # adr<n> from Project.decision_seq, shown as "ADR-<n>"
    title: str  # ≤ 160
    status: str = "draft"  # DECISION_STATUSES
    context: str = ""  # ≤ 3000
    options: list[DecisionOption] = []  # ≤ 5
    chosen: int | None = None  # index into options
    decision: str = ""  # the decision and its rationale, ≤ 3000
    consequences: str = ""  # ≤ 3000
    scenario: str | None = None  # the scenario it came from
    drivers: list[str] = []  # ≤ 20
    elements: list[str] = []  # ≤ 20
    superseded_by: str | None = None
    created_at: str
    decided_at: str | None = None  # set when it becomes accepted
```

`Project.stakeholders`, `Project.scenarios`, `Project.decisions` and their `*_seq` counters are omitted from dumps
while empty / 0. Limits: `MAX_STAKEHOLDERS` 50, `MAX_SCENARIOS` 200, `MAX_DECISIONS` 100. The worst case of all
bounds together exceeds the project document's size guard (`MAX_PROJECT_BYTES`); the guard refuses such a write with
its usual message (ADR-0166). Typical entries are far smaller (a decision of an analysis: about 3–5 kB).

Integrity (part of `Project.integrity_errors`): unique ids per kind, values from the closed sets, references to
existing drivers, elements, scenarios and decisions, `chosen` within `options`, `superseded_by` not the decision
itself.

### Operations (`projects.py`, public: one document)

`add/update/remove_stakeholder`, `add/update/remove_scenario`, `add/update/remove_decision`
(`projects.DECISION_SUPPORT_OPERATIONS`). References are given as ids (drivers, elements also by title/name).
Cascades in the same write: `remove_driver` drops the driver from stakeholders and decisions and unassigns its
scenarios; `remove_element` drops the element from scenarios and decisions; `remove_scenario` clears `scenario` of the
decisions that came from it; `remove_decision` clears `superseded_by` pointing at it. `update_decision` with
`status="accepted"` sets `decided_at` (when not set yet); `status="superseded"` requires `superseded_by` (given or
already set). Errors list the known ids, as the other project operations do.

### HTTP (all through `ProjectService.apply`, access matrix entries)

| Method | Path | Need | Answer |
|---|---|---|---|
| GET, POST | `/api/projects/{pid}/stakeholders` | viewer / editor | `{project, stakeholders}`; POST 201 `{stakeholder, …}` |
| PATCH, DELETE | `/api/projects/{pid}/stakeholders/{id}` | editor | `{stakeholder, project, stakeholders}` |
| GET, POST | `/api/projects/{pid}/scenarios` | viewer / editor | `{project, scenarios}` |
| PATCH, DELETE | `/api/projects/{pid}/scenarios/{id}` | editor | `{scenario, project, scenarios}` |
| POST | `/api/projects/{pid}/scenarios/{id}/analysis` | viewer | the analysis (below); nothing stored; rate class `review` |
| GET, POST | `/api/projects/{pid}/decisions` | viewer / editor | `{project, decisions}` |
| PATCH, DELETE | `/api/projects/{pid}/decisions/{id}` | editor | `{decision, project, decisions}` |
| GET | `/api/projects/{pid}/decisions/{id}/markdown` | viewer | `text/markdown`, the ADR |

### What-if analysis (`arcviz/scenario_analysis.py`)

`analyse(provider, snapshot, scenario_id) -> dict`: one `LLMProvider.complete_tool` call offering only
`submit_scenario_analysis`, over a digest of the project (title, structure, model elements and relations, drivers with
their core scenarios, stakeholders, **accepted** decisions, the diagrams' summaries within the project review's budget)
and the scenario in its six parts. The tool's arguments:

```json
{
  "summary": "…",
  "drivers": [{"driver": "d1", "effect": "supports|at_risk|violates|neutral", "reason": "…"}],
  "elements": [{"element": "api", "reason": "…"}],
  "sensitivity_points": ["…"],
  "tradeoffs": ["…"],
  "options": [{"title": "…", "description": "…", "pros": ["…"], "cons": ["…"], "instruction": "…"}],
  "recommended": 0
}
```

`parse_analysis` validates item by item like the reviews: unknown driver or element ids are dropped (counted in
`dropped`), texts are cut to the decision bounds, 1–4 options are kept (a missing title drops the option), and
`recommended` must name a kept option. An option's `instruction` is a German instruction to the chat assistant that a
user can send as a **preview** turn (like review suggestions). Never raises: a failed call answers `llm.ok = false`
and no options. The answer adds `scenario` (the scenario as analysed) and `usage`.

### From an option to a decision (UI, `static/decisions.js`)

`decisionFromAnalysis(scenario, analysis, index)` builds the body of `POST …/decisions`: title (the option's title
with the scenario's), `context` (the scenario's six parts, the summary, sensitivity points and trade-offs), all
`options` with pros and cons, `chosen` = index, `decision` ("Gewählt: … — Begründung: …" from the option, to be
edited), `consequences` (the chosen option's pros and cons), `scenario`, `drivers` (the scenario's and every rated
driver except `neutral`), `elements` (the affected ones), status `draft`. The analysis is not kept anywhere else
(ADR-0167).

### Markdown ADR (`decision_model.decision_markdown`)

In German, like the UI:

```
# ADR-3: <title>

- Status: Angenommen (<decided_at date>) | Entwurf | Verworfen | Ersetzt durch ADR-<n>
- Szenario: <title> (q4)
- Treiber: <titles>
- Elemente: <names>

## Kontext
## Optionen        (### per option, "Pro:" / "Contra:" lists, the chosen one marked "(gewählt)")
## Entscheidung
## Konsequenzen
```

### Assistants and reviews (ADR-0168)

- Prompt blocks after the drivers block, both chats: **stakeholders** (`- [s1] Name (role, influence high): concerns;
  cares about d1, d2`), **core scenarios** as part of their driver's line (`core: [q2] stimulus → response measure`),
  and **accepted decisions** (`prompts/shared/decisions.txt` + `- [adr3] Title — decision (shortened to 300)`),
  each budgeted (stakeholders and decisions 3 000 characters each, the lowest first cut and counted).
- `decisions.txt` (the stakeholders and core-scenario hints are in `drivers.txt`): accepted decisions are binding; when a request would contradict one, name it (ADR-<n>) and ask
  before changing anything; propose superseding it rather than silently deviating.
- Agent tools: `list_stakeholders`, `list_scenarios`, `list_decisions` (read, also in preview), `create_scenario`
  (on request, refused in preview). Decisions are made in the dialog, not by the assistant.
- Reviews: `review.review(provider, diagram, decisions=…)` and `review_project` add the accepted decisions to their
  input; their system prompts ask for a risk finding whose message names the decision ("widerspricht ADR-3: …")
  when the diagram or project contradicts one. The diagram review route reads the diagram's project.

### UI

**Architektur** in the navigator (replacing **Treiber**) opens the page `#architecture-view` (since the [app shell](app-shell.md): it was a dialog `#arch-dialog`) with tabs **Treiber**, **Szenarien**,
**Entscheidungen**, **Stakeholder** (`static/archview.js`, layer 6 above the tab modules: tabs; `driverpanel.js` keeps the drivers tab;
`static/scenariopanel.js`, `static/decisionpanel.js`, `static/stakeholderpanel.js`; pure helpers `static/decisions.js`).

- Szenarien: grouped by driver (unassigned last), core scenarios first and starred; a form with the six parts;
  **Analysieren** shows the analysis inline (driver effects, elements, trade-offs, options with pros and cons, the
  recommended one marked); per option **Als Entscheidung festhalten** (creates the draft and opens it in the
  Entscheidungen tab) and, when it has an instruction, **Als Vorschlag umsetzen** (a preview chat turn).
- Entscheidungen: list by status (accepted, draft, superseded, rejected) with `ADR-<n>`; an editor for title, status,
  superseded by, context, decision, consequences; the options read-only with the chosen one marked; **Markdown**
  downloads the ADR.
- Stakeholder: list and form (name, role, influence, concerns, drivers).
- Viewers see everything read-only.

### Exchange

Project bundles carry the three lists and their counters; a bundle with any of them is `schema_version` 5, others keep
their version.

## Testing

- Operations: bounds, integrity, cascades, status rules (`tests/test_decision_support.py`).
- HTTP: every route, 400/404, viewer 403, access matrix.
- Analysis: parse/validation, failure answer, digest content (with `ScriptedProvider`).
- Prompts and reviews: blocks present only with content, accepted decisions only, review input carries them.
- Agent tools: list/create, refusal in preview.
- Bundles: round trip; older versions unchanged.
- JS: `decisions.js` helpers. e2e: a scenario analysed (scripted), decided, accepted, exported.

## Migration and compatibility

No migration: the new fields are omitted while empty, so stores, fingerprints and bundles written before stay
byte-identical. An older Arcviz refuses a `schema_version` 5 bundle with its usual message.

## Decisions

- Scenarios belong to the project; one driver per scenario, several scenarios per driver; core scenarios mark core
  goals (maintainer, 2026-10-13).
- ATAM six-part scenarios (maintainer).
- Analyses are stored only as the decision taken from them, as an editable ADR draft with a status (maintainer;
  ADR-0167).
- Conflicts with accepted decisions are raised by the chat assistants and the AI reviews (maintainer; ADR-0168).
- Stakeholders with name, role, concerns, influence and drivers (maintainer).
