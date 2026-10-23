"""Render a Diagram as Mermaid flowchart source (containers become subgraphs).

Deployment and activity diagrams are drawn here; the node notation depends on the diagram type
(activity: UML 2.5.1 shapes, spec docs/architecture/specs/activity-diagrams.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from .class_diagram import ASSOCIATIONS, association_end_text, is_abstract, lines_of
from .component_diagram import port_text
from .model import (
    ACTIVITY_KINDS,
    CLASS_KINDS,
    EDGE_STEREOTYPES,
    STATE_MACHINE_KINDS,
    Diagram,
    Edge,
    Node,
    active_kinds,
    single_line,
)
from .state_machine import STATE_FIELDS, transition_text
from .use_case import extend_text

_SHAPES = {
    "database": ("[(", ")]"),
    "storage": ("[(", ")]"),
    "queue": ("[[", "]]"),
    "gateway": ("{{", "}}"),
    "external": ("([", "])"),
    "function": (">", "]"),
    "artifact": ("[/", "/]"),
}

# Mermaid syntax of the shape names a configured kind may use (model.NODE_SHAPES).
SHAPE_SYNTAX = {
    "rect": ("[", "]"),
    "rounded": ("(", ")"),
    "stadium": ("([", "])"),
    "subroutine": ("[[", "]]"),
    "cylinder": ("[(", ")]"),
    "circle": ("((", "))"),
    "hexagon": ("{{", "}}"),
    "parallelogram": ("[/", "/]"),
    "trapezoid": ("[/", "\\]"),
    "asymmetric": (">", "]"),
    "rhombus": ("{", "}"),
}

# Edge kind -> (line style, default direction). The direction of an edge may override the default.
_EDGE_LINES = {
    "connection": ("solid", "forward"),
    "dependency": ("dotted", "forward"),
    "deploys": ("thick", "forward"),
    "association": ("solid", "none"),
    "replication": ("solid", "both"),
    # class diagrams: the end markers of aggregation and composition are `_CIRCLE_ENDS`
    "generalization": ("solid", "forward"),
    "realization": ("dotted", "forward"),
    "aggregation": ("solid", "forward"),
    "composition": ("thick", "forward"),
    # use case diagrams: the keyword of include and extend is `model.EDGE_STEREOTYPES`
    "include": ("dotted", "forward"),
    "extend": ("dotted", "forward"),
    "usage": ("dotted", "forward"),  # component diagrams: a required interface
}
# Mermaid draws markers only at the target end, so aggregation and composition (part -> whole) end in a circle there.
_CIRCLE_ENDS = {"aggregation": "--o", "composition": "==o"}

_ARROWS = {
    ("solid", "forward"): "-->",
    ("solid", "both"): "<-->",
    ("solid", "none"): "---",
    ("dotted", "forward"): "-.->",
    ("dotted", "both"): "<-.->",
    ("dotted", "none"): "-.-",
    ("thick", "forward"): "==>",
    ("thick", "both"): "<==>",
    ("thick", "none"): "===",
}

_STYLES = {
    "deployment": "fill:#f4f6fb,stroke:#3b4f7a,stroke-width:1.5px,color:#1b2a4a",
    "vnet": "fill:#eef7f1,stroke:#2f7d4f,stroke-width:1.5px,color:#1d4d30",
    "subnet": "fill:#f7fbf8,stroke:#5aa374,stroke-dasharray:4 3,color:#1d4d30",
    "node": "fill:#fdf6ec,stroke:#b8742b,stroke-width:1.5px,color:#5a3812",
    "cluster": "fill:#f3effa,stroke:#6a4bb3,stroke-width:1.5px,color:#2f1f5c",
    "group": "fill:#f6f6f6,stroke:#8a8a8a,stroke-dasharray:5 3,color:#333",
    "region": "fill:#fbfbfb,stroke:#555,stroke-width:1px,color:#222",
    "service": "fill:#dbe8ff,stroke:#2b5fc7,color:#0f2a63",
    "component": "fill:#e8e8ef,stroke:#5b5b7a,color:#22223a",
    "database": "fill:#ffe9d6,stroke:#c7672b,color:#5a2a0d",
    "queue": "fill:#f1e4ff,stroke:#8a4bd1,color:#3b1a66",
    "storage": "fill:#fff3c4,stroke:#b9960b,color:#4d3f05",
    "gateway": "fill:#d9f4ec,stroke:#1f8f6a,color:#0d4a36",
    "external": "fill:#ffffff,stroke:#777,stroke-dasharray:3 3,color:#333",
    "artifact": "fill:#f0f0f0,stroke:#444,color:#222",
    "function": "fill:#e0f7fa,stroke:#00838f,color:#004d55",
    "changed": "stroke:#e0a100,stroke-width:4px",
    "added": "stroke:#2e7d32,stroke-width:4px",
    "removed": "stroke:#c62828,stroke-width:3px,stroke-dasharray:6 4,opacity:0.55",
    "stub": "fill:transparent,stroke:#8a8a8a,stroke-dasharray:2 3,color:#777",
    # activity diagrams (UML notation: black control nodes, rounded actions)
    "opaque_action": "fill:#eef4ff,stroke:#2b5fc7,color:#0f2a63",
    "initial_node": "fill:#1b1b1b,stroke:#1b1b1b",
    "activity_final_node": "fill:#1b1b1b,stroke:#ffffff,stroke-width:2px",  # bull's eye on fr-circ
    "flow_final_node": "fill:#ffffff,stroke:#1b1b1b",
    "decision_node": "fill:#fffbe6,stroke:#8a6d00,color:#4d3f05",
    "merge_node": "fill:#fffbe6,stroke:#8a6d00,color:#4d3f05",
    "fork_node": "fill:#1b1b1b,stroke:#1b1b1b",
    "join_node": "fill:#1b1b1b,stroke:#1b1b1b",
    "central_buffer_node": "fill:#f6f6f6,stroke:#444,color:#222",
    "data_store_node": "fill:#f6f6f6,stroke:#444,color:#222",
    "activity_partition": "fill:#fafafa,stroke:#666,stroke-width:1px,color:#222",
    # class diagrams
    "class": "fill:#f1f8ee,stroke:#4c8a3c,color:#1e3b16",
    "interface": "fill:#eef4ff,stroke:#2b5fc7,color:#0f2a63",
    "enumeration": "fill:#fff6e5,stroke:#b8742b,color:#5a3812",
    "data_type": "fill:#f6f0fb,stroke:#7a4bb3,color:#2f1f5c",
    "package": "fill:#fafafa,stroke:#666,stroke-width:1px,color:#222",
    # use case diagrams
    "actor": "fill:#fffbe6,stroke:#8a6d00,color:#4d3f05",
    "use_case": "fill:#eef4ff,stroke:#2b5fc7,color:#0f2a63",
    "subject": "fill:#fafafa,stroke:#444,stroke-width:1.5px,color:#222",
    "subsystem": "fill:#f7faff,stroke:#5b5b7a,stroke-width:1.5px,color:#22223a",
    # state machine diagrams (UML notation: rounded states, black pseudostates, dashed regions)
    "state": "fill:#eef4ff,stroke:#2b5fc7,color:#0f2a63",
    "composite_state": "fill:#f7faff,stroke:#2b5fc7,stroke-width:1.5px,color:#0f2a63",
    "state_region": "fill:transparent,stroke:#2b5fc7,stroke-dasharray:6 4,color:#0f2a63",
    "initial_pseudostate": "fill:#1b1b1b,stroke:#1b1b1b",
    "final_state": "fill:#1b1b1b,stroke:#ffffff,stroke-width:2px",  # bull's eye on fr-circ
    "terminate_pseudostate": "fill:#ffffff,stroke:#1b1b1b",
    "choice_pseudostate": "fill:#fffbe6,stroke:#8a6d00,color:#4d3f05",
    "junction_pseudostate": "fill:#1b1b1b,stroke:#1b1b1b",
    "fork_pseudostate": "fill:#1b1b1b,stroke:#1b1b1b",
    "join_pseudostate": "fill:#1b1b1b,stroke:#1b1b1b",
    "shallow_history_pseudostate": "fill:#ffffff,stroke:#1b1b1b,color:#1b1b1b",
    "deep_history_pseudostate": "fill:#ffffff,stroke:#1b1b1b,color:#1b1b1b",
}

# Activity node notation: Mermaid >= 11.3 shape names (`@{ shape: ... }`); `None` label = no text.
_ACTIVITY_SHAPES = {
    "initial_node": ("f-circ", None),
    "activity_final_node": ("fr-circ", None),
    "flow_final_node": ("cross-circ", None),
    "decision_node": ("diam", "name"),
    "merge_node": ("diam", " "),
    "fork_node": ("fork", " "),
    "join_node": ("fork", " "),
}
# State machine notation (same syntax); the history pseudostates are circles with the letter of UML.
_STATE_SHAPES = {
    "initial_pseudostate": ("f-circ", None),
    "final_state": ("fr-circ", None),
    "terminate_pseudostate": ("cross-circ", None),
    "choice_pseudostate": ("diam", " "),
    "junction_pseudostate": ("sm-circ", None),
    "fork_pseudostate": ("fork", " "),
    "join_pseudostate": ("fork", " "),
    "shallow_history_pseudostate": ("circle", "H"),
    "deep_history_pseudostate": ("circle", "H*"),
}
_RULE = "─" * 10  # separates a state's name from its entry/do/exit lines
# Object nodes: a rectangle with the keyword of the metaclass.
_ACTIVITY_KEYWORDS = {"central_buffer_node": "centralBuffer", "data_store_node": "datastore"}


# Marks of a version comparison (`marks` / `edge_marks` in `render`); node marks reuse `_STYLES`.
_MARKS = ("added", "removed", "changed")
_EDGE_MARK_STYLES = {
    "added": "stroke:#2e7d32,stroke-width:3px",
    "removed": "stroke:#c62828,stroke-width:2px,stroke-dasharray:6 4",
    "changed": "stroke:#e0a100,stroke-width:3px",
}


# Front matter selecting Mermaid's swimlane layout (activity diagrams with partitions, ADR-0038).
SWIMLANE_FRONT_MATTER = "---\nconfig:\n  layout: swimlane\n---\n"


def mermaid_id(node_id: str) -> str:
    """Readable Mermaid identifier for a node id (plain word, prefixed to dodge keywords such as 'end').

    Not injective on its own ('foo-bar' and 'foo_bar' collide); `id_map` resolves collisions per diagram.
    """
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", node_id)


def id_map(diagram: Diagram) -> dict[str, str]:
    """Deterministic, collision-free mapping from node ids to Mermaid identifiers."""
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for node_id in sorted(diagram.nodes):
        base = mermaid_id(node_id)
        candidate, n = base, 2
        while candidate in taken:
            candidate = f"{base}_{n}"
            n += 1
        mapping[node_id] = candidate
        taken.add(candidate)
    return mapping


# One code per character, in one pass: `#` too, so the escaping is injective (#102). `:` as well: before decoding,
# Mermaid drops the last `;` of a `#…;` on a line matching `style.*:\S*#.*;` (or `classDef…`), which turned
# "Lifestyle:#1" into "Lifestyle:#351"; without a `:` in any label, only our own `key: value` syntax has one.
_ENTITY_CODES = str.maketrans({"#": "#35;", "&": "#amp;", "<": "#lt;", ">": "#gt;", '"': "#quot;", ":": "#58;"})


def escape_text(text: str) -> str:
    """Escape user/model supplied text for a quoted Mermaid label using Mermaid entity codes.

    Mermaid turns `#name;` into the HTML entity, so markup in names is shown literally instead of
    being interpreted (htmlLabels are on). Quotes must be escaped because the label is quoted. `#` is
    escaped too (`#35;`), so a name that contains an entity code is shown as typed (#102). Control
    characters are normalised as the operations do (`single_line`, #103), so stored data renders as well.
    """
    return single_line(text).translate(_ENTITY_CODES)


def _edge_line(kind: str) -> tuple[str, str]:
    """(line style, default direction) of an edge kind: built-in, configured, else solid forward."""
    return _EDGE_LINES.get(kind) or active_kinds().edge_lines.get(kind, ("solid", "forward"))


def edge_direction(edge: Edge) -> str:
    """Effective arrow heads of an edge (forward | both | none): its own direction, else its kind's default."""
    return edge.direction or _edge_line(edge.kind)[1]


def edge_arrow(edge: Edge) -> str:
    """Mermaid arrow for an edge: the line style of its kind, the heads of its direction (or the kind's)."""
    if edge.kind in _CIRCLE_ENDS:
        return _CIRCLE_ENDS[edge.kind]
    return _ARROWS.get((_edge_line(edge.kind)[0], edge_direction(edge)), "-->")


def edge_text(edge: Edge) -> str:
    """Edge label text: the label, plus protocol/port from the properties ("orders (https:443)"); an
    activity guard comes first in brackets ("[ok] name"); a transition reads `trigger [guard] / effect`."""
    if edge.kind in STATE_MACHINE_KINDS.edge_kinds:
        return transition_text(edge)
    guard = edge.properties.get("guard", "") if edge.kind in ACTIVITY_KINDS.edge_kinds else ""
    if guard:
        return f"[{guard}] {edge.label}" if edge.label else f"[{guard}]"
    protocol, port = edge.properties.get("protocol", ""), edge.properties.get("port", "")
    endpoint = f"{protocol}:{port}" if port else protocol
    if edge.label and endpoint:
        return f"{edge.label} ({endpoint})"
    return edge.label or endpoint


def edge_lines(edge: Edge, diagram_type: str | None = None) -> list[str]:
    """The lines of an edge's label before escaping: the keyword of its kind (`«realizes»`), then its text; the ends of a
    class diagram's association (roles and multiplicities) on a line of their own; in a component diagram the ports of the
    ends at the end of the last line."""
    lines = _edge_lines(edge)
    if diagram_type == "component":
        ports = port_text(edge)
        if ports and lines:
            lines[-1] = f"{lines[-1]} {ports}"
        elif ports:
            lines.append(ports)
    return lines


def _edge_lines(edge: Edge) -> list[str]:
    lines = []
    if edge.kind == "extend":  # «extend» [condition] (extension point), then the name
        lines.append(" ".join(part for part in (EDGE_STEREOTYPES["extend"], extend_text(edge)) if part))
        return [*lines, edge.label] if edge.label else lines
    if edge.kind in EDGE_STEREOTYPES:
        lines.append(EDGE_STEREOTYPES[edge.kind])
    text = edge_text(edge)
    if text:
        lines.append(text)
    if edge.kind in ASSOCIATIONS:
        ends = association_end_text(edge)
        if ends:
            lines.append(ends)
    return lines


def shape_label(text: str) -> str:
    """Escape text for `@{ label: "..." }`: entity codes as in `escape_text`, plus the backslash, which
    the shape syntax (a YAML string) would otherwise read as an escape sequence."""
    return escape_text(text).replace("\\", "#92;")


_CLASS_KEYWORDS = {"interface": "interface", "enumeration": "enumeration", "data_type": "dataType"}
_COMPARTMENT_LINES = 8  # a compartment shows this many members, then `… (+n)`; the model and the exports keep all


def _compartment(node: Node, field: str) -> list[str]:
    lines = lines_of(node, field)
    shown = [escape_text(line) for line in lines[:_COMPARTMENT_LINES]]
    if len(lines) > _COMPARTMENT_LINES:
        shown.append(f"… (+{len(lines) - _COMPARTMENT_LINES})")
    return shown


def _class_label(node: Node) -> str:
    """A classifier: its keyword («interface», «abstract»…), its name, then one compartment per kind of member."""
    parts: list[str] = []
    if node.kind in _CLASS_KEYWORDS:
        parts.append(f"«{_CLASS_KEYWORDS[node.kind]}»")
    elif is_abstract(node):
        parts.append("«abstract»")
    parts.append(escape_text(node.name))
    fields = {"enumeration": ("literals",), "interface": ("operations",)}.get(node.kind, ("attributes", "operations"))
    for field in fields:
        members = _compartment(node, field)
        if members:
            parts += [_RULE, *members]
    return f'"{"<br/>".join(parts)}"'


_INTERFACE_CIRCLE = 18  # an interface name up to this long fits the circle (the lollipop of UML); longer ones a stadium


def _component_label(node: Node) -> str:
    """`«component»`, the name, then one line `port <port>` per port below a rule."""
    parts = ["«component»", escape_text(node.name)]
    ports = lines_of(node, "ports")
    if ports:
        parts += [_RULE, *(f"port {escape_text(port)}" for port in ports)]
    return f'"{"<br/>".join(parts)}"'


def _label(node: Node, subgraph: bool = False) -> str:
    if node.kind in ("package", "subject"):
        return f'"{escape_text(node.name)}"'
    if node.kind in STATE_MACHINE_KINDS.all_kinds:
        return f'"{escape_text(node.name)}"'  # UML shows states and regions by name
    if node.kind in ACTIVITY_KINDS.all_kinds and node.kind not in _ACTIVITY_KEYWORDS:
        return f'"{escape_text(node.name)}"'  # UML shows actions and partitions by name only
    # Containers get a single-line label: Mermaid clips multi-line titles of nested subgraphs.
    separator = " " if node.is_container or subgraph else "<br/>"
    return f'"«{escape_text(node.kind)}»{separator}{escape_text(node.name)}"'


def _shape(kind: str) -> tuple[str, str]:
    """Opening/closing bracket of a node kind: built-in shape, configured shape, else rectangle."""
    if kind in _SHAPES:
        return _SHAPES[kind]
    return SHAPE_SYNTAX.get(active_kinds().node_shapes.get(kind, "rect"), ("[", "]"))


# Kind names that Mermaid reads as a keyword where a style class is named (`classDef class …` is a parse error).
_STYLE_NAMES = {"class": "class_kind"}


def _style_name(kind: str) -> str:
    return _STYLE_NAMES.get(kind, kind)


def _style(kind: str) -> str | None:
    return _STYLES.get(kind) or active_kinds().node_styles.get(kind)


def _state_label(node: Node) -> str:
    """The name of a simple state, then its entry/do/exit behaviours below a rule (the label is one line per part)."""
    lines = [escape_text(node.name)]
    behaviours = [f"{name} / {node.properties[name]}" for name in STATE_FIELDS if node.properties.get(name)]
    if behaviours:
        lines += [_RULE, *(escape_text(b) for b in behaviours)]
    return f'"{"<br/>".join(lines)}"'


def _node_line(node: Node, mid: str, diagram_type: str = "deployment") -> str:
    if node.kind in _STATE_SHAPES:
        shape, label = _STATE_SHAPES[node.kind]
        if label is None:
            return f"{mid}@{{ shape: {shape} }}"
        return f'{mid}@{{ shape: {shape}, label: "{shape_label(label)}" }}'
    if node.kind == "state":
        return f"{mid}({_state_label(node)})"
    if diagram_type == "component" and node.kind == "interface":
        if len(node.name) <= _INTERFACE_CIRCLE:
            return f'{mid}@{{ shape: circle, label: "{shape_label(node.name)}" }}'
        return f'{mid}(["«interface»<br/>{escape_text(node.name)}"])'
    if diagram_type == "component" and node.kind == "component":
        return f"{mid}[{_component_label(node)}]"
    if node.kind in CLASS_KINDS.leaf_kinds and diagram_type == "class":
        return f"{mid}[{_class_label(node)}]"
    if node.kind == "actor":
        return f'{mid}["«actor»<br/>{escape_text(node.name)}"]'
    if node.kind == "use_case":
        return f'{mid}(["{escape_text(node.name)}"])'
    if node.kind in _ACTIVITY_SHAPES:
        shape, label = _ACTIVITY_SHAPES[node.kind]
        if label is None:
            return f"{mid}@{{ shape: {shape} }}"
        return f'{mid}@{{ shape: {shape}, label: "{shape_label(node.name if label == "name" else label)}" }}'
    if node.kind == "opaque_action":
        return f"{mid}({_label(node)})"
    if node.kind in _ACTIVITY_KEYWORDS:
        return f'{mid}["«{_ACTIVITY_KEYWORDS[node.kind]}»<br/>{escape_text(node.name)}"]'
    icon = active_kinds().node_icons.get(node.kind)
    if icon:  # vendored icon pack (spec: cloud-icons.md); the label sits below the icon
        label = shape_label(f"«{node.kind}»") + "<br/>" + shape_label(node.name)
        return f'{mid}@{{ icon: "{icon}", form: "rounded", label: "{label}", pos: "b", h: 46 }}'
    opening, closing = _shape(node.kind)
    return f"{mid}{opening}{_label(node)}{closing}"


def breadcrumb(diagram: Diagram, focus: str | None) -> list[dict[str, str]]:
    """Path from the root to the focused node as [{id, name, kind}], empty for the whole diagram."""
    if focus is None or focus not in diagram.nodes:
        return []
    chain = [*reversed(diagram.ancestors(focus)), focus]
    return [
        {"id": n, "name": diagram.nodes[n].name, "kind": diagram.nodes[n].kind} for n in chain if n in diagram.nodes
    ]


def render(
    diagram: Diagram,
    highlight: Iterable[str] = (),
    direction: str | None = None,
    focus: str | None = None,
    marks: Mapping[str, str] | None = None,
    edge_marks: Mapping[str, str] | None = None,
) -> str:
    """Render the diagram, or with `focus` only that node's subtree.

    In focus mode, edges that cross the subtree boundary are kept and their outside endpoint is
    drawn once as a dashed *stub* at the top level, so the context of the focused part stays visible.
    An unknown `focus` raises ValueError. `direction` defaults to the diagram's own layout direction.

    `marks` (node id -> added | removed | changed) and `edge_marks` (edge id -> the same) colour a
    version comparison; edges are styled with `linkStyle <n>`, n counting the emitted edge lines.
    """
    direction = direction or diagram.direction
    if focus is not None and focus not in diagram.nodes:
        raise ValueError(f"Unknown focus node '{focus}'.")
    visible = set(diagram.nodes) if focus is None else {focus, *(d.id for d in diagram.descendants(focus))}
    highlight_set = {h for h in highlight if h in diagram.nodes}
    lines = [f"flowchart {direction}"]
    used_kinds: set[str] = set()
    ids = id_map(diagram)
    emitted: set[str] = set()  # guards against containment cycles in corrupt stored data

    def emit(node: Node, depth: int) -> None:
        if node.id in emitted:
            return
        emitted.add(node.id)
        indent = "  " * (depth + 1)
        used_kinds.add(node.kind)
        if node.is_container or diagram.children(node.id):  # children are never hidden, whatever the kind
            lines.append(f"{indent}subgraph {ids[node.id]}[{_label(node, subgraph=True)}]")
            lines.append(f"{indent}  direction {direction}")
            walk(node.id, depth + 1)
            lines.append(f"{indent}end")
        else:
            lines.append(f"{indent}{_node_line(node, ids[node.id], diagram.type)}")

    def walk(parent: str | None, depth: int) -> None:
        for node in diagram.children(parent):
            emit(node, depth)

    if focus is None:
        walk(None, 0)
    else:
        emit(diagram.nodes[focus], 0)

    stubs: list[str] = []
    link_styles: list[str] = []
    link_index = 0
    for edge in diagram.edges:
        if edge.source not in diagram.nodes or edge.target not in diagram.nodes:
            continue
        if edge.source not in visible and edge.target not in visible:
            continue
        for endpoint in (edge.source, edge.target):
            if endpoint not in visible and endpoint not in stubs:
                stubs.append(endpoint)
        arrow = edge_arrow(edge)
        text_lines = edge_lines(edge, diagram.type)
        label = f'|"{"<br/>".join(escape_text(line) for line in text_lines)}"|' if text_lines else ""
        lines.append(f"  {ids[edge.source]} {arrow}{label} {ids[edge.target]}")
        mark = (edge_marks or {}).get(edge.id)
        if mark in _EDGE_MARK_STYLES:
            link_styles.append(f"  linkStyle {link_index} {_EDGE_MARK_STYLES[mark]}")
        link_index += 1

    for stub in sorted(stubs, reverse=True):  # inserted at the top, so reversed keeps them sorted
        lines.insert(1, f"  {_node_line(diagram.nodes[stub], ids[stub], diagram.type)}")

    for kind in sorted(used_kinds):
        style = _style(kind)
        if style:
            lines.append(f"  classDef {_style_name(kind)} {style}")
    for kind in sorted(used_kinds):
        members = [ids[n.id] for n in diagram.nodes.values() if n.kind == kind and n.id in visible]
        if members and _style(kind):
            lines.append(f"  class {','.join(sorted(members))} {_style_name(kind)}")
    if stubs:
        lines.append(f"  classDef stub {_STYLES['stub']}")
        lines.append(f"  class {','.join(sorted(ids[s] for s in stubs))} stub")
    marked: dict[str, set[str]] = {"changed": {h for h in highlight_set if h in visible}}
    for node_id, mark in (marks or {}).items():
        if mark in _MARKS and node_id in visible:
            marked.setdefault(mark, set()).add(node_id)
    for mark in _MARKS:
        members = sorted(ids[n] for n in marked.get(mark, ()))
        if members:
            lines.append(f"  classDef {mark} {_STYLES[mark]}")
            lines.append(f"  class {','.join(members)} {mark}")
    lines.extend(link_styles)
    source = "\n".join(lines) + "\n"
    if diagram.type == "activity" and any(diagram.nodes[n].kind == "activity_partition" for n in visible):
        # ADR-0038: Mermaid's swimlane layout draws top-level subgraphs (the partitions) as lanes.
        source = SWIMLANE_FRONT_MATTER + source
    return source
