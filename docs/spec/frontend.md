# Frontend

Source: `arcviz/static/index.html`, `arcviz/static/style.css` and the ES modules `arcviz/static/*.js`
(entry point `app.js`, see [Modules](#modules)). Unit tests: `tests/js/`.
Browser test: `tests/e2e/smoke.mjs` (Playwright, scripted provider from `tests/e2e/server.py`). It never derives an
id from a title: ids come from the navigator (looked up by title) or from responses, so it passes with the readable ids
the e2e server pins and with the production ids (`ARCVIZ_E2E_REAL_IDS=1`, which CI runs as a second step, #196).

## Shape

A single page with no framework and no build step. `index.html` loads the vendored single-file
Mermaid build `arcviz/static/vendor/mermaid.min.js` (exposes `window.mermaid`; version in
`vendor/VERSION`, updated with `scripts/vendor_mermaid.py`), then `app.js` as an ES module, which
imports the other modules from `/static/`. No request leaves the origin. UI strings are German.

The top bar's menu **Hilfe** (`<details id="help-menu" class="menu">`, static HTML,
no module): links to `/docs/guide/`, `/docs/architecture/arc42/` and `/docs/`, opened in a new
tab (`target="_blank" rel="noopener"`). The app serves the documentation of its own version there
([HTTP server: Documentation](http-server.md#documentation)).

### Modules

Native ES modules without a build step (design: [Frontend split into ES modules](../architecture/specs/frontend-modules.md),
ADR-0020), in layers; a module imports only from lower layers:

| Layer | Module | Content |
|---|---|---|
| pure | `geometry.js` | Zoom limits and step, `zoomAt`, `fitTransform`, `wheelFactor`, `viewTransform`, `zoomLabel`; splitter limits, `chatWidthMax`, `clampChatWidth` |
| | `sse.js` | `createSseParser()`, `readSse(body, onEvent)` |
| | `model.js` | `isContainer`, `descendantIds`, `containerOptions`, `withCurrent`, `parentFocus`, `staleFocus`, `nodeIdForSvgId`, `propertyChanges` |
| | `format.js` | Labels: `describeCall`, `toolError`, `tokenMeta`, `truncate`, `diagramStats`, `authorName`, `versionOptionLabel`, `versionTitle`, `versionLabel`, `chatEntryMeta`, `finalMeta`, `compareSummary`, `edgeSummary`, `exportName`, `reviewMeta` |
| | `commands.js`, `speech.js`, `reply.js`, `proposal.js` | Voice commands, speech input, reply buffer, proposal diff lines (including the interaction `elements` of sequence diagrams) and the items of project proposals (`selectionClosure`: ticking an item ticks the items it needs) |
| | `requirements.js` | The requirements view's pure helpers: labels, `listQuery`, `pipeline`, `acceptItems` (the revision the user saw), `unremarkableIds`, `rangeIds`, `suggestionFields`, `shouldContinue` (a chunk loop), import texts |
| | `findings.js` | Review findings: `CATEGORY_LABELS`, `SEVERITY_LABELS`, `groupFindings`, `mergeFindings`, `elementNames`, `edgeSvgKeys`, `diagramKey`, `suggestionPrompt`, `findingTarget`, `highlightable` (project review) |
| | `types.js` | `typeConfig`, `viewOf`, `TYPE_LABELS`, `unsupportedTitle`, `templateChoices` ([Diagram types](diagram-types.md#frontend)) |
| | `skins.js` | UI skins: `SKINS`, `DEFAULT_SKIN`, `SKIN_STORAGE_KEY`, `resolveSkin` ([Skins](#skins)) |
| | `elements.js` | Sequence-diagram elements: `findElement`, `messageCount`, `elementFields`, `elementUpdate`, `elementRemove`, `elementTitle` |
| | `projectchat.js` | Project chat: `describeProjectCall` (German tool lines of the project tools, `null` for others), `changedDiagramItems`, `changedDiagramsLabel`, `diagramSwitch`, `scopeLabel` ([Project chat](#project-chat)) |
| | `routes.js` | Hash routes of the shell ([app shell](../architecture/specs/app-shell.md), ADR-0194): `ARCH_TABS`, `parseRoute(hash)` (`{view: "diagram"}`, `{view: "requirements"}` or `{view: "architecture", tab}`), `formatRoute(route)` |
| | `relations.js` | Model relations: `RELATION_KIND_LABELS`, `relationKindLabel`, `relationKindName`, `relationText`, `viewConnections`, `fits`, `relationChoices`, `shownElements`, `relationsNotDrawn`, `completenessText`, `resolutionRequest`, `needsConfirmation`, `elementKinds`, `nestedNodes`, `showsRelation` (mirrors `project_model.shows`; both checked against `tests/fixtures/relations_shown.json`) ([model relations](../architecture/specs/model-relations.md)) |
| | `sharedmodel.js` | Shared project model: `ELEMENT_KIND_LABELS`, `kindLabel`, `linkableKinds`, `viewElement`, `findModelElement`, `linkChoices`, `defaultKind`, `usageText`, `elementChanges`, `propagationText` ([Shared project model](#shared-project-model)) |
| | `access.js` | Users, own and shared projects and roles ([Own and shared projects](#own-and-shared-projects)): `ROLE_LABELS`, `ROLES`, `SHARE_ROLES`, `READ_ONLY_HINT`, `FORBIDDEN_TEXT`, `EMPTY_PROJECTS_TEXT`, `canEdit`, `canOwn`, `abilities`, `roleLabel`, `projectGroups`, `offerDemo`, `sharedLabel`, `personLabel`, `shareRows`, `editLock`, `addedText`, `suggestionLabel`, `wantsSuggestions` (share suggestions, ADR-0165) |
| | `presence.js` | Presence ([Presence](#presence)): `PRESENCE_COLORS`, `initials`, `colorOf`, `viewersToShow`, `selectionMarks`, `chatNotice` |
| | `markdown.js` | Markdown of assistant replies ([spec](../architecture/specs/chat-markdown.md), ADR-0162): `parseBlocks`, `parseInline` (a syntax tree, never HTML), `safeHref` (http(s)/mailto only), `plainText` (speech output) |
| | `drivers.js` | Architecture drivers ([design](../architecture/specs/architecture-drivers.md)): `DRIVER_KIND_LABELS`, `LEVEL_LABELS`, `driverKindLabel`, `priorityBadge`, `priorityTitle`, `sortedDrivers` (importance, then risk, then list order), `moveTarget` (the list position that swaps a driver with its neighbour in its priority class), `driverChanges`, `concernsText` |
| | `agentaccess.js` | Agent access ([design](../architecture/specs/agent-authentication.md)): `mcpUrl`, `claudeAddCommand`, `jsonConfig`, `expiryText`, `scopeLabel`, `tokenBody` |
| | `decisions.js` | Decision support ([design](../architecture/specs/decision-support.md)): `SCENARIO_KIND_LABELS`, `SCENARIO_PARTS`, `STATUS_LABELS`, `EFFECT_LABELS`, `adrLabel`, `groupScenarios`, `scenarioLine`, `sortedDecisions`, `decisionFromAnalysis(scenario, analysis, index)`, `changedFields` |
| | `writequeue.js` | `createWriteQueue(shown)`: the inspector's writes, one after another, bound to their diagram; a captured revision is advanced only by own writes (`advanced`) |
| | `svgsafe.js` | The SVG allow-list sanitizer ([hardening](../architecture/specs/hardening.md)): `sanitizeSvg(root)` keeps only `ALLOWED_ELEMENTS` (svg, g, path, rect, circle, ellipse, line, polyline, polygon, text, tspan, title, desc, defs, marker, clipPath, linear/radialGradient, stop, use, style) in the SVG namespace (any other element or namespace goes with its subtree) and only `ALLOWED_ATTRIBUTES` plus `aria-*`/`data-*`; `href` only on `use` and only a `#fragment`; CSS (`<style>`, `style`, presentation values) only when strict: no backslash escape, no comment, no at-rule but `@media` and `@keyframes`, no `expression(`, no scheme, only the functions of `CSS_FUNCTIONS` (no `image-set` or other loading function), every `url()` exactly `url(#id)`; `mask` allowed like `clipPath` (icon packs); checks after removing whitespace and control characters; with `{htmlLabels: true}` (Mermaid, #197, ADR-0132) an SVG `foreignObject` may hold `LABEL_ELEMENTS` (div, span, p, br) in the XHTML namespace with `LABEL_ATTRIBUTES` (class, safe style, `xmlns` = XHTML), nothing else; `isFragment`, `isSafeCss`, `isUnsafeAttribute`, `isUnsafeLabelAttribute` |
| | `conflicts.js` | Concurrent edits ([Live updates](#live-updates)): `same`, `canonicalProperties`, `nodeValues`, `remoteChange`, `fieldConflicts`, `parseEtag`, `liveBackoff`, `changedBy`, `displayValue`, `confirmedDelete` (conditional deletes, #186), `changeAuthor` |
| | `projects.js` | Navigator logic: `treeRows`, `sectionChoices`, `pickProject`, `pickDiagram`, `diagramOrder`, `siblingMove`, `nextDiagramId`, `projectOptionLabel` ([Project navigator](#project-navigator)) |
| base | `dom.js` | `$()`, element registry `el` (`bindElements()`), `option()`, `placeholder(text)` (error placeholders as text, #119) |
| | `state.js` | `state` (also `me`, `role`), `view`, `focusQuery()` |
| | `api.js` | `api(path, options)` (merges `options.headers`; a failure throws an `Error` with `status` and `body`), `ifMatch(revision)`, `apiFetch(path, options)` (every request to the server; a 401 goes to `/login?next=<current page>`; a 403 becomes a German *Nur Lesezugriff* message, `errorText`), `loginUrl(location)` |
| views | `canvas.js` | Theme, `renderView` (hook `onRendered(fn)` after every successful render), `parseSvg`, `registerIcons` (vendored icon packs), `renderOffscreen`, pan and zoom, SVG clicks (`initCanvas({onClick})`), zoom and source buttons |
| | `chatlog.js` | `addMessage` (assistant bubbles as Markdown), `addSummary` (Markdown), `setRichText(div, text)` (the `markdown.js` tree built with DOM APIs; also used by `chat.js` for streamed and final replies), `showChat(chat, {scope})` (project entries also show their changed diagrams), `makeToolLine` |
| | `ui.js` | `setStatus`, `setBusy`, `applyFeatures`, `setRole(role)`, `applyReadOnly()` (read-only mode, called at the end of every `setBusy`) |
| hub | `diagram.js` | `showDiagram(detail, {highlight, fit, remote})` (sets `state.revision`), focus (`setFocus`, breadcrumb, view selector, up button), `openDiagram`, `fillDirections`, clear-chat button; hooks `onDiagramShown(fn)` (end of every `showDiagram`, `fn(detail, {remote})`), `onDiagramSwitched(fn)` (`openDiagram` of another diagram) and `onSelection(fn)` / `notifySelection(id)` (the element the inspector shows) |
| navigator | `navigator.js` | The project navigator: `loadDiagramList(selectId, projectId)` (own and shared projects, grouped; a placeholder when there is none), `askName`, `guarded`, `refreshDiagramListLabel()`, `openProject`, `nextDiagram(delta)`, `currentProject()`, `currentProjectInfo()`, `openInProject(id)`, `markChanged(ids)`, hook `onProjectShown(fn)`, the tree and its row actions, the name, move and new-diagram dialogs, the **Neu**/**Löschen** buttons; `initNavigator()` |
| features | `inspector.js` | Inspector (graph nodes and sequence elements: `openInspector`, `openElementInspector`, the **Projektelement** section), `runOps`, `handleDiagramClick`, direction selector |
| | `live.js` | The live stream of the tab ([Live updates](#live-updates)) and presence ([Presence](#presence)): `initLive()`, `onLiveEvent(fn)` |
| | `modelpanel.js` | The **Modell** dialog of the navigator: elements and relations of the open project, add/edit/delete, **Abgleichen**, the relation × diagram **Matrix**; `initModelPanel()` |
| | `versions.js` | Version list (`onDiagramShown`), preview, restore, undo/redo and shortcuts, snapshot, comparison (`endCompare` also on `onDiagramShown`) |
| | `export.js` | View export, model download, JSON import |
| | `voice.js` | Speech input glue, `runCommand` (next/previous diagram through `nextDiagram`), help popover, `speakReply` (stopped on `onDiagramSwitched`) |
| | `splitter.js` | Splitter |
| | `requirementview.js` | The requirements view (`state.mainView`): pipeline buttons with counts, filters, the table (pages of 100, selection with shift ranges), the detail panel (raw wording, fields in force, the suggestion beside them with per-field accept, issues, status, links, trace), import dialog (dry run first), the chunk loops **Aufbereiten** and **Ableiten** with **Abbrechen**, the traceability matrix; everything built with DOM APIs; refreshed after chat turns |
| | `driverpanel.js` | The **Treiber** tab of the Architektur page (`loadDrivers`, `isDriverDirty`; design: [Architektur workspace](../architecture/specs/architecture-workspace.md)): a filterable card list by priority (badge, title, kind chip, concerned elements/requirements) and one editor beside it (title, kind, importance, risk, description, elements, requirements; ↑/↓ inside a priority class, delete; **Anlegen** while no driver is selected, **Speichern** otherwise); read-only for viewers |
| | `scenariopanel.js` | The **Szenarien** tab (`loadScenarios`, `isScenarioDirty`, `initScenarioPanel({onDecided, onApply})`): a filterable card list grouped by driver, core first (★), the six-part editor beside it (new or selected) and **Analysieren** (`POST …/scenarios/{id}/analysis`, a button on the editor card outside the form, so viewers can use it) whose result is shown below the editor: facts, options, **Als Entscheidung festhalten**, **Als Vorschlag umsetzen** |
| | `decisionpanel.js` | The **Entscheidungen** tab (`loadDecisions(open)`, `isDecisionDirty`): a filterable card list by status and an editor beside it (title, status, superseded by, requirements, context, decision as `text`, consequences; options read-only, the chosen one framed), a **Testbarkeit** group (assumption rows with test, effort, status and evidence, the falsification criterion, **Vorschlag vom Assistenten** via `POST …/decisions/testability`; pure `testability.js`), **Als Markdown** (`GET …/decisions/{id}/markdown`); **+ Entscheidung** opens the editor in create mode (`POST` with title, status, context, decision, consequences, requirements) |
| | `stakeholderpanel.js` | The **Stakeholder** tab (`loadStakeholders`, `isStakeholderDirty`): a filterable card list and one editor (name, role, influence, concerns, drivers; new or selected) |
| features | `paletteview.js` (layer 6) | The palette dialog: `initPalette()`, `Strg+K`, availability of commands, rendering, running (see *Command palette* below) |
| views | `mdpane.js` (layer 2) | The helpers of the two-pane lists: `card`, `chip`, `markSelected`, `bindListKeys` (↑/↓/Enter), `confirmDiscard`, `hideIfEmpty`, `initAutosize(root)` (text areas grow with their text; CSS `field-sizing: content` where supported, else a height set after each edit; [readable editors](../architecture/specs/decision-ui-readability.md)) ([Architektur workspace](../architecture/specs/architecture-workspace.md), ADR-0196) |
| pure | `palette.js` | The palette registry `COMMANDS`/`GROUPS`, `fold`, `scoreMatch`, `matchRanges`, `rankCommands`, `rememberCommand` ([Command palette](../architecture/specs/command-palette.md), ADR-0197) |
| pure | `readability.js`, `testability.js` | `snippet(text, max)` (one-line card text cut at a word), `fitHeight` (height of an auto-growing area); the rows, suggestion merge and card chip of the decision [testability](../architecture/specs/adr-testability.md) |
| pure | `listnav.js` | `filterItems(items, query, textOf)` (every word, case-insensitive), `stepId(ids, current, delta)` (keyboard step, clamped) |
| | `archview.js` (layer 6) | The **Architektur** page (`#architecture-view`, a main view next to the requirements view): tabs Treiber, Szenarien, Entscheidungen, Stakeholder (`#arch-tabs`, each tab carries its `data-route`); every tab loads when shown; `showArchitectureView(on, tab)`, `currentArchTab()`, `initArchView({onApply})` (`onApply` sends an analysis option as a preview chat turn; `app.js` returns to the diagram view first, because the chat is hidden on the page) |
| shell | `shell.js` (layer 7) | `setMainView(view, tab)`: which main view fills the work area (`state.mainView`: `diagram`, `requirements`, `architecture`), the chat scope of the requirements view, the hash and the `aria-current` marks of the rail and navigator entries; `initShell()`: delegated clicks on `[data-route]` set the hash, `hashchange` applies it, the first project shown opens a deep link, **Zum Diagramm**/**Zum Diagramm-Chat**, the rail's settings button |
| | `topbar.js` | Menu behaviour of the top bar's menus (`initTopbar`) |
| | `settings.js` | Opens and closes **Persönliche Einstellungen** (`initSettings`) |
| | `appearance.js` | Skin selector: `applySkin`, `initAppearance()` ([Skins](#skins)) |
| | `welcome.js` (with the pure `onboarding.js`) | Starter cards of the empty project and the guided tour; `initWelcome()` ([spec](../architecture/specs/onboarding.md)) |
| | `releasebanner.js` (with the pure `whatsnew.js`) | The "new in this release" banner and **Hilfe → Neu in dieser Version**; `initReleaseBanner()` ([spec](../architecture/specs/whats-new.md)) |
| | `workspaces.js` | The current user (`setMe`) and the **Freigeben…** dialog; `initWorkspaces()` ([Own and shared projects](#own-and-shared-projects)) |
| | `review.js` | Architecture review panel for the shown diagram or (project mode) the open project: rule checks, KI-Review, findings list, highlight of the selected finding |
| | `chat.js` | `sendMessage(text, {propose})` (exported), `setChatScope(scope)` (diagram or project chat), `showProposal`, composer, propose toggle, the **Projekt-Chat** buttons |
| entry | `app.js` | `bindElements()`, then `initAppearance` (the skin is in place before anything renders), `initCanvas`, `initSplitter`, `initInspector`, `initVoice`, `initDiagram`, `initNavigator`, `initSettings`, `initWorkspaces`, `initModelPanel`, `initChat`, `initVersions`, `initExport`, `initReview({onApply})`, `initArchView({onApply})`, `initShell`, `initPalette`, `initLive` (this order fixes the order of listeners sharing an element: recording and speech stop before a turn starts or the diagram switches), then boot |

Only `app.js` has top-level side effects; every other module can be imported in Node.
`tests/js/modules.test.mjs` checks the layers (which implies an acyclic graph), that pure modules
import only pure modules, and that every module but `app.js` evaluates without a DOM.

Layout (`style.css`; design: [App shell](../architecture/specs/app-shell.md), ADR-0194): a top bar, then a `.workspace` row with the
icon rail (`<nav id="rail">`, 52 px: **Diagramme**, **Anforderungen**, **Architektur**, and the personal settings at the bottom;
the buttons carry `data-route`/`data-area`, the current area `aria-current`), the navigator (`<aside id="navigator">`,
240 px, hidden with the class `hidden`; the groups **Architektur** (Anforderungen, Treiber, Szenarien, Entscheidungen,
Stakeholder, Modell), **Prüfen & Assistent** (Projekt-Review, Projekt-Chat, Vorschläge), then the section tree) and a `.workarea` holding the grid `.layout`
`minmax(280px, var(--chat-width)) 6px minmax(320px, 1fr)` with the chat (`.chat`), a splitter
(`#splitter`, `role="separator"`) and the canvas (`.canvas`), plus the empty-project overlay
`#empty-project`. With the class `architecture-mode` on `.layout` the chat, splitter and canvas are hidden and `#architecture-view` fills the row; the shell tokens (`--surface-2`, `--radius-lg`, `--shadow`, `--space-1…6`) are derived from the colour tokens, so skins follow them. Below 900 px the grid becomes two rows, the splitter is hidden and an open
navigator overlays the workspace.

The top bar is **slim** (design: [Command palette](../architecture/specs/command-palette.md), ADR-0197; it was a menu bar, ADR-0176):
the brand, the navigator toggle **Projekte**, the search field `#palette-open` (opens the palette, shows `⌘K` on Apple platforms and
`Strg K` elsewhere, only its icon below 1000 px), a spacer, **Undo** and **Redo**, the **⋯** menu (`#menu-more`), **Hilfe** and
the user menu (`#user-menu`, label `#me` = the user's name or *Konto*: **Persönliche Einstellungen…** and, in token mode,
**Abmelden**). **⋯** holds the former menus under the headings **Diagramm** (**Neu…**, **Importieren…**, **Chat leeren**,
**Löschen**) and **Version** (**Version sichern**, the version selector, **Wiederherstellen**, **Vergleichen…**). The menus are
`<details class="menu">`; `topbar.js` keeps at most one open and closes it on an action, a click elsewhere or Esc. Every
control keeps its id; the bar hides the brand below 1000 px. `#diagram-tools` (inside **⋯**, **Chat leeren** and **Löschen**),
the `.tools` span around the version items and `#diagram-tools-2` (**Undo**, **Redo**) are the `.tools` groups an empty project makes inert;
**Neu…** and **Importieren…** stay outside them.

**Command palette** (`#palette`, `paletteview.js`; pure `palette.js`): `Strg+K`/`⌘K` or the search field opens a modal dialog
with the input `#palette-input` (`role="combobox"`) and the list `#palette-list` (`role="listbox"`). The registry `COMMANDS`
(`palette.js`) is plain data (`click` = id of the control to click, `route` = location hash, `href` = link in a new tab,
`needs: "project"`); the groups are **Gehe zu**, **Diagramm**, **Version**, **Ansicht**, **Export**, **Projekt**,
**Allgemein**, plus **Diagramme** (one entry per diagram of the open project, `navigator.projectDiagrams()`) and **Zuletzt**
(the five last used, `localStorage["arcviz.palette.recent"]`). A command is offered only if it can run now (its control is not
`disabled`, `hidden`, inert or hidden for a viewer or non-owner; diagram entries not while `state.busy`). A query shows one list by match quality (`scoreMatch`: every
word, case- and diacritics-insensitive, a start or word start beats a match inside a word; matches are `<mark>`ed), an empty one
the groups. ↑/↓ move (wrapping), Enter or a click runs, Esc or the backdrop closes; the palette closes first and the command runs on the next
task; focus returns to where it was.

**Persönliche Einstellungen** (`#settings-dialog`, `settings.js`): who is signed in (`#settings-user`), the skin selector
`#skin-select`, **Agenten-Zugang…** (`#btn-agent-access`; `tokenpanel.js` closes the settings dialog and opens the agent
dialog) and the provider line `#provider-info`. The navigator groups its project tools (`.nav-group`) and puts
**+ Abschnitt** above the tree; the canvas toolbars hold the diagram's own settings (**Richtung**, **Vollständigkeit**,
**Review**) in the first and the view controls (focus, zoom, **Quelltext**, **Export**) in the second row (`.tool-group`).

### Theme

All colours are CSS custom properties on `:root`; a `@media (prefers-color-scheme: dark)` block
overrides them (`color-scheme: dark` included, so form controls follow). `canvas.js` mirrors the
media query: `applyTheme()` sets `document.documentElement.dataset.theme` to `light`/`dark`,
re-initialises Mermaid with `theme: "neutral"` or `"dark"`, and re-renders the current source.
It runs at start-up and on every `change` of the media query. There is no manual light/dark override.

The diagram stage (`.diagram`) uses its own tokens `--stage-bg`, `--stage-grid`, `--stage-text`,
`--stage-muted`, set only on `:root` and in the dark-mode block, and resets `font` and `color`, so
nothing from a skin reaches the Mermaid labels or the sequence SVG.

### Skins

Design: [UI skins](../architecture/specs/ui-skins.md), ADR-0049. The selector **Design**
(`#skin-select`, in **Persönliche Einstellungen**) offers the skins of `skins.js`: `classic` (*Klassisch*, default), `mission-control`,
`synthwave`. A stored `napkin` or `codex` (removed, ADR-0195) resolves to `classic`. `appearance.js` sets `document.documentElement.dataset.skin`
on `change` (no reload, no re-render), stores the id in `localStorage["arcviz.skin"]` and applies a
`storage` event for that key, so other tabs follow. `resolveSkin` maps unknown or missing values to
`classic`. Each skin is a `:root[data-skin="…"]` block in `style.css` that overrides the UI tokens
(including `--font`, `--radius`, `--summary-bg`) and decorates the chrome with pseudo-elements
(`pointer-events: none`); `mission-control` and `synthwave` are always dark. No skin sets the `--stage-*` tokens or styles `.diagram`. Chat bubble rules are
scoped to `.messages .msg` because the sequence SVG uses a `.msg` class. With
`prefers-reduced-motion: reduce` no skin other than `classic` animates.

### Splitter

`setChatWidth(px)` clamps to `[280, layoutWidth − 320 − 6]`, writes `--chat-width` on `.layout`
and persists the value in `localStorage["arcviz.chatWidth"]`. Pointer events with pointer
capture drive dragging; ++arrow-left++/++arrow-right++ move by 24 px (96 px with ++shift++),
++home++/++end++ jump to the limits, double-click removes the override and the stored value.
On `resize` the stored width is re-clamped. `updateSplitterAria()` keeps `aria-valuemin` (280),
`aria-valuemax` (layout width minus canvas minimum) and `aria-valuenow` (current chat width)
consistent after every change, reset and resize.

## Internationalization

Spec: [Internationalization](../architecture/specs/internationalization.md), ADR-0199. `i18n.js` (pure) holds the language
and the English catalog: `t(germanText, params)` returns the entry for `en` and the German text itself otherwise (the
German source text is the key; `{name}` placeholders). `N_(text)` marks a text in a module-level table, which is passed
to `t()` when shown (modules are evaluated before the language is known, so `t()` never runs at the top level).
`initI18n()` (first thing `app.js` awaits) reads `localStorage["arcviz.lang"]`, else the first `de`/`en` of
`navigator.languages`, else German; for English it loads `static/locales/index.json` and every fragment
`static/locales/en/<area>.json`, sets `<html lang>` and `applyDom`s the static markup (`data-i18n`, `data-i18n-title`,
`data-i18n-placeholder`, `data-i18n-aria-label`; the German text is the key). `api.js` sends `Accept-Language` with every
request. The language select in the settings dialog stores the choice and reloads the page. `tests/js/i18n.test.mjs`
extracts every `t("…")`/`N_("…")` literal and every marked HTML text and requires an English entry for each (and no unused
entries, matching placeholders).

## State

`state.js`:

```js
state = { diagramId, view, type, renderSeq, previewVersion, busy, fitPending,
          compare,      // {base, target} while a comparison is shown, else null
          focus,        // node id the canvas is focused on, null = whole diagram
          nodes, edges, // current nodes by id and edges (view selector, breadcrumb, clicks, inspector)
          mermaidIds,   // node id -> Mermaid id from the server
          config,       // GET /api/config
          versions,     // version list of the current diagram
          inspecting,   // node id shown in the inspector, or null
          proposal,     // pending proposal of a propose-mode turn, or null
          undoTarget, redoTarget,
          projectId,    // the open project (navigator)
          chatScope,    // "diagram" | "project": whose transcript the chat pane shows
          revision,     // the shown diagram's latest revision (ETag), sent as If-Match
          lastChange,   // {revision, author_name} of the last change seen on the live stream
          liveSession } // this tab's live session id (sessionStorage "arcviz.live")
view = { scale, x, y, fitted }   // pan & zoom transform of #viewport
```

`localStorage["arcviz.diagram"]` remembers the last opened diagram, `arcviz.project` the project,
`arcviz.navigator` (`"0"`) a hidden navigator, `arcviz.collapsed.<project>` the collapsed sections, `localStorage["arcviz.chatWidth"]`
the splitter position, `localStorage["arcviz.propose"]` the **Vorschau** checkbox, `arcviz.chatScope` the
chat scope.

## Boot

1. `GET /api/config` → provider/model label in the header.
2. `loadDiagramList()` (navigator) → `GET /api/projects` (own and shared projects); for the admin of a fresh
   installation (`offerDemo`: their own empty default project is all they have, not offered before in this browser,
   `localStorage` `arcviz.demo-offered`) it creates the demo diagram in that project (`POST /api/diagrams` without a
   project, `id: "demo", template: "demo"`); opens the remembered or first project (`GET /api/projects/{id}`)
   and in it the remembered or first diagram, or the empty-project state.
3. `openDiagram(id)` → `GET /api/diagrams/{id}`, renders diagram, versions and transcript.

## Project navigator

Design: [Project navigator](../architecture/specs/project-navigator.md), ADR-0055. Tests:
`tests/js/projects.test.mjs`, the navigator step of the e2e test.

- **Project selector** `#project-select` (`projectOptionLabel`: title and diagram count) switches with
  `openProject`. **+** (`#btn-project-new`) asks for a title and `POST /api/projects`; the menu
  `#project-menu` renames (`PATCH {title}`), describes (`PATCH {description}`, shown in
  `#project-description`) and deletes (`DELETE` with `If-Match` of the shown `view.revision`, button disabled while
  the project has diagrams; then the remaining remembered or first project opens; see *Conditional deletes*).
- **Tree** `#nav-tree` (`role="tree"`): `treeRows(view.tree, view.diagrams, collapsed)` rendered as
  `<li role="treeitem" aria-level>` rows indented by `--depth`; sections (`data-section-id`,
  `aria-expanded`, a toggle, an add button, a menu) before diagrams (`data-diagram-id`, button
  `.nav-open`, a menu; the open one has `aria-current="true"`). Titles are set with `textContent`. A redraw (often a
live event) keeps the row menu that is open open (#216 review), so a menu never closes under the pointer. A move into
another project shows what came along (`sharedmodel.carriedText(view.carried)`) in the status line.
  Row actions (delegated click on `[data-action]`): `open`, `toggle` (stored per project),
  `add-diagram`, `add-section` (`POST …/sections {title, parent}`), `rename-section` (`PATCH {title}`),
  `section-up`/`section-down` (`PATCH {position}`), `delete-section` (confirm, `DELETE`),
  `rename-diagram` (`POST /api/diagrams/{id}/ops rename_diagram`, then the view is reloaded),
  `move-diagram` (`#move-dialog`: project and section selects; the section list follows the chosen
  project; moving the open diagram to another project follows it there), `diagram-up`/`diagram-down`
  (`POST …/diagrams/{id}/move {position}`), `delete-diagram` (confirm, `DELETE /api/diagrams/{id}` with `If-Match`,
  see *Conditional deletes*).
  Up/down are disabled at the ends (`siblingMove`). Errors are shown with `alert()`.
- **Names** are asked for in `#name-dialog` (`askName`: heading, label, value; resolves to the trimmed
  text or `null` on cancel).
- **Neu** (`#btn-new`, `#btn-empty-new`, a section's add button) opens `#new-dialog` with the type,
  template and section (`sectionChoices`) and sends `project: currentProject()`. **Löschen**
  (`#btn-delete`) deletes the open diagram and reloads the list. The JSON import sends the open
  project too.
- **Conditional deletes** (#186, ADR-0123): `confirmedDelete` (pure, `conflicts.js`) asks, then sends `DELETE` with
  `If-Match` of the revision the question was about: the shown diagram's `state.revision`, for another row the
  `revision` of a `GET /api/diagrams/{id}` read before asking, for the project `nav.view.revision`. A 412 reloads
  (the shown diagram or the project view is shown again), names who changed it (`changedBy`: the live stream's last
  change for that revision, else the newest version's author, else *jemand anderem*; projects: *jemand anderem*) and
  asks again (*… wurde inzwischen von X geändert. Trotzdem …löschen?*) with the new revision; a project that has
  diagrams after the reload is not asked about again. Other errors are shown with `alert()`. e2e step 14b.
- **Empty project**: `#empty-project` is shown, `.layout` and the `.tools` groups of the top bar are
  `inert`, `state.diagramId` is `null`, the canvas is cleared and the diagram transcript is cleared. In the
  project-chat scope (`applyEmpty`, also called by `setChatScope`) the placeholder is hidden and only
  `.canvas` is inert, so the project chat can create the first diagram.
- The project menu also has **Exportieren** (`GET …/export` through `apiFetch`, saved under the
  name from `Content-Disposition`) and **Projekt importieren…** (`#import-project-file`,
  `POST /api/projects/import`, then the new project opens).
- `#btn-navigator` (`aria-expanded`) shows or hides the column. `setBusy` makes the navigator
  `inert` during a turn or a pending proposal.

## Shared project model

Design: [Shared project model](../architecture/specs/shared-project-model.md). Tests:
`tests/js/sharedmodel.test.mjs`, step 14e of the e2e test.

- **Modell** (`#btn-model`, `modelpanel.js`) opens `#model-dialog` with `GET /api/projects/{pid}/model`:
  one row per element (`li.model-row[data-element-id]`: name, kind select with `kindLabel`, parent select,
  description, **Speichern** = `PATCH …/elements/{eid}` with `elementChanges`, **Löschen** = confirm and
  `DELETE`, the usages from `usageText`). `#model-new` creates an element (`POST …/elements`), `#model-sync`
  runs `POST …/model/sync`. `#model-status` shows the outcome (`propagationText`). After a rename, delete or
  sync that changed diagrams the shown diagram is reloaded and the tree refreshed. Every text is set with
  `textContent`. The dialog closes when another project is shown.
- **Inspector** section `#insp-model` (node and lifeline mode): shown when the inspected element's kind is in
  the type's `linkable` table of `/api/config` (`linkableKinds`). It loads the elements (`GET …/elements`, the
project document only), then shows the
  linked element (name, kind, description, **Verknüpfung lösen** = `POST /api/diagrams/{id}/unlink`), or a
  select of compatible elements (`linkChoices`) with **Verknüpfen** (`POST …/link`) and a kind select
  (`defaultKind`) with **Als Projektelement anlegen** (`POST …/promote`). Saving a new name of a linked node
  or lifeline renames the element (`PATCH …/elements/{ref}`) instead of the view element.
- The project review labels a finding's `model_element` with `modelElementLabel` (`findings.js`) from the
  answer's `model` index.

**Relations** (design: [Model relations](../architecture/specs/model-relations.md); tests
`tests/js/relations.test.mjs`, step 14f of the e2e test):

- The **Modell** dialog lists the relations (`#relation-list`, `li.relation-row[data-relation-id]`: source, kind
  (`relationKindLabel`), target, one input per property the kind allows (`allowedProperties`, `.relation-prop
  [data-property]`; choices for `direction` and `mode`), description, **Speichern** = `PATCH …/relations/{rid}` with
  `relationChanges` (properties the new kind does not allow are added to `remove_properties`), **Löschen** = confirm
  and `DELETE`, `.relation-usage` from `relationUsageText`). `#relation-new` (shown with two or more elements) creates
  one (`POST …/relations`). **Matrix** (`#model-matrix`, `aria-pressed`) shows `#relation-matrix`: `matrixRows(model)`,
  rows = relations, columns = deployment, sequence and component diagrams, cells `td.cell-<state>` (`matrixCell`: linked ●,
  shown ○, hidden –, missing !, `MATRIX_LABELS`; the states come from the server's `usages`/`shown`, which follow
  `project_model.shown_views`, so a node nested under its host counts as shown); a cell with a view is a button that closes the dialog, opens
  the diagram (`openInProject`) and marks the connection with `.relation-hit` on every render of that diagram until
  another one is shown (edges through `edgeSvgKeys`, messages by `data-id`).
- Inspector **Projektbeziehung**: in the details row of every edge of a deployment node (`.edge-relation`) and as
  `#insp-relation` for a message: the linked relation (`relationText`) with **Verknüpfung lösen** (`POST
  …/unlink-relation`), or a select of the relations that fit the connection's ends (`relationChoices`, from `GET
  …/elements`, one request per inspector render for all edge rows) with **Verknüpfen** (`POST …/link-relation`); both
  through `linkAction` (`If-Match`). The "not drawn" list of the **Projektelement** section uses `showsRelation`.
- Completeness (`#completeness-menu` in the canvas toolbar, hidden for types without `completeness` and outside
  projects): one checkbox per kind, a change runs `set_completeness` through `runOps`; the summary is
  `completenessText`. The **Projektelement** section of a linked node or lifeline lists `relationsNotDrawn` with the
  checkbox **hier bewusst ausgeblendet** (`POST …/hidden-relations`). The relation lists, the new-relation form and
  the markers are edit zones: viewers see them read-only (`body.readonly`).

## Chat turn

`sendMessage(text)` (`chat.js`) posts to `/chat` and reads the response body with `readSse`
(`sse.js`), which splits on blank lines, parses `data:` lines as JSON and awaits the handler per event:

| Event | UI effect |
|---|---|
| `text_delta` | Appends the piece to the running round of the reply (`createReplyBuffer().delta`, `reply.js`) and shows the result in the pending bubble. |
| `text` | Replaces the streamed text of the round with the round's full text (`round`); rounds are joined with a blank line. |
| `tool_call` | Inserts a monospace tool line (`name(k=v, …)`) before the pending bubble; status "Wende an: name …". |
| `tool_result` | Marks the tool line ✓ or ✗ with the error message; re-renders the diagram from the event's render payload (`viewOf(ev)`). |
| `error` | Turns the pending bubble into a red error bubble. |
| `final` | Sets the reply text and meta line (operation count, version, tokens via `tokenMeta(ev.usage)`), reloads the detail (versions, stats) while keeping the highlighted Mermaid, refreshes version counts in the selector. |

While busy, the input and all header buttons are disabled. If the stream ends without a `final`
event and no `text`/`text_delta` event has arrived either (the bubble still shows `…`), the bubble says
"Verbindung endete ohne Antwort." A stream that delivered some text and then closed without
`final` leaves that partial text styled as a normal reply; reloading the page shows the persisted
transcript entry, which carries the error note.

### Propose mode

The **Vorschau** checkbox (`#propose-mode`) sends `mode: "propose"`. A `final` event with a `proposal`
keeps the proposed render on the canvas (changed elements highlighted), shows the banner *Vorschlag –
noch nicht gespeichert*, and `showProposal()` appends to the reply bubble a `ul.diff` built by
`describeDiff(diff)` (`proposal.js`: `+`/`−`/`~` lines, German field names) and the buttons
**Übernehmen** (`#btn-proposal-apply`) and **Verwerfen** (`#btn-proposal-discard`). `state.proposal`
keeps the UI locked (`setBusy(true)`, status *Vorschlag prüfen …*) until one is clicked. **Übernehmen**
posts the proposal with the first line of the instruction as `label` to `/proposal/apply`; on success
the bubble says *Übernommen als Version vN* and the detail is shown; on an error (409) a red bubble
*Vorschlag nicht übernommen: …* appears and the current state is reloaded. **Verwerfen** posts
`/proposal/discard` and shows the unchanged diagram. In a reloaded transcript a proposed turn's meta
line says *Vorschlag*.

**Chat leeren** (header) asks for confirmation, calls `DELETE /api/diagrams/{id}/chat` (in the project
scope `DELETE /api/projects/{pid}/chat`) and re-renders the empty transcript; versions are untouched.

### Project chat

Design: [Project chat](../architecture/specs/project-chat.md), ADR-0061. Tests:
`tests/js/projectchat.test.mjs`, the project-chat step of the e2e test.

- `#btn-project-chat` in the navigator's action row (`aria-pressed`) toggles `state.chatScope` via
  `setChatScope`; `#chat-scope` above the messages shows *Projekt-Chat · <project>* and
  **Zum Diagramm-Chat** (`#btn-chat-scope-back`). The project scope loads `GET /api/projects/{pid}/chat`;
  `openDiagram` leaves the transcript alone in that scope; the navigator hook `onProjectShown` reloads
  it after a project switch. The scope is remembered.
- `sendMessage` in the project scope posts `{message, active: state.diagramId, focus, mode}` to
  `/api/projects/{pid}/chat`. Tool lines of the project tools use `describeProjectCall`. A successful
  `tool_result` whose `diagram_id` is not the shown diagram first opens it (`openInProject`), then
  renders the event's payload. On `final` the changed diagrams appear below the reply as buttons
  (`.changed-diagrams`, `changedDiagramItems`) that open them, the active (or proposal's) diagram is
  opened, `markChanged` marks the other changed diagrams in the tree (`li.changed` with a *geändert*
  badge until opened) and the project view is reloaded; the meta line leaves out the version (the list
  names them per diagram).
- A proposal of a project turn is confirmed or discarded through `POST /api/projects/{pid}/proposal/apply`
  (with `diagram`) and `…/proposal/discard`.

## Versions UI

Every version option names its author when known (`versionOptionLabel`: `(auto, <time>, Ada Lovelace)`; the tooltip
`versionTitle`: `<label> – von <author>`), and a reloaded transcript shows the author of each user message written by someone else in its
meta line (`authorName`); versions and messages from before M20 show none ([personal workspaces](../architecture/specs/personal-workspaces.md)).

- Selector lists versions newest first: `v<n> · <label> (<source>, <time>)`. A response carries only the newest page
  (`versions_total`, `versions_more`, #114); then the last entry is *Ältere Versionen laden … (n)*, which fetches
  `GET /versions?before=<oldest loaded>` and prepends the page (`state.versions`, `state.versionsTotal`); a failed
  request puts the list back on the current state and shows the error (the entry stays), an empty page removes the
  entry (pruned meanwhile); e2e step 15c.
- Changing the selection previews via `GET /versions/{n}` and shows the banner; the current
  state is restored by any subsequent `showDiagram`.
- **Wiederherstellen** → `POST /versions/{n}/restore`; **Undo** → `POST /undo` (button disabled
  when `undo_target` is null); **Redo** → `POST /redo` (disabled when `redo_target` is null; both
  targets are kept in `state` so `setBusy(false)` restores the right disabled state);
  **Version sichern** → `prompt()` for a label, then `POST /versions`.
- Shortcuts, ignored while the focus is in an `input`, `textarea`, `select` or contenteditable
  element and while a dialog is open: ++ctrl+z++ / ++cmd+z++ clicks Undo, ++ctrl+shift+z++ /
  ++cmd+shift+z++ and ++ctrl+y++ click Redo (only when the button is enabled).

### Version comparison

**Vergleichen** opens `#compare-dialog` with *Von* (versions) and *Bis* (*Aktueller Stand* = empty
value, plus versions), defaulting to the version picked in the selector (the previous version if
that is the newest) against the current state. `showCompare(base, target)` fetches
`GET /compare?base=&target=&focus=`, sets `state.compare = {base, target}`, ends a preview, fills
`#compare-banner` (summary counts, title/direction changes, legend) and renders with a fit.
While `state.compare` is set, `handleDiagramClick` ignores clicks, `exportSource()` exports the
shown comparison, `exportName()` appends `v<base>-vs-v<target>|aktuell`, and the model downloads
export the *Bis* state. `endCompare()` hides the banner and runs in every `showDiagram`, when a
version is previewed and when a chat message is sent; **Beenden** calls `setFocus(state.focus)`.

### Layout direction

`#direction-select` is filled from `config.directions` and set to `diagram.direction` by every
`showDiagram`. A change runs `set_direction` through `runOps` (so it is versioned and ends a
preview), then re-renders with a fit; a failure is shown in the status line. Disabled while busy.

## Rendering

`renderView({renderer, source}, {fit})` (the view comes from `types.viewOf(payload)`, see
[Diagram types](diagram-types.md#frontend)) increments `renderSeq` and keeps the view in `state.view`.
Mermaid source is rendered with a unique id and stale results are discarded; Mermaid's SVG is parsed by
`parseMermaidSvg` with the HTML parser into an inert `<template>` (its labels are HTML, not well-formed XML), cleaned by
`svgsafe.sanitizeSvg(root, {htmlLabels: true})` and imported (#197, ADR-0132; the SVG export renders through the same
path, `renderOffscreen`); an SVG document (renderer `svg`) is parsed with `DOMParser` by `parseSvg`, cleaned by the
allow-list sanitizer `svgsafe.sanitizeSvg` as a second line of defence (the server escapes all text; the SVG export
runs it too), and imported into the page. Either way the SVG is placed in `#viewport` and given
its natural size from the `viewBox`. `#diagram` carries `aria-busy="true"` from the start of a render until the newest
one is in the page (or has failed); a superseded render leaves it set. The e2e test waits on it instead of a fixed time
(#216). A render failure replaces the view with `placeholder("Rendering fehlgeschlagen:
…")`, a failed start the diagram area with `placeholder("Start fehlgeschlagen: …")`: the message (which may quote
diagram source or server text) is `textContent`, never markup.

**Output encoding** ([hardening](../architecture/specs/hardening.md), ADR-0125): server- or user-derived strings reach
the DOM only through `textContent` and DOM APIs. `innerHTML` is assigned only `""`, constant literals (the whole right-hand side one literal: `"" + text` is not), or, once,
Mermaid's output into an inert `<template>` in `canvas.parseMermaidSvg` (marked `// inert-html:`, sanitized before it
is adopted, ADR-0132); no `// trusted-html:` sink is left; `insertAdjacentHTML`, `outerHTML`,
`document.write` and `DOMParser` with `text/html` are not used. `tests/js/sinks.test.mjs` enforces this.

### Focus and drill-down

`state.focus` is appended as `?focus=` to every request that returns a render (`focusQuery()`),
sent as `focus` in the chat body, and taken back from the responses (`detail.focus`,
`ev.focus`) so a focus that disappeared server-side resets the UI. `showFocus(detail)` renders
the breadcrumb (buttons per level, current level as text) and the **Ansicht** selector (every
node of a container kind per `container_kinds` from `/api/config`, empty ones included, indented
by depth). `setFocus(id)` reloads the detail with the new focus and fits the view; a sequence
counter drops responses of superseded focus requests. Switching diagrams clears the focus.

An answer requested before the user changed the focus may arrive after it (a live refresh, a write's answer, the
events of a chat turn started with the old focus): it must not take the view back (#216). `diagram.isStaleView(answer)`
applies the pure rule `model.staleFocus(answer.focus, state.focus, answer.diagram.nodes, features.focus)`: the server
renders a focus only when asked for it and falls back to the whole diagram only when the asked container is not in the
diagram or the type cannot focus (`check_focus`), so an answer for another focus is stale unless the current one cannot
be shown in the answer's diagram. `showDiagram` takes only the revision from a stale detail of the open diagram and
refetches the current focus (through the same sequence counter, keeping `remote`; a superseded `setFocus` passes its
`fit` on to the request that superseded it, and the newest request clears it however it ends; a failed focus change
the user made is reported in the status line, a failed background refetch stays silent); the chat stream skips a stale `tool_result` render, keeps the current
focus at `final` and drops the final event's highlight when it is stale, so the refetch shows the current focus.

Clicks in the SVG: the pan handler records the pressed element (pointer capture retargets later
events to `#diagram`) and treats a release without movement (≤ 4 px) as a click.
`nodeIdOf(target)` walks up to `g.cluster`/`g.node`, strips Mermaid's render prefix
(`arcviz-<seq>-`) and node wrapper (`flowchart-<id>-<n>`) and maps the Mermaid id back through
`mermaidIds`. A cluster click focuses that container; a stub click focuses the stub's parent (or
the whole diagram). ++alt+arrow-up++ and the **↑ Ebene höher** button go one level up.

### Element inspector

`#inspector` is an `<aside>` beside `#diagram` inside `.stage`. `openInspector(id)` fills it from
`state.nodes`/`state.edges` and `state.config` (kinds from `/api/config`): name, kind select,
parent select (containers from `containerOptions()` minus the element and its descendants),
property rows (the node and edge kind selects also list the element's current kind when it is no
longer configured, so saving never replaces it silently), edge rows (label input, kind select, a details toggle `⋯` that shows the explicit
direction glyph and the number of properties, remove button; the collapsible details row holds the
direction select `Standard der Art / → / ↔ / —` and a key/value table like the node's with
**Übernehmen**), cascade checkbox for
containers, delete button. Leaf clicks open it (`handleDiagramClick`); the **Bearbeiten** button
opens it for the focused container; ++esc++, the close button and switching diagrams close it.

Sequence diagrams (renderer `svg`): a click maps through the nearest `[data-id]` ancestor to an
interaction element and `openElementInspector(id)` switches the panel to `data-mode="element"` (CSS hides
`.node-only` and shows `.element-only`): the fields come from `elements.elementFields(kind, element,
ctx)` (lifeline: name, type, actor; message: name, sort, sender, receiver with an empty choice for
found/lost; combined fragment: operator, covered lifelines as a multi-select, messages for
ignore/consider; operand: guard; interaction use: refers_to, covered; execution: read-only span),
**Speichern** sends `elementUpdate()` (only changed fields) and **… löschen** `elementRemove()`
(`remove_lifeline` or `remove_fragment`), both through `runOps`. Errors appear in `#insp-element-error`.

`runOps(ops, {refresh})` posts the operations one by one to `/ops?focus=…`, re-renders after each
response (`showDiagram` with the highlighted Mermaid), stops at the first error and shows its
message in `#insp-error`, then reopens the inspector from the fresh state with the error kept
(`openInspector(id, {keepError: true})`), or closes it if the element is gone. Edge commits pass
`refresh: "edges"` and only rebuild the edge table (`renderInspectorEdges`), so unsaved node
fields survive. Edge rows are built with DOM APIs, never `innerHTML`, because ids are free-form
strings. While an operation or a chat turn runs, `setBusy` disables the composer, the header
buttons and selectors, the view selector and the inspector controls. Saving builds `update_node` (name/kind/description/properties/remove_properties, only changed fields)
and `move_node` when the parent changed, sent as one `apply_batch` when there are both (one write, one `If-Match`); edge edits commit on `change` (label, kind,
direction) or on **Übernehmen** (`update_edge` with `properties` + `remove_properties`, computed
by `propChanges()` like the node's); delete asks for
confirmation and sends `remove_node` with `cascade` for containers.

### Concurrent edits in the inspector

`openInspector`/`openElementInspector` end with `startEditing(id)`: the **base** is `{id, mode, revision:
state.revision, values}` with the element's field values (`conflicts.nodeValues`, or the element's
`elementFields` values; properties as one canonical JSON value), and `notifySelection(id)`. A `showDiagram` with
`remote` (the live stream) runs `remoteChange(base.values, theirs, formValues())`: untouched fields take the new
values, touched ones that now differ keep the typed value and get a `.remote-note` "Inzwischen von X geändert: „…“";
without such a conflict the base moves to the new revision and values. Saving a form captures `{id, revision}` of its base on Save and calls `runOps(ops, {revision, form: {element, id}})`: the first operation carries `If-Match` with the base revision, each next one the revision of
the previous answer. `runOps` goes through `writequeue.createWriteQueue`: this tab's operations run one after another (an edge input also
commits on blur), each bound to the diagram it was queued for (dropped with a status message when another diagram
is shown by its turn) and with its `If-Match` revision resolved when it starts: the shown revision, or for a form save the captured
revision advanced along this tab's own write steps (`writes.advanced(diagram, from, to)` after every answer), never
another element's base or the shown revision. `runOps` resolves to `null` when nothing may show any more (dropped,
or another diagram opened meanwhile: errors go to the status line only); a 412 of a form whose element is no longer
the inspected one is reported in the status line, and an own write while another element is open only advances that
element's base. After an own write that leaves the form open (`refresh: "edges"`, a rename through
the model) `advanceBase()` moves the base on, so the tab never conflicts with itself. A remote value for a `<select>`
without that option gets one, and an element save sends only the fields changed against the base (`touchedArgs`).
A 412 reloads the detail (if the reload fails, the inspector shows the error and keeps the form); for a form save
`showConflict(form)` fills `#insp-conflict` with a
table *Feld / Ihr Wert / Aktuell* of `fieldConflicts(base, theirs, mine)` (fields both sides changed) and the buttons
**Meine Werte speichern** (rebase onto the current state and submit again) and **Aktuelle Werte übernehmen** (reopen
from the current state); without conflicting fields it rebases and saves at once. Edge rows, the direction selector
and link/unlink send the shown revision; their 412 reloads and asks to try again.

### Voice input

`speech.js` wraps `SpeechRecognition`/`webkitSpeechRecognition`: `speechSupported()` (the
constructor exists **and** `window.isSecureContext` is not `false`; Chrome and Edge expose the
constructor on insecure origins where starting it always fails) and
`createSpeechInput({onInterim, onFinal, onState, onError})` (`null` when unsupported) returning
`configure({lang, continuous})`, `begin()`, `stop()`, `toggle()`, `abort()`. A recogniser instance is built per
session (`interimResults: true`, `maxAlternatives: 1`); `onresult` splits results from
`resultIndex` into final segments (`onFinal` each) and the current interim string (`onInterim`);
`onend` restarts while continuous listening is wanted, otherwise reports `idle`; `no-speech` is
swallowed in continuous mode and `aborted` ignored; the terminal errors `not-allowed`,
`service-not-allowed`, `audio-capture`, `network` and `language-not-supported` turn listening off
(so continuous mode cannot loop on a missing microphone or network) and, like all other errors, are
reported with a German message. `tests/js/speech.test.mjs` pins the restart/stop behaviour with a
fake recogniser.

`voice.js` wires the composer controls: push-to-talk holds `begin()` under pointer capture (and
++space++/++enter++ on the focused button), continuous mode toggles on click; interim text is
shown in `#speech-preview`, finals are appended to the textarea by `appendToInput()` (with a
separating space, caret at the end, an `input` event dispatched); the button carries `listening`/
`starting` classes and `aria-pressed`. Language and mode persist in
`localStorage["arcviz.speech.lang"|"arcviz.speech.continuous"]`. Submitting the form stops a
push-to-talk recording. `setBusy` leaves the microphone enabled so the next instruction can be
dictated while a turn runs. Without API support `speech` is `null`: the microphone, checkbox and language
selector are disabled with the reason as `title` (insecure context vs. unsupported browser,
decided by `window.isSecureContext`), the state label reads *nicht verfügbar*, and a one-time
`system` notice (`#speech-unavailable-notice`, remembered in
`localStorage["arcviz.speech.noticeShown"]`) is rendered above the transcript; `showChat()`
preserves that element when it re-renders the messages.

### Speech output

`synth` is `window.speechSynthesis` when both it and `SpeechSynthesisUtterance` exist, else `null`
(the **Vorlesen** label stays hidden). `speakReply(text)` runs on the `final` event when the box is
ticked: cancels any ongoing utterance, builds a `SpeechSynthesisUtterance` (first 1000 characters,
`lang` = recognition language, a matching voice if `getVoices()` has one) and calls `speak()`.
`stopSpeaking()` (`synth.cancel()`) runs on microphone press (pointer and keyboard), form submit,
diagram switch and when the box is unticked. The setting persists in
`localStorage["arcviz.speech.speak"]`, default off.

### Voice commands

`commands.js` is a pure module (unit-tested with `node --test tests/js`): `normalize()` folds
case, punctuation and umlauts; `matchCommand(text, {nodes, containerKinds})` strips fillers,
matches the catalogue `COMMANDS` by whole phrase (zoom, fit, focus_all, focus_up, undo, redo, snapshot,
next/prev diagram, toggle_source, send, clear_input, help) or by the `focus` pattern, whose target
is resolved with `resolveTarget()` (exact > id-with-dashes > prefix > substring > word overlap,
containers only); an unresolved target yields `{id: "focus", error}`. `commandHelp()` feeds the
**?** popover. In `voice.js`, every final speech segment goes through `matchCommand` first;
`runCommand()` dispatches to the existing UI functions (`zoomCenter`, `fitView`, `setFocus`,
the Undo/Redo buttons, a snapshot POST, diagram selector, source toggle, form submit) and echoes a
`system` chat line that is not persisted. Non-commands are appended to the input box.

### Export

The **Export** menu is a `<details>` element that closes after use and on outside clicks.
**JSON herunterladen** and **PlantUML herunterladen** call `downloadModel(format)`, which fetches
`GET /export?format=…` (with `&version=` while a version is previewed) and downloads the body under
the file name from `Content-Disposition`; they always export the whole model, not the focused view.
The remaining entries render the current view:
`exportView()` returns `state.view` when *Hervorhebung behalten* is ticked, otherwise a clean
view from `GET /render?focus=` (or `GET /versions/{n}?focus=` while previewing). An SVG-rendered view
is exported as it is (`standaloneSvg()` adds the XML declaration and explicit size); a Mermaid view `exportSvg()`
renders it off-screen with `mermaid.render`, prefixed with an init directive
setting `htmlLabels: false` at the top level (edge labels) and under `flowchart` (node labels)
(plain `<text>` labels: a `foreignObject` would taint the PNG canvas
and is unsupported by most vector tools), parses the result, sets explicit `width`/`height`
from the `viewBox`, drops the inline `style` and adds the SVG namespaces, and returns a standalone
document with an XML prolog. SVG is downloaded as a Blob; PNG is produced by drawing the SVG (as a
Blob URL image) onto a canvas at 2× on the current `body` background colour; the Mermaid source is
written with `navigator.clipboard.writeText`, falling back to a hidden textarea plus
`document.execCommand("copy")` when the Clipboard API is unavailable (plain HTTP on a non-loopback
host is not a secure context). File names, `<diagramId>[-<focus>][-v<previewVersion>].<ext>`, are
computed before the first `await` so a view change during rendering cannot mislabel the file.
Errors are shown in the composer status line.

### Pan and zoom

`#diagram` is an `overflow: hidden` viewport; `#viewport` carries
`translate(x, y) scale(s)` from `view`. `fitView()` scales the SVG into the container with 24 px
padding and centres it (the fit scale may go below the interactive minimum; only the maximum of
4 applies); it runs when `fitPending` is set (opening a diagram, `fit: true`) and marks
`view.fitted`. A `ResizeObserver` on `#diagram` refits while `fitted` is set, so window resizes and
splitter drags keep a fitted diagram fitted; zooming, panning and 100 % clear the flag.
Re-renders during a chat turn, undo, restore and theme changes keep the current `view`.
`zoomAt(factor, cx, cy)` keeps the diagram point under the cursor fixed (limits 0.1–4); the
wheel handler normalises `deltaMode` (lines ×16, pages × canvas height) and maps the delta to
`exp(-delta·k)` with a larger `k` for `ctrlKey` (trackpad pinch). Dragging uses pointer capture on `#diagram`. Toolbar: −/+ (×1.2
around the centre), 100 % (`resetView`, scale 1 at 24/24), Einpassen (`fitView`); double-click
fits.

### Architecture review

**Review** (`#btn-review`, canvas toolbar) toggles the overlay `aside#review` on the left of the stage
(`review.js`). Opening it runs `GET /checks`; **Regeln prüfen** (`#review-run-checks`) runs it again,
**KI-Review** (`#review-run-llm`, label *KI prüft …* while running) posts `/review`; while that request
runs the button carries `data-running="true"`, which `setBusy` respects, so only one review runs at a time. The list
`#review-list` shows `groupFindings(mergeFindings(rules, llm))` as `h4` per category (*Risiken*,
*Verbesserungen*, *Stärken*, with counts) and `li.finding.sev-<severity>` items (`data-finding` = id)
with the severity, *KI* for LLM findings, the element names (`elementNames`) and the message.
`#review-summary` holds the model's summary, `#review-status` `reviewMeta(...)` or an error.

Clicking a finding (or ++enter++ / ++space++ on it) selects it, again deselects. The selected
finding's nodes get the class `review-hit` on their `g.node` / `g.cluster` (mapped with
`nodeIdForSvgId`), its edges on the `path` whose id or `data-id` ends with one of
`edgeSvgKeys(edges, state.edges, state.mermaidIds, visible)`; the CSS draws a glow on nodes (Mermaid's
inline `!important` classDef styles cannot be overridden) and a magenta stroke on edges. The
highlight is re-applied through `onRendered` and skipped while a comparison or a version preview is
shown; closing the panel removes it.

`onDiagramShown` stores the shown diagram; while the panel is open and `diagramKey` differs from the
one the findings belong to, the rule checks run again and LLM findings stay, marked `.stale`, with
`#review-stale` (*Diagramm geändert – KI-Review erneut ausführen*). An LLM answer for a diagram that
changed meanwhile is shown as stale as well. `onDiagramSwitched` clears everything; answers for
another diagram or superseded requests are ignored. `setBusy` disables the panel's action buttons.

**Project mode** (design: [Project review](../architecture/specs/project-review.md)). **Projekt-Review**
(`#btn-project-review`, navigator action row, `aria-pressed`) opens the same panel for the open project
(`#review-title` *Projekt-Review · <project>*): the buttons call `GET /api/projects/{pid}/checks` and
`POST /api/projects/{pid}/review`; each finding gets a chip `.target` with `findingTarget(finding,
diagrams, sections)` (*Diagramm: …*, *Diagramme: …*, *Abschnitt: …* or *Projekt*). Selecting a finding of
another diagram opens it with `navigator.openInProject`; the highlight is applied once it is rendered and
only to findings of the shown diagram (`highlightable`). **Als Vorschlag umsetzen** appears for findings
with a diagram; it opens that diagram first. Project findings survive diagram switches; a shown diagram
whose content differs from when it was last shown (`diagramKey` per diagram id) counts as an edit: the rule
checks run again and the AI findings are marked `.stale` (also when the edit happened while the AI review
ran);
`onProjectShown` closes and clears the project mode; switching to the diagram **Review** clears it too.
Correspondence findings with `resolutions` ([model relations](../architecture/specs/model-relations.md)) get one
button `.finding-resolution` (`data-action`, also `.finding-apply`, so hidden for viewers) per resolution:
`propose` opens the diagram and calls `onApply(prompt)`; the others send `relations.resolutionRequest(projectId,
resolution)` (`remove_relation` after `confirm`), reload the shown diagram and run the checks again. `diagramKey`
includes `complete` and `hidden_relations`, so a marker set from the inspector re-runs the project checks.

**Applying a suggestion** (design: [Applying a review suggestion](../architecture/specs/review-suggestions.md),
ADR-0026). Every risk or suggestion finding with a `suggestion` has a button `.finding-apply`
**Als Vorschlag umsetzen**. It calls `onApply(suggestionPrompt(finding, state.nodes))`, which `app.js`
wires to `sendMessage(text, { propose: true })`: an ordinary propose-mode turn (whatever the
*Vorschau* checkbox says) whose user message is

```text
Setze diesen Vorschlag aus dem Architektur-Review um: <suggestion>
Befund (Risiko|Verbesserung, <severity>): <message>
Betroffene Elemente: <name> (<id>), … · Betroffene Kanten: <edge ids>
```

(the last line only with existing elements or edges). The proposal is then confirmed or discarded
as in [Propose mode](#propose-mode); after **Übernehmen** the changed diagram re-runs the rule
checks of the open panel. The button is disabled while the UI is busy (including a pending
proposal; `setBusy` keeps the `data-stale` ones disabled) and on stale LLM findings.

## Live updates

Design: [Collaboration](../architecture/specs/collaboration.md), ADR-0095. `live.js` keeps one stream per tab:
`GET /api/diagrams/{id}/live?session=…` while a diagram is shown, else `GET /api/projects/{pid}/live?session=…`
(re-opened from `onDiagramShown`/`onProjectShown` when the target changes, the old one aborted). It reads the body
with `apiFetch` + `readSse`; 404 ends it (after a stream of it had connected, it is handled like `gone`: the diagram
was deleted or moved out of sight while the last stream ended with `bye`, #218), 429/503 wait 30 s, other failures and
`bye` reconnect (`liveBackoff`, a normal end after 0.2–1 s), `gone` ends it and reloads the navigator. `changed` with a revision other than
`state.revision` records `state.lastChange` and schedules a refresh (150 ms, merged): the detail is fetched again and
shown with `showDiagram(detail, {remote: {author_name, origin}})`; a `chat` event only redraws the transcript when
its last entry changed; `access` refreshes the detail (the role); `project` refreshes the navigator, or, when it
names another project than `state.projectId` (the open diagram was moved there), reloads the navigator on that
project with the diagram kept open (`loadDiagramList(diagramId, project)`). A refetch that fails is retried with
`liveBackoff` (the stream reports a change only once); a 404 is left to the stream's `gone`. While a chat
turn, a proposal, a version preview or a comparison is shown the refresh waits (retries every 1.5 s); a change this
tab made itself (its answer carried the revision) is not fetched again. On every `hello` (also after a reconnect) a
`revision` other than `state.revision` refreshes the diagram and a `project_revision` other than the last one seen
refreshes the navigator, so nothing changed while the stream was down is missed. The session id lives in memory
only (a duplicated tab gets its own). `onLiveEvent(fn)` passes every event on.

### Presence

Pure helpers in `presence.js` (#171): `initials(name)`, `colorOf(userId)` (one of `PRESENCE_COLORS` by a hash of the
id), `viewersToShow(snapshot, me, {project})` (one chip per other user, of the diagram, or of the project in the
project chat or when nobody else is on the diagram; `chatting` set), `selectionMarks(snapshot, me)` (one per user and
element), `chatNotice(snapshot, me)` (*Bob chattet gerade mit dem Assistenten.*). `live.js` keeps the last
`presence` snapshot and draws: the chips in `#presence` next to the title (`.presence-chip`, `--c` colour,
`.chatting` ring, title = name), the notice in `#chat-presence` above the chat input, and for every selection a
`g.presence-overlay` inside the element's SVG group (Mermaid node or cluster via `nodeIdForSvgId`, sequence elements
by `data-id`) with a dashed `rect.presence-mark` around its bounding box and a `text.presence-tag` with the initials
(paint as important inline styles, because Mermaid styles node shapes with `!important`); several people on one
element get nested outlines. Marks are redrawn on every render (`canvas.onRendered`) and hidden during a version
preview or comparison. The inspector reports the element it shows through `diagram.notifySelection` (also `null`
when it closes); `live.js` posts it to `POST /api/diagrams/{id}/presence` (250 ms debounce, again after every
`hello`). All names reach the DOM through `textContent` only.

## Testing

`node --test tests/js` unit-tests the pure modules (`geometry`, `sse`, `model`, `format`, `commands`,
`speech`, `reply`, `proposal`, `findings`), the module rules (`modules.test.mjs`) and the HTML-sink rule
(`sinks.test.mjs`) and the SVG sanitizer (`svgsafe.test.mjs`). DOM wiring is covered by
the browser smoke test only.

The Playwright smoke test boots the app with a scripted provider that replays the vnet example,
drives the chat, and asserts on the rendered SVG and the version list. It also toggles the colour
scheme (`emulateMedia`), exercises the splitter by drag, keyboard and double-click, checks pan
and zoom (fit on load, wheel, drag, toolbar, reset, double-click) by reading the viewport's
computed transform, and drills into the deployment (cluster click, stub, selector, up button); the
chat turn runs while focused so the live renders are checked to keep the focus. Finally it drives
the inspector (rename plus property on a leaf, kind change, move into the new container, delete)
and the export menu (SVG and PNG downloads via Playwright's download events, clipboard write
captured by an init script), and finally a propose-mode turn twice: the diff list, banner,
highlight and lock, then **Verwerfen** (nothing saved) and **Übernehmen** (one version), and the
architecture review on a fresh demo diagram (rule finding, KI-Review with a dropped invalid finding,
node and edge highlight, applying the database suggestion as a proposal and confirming it, the
finding resolved and the LLM findings marked outdated, closing removes the highlight). Requests to
`cdn.jsdelivr.net` are blocked and counted as failures, so the test also proves that the UI needs
no CDN. Step 15b checks that a start failure and a Mermaid parse error with markup in their message show it as
text. `CHROMIUM_PATH` selects a preinstalled browser.

## Own and shared projects

Design: [Personal workspaces and direct sharing](../architecture/specs/personal-workspaces.md) (M21). Tests:
`tests/js/access.test.mjs`, `tests/js/api.test.mjs`, the proxy-mode part of `tests/e2e/smoke.mjs` (step 17, two
users via identity headers).

- **Boot** (`app.js`): `/api/config` and `/api/me` (the header shows the user's name, `#me`, with e-mail and id as
  tooltip), then `loadDiagramList()`.
- **Project switcher** (navigator): `GET /api/projects` lists own and shared projects; `fillProjectSelect` builds
  one `<optgroup>` per non-empty group of `projectGroups` (**Meine Projekte**, then **Mit mir geteilt**), a shared
  project's label names its owner (`sharedLabel`). **+** and **Projekt importieren…** create in the own workspace
  (`POST /api/projects`, `POST /api/projects/import`, no `workspace`). Every user has an own default project
  (*Standardprojekt*); should there be no project at all, the placeholder says `EMPTY_PROJECTS_TEXT` and offers
  **Neues Projekt anlegen**.
- **Freigeben…** (project menu, `workspaces.js`): `GET /api/projects/{pid}/access`; rows from `shareRows` (the
  owner first, never editable, then the shares). The owner gets a role select per share (viewer/editor), **Entfernen** (`DELETE …/access/{user}`, after a confirmation) and the form
  `#share-add` (user id or e-mail, role) that posts `POST …/access` and shows `addedText(added, typed, role)`. Typing
  into its input asks `GET /api/users?q=` (200 ms after the last key, only when `wantsSuggestions`; an older answer is
  dropped) and fills the `<datalist id="share-add-suggestions">` with the user ids, labelled `suggestionLabel(user)`; the
  display name and what was typed (the server reveals no e-mail address). Others see the owner and their own role, read-only. Closing the dialog reloads the project list.
- When a share is revoked, the recipient's live stream ends with `gone` (see [Live updates](#live-updates)), which
  reloads the navigator without the project.
- **Read-only mode**: `state.role` comes from the project view (`role`) and every diagram detail (`access.role`);
  `setRole` reruns `setBusy`, whose `applyReadOnly` disables every `[data-edit]` control (undo/redo, versions,
  direction, new/import/delete, voice; `setBusy` recomputes them first, `versions.js` keeps undo/redo/restore
  disabled for viewers, and a control nobody recomputes carries `data-edit-restore` (the mic): `access.editLock`
  disables it and gives back exactly what read-only mode disabled when the role allows editing again, never a mic its
  feature disabled), makes the `[data-edit-zone]`s inert (the inspector's forms and project
  element section, the model list), toggles `body.readonly` (CSS hides the edit-only actions: section and diagram
  menus, **+ Abschnitt**, model and inspector buttons, **Als Vorschlag umsetzen**) and `body.not-owner` (hides the
  owner-only deletions), and disables the chat input with the hint *Nur Lesezugriff: …*. The server refuses
  anything else with 403, which `api.js` shows in German.
- Authors: see [Versions UI](#versions-ui); a reloaded transcript names the author of messages by other people.

