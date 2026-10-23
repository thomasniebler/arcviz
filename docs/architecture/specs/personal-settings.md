# Menu bar and personal settings

- Status: Implemented
- ADR: [0176 A menu bar for diagram actions and a personal settings dialog behind the user menu](../adr/0176-menu-bar-and-personal-settings-dialog.md)
- Builds on: [Frontend split into ES modules](frontend-modules.md), [UI skins](ui-skins.md), [Project navigator](project-navigator.md), [Agent authentication](agent-authentication.md)

> Superseded in part: since the [command palette](command-palette.md) the menus **Diagramm** and **Version** are one **⋯** menu (ADR-0197); the personal settings are unchanged.

## Summary

The UI grouped its controls by history, not by meaning: diagram actions, personal settings and project actions were mixed
in one top-bar row, the project menu held the user's agent tokens, and the canvas toolbars mixed diagram settings with
view controls. This change orders them by what they act on: the **diagram** (top bar menus, canvas toolbar), the
**project** (navigator) and the **person** (user menu and a settings dialog). No behaviour of any control changes.

## Goals

- Top bar: brand, **Projekte** (navigator toggle), the menus **Diagramm** and **Version**, the buttons **Undo/Redo**; on
  the right **Hilfe** and the user menu.
- **Diagramm**: **Neu…**, **Importieren…**, then **Chat leeren** and **Löschen** (the inert-in-an-empty-project part).
  **Version**: **Version sichern**, the version selector, **Wiederherstellen**, **Vergleichen…**.
- User menu (its label is the user's name, *Konto* while unknown): **Persönliche Einstellungen…** and **Abmelden** (token
  mode only).
- Dialog **Persönliche Einstellungen** (`#settings-dialog`): who is signed in, **Design** (skin selector),
  **Agenten-Zugang…** (opens the existing agent dialog and closes the settings), the provider line.
- Project menu keeps what acts on the project (share, rename, describe, export, import, delete), separated by rules;
  **Agenten-Zugang…** leaves it.
- Navigator: the project tools are grouped (**Projekt-Review**, **Projekt-Chat** | **Modell**, **Architektur**,
  **Vorschläge**); **+ Abschnitt** sits above the tree, where sections are.
- Canvas: the first toolbar holds the diagram's own settings (**Richtung**, **Vollständigkeit**, **Review**), the second
  the view (focus, zoom, **Quelltext**, **Export**), in groups with separators.

## Non-goals

- Moving the composer's speech and preview controls (kept as they are, decided with the user).
- Server-side user preferences: skin and speech settings stay in `localStorage`.
- New settings.

## Design

All markup is static in `index.html`; every moved control keeps its id, so modules that bind by id are unchanged
(`tokenpanel.js` closes `#settings-dialog` before it opens the agent dialog; `workspaces.js` fills `#me` and
`#settings-user`). `topbar.js` gives the `<details class="menu">` elements of the bar menu behaviour (one open at a time,
closed by an action, a click elsewhere or Esc) and no longer measures widths. `settings.js` (feature layer) opens and
closes the dialog. The bar wraps on a very narrow window instead of moving controls. The `.tools` groups
(`#diagram-tools`, `#diagram-tools-2`) stay and still make the diagram actions inert in an empty project; **Neu…** and
**Importieren…** stay outside them.

## Testing

`tests/js/modules.test.mjs` (layer of `settings.js`). The e2e test (`tests/e2e/smoke.mjs`) opens the menu or the settings
dialog through helpers before it uses a control that moved (skin, agent access, logout, delete, version list).

## Migration/compatibility

No API, data or setting changes; the skin choice and speech settings keep their `localStorage` keys.

## Open questions

None.

## Decisions

- Dialog, not a dropdown, for the personal settings (user choice).
- A menu bar instead of grouped buttons (user choice); the composer stays unchanged (user choice).
