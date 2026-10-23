# ADR-0072: Deleting from a view keeps the element; deleting from the model unlinks; dangling refs are tolerated

- Status: Proposed (amended by ADR-0079: links are dropped when a diagram leaves its project)
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

Modelling tools distinguish "delete from diagram" and "delete from model". With references (ADR-0070) a
view element and its element are separate objects in separate documents.

## Options

1. **Delete from the model deletes every view element**, cascading into edges and messages of several
   diagrams. Surprising and destructive across diagrams.
2. **Delete from the model unlinks the views** (internal `clear_element`, versioned per diagram) and then
   removes the element; view elements stay as plain elements. Removing a view element never touches the
   model.
3. **Refuse deleting a referenced element** until every view is unlinked by hand. Safe but tedious.

## Decision

Option 2: nothing is lost, the views stay readable, and the order (diagrams first, then the project)
leaves at worst an unused element after a crash. References that point to no element (a racing delete, a
diagram moved to or imported into another project) are tolerated everywhere: rendering ignores refs, the
review reports `dangling-element-ref`, `unlink_element` clears one.

## Consequences

- Children of a removed element move to its parent; sections linked to it lose the link.
- No cascade of deletes across diagrams; an unused element is a low suggestion in the review.
