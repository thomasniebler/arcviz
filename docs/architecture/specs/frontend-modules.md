# Frontend split into ES modules

- Status: Implemented (milestone M11)
- Issue: #88
- ADR: [0020 Native ES modules by feature, pure logic in separate Node-testable modules](../adr/0020-frontend-native-es-modules.md)

## Summary

`arcviz/static/app.js` has grown to about 1300 lines that mix pure logic (zoom maths, SSE parsing,
container trees, labels) with DOM wiring for eight features. Only the four small modules split off
earlier (`speech.js`, `commands.js`, `reply.js`, `proposal.js`) have unit tests. This change splits
`app.js` into native ES modules along the features of the page, and moves every piece of logic that
does not need the DOM into pure modules tested with `node --test`. The page keeps working from
`arcviz/static/` without a build step, and nothing the user sees changes.

## Goals

- One module per feature of the page (canvas with pan and zoom, splitter, chat transcript, chat turn
  over SSE, diagram/focus, element inspector, versions and comparison, export/import, voice), each
  small enough to read in one sitting.
- Pure logic (no DOM, no `window`, no `fetch`) in separate modules with unit tests under `tests/js/`.
- An acyclic import graph with a documented direction, checked by a test, so later milestones can add
  features without tangling the modules again.
- No behaviour change: same DOM, same requests, same `localStorage` keys, same event order. The e2e
  smoke test passes without changing what it asserts.

## Non-goals

- A bundler, TypeScript, a framework or any new npm runtime dependency (ADR-0020).
- Changing `index.html` beyond what loading the modules needs (it already loads `app.js` as a module,
  which is all it needs).
- Unit-testing DOM code with a fake DOM (jsdom would be a new dependency); DOM wiring stays covered by
  the e2e smoke test.
- Fixing behaviour quirks found on the way; they become issues.

## Design

### Module layout

Pure modules (no DOM access at all; imported by `tests/js/*.test.mjs`):

| Module | Content |
|---|---|
| `geometry.js` | Pan/zoom maths: `ZOOM_MIN/MAX/STEP`, `FIT_PADDING`, `zoomAt(view, factor, cx, cy)`, `fitTransform(size, box)`, `wheelFactor(event, pageHeight)`, `viewTransform(view)`, `zoomLabel(scale)`; splitter maths: `CHAT_MIN`, `CANVAS_MIN`, `SPLITTER_WIDTH`, `chatWidthMax(layoutWidth)`, `clampChatWidth(px, layoutWidth)` |
| `sse.js` | `createSseParser()` (`push(text)` returns the complete events parsed from `data:` lines) and `readSse(body, onEvent)`, which reads a `ReadableStream` and awaits `onEvent` per event |
| `model.js` | Helpers over the diagram JSON: `isContainer`, `descendantIds`, `containerOptions`, `withCurrent`, `parentFocus`, `staleFocus(answerFocus, currentFocus, nodes, canFocus)`, `nodeIdForSvgId(svgId, mermaidIds)`, `propertyChanges(rows, current)` |
| `format.js` | German UI labels: `describeCall`, `toolError`, `tokenMeta`, `truncate`, `diagramOptionLabel`, `diagramStats`, `versionOptionLabel`, `versionLabel`, `chatEntryMeta`, `finalMeta`, `compareSummary`, `edgeSummary`, `exportName` |
| `commands.js`, `speech.js`, `reply.js`, `proposal.js` | unchanged |
| `types.js` | Added by M13 ([diagram types](diagram-types.md)): `typeConfig`, `viewOf`, `TYPE_LABELS`, `unsupportedTitle`, `templateChoices` |
| `elements.js` | Added by M13 ([sequence diagrams](sequence-diagrams.md)): `findElement`, `messageCount`, `elementFields`, `elementUpdate`, `elementRemove`, `elementTitle`, `ELEMENT_LABELS` |

DOM modules (browser only; each exports functions and one `init…()` that attaches its listeners):

