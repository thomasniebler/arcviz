# Edge direction and properties

- Status: Implemented (milestone M6)
- Issue: #71
- ADR: [0009 Optional edge direction overriding the kind's arrow](../adr/0009-optional-edge-direction.md)
- Depends on: [Domain model](../../spec/domain-model.md), [Operations](../../spec/operations.md),
  [Rendering](../../spec/rendering.md)

## Summary

Edges carry a kind and an optional label; the arrow (one-way, both ways, none) is fixed by the kind,
and details such as protocol or port can only go into the free-text label. Edges gain an optional
`direction` that overrides the kind's arrow heads while keeping its line style, and a `properties`
dict like nodes (for example `protocol`, `port`). Operations, tool schemas, the summary in the
system prompt, the Mermaid arrows and the inspector follow. Stored diagrams stay valid unchanged.

## Goals

- `Edge.direction`: `forward` (source → target), `both`, `none`, or unset (`null`) = the kind's
  default. Unset edges render exactly as today.
- `Edge.properties: dict[str, str]`, edited like node properties.
- `protocol` and `port` are shown on the edge in the diagram; all properties are in the model
  summary, `get_diagram` and the inspector.
- Backward compatible: stored edges without the new fields load with `direction = None`,
  `properties = {}`.

## Non-goals

- Validating property values (ports as numbers, known protocols).
- Reversing an edge (swapping source and target); remove and re-add instead.
- Per-edge colours or line styles independent of the kind.

## Design

### Model (`model.py`)

```python
EDGE_DIRECTIONS = ("forward", "both", "none")


class Edge(BaseModel):
    id: str
    source: str
    target: str
    label: str | None = None
    kind: str = "connection"
    direction: str | None = None  # one of EDGE_DIRECTIONS; None = default of the kind
    properties: dict[str, str] = Field(default_factory=dict)
```

The model summary prints edges as
`- [e1] a -> b (connection, direction=both) "label" {port=443, protocol=https}`; the
`direction=` part only when set, the braces only when there are properties.

### Operations (`operations.py`)

- `add_edge(source, target, label=None, kind="connection", direction=None, properties=None)`.
  `direction` is lower-cased and must be one of `EDGE_DIRECTIONS` or `default` (= `None`).
  `properties` must be an object; keys and values are coerced to `str`. All arguments are validated
  before anything is changed. Idempotency now compares source, target, kind, label **and
  direction**; for an existing identical edge, given `properties` are merged into it (set or
  overwrite) and the existing edge is returned.
- `update_edge(id, label=None, kind=None, direction=None, properties=None, remove_properties=None)`.
  Same validation, all of it before the first assignment; `direction="default"` resets to the
  kind's default; `remove_properties` is applied before `properties`, as for nodes.

### Tools (`agent.py`)

`add_edge` and `update_edge` gain `direction` (`enum: forward, both, none, default`) and
`properties` (object of strings); `update_edge` also `remove_properties`. The descriptions say that
the direction overrides the kind's default arrow and that protocol/port belong in `properties`.

### Rendering (`mermaid.py`)

Each edge kind has a line style and a default direction:

| Kind | Line | Default direction |
|---|---|---|
| `connection` | solid | forward |
| `dependency` | dotted | forward |
| `deploys` | thick | forward |
| `association` | solid | none |
| `replication` | solid | both |

The arrow is looked up from (line, direction):

| Line \ direction | forward | both | none |
|---|---|---|---|
| solid | `-->` | `<-->` | `---` |
| dotted | `-.->` | `<-.->` | `-.-` |
| thick | `==>` | `<==>` | `===` |

Unknown kinds keep falling back to solid/forward. The edge label is the `label`, followed by
`(protocol:port)` when `protocol` and/or `port` are set (`https:443`, `https`, `:443`); without a
label only that part is shown. Everything goes through `escape_text`.

### Frontend (inspector)

Each edge row gets a direction selector (`Standard (Art)`, `→ vorwärts`, `↔ beidseitig`,
`— ohne`) that commits `update_edge` on change, and a toggle that opens a properties sub-row with
the same key/value editor as nodes and an **Übernehmen** button (`update_edge` with `properties`
and `remove_properties`).

### HTTP

No new endpoints. `/ops` accepts the new arguments; diagram JSON contains the new edge fields.

## Testing

- `tests/test_model.py`: an edge stored without the fields loads with defaults; the summary shows
  direction and properties.
- `tests/test_operations.py`: add/update with direction and properties, `default` resets,
  invalid direction or non-object properties rejected **without** partial changes, idempotent add
  merges properties, direction is part of the idempotency key.
- `tests/test_mermaid.py`: every (line, direction) arrow; unset direction renders exactly the
  previous arrows; protocol/port in the label, escaped.
- `tests/test_agent.py`: the tool schemas expose `direction` and `properties`.
- `tests/e2e/smoke.mjs`: setting an edge to `both` in the inspector changes the Mermaid source to
  a two-headed arrow; adding `protocol`/`port` shows them on the edge.

## Migration/compatibility

Pydantic defaults make old JSON valid; fingerprints of old versions do not change on load, but the
first save after loading includes the new fields (`direction: null`, `properties: {}`), so the
fingerprint of an unchanged diagram differs from its pre-upgrade versions once. This only affects
`undo_target`'s "differs" check across the upgrade boundary (one extra undo step at most).

## Open questions

None.

## Decisions

- Optional `direction` overriding the heads of the kind's arrow, values `forward|both|none`
  (+ `default` as reset), instead of a mandatory direction or new edge kinds:
  [ADR-0009](../adr/0009-optional-edge-direction.md) (Proposed).
- Only `protocol` and `port` are rendered on the edge; other properties are model-only (ADR-0009).
