# ADR-0203: The steward runs in process, on change, with a lease, in the system context

- Status: Proposed
- Date: 2026-10-26
- Spec: `docs/architecture/specs/project-steward.md`

## Context

The user wants a regularly executed background validation. The file and sqlite stores are single-writer; Cosmos allows several replicas. The app already has a heartbeat/writer-lease design (`services/writers.py`) and publishes change hints after every write. A review costs LLM tokens, so unchanged projects must not be re-checked.

## Options

1. **Clock schedule (cron or timer).** Simple to explain; re-checks unchanged projects and ignores the moment a change is fresh.
2. **External trigger calling an endpoint.** Fits scale-to-zero; needs deployment work outside this repository (ADR-0200) and an extra credential.
3. **In-process worker, debounced after changes, per-project lease in a keyed document, opt-in per project, running as the system context with three permitted calls.** No new infrastructure, cost follows change.

## Decision

Option 3, plus a manual "run now" route; a day cap and opt-in per project bound cost and privacy exposure.

## Consequences

The steward only works while a process is up (a scale-to-zero deployment can still call the manual route from a cron). Needs a new keyed document kind and a recording-double test that pins its permitted calls.
