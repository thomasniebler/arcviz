# ADR-0099: The inspector keeps typed values and resolves a 412 field by field

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`

## Context

With live updates the canvas changes under an open inspector. Re-rendering the form would throw away what the
user typed; not updating it would let the user save over a change they never saw.

## Decision

The inspector remembers the base (revision and values) of the element it shows. A remote change that leaves the
element as it was moves the base to the new revision. One that changes it updates the fields the user has not
touched and marks the touched ones "changed by X meanwhile", keeping the typed value and the old base; saving
sends the base revision in `If-Match`, so the server answers 412. The inspector then lists, per field, the user's
value and the current one and offers "save mine" (rebased on the current revision) or "take the current values".
Immediate edits (edge rows, direction, link/unlink) send the current revision and reload on a 412.

## Consequences

A save is sent against the base captured when Save was clicked, advanced only by this tab's own writes completed
since (never another element's base or the shown revision), so a queued save cannot overwrite a remote change it
never showed. A change elsewhere in the diagram never causes a conflict in the inspector; a change to the inspected element
always does, also if the user would not have minded. The pure logic lives in `static/conflicts.js`.
