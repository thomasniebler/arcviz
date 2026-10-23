# ADR-0071: Names live in the model and are propagated into views; drift is repaired by sync

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/shared-project-model.md` (case study: `shared-project-model-case-study.md`)

## Context

With optional references (ADR-0070) a linked view element keeps its own `name`, which renderers,
exports and comparisons use. "Rename once, see everywhere" needs the model's name to win. Writes to the
project and to several diagrams cannot be atomic (no cross-document transactions, ADR-0052, ADR-0078).

## Options

1. **Resolve names at render time.** Renderers, exports and the comparison need the model; old versions
   show today's names. Rejected with the model/view split (ADR-0070).
2. **Denormalise and propagate.** The model's name is authoritative. A rename writes the project, then
   runs the internal operation `sync_element` on every referencing diagram (conditional, versioned).
   Renaming a linked view element in the view (`update_node`, `update_lifeline`) is refused with a
   pointer to the model rename. Drift (a crash between writes, a link racing a rename) is reported by the
   review and repaired by an idempotent `sync`.
3. **No propagation**, only a drift warning. Misses the goal.

## Decision

Option 2, with the conservative choice of *refusing* view renames of linked elements rather than
propagating them from the view (which would turn every `update_node` into a cross-document write).

## Consequences

- `link_element`, `sync_element` and `clear_element` take the element's name/kind as arguments and are
  internal: `/ops`, `apply_batch`, agent tool calls and proposal replays refuse them.
- Every propagated rename is an ordinary diagram version (undo works per diagram; an undo can reintroduce
  drift, which the review shows).
- Descriptions live only in the model and are shown where the element is referenced; nothing propagates.
