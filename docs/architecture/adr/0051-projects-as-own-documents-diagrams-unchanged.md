# ADR-0051: Projects as their own documents, diagrams unchanged

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/projects.md`

## Context

M16 groups diagrams into projects with a folder-like section tree. The maintainer decided that a project
contains several independent diagrams (no shared model yet, option A), that diagram ids stay globally
unique so `/api/diagrams/{id}` keeps working, that Cosmos DB keeps the `/diagram_id` partition key with
projects as their own items, and that a shared project model (M18) must remain possible without
migrating diagram data again. Diagrams have immutable versions whose content fingerprint drives undo and
redo.

## Options

1. **A project field on the diagram** (`Diagram.project`, `Diagram.section`). One write per placement, but
   every existing diagram document changes (a data migration), moving a diagram would be a diagram write
   (a new version or an unversioned change of latest that versions do not show), the fingerprint and the
   exchange format would carry organisation data, and order within a section would still need a separate
   document.
2. **A project document holding the section tree and the ordered membership.** Diagram documents and
   versions stay as they are; a placement is a project write; order, sections and membership change
   atomically in one document. A diagram's project is found by reading the projects (few per
   installation). The "exactly one project" invariant spans documents (ADR-0052).
3. **A separate membership index** (one record per diagram). Atomic per diagram, but the tree and the
   order live elsewhere, so most changes touch several records anyway.

## Decision

Option 2: it changes no diagram data, keeps tree and order consistent in one conditional write, and
leaves the project document as the natural home for a shared model in M18.

## Consequences

- New `ProjectRepositoryPort` with five primitives per backend; Cosmos DB stores a project as an item
  `project` in the partition `project:<id>`.
- Finding the project of a diagram scans all projects; fine for tens of projects, listed as a risk if
  installations grow far beyond that.
- M18 can add a model to the project document and reference diagrams by their existing ids.
