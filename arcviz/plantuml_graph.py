"""PlantUML writers of the graph types with a fixed UML vocabulary (spec: docs/architecture/specs/uml-graph-types.md).

One function per type, returning the lines between `@startuml` and `@enduml` after the title (`exchange.export_plantuml`
adds both). All text goes through `plantuml_text.plain`; nesting is bounded by `MAX_PLANTUML_NESTING` (`TooDeep`).
"""

from __future__ import annotations

from collections import defaultdict

from .class_diagram import is_abstract, lines_of
from .component_diagram import port_names
from .mermaid import id_map
from .model import Diagram, Edge, Node
from .plantuml_text import MAX_PLANTUML_NESTING, TooDeep, plain
from .state_machine import STATE_FIELDS, transition_text
from .use_case import extend_text

# ---- state machine diagrams (spec: state-machine-diagrams.md) -----------------------------------------------------
# Every vertex is a named state and every transition is written at the end of the file, because PlantUML cannot address
# the states of a second concurrent region by their alias (checked with 1.2026.8): a state inside a composite state is
# `<outermost composite>.<alias>`. The pseudostates are stereotypes (`<<start>>`, `<<end>>`, `<<history>>`, `<<history*>>`,
# `<<choice>>`, `<<fork>>`, `<<join>>`); terminate and junction have no stereotype of their own, they are an `<<end>>` and
# a `<<choice>>`, each marked with a comment line.

_STEREOTYPES = {
    "initial_pseudostate": "<<start>>",
    "final_state": "<<end>>",
    "terminate_pseudostate": "<<end>>",
    "shallow_history_pseudostate": "<<history>>",
    "deep_history_pseudostate": "<<history*>>",
    "choice_pseudostate": "<<choice>>",
    "junction_pseudostate": "<<choice>>",
    "fork_pseudostate": "<<fork>>",
    "join_pseudostate": "<<join>>",
}
_MARKED = {"terminate_pseudostate": "terminate", "junction_pseudostate": "junction"}


def state_machine(diagram: Diagram) -> list[str]:
    ids = id_map(diagram)
    children: dict[str | None, list[Node]] = defaultdict(list)
    for node in diagram.nodes.values():
        children[node.parent].append(node)
    lines = ["left to right direction" if diagram.direction in ("LR", "RL") else "top to bottom direction"]

    def ref(node: Node) -> str:
        """How PlantUML names the node: its alias, or `<outermost composite state>.<alias>` inside a composite state."""
        top, seen = node, {node.id}
        while top.parent in diagram.nodes and top.parent not in seen:
            top = diagram.nodes[top.parent]
            seen.add(top.id)
        return ids[node.id] if top.id == node.id else f"{ids[top.id]}.{ids[node.id]}"

    def declare(node: Node, depth: int) -> None:
        if depth >= MAX_PLANTUML_NESTING:
            raise TooDeep("states", node.id)
        indent = "  " * depth
        name = plain(node.name)
        if node.kind in _STEREOTYPES:
            if node.kind in _MARKED:  # a comment on its own line: PlantUML takes a trailing one as part of the line
                lines.append(f"{indent}' {_MARKED[node.kind]}: {name}")
            lines.append(f'{indent}state "{name}" as {ids[node.id]} {_STEREOTYPES[node.kind]}')
        elif node.kind == "composite_state":
            lines.append(f'{indent}state "{name}" as {ids[node.id]} {{')
            body = children[node.id]
            if body and all(c.kind == "state_region" for c in body):
                for position, region in enumerate(body):
                    if position:
                        lines.append(f"{indent}  --")
                    lines.append(f"{indent}  ' region: {plain(region.name)}")
                    scope(region.id, depth + 1)
            else:
                scope(node.id, depth + 1)
            lines.append(f"{indent}}}")
        else:
            lines.append(f'{indent}state "{name}" as {ids[node.id]}')
            for field in STATE_FIELDS:
                if node.properties.get(field):
                    lines.append(f"{indent}{ref(node)} : {field} / {plain(node.properties[field])}")

    def scope(parent: str | None, depth: int) -> None:
        for node in children[parent]:
            declare(node, depth)

    scope(None, 0)
    for edge in diagram.edges:
        if edge.source in diagram.nodes and edge.target in diagram.nodes:
            text = plain(transition_text(edge))
            source, target = diagram.nodes[edge.source], diagram.nodes[edge.target]
            lines.append(f"{ref(source)} --> {ref(target)}" + (f" : {text}" if text else ""))
    return lines


