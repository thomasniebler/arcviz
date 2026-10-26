"""The shared project model: project-wide elements and the view elements of diagrams that represent them.

Spec: docs/architecture/specs/shared-project-model.md (ADR-0070, ADR-0073). Elements live in the project
document (`Project.elements`); a deployment or activity node and a sequence lifeline may reference one through
its optional `ref`, a section through `Section.element`. This module holds the element type, the abstract
element kinds and which view elements may represent which of them, and pure queries over a diagram's
references. It changes nothing: diagram operations live in `operations.py`, project operations in
`projects.py`, the writes across documents in `services/model.py`.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, field_validator, model_serializer

from . import sequence
from .model import (
    ACTIVITY_KINDS,
    CONTAINER_KINDS,
    LEAF_KINDS,
    RELATION_DIAGRAM_TYPES,
    Diagram,
    active_kinds,
    single_line,
    slugify,
)

# Abstract kinds of project elements, independent of the diagram types' kinds (ADR-0073).
ELEMENT_KINDS = ("actor", "system", "subsystem", "component", "node", "data")
MAX_ELEMENTS = 500  # per project: keeps the project document far below Cosmos DB's 2 MB item limit
# Bounds of a new or changed element and of an imported one (#212, ADR-0153; spec model-relations.md, size budget):
# 500 elements at both bounds take at most ~0.7 MB of the project item. Never an integrity rule: a project stored
# before them keeps loading and being written.
MAX_ELEMENT_NAME = 100
MAX_ELEMENT_DESCRIPTION = 300
LIFELINE = "lifeline"  # the view kind of a sequence lifeline

# Which element kinds a view element may represent; the first is the default of "promote". Behaviour
# (activity actions, control nodes, messages) has no entry: it belongs to its diagram and is never linked.
_STRUCTURE = ("component", "system", "subsystem")
_CONTAINER = ("node", "system", "subsystem")
_EVERYTHING = ("component", "system", "subsystem", "node", "data", "actor")
_DEPLOYMENT: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(CONTAINER_KINDS, _CONTAINER),
    **dict.fromkeys(LEAF_KINDS, _STRUCTURE),
    "database": ("data", "component"),
    "storage": ("data", "component"),
    "queue": ("data", "component"),
    "external": ("system", "actor", "component"),
}
_ACTIVITY: dict[str, tuple[str, ...]] = {
    "activity_partition": ("component", "actor", "system", "subsystem", "node"),
    "data_store_node": ("data",),
    "central_buffer_node": ("data",),
}
assert set(_ACTIVITY) <= ACTIVITY_KINDS.all_kinds
# Class diagrams (ADR-0182): a classifier of the domain stands for data, an interface for a component. They are linkable
# but not adopted when created, so a domain model of many classes does not flood the project model.
_CLASS: dict[str, tuple[str, ...]] = {
    "class": ("data",),
    "enumeration": ("data",),
    "data_type": ("data",),
    "interface": ("component",),
}
# Use case diagrams (ADR-0182): an actor stands for an actor, the subject for a system; a use case is behaviour.
_USE_CASE: dict[str, tuple[str, ...]] = {"actor": ("actor",), "subject": ("system", "subsystem")}
# Component diagrams (ADR-0182): building blocks stand for the elements of the project; an interface and a package do not.
_COMPONENT: dict[str, tuple[str, ...]] = {
    "component": ("component", "system", "subsystem"),
    "subsystem": ("subsystem", "system"),
}
# Types whose view elements never stand for project elements (behaviour only, ADR-0182): a state machine describes the
# lifecycle of one thing, its states are no building blocks.
NOT_LINKED_TYPES = frozenset({"state_machine"})


# ---- code links (spec: docs/architecture/specs/agent-access.md, ADR-0171) -----------------------------------------

MAX_CODE_REFS = 20  # per element: 20 links at the bounds below take at most ~10 KB of the project item
MAX_CODE_PATH = 200
MAX_CODE_LABEL = 64  # repo label and symbol
MAX_CODE_NOTE = 200
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def normalise_code_path(path: str) -> str:
    """A repository-relative POSIX path in its one spelling (`./src//orders/` -> `src/orders`, the repository root
    -> `.`); ValueError for an empty, absolute, drive-lettered, backslashed or parent-escaping path."""
    text = path.strip()
    if not text:
        raise ValueError("a code link needs a path")
    if len(text) > MAX_CODE_PATH:
        raise ValueError(f"the path is too long ({len(text)} characters, at most {MAX_CODE_PATH})")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("the path must not contain control characters")
    if "\\" in text or text.startswith("/") or _DRIVE_RE.match(text):
        raise ValueError("the path must be repository-relative with '/' separators (no leading '/', drive or '\\')")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ValueError("the path must not contain '..'")
    return "/".join(parts) or "."


class CodeRef(BaseModel):
    """Where the code of an element lives: free text, Arcviz never reads a repository."""

    path: str  # normalised, repository-relative; a directory owns everything below it, "." the whole repository
    repo: str = ""  # optional label of the repository ("backend"); empty = any / the only one
    symbol: str = ""  # optional class, function or module inside the path
    note: str = ""

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return normalise_code_path(value)

    @field_validator("repo", "symbol", "note")
    @classmethod
    def _text(cls, value: str, info: Any) -> str:
        value = value.strip()
        limit = MAX_CODE_NOTE if info.field_name == "note" else MAX_CODE_LABEL
        if len(value) > limit:
            raise ValueError(f"the {info.field_name} is too long ({len(value)} characters, at most {limit})")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"the {info.field_name} must be one line without control characters")
        if info.field_name == "repo" and not _REPO_RE.match(value):
            raise ValueError("the repo label may only contain letters, digits, '.', '_' and '-'")
        return value

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.repo, self.symbol)


class Element(BaseModel):
    """A project-wide model element (spec: shared-project-model.md)."""

    id: str  # slug, unique in the project
    name: str
    kind: str = "component"  # one of ELEMENT_KINDS
    description: str = ""
    parent: str | None = None  # the element that contains it (e.g. a subsystem)
    code_refs: list[CodeRef] = Field(
        default_factory=list
    )  # omitted from dumps while empty (data before M30 is unchanged)

    @model_serializer(mode="wrap")
    def _omit_empty_code_refs(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if not data.get("code_refs"):
            data.pop("code_refs", None)
        return data


def owner_of_path(elements: list[Element], path: str, repo: str | None = None) -> list[tuple[Element, CodeRef]]:
    """The elements that own `path` and the link that makes them: the links with the longest matching prefix (a link to
    a directory covers everything below it, `.` everything). With `repo`, only links of that repository or without
    one count. Ties keep every owner; an invalid path has none."""
    try:
        wanted = normalise_code_path(path)
    except ValueError:
        return []
    best = -1
    found: list[tuple[Element, CodeRef]] = []
    for element in elements:
        for ref in element.code_refs:
            if repo is not None and ref.repo not in ("", repo):
                continue
            if ref.path != "." and wanted != ref.path and not wanted.startswith(ref.path + "/"):
                continue
            depth = 0 if ref.path == "." else ref.path.count("/") + 1
            if depth > best:
                best, found = depth, []
            if depth == best:
                found.append((element, ref))
    return found


# ---- relations (spec: docs/architecture/specs/model-relations.md, ADR-0135, ADR-0136) ---------------------------

RELATION_KINDS = ("communicates", "deployed_on", "data_flow")
MAX_ID_LENGTH = 64  # relation ids and the element ids at their ends, like every stored id (ports.ID_RE)
MAX_RELATIONS = 500  # per project: with the bounds below at most ~1 MB of the project item (spec: size budget)
MAX_RELATION_DESCRIPTION = 300
MAX_PROPERTY_VALUE = 64
RELATION_PROPERTIES = ("protocol", "port", "direction", "mode")
PROPERTY_VALUES: dict[str, tuple[str, ...]] = {"direction": ("forward", "both", "none"), "mode": ("sync", "async")}
_RUNS = ("component", "subsystem", "system", "data", "node")
_FLOWS = ("actor", "system", "subsystem", "component", "data")
# kind -> (source kinds, target kinds, allowed properties)
RELATION_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "communicates": (ELEMENT_KINDS, ELEMENT_KINDS, RELATION_PROPERTIES),
    "deployed_on": (_RUNS, ("node",), ()),
    "data_flow": (_FLOWS, _FLOWS, ("protocol", "port", "mode")),
}
SYMMETRIC_RELATIONS = frozenset({"communicates"})  # one relation for both orientations


class Relation(BaseModel):
    """A project-wide relation between two elements (spec: model-relations.md)."""

    id: str  # slug, unique in the project
    kind: str  # one of RELATION_KINDS
    source: str  # element id
    target: str  # element id
    properties: dict[str, str] = Field(default_factory=dict)  # keys of the kind's allowed RELATION_PROPERTIES
    description: str = ""


def relation_number(relation_id: str | None) -> int | None:
    """`n` of a generated-style id `r<n>` that fits MAX_ID_LENGTH, else None (other ids never move the counter)."""
    if not relation_id or len(relation_id) > MAX_ID_LENGTH or relation_id[:1] != "r" or not relation_id[1:].isdigit():
        return None
    return int(relation_id[1:])


def next_relation_number(seq: int, ids: list[str]) -> int | None:
    """The number of the next generated id: one above `seq` and every `r<n>` in `ids`; None when `r<n>` would no
    longer fit MAX_ID_LENGTH (then a relation needs an explicit id)."""
    n = max([seq, *(relation_number(i) or 0 for i in ids)]) + 1
    return n if len(f"r{n}") <= MAX_ID_LENGTH else None


def shorten_element_texts(elements: list[Element]) -> tuple[str | None, dict[str, str]]:
    """Bound imported names and descriptions like the operations do (#212): stripped first, then cut to
    MAX_ELEMENT_NAME / MAX_ELEMENT_DESCRIPTION ending in "…", so a project stored before the bounds, or exported with
    longer texts, can always be imported again (a backup). A cut name that equals another name (case-insensitive, as
    the operations compare) becomes "… (2)", "… (3)", …. Returns the warning that says how many were cut (None when
    nothing was) and the new names of the cut elements by id (their views follow them)."""
    names = descriptions = 0
    renamed: dict[str, str] = {}
    for element in elements:
        element.name, element.description = element.name.strip(), element.description.strip()
        if len(element.name) > MAX_ELEMENT_NAME:
            renamed[element.id] = element.name
            names += 1
        if len(element.description) > MAX_ELEMENT_DESCRIPTION:
            element.description = element.description[: MAX_ELEMENT_DESCRIPTION - 1] + "…"
            descriptions += 1
    taken = {e.name.casefold() for e in elements if e.id not in renamed}
    for element in elements:
        if element.id not in renamed:
            continue
        n, suffix = 1, "…"
        while (candidate := element.name[: MAX_ELEMENT_NAME - len(suffix)].rstrip() + suffix).casefold() in taken:
            n += 1
            suffix = f"… ({n})"
        element.name = renamed[element.id] = candidate
        taken.add(candidate.casefold())
    parts = [
        f"{n} element {what}{'s' if n != 1 else ''} longer than {limit}"
        for n, what, limit in (
            (names, "name", MAX_ELEMENT_NAME),
            (descriptions, "description", MAX_ELEMENT_DESCRIPTION),
        )
        if n
    ]
    if not parts:
        return None, renamed
    return f"{' and '.join(parts)} characters {'was' if names + descriptions == 1 else 'were'} shortened.", renamed


def name_key(name: str) -> str:
    """How element names are compared (unique per project, mapped on a carry): case-insensitive and one line, so a
    name stored before #103 equals its normalised form."""
    return single_line(name).strip().casefold()


def duplicate_element_names(elements: list[Element]) -> list[str]:
    """Problems for elements whose name another element has already (case-insensitive, like add_element)."""
    seen: dict[str, str] = {}
    problems = []
    for element in elements:
        key = name_key(element.name)
        if key in seen:
            problems.append(f"element '{element.id}' has the same name as element '{seen[key]}'")
        else:
            seen[key] = element.id
    return problems


def relation_problem(kind: str, source_kind: str, target_kind: str) -> str | None:
    """Why a relation of `kind` may not connect elements of these kinds, or None."""
    if kind not in RELATION_RULES:
        return f"Unknown relation kind '{kind}'. Available: {', '.join(RELATION_KINDS)}."
    sources, targets, _ = RELATION_RULES[kind]
    if source_kind not in sources or target_kind not in targets:
        return (
            f"A {kind} relation connects a {' / '.join(sources)} element with a {' / '.join(targets)} element, "
            f"not a {source_kind} with a {target_kind}."
        )
    return None


def property_problem(kind: str, key: str, value: str) -> str | None:
    """Why a relation of `kind` may not carry this property value, or None."""
    allowed = RELATION_RULES[kind][2] if kind in RELATION_RULES else ()
    if key not in allowed:
        return f"A {kind} relation has no property '{key}' (allowed: {', '.join(allowed) or 'none'})."
    if len(value) > MAX_PROPERTY_VALUE:
        return f"The value of '{key}' is too long ({len(value)} characters, at most {MAX_PROPERTY_VALUE})."
    if key in PROPERTY_VALUES and value not in PROPERTY_VALUES[key]:
        return f"'{key}' must be one of {', '.join(PROPERTY_VALUES[key])}, not '{value}'."
    return None


def same_relation(a: Relation, kind: str, source: str, target: str) -> bool:
    """Whether `a` already is the relation of `kind` between these ends (symmetric kinds in either orientation)."""
    if a.kind != kind:
        return False
    if (a.source, a.target) == (source, target):
        return True
    return kind in SYMMETRIC_RELATIONS and (a.source, a.target) == (target, source)


def find_relation(relations: list[Relation], ref: str | None) -> Relation | None:
    """A relation by id (case-insensitive, trimmed); None for no match."""
    if not ref or not ref.strip():
        return None
    wanted = ref.strip().lower()
    return next((r for r in relations if r.id == wanted), None)


def known_relations(relations: list[Relation]) -> str:
    """The relation list of an error message for the model or the user."""
    return ", ".join(f"{r.id} ({r.kind}: {r.source} -> {r.target})" for r in relations) or "none"


def relation_line(relation: Relation, described: bool = True) -> str:
    """One relation for a prompt: `- [r1] communicates: web -> orders {protocol=HTTPS} — description`."""
    props = ", ".join(f"{k}={v}" for k, v in sorted(relation.properties.items()))
    props = f" {{{props}}}" if props else ""
    description = f" — {relation.description}" if described and relation.description else ""
    return f"- [{relation.id}] {relation.kind}: {relation.source} -> {relation.target}{props}{description}"


# Which view connections may show which relation kinds (ADR-0139). Activity edges are behaviour: never linked.
MESSAGE = "message"  # the view kind of a sequence message
_EDGE_RELATIONS: dict[str, tuple[str, ...]] = {
    "connection": ("communicates", "data_flow"),
    "dependency": ("communicates",),
    "association": ("communicates",),
    "replication": ("communicates",),
    "deploys": ("deployed_on",),
}
_CONFIGURED_EDGE_RELATIONS = ("communicates", "data_flow")
# Component diagrams (ADR-0181): usage and realization end at an interface, which is no element: never linked.
_COMPONENT_EDGE_RELATIONS: dict[str, tuple[str, ...]] = {
    "connection": ("communicates", "data_flow"),
    "dependency": ("communicates",),
}
ORIENTED_RELATIONS = frozenset({"data_flow"})  # links keep the relation's orientation; the others either way


def relation_kinds_for(diagram_type: str, view_kind: str) -> tuple[str, ...]:
    """The relation kinds a view connection of this kind (an edge kind, or MESSAGE) may show."""
    if diagram_type == "sequence":
        return ("communicates",) if view_kind == MESSAGE else ()
    if diagram_type == "component":
        return _COMPONENT_EDGE_RELATIONS.get(view_kind, ())
    if diagram_type != "deployment":
        return ()
    if view_kind in _EDGE_RELATIONS:
        return _EDGE_RELATIONS[view_kind]
    return _CONFIGURED_EDGE_RELATIONS if view_kind in active_kinds().edge_kinds else ()


def relation_table(diagram_type: str) -> dict[str, list[str]]:
    """Every view connection kind of a diagram type that may show relations, with the kinds (what the UI needs)."""
    if diagram_type == "sequence":
        return {MESSAGE: ["communicates"]}
    if diagram_type == "component":
        return {k: list(v) for k, v in _COMPONENT_EDGE_RELATIONS.items()}
    if diagram_type != "deployment":
        return {}
    return {k: list(relation_kinds_for(diagram_type, k)) for k in sorted(active_kinds().edge_kinds)}


def shown_relation_kinds(diagram_type: str) -> tuple[str, ...]:
    """The relation kinds a diagram of this type can show (and declare complete), in RELATION_KINDS order; empty for
    the types outside `RELATION_DIAGRAM_TYPES`."""
    if diagram_type == "sequence":
        return ("communicates",)
    if diagram_type == "component":
        return ("communicates", "data_flow")
    if diagram_type in RELATION_DIAGRAM_TYPES:
        return RELATION_KINDS
    return ()


def ends_fit(kind: str, source: str, target: str, a: str | None, b: str | None) -> bool:
    """Whether a connection from a view of element `a` to one of `b` can show the relation `kind` source -> target."""
    if a is None or b is None:
        return False
    if (a, b) == (source, target):
        return True
    return kind not in ORIENTED_RELATIONS and (a, b) == (target, source)


class ViewConnection(NamedTuple):
    """A deployment edge or a sequence message as the model sees it: its ends' views and their element refs."""

    id: str
    kind: str  # the edge kind, or MESSAGE
    source: str | None  # the view at each end (None: a found or lost message)
    target: str | None
    source_ref: str | None  # the elements those views stand for
    target_ref: str | None
    ref: str | None  # the relation it shows
    label: str


def view_connections(diagram: Diagram) -> list[ViewConnection]:
    """Every connection of a deployment or sequence diagram that could show a relation (activity: none)."""
    if diagram.interaction is not None:
        refs = {lf.id: lf.ref for lf in diagram.interaction.lifelines}
        return [
            ViewConnection(
                m.id, MESSAGE, m.sender, m.receiver, refs.get(m.sender or ""), refs.get(m.receiver or ""), m.ref, m.name
            )
            for m in sequence.messages(diagram.interaction)
        ]
    if diagram.type not in ("deployment", "component"):
        return []
    return [
        ViewConnection(
            e.id,
            e.kind,
            e.source,
            e.target,
            n.ref if (n := diagram.nodes.get(e.source)) else None,
            n.ref if (n := diagram.nodes.get(e.target)) else None,
            e.ref,
            e.label or "",
        )
        for e in diagram.edges
    ]


def fits(diagram_type: str, connection: ViewConnection, relation: Relation) -> bool:
    """Whether the connection may show the relation: its kind can, and its ends stand for the relation's ends."""
    return relation.kind in relation_kinds_for(diagram_type, connection.kind) and ends_fit(
        relation.kind, relation.source, relation.target, connection.source_ref, connection.target_ref
    )


def nested(diagram: Diagram, kinds: dict[str, str]) -> list[tuple[str, str, str | None, str | None]]:
    """Nesting in a deployment diagram, for every node standing for a known element: `(view, element, holder, host)`
    with `holder` the element of the nearest linked ancestor (containment) and `host` the element of the nearest
    ancestor standing for a `node` element, through any other containers (deployed_on). Both None when absent."""
    out: list[tuple[str, str, str | None, str | None]] = []
    if diagram.type != "deployment":
        return out
    for node in diagram.nodes.values():
        if node.ref not in kinds:
            continue
        chain = [diagram.nodes[a].ref for a in diagram.ancestors(node.id) if a in diagram.nodes]
        linked = [ref for ref in chain if ref in kinds and ref != node.ref]
        holder = linked[0] if linked else None
        host = next((ref for ref in linked if kinds[ref] == "node"), None)
        if holder is not None:
            out.append((node.id, node.ref, holder, host))
    return out


class DiagramViews:
    """A diagram's connections and nesting, each computed at most once: callers ask per relation."""

    def __init__(self, diagram: Diagram, kinds: dict[str, str]):
        self.diagram = diagram
        self.kinds = kinds
        self.connections = view_connections(diagram)
        self._nesting: list[tuple[str, str, str | None, str | None]] | None = None

    @property
    def nesting(self) -> list[tuple[str, str, str | None, str | None]]:
        if self._nesting is None:
            self._nesting = nested(self.diagram, self.kinds)
        return self._nesting


def shown_views(
    diagram: Diagram, relation: Relation, kinds: dict[str, str], views: DiagramViews | None = None
) -> tuple[list[str], list[str]]:
    """How the diagram shows the relation (spec: model-relations.md, the one definition of "shown"): `(linked,
    unlinked)`, the connections linked to it, and the views that show it without a link: unlinked connections that
    fit, and for deployed_on the source's nodes nested under the target (its nearest node). Pass `views` when asking
    for several relations of one diagram."""
    views = views if views is not None else DiagramViews(diagram, kinds)
    linked = [c.id for c in views.connections if c.ref == relation.id]
    unlinked = [c.id for c in views.connections if c.ref is None and fits(diagram.type, c, relation)]
    if relation.kind == "deployed_on":
        unlinked += [v for v, e, _, host in views.nesting if e == relation.source and host == relation.target]
    return linked, unlinked


def shows(diagram: Diagram, relation: Relation, kinds: dict[str, str], views: DiagramViews | None = None) -> bool:
    """Whether the diagram shows the relation (`shown_views` finds a view)."""
    linked, unlinked = shown_views(diagram, relation, kinds, views)
    return bool(linked or unlinked)


class ViewElement(NamedTuple):
    """A node or lifeline of a diagram as the model sees it."""

    id: str
    name: str
    kind: str  # the node's kind, or LIFELINE
    ref: str | None


def linkable_kinds(diagram_type: str, view_kind: str) -> tuple[str, ...]:
    """The element kinds a view element of this kind may represent (empty: it cannot be linked)."""
    if diagram_type in NOT_LINKED_TYPES:
        return ()
    if diagram_type == "class":
        return _CLASS.get(view_kind, ())
    if diagram_type == "use_case":
        return _USE_CASE.get(view_kind, ())
    if diagram_type == "component":
        return _COMPONENT.get(view_kind, ())
    if diagram_type == "sequence":
        return _EVERYTHING if view_kind == LIFELINE else ()
    if diagram_type == "activity":
        return _ACTIVITY.get(view_kind, ())
    if view_kind in _DEPLOYMENT:
        return _DEPLOYMENT[view_kind]
    catalog = active_kinds()  # kinds configured by a kinds file: permissive defaults (ADR-0073)
    if view_kind in catalog.container_kinds:
        return _CONTAINER
    if view_kind in catalog.leaf_kinds:
        return _EVERYTHING
    return ()


def linkable_table(diagram_type: str) -> dict[str, list[str]]:
    """Every linkable view kind of a diagram type with its element kinds (what the UI needs)."""
    if diagram_type in NOT_LINKED_TYPES:
        return {}
    if diagram_type == "class":
        return {k: list(v) for k, v in _CLASS.items()}
    if diagram_type == "use_case":
        return {k: list(v) for k, v in _USE_CASE.items()}
    if diagram_type == "component":
        return {k: list(v) for k, v in _COMPONENT.items()}
    if diagram_type == "sequence":
        return {LIFELINE: list(_EVERYTHING)}
    if diagram_type == "activity":
        return {k: list(v) for k, v in _ACTIVITY.items()}
    return {k: list(linkable_kinds(diagram_type, k)) for k in sorted(active_kinds().all_kinds)}


def view_elements(diagram: Diagram) -> list[ViewElement]:
    """Every node (graph types) or lifeline (sequence diagrams), linkable or not."""
    if diagram.interaction is not None:
        return [ViewElement(lf.id, lf.name, LIFELINE, lf.ref) for lf in diagram.interaction.lifelines]
    return [ViewElement(n.id, n.name, n.kind, n.ref) for n in diagram.nodes.values()]


def find_view(diagram: Diagram, ref: str) -> ViewElement | None:
    """A node (by id, name or slug, like `Diagram.find_node`) or a lifeline (by id or name)."""
    if diagram.interaction is not None:
        wanted = ref.strip()
        lowered = wanted.lower()
        for lifeline in diagram.interaction.lifelines:
            if lifeline.id == wanted:
                return ViewElement(lifeline.id, lifeline.name, LIFELINE, lifeline.ref)
        for lifeline in diagram.interaction.lifelines:
            if lifeline.name.lower() == lowered or lifeline.id == slugify(wanted):
                return ViewElement(lifeline.id, lifeline.name, LIFELINE, lifeline.ref)
        return None
    node = diagram.find_node(ref)
    return ViewElement(node.id, node.name, node.kind, node.ref) if node is not None else None


def references(diagram: Diagram, element_id: str) -> list[ViewElement]:
    """The view elements of `diagram` that represent the element."""
    return [v for v in view_elements(diagram) if v.ref == element_id]


def default_kind(diagram: Diagram, view: ViewElement) -> str | None:
    """The element kind "promote" uses for a view element; None when it cannot be linked."""
    kinds = linkable_kinds(diagram.type, view.kind)
    if not kinds:
        return None
    if view.kind == LIFELINE and diagram.interaction is not None:
        lifeline = next(lf for lf in diagram.interaction.lifelines if lf.id == view.id)
        if lifeline.actor:
            return "actor"
    return kinds[0]


def find_element(elements: list[Element], ref: str | None) -> Element | None:
    """An element by id, case-insensitive name or slugified name; None for no match."""
    if not ref or not ref.strip():
        return None
    for element in elements:
        if element.id == ref:
            return element
    lowered = name_key(ref)
    for element in elements:
        if name_key(element.name) == lowered:
            return element
    slug = slugify(ref)
    return next((e for e in elements if e.id == slug), None)


def known_elements(elements: list[Element]) -> str:
    """The element list of an error message for the model or the user."""
    return ", ".join(f"{e.id} ({e.name})" for e in elements) or "none"


MODEL_SUMMARY_BUDGET = 4_000  # characters of project elements in a system prompt


def model_summary(
    elements: list[Element],
    usages: dict[str, list[str]] | None = None,
    budget: int = MODEL_SUMMARY_BUDGET,
    relations: list[Relation] | None = None,
) -> str:
    """The project's elements for a system prompt, one line each (`- [id] «kind» name — description (in: …)`),
    then its relations under `Relations:` (`relation_line`), within `budget` characters: descriptions are dropped
    first, then the remaining relations are counted, then the remaining elements."""

    def line(element: Element, described: bool) -> str:
        parent = f" (part of {element.parent})" if element.parent else ""
        description = f" — {element.description}" if described and element.description else ""
        where = usages.get(element.id) if usages is not None else None
        shown = f" (in: {', '.join(where)})" if where else ""
        return f"- [{element.id}] «{element.kind}» {element.name}{parent}{description}{shown}"

    relations = relations or []

    def text(element_lines: list[str], relation_lines: list[str]) -> str:
        return "\n".join([*element_lines, *(["Relations:", *relation_lines] if relation_lines else [])])

    for described in (True, False):
        full = text([line(e, described) for e in elements], [relation_line(r, described) for r in relations])
        if len(full) <= budget:
            return full
    element_lines = [line(e, False) for e in elements]
    elements_text = "\n".join(element_lines)
    if relations and len(elements_text) + 120 <= budget:  # every element, as many relations as fit
        shown_relations: list[str] = []
        used = len(elements_text) + len("\nRelations:")
        for relation in relations:
            entry = relation_line(relation, False)
            if used + len(entry) + 100 > budget:
                more = len(relations) - len(shown_relations)
                shown_relations.append(f"(+ {more} more relations; call list_elements for all of them)")
                break
            shown_relations.append(entry)
            used += len(entry) + 1
        return text(element_lines, shown_relations)
    lines: list[str] = []
    used = 0
    for element in elements:
        entry = line(element, False)
        if used + len(entry) + 80 > budget:
            lines.append(f"(+ {len(elements) - len(lines)} more elements; call list_elements for all of them)")
            break
        lines.append(entry)
        used += len(entry) + 1
    if relations:
        lines.append(f"(+ {len(relations)} more relations; call list_elements for all of them)")
    return "\n".join(lines)


def model_refs(diagram: Diagram) -> tuple[set[str], set[str]]:
    """The element ids and relation ids the diagram's views, connections and hidden relations refer to (#153)."""
    elements = {v.ref for v in view_elements(diagram) if v.ref is not None}
    if diagram.type not in RELATION_DIAGRAM_TYPES:
        return elements, set()
    relations = {c.ref for c in view_connections(diagram) if c.ref is not None} | set(diagram.hidden_relations)
    return elements, relations


def referenced_model(
    diagram: Diagram, elements: list[Element], relations: list[Relation]
) -> tuple[list[Element], list[Relation]]:
    """What a single diagram document carries of its project's model (#153, ADR-0161): the elements it refers to (views
    and the ends of the relations it refers to) with their ancestors, and the relations between referenced elements,
    in the project's order. Only what a viewer of the diagram's project can see anyway."""
    element_ids, relation_ids = model_refs(diagram)
    referenced = element_ids | {end for r in relations if r.id in relation_ids for end in (r.source, r.target)}
    by_id = {e.id: e for e in elements}
    wanted = set()
    for element_id in referenced:
        current = by_id.get(element_id)
        while current is not None and current.id not in wanted:
            wanted.add(current.id)
            current = by_id.get(current.parent) if current.parent else None
    return (
        [e for e in elements if e.id in wanted],
        [r for r in relations if r.source in referenced and r.target in referenced],
    )


# ---- project vision (spec: docs/architecture/specs/project-vision.md, ADR-0203) -------------------------------------

MAX_VISION_STATEMENT = 2000
MAX_VISION_GOALS = 10
MAX_VISION_ITEM = 200


class Vision(BaseModel):
    """What the product is for: a statement, what success looks like and what it deliberately is not."""

    statement: str = ""
    goals: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.statement or self.goals or self.non_goals)


