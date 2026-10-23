# ADR-0135: Model relations live in the project document, sized by `MAX_RELATIONS`

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/model-relations.md` (M25)

## Context

M18 put the shared model's elements into the project document (ADR-0070, ADR-0078). M25 adds relations between
elements (#203). In Cosmos DB the project document is one item with a 2 MB limit; elements already share it. The issue
asks to size relations there or move them to their own keyed document, with the arithmetic in the spec.

## Options

1. **In the project document** (`Project.relations`), every field bounded, `MAX_RELATIONS` per project.
2. **A keyed document per project** (`relations:<project>`), conditional on its own revision.
3. **One keyed document per relation.**

## Decision

Option 1. A relation is about 0.9 KB (ASCII) and at most about 2 KB (every free-text character three UTF-8 bytes)
with the bounds `MAX_RELATION_DESCRIPTION` = 300 and `MAX_PROPERTY_VALUE` = 64; `MAX_RELATIONS` = 500 keeps relations
below 1 MB even in the worst case, next to about 0.5 MB of elements, sections and memberships of a large project (the
arithmetic is in the spec). Options 2 and 3 would make element deletion (which deletes the element's relations) a
write across two documents with its own crash window, and listing relations a second read, for space we do not need.

Deleting an element deletes its relations (a relation without an end means nothing); the model service clears their
links in the diagrams first. Project bundles carry relations under `project.relations` and are `schema_version` 3 only
when there are relations (ADR-0077 continued): older exports stay byte-identical.

Relation ids (and the element ids at their ends, and explicit element ids) are at most 64 characters, so the bounds
of the arithmetic hold for every field. Generated ids `r<n>` are never reused (`Project.relation_seq` records the highest
number issued, exported in project bundles and restored on import from the counter, the relations and the diagrams'
links): a link or hidden marker brought back by an undo or a restore can only dangle, never attach to an unrelated
relation.

## Consequences

- `Project.relations` is omitted from dumps while empty: stores and fingerprints written before M25 are unchanged.
- Element names and descriptions remain unbounded (M18); arc42 section 11 records that a project with very long
  element descriptions can reach the item limit earlier (follow-up: bound them).
- Relation operations are internal project operations; only `ModelService` runs them.
