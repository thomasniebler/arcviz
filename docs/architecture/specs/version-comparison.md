# Visual comparison of two versions

- Status: Implemented (milestone M8; sequence diagrams: milestone M27)
- Issue: #80, #108 (sequence diagrams)
- ADR: [0019 Compare versions on the server as one merged render](../adr/0019-compare-versions-as-one-merged-render.md)

## Summary

Versions can be previewed one at a time, but there is no way to see *what* changed between two of
them. This change computes the difference of two states (two versions, or a version and the
current state) and renders it as **one** diagram: the newer state plus the removed elements and
edges, with added, removed and changed elements marked by colour, reusing the `classDef`
highlight mechanism the live renders already use for touched nodes.

## Goals

- `GET /api/diagrams/{id}/compare?base=<n>&target=<m>` returns the diff and a Mermaid render of it;
  without `target` the current state is the newer side.
- Nodes: added (green border), removed (red dashed border, faded, drawn where they used to be),
  changed (yellow border, as the existing highlight: name, kind, parent or properties differ).
  Edges: added, removed and changed (label, kind, direction or properties) get the same colours via `linkStyle`.
- The UI opens a comparison from the version selector, shows a summary and a legend, and returns to
  the normal view with one click or any other action. Comparison renders can be exported like any
  view.
- Works with focus: the comparison is restricted to a container's subtree like every render.

## Non-goals

- Side-by-side rendering, animated transitions, text diffs of the JSON.
- Comparing two different diagrams.
- Restoring parts of a version (cherry-picking).

## Design

### Module `arcviz/diff.py`

Imports `model` only. Pure functions over two `Diagram`s; nothing is persisted or mutated.

```python
@dataclass
class DiagramDiff:
    added_nodes: list[str]      # sorted ids
    removed_nodes: list[str]
    changed_nodes: list[str]    # same id, different name, kind, parent or properties
    added_edges: list[str]      # edge ids
    removed_edges: list[str]
    changed_edges: list[str]    # same id and endpoints, different label, kind, direction or properties
    title_changed: bool
    direction_changed: bool
    def to_dict(self) -> dict[str, Any]   # all fields plus "summary": {"added", "removed", "changed"} counts (nodes + edges)

def compare(old: Diagram, new: Diagram) -> DiagramDiff
def merged(old: Diagram, new: Diagram, diff: DiagramDiff) -> tuple[Diagram, dict[str, str], dict[str, str]]
```

**Edge identity.** Edges are matched by id. An id present on both sides with different
endpoints is reported as removed *and* added (ids can be reused after a removal).

**`merged`** builds a fresh `Diagram` (title, direction from `new`) for rendering only: all nodes of
`new`, plus every removed node. A removed node keeps its old parent if that parent exists in the
merged diagram and is a container there, otherwise it is placed at the top level (the merged
diagram is acyclic: removed nodes only point to old ancestors, present nodes only to new ones).
Edges: all edges of `new`, then every removed edge with id `-<old id>`, prefixed with further dashes while that id is taken
(imported diagrams may use any edge id), so it never collides with a real edge id. It returns the merged diagram, the node marks `{id: "added" | "removed" | "changed"}` and the
edge marks keyed by merged edge id.

### Rendering

