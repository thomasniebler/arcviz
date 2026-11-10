"""Model first: diagrams are views of the project model (spec: docs/architecture/specs/model-first.md, ADR-0213..0216).

Pure functions, no store access:

- `resolve(stored, project)` turns a stored diagram document into the `Diagram` every consumer reads (renderers, rules,
  exports, the agents): a structure view (`Diagram.shows`) gets its nodes and edges from the project's elements and
  connections; a behaviour view keeps its own content, but the parts that stand for elements (lifelines, partitions,
  data stores) take their names and notes from the model.
- `decompose(edited, stored, project)` is the way back: it writes what an operation changed into the model (elements,
  connections) and returns the document to store (for structure views only the ids it shows and hides).

A diagram outside any project, and one stored before model first, is a full document: `resolve` hands it on unchanged
and the first `decompose` in a project converts it (the migration does the same at start-up, ADR-0220).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph_types import profile_of
from .model import Diagram, Edge, Node, kinds_for, slugify
from .operations import OperationError
from .project_model import (
    ELEMENT_KINDS,
    LIFELINE,
    MAX_ELEMENT_DESCRIPTION,
    MAX_ELEMENT_NAME,
    Connection,
    Element,
    adoptable,
    linkable_kinds,
    name_key,
)
from .projects import Project

# The types whose diagrams are views of structure: nodes are elements, edges are connections (ADR-0213).
STRUCTURE_TYPES = frozenset({"deployment", "class", "use_case", "component"})
# Behaviour types keep their content; parts that stand for elements are drawn with the element's name.
BEHAVIOUR_TYPES = frozenset({"activity", "state_machine", "sequence"})

# Abstract kinds of nodes that cannot stand for one of the M18 kinds (ADR-0213: every node is an element).
_EXTRA_CATEGORY = {"interface": "interface", "use_case": "use_case", "package": "package"}
# How an element of a kind is drawn in a type whose notation it has not been given yet (first kind the type knows).
_DEFAULT_NOTATION: dict[str, dict[str, tuple[str, ...]]] = {
    "deployment": {
        "actor": ("external",),
        "system": ("external",),
        "subsystem": ("group",),
        "component": ("service", "component"),
        "node": ("node",),
        "data": ("database",),
        "package": ("group",),
    },
    "component": {
        "component": ("component",),
        "system": ("subsystem",),
        "subsystem": ("subsystem",),
        "interface": ("interface",),
        "package": ("package",),
    },
    "class": {"data": ("class",), "interface": ("interface",), "package": ("package",)},
    "use_case": {"actor": ("actor",), "system": ("subject",), "subsystem": ("subject",), "use_case": ("use_case",)},
}


def is_view(diagram: Diagram) -> bool:
    """Whether a stored document is a structure view (only ids), not a full document."""
    return diagram.shows is not None


# Deployment containers that group rather than run things: packages, never execution nodes (`deployed_on` hosts).
_GROUPING = frozenset({"deployment", "vnet", "subnet", "region", "group"})


def category(diagram_type: str, view_kind: str) -> str:
    """The abstract element kind a node of this kind stands for."""
    if diagram_type == "deployment" and view_kind in _GROUPING:
        return "package"
    kinds = linkable_kinds(diagram_type, view_kind)
    if kinds:
        return kinds[0]
    if view_kind in _EXTRA_CATEGORY:
        return _EXTRA_CATEGORY[view_kind]
    catalog = kinds_for(diagram_type)
    return "node" if view_kind in catalog.container_kinds else "component"


def view_kind(element: Element, diagram_type: str) -> str | None:
    """How the element is drawn in a view of this type; None when the type cannot show it."""
    catalog = kinds_for(diagram_type)
    kinds = catalog.container_kinds | catalog.leaf_kinds
    given = element.notation.get(diagram_type)
    if given in kinds:
        return given
    for candidate in _DEFAULT_NOTATION.get(diagram_type, {}).get(element.kind, ()):
        if candidate in kinds:
            return candidate
    return None


def showable(project: Project, diagram_type: str) -> list[Element]:
    """The elements a view of this type can show."""
    return [e for e in project.elements if view_kind(e, diagram_type) is not None]


def _node(element: Element, kind: str, parent: str | None) -> Node:
    return Node(
        id=element.id,
        name=element.name,
        kind=kind,
        parent=parent,
        properties=dict(element.properties),
        ref=element.id,
        description=element.description or None,
    )


def _edge(connection: Connection, diagram_type: str) -> Edge:
    from .model import RELATION_DIAGRAM_TYPES

    return Edge(
        id=connection.id,
        source=connection.source,
        target=connection.target,
        label=connection.label,
        kind=connection.kind,
        direction=connection.direction,
        properties=dict(connection.properties),
        ref=connection.relation if diagram_type in RELATION_DIAGRAM_TYPES else None,
    )


def _reserved(project: Project) -> set[str]:
    return {e.id for e in project.elements} | {c.id for c in project.connections}


def _edge_floor(project: Project) -> int:
    numbers = [int(c.id[1:]) for c in project.connections if c.id[:1] == "e" and c.id[1:].isdigit()]
    return max([project.connection_seq, *numbers], default=0)


def shown_connections(project: Project, diagram: Diagram, shown: set[str]) -> list[Connection]:
    """The connections a structure view of `diagram.type` draws between the shown elements (ADR-0216), hidden ones
    excluded."""
    catalog = kinds_for(diagram.type)
    hidden = set(diagram.hidden_connections)
    return [
        c
        for c in project.connections
        if c.source in shown and c.target in shown and c.kind in catalog.edge_kinds and c.id not in hidden
    ]


def resolve(stored: Diagram, project: Project | None) -> Diagram:
    """The diagram as every consumer reads it: built from the model for a structure view, the behaviour content with
    the model's names otherwise. Never stored as such (`decompose` turns it back)."""
    if project is None:
        return stored
    if stored.type in STRUCTURE_TYPES and is_view(stored):
        out = _resolve_structure(stored, project)
    else:
        out = stored.model_copy(deep=True)
        _apply_names(out, project)
        out._legacy = _unconverted(stored, project)
    out._resolved = not out._legacy  # a full document (not yet in the model) can still be linked and unlinked
    out._reserved = _reserved(project)
    out._edge_floor = _edge_floor(project)
    if out.type in STRUCTURE_TYPES:
        hidden = set(stored.hidden_connections)
        out._hidden_edges = {c.id: _edge(c, out.type) for c in project.connections if c.id in hidden}
        out._pool = {
            e.id: _node(e, kind, None)
            for e in project.elements
            if e.id not in out.nodes and (kind := view_kind(e, out.type)) is not None
        }
    return out


