# ADR-0188: Requirements are their own object, kept in their own keyed document

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/requirements.md`

## Context

Today a requirement is only `Driver.kind = "requirement"`: an already-interpreted, prioritised driver. The new
elaboration mode starts earlier, with raw text from meetings, documents and tickets that has to be cleaned up before
anything is derived. The expected volume is 200 to 500 requirements per project, imported in bulk, edited often.
The project document has a 1.9 MB guard shared with the model, relations, drivers, scenarios, decisions and
stakeholders, and every write to it is a conditional write on the project revision (ADR-0166 accepted that for items
that are few and long-lived).

## Options

1. **Extend `Driver.kind = "requirement"`.** No new type; mixes unprocessed input with prioritised drivers, has no place
   for the original wording, a lifecycle or issues, and 500 entries drown the driver list.
2. **A new object in the project document.** One read, atomic cascades; 500 entries (about 0.5 to 1 MB typical) crowd out
   everything else, and a bulk import or an agent writing while people edit diagrams contends on the project revision.
3. **A new object in its own keyed document per project** (`requirements`, key = project id). Own budget, own revision,
   bulk writes do not touch the project revision; references to project objects cross a document boundary.

## Decision

Option 3. `Requirement` keeps the original text (`raw`, never changed) next to the normalised `statement`; the document
is a keyed document of the new kind `requirements` (no new store primitive), carries the project's incarnation like the
proposal document (ADR-0127), and has its own byte guard.

## Consequences

- References from a requirement to stakeholders and elements are checked on write and **tolerated when dangling** on
  read; cascades (deleting a stakeholder or element) clean the requirement document as a second, ordered write, and
  `sync` repairs drift, as the model service does (ADR-0073).
- A project export or import carries the requirements (`schema_version` 7 with requirements); a project delete removes
  the document.
- Project review and the architecture context read the requirement document in addition to the project; they read it
  once per request.
- Moving requirements into the project document later stays possible; only the service changes.