`mermaid.render(..., marks=None, edge_marks=None)`: `marks` adds class lines per mark
(`classDef added`, `classDef removed`, `classDef changed`, the last one being the existing
highlight style); `edge_marks` adds `linkStyle <index> <style>` lines, where `<index>` is the
position of the edge among the emitted edge lines (Mermaid's link numbering). Styles:

| Mark | Node style | Edge style |
|---|---|---|
| `added` | `stroke:#2e7d32,stroke-width:4px` | `stroke:#2e7d32,stroke-width:3px` |
| `removed` | `stroke:#c62828,stroke-width:3px,stroke-dasharray:6 4,opacity:0.55` | `stroke:#c62828,stroke-width:2px,stroke-dasharray:6 4` |
| `changed` | existing `changed` style | `stroke:#e0a100,stroke-width:3px` |

`highlight` keeps working as before; marks are an additional, independent input.

### HTTP

`GET /api/diagrams/{id}/compare?base=<n>&target=<m>&focus=`: `base` required; `target` optional
(current state). Unknown diagram or version → 404. `focus` is validated against the merged diagram
and falls back to the whole diagram like the other render endpoints. Response:

```json
{
  "base": { "version": 3, "label": "...", "...": "..." },
  "target": null,
  "diff": { "added_nodes": ["prod-vnet"], "removed_nodes": [], "changed_nodes": ["service-a"], "added_edges": [], "removed_edges": ["e4"], "changed_edges": [], "title_changed": false, "direction_changed": false, "summary": { "added": 1, "removed": 1, "changed": 1 } },
  "mermaid": "flowchart TB ...",
  "mermaid_ids": { "...": "..." },
  "focus": null
}
```

`target: null` means "current state". Comparing a version with itself is valid (empty diff).

### UI

- Topbar button **Vergleichen** opens a dialog with **Von** (versions) and **Bis** (*Aktueller
  Stand* plus versions). *Von* defaults to the version selected in the version selector, *Bis* to
  the current state.
- The result replaces the canvas (fitted) and shows a banner `Vergleich v3 → aktueller Stand:
  +1 neu · −1 entfernt · ~1 geändert` with a colour legend and a **Beenden** button. Title or
  direction changes are mentioned in the banner.
- While a comparison is shown, clicks on elements do nothing (the merged diagram is not the model).
  **Beenden**, a focus change, selecting a version, any edit or chat turn returns to the normal view
  (every `showDiagram` ends it). Exports of the view (SVG, PNG, Mermaid) export the comparison; the
  model downloads (JSON, PlantUML) export the *Bis* state.

### Sequence diagrams (#108)

Sequence diagrams keep their content in an ordered fragment tree (`Diagram.interaction`), not in nodes and edges.
`DiagramDiff` gets three more lists, `added_elements`, `removed_elements`, `changed_elements` (sorted interaction
element ids: lifelines, messages, combined fragments, operands, interaction uses, destructions, executions), which
`summary` counts too; for the other types they stay empty.

- **Identity**: elements are matched by (element kind, id). An id whose element kind differs between the states is
  a removal plus an addition (imported data may reuse ids across kinds); in the merged view the removed element
  comes back as `-<id>` (more dashes while taken), and every reference of a restored element (message ends, covered
  lifelines, destruction and execution lifelines, execution start and finish) follows the renaming, so the view
  never holds an id twice and never points at a missing lifeline.
- **Changed**: the element's own attributes differ (messages: name, sort, kind, ends, relation link; fragments:
  operator, covered lifelines as a set, message lists; operands: guard; executions: lifeline, start, finish;
  lifelines: name, type, actor, link, note), or its container differs (the owning operand of a fragment, the
  owning combined fragment of an operand), or it moved among the elements both states keep unchanged in the same
  container: those outside a longest common subsequence of the two orders (on a tie the earlier elements of the
  old order are kept, so of two swapped neighbours the one that moved forward is marked). The common prefix and
  suffix are trimmed, and since ids are unique the subsequence is a longest increasing run of old positions
  (patience sorting, O(n log n), no n×m table). Lifelines use the same
  rule for their left-to-right order.
- **Merged view** (`_merged_interaction`): a copy of the newer interaction plus every removed element, placed
  after its nearest former predecessor (its former siblings before it, nearest first) that is in the view, in that
  predecessor's current container; without one, at the start of its former container. Removed elements are
  restored in the old order, a removed combined fragment (without its operands) before its operands and a removed
  operand (empty) before its contents, so the former container is always there; contents that survive elsewhere
  stay where the newer state has them (no duplicates). Removed lifelines are placed the same way in the lifeline
  order, removed executions are appended.
- **Marks**: added, removed and changed elements by id, drawn by the own SVG renderer (`data-id` groups get the
  class; the styles exist since M13). There are no edge marks.
- The view may break interaction rules (a restored message after a destruction, an execution whose start moved
  after its finish); it is only rendered, never stored, and the renderer draws such states.

The endpoint answers the render payload of the type (`renderer: "svg"`, `svg`, `mermaid: null`); focus does not
apply. The UI needs no change: the feature flag comes from `diff.SUPPORTED_TYPES`, the banner reads `summary`, and
clicks are already ignored during a comparison.

## Testing

- `tests/test_diff.py`: added/removed/changed nodes (each attribute), edge identity rules (changed
  label/kind, reused id with new endpoints), title/direction flags, summary counts; `merged` places
  removed nodes under surviving containers, at top level otherwise, keeps removed subtrees nested,
  renames removed edges; render emits the class and `linkStyle` lines with correct indices,
  including with focus (indices count only emitted edges).
- Server tests: compare two versions, a version with the current state, focus, 404s.
- e2e: after a change, comparing the previous version with the current state shows `added` nodes
  and the banner; **Beenden** restores the normal view.
- Sequence diagrams (#108): `tests/test_diff_sequence.py` (added/removed/changed elements, moves inside and across
  containers, placement of removed elements after their predecessor or at the start of their operand, restored
  fragments with their contents, executions, marks in the SVG, ids reused across kinds, the endpoint);
  `tests/test_properties.py` checks for any two reachable states that the merged view holds every element of both
  exactly once with the right marks; e2e step 13 compares the checkout template with an edited state.

## Migration/compatibility

None; new endpoint, module and optional render parameters.

## Open questions

None.

## Decisions

- ADR-0019 (status *Proposed*): diff on the server, one merged render with marks.
