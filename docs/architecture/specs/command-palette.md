# Command palette and slim top bar

- Status: Implemented
- Issue: not yet filed
- ADR: [0197 One palette registry of declarative commands, executed through the existing controls](../adr/0197-palette-registry-executes-existing-controls.md)
- Builds on: [App shell](app-shell.md), [Architektur workspace](architecture-workspace.md), [Menu bar and personal settings](personal-settings.md)

## Summary

Actions are spread over the menus of the top bar (**Diagramm**, **Version**, **Hilfe**, the user menu), the navigator
buttons, the canvas toolbar and the export menu. A **command palette** (`Strg+K` / `⌘K`, or the search field in the top
bar) finds every action, every area and tab, and every diagram of the open project from one text field. With it the top
bar gets slim: the two menus **Diagramm** and **Version** collapse into one **⋯** menu.

## Goals

- `Strg+K` / `⌘K` (and the search field) opens a modal palette: a text field, a grouped result list, a hint line.
- Typing filters the whole registry: every word of the query must occur (case- and diacritics-insensitive), results are
  ordered by match quality, matches are highlighted. An empty query shows the five most recently used commands first, then
  all groups.
- ↑/↓ move the active result (wrapping), `Enter` runs it, `Esc` or a click outside closes the palette, a click on a
  result runs it, hovering marks it active. The active result is announced (`aria-activedescendant`).
- Commands: **Gehe zu** (Diagramme, Anforderungen, Architektur › Treiber/Szenarien/Entscheidungen/Stakeholder, Modell,
  Vorschläge, Projekt-Review, Projekt-Chat), **Diagramm** (new, import, clear chat, delete), **Version** (snapshot,
  restore, compare, undo, redo), **Ansicht** (fit, zoom 100 %, source, review), **Export** (SVG, PNG, JSON, PlantUML,
  Mermaid), **Projekt** (new, share, rename, describe, export, import, new section), **Allgemein** (settings, tour,
  what's new, user guide, architecture documentation), and **Diagramme**: one entry per diagram of the open project.
- A command that cannot run now (its control is disabled, inert because the project is empty, or hidden for a viewer) is
  not offered; opening the palette re-evaluates this every time. Diagram entries have no control behind them, so they are
  withheld, and refused when run, while the UI is busy (a streaming turn or a pending proposal would otherwise lose its chat).
- Top bar after the change: brand, **Projekte** toggle, the search field, spacer, **Undo**/**Redo**, **⋯**, **Hilfe**,
  user menu. **⋯** holds the former **Diagramm** and **Version** items under two headings.
- No new endpoint, no new data; keyboard focus returns to where it was before the palette opened.

## Non-goals

- Searching drivers, scenarios, decisions, stakeholders or requirements (the maintainer chose commands, navigation and
  diagrams; searching objects needs the lists loaded and is a later step).
- User-defined shortcuts or a shortcut editor; single-key shortcuts other than `Strg+K`.
- Running a command that opens a dialog and then keeps the palette open: the palette always closes first.

## Design

### Pure module `palette.js` (layer pure)

- `fold(text)`: lower-case and strip diacritics per UTF-16 unit, so folded and original strings have the same length
  (`ä`→`a`, `ß`→`s`).
- `scoreMatch(query, text)`: `null` unless every word of the query occurs in `text`; otherwise a number, higher is
  better: 2 per query character, +8 when the match starts the text, +5 when it starts a word, minus a small penalty for
  the position and the text length (deterministic).
- `matchRanges(query, text)`: the `[start, end)` ranges of the first occurrence of each word, merged and sorted, for
  highlighting the label.
- `COMMANDS`: the registry, plain data `{id, label, group, keywords, hint, click | route | href, needs}`:
  `click` is the id of the control to click, `route` a location hash, `href` a link opened in a new tab, `needs`
  `"project"` (a project must be open). `GROUPS` fixes the display order.
- `rankCommands(commands, query, recent)`: for a blank query the recent ids first (group `Zuletzt`), then the rest in
  registry order; otherwise the matching commands by `scoreMatch` over `label + " " + keywords` (stable for equal scores),
  each with its label ranges.
- `rememberCommand(recent, id, limit = 5)`: the new recent list (most recent first, no duplicates).

### View module `paletteview.js` (layer features)

Builds `<dialog id="palette">` (input `#palette-input` with `role="combobox"`, list `#palette-list` with
`role="listbox"`, `li` options `role="option"`, group headings `role="presentation"`, an empty message and the hint line).
`openPalette()` collects the available commands (`available(command)`: for `click` the control exists, is not
`disabled` or `hidden` and has no `[inert]` ancestor; for every command with `needs: "project"` a project is open), adds one
entry per diagram of the open project (`navigator.projectDiagrams()`), ranks and renders. `run(command)` closes the
palette, remembers the id (`localStorage["arcviz.palette.recent"]`, wrapped in `try`) and on the next task: clicks the
control, sets `location.hash`, opens the link or opens the diagram (`location.hash = ""` when another view is shown, then
`navigator.openInProject(id)`). `Strg+K`/`⌘K` on `document` toggles the palette (`preventDefault`, also inside inputs).
Text is always set with `textContent`; highlights are `<mark>` elements built from the ranges.

### Markup and styles

- `index.html`: `#palette-open` (the search field button with a `<kbd>`; shows `⌘K` on Apple platforms, else `Strg K`),
  the `⋯` menu `#menu-more` replacing `#menu-diagram` and `#menu-version` (every item keeps its id; the `.tools` wrappers
  that the empty project makes inert stay), the dialog `#palette`.
- `components.css`: `.topbar-search`, `.kbd`, `.palette` (centered, 640 px, `--radius-lg`, `--shadow`, backdrop),
  `.palette-input`, `.palette-list`, `.palette-group`, `.palette-item` (`[aria-selected="true"]` accent), `.palette-hint`;
  below 1000 px the search field shows only its icon; skins override colours through tokens (Mission Control glow,
  Synthwave neon outline).

### Errors, concurrency

A command whose control disappeared between opening and running does nothing. Commands run in a task after the dialog
closed, so a dialog that the command opens is not blocked by the palette. An unavailable `localStorage` only loses the
recents.

## Testing

- `tests/js/palette.test.mjs`: `fold`, `scoreMatch` (every word, order, diacritics, word-start beats inside), `matchRanges`,
  `rankCommands` (blank query with recents, filtering, stability), `rememberCommand`, and the registry against
  `index.html`: every `click` id exists there, ids are unique, every group is listed in `GROUPS`.
- `tests/js/modules.test.mjs`: `palette.js` pure, `paletteview.js` a feature.
- e2e smoke: `Strg+K` opens the palette and focuses the input; typing "treiber" lists the Treiber tab and `Enter` opens it
  (`#/architecture/drivers`); `Esc` closes; typing a diagram title opens that diagram; a disabled command (Redo without a
  redo target) is not listed; **⋯** holds **Neu…** and **Version sichern**; the search field opens the palette; the palette
  works in Mission Control.

## Migration / compatibility

The menus `#menu-diagram` and `#menu-version` disappear; every item inside keeps its id, so scripts and the e2e helper
that open "the menu of a control" keep working. `docs/guide/` and `docs/spec/frontend.md` describe the new bar.

## Open questions

None.

## Decisions

- Slim top bar with one **⋯** menu, palette scope commands + navigation + diagrams: maintainer, 2026-10-21.
- Registry and execution through the existing controls: [ADR-0197](../adr/0197-palette-registry-executes-existing-controls.md).