def vision_problems(statement: str, goals: list[str], non_goals: list[str]) -> list[str]:
    """What is wrong with the three fields of a vision (bounds), for the operation to report."""
    problems: list[str] = []
    if len(statement) > MAX_VISION_STATEMENT:
        problems.append(f"The vision statement has at most {MAX_VISION_STATEMENT} characters ({len(statement)} given).")
    for name, items in (("goals", goals), ("non_goals", non_goals)):
        if len(items) > MAX_VISION_GOALS:
            problems.append(f"The vision has at most {MAX_VISION_GOALS} {name} ({len(items)} given).")
        problems += [
            f"A vision entry in {name} has at most {MAX_VISION_ITEM} characters."
            for i in items
            if len(i) > MAX_VISION_ITEM
        ]
    return problems


def vision_lines(vision: Vision | None) -> str | None:
    """The vision as prompt lines (goal ids `g1…` by position, so a finding can cite `vision:g2`); None while unset."""
    if vision is None or vision.is_empty():
        return None
    lines = [f"Vision: {vision.statement}"] if vision.statement else []
    lines += [f"[g{n}] goal: {goal}" for n, goal in enumerate(vision.goals, start=1)]
    lines += [f"non-goal: {item}" for item in vision.non_goals]
    return "\n".join(lines)


