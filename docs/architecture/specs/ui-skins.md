# UI skins

- Status: Implemented (Serviette and Codex removed by [ADR-0195](../adr/0195-remove-serviette-and-codex-skins.md); three skins remain)
- Issue: #127
- ADR: [0049 UI skins as token overrides on a root attribute, diagram stage excluded](../adr/0049-ui-skins-as-token-overrides-diagram-excluded.md)

## Summary

The UI has one look that only follows the operating system's light or dark setting. This change adds
*skins*: complete visual identities for the UI chrome (top bar, chat, toolbars, panels, menus,
dialogs) that the user switches live from the top bar. The diagram itself is explicitly not part of a
skin: the stage keeps its own surface, Mermaid keeps its theme, and the SVG is not touched, so a
diagram reads and exports the same in every skin.

## Goals

- Five skins (since the app shell only the first, second and fourth): **Klassisch** (today's look, default), **Mission Control** (blueprint CRT console),
  **Serviette** (napkin sketch on paper, a chalkboard in dark mode), **Synthwave** (neon over a
  horizon grid) and **Codex** (illuminated manuscript: parchment, rubric red, gold leaf, drop caps).
- Live switching: no reload, no re-render of the diagram.
- The choice persists per browser and follows into other open tabs.
- No build step, no new dependency, no web fonts (system font stacks only), no network access.

## Non-goals

- Styling the diagram (node colours, edges, labels, the stage background). Explicitly ruled out by the
  maintainer.
- A manual light/dark override. Light/dark still follows the OS; a skin may ignore it (Mission Control
  and Synthwave are always dark) but the diagram stage keeps following it.
- Server-side persistence of the choice or a configuration variable for the default.

## Design

### Data and modules

- Pure module `skins.js` (layer *pure*): `SKINS` (ordered list of `{id, label}`), `DEFAULT_SKIN`
  (`"classic"`), `SKIN_STORAGE_KEY` (`"arcviz.skin"`), `resolveSkin(value)` (a known id or the
  default).
- Feature module `appearance.js` (layer *features*): `initAppearance()` fills `#skin-select`, applies
  the stored skin, and on `change` applies and stores the new one. A `storage` event for the key
  applies the skin in other tabs. `applySkin(id)` sets `document.documentElement.dataset.skin` and the
  selector's value. All `localStorage` access is wrapped in `try`, so a blocked storage only loses
  persistence.
- `app.js` calls `initAppearance()` first, right after `bindElements()`, so the skin is applied before
  the first render.

### CSS

- Every skin is a block `:root[data-skin="<id>"] { … }` that overrides the existing colour tokens
  (`--bg`, `--panel`, `--accent`, …) plus three new UI tokens (`--font`, `--radius`, `--glow`). The
  attribute selector is more specific than `:root` inside the dark-mode media query, so a skin wins;
  skins that support both modes add their own `@media (prefers-color-scheme: dark)` block.
- Decoration (scanlines, the synthwave horizon, paper texture, drop caps, the wax-seal send button) is
  drawn with gradients and pseudo-elements on the chrome only (`.topbar`, `.chat`, toolbars, panels).
  Every decoration has `pointer-events: none`.
- Chat bubble rules are scoped to `.messages .msg`, because the sequence-diagram SVG also uses a
  `.msg` class.
- `@media (prefers-reduced-motion: reduce)` turns off every skin animation.

### The diagram stage is excluded

- New tokens `--stage-bg`, `--stage-grid`, `--stage-muted` are defined only on `:root` and in the OS
  dark-mode block, and no skin overrides them. `.diagram` uses them for its background, grid and
  placeholder text.
- `.diagram` resets `font` and `color`, so a skin's body font or text colour does not reach Mermaid's
  HTML labels inside `foreignObject`.
- `canvas.js` is unchanged: the Mermaid theme still follows only the OS setting; `data-theme` keeps
  its meaning (`light`/`dark` of the OS).

### Error handling, concurrency

An unknown or tampered stored value resolves to `classic`. Switching only sets an attribute, so it
cannot race with a render or an SSE turn.

## Testing

- `tests/js/skins.test.mjs`: `SKINS` has unique ids and contains the default; `resolveSkin` keeps
  known ids and maps unknown, empty and non-string values to the default.
- `tests/js/modules.test.mjs`: `skins.js` pure, `appearance.js` a feature.
- e2e smoke: the selector lists the skins (three since ADR-0195); Classic is the default; switching to every skin sets
  `data-skin` and changes the top bar background without a reload, while the stage background and the
  diagram SVG stay identical; the choice survives a reload.

## Migration / compatibility

None. Without a stored value the UI looks as before. The e2e check of the dark page background still
holds for Classic.

## Open questions

None.

## Decisions

- Diagram not styled, live switching: maintainer, 2026-10-06.
- Skins and names: the maintainer picked Mission Control, Napkin and Synthwave and asked for one more
  "totally random, extremely unusual" skin; Codex is that one.
- Name `skin` instead of `theme` in code, because `data-theme` already means the OS light/dark mode.
- Mechanism: [ADR-0049](../adr/0049-ui-skins-as-token-overrides-diagram-excluded.md).