def _resolve_structure(stored: Diagram, project: Project) -> Diagram:
    elements = {e.id: e for e in project.elements}
    shown = [i for i in dict.fromkeys(stored.shows or []) if i in elements]  # dangling ids are skipped (ADR-0218)
    catalog = kinds_for(stored.type)
    kinds = {i: k for i in shown if (k := view_kind(elements[i], stored.type)) is not None}
    out = stored.model_copy(update={"nodes": {}, "edges": [], "shows": None}, deep=True)
    containers = {i for i, k in kinds.items() if k in catalog.container_kinds}
    for element_id, kind in kinds.items():
        element = elements[element_id]
        out.nodes[element_id] = _node(element, kind, shown_ancestor(element, elements, containers))
    profile = profile_of(out.type)
    for connection in shown_connections(project, stored, set(kinds)):
        edge = _edge(connection, out.type)
        if profile is not None and profile.edge_problem(out, edge):
            continue  # a connection this type's rules do not allow between these kinds is not drawn here
        out.edges.append(edge)
    return out


def _unconverted(stored: Diagram, project: Project) -> bool:
    """A full document the migration still has to take into the model: a structure diagram not stored as a view, or
    a behaviour diagram with a part that stands for no element yet."""
    if stored.type in STRUCTURE_TYPES:
        return True
    known = {e.id for e in project.elements}

    def open_part(ref: str | None, kind: str, name: str) -> bool:
        return ref not in known and not (ref is None and _unfitting_namesake(stored.type, kind, name, project, {}))

    if stored.interaction is not None and any(
        open_part(lf.ref, LIFELINE, lf.name) for lf in stored.interaction.lifelines
    ):
        return True
    return any(adoptable(stored.type, n.kind) and open_part(n.ref, n.kind, n.name) for n in stored.nodes.values())


