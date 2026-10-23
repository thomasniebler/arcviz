"""Typed, validated mutations of a Diagram. Both the LLM tools and the REST API go through here."""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from functools import cache
from typing import Any, Union

from .graph_types import FieldError, clean_fields, guard_types, profile_of, reserved_key_error
from .model import (
    DIRECTIONS,
    EDGE_DIRECTIONS,
    MAX_DESCRIPTION,
    Diagram,
    Edge,
    Node,
    kinds_for,
    now_iso,
    single_line,
)
from .project_model import (
    LIFELINE,
    MESSAGE,
    ORIENTED_RELATIONS,
    ends_fit,
    find_view,
    linkable_kinds,
    relation_kinds_for,
    shown_relation_kinds,
)
from .sequence import Lifeline, Message, messages


class OperationError(Exception):
    """Raised when an operation cannot be applied; the message is safe to show to the LLM/user."""


# Values a node or edge property may be given; they are stored as strings (spec: operation-arguments.md).
PropertyValue = str | int | float | bool


def _require_node(diagram: Diagram, ref: str, what: str = "node") -> Node:
    node = diagram.find_node(ref)
    if node is None:
        known = ", ".join(sorted(diagram.nodes)) or "(none)"
        raise OperationError(f"Unknown {what} '{ref}'. Known node ids: {known}")
    return node


def _require_container(diagram: Diagram, ref: str) -> Node:
    node = _require_node(diagram, ref, "parent")
    if not node.is_container:
        raise OperationError(
            f"Node '{node.id}' has kind '{node.kind}' which cannot contain other nodes. "
            f"Container kinds are: {', '.join(sorted(kinds_for(diagram.type).container_kinds)) or '(none)'}"
        )
    return node


def _check_kind(diagram: Diagram, kind: str) -> str:
    kind = kind.strip().lower()
    allowed = kinds_for(diagram.type).all_kinds
    if kind not in allowed:
        raise OperationError(f"Unknown node kind '{kind}'. Allowed kinds: {', '.join(sorted(allowed))}")
    return kind


def _check_edge_kind(diagram: Diagram, kind: str) -> str:
    kind = kind.strip().lower()
    allowed = kinds_for(diagram.type).edge_kinds
    if kind not in allowed:
        raise OperationError(f"Unknown edge kind '{kind}'. Allowed kinds: {', '.join(sorted(allowed))}")
    return kind


def _check_direction(direction: str) -> str | None:
    """Validate an edge direction; "default" means "use the kind's default" and is stored as None."""
    if not isinstance(direction, str):
        raise OperationError(f"Edge direction must be a string: one of {', '.join(EDGE_DIRECTIONS)} or default.")
    direction = direction.strip().lower()
    if direction == "default":
        return None
    if direction not in EDGE_DIRECTIONS:
        raise OperationError(
            f"Unknown edge direction '{direction}'. Allowed: {', '.join(EDGE_DIRECTIONS)}, or default for the kind's own."
        )
    return direction


def _check_properties(properties: Any) -> dict[str, str]:
    if properties is None:
        return {}
    if not isinstance(properties, dict):
        raise OperationError("properties must be an object of key/value strings.")
    return {str(k): str(v) for k, v in properties.items()}


def clean_description(description: str | None) -> str | None:
    """A note as stored (spec: collaboration.md, ADR-0092): stripped, "" (or blank) removes it; OperationError when it
    is not a string or longer than MAX_DESCRIPTION. Callers treat None as "unchanged" on updates."""
    if description is None:
        return None
    if not isinstance(description, str):
        raise OperationError("description must be a string (an empty string removes the note).")
    text = description.strip()
    if len(text) > MAX_DESCRIPTION:
        raise OperationError(f"description is too long ({len(text)} characters, at most {MAX_DESCRIPTION}).")
    return text


def _check_keys(keys: Any) -> list[str]:
    if keys is None:
        return []
    if not isinstance(keys, list):
        raise OperationError("remove_properties must be a list of property keys.")
    return [str(k) for k in keys]


# Kinds used when an operation is called without one: deployment diagrams' own, else the profile's (ADR-0183).
def _default_node_kind(diagram: Diagram) -> str:
    profile = profile_of(diagram.type)
    return profile.default_node_kind if profile else "component"


def _default_edge_kind(diagram: Diagram) -> str:
    profile = profile_of(diagram.type)
    return profile.default_edge_kind if profile else "connection"


def _check_guard(diagram: Diagram, guard: Any) -> str:
    profile = profile_of(diagram.type)
    if profile is None or not profile.guard:
        types = " and ".join(guard_types())
        raise OperationError(f"guard is only defined for edges of {types} diagrams, not {diagram.type} diagrams.")
    if not isinstance(guard, str):
        raise OperationError("guard must be a string (an empty string removes it).")
    return guard.strip()


def _check_edge(diagram: Diagram, edge: Edge) -> None:
    """The rules of the diagram type for `edge` as it would be stored (a candidate; its ends exist)."""
    profile = profile_of(diagram.type)
    if profile is None:
        return
    problem = profile.edge_problem(diagram, edge)
    if problem:
        raise OperationError(f"Cannot connect '{edge.source}' to '{edge.target}': {problem}.")
    if profile.directed_noun and edge.direction is not None:
        raise OperationError(
            f"{profile.directed_noun} are always directed; omit direction (or use default) in {diagram.type} diagrams."
        )


