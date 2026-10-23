# ADR-0079: Links are dropped when a diagram leaves its project

- Status: Superseded by ADR-0161 (elements and relations come along on moves and imports; what does not fit, and a document without a model, keep this behaviour)
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md`
- Amends: ADR-0072 (dangling refs after a move or a single-diagram import), ADR-0077 (single-diagram documents)

## Context

ADR-0072 tolerated refs that point to no element, including those of a diagram moved to another project or a
single diagram imported into one. The review of #154 showed the flaw: element ids are only unique within a
project, so such a ref does not necessarily dangle. It can name an *unrelated* element of the target project
with the same id, and then renames, sync and deletes of that element act on the moved view. The dangling-ref rule
does not see it.

## Options

1. **Keep the refs** (ADR-0072 as written). Silent wrong links whenever ids collide.
2. **Keep only refs that resolve to "the same" element.** Elements of different projects share no identity, so
   "the same" would be a guess (equal names?).
3. **Drop every ref** when a diagram moves to another project (one version, "Verknüpfungen gelöst (anderes
   Projekt)") and when a single diagram document is imported. Project bundles keep their refs, because their
   elements are created with the same ids in the new project.
4. **Carry the elements along** to the target project (#153). Needs a decision on id collisions.

## Decision

Option 3, until #153 carries elements along: it can never link a view to the wrong element. The view elements
keep their names, so nothing visible is lost; relinking in the target project is one action per view.

## Consequences

- `DiagramService.move` unlinks before it moves the membership (a crash in between leaves an unlinked diagram in
  the old project, never a wrong link in the new one); `DiagramService.import_new` unlinks unless `keep_refs`
  (project bundles).
- Dangling refs remain possible (a racing delete, documents edited by hand in a bundle) and stay tolerated.