# ---- arc42 notes (spec: docs/architecture/specs/arc42-export.md, ADR-0208) -------------------------------------------

ARC42_SECTIONS = tuple(f"{n:02d}" for n in range(1, 13))
MAX_ARC42_NOTE = 8000


# ---- architecture drivers (spec: docs/architecture/specs/architecture-drivers.md, ADR-0163) ----------------------

DRIVER_KINDS = ("quality_goal", "constraint", "requirement", "risk")
DRIVER_KIND_TEXT = {
    "quality_goal": "quality goal",
    "constraint": "constraint",
    "requirement": "requirement",
    "risk": "risk",
}
# ATAM utility tree: business importance and technical risk, each rated high/medium/low; (high, high) ranks first
DRIVER_LEVELS = ("high", "medium", "low")
# Lifecycle of drivers and scenarios (spec: graded-deletion.md, ADR-0198): only a retired one can be deleted, and a
# retired one no longer feeds the prompts, the rules or the coverage.
OBJECT_STATUSES = ("active", "retired")
MAX_DRIVERS = 100
MAX_DRIVER_TITLE = 120
MAX_DRIVER_DESCRIPTION = 1000
DRIVERS_SUMMARY_BUDGET = 4_000  # characters of drivers in a system prompt
_DRIVER_PROMPT_DESCRIPTION = 200