# ---- class diagrams (spec: class-diagrams.md) ---------------------------------------------------------------------
# Members are written as typed. PlantUML has no syntax for the role names of an association, so they follow the name in
# parentheses. Aggregation and composition are stored from the part to the whole (ADR-0184); PlantUML draws the whole
# first, so the orientation is swapped back. `package` bodies nest like containers elsewhere.

_CLASS_DECLARATIONS = {
    "class": "class",
    "interface": "interface",
    "enumeration": "enum",
    "data_type": "class",
}


def class_diagram(diagram: Diagram) -> list[str]:
    ids = id_map(diagram)
    children: dict[str | None, list[Node]] = defaultdict(list)
    for node in diagram.nodes.values():
        children[node.parent].append(node)
    lines = ["left to right direction" if diagram.direction in ("LR", "RL") else "top to bottom direction"]

    def declare(node: Node, depth: int) -> None:
        if depth >= MAX_PLANTUML_NESTING:
            raise TooDeep("packages", node.id)
        indent = "  " * depth
        name = plain(node.name)
        if node.kind == "package":
            lines.append(f'{indent}package "{name}" as {ids[node.id]} {{')
            for child in children[node.id]:
                declare(child, depth + 1)
            lines.append(f"{indent}}}")
            return
        keyword = _CLASS_DECLARATIONS.get(node.kind, "class")
        if node.kind == "class" and is_abstract(node):
            keyword = "abstract class"
        head = f'{indent}{keyword} "{name}" as {ids[node.id]}' + (" <<dataType>>" if node.kind == "data_type" else "")
        members = [plain(line) for field in ("literals", "attributes", "operations") for line in lines_of(node, field)]
        if members:
            lines.append(head + " {")
            lines.extend(f"{indent}  {member}" for member in members)
            lines.append(f"{indent}}}")
        else:
            lines.append(head)

    for node in children[None]:
        declare(node, 0)
    for edge in diagram.edges:
        if edge.source in ids and edge.target in ids:
            lines.append(_class_relationship(diagram, edge, ids))
    return lines


def _class_relationship(diagram: Diagram, edge: Edge, ids: dict[str, str]) -> str:
    source, target = ids[edge.source], ids[edge.target]
    props = edge.properties
    sm, tm = props.get("source_multiplicity", ""), props.get("target_multiplicity", "")
    name = plain(edge.label) if edge.label else ""
    roles = " ↔ ".join(r for r in (props.get("source_role", ""), props.get("target_role", "")) if r)
    label = " ".join(p for p in (name, f"({plain(roles)})" if roles else "") if p)
    text = f" : {label}" if label else ""

    def mult(value: str) -> str:
        return f' "{value}"' if value else ""

    if edge.kind in ("aggregation", "composition"):
        arrow = "o--" if edge.kind == "aggregation" else "*--"
        return f"{target}{mult(tm)} {arrow}{mult(sm)} {source}{text}"
    if edge.kind == "generalization":
        return f"{target} <|-- {source}{text}"
    if edge.kind == "realization":
        return f"{target} <|.. {source}{text}"
    if edge.kind == "dependency":
        return f"{source} ..> {target}{text}"
    arrow = "-->" if edge.direction == "forward" else "--"
    return f"{source}{mult(sm)} {arrow}{mult(tm)} {target}{text}"


# ---- use case diagrams (spec: use-case-diagrams.md) ---------------------------------------------------------------
# The subject is a `rectangle`, the specification of a use case a one-line note (the first 200 characters).

NOTE_LENGTH = 200


