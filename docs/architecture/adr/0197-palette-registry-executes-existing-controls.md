# ADR-0197: One palette registry of declarative commands, executed through the existing controls

- Status: Accepted
- Date: 2026-10-21
- Spec: `docs/architecture/specs/command-palette.md`

## Context

Every action already exists as a button or link with a listener, a disabled state and (for a viewer or an empty project)
a hidden or inert state that the rest of the UI maintains. The frontend has no build step; modules form strict layers.

## Options

1. **A registry of functions.** Each command carries its own handler that calls the feature module. Needs imports from
   every feature into the palette (layering violation, the palette would sit above all of them) and duplicates the
   availability rules.
2. **A registry of declarative commands that click the existing controls.** Plain data in a pure module (testable
   against `index.html`), executed by clicking the control, setting the location hash or opening a link. Availability is
   read from the control itself.
3. **No registry: derive commands from the DOM** (every button with a title). No curation of labels, groups or
   synonyms, and the result depends on markup details.

## Decision

Option 2: the registry is data (`palette.js`), the view clicks the existing controls. One source of truth for what an
action does and when it is allowed; no feature module is imported; a test keeps the registry and the markup in line.

## Consequences

A new action that should be reachable from the palette is one registry entry plus, if it has no control yet, the
control. Actions that exist only as keyboard shortcuts are not in the palette. A control that moves keeps working as long
as it keeps its id.
