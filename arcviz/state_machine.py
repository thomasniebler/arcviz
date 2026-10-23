"""State machine diagrams: the rules and the text of a transition (spec: docs/architecture/specs/state-machine-diagrams.md,
ADR-0178). UML 2.5.1, chapter 14. Pure domain code over `Diagram`; the kinds are `model.STATE_MACHINE_KINDS`, the profile
that wires these functions into the operations and the importer is registered in `graph_types`.
"""

from __future__ import annotations

from .model import Diagram, Edge, Node

COMPOSITE = "composite_state"
REGION = "state_region"
STATE = "state"
INITIAL = "initial_pseudostate"
FINALS = frozenset({"final_state", "terminate_pseudostate"})
FORK = "fork_pseudostate"
JOIN = "join_pseudostate"
CHOICE = "choice_pseudostate"
# Everything a transition can start or end at: all kinds but the region, which only groups.
STATE_FIELDS = ("entry", "do", "exit")


def transition_text(edge: Edge) -> str:
    """`trigger [guard] / effect`, each part optional: the trigger is the edge label (UML: the label of a transition)."""
    parts = []
    trigger = (edge.label or "").strip()
    if trigger:
        parts.append(trigger)
    guard = edge.properties.get("guard", "").strip()
    if guard:
        parts.append(f"[{guard}]")
    effect = edge.properties.get("effect", "").strip()
    if effect:
        parts.append(f"/ {effect}")
    return " ".join(parts)


def edge_problem(diagram: Diagram, edge: Edge) -> str | None:
    """Why a transition between these nodes is not well-formed UML, or None (the hard rules 1 to 3 of the spec)."""
    src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
    if src is None or tgt is None:
        return None
    for end in (src, tgt):
        if end.kind == REGION:
            return f"a state_region ('{end.id}') is not a vertex, so no transition may start or end at it"
    if tgt.kind == INITIAL:
        return "an initial_pseudostate has no incoming transitions"
    if src.kind in FINALS:
        return f"a {src.kind} has no outgoing transitions"
    has_trigger_or_guard = bool((edge.label or "").strip() or edge.properties.get("guard", "").strip())
    if has_trigger_or_guard and (src.kind in (INITIAL, FORK) or tgt.kind == JOIN):
        which = src.kind if src.kind in (INITIAL, FORK) else tgt.kind
        return f"a transition at a {which} has no trigger and no guard (only an effect)"
    return None


def node_problem(diagram: Diagram, node: Node) -> str | None:
    """Why `node` (as it would be stored) may not have this kind and parent, or None (hard rules 4 and 5)."""
    parent = diagram.nodes.get(node.parent) if node.parent is not None else None
    if node.kind == REGION and (parent is None or parent.kind != COMPOSITE):
        return "a state_region belongs in a composite_state"
    if parent is not None and parent.kind == COMPOSITE:
        siblings = [n for n in diagram.children(parent.id) if n.id != node.id]
        if node.kind == REGION and any(n.kind != REGION for n in siblings):
            return f"composite_state '{parent.id}' already has states directly; regions and states cannot be mixed"
        if node.kind != REGION and any(n.kind == REGION for n in siblings):
            return f"composite_state '{parent.id}' has regions: put '{node.id}' into one of them"
    if node.kind == COMPOSITE:
        children = diagram.children(node.id)
        if any(c.kind == REGION for c in children) and any(c.kind != REGION for c in children):
            return f"composite_state '{node.id}' would hold regions and states side by side"
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
        if edge.direction is not None:
            errors.append(f"edge '{edge.id}': Transitions are always directed (direction must be unset)")
    return errors