class Driver(BaseModel):
    """An architecture driver of a project: a quality goal, constraint, requirement or risk, prioritised."""

    id: str  # d<n>, from Project.driver_seq, never reused
    title: str
    kind: str = "quality_goal"  # one of DRIVER_KINDS
    importance: str = "medium"  # business importance, one of DRIVER_LEVELS
    risk: str = "medium"  # technical risk or difficulty, one of DRIVER_LEVELS
    description: str = ""
    elements: list[str] = Field(default_factory=list)  # ids of the project elements it concerns
    # requirements it addresses (ids, ≤ MAX_LINKED; spec: requirements.md, ADR-0189) and the proposal item that created it
    # (`<proposal>/<key>`, ADR-0187); both omitted from dumps while empty, so data written before is unchanged
    requirements: list[str] = Field(default_factory=list)
    origin: str = ""
    status: str = "active"  # one of OBJECT_STATUSES; omitted from dumps while active (ADR-0198)

    @model_serializer(mode="wrap")
    def _omit_empty_links(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in ("requirements", "origin"):
            if not data.get(key):
                data.pop(key, None)
        if data.get("status") == "active":
            data.pop("status", None)
        return data


def is_retired(item: Any) -> bool:
    """Whether a driver or scenario is retired (any other object, a decision included, never is)."""
    return getattr(item, "status", "active") == "retired"


def active(items: list[Any]) -> list[Any]:
    """The items that are not retired, in order."""
    return [i for i in items if not is_retired(i)]


def priority_key(driver: Driver) -> tuple[int, int]:
    """Importance first, then risk: (high, high) sorts first. A stable sort keeps the list order inside a class."""
    rank = {level: n for n, level in enumerate(DRIVER_LEVELS)}
    return rank.get(driver.importance, len(DRIVER_LEVELS)), rank.get(driver.risk, len(DRIVER_LEVELS))


def ranked_drivers(drivers: list[Driver]) -> list[Driver]:
    """The drivers by priority (the prompt's and the panel's order)."""
    return sorted(drivers, key=priority_key)


def find_driver(drivers: list[Driver], ref: str | None) -> Driver | None:
    """A driver by id or case-insensitive title; None for no match."""
    if not ref or not ref.strip():
        return None
    found = next((d for d in drivers if d.id == ref.strip()), None)
    if found is not None:
        return found
    wanted = name_key(ref)
    return next((d for d in drivers if name_key(d.title) == wanted), None)


def known_drivers(drivers: list[Driver]) -> str:
    """The drivers for an error message the model can act on."""
    return ", ".join(f"{d.id} ({d.title})" for d in drivers[:30]) + (" …" if len(drivers) > 30 else "") or "none"


def driver_number(driver_id: str) -> int | None:
    """n of a generated id `d<n>`; None for any other id (an imported one)."""
    if driver_id.startswith("d") and driver_id[1:].isdigit():
        return int(driver_id[1:])
    return None


def driver_line(driver: Driver, description_limit: int | None = _DRIVER_PROMPT_DESCRIPTION) -> str:
    """`- [d3] quality goal (importance high, risk medium): Title — description (concerns: a, b)`."""
    text = ""
    if description_limit is None or description_limit > 0:
        description = single_line(driver.description).strip()
        if description_limit is not None and len(description) > description_limit:
            description = description[: description_limit - 1].rstrip() + "…"
        text = f" — {description}" if description else ""
    concerns = f" (concerns: {', '.join(driver.elements)})" if driver.elements else ""
    kind = DRIVER_KIND_TEXT.get(driver.kind, driver.kind)
    return (
        f"- [{driver.id}] {kind} (importance {driver.importance}, risk {driver.risk}): {driver.title}{text}{concerns}"
    )


def drivers_summary(drivers: list[Driver], budget: int = DRIVERS_SUMMARY_BUDGET) -> str:
    """The drivers for a system prompt, most important first, within `budget` characters: descriptions are shortened,
    then dropped, then the lowest-priority drivers are counted."""
    ranked = ranked_drivers(active(drivers))
    for limit in (_DRIVER_PROMPT_DESCRIPTION, 0):
        text = "\n".join(driver_line(d, limit) for d in ranked)
        if len(text) <= budget:
            return text
    lines: list[str] = []
    used = 0
    for n, driver in enumerate(ranked):
        entry = driver_line(driver, 0)
        if used + len(entry) + 60 > budget:
            lines.append(f"(+ {len(ranked) - n} more drivers of lower priority; call list_drivers for all of them)")
            break
        lines.append(entry)
        used += len(entry) + 1
    return "\n".join(lines)


# ---- project elements by default (spec: docs/architecture/specs/elements-by-default.md, ADR-0164) -----------------

# Grouping containers stay local to their diagram; execution environments are building blocks of their own.
_NOT_ADOPTED_DEPLOYMENT = frozenset({"deployment", "vnet", "subnet", "region", "group"})
_ADOPTED_ACTIVITY = frozenset({"activity_partition", "data_store_node"})


def adoptable(diagram_type: str, view_kind: str) -> bool:
    """Whether a new view element of this kind becomes a project element at once: deployment building blocks and
    execution environments (not grouping containers), every lifeline, activity partitions and data stores."""
    if not linkable_kinds(diagram_type, view_kind) or diagram_type == "class":
        return False
    if diagram_type in ("sequence", "use_case"):
        return True  # every lifeline; every actor and subject (the use cases are not linkable)
    if diagram_type == "activity":
        return view_kind in _ADOPTED_ACTIVITY
    if view_kind in _NOT_ADOPTED_DEPLOYMENT:
        return False
    if view_kind in CONTAINER_KINDS:
        return True  # node, cluster: execution environments
    return view_kind not in active_kinds().container_kinds  # a kinds file's containers group, its leaves are parts


def loose_key(name: str) -> str:
    """Letters and digits only, case-folded: "Auth-Service" and "authservice" are the same name to a reader."""
    return "".join(ch for ch in name_key(name) if ch.isalnum())


def adoption_target(elements: list[Element], name: str) -> Element | None:
    """The element a new view named `name` stands for: the same name, else the only one with the same loose key."""
    wanted = name_key(name)
    exact = next((e for e in elements if name_key(e.name) == wanted), None)
    if exact is not None:
        return exact
    loose = loose_key(name)
    if not loose:
        return None
    matches = [e for e in elements if loose_key(e.name) == loose]
    return matches[0] if len(matches) == 1 else None


def adoptable_views(diagram: Diagram, known: set[str] | None = None) -> list[ViewElement]:
    """The unlinked views of `diagram` that adoption would link (only those whose ids are not in `known`)."""
    return [
        v
        for v in view_elements(diagram)
        if v.ref is None and (known is None or v.id not in known) and adoptable(diagram.type, v.kind)
    ]
