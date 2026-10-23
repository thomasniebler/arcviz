"""Component diagrams: the rules and the text of a port (spec: docs/architecture/specs/component-diagrams.md, ADR-0181).
UML 2.5.1, chapters 10 and 11. Pure domain code over `Diagram`; the kinds are `model.COMPONENT_KINDS`, the profile that wires
these functions into the operations and the importer is registered in `graph_types`.

An edge points from the element that refers to the other to the element it refers to: a connection from the requirer to
the provider, a realization (a provided interface) and a usage (a required interface) from the component to the
interface.
"""

from __future__ import annotations

from .class_diagram import lines_of
from .model import Diagram, Edge, Node

COMPONENT = "component"
SUBSYSTEM = "subsystem"
INTERFACE = "interface"
PACKAGE = "package"
BUILDING_BLOCKS = frozenset({COMPONENT, SUBSYSTEM})
PORTS = "ports"
MAX_PORTS = 10
PORT_LENGTH = 40


def port_names(node: Node) -> list[str]:
    """The names of a component's ports: the text of each line before an optional `:` (`orders-in: OrderApi`)."""
    return [line.split(":", 1)[0].strip() for line in lines_of(node, PORTS)]


def port_text(edge: Edge) -> str:
    """`(source_port → target_port)` with the missing side left blank, "" without ports."""
    source, target = edge.properties.get("source_port", ""), edge.properties.get("target_port", "")
    if not source and not target:
        return ""
    return f"({source} → {target})".replace("( →", "(→").replace("→ )", "→)")


def edge_problem(diagram: Diagram, edge: Edge) -> str | None:
    """Why a relationship between these nodes is not well-formed UML, or None (hard rules 1 to 4 of the spec)."""
    src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
    if src is None or tgt is None:
        return None
    kind = edge.kind
    if kind != "dependency":
        for end in (src, tgt):
            if end.kind == PACKAGE:
                return f"a package ('{end.id}') takes part only in dependency edges"
    if src.id in diagram.ancestors(tgt.id) or tgt.id in diagram.ancestors(src.id):
        return f"'{src.id}' and '{tgt.id}' are one inside the other: an edge between a node and its own container cannot be drawn"
    if kind == "connection":
        if src.kind not in BUILDING_BLOCKS or tgt.kind not in BUILDING_BLOCKS or src.id == tgt.id:
            return f"a connection joins two different components or subsystems (got {src.kind} and {tgt.kind})"
    elif kind in ("realization", "usage") and (src.kind not in BUILDING_BLOCKS or tgt.kind != INTERFACE):
        return f"a {kind} goes from a component or subsystem to an interface (got {src.kind} to {tgt.kind})"
    for end, key in ((src, "source_port"), (tgt, "target_port")):
        port = edge.properties.get(key)
        if port and (end.kind != COMPONENT or port not in port_names(end)):
            return f"'{end.id}' has no port '{port}'"
    return None


def node_problem(diagram: Diagram, node: Node) -> str | None:
    """Why `node` (as it would be stored) may not have this kind, parent and ports, or None (hard rules 3 and 4)."""
    parent = diagram.nodes.get(node.parent) if node.parent is not None else None
    if node.kind == PACKAGE and parent is not None and parent.kind != PACKAGE:
        return "a package stands in a package or at the top level"
    if node.kind != COMPONENT:
        return None
    names = port_names(node)
    for name in dict.fromkeys(names):
        if names.count(name) > 1:
            return f"port '{name}' twice in '{node.id}'"
    for edge in diagram.edges:
        for end, key in ((edge.source, "source_port"), (edge.target, "target_port")):
            port = edge.properties.get(key)
            if end == node.id and port and port not in names:
                return f"port '{port}' is used by edge {edge.id}"
    return None


def rule_errors(diagram: Diagram) -> list[str]:
    """The hard rules over a whole diagram, for the importer (the same functions the operations use)."""
    errors: list[str] = []
    for node in diagram.nodes.values():
        problem = node_problem(diagram, node)
        if problem:
            errors.append(f"node '{node.id}': {problem}")
    for edge in diagram.edges:
        problem = edge_problem(diagram, edge)
        if problem:
            errors.append(f"edge '{edge.id}' ({edge.source} -> {edge.target}): {problem}")
    return errors
