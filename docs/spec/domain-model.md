# Domain model

Source: `arcviz/model.py`, `arcviz/templates.py`.

## Types

All types are Pydantic v2 models and serialise to JSON with `model_dump(mode="json")`.

```python
class Node(BaseModel):
    id: str
    name: str
    kind: str = "component"
    parent: str | None = None
    properties: dict[str, str] = {}
    ref: str | None = None  # project model element it stands for; omitted from dumps while unset
    description: str | None = None  # a note, at most MAX_DESCRIPTION (2000) characters; omitted while unset


class Edge(BaseModel):
    id: str
    source: str
    target: str
    label: str | None = None
    kind: str = "connection"
    direction: str | None = None  # "forward" | "both" | "none"; None = default of the kind
    properties: dict[str, str] = {}


class Diagram(BaseModel):
    id: str
    title: str
    type: str = "deployment"  # one of DIAGRAM_TYPES, see Diagram types
    nodes: dict[str, Node] = {}  # keyed by Node.id
    edges: list[Edge] = []
    direction: str = "TB"  # layout direction, one of DIRECTIONS
    created_at: str  # ISO 8601, UTC, seconds
    updated_at: str
```

`Node.ref` (and `Lifeline.ref` of sequence diagrams) links a view element to an element of the project's shared
model ([Projects: shared model](projects.md#shared-model)). A wrap serializer leaves the key out while it is
`None`, so documents written before M18 dump, fingerprint and export unchanged. The summary for the prompt
marks a linked element with `(= element <id>)`.

`Node.description` (and `Lifeline.description`) is a free-text **note** (M10, #87; design:
[Collaboration](../architecture/specs/collaboration.md), ADR-0092). It is at most `MAX_DESCRIPTION` = 2000
characters (Pydantic `max_length`, so an import with a longer one fails validation), set by `add_node`,
`update_node`, `add_lifeline`, `update_lifeline` ([Operations](operations.md)), and omitted from dumps while
`None` like `ref`, so documents without notes dump, fingerprint and export as before. Notes are not rendered. The
summary appends `— note: "<text>"` to the element's line (whitespace collapsed to single spaces, cut at
`NOTE_CHARS` = 200 characters with `…`); see the budget below.

## Kinds

| Set | Values |
|---|---|
| `CONTAINER_KINDS` | `deployment`, `vnet`, `subnet`, `node`, `cluster`, `group`, `region` |
| `LEAF_KINDS` | `service`, `component`, `database`, `queue`, `storage`, `gateway`, `external`, `artifact`, `function` |
| `ALL_KINDS` | union of both |
| `EDGE_KINDS` | `connection`, `dependency`, `deploys`, `association`, `replication` |
| `DIRECTIONS` | `TB` (default), `BT`, `LR`, `RL` (Mermaid flowchart directions) |
| `EDGE_DIRECTIONS` | `forward`, `both`, `none` (tuple; an edge's `direction` is one of these or `None`) |

Edges stored before `direction`/`properties` existed load with `None` and `{}` (Pydantic
defaults). An edge's `direction` overrides only the arrow heads its kind would draw; see
[Rendering](rendering.md#shapes-arrows-and-styles) and the design spec
[Edge direction and properties](../architecture/specs/edge-attributes.md).

These constants are the **built-in** kinds. The kinds in effect are a `KindCatalog`
(`container_kinds`, `leaf_kinds`, `edge_kinds`, `all_kinds`, plus `node_styles`, `node_shapes`
and `edge_lines` for configured kinds only), process-wide: `active_kinds()` returns it and
`use_kinds(catalog)` replaces it. It starts as `BUILTIN_KINDS` (the constants, no extras); the
server activates the catalogue loaded from `ARCVIZ_KINDS_FILE` at startup
(`kinds.load_kind_catalog`, see [Configurable element kinds](../architecture/specs/configurable-kinds.md)).
`NODE_SHAPES` and `EDGE_LINES` name the shapes and line styles a configured kind may use.

`Node.is_container` is `kind in active_kinds().container_kinds`. Only containers may be a `parent`.
The kinds are plain strings on the model so persisted files stay readable; validation happens in
operations (against the active catalogue), not in the Pydantic types, so elements of a kind that
is no longer configured still load. Adding a built-in kind means: add it to the set here, give it
a style (and shape, if not a rectangle) in `arcviz/mermaid.py`, and document it in the user guide.

### Kinds file

`kinds.load_kind_catalog(*paths)` returns `BUILTIN_KINDS` extended by the files' kinds (in order; a kind
defined in two files is an error naming both) or raises `KindConfigError` (a `ValueError`; the message
starts with `kinds file <path>:`). `server.create_app` passes the kind packs of `ARCVIZ_KIND_PACKS`
(`kinds.kind_pack_path(name)`, files in `arcviz/kindpacks/`) followed by `ARCVIZ_KINDS_FILE`. Format:
`{"node_kinds": {name: {"container"?, "style"?, "shape"?, "icon"?, "role"?, "traits"?}}, "edge_kinds": {name: {"line"?, "direction"?}}}`.
`icon` (leaf kinds only) is `"<pack>:<icon>"` and must exist in a vendored pack
(`kinds.ICON_DIR`, `kinds.icon_packs()`, `kinds.icon_names(pack)`); it ends up in
`KindCatalog.node_icons` ([cloud icons spec](../architecture/specs/cloud-icons.md)). Names of every
diagram type's built-in kinds are reserved.
`role` (leaf kinds only, one of `model.ROLES`: `database`, `storage`, `queue`, `service`, `function`, `component`,
`gateway`, `external`) names the built-in kind the deployment rules treat the kind as (`model.role_of(node)`: a built-in
kind is its own role); `traits` (needs a `role`) holds what the platform guarantees, today only
`{"redundancy": "built_in"}` (`model.TRAITS`, `model.kind_trait`). They end up in `KindCatalog.node_roles` and
`node_traits` and in `GET /api/config` (`node_roles`, `node_traits`). The node property `redundancy`
(`built_in` or `none`, `model.REDUNDANCY_VALUES`) overrides the kind's trait
([deployment traits](../architecture/specs/deployment-traits.md), ADR-0169).
Rules: only those keys at every level; names match `^[a-z][a-z0-9_]{0,31}$`, are not built-in node or edge kinds, not
reserved (`changed`, `stub`, `default`, `end`, `graph`, `subgraph`, `flowchart`, `class`,
`classdef`, `style`, `linkstyle`, `click`, `call`, `href`, `direction`) and not both a node and an
edge kind; `container` is a bool; `style` matches `^[A-Za-z0-9#:,.%() -]{1,200}$`; `shape` is in
`NODE_SHAPES`; `line` in `EDGE_LINES` (default `solid`); `direction` in `EDGE_DIRECTIONS` (default
`forward`).

## Identifiers

- Generated node ids are slugs: `slugify(text)` lower-cases, replaces runs of non `[a-z0-9]` with
  `-`, strips leading/trailing `-`, and falls back to `node` for an empty result.
- `Diagram.unique_id(base)` returns `slugify(base)` or the first free `slug-2`, `slug-3`, …
- An explicit `id` passed to `add_node` (by the model or via `/ops`) is only stripped, not
  validated or slugified, so ids such as `Service A` can be persisted. The Mermaid renderer and
  `find_node` cope with any string; `escape_text` keeps labels safe. Callers that need the slug
  invariant should omit `id`.
- Edge ids are `e<n>`; `next_edge_id()` starts at `len(edges) + 1` and skips existing ids.
- Diagram ids are validated by the store: `^[a-z0-9][a-z0-9_-]{0,63}$`.

## Lookup

`Diagram.find_node(ref)` resolves, in order: exact id; case-insensitive name or id match;
`slugify(ref)` as id. This is what lets the model and the API use names where ids are expected.
Names are not unique, so the first match in dict order wins; ids should be preferred.

## Tree queries

- `children(parent_id)`: direct children sorted by lower-cased name (`None` for top level).
- `descendants(node_id)`: depth-first list of the whole subtree.
- `ancestors(node_id)`: parent chain from the node up to the root.
- `path(node_id)`: `"Root / Container / Node"` using names.

Every traversal terminates on a containment cycle (#68): `ancestors` stops at the first node it has
already listed, `descendants` skips nodes it has already visited, and `mermaid.render` draws each
node at most once. Operations never create a cycle (`move_node` refuses), so a cycle can only come
from corrupt stored data, which `load_latest` does not validate. Such a diagram still loads and
renders (the nodes on the cycle are unreachable from the top level and therefore not drawn in the
whole view), every write of it is refused by the integrity check below, and a version restore
repairs it. `tests/test_cycles.py` runs every traversal on self-, two- and five-node cycles under a
wall-clock guard.

## Integrity

`Diagram.integrity_errors()` returns human-readable messages for: unknown parent, containment
cycle (a node among its own ancestors), edge with unknown source or target. The store refuses to
persist a diagram with integrity errors (`StoreError`). Operations are written so that they
cannot produce such a state; the check is a safety net.

## Summary for the prompt

`Diagram.summary()` renders the model as indented text, which is embedded in the system prompt:

```text
Diagram '<title>' (id: <id>)
  - [<id>] «<kind>» <name> {k=v, k=v}
    - [<child id>] «<kind>» <name>
Edges:
  - [<edge id>] <source> -> <target> (<kind>[, direction=<direction>]) "<label>" {k=v, ...}
```

Children are sorted by name, properties by key. An empty diagram shows `(no nodes yet)` and
`Edges: (none)`. A non-default direction adds the line `Layout direction: <dir>` after the header. The ids in square brackets are what the prompt tells the model to use.

### Budget

`summary(budget=N)` returns the full text when it has at most `N` characters. Otherwise notes give way first
(ADR-0093): every note is cut at `SHORT_NOTE_CHARS` = 40 characters; if that is still too long, each note becomes
`— note (call get_diagram)`. Only then are containers
with children are collapsed one at a time, largest subtree first (ties by id, containers inside an
already collapsed one are skipped), re-rendering after each until the text fits or nothing is left
to collapse. A collapsed container keeps its own line plus
`(collapsed: E elements, C containers inside; call get_diagram with focus="<id>" for details)`;
edges touching hidden elements are replaced by a count line, and a trailing note tells the model
that collapsed contents still exist.

What is still too long after every container is collapsed (a flat diagram, a sequence diagram, very long lines) is
**cut** (#115, [ADR-0152](../architecture/adr/0152-the-summary-cuts-edges-then-leaves.md)): edge lines from the end
first, then leaf lines from the end, then container lines from the end (for a sequence diagram: its lines from the
end, so fragments go before lifelines); each cut is one count line, `(+ N more edges not shown; call get_diagram for
them)` and `(+ N more elements not shown to keep this summary short; call get_diagram for them)` (`lines of the
interaction` for a sequence diagram), and a cut summary shows at most `SUMMARY_TITLE_CHARS` = 200 characters of the
title. So for every diagram and every budget `len(summary(N)) <= N + SUMMARY_SLACK` (1 000: the header, the footer and
the two count lines), which `tests/test_properties.py` checks for generated diagrams, flat and nested, far beyond the
budget. The top level is hidden only when it does not fit on its own.

### Subsets

`subset(focus=None, ids=None)` returns a JSON-able dict (`id`, `title`, `nodes`, `edges`,
`partial`) with the subtree of `focus` and/or the nodes in `ids` (resolved with `find_node`,
ancestors included for context), plus every edge touching a selected node. Without arguments it is
the whole diagram. This backs the `get_diagram` tool.

## Templates

`arcviz/templates.py` defines `TEMPLATES`, a mapping from template name to a factory
`(diagram_id, title) -> Diagram`:

- `empty`: a diagram with only id and title.
- `demo`: `prod-deployment` (`cloud=azure`, `region=westeurope`) containing `api-gateway`
  (gateway), `service-a`, `service-b` (services, `runtime=container`), `service-c` (service,
  `runtime=function`), `orders-db` (database, `engine=postgres`), `events` (queue); top-level
  `idp` (external, name "Identity Provider"); edges gateway→a/b (`https`), a→orders-db (`sql`),
  b→events (`publish`), c→events (`subscribe`), gateway→idp (`dependency`, `oidc`).
- `hub-spoke`, `aks-database`, `event-driven`: Azure starter architectures with explicit ids,
  several nesting levels, `cidr`/`sku`/… properties, edge directions and edge `protocol`/`port`
  (content in the design spec [More starter templates](../architecture/specs/starter-templates.md)
  and the [user guide](../guide/diagrams.md#creating-a-diagram)).

Every template is built with `operations` calls, so it satisfies the model invariants and uses only
built-in kinds. The template ids are exposed in `GET /api/config`; the UI's create dialog lists them
as static options with German labels (`tests/test_templates.py` checks that every id has one).
