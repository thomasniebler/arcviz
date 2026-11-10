# ADR-0216: Relations among shown elements are shown unless hidden

- Status: Proposed
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`
- Amends: ADR-0137 (completeness per diagram is dropped; hiding per view stays)

## Context

A structure view lists the elements it shows. Relations can either be listed as well or derived from the shown
elements. ADR-0137 introduced an open world: a diagram declares completeness per relation kind and marks relations
hidden.

## Options

1. **Explicit relation lists per view.** Full control; a new relation shows nowhere until each view adds it, which
   recreates the gaps the correspondence rules reported.
2. **Derived:** every relation of a kind the view type can show between two shown elements is drawn unless hidden.

## Decision

Option 2: a model change appears wherever it is relevant, which is the point of a model-first design; hiding covers
deliberate omissions.

## Consequences

- `Diagram.complete` and the completeness rules disappear.
- The migration hides relations an old diagram did not draw, so no migrated view shows more than before.
- Large views can get busy; hiding and a "hide all of kind" view option are the tools.