def _check_node(diagram: Diagram, node: Node) -> None:
    """The rules of the diagram type for `node` as it would be stored (kind, parent; its children exist already)."""
    profile = profile_of(diagram.type)
    problem = profile.node_problem(diagram, node) if profile else None
    if problem:
        raise OperationError(f"Cannot place '{node.id}' ({node.kind}): {problem}.")


def _check_kind_change(diagram: Diagram, node: Node, new_kind: str) -> None:
    profile = profile_of(diagram.type)
    if profile is None:
        return
    old_kind = node.kind
    node.kind = new_kind  # the rules look the kinds up in the diagram; always put back
    try:
        for edge in diagram.edges:
            if edge.source == node.id or edge.target == node.id:
                problem = profile.edge_problem(diagram, edge)
                if problem:
                    raise OperationError(
                        f"Cannot change '{node.id}' to {new_kind}: edge '{edge.id}' would break a rule: {problem}."
                    )
        _check_node(diagram, node)
    finally:
        node.kind = old_kind


def _node_fields(diagram: Diagram, kind: str, raw: Any) -> dict[str, str | None]:
    """The property values of the typed `fields` of a node of `kind` (None: remove); OperationError otherwise."""
    if raw is None:
        return {}
    profile = profile_of(diagram.type)
    defined = profile.fields_of_node(kind) if profile else ()
    try:
        return clean_fields(f"«{kind}» nodes of {diagram.type} diagrams", defined, raw)
    except FieldError as exc:
        raise OperationError(str(exc)) from exc


def _edge_fields(diagram: Diagram, kind: str, raw: Any) -> dict[str, str | None]:
    if raw is None:
        return {}
    profile = profile_of(diagram.type)
    defined = profile.fields_of_edge(kind) if profile else ()
    try:
        return clean_fields(f"«{kind}» edges of {diagram.type} diagrams", defined, raw)
    except FieldError as exc:
        raise OperationError(str(exc)) from exc


def _check_reserved(diagram: Diagram, used: list[str], edge: bool) -> None:
    """`properties` and `remove_properties` never set the keys that typed fields own: those go through `fields`."""
    profile = profile_of(diagram.type)
    if profile is None:
        return
    keys = profile.reserved_edge_keys() if edge else profile.reserved_node_keys()
    problem = reserved_key_error(keys, used, f"{diagram.type} diagrams")
    if problem:
        raise OperationError(problem)


def _store_fields(properties: dict[str, str], values: dict[str, str | None]) -> None:
    for name, text in values.items():
        if text is None:
            properties.pop(name, None)
        else:
            properties[name] = text


def _drop_foreign_fields(
    diagram: Diagram, properties: dict[str, str], old_kind: str, new_kind: str, edge: bool
) -> None:
    """A kind change removes the stored values of fields the new kind does not have."""
    profile = profile_of(diagram.type)
    if profile is None:
        return
    own = profile.fields_of_edge if edge else profile.fields_of_node
    keep = {f.name for f in own(new_kind)}
    for spec in own(old_kind):
        if spec.name not in keep:
            properties.pop(spec.name, None)


def _touch(diagram: Diagram) -> None:
    diagram.updated_at = now_iso()


# ---------------------------------------------------------------------------
# Node operations
# ---------------------------------------------------------------------------


