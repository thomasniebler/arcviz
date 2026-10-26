# ISO/IEC 25010 quality characteristics of scenarios

- Status: Implemented
- Issues: none yet
- ADRs: [0202 Characteristics are stored on scenarios, suggested by the model and chosen by a person](../adr/0202-iso25010-characteristics-on-scenarios.md)
- Builds on: [Decision support](decision-support.md) (quality scenarios, what-if analysis),
  [Architecture drivers](architecture-drivers.md)

## Summary

Every quality scenario can be assigned to **one, at most two** of the nine product quality characteristics of ISO/IEC
25010:2023 (functional suitability, performance efficiency, compatibility, interaction capability, reliability, security,
maintainability, flexibility, safety). A driver covers the characteristics of its scenarios (computed). In the scenario
editor the model **suggests** the characteristics from the text of the scenario; the person decides, and all nine stay
selectable because the suggestion can be wrong.

## Goals

- See which ISO 25010 characteristics a project's drivers and scenarios cover.
- Propose the characteristics from the scenario text, never trust the answer, never store it unconfirmed.
- Keep old data and bundles valid.

## Non-goals

- The sub-characteristics of the standard (availability, recoverability, …).
- A required field, or a project-wide quality tree view (a follow-up).
- Changing drivers, which stay as they are (kind, importance, risk).

## Design

### Model (`arcviz/quality_model.py`, pure)

`QUALITY_CHARACTERISTICS` (the nine names in the order of the standard), `QUALITY_MEANING` (English glosses for the model), `MAX_QUALITIES = 2`, `clean_qualities(values) -> (kept, dropped)`
(known, each once, at most two, never raises) and `driver_qualities(driver_id, scenarios)` (union over the active
scenarios of a driver, in the order of the standard).

`Scenario.qualities: list[str]` is omitted from dumps while empty, so data written before is byte-identical.
`add_scenario(…, qualities=)` and `update_scenario(…, qualities=)` (replaces; `[]` clears) refuse unknown names,
duplicates and more than two with a `ProjectError` that lists the available names. `Project.integrity_errors` reports a
stored invalid value. Scenario text shown to the model carries `ISO 25010: …`, a core scenario line in the drivers block
ends with `(<names>)`.

### Suggestion (`arcviz/quality_suggestion.py`)

`suggest(provider, values)`: one `complete_tool` call offering only `submit_qualities` (`qualities`: array of the enum,
`maxItems` 2; `reason`: German text) over the scenario as typed (title, kind, the six parts). The answer goes through
`clean_qualities`; without a valid name the result is `llm.ok = false` and an empty list. It never raises because of the
model and stores nothing.

### REST

| Endpoint | Role | Answer |
| --- | --- | --- |
| `POST /api/projects/{pid}/scenarios/qualities` | viewer; rate class `review` | `{"qualities", "reason", "llm": {"ok", "error", "dropped", "usage"}}`; 400 without title and parts, 404 unknown project |

`POST`/`PATCH …/scenarios[/{id}]` accept `qualities`; `GET …/drivers` rows carry the computed `qualities`.

### Agents and MCP

`create_scenario` has `qualities` (enum, `maxItems` 2); proposal items `add_scenario`/`update_scenario` carry it through
the operation signature; `list_scenarios` and `list_drivers` (agent and MCP) return it, `get_scenario` dumps it. Agents
cannot bypass the validation: the same operation checks them.

### Review

`scenario-no-quality` (suggestion, low): active scenarios without a characteristic.

### Bundles

A bundle with any scenario that has `qualities` is `schema_version` 8 (`QUALITIES_PROJECT_SCHEMA_VERSION`); older
versions refuse it. Others keep their version.

### UI

`static/qualities.js` (pure: labels, `validQualities`, `qualityChoices`, `toggledQuality`, `suggestionBody`), the editor of
`scenariopanel.js` (nine checkboxes, the other seven disabled once two are chosen, **Vorschlagen** pre-ticks the boxes
and shows the reason; the person saves), the characteristics as chips on scenario cards and, computed, on driver cards.

## Testing

`tests/test_qualities.py` (catalogue, cleaning, operations, dumps, integrity, driver union, review rule, prompt lines,
suggestion with gibberish and failures, routes, bundle), `tests/test_decisions_in_agents.py`,
`tests/test_architecture_tools.py`, `tests/js/qualities.test.mjs`, e2e step 14h (suggestion, gibberish dropped, limit of
two, chip).

## Migration and compatibility

None: optional field, omitted while empty; only bundles that use it change their version.

## Decisions

- Carrier: the scenario, not the driver (ADR-0202). Level: the nine characteristics of the 2023 edition. Optional, invalid
  suggestions dropped. Suggested on demand, also settable by agents through the existing operations.
