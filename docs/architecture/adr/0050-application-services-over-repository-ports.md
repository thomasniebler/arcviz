# ADR-0050: Application services over repository ports

- Status: Accepted
- Date: 2026-10-07
- Spec: `docs/architecture/specs/application-services.md`

## Context

`server.py`, `agent.py` and `proposal.py` call the repository directly at about 45 places and import
`arcviz/store/` for its errors and helpers. The read-modify-write loops with conditional writes (#65)
live in those callers. Projects (M16) add a second aggregate whose invariants span several stored
documents, and a shared project model (M18) may change the storage layout again. The maintainer asked
for the persistence implementation to be hidden from the business logic before any project feature.

## Options

1. **Keep calling the repository** and only forbid backend imports. Cheap, but the conditional-write
   loops stay spread over the entry points, and project invariants would end up in routes.
2. **Application services over abstract ports.** A `services/` package owns every read-modify-write;
   a `ports.py` module holds the contract (a `Protocol`, value types, errors) that the store
   implements. The versioning semantics stay in the repository base class behind the port.
3. **Services plus moving the versioning logic out of the store** into the services (the store keeps
   only primitives). Cleanest separation, but a large behaviour-sensitive move of tested code
   (undo/redo targets, restore) in a milestone whose refactor must not change behaviour.

## Decision

Option 2: it gives the entry points one place to call and the store one contract to fulfil, with a
test that enforces the boundary, without moving the versioning logic that the store contract already
pins down.

## Consequences

- New modules `arcviz/ports.py` and `arcviz/services/`; `fingerprint` moves to `model.py`. The store
  re-exports the moved names.
- `DiagramAgent` and `proposal` take a `DiagramService`; `server.py` only wires the repository into
  services. `tests/test_layering.py` fails on violations.
- New features add service methods instead of repository calls in routes; the project service (#131)
  is the first.
- Option 3 stays possible later: the port lists the derived operations, so moving them into a service
  changes only the store and the service.
