# ADR-0189: Traceability is stored on drivers, scenarios and decisions; coverage is computed

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/requirements.md`

## Context

Requirements, drivers, scenarios and decisions have to be traceable in both directions ("which requirements does ADR-7
address", "which scenarios cover req12"). Requirements live in their own document (ADR-0188); drivers, scenarios,
decisions and stakeholders live in the project document. A link stored on both sides must be written in two
documents and can disagree.

## Options

1. **Links on the requirement** (`Requirement.drivers`, `.scenarios`, `.decisions`). A requirement's page is one read;
   editing a decision's "addresses" list rewrites many requirements, and removing a driver or decision dangles in the
   requirement document.
2. **Links on both sides.** Fast both ways; two writes and a drift problem for every change.
3. **Links on the authoring objects** (`Driver.requirements`, `Scenario.requirements`, `Decision.requirements`, ids,
   at most 20 each, omitted while empty) and an **inverse index computed on read**. Authors state "this addresses req3,
   req7" where they write; a requirement's traces and its coverage are computed from the project document.

## Decision

Option 3. Stakeholder links on scenarios and decisions (`Scenario.stakeholders`, `Decision.stakeholders`) are stored in
the project document the same way, with ordinary cascades. Coverage of a requirement (`uncovered`, `driver`,
`scenario`, `decision` levels) is never stored.

## Consequences

- The only dangling case is a requirement id in the project document after a requirement was deleted: the delete is a
  human action, writes the requirement document first and then strips the id from the project in one conditional
  write; a failure between the two leaves a dangling id that reads ignore and `sync` removes.
- Dumps and fingerprints of existing projects do not change (new fields are omitted while empty); no migration.
- Validating a `requirements` list on a driver, scenario or decision needs one read of the requirement document
  (ids only) in the services; `Project.integrity_errors` cannot check it and says so in its docstring.
