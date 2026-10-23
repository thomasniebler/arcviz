"""Use case diagrams: the rules and the text of an extend (spec: docs/architecture/specs/use-case-diagrams.md, ADR-0180).
UML 2.5.1, chapter 18. Pure domain code over `Diagram`; the kinds are `model.USE_CASE_KINDS`, the profile that wires these
functions into the operations and the importer is registered in `graph_types`.

Like every graph type, an edge points from the element that refers to the other to the element it refers to: an include
from the base to the included use case, an extend from the extending to the extended use case, a generalization from the
specific to the general one.
"""

from __future__ import annotations

from collections import defaultdict

from .model import Diagram, Edge, Node, reaches

ACTOR = "actor"
USE_CASE = "use_case"
SUBJECT = "subject"


def extend_text(edge: Edge) -> str:
    """`[condition] (extension point)`, each part optional, the text an extend shows after its keyword."""
    parts = []
    condition = edge.properties.get("condition", "").strip()
    if condition:
        parts.append(f"[{condition}]")
    point = edge.properties.get("extension_point", "").strip()
    if point:
        parts.append(f"({point})")
    return " ".join(parts)


def _closure(diagram: Diagram, kind: str, skip: str | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == kind and edge.id != skip:
            out[edge.source].append(edge.target)
    return out


def edge_problem(diagram: Diagram, edge: Edge) -> str | None:
    """Why a relationship between these nodes is not well-formed UML, or None (hard rules 1 to 4 of the spec)."""
    src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
    if src is None or tgt is None:
        return None
    for end in (src, tgt):
        if end.kind == SUBJECT:
            return f"a subject ('{end.id}') takes part in no edge"
    kind = edge.kind
    if kind == "association":
        if {src.kind, tgt.kind} != {ACTOR, USE_CASE}:
            return f"an association joins an actor and a use case (got {src.kind} and {tgt.kind})"
        if edge.direction not in (None, "forward", "none"):
            return "an association may only be directed forward (navigable to the target) or have no direction"
        return None
    if edge.direction is not None:
        return f"a {kind} has a fixed notation: omit direction"
    if kind in ("include", "extend"):
        if src.kind != USE_CASE or tgt.kind != USE_CASE or src.id == tgt.id:
            return f"an {kind} joins two different use cases (got {src.kind} and {tgt.kind})"
        if kind == "include" and reaches(_closure(diagram, "include", edge.id), tgt.id, src.id):
            return f"'{tgt.id}' already includes '{src.id}': the include would form a cycle"
    elif kind == "generalization":
        if src.id == tgt.id:
            return "a node cannot specialise itself"
        if src.kind != tgt.kind or src.kind not in (ACTOR, USE_CASE):
            return f"a generalization joins two actors or two use cases (got {src.kind} and {tgt.kind})"
        if reaches(_closure(diagram, "generalization", edge.id), tgt.id, src.id):
            return f"'{tgt.id}' already specialises '{src.id}': the generalization would form a cycle"
    return None


def node_problem(diagram: Diagram, node: Node) -> str | None:
    """Why `node` (as it would be stored) may not have this kind and parent, or None (hard rule 5)."""
    if node.parent is None:
        return None
    if node.kind == ACTOR:
        return f"an actor stands outside every subject (not in '{node.parent}')"
    if node.kind == SUBJECT:
        return "a subject stands at the top level"
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
