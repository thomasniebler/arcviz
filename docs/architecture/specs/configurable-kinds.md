# Configurable element kinds

- Status: Implemented (milestone M6)
- Issue: #72
- ADR: [0010 Element kinds extended from a JSON file at startup](../adr/0010-kinds-extended-from-a-json-file.md)
- Depends on: [Domain model](../../spec/domain-model.md), [Edge direction and properties](edge-attributes.md)

## Summary

Node and edge kinds are closed sets in `model.py`. They feed the tool enums, the system prompt,
`/api/config` (and through it the inspector and the voice commands) and the Mermaid styles, so an
AWS, GCP or Kubernetes vocabulary needs a code change today. A JSON file named by
`ARCVIZ_KINDS_FILE` can add node kinds (container or leaf, with an optional Mermaid style and
shape) and edge kinds (with a line style and default direction). The built-in kinds stay; the file
only extends them. The file is read once at startup; an invalid file stops the server with a clear
message.

## Goals

- Add node kinds and edge kinds without code changes; they behave exactly like built-in kinds in
  operations, tools, the system prompt, `/api/config`, the inspector, voice commands and Mermaid.
- Validation at startup with messages that name the file, the kind and the problem.
- No new runtime dependency; the Docker image and the wheel stay self-contained.

## Non-goals

- Removing, renaming or restyling built-in kinds.
- Per-diagram vocabularies or changing kinds at runtime (an API to edit kinds).
- Icons or images per kind.
- Describing kinds to the model beyond their names.

## Design

### File format (`ARCVIZ_KINDS_FILE`)

```json
{
  "node_kinds": {
    "vpc": {"container": true, "style": "fill:#fff4e5,stroke:#ff9900,color:#232f3e"},
    "lambda": {"shape": "asymmetric", "style": "fill:#fff4e5,stroke:#ff9900,color:#232f3e"},
    "s3": {"shape": "cylinder"}
  },
  "edge_kinds": {
    "peering": {"line": "dotted", "direction": "both"},
    "invokes": {}
  }
}
```

- Top level: an object with the optional keys `node_kinds` and `edge_kinds`, each an object of
  kind name → definition. Other keys are errors.
- Kind names: `^[a-z][a-z0-9_]{0,31}$` (they become Mermaid `classDef` names, so no hyphens), not
  a built-in node or edge kind and not a reserved word (`changed`, `stub`, `default`, `end`, `graph`,
  `subgraph`, `flowchart`, `class`, `classdef`, `style`, `linkstyle`, `click`, `call`, `href`,
  `direction`). A name may not be both a node and an edge kind.
- Node kind definition: `container` (bool, default `false`), `style` (string, optional; a Mermaid
  `classDef` body, characters `[A-Za-z0-9#:,.%() -]` only, at most 200), `shape` (optional, one of
  `rect`, `rounded`, `stadium`, `subroutine`, `cylinder`, `circle`, `hexagon`, `parallelogram`,
  `trapezoid`, `asymmetric`, `rhombus`; ignored for containers, which are subgraphs). Without a
  style the node uses Mermaid's theme default, as the built-in fallback does.
- Edge kind definition: `line` (`solid` default, `dotted`, `thick`), `direction` (`forward`
  default, `both`, `none`).
- Unknown keys in a definition are errors (typos must not pass silently).

### Kind catalogue (`model.py`)

```python
NODE_SHAPES = ("rect", "rounded", "stadium", ...)   # names; Mermaid syntax lives in mermaid.py
EDGE_LINES = ("solid", "dotted", "thick")

@dataclass(frozen=True)
class KindCatalog:
    container_kinds: frozenset[str]
    leaf_kinds: frozenset[str]
    edge_kinds: frozenset[str]
    node_styles: Mapping[str, str]              # only configured kinds
    node_shapes: Mapping[str, str]              # only configured kinds, shape names
    edge_lines: Mapping[str, tuple[str, str]]   # only configured kinds: (line, default direction)
    all_kinds: frozenset[str]                   # property

BUILTIN_KINDS: KindCatalog          # CONTAINER_KINDS, LEAF_KINDS, EDGE_KINDS, no extras
def active_kinds() -> KindCatalog
def use_kinds(catalog: KindCatalog) -> None
```

`CONTAINER_KINDS`, `LEAF_KINDS`, `ALL_KINDS` and `EDGE_KINDS` keep their names and values: they are
the built-in sets. Everything that validates or lists kinds reads `active_kinds()` at call time:
`Node.is_container`, `operations` (`_check_kind`, `_require_container`, `_check_edge_kind`,
`update_node`), `agent` (tool enums, descriptions and the system prompt's kind list), `server`
(`/api/config`) and `mermaid` (style, shape and edge line lookups fall back from the built-in
tables to the catalogue). The catalogue is process-wide, like the model itself: the server
activates it once at startup.

### Loading (`kinds.py`, new module)

`load_kind_catalog(path) -> KindCatalog` reads and validates the file and returns
`BUILTIN_KINDS` extended by its kinds. It raises `KindConfigError` (a `ValueError`) with the path
and the offending kind, e.g. `kinds file /etc/arcviz/kinds.json: node kind 'vnet' is built in and
cannot be redefined`. The module imports only `model`.

### Wiring (`server.py`)

`create_app()` reads `ARCVIZ_KINDS_FILE`; when set it calls
`use_kinds(load_kind_catalog(path))`, otherwise `use_kinds(BUILTIN_KINDS)`. A `KindConfigError`
propagates, so the server does not start with a broken vocabulary.

### Agent

`agent.build_tools(catalog)` builds the tool list from a catalogue; `TOOLS` stays as the list for
`BUILTIN_KINDS` (compatibility). `DiagramAgent.chat` passes `build_tools(active_kinds())` to the
provider, and `build_system_prompt` lists `active_kinds().all_kinds`.

### Rendering

- Node style: `_STYLES[kind]`, else `catalog.node_styles[kind]`, else no `classDef`.
- Node shape: `_SHAPES[kind]`, else the syntax of `catalog.node_shapes[kind]`, else rectangle.
- Edge arrow: `_EDGE_LINES[kind]`, else `catalog.edge_lines[kind]`, else solid/forward.
- A node that has children is always drawn as a subgraph, even if its kind is not (or no longer)
  a container kind, so removing a kind from the file never hides elements.

### Elements of unknown kinds

Diagrams may contain kinds that are no longer configured (file changed or removed). They load,
render (default style) and can be edited, renamed or removed; operations reject *setting* an
unknown kind as before, and such a node cannot receive new children.

## Testing

- `tests/test_kinds.py`: a valid file extends the built-ins; each validation rule rejects with a
  message naming the kind; `use_kinds` affects `Node.is_container` and operations; mermaid style,
  shape and edge arrow of configured kinds; a node with children of an unknown kind renders as a
  subgraph; tool enums and the system prompt list configured kinds; `create_app` with
  `ARCVIZ_KINDS_FILE` reports them in `/api/config` and refuses an invalid file.
- `tests/conftest.py`: an autouse fixture restores `BUILTIN_KINDS` after every test.

## Migration/compatibility

Without `ARCVIZ_KINDS_FILE` nothing changes. Stored diagrams are untouched. The Docker image reads
the file from any mounted path.

## Open questions

None.

## Decisions

- JSON file named by an environment variable, extend-only, validated strictly at startup, process
  wide: [ADR-0010](../adr/0010-kinds-extended-from-a-json-file.md) (Proposed).