def add_node(
    diagram: Diagram,
    name: str,
    kind: str | None = None,
    id: str | None = None,
    parent: str | None = None,
    properties: dict[str, PropertyValue] | None = None,
    description: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Node:
    if not name or not name.strip():
        raise OperationError("A node needs a non-empty name.")
    note = clean_description(description) or None
    kind = _check_kind(diagram, kind if kind is not None else _default_node_kind(diagram))
    parent_id = _require_container(diagram, parent).id if parent else None
    node_id = id.strip() if id and id.strip() else diagram.unique_id(name)  # a blank id counts as none
    if node_id in diagram.nodes:
        raise OperationError(f"A node with id '{node_id}' already exists ({diagram.path(node_id)}).")
    _check_reserved(diagram, [str(k) for k in properties or {}], edge=False)
    values = _node_fields(diagram, kind, fields)
    node = Node(
        id=node_id,
        name=name.strip(),
        kind=kind,
        parent=parent_id,
        properties={str(k): str(v) for k, v in (properties or {}).items()},
        description=note,
    )
    _store_fields(node.properties, values)
    _check_node(diagram, node)
    diagram.nodes[node.id] = node
    _touch(diagram)
    return node


def update_node(
    diagram: Diagram,
    id: str,
    name: str | None = None,
    kind: str | None = None,
    properties: dict[str, PropertyValue] | None = None,
    remove_properties: list[str] | None = None,
    description: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Node:
    node = _require_node(diagram, id)
    note = clean_description(description)  # validated before anything changes
    _check_reserved(diagram, [str(k) for k in (properties or {})] + _check_keys(remove_properties), edge=False)
    values = _node_fields(diagram, _check_kind(diagram, kind) if kind is not None else node.kind, fields)
    if name is not None and name.strip():
        _check_linked_rename(node.id, node.ref, node.name, name)
        node.name = name.strip()
    if kind is not None:
        new_kind = _check_kind(diagram, kind)
        if new_kind not in kinds_for(diagram.type).container_kinds and diagram.children(node.id):
            raise OperationError(
                f"Cannot change '{node.id}' to leaf kind '{new_kind}': it still contains "
                f"{len(diagram.children(node.id))} node(s). Move them out first."
            )
        _check_kind_change(diagram, node, new_kind)
        # A linked node may only become a kind that can stand for every element kind its current kind can: the
        # linked element's kind is one of those (the link checked it), and it is not known here.
        if node.ref is not None and not set(linkable_kinds(diagram.type, node.kind)) <= set(
            linkable_kinds(diagram.type, new_kind)
        ):
            raise OperationError(
                f"'{node.id}' represents the project element '{node.ref}', and a «{new_kind}» may not be able to "
                f"represent it. Unlink '{node.id}' first (unlink_element), or choose a kind like «{node.kind}»."
            )
        _drop_foreign_fields(diagram, node.properties, node.kind, new_kind, edge=False)
        node.kind = new_kind
    for key in remove_properties or []:
        node.properties.pop(str(key), None)
    for key, value in (properties or {}).items():
        node.properties[str(key)] = str(value)
    _store_fields(node.properties, values)
    if values:
        _check_node(diagram, node)  # a field may break a rule of the type (a port an edge names)
    if note is not None:
        node.description = note or None
    _touch(diagram)
    return node


def move_node(diagram: Diagram, id: str, parent: str | None) -> Node:
    node = _require_node(diagram, id)
    if parent is None or parent == "":
        _check_node(diagram, node.model_copy(update={"parent": None}))  # e.g. a region cannot become top level
        node.parent = None
        _touch(diagram)
        return node
    container = _require_container(diagram, parent)
    if container.id == node.id or container.id in {d.id for d in diagram.descendants(node.id)}:
        raise OperationError(f"Cannot move '{node.id}' into '{container.id}': that would create a containment cycle.")
    _check_node(diagram, node.model_copy(update={"parent": container.id}))
    node.parent = container.id
    _touch(diagram)
    return node


def remove_node(diagram: Diagram, id: str, cascade: bool = False) -> dict[str, Any]:
    node = _require_node(diagram, id)
    removed = [node.id]
    children = diagram.children(node.id)
    if cascade:
        for descendant in diagram.descendants(node.id):
            removed.append(descendant.id)
    else:
        for child in children:
            child.parent = node.parent
    removed_set = set(removed)
    for node_id in removed:
        diagram.nodes.pop(node_id, None)
    removed_edges = [e.id for e in diagram.edges if e.source in removed_set or e.target in removed_set]
    diagram.edges = [e for e in diagram.edges if e.id not in set(removed_edges)]
    if not cascade:  # what moved up must still fit where it landed (a region cannot become top level)
        for child in children:
            try:
                _check_node(diagram, child)
            except OperationError as exc:
                raise OperationError(
                    f"Cannot remove '{node.id}' and keep its content: {exc} Move the content out first, or use cascade."
                ) from exc
    _touch(diagram)
    return {
        "removed_nodes": removed,
        "removed_edges": removed_edges,
        "reparented": [c.id for c in children] if not cascade else [],
    }


# ---------------------------------------------------------------------------
# Edge operations
# ---------------------------------------------------------------------------


def add_edge(
    diagram: Diagram,
    source: str,
    target: str,
    label: str | None = None,
    kind: str | None = None,
    direction: str | None = None,
    properties: dict[str, PropertyValue] | None = None,
    guard: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Edge:
    src = _require_node(diagram, source, "source")
    tgt = _require_node(diagram, target, "target")
    kind = _check_edge_kind(diagram, kind if kind is not None else _default_edge_kind(diagram))
    direction = _check_direction(direction) if direction is not None else None
    props = _check_properties(properties)
    _check_reserved(diagram, list(props), edge=True)
    if guard is not None:
        guard = _check_guard(diagram, guard)
        props.pop("guard", None)
        if guard:
            props["guard"] = guard
    values = _edge_fields(diagram, kind, fields)
    label = label.strip() if label and label.strip() else None
    _store_fields(props, values)
    _check_edge(
        diagram,
        Edge(id="", source=src.id, target=tgt.id, label=label, kind=kind, direction=direction, properties=props),
    )
    for existing in diagram.edges:
        if (
            existing.source == src.id
            and existing.target == tgt.id
            and existing.kind == kind
            and existing.label == label
            and existing.direction == direction
        ):
            if props and any(existing.properties.get(k) != v for k, v in props.items()):
                existing.properties.update(props)
                _touch(diagram)
            return existing  # idempotent
    edge = Edge(
        id=diagram.next_edge_id(),
        source=src.id,
        target=tgt.id,
        label=label,
        kind=kind,
        direction=direction,
        properties=props,
    )
    diagram.edges.append(edge)
    _touch(diagram)
    return edge


def update_edge(
    diagram: Diagram,
    id: str,
    label: str | None = None,
    kind: str | None = None,
    direction: str | None = None,
    properties: dict[str, PropertyValue] | None = None,
    remove_properties: list[str] | None = None,
    guard: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Edge:
    edge = next((e for e in diagram.edges if e.id == id), None)
    if edge is None:
        known = ", ".join(e.id for e in diagram.edges) or "(none)"
        raise OperationError(f"Unknown edge '{id}'. Known edge ids: {known}")
    # Validate everything before the first assignment, so a rejected call changes nothing.
    new_kind = _check_edge_kind(diagram, kind) if kind is not None else None
    new_direction = _check_direction(direction) if direction is not None else None
    props = _check_properties(properties)
    removed = _check_keys(remove_properties)
    _check_reserved(diagram, list(props) + removed, edge=True)
    new_guard = _check_guard(diagram, guard) if guard is not None else None
    values = _edge_fields(diagram, new_kind or edge.kind, fields)
    candidate = edge.model_copy(
        update={
            "kind": new_kind or edge.kind,
            "label": (label.strip() or None) if label is not None else edge.label,
            "direction": new_direction if direction is not None else edge.direction,
            "properties": dict(edge.properties),
        }
    )
    for key in removed:
        candidate.properties.pop(key, None)
    candidate.properties.update(props)
    if new_guard is not None:
        candidate.properties.pop("guard", None)
        if new_guard:
            candidate.properties["guard"] = new_guard
    _store_fields(candidate.properties, values)
    _check_edge(diagram, candidate)
    if new_kind is not None and edge.ref is not None:
        old_shows, new_shows = (set(relation_kinds_for(diagram.type, k)) for k in (edge.kind, new_kind))
        if not old_shows <= new_shows:
            raise OperationError(
                f"Edge '{edge.id}' shows the model relation '{edge.ref}', and a «{new_kind}» edge may not be able to "
                f"show it. Unlink it first (unlink_relation), or keep a kind like «{edge.kind}»."
            )
    if label is not None:
        edge.label = label.strip() or None
    if new_kind is not None:
        _drop_foreign_fields(diagram, edge.properties, edge.kind, new_kind, edge=True)
        edge.kind = new_kind
    if direction is not None:
        edge.direction = new_direction
    for key in removed:
        edge.properties.pop(key, None)
    edge.properties.update(props)
    if new_guard is not None:
        edge.properties.pop("guard", None)
        if new_guard:
            edge.properties["guard"] = new_guard
    _store_fields(edge.properties, values)
    _touch(diagram)
    return edge


def remove_edge(
    diagram: Diagram,
    id: str | None = None,
    source: str | None = None,
    target: str | None = None,
) -> list[str]:
    if id:
        matches = [e for e in diagram.edges if e.id == id]
        if not matches:
            raise OperationError(f"Unknown edge '{id}'.")
    elif source and target:
        src = _require_node(diagram, source, "source")
        tgt = _require_node(diagram, target, "target")
        matches = [e for e in diagram.edges if e.source == src.id and e.target == tgt.id]
        if not matches:
            raise OperationError(f"No edge from '{src.id}' to '{tgt.id}'.")
    else:
        raise OperationError("remove_edge needs either an edge id or both source and target.")
    ids = {e.id for e in matches}
    diagram.edges = [e for e in diagram.edges if e.id not in ids]
    _touch(diagram)
    return sorted(ids)


def rename_diagram(diagram: Diagram, title: str) -> Diagram:
    if not title or not title.strip():
        raise OperationError("The diagram title must not be empty.")
    diagram.title = title.strip()
    _touch(diagram)
    return diagram


def set_direction(diagram: Diagram, direction: str) -> dict[str, str]:
    """Layout direction of the rendered flowchart; `TD` is accepted as Mermaid's alias of `TB`."""
    value = str(direction).strip().upper()
    value = "TB" if value == "TD" else value
    if value not in DIRECTIONS:
        raise OperationError(f"Unknown direction '{direction}'. Allowed: {', '.join(DIRECTIONS)}")
    diagram.direction = value
    _touch(diagram)
    return {"direction": value}


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

MAX_BATCH = 50


def apply_batch(diagram: Diagram, operations: list[Any]) -> dict[str, Any]:
    """Apply `operations` ([{"op": ..., "args": {...}}, ...]) in order, all or nothing.

    The shape of the whole list is checked before the first step. If a step fails, the diagram is
    restored to its state before the batch: an OperationError is re-raised naming the step, any other
    exception is re-raised unchanged.
    """
    if not isinstance(operations, list):
        raise OperationError("apply_batch needs `operations`, a list of {op, args} objects.")
    if not operations:
        raise OperationError("apply_batch needs at least one operation.")
    if len(operations) > MAX_BATCH:
        raise OperationError(f"apply_batch takes at most {MAX_BATCH} operations, got {len(operations)}.")
    steps: list[tuple[str, dict[str, Any]]] = []
    for i, item in enumerate(operations, start=1):
        if not isinstance(item, dict):
            raise OperationError(f"Batch step {i} must be an object with `op` and `args`.")
        op = item.get("op")
        if not isinstance(op, str) or not op:
            raise OperationError(f"Batch step {i} needs an 'op' (one of: {', '.join(_batchable(diagram))}).")
        if op == "apply_batch":
            raise OperationError("apply_batch cannot be nested.")
        if op in INTERNAL_OPERATIONS:
            raise OperationError(
                f"'{op}' cannot be part of a batch: links to project elements are made by the project model."
            )
        if op not in OPERATIONS:
            raise OperationError(f"Unknown operation '{op}' in step {i}. Available: {', '.join(_batchable(diagram))}")
        if op not in TYPE_OPERATIONS[diagram.type]:
            raise OperationError(
                f"Operation '{op}' in step {i} is not available for {diagram.type} diagrams. "
                f"Available: {', '.join(_batchable(diagram))}"
            )
        args = item.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise OperationError(f"The args of step {i} must be an object.")
        steps.append((op, args))

    snapshot = diagram.model_copy(deep=True)
    results: list[dict[str, Any]] = []
    try:
        for i, (op, args) in enumerate(steps, start=1):
            try:
                outcome = apply(diagram, op, args)
            except OperationError as exc:
                raise OperationError(f"Batch step {i} ({op}) failed: {exc} No step of the batch was applied.") from exc
            results.append({"op": op, "result": outcome["result"], "touched": outcome["touched"]})
    except BaseException:
        _rollback(diagram, snapshot)
        raise
    touched: list[str] = []
    for step in results:
        for node_id in step["touched"]:
            if diagram.has_element(node_id) and node_id not in touched:
                touched.append(node_id)
    return {"results": results, "touched": touched}


def batch_operations(diagram_type: str) -> list[str]:
    """The operations a batch step may name (REST and the model's `apply_batch` tool alike): the type's operations
    without `apply_batch` and the internal ones (spec: shared-project-model.md)."""
    return [n for n in TYPE_OPERATIONS[diagram_type] if n != "apply_batch" and n not in INTERNAL_OPERATIONS]


def _batchable(diagram: Diagram) -> list[str]:
    return batch_operations(diagram.type)


# ---------------------------------------------------------------------------
# The shared project model (spec: docs/architecture/specs/shared-project-model.md, ADR-0070/0071)
# ---------------------------------------------------------------------------


def _check_linked_rename(view_id: str, ref: str | None, current: str, new_name: str) -> None:
    """A linked view element carries its element's name: renaming it in a view is refused (ADR-0071)."""
    if ref is not None and new_name.strip() != single_line(current).strip():  # stored names may predate #103
        raise OperationError(
            f"'{view_id}' represents the project element '{ref}', so its name comes from the project model. "
            f"Rename the element (update_element) and every diagram follows, or unlink '{view_id}' first."
        )


def _view_target(diagram: Diagram, ref: str) -> Node | Lifeline:
    view = find_view(diagram, ref)
    if view is None:
        if diagram.interaction is not None:
            known = ", ".join(lf.id for lf in diagram.interaction.lifelines) or "none"
            raise OperationError(f"Unknown lifeline '{ref}'. Known lifelines: {known}")
        raise OperationError(f"Unknown node '{ref}'. Known nodes: {', '.join(sorted(diagram.nodes)) or 'none'}")
    if diagram.interaction is not None:
        return next(lf for lf in diagram.interaction.lifelines if lf.id == view.id)
    return diagram.nodes[view.id]


def link_element(diagram: Diagram, id: str, element: str, name: str, element_kind: str) -> dict[str, Any]:
    """Let a node or lifeline represent a project element: it gets `ref` and the element's name. Internal: the
    model service passes the element's name and kind (spec: shared-project-model.md)."""
    target = _view_target(diagram, id)
    view_kind = LIFELINE if isinstance(target, Lifeline) else target.kind
    allowed = linkable_kinds(diagram.type, view_kind)
    if not allowed:
        raise OperationError(
            f"'{target.id}' («{view_kind}») cannot represent a project element: only participants and structure "
            "(nodes of deployment diagrams, activity partitions and data nodes, lifelines) can be linked."
        )
    if element_kind not in allowed:
        raise OperationError(
            f"A «{view_kind}» cannot represent the {element_kind} element '{element}'; it may represent "
            f"{', '.join(allowed)} elements."
        )
    if not element.strip() or not name.strip():
        raise OperationError("link_element needs the element's id and name.")
    if slugify(element) != element:
        raise OperationError(f"Invalid element id '{element}' (lowercase letters, digits and '-').")
    renamed_from = target.name if target.name != name.strip() else None
    unlinked = _unlink_connections(diagram, [target.id]) if target.ref != element else []
    target.ref, target.name = element, name.strip()
    _touch(diagram)
    return {
        "id": target.id,
        "element": element,
        "name": target.name,
        "renamed_from": renamed_from,
        "touched": [target.id],
        **({"unlinked_relations": unlinked} if unlinked else {}),
    }


def unlink_element(diagram: Diagram, id: str) -> dict[str, Any]:
    """The node or lifeline no longer represents a project element; it keeps its name."""
    target = _view_target(diagram, id)
    if target.ref is None:
        raise OperationError(f"'{target.id}' does not represent a project element.")
    element, target.ref = target.ref, None
    unlinked = _unlink_connections(diagram, [target.id])
    _touch(diagram)
    return {
        "id": target.id,
        "element": element,
        "touched": [target.id],
        **({"unlinked_relations": unlinked} if unlinked else {}),
    }


def _linked(diagram: Diagram, element: str) -> list[Node | Lifeline]:
    if diagram.interaction is not None:
        return [lf for lf in diagram.interaction.lifelines if lf.ref == element]
    return [n for n in diagram.nodes.values() if n.ref == element]


def sync_element(diagram: Diagram, element: str, name: str) -> dict[str, Any]:
    """Give every view of the element its (new) name. Internal: the propagation of a model rename."""
    if not name.strip():
        raise OperationError("sync_element needs the element's name.")
    renamed = [view for view in _linked(diagram, element) if view.name != name.strip()]
    for view in renamed:
        view.name = name.strip()
    if renamed:
        _touch(diagram)
    return {"element": element, "name": name.strip(), "touched": [v.id for v in renamed]}


def clear_element(diagram: Diagram, element: str) -> dict[str, Any]:
    """Unlink every view of the element (it is being removed from the model, ADR-0072). Internal."""
    views = _linked(diagram, element)
    for view in views:
        view.ref = None
    unlinked = _unlink_connections(diagram, [v.id for v in views])
    if views:
        _touch(diagram)
    return {
        "element": element,
        "touched": [v.id for v in views],
        **({"unlinked_relations": unlinked} if unlinked else {}),
    }


# ---------------------------------------------------------------------------
# Relations of the shared project model (spec: docs/architecture/specs/model-relations.md, ADR-0139)
# ---------------------------------------------------------------------------


def _connections(diagram: Diagram) -> list[Edge | Message]:
    if diagram.interaction is not None:
        return list(messages(diagram.interaction))
    return list(diagram.edges) if diagram.type in ("deployment", "component") else []


def _ends(connection: Edge | Message) -> tuple[str | None, str | None]:
    if isinstance(connection, Message):
        return connection.sender, connection.receiver
    return connection.source, connection.target


def _unlink_connections(diagram: Diagram, view_ids: list[str]) -> list[str]:
    """Clear the relation links of the connections touching these views (their ends changed); their ids."""
    views = set(view_ids)
    unlinked = [c for c in _connections(diagram) if c.ref is not None and views & set(_ends(c))]
    for connection in unlinked:
        connection.ref = None
    return [c.id for c in unlinked]


def _end_refs(diagram: Diagram, connection: Edge | Message) -> tuple[str | None, str | None]:
    """The elements the views at the connection's ends stand for."""
    source, target = _ends(connection)
    if diagram.interaction is not None:
        refs = {lf.id: lf.ref for lf in diagram.interaction.lifelines}
    else:
        refs = {n.id: n.ref for n in diagram.nodes.values()}
    return refs.get(source or ""), refs.get(target or "")


def _view_kind(connection: Edge | Message) -> str:
    return MESSAGE if isinstance(connection, Message) else connection.kind


def _connection(diagram: Diagram, id: str) -> Edge | Message:
    found = next((c for c in _connections(diagram) if c.id == id), None)
    if found is None:
        what = "message" if diagram.interaction is not None else "edge"
        known = ", ".join(c.id for c in _connections(diagram)) or "none"
        raise OperationError(f"Unknown {what} '{id}'. Known {what}s: {known}")
    return found


def _fits(diagram: Diagram, connection: Edge | Message, kind: str, source: str, target: str) -> bool:
    a, b = _end_refs(diagram, connection)
    return kind in relation_kinds_for(diagram.type, _view_kind(connection)) and ends_fit(kind, source, target, a, b)


def link_relation(diagram: Diagram, id: str, relation: str, kind: str, source: str, target: str) -> dict[str, Any]:
    """Let an edge or message show a model relation. Internal: the model service passes the relation's kind and
    ends (spec: model-relations.md)."""
    connection = _connection(diagram, id)
    if not relation.strip() or slugify(relation) != relation:
        raise OperationError(f"Invalid relation id '{relation}' (lowercase letters, digits and '-').")
    shows = relation_kinds_for(diagram.type, _view_kind(connection))
    if kind not in shows:
        what = "a message" if isinstance(connection, Message) else f"a «{connection.kind}» edge"
        raise OperationError(
            f"'{connection.id}' is {what}, which cannot show a {kind} relation (it can show: {', '.join(shows) or 'none'})."
        )
    if not _fits(diagram, connection, kind, source, target):
        a, b = _end_refs(diagram, connection)
        way = "from source to target" if kind in ORIENTED_RELATIONS else "in either direction"
        raise OperationError(
            f"The ends of '{connection.id}' must stand for the relation's elements '{source}' and '{target}' "
            f"({way}); they stand for {a or 'nothing'} and {b or 'nothing'}. Link the nodes or lifelines first."
        )
    connection.ref = relation
    _touch(diagram)
    return {"id": connection.id, "relation": relation, "touched": [connection.id]}


def unlink_relation(diagram: Diagram, id: str) -> dict[str, Any]:
    """The edge or message no longer shows a model relation; it stays as it is drawn."""
    connection = _connection(diagram, id)
    if connection.ref is None:
        raise OperationError(f"'{connection.id}' does not show a model relation.")
    relation, connection.ref = connection.ref, None
    _touch(diagram)
    return {"id": connection.id, "relation": relation, "touched": [connection.id]}


def sync_relation(diagram: Diagram, relation: str, kind: str, source: str, target: str) -> dict[str, Any]:
    """Clear the links of the relation that no longer fit its (changed) kind or ends. Internal."""
    stale = [c for c in _connections(diagram) if c.ref == relation and not _fits(diagram, c, kind, source, target)]
    for connection in stale:
        connection.ref = None
    if stale:
        _touch(diagram)
    return {"relation": relation, "touched": [c.id for c in stale]}


def clear_relation(diagram: Diagram, relation: str) -> dict[str, Any]:
    """Clear every link of the relation and its hidden marker (it is being removed from the model). Internal."""
    linked = [c for c in _connections(diagram) if c.ref == relation]
    for connection in linked:
        connection.ref = None
    hidden = relation in diagram.hidden_relations
    if hidden:
        diagram.hidden_relations = [r for r in diagram.hidden_relations if r != relation]
    if linked or hidden:
        _touch(diagram)
    return {"relation": relation, "touched": [c.id for c in linked], "unhidden": hidden}


def set_completeness(diagram: Diagram, kinds: list[str]) -> dict[str, Any]:
    """Declare the diagram complete for these relation kinds (`["all"]`: every kind the type can show; `[]`: open
    world again). Only then does a relation it leaves out become a finding (ADR-0137)."""
    shown = shown_relation_kinds(diagram.type)
    wanted = list(shown) if [k.strip().lower() for k in kinds] == ["all"] else [k.strip() for k in kinds]
    unknown = [k for k in wanted if k not in shown]
    if unknown:
        raise OperationError(
            f"A {diagram.type} diagram cannot declare {', '.join(unknown)} complete; it can show: {', '.join(shown)} "
            "(or use ['all'], or [] for none)."
        )
    diagram.complete = sorted(set(wanted))
    _touch(diagram)
    return {"complete": diagram.complete}


def set_relation_hidden(diagram: Diagram, relation: str, hidden: bool) -> dict[str, Any]:
    """Mark a relation as intentionally left out of this diagram, or not any more. Internal: the model service checks
    that the relation exists."""
    if not relation.strip() or slugify(relation) != relation:
        raise OperationError(f"Invalid relation id '{relation}' (lowercase letters, digits and '-').")
    marks = set(diagram.hidden_relations)
    marks = marks | {relation} if hidden else marks - {relation}
    diagram.hidden_relations = sorted(marks)
    _touch(diagram)
    return {"relation": relation, "hidden": hidden}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


OPERATIONS: dict[str, Callable[..., Any]] = {
    "add_node": add_node,
    "update_node": update_node,
    "move_node": move_node,
    "remove_node": remove_node,
    "add_edge": add_edge,
    "update_edge": update_edge,
    "remove_edge": remove_edge,
    "rename_diagram": rename_diagram,
    "set_direction": set_direction,
    "apply_batch": apply_batch,
    "link_element": link_element,
    "unlink_element": unlink_element,
    "sync_element": sync_element,
    "clear_element": clear_element,
    "link_relation": link_relation,
    "unlink_relation": unlink_relation,
    "sync_relation": sync_relation,
    "clear_relation": clear_relation,
    "set_completeness": set_completeness,
    "set_relation_hidden": set_relation_hidden,
}

# Operations on a diagram's links to the shared project model (every diagram type). The internal ones take the
# element's name/kind from the caller, so only the model service may run them: the REST `/ops` route, batches,
# the model's tool calls and proposal replays refuse them (ADR-0071).
MODEL_OPERATIONS = ("link_element", "unlink_element", "sync_element", "clear_element")
# Links of edges and messages to model relations (deployment and sequence diagrams; spec: model-relations.md).
RELATION_OPERATIONS = ("link_relation", "unlink_relation", "sync_relation", "clear_relation", "set_relation_hidden")
INTERNAL_OPERATIONS = frozenset(
    {
        "link_element",
        "sync_element",
        "clear_element",
        "link_relation",
        "sync_relation",
        "clear_relation",
        "set_relation_hidden",
    }
)

# The operations each diagram type offers (spec: docs/architecture/specs/diagram-types.md). Every name is
# a key of OPERATIONS; `apply` rejects the operations of other types.
_GRAPH_OPERATIONS = (
    "add_node",
    "update_node",
    "move_node",
    "remove_node",
    "add_edge",
    "update_edge",
    "remove_edge",
    "rename_diagram",
    "set_direction",
    "apply_batch",
    *MODEL_OPERATIONS,
)
TYPE_OPERATIONS: dict[str, tuple[str, ...]] = {
    "deployment": (*_GRAPH_OPERATIONS, *RELATION_OPERATIONS, "set_completeness"),
    "activity": _GRAPH_OPERATIONS,  # ActivityNodes/ActivityPartitions and ActivityEdges (ADR-0034)
    "state_machine": _GRAPH_OPERATIONS,  # states, pseudostates, regions and transitions (ADR-0178)
    "class": _GRAPH_OPERATIONS,  # classifiers with members, packages and relationships (ADR-0179)
    "use_case": _GRAPH_OPERATIONS,  # actors, use cases, the subject and their relationships (ADR-0180)
    # components, subsystems, interfaces and their connections (ADR-0181); connectors show relations like edges do
    "component": (*_GRAPH_OPERATIONS, *RELATION_OPERATIONS, "set_completeness"),
    "sequence": (  # UML 2.5.1 Interactions; the functions live in sequence_ops.py (registered below)
        "add_lifeline",
        "update_lifeline",
        "move_lifeline",
        "remove_lifeline",
        "add_message",
        "update_message",
        "add_combined_fragment",
        "update_combined_fragment",
        "add_interaction_operand",
        "update_interaction_operand",
        "add_interaction_use",
        "update_interaction_use",
        "add_execution_specification",
        "add_destruction_occurrence_specification",
        "move_fragment",
        "remove_fragment",
        "rename_diagram",
        "apply_batch",
        *MODEL_OPERATIONS,
        *RELATION_OPERATIONS,
        "set_completeness",
    ),
}


# ---------------------------------------------------------------------------
# Argument validation (spec: docs/architecture/specs/operation-arguments.md, ADR-0045)
# ---------------------------------------------------------------------------


@cache
def _parameters(func: Callable[..., Any]) -> tuple[dict[str, Any], frozenset[str]]:
    """The keyword parameters of an operation (without `diagram`) with their types, and the required ones."""
    hints = typing.get_type_hints(func)
    params = list(inspect.signature(func).parameters.values())[1:]
    types_ = {p.name: hints.get(p.name, Any) for p in params}
    return types_, frozenset(p.name for p in params if p.default is inspect.Parameter.empty)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int | float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__  # pragma: no cover - not produced by JSON


def _matches(value: Any, expected: Any) -> bool:
    if expected is Any:
        return True
    origin = typing.get_origin(expected)
    if origin in (Union, types.UnionType):
        return any(_matches(value, member) for member in typing.get_args(expected))
    if expected is type(None):
        return value is None
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected is str:
        return isinstance(value, str)
    if origin is list:
        (item,) = typing.get_args(expected) or (Any,)
        return isinstance(value, list) and all(_matches(v, item) for v in value)
    if origin is dict:
        key, item = typing.get_args(expected) or (Any, Any)
        return isinstance(value, dict) and all(_matches(k, key) and _matches(v, item) for k, v in value.items())
    raise TypeError(f"Unsupported parameter type {expected!r}")  # pragma: no cover - a new operation's annotation


def _describe(expected: Any) -> str:
    """What a value of the parameter type looks like in JSON terms, for the error message."""
    if expected is Any:
        return "any value"
    origin = typing.get_origin(expected)
    if origin in (Union, types.UnionType):
        members = [m for m in typing.get_args(expected) if m is not type(None)]
        text = " or ".join(_describe(m) for m in members)
        return f"{text} or null" if len(members) < len(typing.get_args(expected)) else text
    if expected is bool:
        return "true or false"
    if expected in (int, float):
        return "a number"
    if expected is str:
        return "a string"
    if origin is list:
        (item,) = typing.get_args(expected) or (Any,)
        return "a list of strings" if item is str else "a list"
    if origin is dict:
        return "an object of string, number or boolean values"
    return str(expected)  # pragma: no cover


# Arguments that are names, titles or labels: one line without control characters (#103, ADR-0160), normalised in
# `validate_arguments` for every diagram and project operation, so the model and the UI read back what is shown.
ONE_LINE_ARGUMENTS = frozenset({"name", "title", "label", "guard", "type", "refers_to"})


def check_arguments(op: str, args: Any) -> dict[str, Any]:
    """Validate `args` against the signature of operation `op` before anything is changed.

    Rejects a non-object, unknown and missing arguments and values of the wrong JSON type with an
    `OperationError` naming the argument; values (kinds, ids, directions) are checked by the operation.
    Returns the arguments to call it with: `ONE_LINE_ARGUMENTS` made `single_line` (#103).
    """
    return validate_arguments(OPERATIONS[op], op, args)


def validate_arguments(
    func: Callable[..., Any], op: str, args: Any, error: type[OperationError] = OperationError
) -> dict[str, Any]:
    """`check_arguments` for any operation function whose first parameter is the object it changes
    (also the project operations, `arcviz/projects.py`); raises `error`."""
    if not isinstance(args, dict):
        raise error(f"Invalid arguments for {op}: expected an object of named arguments, got {_json_type(args)}.")
    expected, required = _parameters(func)
    unknown = [name for name in args if name not in expected]
    if unknown:
        raise error(
            f"Invalid arguments for {op}: unknown argument {', '.join(repr(u) for u in unknown)}. "
            f"Allowed: {', '.join(expected)}."
        )
    for name, value in args.items():
        if not _matches(value, expected[name]):
            raise error(
                f"Invalid arguments for {op}: '{name}' must be {_describe(expected[name])}, got {_json_type(value)}."
            )
    missing = [name for name in expected if name in required and name not in args]
    if missing:
        raise error(f"Invalid arguments for {op}: missing required argument {', '.join(repr(m) for m in missing)}.")
    return {
        name: single_line(v) if name in ONE_LINE_ARGUMENTS and isinstance(v, str) else v for name, v in args.items()
    }


def _rollback(diagram: Diagram, snapshot: Diagram) -> None:
    """Put every model field back to the state before the failed operation."""
    for field in type(diagram).model_fields:
        setattr(diagram, field, getattr(snapshot, field))


def apply(diagram: Diagram, op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Apply a named operation. Returns a JSON-able result including the touched node ids.

    Raises OperationError on invalid input; the diagram is left unchanged in that case. Any other
    exception is an internal defect: it propagates unchanged, but the diagram is restored as well, so
    no caller can persist a half-applied operation.
    """
    func = OPERATIONS.get(op)
    available = TYPE_OPERATIONS[diagram.type]
    if func is None:
        raise OperationError(f"Unknown operation '{op}'. Available: {', '.join(available)}")
    if op not in available:
        raise OperationError(
            f"Operation '{op}' is not available for {diagram.type} diagrams. Available: {', '.join(available)}"
        )
    args = check_arguments(op, args)
    snapshot = diagram.model_copy(deep=True)
    try:
        result = func(diagram, **args)
    except BaseException:
        _rollback(diagram, snapshot)
        raise

    touched: list[str] = []
    payload: Any
    if isinstance(result, Node):
        touched = [result.id]
        payload = result.model_dump()
    elif isinstance(result, Edge):
        touched = [result.source, result.target]
        payload = result.model_dump()
    elif isinstance(result, Diagram):
        payload = {"title": result.title}
    elif isinstance(result, dict):
        touched = list(result.get("touched") or result.get("reparented") or [])
        payload = result
    else:
        payload = result
    return {"ok": True, "op": op, "result": payload, "touched": touched}


# re-exported for convenience
# Sequence-diagram operations need OperationError and the helpers above, so their module is imported last.
from . import sequence_ops  # noqa: E402
from .model import slugify  # noqa: E402,F401

OPERATIONS.update(sequence_ops.OPERATIONS)
