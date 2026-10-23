# Canvas

The canvas renders the current diagram as a Mermaid flowchart in the diagram's layout direction
(top to bottom unless changed, see below). Containers become
nested boxes, leaves get a shape and colour by kind, edges get an arrow style by kind
(see [Elements and edges](elements.md)).

## Focus and drill-down

Large diagrams are easier to read one container at a time. The second toolbar row shows a
**breadcrumb** (*Ganzes Diagramm › prod-deployment › prod-vnet*) and an **Ansicht** selector
listing every container, indented by nesting.

| Action | Effect |
|---|---|
| Click a container's title in the diagram, or pick it in **Ansicht** | Focus: only that container and its contents are drawn |
| Click an entry in the breadcrumb | Jump to that level; *Ganzes Diagramm* shows everything |
| **↑ Ebene höher** or ++alt+arrow-up++ | One level up |
| Click a stub | Jump to the container that stub lives in |

Edges that leave the focused container are kept. Their outside endpoint is drawn once as a
dashed, grey **stub** so you still see what the focused part talks to. The focus is kept while
the model works; if the focused container is removed, the view falls back to the whole diagram.
Switching diagrams resets the focus. Exports and the Mermaid source follow the focused view.

## Layout direction

The **Richtung** selector in the second toolbar row sets the direction in which the diagram is laid
out: **TB ↓** (top to bottom, the default), **BT ↑**, **LR →** (left to right) or **RL ←**. Wide
diagrams with many elements side by side usually read better left to right. The direction is part
of the diagram, not of the view: changing it is saved as a new version (and can be undone), applies
to previews, comparisons and exports, and the model can change it too (*"Zeichne das Diagramm von
links nach rechts"*). The diagram is fitted again after a change.

## Light and dark

The canvas follows the operating system's colour scheme. In dark mode Mermaid's dark theme is used
for edges, labels and defaults, while element fills and borders keep their kind-specific colours,
which are chosen to be readable on both backgrounds.

## Language

The selector **Sprache** / **Language** in **Persönliche Einstellungen** (user menu at the top right) switches the user
interface, the findings of the architecture review and the answers of the assistants between German and English; the page
reloads. The choice is stored in the browser. Until you choose, the browser's preferred language decides (German or English;
German for any other). Your own content (diagram and element names, notes, earlier chat messages) is never translated.

## Design (skins)

The selector **Design** in **Persönliche Einstellungen** (user menu at the top right) changes the look of the user interface, live and without
reloading. The diagram itself never changes with it: its canvas, colours and exports look the same in
every design, and only light or dark of the operating system affects them.

| Design | Look |
|---|---|
| **Klassisch** | The default: plain panels, follows light and dark |
| **Mission Control** | A blueprint console with scanlines, glowing text and a terminal-style chat (always dark) |
| **Synthwave** | Neon outlines, a striped sun and a moving grid floor behind the chat (always dark) |

The choice is remembered in the browser and applies to all open tabs. If the system asks for reduced
motion, the designs do not animate.

## Pan and zoom

When a diagram is opened it is fitted into the canvas. From there:

| Action | Effect |
|---|---|
| Mouse wheel or trackpad pinch | Zoom around the pointer (10 %–400 %) |
| Drag with the left button | Pan |
| **−** / **+** | Zoom out / in by 20 % around the centre |
| **100 %** | Reset to natural size, top-left |
| **Einpassen** or double-click | Fit the whole diagram into the canvas |

The view is kept while the model works, so you can stay zoomed in on one part of a large
diagram and watch it change after every operation. Switching to another diagram fits again.

## Highlighting

After a chat turn or a direct operation, the touched elements are drawn with a thick yellow border.
The highlight disappears when the diagram is next loaded (switching diagram, reloading the page,
the next turn).

## Mermaid source

**Quelltext** toggles a panel with the Mermaid source of what is currently shown. It is generated
on the server and is read-only, but you can copy it into any Mermaid-capable tool (GitHub,
Markdown viewers, the Mermaid live editor) to reuse the diagram.

Element ids are prefixed with `n_` and non-alphanumerics become `_` in the source, so `orders-db`
appears as `n_orders_db`. Labels are escaped; markup in names is shown literally.

## Export

The **Export** menu in the view row works on what you currently see: the whole diagram or the
focused container, the current state or a previewed version.

| Entry | Result |
|---|---|
| **SVG herunterladen** | Standalone SVG file, named `<diagram>[-<focus>][-v<n>].svg` |
| **PNG herunterladen (2×)** | PNG at twice the natural size, on the current theme's background |
| **Mermaid-Quelltext kopieren** | Copies the Mermaid source to the clipboard |
| **JSON herunterladen** | The whole model as `<diagram>[-v<n>].arcviz.json`, which **Importieren** reads back (see [Diagrams](diagrams.md#importing-a-diagram)) |
| **PlantUML herunterladen** | The whole model as a PlantUML diagram of its type `<diagram>[-v<n>].puml` |

The two model downloads ignore the focus (they always contain the whole diagram) but follow a
previewed version. For a deployment diagram the PlantUML file keeps elements, nesting, kinds (as stereotypes),
edges and labels; properties are not included, and `BT`/`RL` become top-to-bottom/left-to-right because
PlantUML has no reversed directions. Activity and sequence diagrams are written in PlantUML's activity and
sequence syntax (see [Activity diagrams](activity-diagrams.md) and [Sequence diagrams](sequence-diagrams.md)).
| **Hervorhebung behalten** | By default exports are re-rendered without the yellow change highlight; tick this to keep it |

Exports are rendered in the browser from the same source as the canvas, with plain SVG text
labels instead of HTML labels so that the files open in vector tools such as Inkscape or draw.io.

## Rendering problems

If the panel shows *Rendering fehlgeschlagen*, Mermaid could not be loaded or rejected the
source. See [Troubleshooting](../getting-started/troubleshooting.md).