| Module | Content |
|---|---|
| `dom.js` | `$()`, the element registry `el` filled by `bindElements()`, `option()` |
| `state.js` | The shared `state` and `view` objects, `focusQuery()` |
| `api.js` | `api(path, options)`: JSON fetch wrapper that throws the server's `detail`; `apiFetch` (also used for the chat stream and downloads) sends a 401 to the login page (#82) |
| `ui.js` | `setStatus()`, `setBusy()` |
| `canvas.js` | Theme (`applyTheme`), `renderMermaid`, `applyView`, `fitView`, `zoomCenter`, `resetView`, wheel/drag/click handling; `initCanvas({onClick})` |
| `splitter.js` | Splitter drag, keyboard, double-click, resize and ARIA; `initSplitter()` |
| `topbar.js` | Menu behaviour of the top bar's menus (one open at a time, closed by an action, a click elsewhere or Esc; [menu bar](personal-settings.md)); `initTopbar()` |
| `mdpane.js` | Cards, chips, selection mark, list keys and the discard question of the Architektur two-pane lists ([Architektur workspace](architecture-workspace.md)) |
| `chatlog.js` | Transcript rendering: `addMessage`, `addSummary`, `showChat`, `makeToolLine` |
| `diagram.js` | `showDiagram`, `showFocus`, `setFocus`, `openDiagram`, `loadDiagramList`, `refreshDiagramListLabel`, `fillDirections`, the hooks `onDiagramShown` / `onDiagramSwitched`; diagram selector, new/delete/clear-chat buttons and the view selector; `initDiagram()` |
| `inspector.js` | Direct edits through `POST /ops`: `openInspector`, `closeInspector`, `runOps`, `handleDiagramClick`, the layout-direction selector; `initInspector()` |
| `versions.js` | `fillVersions`, preview, restore, undo/redo (buttons and shortcuts), snapshot, comparison (`showCompare`, `endCompare`); `initVersions()` |
| `export.js` | SVG/PNG/Mermaid export of the view, JSON/PlantUML download, JSON import; `initExport()` |
| `voice.js` | Speech input glue, local voice commands (`runCommand`), help popover, speech output (`speakReply`); `initVoice()` |
| `chat.js` | `sendMessage` (SSE turn via `readSse`), propose mode (`showProposal`), composer; `initChat()` |
| `paletteview.js` | The command palette dialog and `Strg+K` (layer 6; [command palette](command-palette.md)) |
| `archview.js` | The Architektur page: tabs and loading through the panel modules (layer 6, above them); `initArchView({onApply})`, `showArchitectureView(on, tab)` ([app shell](app-shell.md)) |
| `shell.js` | Main view and hash routes (`setMainView`, `initShell`; layer 7, above `chat.js` and `archview.js`; [app shell](app-shell.md)) |
| `app.js` | Entry point: `bindElements()`, the `init…()` calls in a fixed order, boot (`/api/config`, `loadDiagramList`) |

### Import direction

Modules are layered; a module imports only from lower layers:

| Layer | Modules | May import |
|---|---|---|
| entry | `app.js` | everything |
| shell | `shell.js` | `chat.js`, `archview.js` and the layers below |
| features | `chat.js`, `archview.js` | `voice.js`, `versions.js` and the layers below |
| | `voice.js`, `versions.js`, `inspector.js`, `export.js`, `splitter.js`, `topbar.js` (menus of the top bar), `settings.js` ([personal settings](personal-settings.md)), `driverpanel.js` ([architecture drivers](architecture-drivers.md)), `scenariopanel.js`, `decisionpanel.js`, `stakeholderpanel.js` ([decision support](decision-support.md)), `proposalpanel.js` (also the items of project proposals), `requirementview.js` ([requirements workshop](requirements-workshop.md); pure part `requirements.js`), `tokenpanel.js` ([agent access](agent-access.md)), `review.js` (M12), `appearance.js` ([UI skins](ui-skins.md)), `modelpanel.js` (M18, [shared project model](shared-project-model.md)), `welcome.js` ([onboarding](onboarding.md): starters in the empty project, guided tour; pure part `onboarding.js`), `releasebanner.js` ([release highlights](whats-new.md); pure part `whatsnew.js`), `workspaces.js` (M20/M21, [personal workspaces](personal-workspaces.md): the user and the sharing dialog), `live.js` (M10, [collaboration](collaboration.md)) | the layers below |
| navigator | `navigator.js` (M16, [project navigator](project-navigator.md)) | the layers below |
| hub | `diagram.js` | the layers below |
| views | `canvas.js`, `chatlog.js`, `ui.js` | the layers below |
| base | `dom.js`, `state.js`, `api.js` | pure modules |
| pure | `geometry.js`, `sse.js`, `model.js`, `format.js`, `commands.js`, `speech.js`, `reply.js`, `proposal.js`, `findings.js` (M12), `types.js`, `elements.js` (M13), `skins.js` ([UI skins](ui-skins.md)), `projects.js` (M16, [project navigator](project-navigator.md)), `projectchat.js` (M17, [project chat](project-chat.md)), `sharedmodel.js` (M18, [shared project model](shared-project-model.md)), `relations.js` (M25, [model relations](model-relations.md)), `access.js` (M20/M21, [personal workspaces](personal-workspaces.md)), `conflicts.js`, `presence.js`, `writequeue.js` (M10, [collaboration](collaboration.md)), `svgsafe.js` (M23, [hardening](hardening.md)), `markdown.js` ([Markdown in replies](chat-markdown.md)), `drivers.js` ([architecture drivers](architecture-drivers.md)), `agentaccess.js` ([agent authentication](agent-authentication.md)), `decisions.js` ([decision support](decision-support.md)), `routes.js` (hash routes, [app shell](app-shell.md)), `lifecycle.js` (graded deletion, [graded deletion](graded-deletion.md)), `listnav.js` (list filter and keyboard step, [Architektur workspace](architecture-workspace.md)), `palette.js` (command registry and ranking, [command palette](command-palette.md)) | pure modules |

