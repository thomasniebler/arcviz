# ADR-0214: One structure document and one document per behaviour, versioned like diagrams

- Status: Accepted in part (first stage: elements and connections stay in the project document, behaviours in their diagram document; see the spec, "As implemented")
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`

## Context

The model must hold structure (elements, features, relations) and, since every diagram type becomes a view,
behaviour (activities, state machines, interactions). The project document is bounded at `MAX_PROJECT_BYTES` 1.9 MB
(Cosmos DB's 2 MB item limit) and already holds sections, memberships, drivers, decisions and the M18/M25 model.
Diagram documents already have versions, fingerprints, undo/redo, retention and the Git mirror.

## Options

1. **Everything in the project document.** One conditional write per edit; does not fit: one sequence diagram can
   approach 100 KB.
2. **One structure document plus one document per behaviour, stored with the diagram primitives.** Reuses versions,
   history, retention and partitions; the structure gets its own 1.9 MB; a behaviour is as big as a diagram is today.
3. **One document per element.** Unbounded size, but every element removal (with its relations) and every relation
   write becomes a multi-document write with crash windows.

## Decision

Option 2: the structure document (reserved id `model_<32 hex>`) and behaviour documents (`bhv_…`) go through the
existing `create_latest`/`commit_latest`/versions primitives; elements and relations leave the project document.

## Consequences

- No new store primitives; reserved id prefixes must be skipped by listings and the navigator.
- The structure document is a write hot spot for a busy project; conditional writes with retry (`update`) cover
  it, and `MAX_ELEMENTS`/`MAX_RELATIONS` follow from a new size calculation.
- Behaviour-local parts (actions, states, messages) are model content but not project-wide elements.