def shown_ancestor(element: Element, elements: dict[str, Element], shown: set[str]) -> str | None:
    """The nearest container of the element (its parent, else that one's parent, …) that the view shows: a part of a
    container the view hides sits in the next container it shows (hiding is no move, ADR-0217)."""
    seen = {element.id}
    current = element.parent
    while current is not None and current not in seen:
        if current in shown:
            return current
        seen.add(current)
        current = elements[current].parent if current in elements else None
    return None


def _apply_names(diagram: Diagram, project: Project) -> None:
    """Behaviour views: the parts that stand for elements show the element's name and note."""
    elements = {e.id: e for e in project.elements}
    for node in diagram.nodes.values():
        element = elements.get(node.ref or "")
        if element is not None:
            node.name, node.description = element.name, element.description or None
    if diagram.interaction is not None:
        for lifeline in diagram.interaction.lifelines:
            element = elements.get(lifeline.ref or "")
            if element is not None:
                lifeline.name, lifeline.description = element.name, element.description or None


@dataclass
class Decomposed:
    """What `decompose` produced: the document to store and whether the model changed (then `project` is to be
    written), plus the ids it created and removed (for the caller's report)."""

    stored: Diagram
    model_changed: bool
    created: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def decompose(edited: Diagram, previous: Diagram | None, project: Project) -> Decomposed:
    """Write what `edited` (a resolved diagram after operations) says into `project` (mutated in place) and return
    the document to store. `previous` is the stored document it was resolved from (None for a new diagram)."""
    before = project.model_dump(mode="json", exclude={"updated_at"})
    if edited.type in STRUCTURE_TYPES:
        result = _decompose_structure(edited, previous, project)
    else:
        result = _decompose_behaviour(edited, project)
    result.model_changed = project.model_dump(mode="json", exclude={"updated_at"}) != before
    from . import projects as project_ops

    if result.created and len(project.elements) > project_ops.MAX_ELEMENTS:
        raise OperationError(f"A project holds at most {project_ops.MAX_ELEMENTS} model elements.")
    return result


def _new_element_id(project: Project, base: str, taken: set[str]) -> str:
    candidate = slugify(base) or "element"
    i = 2
    unique = candidate
    while unique in taken:
        unique = f"{candidate}-{i}"
        i += 1
    return unique


def _check_name(name: str) -> str:
    name = name.strip()
    if len(name) > MAX_ELEMENT_NAME:
        raise OperationError(f"The name '{name[:40]}…' is too long for a model element (at most {MAX_ELEMENT_NAME}).")
    return name


def _element_for(
    node_id: str, name: str, ref: str | None, project: Project, claimed: dict[str, str], allow_merge: bool = True
) -> Element | None:
    """The existing element a node stands for: its id, its link, else one of the same name (the reuse rule)."""
    by_id = {e.id: e for e in project.elements}
    # A link (an M18 `ref`, `link_element`, or every node of a resolved view) names the element it stands for; the id of
    # a node in a full document is local to that document and decides nothing (only its name does, below).
    if ref and ref in by_id and claimed.get(ref, node_id) == node_id:
        return by_id[ref]
    if allow_merge:
        wanted = name_key(name)
        for element in project.elements:
            if name_key(element.name) == wanted and element.id not in claimed:
                return element
    return None


