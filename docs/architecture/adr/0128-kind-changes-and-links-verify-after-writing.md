# ADR-0128: Kind changes and links verify after writing and compensate conditionally

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/hardening.md`

## Context

`ModelService.update_element` checks a new element kind against the views of a snapshot of the member diagrams and
then writes the project; a link checks the element's kind and then writes the diagram (#155). The two writes go to
different documents (in Cosmos DB: different partitions), so a link landing between the kind change's check and its
write, or a kind change landing between a link's read and its write, leaves a view that cannot stand for its
element's kind. The project review reports it (`incompatible-element-link`), but nothing prevented it.

## Options

1. **Verify after writing, compensate conditionally.** The kind change re-reads the member diagrams after its project
   write and, if an incompatible link appeared, puts the old kind back (a new internal project operation
   `revert_element`, which restores the whole element as it was before the update, applied only while the kind is
   still the new one). The link re-reads the element after its
   diagram write and, if the kind no longer suits the view, unlinks again (only while the view still stands for that
   element). Each side reads after its own write, so in every interleaving at least one side sees the other: they
   cannot both succeed; both may fail, which is safe.
2. **A model revision checked by the link**: the link's diagram write would have to be conditional on the project
   revision, which a single-document conditional write cannot express across partitions.
3. **A lock document per element**: a third document per change and a lease to expire; more writes on every link.

## Decision

Option 1. It needs no new storage and no cross-document atomicity; the REST paths raise the usual 400
`ProjectError`, the agents get a tool error and their turn's write-through takes the link back.

## Consequences

- One extra read after every link (the project document) and after every kind change (the member diagrams).
- With Cosmos DB's session consistency a read on another replica may lag the other side's write by a moment; the
  review finding stays as the backstop for that residual.
- A failed re-read after a link takes the link back (fail closed), on the REST path as in the agents.
- Compensations are retried and conditional: `revert_element` puts back only the fields the update changed, each only
  while it still has the value written (the kind alone if that fails), so a concurrent change is never undone. One that still cannot be written is
  said in the error and logged; the project review reports the incompatible link that stays.
