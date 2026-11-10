# ADR-0213: The project model is the source of truth; diagrams are views

- Status: Proposed
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`
- Supersedes: ADR-0070, ADR-0071, ADR-0138, ADR-0164; amends ADR-0137

## Context

Since M18 a project has a shared model (`Project.elements`, since M25 `relations`), but every diagram is a
self-contained document with its own nodes, edges and lifelines that may *link* to the model (ADR-0070). Views stay
primary (ADR-0138): the model is reconciled with them, renames propagate (ADR-0071), new views are adopted into the
model (ADR-0164), and a family of correspondence rules exists only to report drift between views and model. The owner
decided on 2026-11-10 that the model is the guiding element: agents and people should build the architecture in the
model, and diagrams should show it.

## Options

1. **Keep optional references, propagate more.** Push every model change (names, kinds, properties, relations) into
   all linked views. Drift stays possible (unlinked views, concurrent writes) and every model change becomes a
   multi-document write.
2. **Full model/view split.** Diagrams store only what they show and how; all content lives in the model. Needs a
   data migration and new history semantics; renderers can be kept by resolving a view into today's `Diagram`.

## Decision

Option 2 (owner's decision): one source of truth makes drift impossible by construction and lets agents work on the
model directly; the projection (ADR-0215) keeps the rewrite away from rendering and rules.

## Consequences

- Linking, syncing, adoption and the drift rules are removed; the migration (ADR-0220) is a one-time cost.
- Every edit of a diagram is an edit of the model; removal needs an explicit scope (ADR-0217).
- History and undo span several documents (ADR-0218).
- Data, exports and the API change incompatibly: a breaking release.
