# Architektur workspace: two-pane lists, shared components and motion

- Status: Implemented
- Issue: not yet filed
- ADR: [0196 Master/detail with one editor per tab and a shared component layer](../adr/0196-master-detail-editor-and-component-layer.md)
- Builds on: [App shell](app-shell.md), [Decision support](decision-support.md), [Architecture drivers](architecture-drivers.md)

## Summary

The four tabs of the Architektur page ([app shell](app-shell.md)) still show the markup of the old dialog: drivers and
stakeholders are rows full of inline form controls, scenarios and decisions stack a list above a form. This change
gives every tab the same **two-pane layout**: a filterable list of cards on the left, the editor of the selected item
on the right. It also introduces the first **shared component layer** (buttons, cards, chips, fields, empty states,
focus ring, transitions) that the page uses and the rest of the UI can adopt step by step.

## Goals

- One layout for all tabs: toolbar (filter field, **+ New**), list, editor. The editor is always visible: without a
  selection it is the "new item" form, so creating needs no extra click.
- Selecting a card fills the editor; explicit **Speichern**/**Abbrechen**; switching items or tabs with unsaved
  changes asks once whether to discard them (`confirmDiscard`).
- Keyboard: ↑/↓ move the selection in the list (also while the list has focus), `Enter` opens, `Esc` in the editor
  cancels (reverts to the stored values).
- The list filters as you type (all words must occur in the card text, case-insensitive); the count of shown items is
  announced in the status line.
- Scenarios: the what-if analysis of the selected scenario is part of its editor (below the form), no longer inside
  the list item. Decisions: **+ Entscheidung** opens the editor in create mode, replacing the `prompt()` dialog.
- Components in a separate stylesheet `components.css` on the existing colour and shell tokens: `.btn` (primary,
  secondary, ghost, danger, small), `.card`, `.chip` (tones), `.field`, `.md-*` (two-pane), `.empty-state`; a global
  `:focus-visible` ring and 150 ms transitions that `prefers-reduced-motion` switches off. Klassisch, Mission
  Control and Synthwave keep working: the components read only tokens, the skins add their few overrides.
- Responsive: below 900 px the editor moves below the list.

## Non-goals

- Autosave. Saving stays explicit (a decision such as *accepted* binds the assistants, so it must be deliberate).
- Restyling the rest of the UI (top bar, chat, inspector) with the component layer; that follows with the
  [command palette](command-palette.md) step.
- New API endpoints or data fields: only the existing REST calls are used.
- Searching across tabs (the command palette).

## Design

### Pure module `listnav.js`

- `filterItems(items, query, textOf)`: items whose `textOf(item)` contains every word of `query` (split on
  whitespace, lower-cased with `toLocaleLowerCase("de")`); an empty query keeps all items, order is kept.
- `stepId(ids, current, delta)`: the id `delta` steps from `current` in `ids`, clamped to the ends; `null`
  (nothing selected) steps to the first id for `+1` and to the last for `-1`; `null` for an empty list.

### View helpers `mdpane.js` (layer 2, views)

`card({lead, title, chips, meta})` builds the list button (`.md-card`, built with DOM APIs, texts never reach
`innerHTML`), `chip(text, tone)`, `markSelected(list, id)` (`aria-current="true"` on the card of `data-id`),
`bindListKeys(list, onStep, onOpen)` (↑/↓/Enter) and `confirmDiscard(dirty)` (`window.confirm` only when dirty) and `hideIfEmpty(select)` (no empty multi-select box in the editor).

### Markup and ids

Each panel is `.arch-panel > .md-toolbar + .md-intro + .md > (.md-side, form.md-detail.card)` plus a status line.

| Tab | List (kept ids) | Editor (ids) |
|---|---|---|
| Treiber | `#drivers-list` (`li.driver-row[data-driver-id]` with `.driver-badge` carrying importance and risk, `.md-title`, a kind chip), `#drivers-empty` | `#driver-form`: `driver-title`, `driver-kind`, `driver-importance`, `driver-risk`, `driver-description`, `driver-elements`, `driver-requirements`; `driver-save`, `driver-cancel`, `driver-delete`, `driver-up`, `driver-down`; toolbar `drivers-filter`, `driver-add` |
| Szenarien | `#scenarios-list` (groups by driver, `article.scenario-item[data-scenario-id]`), `#scenarios-empty` | `#scenario-form` (all ids kept); `#scenario-analyse` is a button on the editor card outside the form (`.md-detail-wrap`, so it stays usable read-only) and its result is `#scenario-analysis` below; `scenarios-filter`, `scenario-add` |
| Entscheidungen | `#decisions-list` (`li.decision-row`), `#decisions-empty` | `#decision-form` (ids kept; `.hidden` while no item is selected and not creating), `#decision-empty-detail`; `decisions-filter`, `decision-add` |
| Stakeholder | `#stakeholders-list` (`li.stakeholder-row[data-stakeholder-id]`), `#stakeholders-empty` | `#stakeholder-form`: `stakeholder-name`, `-role`, `-influence`, `-concerns`, `-drivers`; `stakeholder-save`, `-cancel`, `-delete`; `stakeholders-filter`, `stakeholder-add` |

The old creation forms (`#driver-new`, `#stakeholder-new`) and `#decision-new`'s `prompt()` are gone; a viewer
(`body.readonly`) still sees the lists and the editor read-only (`data-edit-zone`).

### Behaviour

- Selection state lives in each panel module (`selected` id or `null`); the editor has a `dirty` flag set by
  `input`/`change` events and cleared on load, save and cancel.
- `archview.js` asks every panel `isDirty()` before it switches tabs or the project reloads the page, and cancels the
  switch when the user declines; a route change (back button) to another tab is applied after the same question.
- A tab's data loads when it is shown; a load that finishes while the editor is dirty re-renders the list but leaves the
  editor alone, so late data never wipes what the user has already typed.
- Save re-renders the list, keeps the item selected (create mode: selects the new item) and says so in the status
  line; delete asks `confirm`, then returns to create mode.

### Components and motion

`components.css` is loaded after `style.css`. It defines classes only (no element selectors) so nothing outside the
page changes. Focus ring: `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }` for buttons,
links, tabs, cards and fields. Transitions (150 ms on colour, border, shadow, transform) sit behind
`@media (prefers-reduced-motion: no-preference)`. Skin overrides: Mission Control squares and glows `.btn`,
`.card` and `.chip`, Synthwave adds the neon outline to the selected card.

## Testing

- `tests/js/listnav.test.mjs`: `filterItems` (empty query, several words, case, umlauts, order kept), `stepId`
  (clamping, nothing selected, empty list).
- `tests/js/modules.test.mjs`: `listnav.js` pure, `mdpane.js` layer 2.
- e2e smoke: every Architektur step uses the new editors (create a driver through the toolbar, list shows it by
  priority, select fills the editor, filter narrows the list, ↓ moves the selection, unsaved changes ask before
  switching, a scenario analysis in the editor, a decision from an option opens in the editor, a stakeholder is
  created); a narrow window stacks the panes.

## Migration / compatibility

No data or API change. Element ids of the old inline rows (`.driver-title` as an input, `#driver-new-*`,
`#stakeholder-new-*`) disappear; the e2e test and `docs/guide/architecture.md` are updated with them.

## Open questions

None.

## Decisions

- Two-pane with an inline right editor, explicit save; slim top bar and the palette in a separate step; components and
  motion included: maintainer, 2026-10-21.
