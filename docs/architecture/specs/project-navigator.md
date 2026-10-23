# Project navigator with sections

- Status: Implemented (milestone M16)
- Issue: #133
- ADR: [0055 A navigator column with its own layer between the hub and the features](../adr/0055-navigator-column-and-layer.md)
- Builds on: [Projects](projects.md) (#131, #132), [Frontend split into ES modules](frontend-modules.md)

## Summary

The topbar's diagram drop-down lists every diagram of the installation. With projects (#131/#132) it is
replaced by a **project navigator**: a collapsible column left of the chat with a project switcher and
the project's section tree, in which diagrams and sections are created, renamed, moved, reordered and
deleted. The navigator remembers the open project, the open diagram, collapsed sections and whether it
is shown. Everything it shows comes from the project API; it holds no business logic.

## Goals

- Project switcher: choose, create, rename, describe and delete (empty) projects.
- Section tree: sections and diagrams in their stored order, sections collapsible; the open diagram is
  marked (`aria-current`).
- Actions on a diagram: open, rename, move (to another section or project), up/down, delete. On a
  section: new diagram here, new subsection, rename, up/down, delete (its contents move up).
- New diagrams and imports go into the open project (the new-diagram dialog offers its sections).
- Remembered selection across reloads (`localStorage`), robust against deleted projects and diagrams.
- The frontend layering stays enforced by `tests/js/modules.test.mjs`; pure logic is unit-tested.

## Non-goals

- Drag and drop (up/down buttons and the move dialog cover reordering and moving; drag and drop can
  follow without API changes).
- Project chat, project review (M17). (#134 adds export and import entries to the project menu.)
- Showing diagrams of several projects at once.

## Design

### Layout

`<body>` keeps the topbar. Below it a `.workspace` row holds `<aside id="navigator">` (240 px)
and the existing `<main class="layout">` (chat, splitter, canvas), so the splitter's maths, which is
relative to `.layout`, is unchanged. The topbar gets a toggle button **Projekte** (`#btn-navigator`,
`aria-expanded`), which shows or hides the column (remembered as `arcviz.navigator`). Below 900 px the
column overlays the workspace when open.

The topbar keeps **Neu**, **Importieren** and the diagram buttons; `#diagram-select` is removed. The
buttons that act on the open diagram (delete, undo, redo, snapshot, versions, restore, compare, clear
chat) sit in `display: contents` groups of class `tools` (`#diagram-tools`, `#diagram-tools-2`).

### Navigator content

```
Projekt [select #project-select] [+ #btn-project-new] [⋯ #project-menu: Umbenennen…, Beschreibung…, Löschen]
[+ Abschnitt #btn-section-new]
#nav-tree
  ▾ Backend                        [+ ] [⋯]   section row (data-section-id)
      Orders                        [⋯]        diagram row (data-diagram-id, button .nav-open)
  Overview                          [⋯]
```

Each row is an `<li>` in a `role="tree"` list with `aria-level`; a section row has a collapse toggle
(`aria-expanded`) and an add button (new diagram in this section); every row has a `⋯` menu (`<details
class="menu">`) with its actions. **Löschen** of a project is disabled while it has diagrams (the
server refuses it with 409, ADR-0054).

Names are asked for in one reusable dialog `#name-dialog` (heading, one text field) instead of
`prompt()`. **Verschieben…** opens `#move-dialog` with a project select and a section select (the
sections of the chosen project, loaded on change, "Oberste Ebene" first).

### Modules and layers

| Module | Layer | Content |
|---|---|---|
| `projects.js` | pure | `treeRows(tree, diagrams, collapsed)` (visible rows with depth, kind, title, type, position among siblings), `sectionChoices(tree)` (indented labels for selects), `pickProject(projects, wanted, remembered)`, `pickDiagram(view, wanted, remembered)`, `siblingMove(rows, row, delta)` (the `position` for up/down or `null` at an end), `nextDiagramId(view, current)`, `projectOptionLabel(summary)` |
| `navigator.js` | navigator (new, between the hub and the features) | the column: `loadDiagramList(selectId)`, `refreshDiagramListLabel()`, `openProject(id)`, `nextDiagram()`, `currentProject()`, the name and move dialogs, the new-diagram dialog and the diagram/chat buttons that change the list; `initNavigator()` |
| `diagram.js` | hub | keeps `showDiagram`, focus, `openDiagram`, hooks, directions; loses the list and its buttons |

The layers become: pure → base → views → hub `diagram.js` → **navigator `navigator.js`** → features →
`chat.js` → `app.js`. Features that refreshed the drop-down (`chat.js`, `inspector.js`,
`versions.js`, `voice.js`, `export.js`) import `loadDiagramList`/`refreshDiagramListLabel` from
`navigator.js` instead of `diagram.js`; the voice command "nächstes Diagramm" uses `nextDiagram()`
(next diagram of the open project in tree order, wrapping). `tests/js/modules.test.mjs` gets the new
layer.

### Behaviour

- **Boot**: `GET /api/projects` (which also adopts diagrams without a project into the default
  project). With no project at all the demo diagram is created as before (it lands in the default
  project). The project is the one asked for, else the remembered one (`arcviz.project`) if it still
  exists, else the first; the diagram likewise (`arcviz.diagram`), else the first in tree order.
- **Empty project**: the canvas shows a placeholder ("Dieses Projekt enthält noch keine Diagramme."),
  the chat and canvas (`.layout`) and the `.tools` groups are `inert`, `state.diagramId` is `null`; **Neu**
  and **Importieren** stay active.
- **Create/import**: in the open project; the new-diagram dialog has a section select (preselected
  when opened from a section's add button).
- **Rename diagram**: `POST /api/diagrams/{id}/ops` `rename_diagram` (a version, like every edit).
- **Up/down**: `POST /api/projects/{pid}/diagrams/{did}/move` with the sibling position, or
  `PATCH …/sections/{sid}` with `position`; the end positions disable the buttons.
- **Delete diagram**: confirm, `DELETE /api/diagrams/{id}`, then the list is reloaded (the next diagram
  of the project opens). **Delete section**: confirm ("Inhalte wandern eine Ebene nach oben"),
  `DELETE …/sections/{sid}`.
- **Move**: `POST …/move {project, section}`; moving the open diagram to another project switches the
  navigator to that project.
- **Remembered**: `arcviz.project`, `arcviz.diagram` (as before), `arcviz.navigator` (`"0"` when
  hidden), `arcviz.collapsed.<project id>` (JSON list of collapsed section ids). Every `localStorage`
  access is wrapped in `try`.
- Errors of the project API are shown with `alert()` like the other list actions; nothing is changed
  locally until the server answered.
- Labels are German; diagram rows show the title (the diagram list carries no type).

## Testing

- `tests/js/projects.test.mjs`: tree rows (order, depth, collapsed sections hide their contents,
  positions), section choices, project and diagram picking with deleted remembered ids, sibling moves at
  the ends, next diagram wrapping, labels.
- `tests/js/modules.test.mjs`: the new layer.
- e2e (`tests/e2e/smoke.mjs`): the navigator lists the demo diagram in the default project; existing
  steps select diagrams through the navigator; a new project with a section, a diagram created in that
  section, rename, reorder, a move into another project, deleting a section moves its diagram up,
  deleting the empty project, and the remembered selection after a reload.

## Migration / compatibility

The drop-down disappears; everything it did is in the navigator. `localStorage` key `arcviz.diagram` is
kept, so the last diagram opens again after the upgrade (in the default project).

## Open questions

None open; ADR-0055 is *Proposed*.

## Decisions

- A navigator column instead of a drop-down, with its own layer between the hub and the features
  (ADR-0055, Proposed).
- Up/down buttons and a move dialog instead of drag and drop (non-goal above).
- A reusable name dialog instead of `prompt()`, so names are entered in the page (and testable).