def _decompose_structure(edited: Diagram, previous: Diagram | None, project: Project) -> Decomposed:
    diagram_type = edited.type
    created: list[str] = []
    removed: list[str] = []
    claimed: dict[str, str] = {}  # element id -> the node id of `edited` that stands for it
    node_to_element: dict[str, str] = {}
    taken = _reserved(project) | set(edited.nodes)
    for node in edited.nodes.values():
        element = _element_for(node.id, node.name, node.ref, project, claimed)
        if element is None and any(
            name_key(e.name) == name_key(node.name) for e in project.elements if e.id in claimed
        ):
            raise OperationError(
                f"Another node of this diagram stands for the element '{node.name.strip()}' already; an element is "
                "shown once per diagram (names are unique in the project)."
            )
        if element is None:
            wanted = node.ref if node.ref and slugify(node.ref) == node.ref else node.id  # a link names the id
            element_id = (
                wanted
                if wanted not in taken - {node.id} and wanted not in _reserved(project)
                else (_new_element_id(project, node.name, taken))
            )
            taken.add(element_id)
            element = Element(id=element_id, name=_check_name(node.name), kind=category(diagram_type, node.kind))
            project.elements.append(element)
            created.append(element_id)
        claimed[element.id] = node.id
        node_to_element[node.id] = element.id
    by_id = {e.id: e for e in project.elements}
    catalog = kinds_for(diagram_type)
    shown_containers = {node_to_element[n.id] for n in edited.nodes.values() if n.kind in catalog.container_kinds}
    for node in edited.nodes.values():
        element = next(e for e in project.elements if e.id == node_to_element[node.id])
        name = _check_name(node.name)
        if name_key(name) != name_key(element.name):
            other = next(
                (e for e in project.elements if e.id != element.id and name_key(e.name) == name_key(name)), None
            )
            if other is not None:
                raise OperationError(
                    f"Another model element is named '{other.name}' ({other.id}); choose another name."
                )
        element.name = name
        element.notation[diagram_type] = node.kind
        if element.kind not in linkable_kinds(diagram_type, node.kind) and element.kind not in ELEMENT_KINDS[6:]:
            if linkable_kinds(diagram_type, node.kind) or node.kind in _EXTRA_CATEGORY:
                element.kind = category(diagram_type, node.kind)
        elif node.kind in _EXTRA_CATEGORY and element.kind != _EXTRA_CATEGORY[node.kind]:
            element.kind = _EXTRA_CATEGORY[node.kind]
        element.properties = dict(node.properties)
        description = (node.description or "").strip()
        if len(description) > MAX_ELEMENT_DESCRIPTION:
            raise OperationError(f"A note holds at most {MAX_ELEMENT_DESCRIPTION} characters.")
        element.description = description
        # The view draws the nearest shown container; only a different one is a move (hiding is none, ADR-0217)
        drawn = node_to_element.get(node.parent) if node.parent is not None else None
        if drawn != shown_ancestor(element, by_id, shown_containers):
            element.parent = drawn
    shown = [node_to_element[n] for n in edited.nodes]
    previously_shown = _previously_shown(previous, project)
    for element_id in previously_shown - set(shown):
        if element_id in edited._removals:
            _remove_element(project, element_id)
            removed.append(element_id)
    hidden = set(edited.hidden_connections)
    edge_ids: set[str] = set()
    connection_ids = {c.id for c in project.connections}
    for edge in edited.edges:
        source, target = node_to_element.get(edge.source), node_to_element.get(edge.target)
        if source is None or target is None:
            continue
        connection = next((c for c in project.connections if c.id == edge.id), None)
        if connection is None:
            connection_id = edge.id if edge.id not in connection_ids else _next_connection_id(project)
            connection = Connection(id=connection_id, kind=edge.kind, source=source, target=target)
            project.connections.append(connection)
            connection_ids.add(connection_id)
            created.append(connection_id)
        connection.kind, connection.source, connection.target = edge.kind, source, target
        connection.label, connection.direction = edge.label, edge.direction
        connection.properties = dict(edge.properties)
        if diagram_type in ("deployment", "component", "sequence"):
            connection.relation = edge.ref
        edge_ids.add(connection.id)
        hidden.discard(connection.id)
    if previous is not None and is_view(previous):
        # what the view drew before and does not draw now was removed in it: hidden here (ADR-0217), unless the
        # operation removed it from the model
        gone = {e.id for e in resolve(previous, project).edges} - edge_ids
        ends = {c.id: (c.source, c.target) for c in project.connections}
        gone = {i for i in gone if i in ends and ends[i][0] in shown and ends[i][1] in shown}
    elif previous is not None:
        # a full document converted into a view (ADR-0220): no view shows more than before
        gone = {c.id for c in shown_connections(project, edited, set(shown))} - edge_ids
    else:
        gone = set()  # a new view draws every connection between what it shows (ADR-0216)
    gone |= {i for i in edited._removals if i in connection_ids} - edge_ids
    for connection_id in gone:
        if connection_id in edited._removals:
            project.connections = [c for c in project.connections if c.id != connection_id]
            removed.append(connection_id)
        else:
            hidden.add(connection_id)
    alive = {c.id for c in project.connections}
    project.connection_seq = max(project.connection_seq, _edge_floor(project))
    stored = edited.model_copy(
        update={
            "nodes": {},
            "edges": [],
            "shows": shown,
            "hidden_connections": sorted(h for h in hidden if h in alive),
        },
        deep=True,
    )
    return Decomposed(stored=stored, model_changed=False, created=created, removed=removed)