Rules, checked by `tests/js/modules.test.mjs`:

1. The import graph of `arcviz/static/*.js` is acyclic.
2. Pure modules import only pure modules.
3. No module except `app.js` touches the DOM, `window` or `document` while it is evaluated; every
   module except `app.js` can be imported in Node.

Lower modules never call upwards. Where `diagram.js` must let a feature react to a newly shown state,
it offers hooks instead of importing the feature:

- `onDiagramShown(fn)`: `fn(detail)` runs at the end of every `showDiagram`. `inspector.js` uses it to
  close the inspector when its element is gone, `versions.js` to refill the version list from
  `detail.versions` and to end a comparison.
- `onDiagramSwitched(fn)`: runs when `openDiagram` opens a different diagram (before it is loaded);
  `inspector.js` closes the inspector.

The canvas reports a click on the SVG through the `onClick(target)` callback given to `initCanvas`;
`app.js` passes `handleDiagramClick` from `inspector.js`.

### Order of initialisation

`app.js` calls `bindElements()`, then `initCanvas`, `initSplitter`, `initInspector`, `initVoice`,
`initDiagram`, `initChat`, `initVersions`, `initExport`, then boots. This keeps the registration order
of listeners that share an element as before: the voice module's `submit` and diagram-selector
`change` listeners (stop recording, stop speaking) run before the chat turn starts and before the
diagram switches, and the one-time "no speech" notice is in the transcript before `showChat` renders
it.

### Error handling, concurrency

Unchanged: the sequence counters (`renderSeq`, the focus sequence, `exportSeq`) move with their
functions; `api()` still throws `Error(detail)`; `readSse` propagates a JSON parse error or a read
error to `sendMessage`, which shows it in the reply bubble as before.

## Testing

- `tests/js/geometry.test.mjs`, `sse.test.mjs`, `model.test.mjs`, `format.test.mjs` pin the pure
  modules (zoom keeps the point under the cursor fixed and respects the limits, fit centres with
  padding and may go below the interactive minimum, wheel normalisation, splitter clamping, SSE events
  split across chunks and multi-byte characters split across reads, container tree order and depth,
  descendant exclusion, SVG id mapping, property diffs, labels and export file names).
- `tests/js/modules.test.mjs` enforces the three rules above.
- The e2e smoke test is unchanged and must pass: it covers every DOM module.

## Migration / compatibility

None for users. `index.html` keeps loading `/static/app.js` as a module; the browser fetches the other
modules from `/static/`, which the server already serves. The wheel and the Docker image include the
new files because they live under `arcviz/`.

## Open questions

None open; decisions taken without the maintainer are *Proposed* in the ADR.

## Decisions

- Native ES modules split by feature, pure logic in separate modules, no bundler (ADR-0020, Proposed).
- Hooks on `diagram.js` instead of import cycles between feature modules (ADR-0020, Proposed).
- DOM wiring is not unit-tested with a fake DOM; the e2e smoke test covers it (no jsdom dependency).
