# ADR-0198: Graded deletion: requirements delete directly, drivers, scenarios and decisions are retired first

- Status: Accepted
- Date: 2026-10-21
- Spec: `docs/architecture/specs/graded-deletion.md`

## Context

All four architecture objects can be deleted today, but without a difference in weight. A requirement is deleted with
one click and no confirmation; a driver, scenario or decision with a plain `confirm()`. Requirements are bulk material
(imported by the hundred, refined, rejected); drivers, scenarios and decisions are few, hand-made and shape what both
assistants and the reviews say (accepted decisions bind the prompts, ADR-0168; drivers rank the prompt). Deleting one of
them removes references from others (`remove_driver` unassigns scenarios, drops the driver from decisions and
stakeholders) and cannot be undone: the project document keeps no versions.

## Options

1. **Impact-aware confirm.** Keep deletion as it is, list the consequences in the dialog. Cheap, but one click away from
   data loss; the dialog is easy to confirm without reading.
2. **Soft delete / trash.** A deleted flag with a restore action. Safe, but a new lifecycle (filters, quotas, bundles,
   prompts, restore conflicts) for objects that already have a natural "no longer valid" state.
3. **Retire first, then delete.** Drivers and scenarios get a `status` (`active`/`retired`), decisions already have
   `rejected`/`superseded`. Delete is allowed only for an object that was retired (decisions: not `accepted`). Retired
   objects stay visible but no longer feed prompts, rules or coverage.

## Decision

Option 3 for drivers, scenarios and decisions, chosen by the user: deletion becomes a deliberate second step and the
first step is useful in its own right (the object stops influencing the assistants but keeps its history in the
document). Requirements keep direct deletion and get a confirm and a bulk delete, because their volume makes a two-step
process a burden and their loss is cheaper (the raw text is usually in the source file).

## Consequences

`Driver` and `Scenario` get a field `status`, omitted while `active` so stored data and fingerprints do not change. Every
consumer that lists drivers or scenarios for an assistant or a rule must filter retired ones (one helper each), which
is the main cost and the main risk of the change; a test enumerates the consumers. `remove_driver`, `remove_scenario`
and `remove_decision` refuse with an error the UI can show. Follow-up if wanted: a trash with restore (option 2) would
build on the same status without replacing it.
