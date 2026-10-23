# ADR-0196: Master/detail with one editor per tab and a shared component layer

- Status: Accepted
- Date: 2026-10-21
- Spec: `docs/architecture/specs/architecture-workspace.md`

## Context

The Architektur tabs show one inline form per list row (drivers, stakeholders) or a list above a form (scenarios,
decisions). A row with eight controls does not fit a page, creating needs a second form, and each tab styles its own
controls. The frontend has no build step and no component framework.

## Options

1. **Keep inline rows and widen them.** No restructuring; the rows stay unreadable and every tab keeps its own
   look.
2. **Master/detail with one editor per tab, explicit save.** A card list plus a single form for the selection or for a
   new item; a small pure module for filtering and stepping; shared CSS classes.
3. **Master/detail with autosave.** Fewer clicks, but accepting a decision or lowering a driver's priority changes
   what the assistants do, so an accidental keystroke must not store it.

## Decision

Option 2, with the component classes in their own stylesheet `components.css` (class selectors only, on the
existing tokens) so that skins keep overriding tokens and the rest of the UI is untouched until it adopts the classes.

## Consequences

Four panel modules are rewritten around one pattern (`listnav.js`, `mdpane.js`); the e2e steps for the page change
with them. Unsaved changes need a guard when switching items, tabs and projects. Later pages (Modell, the
requirements view) can adopt the same pattern and classes.
