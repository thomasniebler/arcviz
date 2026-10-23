# ADR-0055: A navigator column with its own layer between the hub and the features

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-navigator.md`

## Context

With projects (#131/#132) the UI must show a project's section tree and offer actions on sections and
diagrams. The flat diagram drop-down cannot show a tree. The frontend is layered (ADR-0020): features
import the hub `diagram.js`, never the other way round; several features refresh the diagram list after
a change. The issue asks to replace the drop-down with a navigator.

## Options

1. **A tree inside the topbar drop-down area** (a popover). Keeps the layout, but a tree with row
   actions in a popover is cramped, and the selection is invisible while working.
2. **A collapsible column left of the chat**, as in IDEs, in a new module `navigator.js` with its own
   layer between the hub and the features. The list functions move there from `diagram.js`; features
   import them from the navigator. Pure tree logic goes into `projects.js` with unit tests.
3. **The navigator as a feature module** in the feature layer. Features could not call it (same
   layer), so list refreshes would need hooks on the hub; more indirection for no gain.

## Decision

Option 2: the tree stays visible, rows have room for their actions, and the layering stays a strict
order (`modules.test.mjs`).

## Consequences

- The layout gets a `.workspace` row; the splitter and canvas maths are unchanged.
- `diagram.js` becomes smaller (view and focus only); `navigator.js` owns the list, its dialogs and
  the remembered selection.
- The e2e test selects diagrams in the navigator instead of a `<select>`.
