# Rendering

Which renderer draws a diagram depends on its type (`arcviz/render.py`, see
[Diagram types](diagram-types.md#rendering)); this page describes the Mermaid renderer.

Source: `arcviz/mermaid.py`. Tests: `tests/test_mermaid.py`. Client side: `arcviz/static/app.js`
(`renderMermaid`).

## Output

`render(diagram, highlight=(), direction=None, focus=None) -> str` produces Mermaid flowchart source.
The direction is the diagram's own `direction` (default `TB`) unless an explicit `direction` is
passed; the root and every subgraph use it:

```text
flowchart TB
  n_idp(["«external»<br/>Identity Provider"])
  subgraph n_prod_deployment["«deployment» prod-deployment"]
    direction TB
    n_api_gateway{{"«gateway»<br/>api-gateway"}}
    ...
  end
  n_api_gateway -->|"https"| n_service_a
  ...
  classDef deployment fill:#f4f6fb,stroke:#3b4f7a,...
  class n_prod_deployment deployment
  classDef changed stroke:#e0a100,stroke-width:4px
  class n_service_a changed
```

## Focus mode

`render(..., focus=<node id>)` draws only the focused node and its descendants (`visible`). The
focused node is emitted at the top level (as a subgraph if it is a container). Edges are kept when
at least one endpoint is visible; an endpoint outside `visible` is drawn once as a **stub** at the
top level: its normal shape and `«kind»` label, but class `stub` (transparent fill, dashed grey
stroke) instead of its kind class, so context is visible without being mistaken for content.
Edges with both endpoints outside are dropped. Highlights outside the subtree are ignored. An
unknown focus raises `ValueError`; callers (server, agent) validate or fall back beforehand.

`breadcrumb(diagram, focus)` returns the root-to-focus path as `[{id, name, kind}]` (empty for the
whole diagram or an unknown focus).

## Identifiers

`mermaid_id(node_id)` = `"n_" + re.sub(r"[^A-Za-z0-9_]", "_", node_id)`. The prefix avoids
Mermaid keywords such as `end`. Because the mapping is not injective (`foo-bar` and `foo_bar`
collide), `id_map(diagram)` assigns ids in sorted node order and suffixes `_2`, `_3`, … on
collision; the whole render uses that map.

## Labels and escaping

`escape_text()` replaces `#` (`#35;`), `&`, `<`, `>`, `"` and `:` with Mermaid entity codes
(`#amp;`, `#lt;`, `#gt;`, `#quot;`, `#58;`) in one pass so that user or model supplied names are shown literally even though
`htmlLabels` is on. Escaping `#` first makes the escaping injective: Mermaid decodes `#name;` and `#<number>;`
in labels, so a name that contains an entity code (`#lt;b#gt;`) is shown as typed, not as `<b>` (#102; the e2e
test checks it in Chromium). `:` is escaped because Mermaid, before decoding, drops the last `;` of a `#…;` on any
line matching `style.*:\S*#.*;` (or `classDef…`): "Lifestyle:#1" was shown as "Lifestyle:#351". With no `:` in a
label, the only colons on a line are those of Mermaid's own `key: value` syntax, always followed by a space. Control characters are normalised first as the operations do (`model.single_line`,
#103): line breaks and tabs become one space, other control characters are dropped, so no raw line break reaches the
Mermaid source even from data stored before. Labels are always quoted. In the browser Mermaid's SVG then goes through the allow-list sanitizer
like every other SVG, its HTML labels through a sub-sanitizer that keeps only `div`/`span`/`p`/`br` (#197,
ADR-0132, [frontend](frontend.md#rendering)); the escaping stays the first line of defence.

- Leaf label: `"«kind»<br/>name"` (two lines).
- Container label: `"«kind» name"` on one line, because Mermaid clips multi-line titles of nested
  subgraphs.
- Edge label: `|"text"|` when `edge_text(edge)` is not empty: the `label`, followed by
  ` (protocol:port)` from the edge properties when `protocol` and/or `port` are set
  (`orders (https:443)`, `https`, `:5432`). Other edge properties are not drawn.

## Shapes, arrows and styles

| Kind | Shape syntax |
|---|---|
| `database`, `storage` | `[( )]` cylinder |
| `queue` | `[[ ]]` subroutine |
| `gateway` | `{{ }}` hexagon |
| `external` | `([ ])` stadium |
| `function` | `> ]` asymmetric |
| `artifact` | `[/ /]` parallelogram |
| everything else | `[ ]` rectangle |

`edge_arrow(edge)` combines the line style of the edge kind (`_EDGE_LINES`) with the edge's
`direction`, or the kind's default direction when it is `None`; unknown kinds are solid/forward.
`edge_direction(edge)` returns that effective direction (`forward`, `both` or `none`) on its own; the
rule checks (`arcviz/rules.py`) use it to decide which way traffic flows along an edge.

| Edge kind | Line | Default direction | Arrow without `direction` |
|---|---|---|---|
| `connection` | solid | forward | `-->` |
| `dependency` | dotted | forward | `-.->` |
| `deploys` | thick | forward | `==>` |
| `association` | solid | none | `---` |
| `replication` | solid | both | `<-->` |

| Line \ direction | `forward` | `both` | `none` |
|---|---|---|---|
| solid | `-->` | `<-->` | `---` |
| dotted | `-.->` | `<-.->` | `-.-` |
| thick | `==>` | `<==>` | `===` |

A leaf kind with an icon (`KindCatalog.node_icons`) is drawn as
`n_x@{ icon: "<pack>:<icon>", form: "rounded", label: "«kind»<br/>name", pos: "b", h: 46 }` (labels through
`shape_label`); the browser registers the vendored packs (`canvas.registerIcons`, lazy loaders on
`/static/vendor/icons/<pack>.json`), and such nodes are `g.icon-shape` in the SVG.

Configured kinds (`ARCVIZ_KINDS_FILE`, `ARCVIZ_KIND_PACKS`) fall back after the built-in tables: the shape from
`active_kinds().node_shapes` through `SHAPE_SYNTAX` (`rect` `[ ]`, `rounded` `( )`, `stadium`
`([ ])`, `subroutine` `[[ ]]`, `cylinder` `[( )]`, `circle` `(( ))`, `hexagon` `{{ }}`,
`parallelogram` `[/ /]`, `trapezoid` `[/ \]`, `asymmetric` `> ]`, `rhombus` `{ }`), the style from
`node_styles` (no `classDef` without one), the edge line and default direction from `edge_lines`.

`_STYLES` maps every built-in kind to a `classDef` (fill, stroke, text colour); only kinds present in the
diagram get a `classDef` and a `class` line. Fill and text colour are always set together, so the
node styles are self-contained and stay readable on the light and the dark Mermaid theme; only
edges, edge labels and unstyled defaults change with the theme. Nodes in `highlight` (filtered to existing ids) get
the additional class `changed` with a thick yellow stroke.

## Comparison marks

`render(..., marks=None, edge_marks=None)` colours a version comparison (see
`arcviz/diff.py` and [HTTP server](http-server.md#comparison)). `marks` maps node ids to `added`,
`removed` or `changed`; each mark used by a visible node gets a `classDef` and a `class` line
(`changed` is the highlight style, merged with `highlight`). `edge_marks` maps edge ids to the same
marks and emits `linkStyle <n> <style>` after the class lines, `<n>` being the index of the edge
among the edge lines actually emitted (Mermaid numbers links in source order; edges dropped by
focus do not count).

| Mark | Node `classDef` | Edge `linkStyle` |
|---|---|---|
| `added` | `stroke:#2e7d32,stroke-width:4px` | `stroke:#2e7d32,stroke-width:3px` |
| `removed` | `stroke:#c62828,stroke-width:3px,stroke-dasharray:6 4,opacity:0.55` | `stroke:#c62828,stroke-width:2px,stroke-dasharray:6 4` |
| `changed` | `stroke:#e0a100,stroke-width:4px` | `stroke:#e0a100,stroke-width:3px` |

`diff.compare(old, new)` returns a `DiagramDiff` (sorted added/removed/changed node and edge ids,
`title_changed`, `direction_changed`; `to_dict()` adds `summary` counts). Nodes are changed when
name, kind, parent or properties differ; edges are matched by id and changed when label, kind,
direction or properties differ, while a reused id with other endpoints is removed and added. `diff.merged(old, new, diff)`
builds a render-only `Diagram`: `new`'s nodes and edges, the removed nodes (under their old parent
if that is a container in the merged diagram, else at the top level) and the removed edges with id
`-<id>` (with more leading dashes while that id is taken, since imported diagrams may use any edge
id), plus the node and edge marks.

Sequence diagrams (#108): `DiagramDiff` also lists `added_elements`, `removed_elements` and `changed_elements`
(interaction element ids; counted in `summary`). Elements are matched by (kind, id): an id naming another kind of
element in the newer state is removed and added. A matched element is changed when its own attributes (`covered`
compared as a set), its container (owning operand or combined fragment) or its position among the unchanged
elements both states keep in that container (outside a longest common subsequence, found as a longest increasing run
after trimming the common prefix and suffix; ties keep the old order's earlier elements) differ; lifelines likewise
in their order. `merged` copies the newer interaction and restores
each removed element, in old order, after its nearest former predecessor present in the view (in that
predecessor's container), else at the start of its former container; a removed combined fragment comes back
before its operands and they before their contents, removed executions are appended. A removed element whose id the newer state uses for another kind of element comes
back as `-<id>` (more dashes while taken), its mark is keyed by that id, and the references of restored elements
follow the renaming. The marks go to
`sequence_svg.render(marks=)`; there are no edge marks.

## Structure

Containers are emitted as nested `subgraph … end` blocks with their own `direction`, walking
`children()` recursively (sorted by name). A node that has children is emitted as a subgraph even
if its kind is not a container kind (a kind removed from the kinds file), so no element is hidden. Edges are emitted after all nodes; edges with a
missing endpoint are skipped defensively.

## Client

`canvas.js` initialises the vendored Mermaid 11 build (`window.mermaid`) with
`securityLevel: "strict"`, `theme: "neutral"` (light) or `"dark"`, `htmlLabels: true`,
`curve: "basis"`, and re-initialises when the OS colour scheme changes. Each render
uses a fresh element id (`arcviz-<seq>`) and ignores results of superseded renders; on error the
message is shown and Mermaid's leftover temp element is removed. Pan and zoom are a transform on
the wrapping `#viewport` element (see [Frontend](frontend.md)).
