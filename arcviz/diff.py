"""Difference of two diagram states and a merged view of it for rendering.

Spec: docs/architecture/specs/version-comparison.md. Pure functions: the inputs are never changed and
the merged diagram is a render-only object that is never persisted.
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field
from typing import Any

from . import sequence
from .model import DIAGRAM_TYPES, Diagram, Edge, Node
from .sequence import CombinedFragment, ExecutionSpecification, FragmentNode, Interaction, InteractionOperand, Lifeline

# Diagram types whose versions can be compared (spec: docs/architecture/specs/diagram-types.md): all since M27 (#108).
SUPPORTED_TYPES = frozenset(DIAGRAM_TYPES)


@dataclass
class DiagramDiff:
    added_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    changed_nodes: list[str] = field(default_factory=list)
    added_edges: list[str] = field(default_factory=list)
    removed_edges: list[str] = field(default_factory=list)
    changed_edges: list[str] = field(default_factory=list)
    title_changed: bool = False
    direction_changed: bool = False
    # Sequence diagrams (#108): interaction elements (lifelines, fragments, operands, executions) by id.
    added_elements: list[str] = field(default_factory=list)
    removed_elements: list[str] = field(default_factory=list)
    changed_elements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["summary"] = {
            "added": len(self.added_nodes) + len(self.added_edges) + len(self.added_elements),
            "removed": len(self.removed_nodes) + len(self.removed_edges) + len(self.removed_elements),
            "changed": len(self.changed_nodes) + len(self.changed_edges) + len(self.changed_elements),
        }
        return out


def _node_changed(a: Node, b: Node) -> bool:
    return (a.name, a.kind, a.parent, a.properties, a.ref, a.description) != (
        b.name,
        b.kind,
        b.parent,
        b.properties,
        b.ref,
        b.description,
    )


def compare(old: Diagram, new: Diagram) -> DiagramDiff:
    """What changed from `old` to `new`. Edges are matched by id; a reused id with other endpoints
    counts as removed and added."""
    result = DiagramDiff(
        added_nodes=sorted(set(new.nodes) - set(old.nodes)),
        removed_nodes=sorted(set(old.nodes) - set(new.nodes)),
        changed_nodes=sorted(n for n in set(old.nodes) & set(new.nodes) if _node_changed(old.nodes[n], new.nodes[n])),
        title_changed=old.title != new.title,
        direction_changed=old.direction != new.direction,
    )
    old_edges = {e.id: e for e in old.edges}
    new_edges = {e.id: e for e in new.edges}
    for edge_id in sorted(set(old_edges) | set(new_edges)):
        before, after = old_edges.get(edge_id), new_edges.get(edge_id)
        if before is None:
            result.added_edges.append(edge_id)
        elif after is None:
            result.removed_edges.append(edge_id)
        elif (before.source, before.target) != (after.source, after.target):
            result.removed_edges.append(edge_id)
            result.added_edges.append(edge_id)
        elif (before.label, before.kind, before.direction, before.properties) != (
            after.label,
            after.kind,
            after.direction,
            after.properties,
        ):
            result.changed_edges.append(edge_id)
    if old.interaction is not None and new.interaction is not None:
        _compare_interactions(old.interaction, new.interaction, result)
    return result


# ---- sequence diagrams (#108) ----------------------------------------------------------------
# Elements are matched by (kind, id): an id that names another kind of element in the newer state is a removal plus
# an addition. A matched element counts as changed when its own attributes differ, when its container (the owning
# operand of a fragment, the owning combined fragment of an operand) differs, or when it moved among the elements
# both states keep in that container (those outside a longest common subsequence of the two orders).

Element = Lifeline | FragmentNode | InteractionOperand | ExecutionSpecification
Key = tuple[str, str]  # (element kind, id)


def _key(element: Element) -> Key:
    return sequence.element_kind(element), element.id


def _elements(interaction: Interaction) -> dict[Key, tuple[Element, Key | None]]:
    """Every element by key with its container's key: None for lifelines, top-level fragments and executions, the
    operand for fragments inside one, the combined fragment for operands."""
    out: dict[Key, tuple[Element, Key | None]] = {_key(lf): (lf, None) for lf in interaction.lifelines}
    for fragment, _, operand in sequence.walk(interaction.fragments):
        out[_key(fragment)] = (fragment, _key(operand) if operand is not None else None)
        if isinstance(fragment, CombinedFragment):
            out.update((_key(op), (op, _key(fragment))) for op in fragment.operands)
    out.update((_key(e), (e, None)) for e in interaction.executions)
    return out


def _own(element: Element) -> dict[str, Any]:
    """The attributes of an element itself, without the elements it contains; `covered` is a set (moving a
    lifeline re-sorts every covered list)."""
    data = element.model_dump(mode="json", exclude={"operands", "fragments"} & set(type(element).model_fields))
    if "covered" in data:
        data["covered"] = sorted(data["covered"])
    return data


def _orders(interaction: Interaction) -> dict[Key, list[Key]]:
    """The ordered children of every container (the lifelines and the top-level fragments under pseudo keys)."""
    out: dict[Key, list[Key]] = {
        ("", "lifelines"): [_key(lf) for lf in interaction.lifelines],
        ("", "fragments"): [_key(f) for f in interaction.fragments],
    }
    for fragment, _, _ in sequence.walk(interaction.fragments):
        if isinstance(fragment, CombinedFragment):
            out[_key(fragment)] = [_key(op) for op in fragment.operands]
            out.update((_key(op), [_key(f) for f in op.fragments]) for op in fragment.operands)
    return out


def _common_subsequence(a: list[Key], b: list[Key]) -> set[Key]:
    """The elements of a longest common subsequence of `a` and `b`, two orders of the same distinct elements: the
    longest increasing run of `a`-positions along `b` (patience sorting, O(n log n), no table). On a tie it keeps the
    earlier elements of `a`, so of two swapped neighbours the one that moved forward counts as moved."""
    position = {k: i for i, k in enumerate(a)}
    tails: list[int] = []  # tails[length - 1]: index into b of the smallest tail of an increasing run of that length
    tail_positions: list[int] = []  # their positions in a, kept alongside for the binary search
    previous: list[int] = [-1] * len(b)
    for j, k in enumerate(b):
        at = bisect.bisect_left(tail_positions, position[k])
        if at > 0:
            previous[j] = tails[at - 1]
        if at == len(tails):
            tails.append(j)
            tail_positions.append(position[k])
        else:
            tails[at] = j
            tail_positions[at] = position[k]
    out: set[Key] = set()
    j = tails[-1] if tails else -1
    while j >= 0:
        out.add(b[j])
        j = previous[j]
    return out


def _compare_interactions(old: Interaction, new: Interaction, result: DiagramDiff) -> None:
    before, after = _elements(old), _elements(new)
    result.added_elements = sorted(i for _, i in set(after) - set(before))
    result.removed_elements = sorted(i for _, i in set(before) - set(after))
    changed = {
        k for k in set(before) & set(after) if before[k][1] != after[k][1] or _own(before[k][0]) != _own(after[k][0])
    }
    old_orders, new_orders = _orders(old), _orders(new)
    for container in set(old_orders) & set(new_orders):
        stayed = (set(old_orders[container]) & set(new_orders[container])) - changed
        a = [k for k in old_orders[container] if k in stayed]
        b = [k for k in new_orders[container] if k in stayed]
        start = 0  # a common prefix and suffix never moved
        while start < len(a) and start < len(b) and a[start] == b[start]:
            start += 1
        end = 0
        while end < len(a) - start and end < len(b) - start and a[-1 - end] == b[-1 - end]:
            end += 1
        a, b = a[start : len(a) - end], b[start : len(b) - end]
        changed |= set(a) - _common_subsequence(a, b)
    result.changed_elements = sorted(i for _, i in changed)


def _merged_interaction(old: Interaction, new: Interaction, removed: set[str]) -> tuple[Interaction, dict[str, str]]:
    """`new` plus every removed element: after its nearest former predecessor that is there, else at the start of
    its former container (which is there too: a removed container is restored before its contents). A removed
    element whose id the newer state uses for another element comes back as `-<id>` (more dashes while taken), and
    the restored elements' references follow it, so nothing points at a missing lifeline or message. Returns the view
    and those renamings."""
    view = new.model_copy(deep=True)
    old_keys = set(_elements(old))
    gone = {k for k in old_keys - set(_elements(new)) if k[1] in removed}
    new_ids = set(sequence.element_ids(new))
    taken = new_ids | set(sequence.element_ids(old))
    alias: dict[str, str] = {}
    for _, element_id in sorted(gone):
        if element_id in new_ids:
            candidate = f"-{element_id}"
            while candidate in taken:
                candidate = f"-{candidate}"
            taken.add(candidate)
            alias[element_id] = candidate
    gone_ids = {i for _, i in gone}

    def view_id(element_id: str) -> str:
        """Where an element of the old state is in the view (a removed one may be renamed)."""
        return alias.get(element_id, element_id) if element_id in gone_ids else element_id

    def restored(element: Any, **update: Any) -> Any:
        copy = element.model_copy(deep=True, update={"id": view_id(element.id), **update})
        if isinstance(copy, sequence.Message):
            copy.sender = view_id(copy.sender) if copy.sender else None
            copy.receiver = view_id(copy.receiver) if copy.receiver else None
        if isinstance(copy, CombinedFragment | sequence.InteractionUse):
            copy.covered = [view_id(c) for c in copy.covered]
        if isinstance(copy, sequence.DestructionOccurrenceSpecification | ExecutionSpecification):
            copy.lifeline = view_id(copy.lifeline)
        if isinstance(copy, ExecutionSpecification):
            copy.start = view_id(copy.start)
            copy.finish = view_id(copy.finish) if copy.finish else None
        return copy

    def predecessors(siblings: list[Any], element_id: str) -> list[str]:
        """The view ids of the old siblings before `element_id`, nearest first."""
        ids = [s.id for s in siblings]
        return [view_id(i) for i in ids[: ids.index(element_id)][::-1]]

    for lifeline in old.lifelines:
        if (sequence.element_kind(lifeline), lifeline.id) in gone:
            _insert_after(view.lifelines, restored(lifeline), predecessors(old.lifelines, lifeline.id))

    def restore(fragments: list[FragmentNode], container: list[FragmentNode]) -> None:
        for fragment in fragments:
            if _key(fragment) in gone:
                copy = restored(fragment, **({"operands": []} if isinstance(fragment, CombinedFragment) else {}))
                before = predecessors(fragments, fragment.id)
                _insert_after(_container_of(view, before) or container, copy, before)
            if isinstance(fragment, CombinedFragment):
                owner = sequence.find(view, view_id(fragment.id))
                for operand in fragment.operands:
                    if _key(operand) in gone and isinstance(owner, CombinedFragment):
                        copy_op = restored(operand, fragments=[])
                        _insert_after(owner.operands, copy_op, predecessors(fragment.operands, operand.id))
                    target_op = sequence.find(view, view_id(operand.id))
                    # defensive: contents whose operand is not in the view go to the enclosing list, never lost
                    inner = target_op.fragments if isinstance(target_op, InteractionOperand) else container
                    restore(operand.fragments, inner)

    restore(old.fragments, view.fragments)
    view.executions += [restored(e) for e in old.executions if _key(e) in gone]
    return view, alias


def _container_of(view: Interaction, predecessors: list[str]) -> list[FragmentNode] | None:
    """The list in `view` holding the first of `predecessors` that is there, or None."""
    for previous in predecessors:
        for fragment, container, _ in sequence.walk(view.fragments):
            if fragment.id == previous:
                return container
    return None


def _insert_after(target: list[Any], element: Any, predecessors: list[str]) -> None:
    """Insert `element` after the first of `predecessors` in `target`, or at its start."""
    ids = [t.id for t in target]
    at = next((ids.index(p) + 1 for p in predecessors if p in ids), 0)
    target.insert(at, element)


def merged(old: Diagram, new: Diagram, diff: DiagramDiff) -> tuple[Diagram, dict[str, str], dict[str, str]]:
    """`new` plus the removed elements of `old` in their old place, with node and edge marks."""
    nodes: dict[str, Node] = {nid: node.model_copy(deep=True) for nid, node in new.nodes.items()}
    removed = [old.nodes[nid].model_copy(deep=True) for nid in diff.removed_nodes]
    nodes.update((node.id, node) for node in removed)
    for node in removed:
        parent = nodes.get(node.parent) if node.parent is not None else None
        if parent is None or not parent.is_container:
            node.parent = None
    edges: list[Edge] = [e.model_copy(deep=True) for e in new.edges]
    old_edges = {e.id: e for e in old.edges}
    # Removed edges get "-<id>" (more dashes if that is taken: imported diagrams may use any edge id).
    taken = {e.id for e in new.edges}
    removed_ids: dict[str, str] = {}
    for eid in diff.removed_edges:
        candidate = f"-{eid}"
        while candidate in taken:
            candidate = f"-{candidate}"
        taken.add(candidate)
        removed_ids[eid] = candidate
        edges.append(old_edges[eid].model_copy(update={"id": candidate}))
    interaction: Interaction | None = None
    alias: dict[str, str] = {}
    if old.interaction is not None and new.interaction is not None:
        interaction, alias = _merged_interaction(old.interaction, new.interaction, set(diff.removed_elements))
    view = Diagram(
        id=new.id,
        title=new.title,
        type=new.type,
        nodes=nodes,
        edges=edges,
        direction=new.direction,
        interaction=interaction,
    )

    marks = {nid: "added" for nid in [*diff.added_nodes, *diff.added_elements]}
    marks.update({nid: "removed" for nid in diff.removed_nodes})
    marks.update({alias.get(i, i): "removed" for i in diff.removed_elements})
    marks.update({nid: "changed" for nid in [*diff.changed_nodes, *diff.changed_elements]})
    edge_marks = {eid: "added" for eid in diff.added_edges}
    edge_marks.update({eid: "changed" for eid in diff.changed_edges})
    edge_marks.update({removed_ids[eid]: "removed" for eid in diff.removed_edges})
    return view, marks, edge_marks