def use_case(diagram: Diagram) -> list[str]:
    ids = id_map(diagram)
    children: dict[str | None, list[Node]] = defaultdict(list)
    for node in diagram.nodes.values():
        children[node.parent].append(node)
    lines = ["left to right direction" if diagram.direction in ("LR", "RL") else "top to bottom direction"]

    def declare(node: Node, depth: int) -> None:
        if depth >= MAX_PLANTUML_NESTING:
            raise TooDeep("subjects", node.id)
        indent = "  " * depth
        name = plain(node.name)
        if node.kind == "subject":
            lines.append(f'{indent}rectangle "{name}" as {ids[node.id]} {{')
            for child in children[node.id]:
                declare(child, depth + 1)
            lines.append(f"{indent}}}")
        else:
            keyword = "actor" if node.kind == "actor" else "usecase"
            lines.append(f'{indent}{keyword} "{name}" as {ids[node.id]}')

    for node in children[None]:
        declare(node, 0)
    for edge in diagram.edges:
        if edge.source not in ids or edge.target not in ids:
            continue
        source, target = ids[edge.source], ids[edge.target]
        label = plain(edge.label) if edge.label else ""
        if edge.kind == "generalization":
            lines.append(f"{target} <|-- {source}" + (f" : {label}" if label else ""))
        elif edge.kind in ("include", "extend"):
            text = " ".join(
                p for p in (f"<<{edge.kind}>>", plain(extend_text(edge)) if edge.kind == "extend" else "", label) if p
            )
            lines.append(f"{source} ..> {target} : {text}")
        else:
            arrow = "-->" if edge.direction == "forward" else "--"
            lines.append(f"{source} {arrow} {target}" + (f" : {label}" if label else ""))
    for node in diagram.nodes.values():
        note = " ".join((node.description or "").split())
        if note:
            if len(note) > NOTE_LENGTH:
                note = note[: NOTE_LENGTH - 1] + "…"
            lines.append(f"note right of {ids[node.id]} : {plain(note)}")
    return lines


# ---- component diagrams (spec: component-diagrams.md) -------------------------------------------------------------
# A component with ports is `component "Name" as n_id { portin "name" as n_id_p0 … }` (PlantUML cannot tell in from out, so
# every port is a `portin`); a connector that names a port goes from or to the port, else the component. The provided and
# required interfaces are `..|>` and `..>` with `<<use>>` to the interface, which is an `interface` node.

_COMPONENT_ARROWS = {"connection": "-->", "dependency": "..>"}


def component_diagram(diagram: Diagram) -> list[str]:
    ids = id_map(diagram)
    children: dict[str | None, list[Node]] = defaultdict(list)
    for node in diagram.nodes.values():
        children[node.parent].append(node)
    lines = ["left to right direction" if diagram.direction in ("LR", "RL") else "top to bottom direction"]
    port_ids: dict[tuple[str, str], str] = {}  # (component, port name) -> alias

    def declare(node: Node, depth: int) -> None:
        if depth >= MAX_PLANTUML_NESTING:
            raise TooDeep("containers", node.id)
        indent = "  " * depth
        name = plain(node.name)
        alias = ids[node.id]
        if node.kind == "package":
            lines.append(f'{indent}package "{name}" as {alias} {{')
            for child in children[node.id]:
                declare(child, depth + 1)
            lines.append(f"{indent}}}")
        elif node.kind == "subsystem":
            lines.append(f'{indent}component "{name}" as {alias} <<subsystem>> {{')
            for child in children[node.id]:
                declare(child, depth + 1)
            lines.append(f"{indent}}}")
        elif node.kind == "interface":
            lines.append(f'{indent}interface "{name}" as {alias}')
        else:
            ports = port_names(node)
            if not ports:
                lines.append(f'{indent}component "{name}" as {alias}')
                return
            lines.append(f'{indent}component "{name}" as {alias} {{')
            for number, port in enumerate(dict.fromkeys(ports)):
                port_alias = f"{alias}_p{number}"
                port_ids[(node.id, port)] = port_alias
                lines.append(f'{indent}  portin "{plain(port)}" as {port_alias}')
            lines.append(f"{indent}}}")

    for node in children[None]:
        declare(node, 0)

    def end(node_id: str, port: str | None) -> str:
        return port_ids.get((node_id, port or ""), ids[node_id])

    for edge in diagram.edges:
        if edge.source not in ids or edge.target not in ids:
            continue
        source = end(edge.source, edge.properties.get("source_port"))
        target = end(edge.target, edge.properties.get("target_port"))
        label = plain(edge.label) if edge.label else ""
        if edge.kind == "realization":
            lines.append(f"{source} ..|> {target}" + (f" : {label}" if label else ""))
        elif edge.kind == "usage":
            lines.append(f"{source} ..> {target} : " + " ".join(p for p in ("<<use>>", label) if p))
        else:
            arrow = _COMPONENT_ARROWS.get(edge.kind, "-->")
            lines.append(f"{source} {arrow} {target}" + (f" : {label}" if label else ""))
    return lines