def _previously_shown(previous: Diagram | None, project: Project) -> set[str]:
    if previous is None:
        return set()
    if is_view(previous):
        return set(previous.shows or [])
    return {n.ref or n.id for n in previous.nodes.values()}


def _next_connection_id(project: Project) -> str:
    n = _edge_floor(project) + 1
    taken = {c.id for c in project.connections}
    while f"e{n}" in taken:
        n += 1
    project.connection_seq = n
    return f"e{n}"


def _remove_element(project: Project, element_id: str) -> None:
    from . import projects as project_ops

    if any(e.id == element_id for e in project.elements):
        project_ops.apply(project, "remove_element", {"element": element_id})


def _unfitting_namesake(diagram_type: str, kind: str, name: str, project: Project, claimed: dict[str, str]) -> bool:
    """Whether an unlinked part's name belongs to an element it cannot stand for (or one another part stands for):
    then it is not merged (a take-back stays taken back, ADR-0128) and no second element of that name is created."""
    wanted = name_key(name)
    same = next((e for e in project.elements if name_key(e.name) == wanted), None)
    if same is None:
        return False
    return same.kind not in linkable_kinds(diagram_type, kind)


def _decompose_behaviour(edited: Diagram, project: Project) -> Decomposed:
    """Behaviour views keep their content; the parts that stand for elements are elements (created when new) and give
    them their names and notes."""
    created: list[str] = []
    removed: list[str] = []
    claimed: dict[str, str] = {}
    taken = _reserved(project)
    stored = edited.model_copy(deep=True)
    parts: list[tuple[object, str, str]] = []  # (part, view kind, default element kind)
    if stored.interaction is not None:
        for lifeline in stored.interaction.lifelines:
            parts.append((lifeline, LIFELINE, "actor" if lifeline.actor else "component"))
    for node in stored.nodes.values():
        if adoptable(stored.type, node.kind):
            parts.append((node, node.kind, category(stored.type, node.kind)))
    for part, kind, default in parts:
        part_id, name = part.id, part.name  # type: ignore[attr-defined]
        ref = part.ref  # type: ignore[attr-defined]
        if ref is None and _unfitting_namesake(stored.type, kind, name, project, claimed):
            continue  # a link taken back (the element's kind does not fit): the part stays unlinked
        element = _element_for(part_id, name, ref, project, {})  # several parts may stand for one element
        if element is None:
            element_id = _new_element_id(project, name, taken)
            taken.add(element_id)
            element = Element(id=element_id, name=_check_name(name), kind=default)
            project.elements.append(element)
            created.append(element_id)
        claimed[element.id] = part_id
        if name_key(name) != name_key(element.name):
            other = next(
                (e for e in project.elements if e.id != element.id and name_key(e.name) == name_key(name)), None
            )
            if other is not None:
                raise OperationError(
                    f"Another model element is named '{other.name}' ({other.id}); choose another name."
                )
            element.name = _check_name(name)
        description = (part.description or "").strip()  # type: ignore[attr-defined]
        if description and description != element.description:
            element.description = description[:MAX_ELEMENT_DESCRIPTION]
        part.ref = element.id  # type: ignore[attr-defined]
    for gone in edited._removals:
        if any(e.id == gone for e in project.elements):
            _remove_element(project, gone)
            removed.append(gone)
    return Decomposed(stored=stored, model_changed=False, created=created, removed=removed)
