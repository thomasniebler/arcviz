# App shell: icon rail, grouped sidebar and the Architektur page

- Status: Implemented
- Issue: not yet filed (GitHub access was unavailable when this was written)
- ADRs: [0194 Architektur as a hash-routed main view](../adr/0194-architecture-as-a-hash-routed-main-view.md), [0195 Remove the Serviette and Codex skins](../adr/0195-remove-serviette-and-codex-skins.md)

## Summary

The Architektur tool (drivers, scenarios, decisions, stakeholders) lives in a modal dialog that is too small for
its content, and the navigator's two button groups mix areas, tools and toggles. This change introduces the first
part of a new application shell: a narrow **icon rail** for the areas, a navigator **grouped by purpose**, and the
Architektur tool as a **full page** (a main view like the requirements view) addressed by hash routes. The visual
language is a small set of semantic tokens that the UI chrome and the remaining skins share.

## Goals

- Architektur opens as a page filling the work area (the chat is hidden while it is shown), with the four tabs as
  sub-navigation, deep links (`#/architecture/<tab>`) and a working back button.
- The navigator shows labelled groups: **Architektur** (Anforderungen, Treiber, Szenarien, Entscheidungen,
  Stakeholder, Modell), **Prüfen & Assistent** (Projekt-Review, Projekt-Chat, Vorschläge) and then the section tree.
- An icon rail left of the navigator switches the area: Diagramme, Anforderungen, Architektur; the personal
  settings sit at its bottom.
- A token layer extends `:root` (surfaces, radii, elevation, spacing, type) without changing the existing token
  names; Klassisch, Mission Control and Synthwave keep working because they only override tokens and a few chrome
  rules. The diagram stage keeps its own `--stage-*` tokens (ADR-0049).
- The skins Serviette and Codex are removed ([ADR-0195](../adr/0195-remove-serviette-and-codex-skins.md)).

## Non-goals

- A command palette and moving the menus of the top bar (Diagramm, Version, Hilfe, Konto): a later step.
- Counts next to navigator entries (they would need extra requests).
- Moving Modell, Vorschläge or the requirements matrix out of their dialogs.
- Server routes: hash routes need no server change.

## Design

### Routes (pure module `routes.js`)

`parseRoute(hash)` returns `{view: "diagram"}` for anything unknown or empty, `{view: "requirements"}` for
`#/requirements`, and `{view: "architecture", tab}` for `#/architecture[/<tab>]` with `tab` in `drivers`,
`scenarios`, `decisions`, `stakeholders` (default `drivers`; an unknown tab falls back to the default).
`formatRoute(route)` is its inverse (`#/architecture/scenarios`, `#/requirements`, `""` for the diagram view).
No DOM, tested in `tests/js/routes.test.mjs`.

### Main view

`state.mainView` gains the value `"architecture"`. `shell.js` (layer 7; it imports `chat.js` and `archview.js`, which
sit one layer below because `archview.js` must import the panel modules) owns `setMainView(view, tab)`: it hides the
other views, calls `showRequirementView` or `showArchitectureView(on, tab)`, keeps the requirements view's own chat
scope (the other views leave the chat as it is), and syncs the hash and the `aria-current` marks. The `.layout` gets
the class `architecture-mode`, which hides chat, splitter and canvas and shows `#architecture-view`. `archview.js`
(renamed from `archview.js`) owns the page: tab switching, loading through the unchanged panel modules
(`driverpanel.js`, `scenariopanel.js`, `decisionpanel.js`, `stakeholderpanel.js`, all element ids kept) and the
reload on a project switch. Clicks on `[data-route]` elements (rail, navigator entries, tabs) only set the hash;
`hashchange` (also the back button) applies it through `setMainView`, and the first project shown applies a deep
link. Programmatic switches (**Zum Diagramm**) go through `setMainView`, which pushes the hash. A switch that is not
possible (no project, a chat turn running) puts the hash back (`replaceState`).

Leaving the page: a diagram opened in the shown project (tree click, also of the open one, **Neu…**, import, a project
chat turn) switches back to the diagram view (`onDiagramSwitched`, ignored while a project switch opens the new
project's diagram); when no project is left (`onProjectShown(null)`) the shell returns to the diagram view too.

`onApply` of a scenario analysis (an option sent as a preview chat turn) returns to the diagram view first (`app.js`),
then sends the text, because the chat is hidden on the page.

### Markup

- `#architecture-view` (inside `.layout`, next to `#requirements-view`) holds the page header, the tab strip
  `#arch-tabs` (the former `arch-tab-*` buttons keep their ids and carry `data-route`) and the four panels
  (`arch-panel-*`, ids of all inner elements unchanged). The dialog `#arch-dialog` and its close button are gone.
- The navigator's button groups become `.nav-sections` with `.nav-section` groups and `.nav-entry` buttons that keep
  their old ids (`btn-requirements`, `btn-model`, `btn-proposals`, `btn-project-review`, `btn-project-chat`); the
  entries for the tabs are `nav-arch-drivers|scenarios|decisions|stakeholders` (the old `btn-architecture` is gone).
- `<nav id="rail" class="rail">` is the first child of `.workspace`; its buttons carry `data-route` and `data-area`,
  and `#rail-settings` opens the personal settings.

### Tokens and skins

`style.css` adds semantic tokens beside the existing ones (`--surface-2`, `--radius-lg`, `--shadow`, `--space-1…6`) defined from the existing colours so that skins need no change; shell components (`.rail`,
`.nav-section`, `.page-header`, tabs, cards) use only tokens. Mission Control and Synthwave get the few rules
the new components need (rail, page header, tabs). Serviette and Codex blocks are deleted.

### Error handling

A route to an unknown tab shows the default tab; a route to the architecture or requirements view without a
project is ignored and the diagram view stays. A panel load failure is shown in the panel's status line as before.

## Testing

- `tests/js/routes.test.mjs`: parse/format round trip, unknown values, tab fallback.
- `tests/js/modules.test.mjs`: `routes.js` pure, `archview.js` layer 6, `shell.js` layer 7.
- `tests/js/skins.test.mjs`: three skins; a stored `napkin` or `codex` resolves to Klassisch.
- e2e smoke: the Architektur steps use the page (`#architecture-view`, hash `#/architecture/scenarios`), the
  rail and navigator groups exist, back button returns to the previous tab, three skins are offered.

## Migration / compatibility

A stored skin id `napkin` or `codex` falls back to the default (`resolveSkin`). Element ids of the panels and
the old navigator buttons are unchanged. No data or API changes.

## Follow-ups

- The panels keep their stacked form layout inside the page; a master/detail layout per tab is a later step, as are the command
  palette, counts in the navigator and moving **Modell** to a page.

## Open questions

None.

## Decisions

- Scope (rail, grouped navigator, Architektur page), top bar unchanged, hash routes: maintainer, 2026-10-20.
- Serviette and Codex dropped, Mission Control and Synthwave kept: maintainer, 2026-10-20.
