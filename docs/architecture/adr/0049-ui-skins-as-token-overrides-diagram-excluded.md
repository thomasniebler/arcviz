# ADR-0049: UI skins as token overrides on a root attribute, diagram stage excluded

- Status: Accepted
- Date: 2026-10-06
- Spec: `docs/architecture/specs/ui-skins.md`

## Context

The maintainer wants several switchable UI looks, switched live, that leave the diagram unstyled. The
frontend has no build step (ADR-0020), and all colours already are CSS custom properties on `:root`.
`data-theme` on `<html>` already carries the OS light/dark mode, and `canvas.js` re-initialises
Mermaid from it.

## Options

1. **One attribute `data-skin` on `<html>`, one CSS block per skin in `style.css` that overrides the
   tokens and adds decoration.** Switching is one attribute write; the browser restyles without a
   reload. All skins ship in one file (a few kB each).
2. **One stylesheet per skin, swapped by changing a `<link>`.** Loads only the active skin, but a switch
   fetches a file and briefly shows unstyled content, and every skin would repeat the base rules or
   depend on load order.
3. **Skins that also restyle the diagram** (Mermaid `themeVariables`, CSS on the SVG). Ruled out by
   the maintainer; it would also make exports depend on the skin and break with Mermaid updates.

## Decision

Option 1, with separate stage tokens (`--stage-*`) that no skin overrides and a font/colour reset on
`.diagram`, because it switches instantly, needs no extra request and keeps the diagram independent.

## Consequences

- A new skin is one CSS block, one entry in `skins.js` and a line in the guide.
- `style.css` grows; rules that a skin must not reach have to use the stage tokens.
- Chat rules must be scoped to `.messages`, because the sequence SVG uses a `.msg` class.
