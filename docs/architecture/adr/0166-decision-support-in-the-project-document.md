# ADR-0166: Stakeholders, scenarios and decisions live in the project document

- Status: Accepted
- Date: 2026-10-13
- Spec: `docs/architecture/specs/decision-support.md`

## Context

ADR-0163 put the architecture drivers into the project document. Stakeholders, quality scenarios and decisions refer
to drivers, elements and each other, and the assistants read them once per turn with the model. Decisions can be long
(context, several options with pros and cons, rationale, consequences).

## Options

1. **Fields of the project document**, like drivers. One read per turn, one conditional write per change, cascades
   (removing a driver or an element) inside one write, events, access and bundles as they are. The project document's
   size guard (1.9 MB) bounds the total; with every bound at its maximum the lists would exceed it.
2. **A keyed document per project for decisions** (kind `decisions`). Its own 2 MB budget, but a second read per turn,
   cross-document cascades (a removed element must be cleared in two documents), new events and bundle handling.
3. **One keyed document per decision.** Unbounded count, but listing them is a cross-partition query on Cosmos DB.

## Decision

Option 1, with per-field bounds and limits (50 stakeholders, 200 scenarios, 100 decisions); the size guard refuses a
write that would make the document too large, with its usual message.

## Consequences

- Cascades stay atomic. A project with many very long decisions can reach the guard; moving decisions into their own
  document (option 2) stays possible later without changing the API.
