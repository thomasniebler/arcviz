# Architecture drivers

- Status: Implemented
- Issue: #224
- ADR: [0163 Architecture drivers in the project document, prioritised like an ATAM utility tree](../adr/0163-architecture-drivers-in-the-project-document.md)
- Related: [Shared project model](shared-project-model.md), [Model relations](model-relations.md),
  [Project exchange](project-exchange.md), [Assistant prompts](assistant-prompts.md); follow-up: what-if scenarios
  (#226)

## Summary

The assistants know a project's diagrams and its shared model, but not why the architecture looks as it does. A
project now holds **architecture drivers**: quality goals, constraints, key requirements and risks, each prioritised
on the two axes of an ATAM utility tree, **business importance** and **technical risk** (high, medium, low). Both
assistants get the drivers in their system prompt, most important first, weigh what they propose against them and
name the trade-offs; they change drivers only when the user asks. The **Treiber** tab of the **Architektur** dialog (since M29, [decision support](decision-support.md)) lists and edits
them. Drivers travel with project bundles.

## Goals

- Record drivers per project with kind, title, description, importance, risk and optional related elements.
- Put them into the context of the diagram chat and the project chat within a bounded budget, ordered by priority.
- Let the user edit them in the UI and let the assistants read them, and create, change or remove them on request.
- Keep data written before this change byte-identical (drivers omitted while empty).

## Non-goals

- Evaluating scenarios against drivers (what-if analysis, #226).
- Rules that check diagrams against drivers (the review stays as is).
- Drivers per diagram; a driver belongs to the project.
- History of drivers: like elements, they are part of the project document, which keeps no versions.

## Design

### Model (`project_model.py`)

```python
DRIVER_KINDS = ("quality_goal", "constraint", "requirement", "risk")
DRIVER_LEVELS = ("high", "medium", "low")
MAX_DRIVERS = 100
MAX_DRIVER_TITLE = 120
MAX_DRIVER_DESCRIPTION = 1000


class Driver(BaseModel):
    id: str  # d<n>, from Project.driver_seq, never reused
    title: str
    kind: str = "quality_goal"  # DRIVER_KINDS
    importance: str = "medium"  # business importance, DRIVER_LEVELS
    risk: str = "medium"  # technical risk / difficulty, DRIVER_LEVELS
    description: str = ""
    elements: list[str] = []  # ids of project elements it concerns
```

`Project.drivers: list[Driver]` (list order is the user's order inside a priority class) and
`Project.driver_seq: int`, both omitted from dumps while empty / 0. `priority_key(driver)` orders by importance, then
risk, then list position: (H,H) first, (L,L) last, as the utility tree's ranking. Size: 100 drivers × ~1.3 kB stay far
below the project document's `MAX_PROJECT_BYTES` (the existing guard still applies).

Integrity (`Project.integrity_errors`): unique driver ids, kinds and levels from the closed sets, every `elements`
entry an element of the project.

### Operations (`projects.py`)

Public project operations (no other document is involved), validated like all project operations:

- `add_driver(title, kind="quality_goal", importance="medium", risk="medium", description="", elements=None,
  position=None)` → the driver; refuses an empty or too long title, an unknown kind or level, an unknown element, the
  101st driver, a title that another driver already has (case-insensitive).
- `update_driver(driver, title=None, kind=None, importance=None, risk=None, description=None, elements=None)`.
- `move_driver(driver, position)`; `remove_driver(driver)`.
- `remove_element` also removes the element from every driver's `elements` (same document, same write).

`driver` arguments accept the id or the exact (case-insensitive) title. Error messages list the known drivers, kinds
and levels, so a model can correct itself.

### Service and HTTP

`ProjectService.apply` covers them (editor required, conditional write, project event published). Routes, all in the
access matrix:

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/api/projects/{pid}/drivers` | | `{project, drivers}` in list order, each with its `rank` (1 = highest priority) |
| POST | `/api/projects/{pid}/drivers` | `title`, `kind`, `importance`, `risk`, `description`, `elements`, `position` | 201 `{driver, drivers}` |
| PATCH | `/api/projects/{pid}/drivers/{did}` | any of the fields, `position` | `{driver, drivers}` |
| DELETE | `/api/projects/{pid}/drivers/{did}` | | `{driver, drivers}` |

400 for invalid input (the operation's message), 404 as for every project route.

### Prompts

- `prompts/shared/drivers.txt`: how to use drivers (weigh proposals against the high-priority drivers, name the
  driver a decision serves or hurts, never change drivers unless asked; drivers are not diagram elements).
- `prompts.drivers_part(text)` = that block plus `Architecture drivers (most important first):` and one line per
  driver: `- [d3] quality goal (importance high, risk medium): Title — description (concerns: elem-a, elem-b)`.
  `project_model.drivers_summary(drivers, budget=DRIVERS_SUMMARY_BUDGET)` (4000 characters): descriptions shortened to
  200 characters, then dropped, then the lowest-priority drivers counted (`(+ N more drivers; call list_drivers)`).
- The diagram chat puts it after the model part, before the current state; the project chat after the model part.
  Both read it once per turn with the elements (one project read, `ModelTools.context()`).

### Agent tools (`agent_model.py`)

`list_drivers` (read; also in preview mode), `create_driver`, `update_driver`, `remove_driver` (refused in preview
mode, like the element tools). They are project-level tools: available in the diagram chat and in the project chat
without an active diagram.

### UI

`static/drivers.js` (pure: `DRIVER_KIND_LABELS`, `LEVEL_LABELS`, `priorityBadge`, `priorityTitle`, `sortedDrivers`,
`moveTarget`, `driverChanges`, `concernsText`) and
`static/driverpanel.js` (feature): the **Treiber** tab of the **Architektur** dialog (M29) lists the drivers by priority
(badge `H/M`, kind, title, concerned elements) with **Neu**, **Bearbeiten**, **Löschen** and up/down within a class;
read-only for viewers. The dialog shows the state when it is opened (no live refresh while it is open; a write
answers the current list).

### Exchange

Project bundles carry `project.drivers` (and `driver_seq`); a bundle with drivers is `schema_version` 4, others keep
their version. `exchange.parse_project` validates them like the operations; the import keeps them unchanged (element
ids are kept by the import).

## Testing

- Operations: every argument check, ordering, element removal cleaning drivers, integrity, dumps without drivers
  unchanged (`tests/test_drivers.py`).
- Server: the routes, 400/404, viewer 403, access matrix entries.
- Prompt: block present only with drivers, priority order, budget.
- Agent with `ScriptedProvider`: `create_driver` writes the project; refused in preview mode; `list_drivers` works there.
- Exchange round trip with drivers; older bundles unchanged.
- JS: `drivers.js` unit tests. e2e: create a driver in the panel and see it listed.

## Migration and compatibility

No migration: `drivers` and `driver_seq` are omitted while empty, so stores, fingerprints and exports written before
stay byte-identical (`tests/test_compat_v040.py` keeps passing). An older Arcviz refuses a `schema_version` 4 bundle
with its usual message.

## Decisions

- ATAM utility-tree priority: importance × risk, each high/medium/low (user's choice, 2026-10-12).
- Kinds quality goal, constraint, requirement, risk (user's choice).
- Drivers live in the project document, not in a document of their own (ADR-0163).
- The assistants change drivers only on request (user's choice).
