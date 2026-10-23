"""Class diagrams: the rules, the member and multiplicity text (spec: docs/architecture/specs/class-diagrams.md, ADR-0179).
UML 2.5.1, chapters 10 and 11. Pure domain code over `Diagram`; the kinds are `model.CLASS_KINDS`, the profile that wires
these functions into the operations and the importer is registered in `graph_types`.

An edge always points from the element that refers to the other to the element it refers to: a generalization from the
specific to the general classifier, a realization from the class to the interface, aggregation and composition from the
part to the whole (the end marker of a Mermaid edge is at its target, ADR-0184).
"""

from __future__ import annotations

import re
from collections import defaultdict

from .model import Diagram, Edge, Node, reaches

CLASS = "class"
INTERFACE = "interface"
ENUMERATION = "enumeration"
DATA_TYPE = "data_type"
PACKAGE = "package"
CLASSIFIERS = frozenset({CLASS, INTERFACE, ENUMERATION, DATA_TYPE})
ASSOCIATIONS = frozenset({"association", "aggregation", "composition"})
# Generalization joins classifiers of one kind; enumerations are not specialised.
SPECIALISABLE = frozenset({CLASS, INTERFACE, DATA_TYPE})
ABSTRACT = "abstract"
MEMBER_LINES = 30  # per compartment: bounds the project document (spec: class-diagrams.md)
MEMBER_LENGTH = 120
LITERAL_LENGTH = 60

_MULTIPLICITY = re.compile(r"^(\d+|\*)(\.\.(\d+|\*))?$")


def multiplicity_problem(text: str) -> str | None:
    """Why `text` is not a UML multiplicity (`1`, `*`, `0..1`, `1..*`, `2..5`), or None."""
    match = _MULTIPLICITY.match(text)
    if match is None:
        return "a multiplicity is a number or *, or lower..upper (1, *, 0..1, 1..*)"
    lower, upper = match.group(1), match.group(3)
    if upper is not None and lower != "*" and upper != "*" and int(lower) > int(upper):
        return f"the lower bound {lower} is above the upper bound {upper}"
    if upper is not None and lower == "*":
        return "a lower bound cannot be *"
    return None


def is_abstract(node: Node) -> bool:
    return node.properties.get(ABSTRACT) == "true"


def lines_of(node: Node, name: str) -> list[str]:
    """The stored lines of a list field (`attributes`, `operations`, `literals`)."""
    value = node.properties.get(name, "")
    return value.split("\n") if value else []


_VISIBILITY = "+-#~"
_MEMBER_NAME = re.compile(r"^\s*(?:[+\-#~]\s*)?(/?\s*[^\s:(\[{=]+)")


def member_key(line: str, operation: bool) -> str:
    """What makes two members the same: an attribute's name, an operation's name and parameter list."""
    match = _MEMBER_NAME.match(line)
    name = "".join(match.group(1).split()).lower() if match else line.strip().lower()
    if not operation:
        return name
    params = line[line.index("(") + 1 : line.rindex(")")] if "(" in line and ")" in line else ""
    return f"{name}({''.join(params.split()).lower()})"


# ---- association text ---------------------------------------------------------------------------------------------


def association_end_text(edge: Edge) -> str:
    """`source_role source_multiplicity – target_role target_multiplicity`, parts that are unset left out; "" when nothing
    is set (so an association without roles and multiplicities draws just its name)."""
    props = edge.properties
    left = " ".join(p for p in (props.get("source_role", ""), props.get("source_multiplicity", "")) if p)
    right = " ".join(p for p in (props.get("target_role", ""), props.get("target_multiplicity", "")) if p)
    if not left and not right:
        return ""
    return f"{left} – {right}".strip() if left and right else left or right


# ---- rules --------------------------------------------------------------------------------------------------------


def _generalizations(diagram: Diagram, skip: str | None = None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "generalization" and edge.id != skip:
            out[edge.source].append(edge.target)
    return out


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
    if kind == "generalization":
        if src.id == tgt.id:
            return "a classifier cannot specialise itself"
        if src.kind != tgt.kind or src.kind not in SPECIALISABLE:
            return (
                f"a generalization joins two classes, two interfaces or two data types (got {src.kind} and {tgt.kind})"
            )
        if reaches(_generalizations(diagram, skip=edge.id), tgt.id, src.id):
            return f"'{tgt.id}' already specialises '{src.id}': the generalization would form a cycle"
    elif kind == "realization":
        if src.kind != CLASS or tgt.kind != INTERFACE:
            return f"a realization goes from a class to an interface (got {src.kind} to {tgt.kind})"
    elif kind in ASSOCIATIONS:
        allowed = {CLASS} if kind != "association" else {CLASS, INTERFACE}
        if kind == "association":
            ends_ok = src.kind in allowed and tgt.kind in allowed
        else:
            ends_ok = src.kind == CLASS and tgt.kind == CLASS
        if not ends_ok:
            where = "two classes or interfaces" if kind == "association" else "two classes"
            return f"an {kind} joins {where} (got {src.kind} and {tgt.kind})"
    if kind == "association":
        if edge.direction not in (None, "forward", "none"):
            return "an association may only be directed forward (navigable to the target) or have no direction"
    elif edge.direction is not None:
        return f"a {kind} has a fixed notation: omit direction"
    return None


def rule_errors(diagram: Diagram) -> list[str]:
    """The hard rules over a whole diagram, for the importer (the same function the operations use)."""
    errors: list[str] = []
    for edge in diagram.edges:
        problem = edge_problem(diagram, edge)
        if problem:
            errors.append(f"edge '{edge.id}' ({edge.source} -> {edge.target}): {problem}")
    return errors
