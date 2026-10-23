# Graded deletion

- Status: Implemented
- Issue: none (requested in the session)
- ADR: [0198 Graded deletion: requirements delete directly, drivers, scenarios and decisions are retired first](../adr/0198-graded-deletion-retire-before-delete.md)
- Related: [Architecture drivers](architecture-drivers.md), [Decision support](decision-support.md),
  [Requirements](requirements.md), [Architecture workspace](architecture-workspace.md)

## Summary

Requirements, drivers, scenarios and decisions can be deleted, but without a difference in weight. This change grades
it. **Requirements** stay easy: a short confirm for one, and a bulk delete of the selected rows with one confirm that
names the count. **Drivers, scenarios and decisions** are deleted in two steps: first **retire** (decisions: reject or
supersede, which exist already), then delete. A retired driver or scenario stays in the project but no longer influences
the assistants, the reviews or the coverage.

## Goals

- Make deleting requirements quick, including many at once.
- Make deleting a driver, scenario or decision a deliberate second step with a useful first step.
- Keep data written before this change byte-identical (the new field is omitted while it has its default).

## Non-goals

- A trash or restore (ADR-0198, option 2); an undo of a delete.
- Retiring requirements (they already have `rejected` and `duplicate`) or stakeholders.
- Changing what deleting does to references (`remove_*` cascades stay as they are).

## Design

### Model

`Driver.status` and `Scenario.status`: `"active"` (default) or `"retired"` (`project_model.OBJECT_STATUSES`). A
`model_serializer` omits `status` while it is `active`, like the other optional fields of these types. Retired state is
changed by `update_driver(status=…)` and `update_scenario(status=…)` (an argument like the others, validated against
`OBJECT_STATUSES`); no new operation. Helpers `project_model.active(items)` filter retired items.

Decisions keep `DECISION_STATUSES`; `rejected` and `superseded` are their retired states, `draft` is scratch work.

### Delete rules (`projects.py`)

| Object | May be deleted when | Otherwise |
|---|---|---|
| requirement | always | (n/a) |
| driver | `status == "retired"` | `ProjectError`: "Retire driver d3 before deleting it." |
| scenario | `status == "retired"` | same |
| decision | `status` in `draft`, `rejected`, `superseded` | `ProjectError`: "ADR-3 is accepted: reject or supersede it first." |

REST answers 409 (conflict with the object's state) for a refused delete, with the message; the cascades of `remove_*`
are unchanged. Reactivating a retired object is `update_*(status="active")`.

### Effect of `retired`

Retired drivers and scenarios are left out of: the drivers block of both assistants and the project chat
(`drivers_summary`, `drivers_with_core_scenarios`, `core_scenario_lines`), the scenario analysis input, the requirement
refine vocabulary and derive context, the retired drivers named in the stakeholder lines (`cares about`), the default lists of the agent tools (`list_drivers`, `list_scenarios` take
`include_retired`, default false; reading one by id works), the project review rules that count scenarios and drivers
(`scenario-no-requirement`, driver coverage) and `coverage_map` (a requirement covered only by a retired object counts as
uncovered). They stay in the REST lists with their `status`, in exports and bundles, and in the Markdown of decisions.
New links to a retired driver (a scenario's `driver`, a decision's `drivers`) are refused; existing links stay.
`requirement_model.traces` skips retired carriers, so the requirement detail's trace and `coverage_map` ignore them too.

### Agents

Setting `status` is a person's act, like deciding (ADR-0186): the chat tool `update_driver` refuses the argument (its
schema does not offer it either), the scenario tools have no such argument, MCP tools have no update operation. The chat
tool `remove_driver` stays and is refused for a driver the user did not retire. A project proposal applied by a person may
carry `status` in an `update_driver`/`update_scenario` item: the person's confirmation is the act. Agents may read `status`.

### Requirements

- `DELETE /api/projects/{pid}/requirements/{id}` is unchanged; the UI asks first (`Anforderung req3 löschen? …`).
- `POST /api/projects/{pid}/requirements/delete` with `{ids: [...]}` (at most 500, like the bulk status route; registered before
  `/requirements/{requirement_id}`) removes the requirements in **one** requirement write and one reference strip:
  `{removed: [ids], skipped: [{id, reason}]}`, like the bulk status route (`RequirementService.remove_many`, editor).
- UI: a button **Ausgewählte löschen** next to the other bulk actions, one `confirm()` naming the count and how many are
  referenced by drivers, scenarios or decisions (read from the `coverage` of the list rows, which is only known for refined requirements, hence "at least").

### UI

Driver and scenario editors get a status control (**Aktiv** / **Außer Kraft**); a retired card is greyed and filtered out
by default (filter **Außer Kraft anzeigen**). **Löschen** is enabled only for a retired object and says why otherwise
(tooltip). A decision's **Löschen** is enabled for draft, rejected and superseded. The confirm names the cascade
(unassigned scenarios, decisions and stakeholders that lose the reference).

## Testing

Operations (retire/reactivate, delete refused/allowed, cascades unchanged, new links to a retired driver refused),
serializer round trip (an active object dumps without `status`; `tests/test_compat_*` stay green), one test per consumer
that retired objects are excluded, REST 409/200 and the bulk route (partial skip, ordering before `/{id}`), agent refusal,
JS unit tests for the pure helpers (`drivers.js`, `scenarios`, `requirements.js` selection summary), e2e: retire then
delete a driver, bulk delete requirements.

## Migration and compatibility

No migration: `status` is optional and omitted while `active`. Bundles keep their `schema_version` and carry `status` of
a retired object as part of its dump; the importer reads it (it validates through the same models). A reader older than
this change rejects a bundle with a retired object; there is no version bump for it (decision below).

## Open questions

None.

## Decisions

- Agents can neither retire nor reactivate (user, "go with your proposals").
- Retiring a driver does not retire its scenarios; they become unassigned only when it is deleted.
- Bundles keep their `schema_version`; `status` travels with the retired object.
- The bulk delete takes at most 500 ids, like the bulk status route.
- Requirements: bulk delete with a count confirm, no undo (user).
- Decisions: `draft`, `rejected`, `superseded` may be deleted, `accepted` not (user).
- Drivers and scenarios: new `status` field, retired items excluded from prompts and rules (user).
