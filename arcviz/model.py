"""Domain model: a diagram is a set of nodes (optionally nested) plus edges between them."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from . import sequence
from .sequence import MAX_DESCRIPTION as MAX_DESCRIPTION  # notes (spec: collaboration.md), re-exported
from .sequence import NOTE_CHARS as NOTE_CHARS
from .sequence import SHORT_NOTE_CHARS as SHORT_NOTE_CHARS
from .sequence import Interaction, note_suffix

# Diagram types (spec: docs/architecture/specs/diagram-types.md); the first one is the default. Each layer
# keeps a table keyed by these names (kinds here, operations, renderers, tools and prompts, rules).
DIAGRAM_TYPES = ("deployment", "activity", "sequence", "state_machine", "class", "use_case", "component")

# The UML 2.5.1 chapter each type follows (ADR-0030).
UML_REFERENCES = {
    "deployment": "UML 2.5.1, 19 Deployments (loosely: cloud-oriented kinds, ADR-0030)",
    "activity": "UML 2.5.1, 15 Activities (and OpaqueAction from 16 Actions)",
    "sequence": "UML 2.5.1, 17 Interactions (sequence diagram notation, 17.8 and 17.12)",
    "state_machine": "UML 2.5.1, 14 State Machines",
    "class": "UML 2.5.1, 11 Classification (and 10 Simple Classifiers)",
    "use_case": "UML 2.5.1, 18 Use Cases",
    "component": "UML 2.5.1, 11 Components (with the ports and interfaces of 10 and 11)",
}

# The layout direction a new diagram of the type starts with (the default of the others is DIRECTIONS[0]).
TYPE_DIRECTIONS = {"use_case": "LR", "component": "LR"}

# Built-in kinds of deployment diagrams. Kinds that may contain other nodes; everything else is a leaf. A kinds file can add
# more at startup (see KindCatalog / active_kinds()); these constants always hold the built-in sets.
CONTAINER_KINDS = frozenset({"deployment", "vnet", "subnet", "node", "cluster", "group", "region"})

LEAF_KINDS = frozenset(
    {"service", "component", "database", "queue", "storage", "gateway", "external", "artifact", "function"}
)

ALL_KINDS = CONTAINER_KINDS | LEAF_KINDS

EDGE_KINDS = frozenset({"connection", "dependency", "deploys", "association", "replication"})

# Diagram types whose connections may show relations of the project model (spec: model-relations.md, ADR-0139); only
# these carry relation refs, completeness declarations and hidden markers.
RELATION_DIAGRAM_TYPES = frozenset({"deployment", "sequence", "component"})

# Layout directions of the rendered flowchart (Mermaid syntax); the first one is the default.
DIRECTIONS = ("TB", "BT", "LR", "RL")

# Explicit arrow heads of an edge; None on an edge means "the default of its kind".
EDGE_DIRECTIONS = ("forward", "both", "none")

# Line styles of edges and node shapes a configured kind may use (Mermaid syntax lives in mermaid.py).
EDGE_LINES = ("solid", "dotted", "thick")
NODE_SHAPES = (
    "rect",
    "rounded",
    "stadium",
    "subroutine",
    "cylinder",
    "circle",
    "hexagon",
    "parallelogram",
    "trapezoid",
    "asymmetric",
    "rhombus",
)


@dataclass(frozen=True)
class KindCatalog:
    """The node and edge kinds in effect: the built-in sets plus kinds configured at startup.

    The mappings only hold entries for configured kinds; built-in kinds are styled by mermaid.py.
    """

    container_kinds: frozenset[str]
    leaf_kinds: frozenset[str]
    edge_kinds: frozenset[str]
    node_styles: Mapping[str, str] = field(default_factory=dict)  # kind -> Mermaid classDef body
    node_shapes: Mapping[str, str] = field(default_factory=dict)  # kind -> one of NODE_SHAPES
    edge_lines: Mapping[str, tuple[str, str]] = field(default_factory=dict)  # kind -> (line, direction)
    node_icons: Mapping[str, str] = field(default_factory=dict)  # leaf kind -> "<pack>:<icon>" (cloud-icons.md)
    # leaf kind -> the built-in kind the rules treat it as, and what the platform guarantees (deployment-traits.md)
    node_roles: Mapping[str, str] = field(default_factory=dict)
    node_traits: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @property
    def all_kinds(self) -> frozenset[str]:
        return self.container_kinds | self.leaf_kinds


BUILTIN_KINDS = KindCatalog(CONTAINER_KINDS, LEAF_KINDS, EDGE_KINDS)
_active_kinds = BUILTIN_KINDS


def active_kinds() -> KindCatalog:
    """The process-wide kind catalogue (set once at startup by the server)."""
    return _active_kinds


def use_kinds(catalog: KindCatalog) -> None:
    global _active_kinds
    _active_kinds = catalog


# Deployment traits (spec: docs/architecture/specs/deployment-traits.md, ADR-0169). A configured leaf kind may name the
# built-in kind the rules treat it as (`role`) and a platform guarantee (`traits`); a node's `redundancy` property wins.
ROLES = ("database", "storage", "queue", "service", "function", "component", "gateway", "external")
TRAITS: dict[str, tuple[str, ...]] = {"redundancy": ("built_in",)}
REDUNDANCY_PROPERTY = "redundancy"
REDUNDANCY_VALUES = ("built_in", "none")  # documented values of the node property


def role_of(node: Node) -> str | None:
    """The built-in kind the rules reason about for `node`: its own kind, or the `role` of a configured kind."""
    if node.kind in LEAF_KINDS:
        return node.kind
    return _active_kinds.node_roles.get(node.kind)


def kind_trait(node: Node, name: str) -> str | None:
    """The value of trait `name` that the node's kind guarantees, if any."""
    return _active_kinds.node_traits.get(node.kind, {}).get(name)


# Activity diagrams (spec: docs/architecture/specs/activity-diagrams.md): UML 2.5.1 metaclasses in snake_case.
ACTIVITY_INITIAL = "initial_node"
ACTIVITY_FINALS = frozenset({"activity_final_node", "flow_final_node"})
ACTIVITY_KINDS = KindCatalog(
    container_kinds=frozenset({"activity_partition"}),
    leaf_kinds=frozenset(
        {
            "opaque_action",
            ACTIVITY_INITIAL,
            *ACTIVITY_FINALS,
            "decision_node",
            "merge_node",
            "fork_node",
            "join_node",
            "central_buffer_node",
            "data_store_node",
        }
    ),
    edge_kinds=frozenset({"control_flow", "object_flow"}),
)


def activity_edge_problem(source_kind: str, target_kind: str) -> str | None:
    """Why an ActivityEdge between nodes of these kinds is not well-formed UML, or None."""
    if "activity_partition" in (source_kind, target_kind):
        return "an ActivityPartition is not an ActivityNode, so no edge may start or end at it"
    if target_kind == ACTIVITY_INITIAL:
        return "an InitialNode has no incoming edges"
    if source_kind in ACTIVITY_FINALS:
        return f"a final node ({source_kind}) has no outgoing edges"
    return None


def activity_rule_errors(diagram: Diagram) -> list[str]:
    """UML well-formedness rules of activity diagrams that are cheap to check (spec: activity-diagrams.md)."""
    errors: list[str] = []
    for edge in diagram.edges:
        src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
        if src is not None and tgt is not None:
            problem = activity_edge_problem(src.kind, tgt.kind)
            if problem:
                errors.append(f"edge '{edge.id}' ({edge.source} -> {edge.target}): {problem}")
        if edge.direction is not None:
            errors.append(f"edge '{edge.id}': ActivityEdges are always directed (direction must be unset)")
    return errors


# Flow helpers of activity diagrams, shared by the rule checks (rules.py) and the PlantUML export (exchange.py).
ELSE_GUARDS = frozenset({"else", "[else]"})  # the else branch of a DecisionNode, compared case-insensitively


def edge_guard(edge: Edge) -> str:
    """The guard of an ActivityEdge (the edge property `guard`), stripped; empty without one."""
    return edge.properties.get("guard", "").strip()


def is_else_guard(guard: str) -> bool:
    return guard.strip().lower() in ELSE_GUARDS


def activity_outgoing(diagram: Diagram) -> dict[str, list[Edge]]:
    """The outgoing ActivityEdges of every ActivityNode, in model order (ActivityPartitions are no nodes)."""
    nodes = {nid for nid, node in diagram.nodes.items() if node.kind != "activity_partition"}
    out: dict[str, list[Edge]] = {}
    for edge in diagram.edges:
        if edge.source in nodes and edge.target in nodes:
            out.setdefault(edge.source, []).append(edge)
    return out


def reaches(edges_from: Mapping[str, list[str]], start: str, goal: str) -> bool:
    """Whether `goal` can be reached from `start` along `edges_from` (node id -> target ids), `start` itself included."""
    seen, stack = {start}, [start]
    while stack:
        current = stack.pop()
        if current == goal:
            return True
        for nxt in edges_from.get(current, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def flow_distances(outgoing: Mapping[str, list[Edge]], starts: Iterable[str]) -> dict[str, int]:
    """Every node reachable from `starts` along the flows with its distance (0 for a start), breadth first."""
    distance = dict.fromkeys(starts, 0)
    queue = list(distance)
    for current in queue:
        for edge in outgoing.get(current, []):
            if edge.target not in distance:
                distance[edge.target] = distance[current] + 1
                queue.append(edge.target)
    return distance


def reverse_flows(outgoing: Mapping[str, list[Edge]]) -> dict[str, list[Edge]]:
    """The flows turned around (target -> source), for reachability towards a set of nodes."""
    out: dict[str, list[Edge]] = {}
    for edges in outgoing.values():
        for edge in edges:
            out.setdefault(edge.target, []).append(
                edge.model_copy(update={"source": edge.target, "target": edge.source})
            )
    return out


# State machine diagrams (spec: docs/architecture/specs/state-machine-diagrams.md, ADR-0178): UML 2.5.1 metaclasses.
STATE_MACHINE_KINDS = KindCatalog(
    container_kinds=frozenset({"composite_state", "state_region"}),
    leaf_kinds=frozenset(
        {
            "state",
            "initial_pseudostate",
            "final_state",
            "terminate_pseudostate",
            "choice_pseudostate",
            "junction_pseudostate",
            "fork_pseudostate",
            "join_pseudostate",
            "shallow_history_pseudostate",
            "deep_history_pseudostate",
        }
    ),
    edge_kinds=frozenset({"transition"}),
)


# Class diagrams (spec: docs/architecture/specs/class-diagrams.md, ADR-0179): UML 2.5.1 metaclasses.
CLASS_KINDS = KindCatalog(
    container_kinds=frozenset({"package"}),
    leaf_kinds=frozenset({"class", "interface", "enumeration", "data_type"}),
    edge_kinds=frozenset({"association", "aggregation", "composition", "generalization", "realization", "dependency"}),
)

# Use case diagrams (spec: docs/architecture/specs/use-case-diagrams.md, ADR-0180): UML 2.5.1 metaclasses.
USE_CASE_KINDS = KindCatalog(
    container_kinds=frozenset({"subject"}),
    leaf_kinds=frozenset({"actor", "use_case"}),
    edge_kinds=frozenset({"association", "include", "extend", "generalization"}),
)

# Component diagrams (spec: docs/architecture/specs/component-diagrams.md, ADR-0181): UML 2.5.1 metaclasses. `component`,
# `connection`, `interface`, `package`, `realization` and `dependency` are shared with other types (SHARED_KINDS).
COMPONENT_KINDS = KindCatalog(
    container_kinds=frozenset({"subsystem", "package"}),
    leaf_kinds=frozenset({"component", "interface"}),
    edge_kinds=frozenset({"connection", "realization", "usage", "dependency"}),
)

# The text an edge of this kind always shows before its own label (UML keywords of the notation).
EDGE_STEREOTYPES: dict[str, str] = {
    "realization": "«realizes»",
    "include": "«include»",
    "extend": "«extend»",
    "usage": "«use»",
}


# Fixed UML catalogues of the other diagram types (ADR-0037: the kinds file extends deployment only).
# Sequence diagrams have no node or edge kinds: their content is an Interaction (sequence.py).
SEQUENCE_KINDS = KindCatalog(frozenset(), frozenset(), frozenset())
TYPE_KINDS: dict[str, KindCatalog] = {
    "activity": ACTIVITY_KINDS,
    "sequence": SEQUENCE_KINDS,
    "state_machine": STATE_MACHINE_KINDS,
    "class": CLASS_KINDS,
    "use_case": USE_CASE_KINDS,
    "component": COMPONENT_KINDS,
}


# Kind names that more than one diagram type has, with those types (ADR-0185): the same UML metaclass, the same
# container-ness. Every other name belongs to exactly one type; tests/test_diagram_types.py checks both.
SHARED_KINDS: dict[str, frozenset[str]] = {
    "association": frozenset({"deployment", "class", "use_case"}),
    "dependency": frozenset({"deployment", "class", "component"}),
    "generalization": frozenset({"class", "use_case"}),
    "component": frozenset({"deployment", "component"}),
    "connection": frozenset({"deployment", "component"}),
    "interface": frozenset({"class", "component"}),
    "package": frozenset({"class", "component"}),
    "realization": frozenset({"class", "component"}),
}


def kinds_for(diagram_type: str) -> KindCatalog:
    """The kind catalogue of a diagram type: the active (configurable) one for deployment diagrams."""
    if diagram_type == DIAGRAM_TYPES[0]:
        return _active_kinds
    return TYPE_KINDS[diagram_type]


def container_kinds() -> frozenset[str]:
    """Container kinds of every type; kind names are disjoint across types, so no type is needed."""
    out = _active_kinds.container_kinds
    for catalog in TYPE_KINDS.values():
        out = out | catalog.container_kinds
    return out


def check_diagram_type(value: str) -> str:
    if value not in DIAGRAM_TYPES:
        raise ValueError(f"Unknown diagram type '{value}'. Available: {', '.join(DIAGRAM_TYPES)}")
    return value


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Control characters in one-line texts (#103, ADR-0160): line breaks and tabs (a run of them) become one space, every
# other C0/C1 control character (and DEL) is dropped. U+2028/U+2029 are line breaks too.
_LINE_BREAKS = re.compile("[\r\n\t\u2028\u2029]+")
_CONTROLS = re.compile("[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]")


def single_line(text: str) -> str:
    """`text` as one line without control characters: what names, titles and labels hold (#103). Never an error."""
    return _LINE_BREAKS.sub(" ", _CONTROLS.sub("", text))


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "node"


class Node(BaseModel):
    id: str
    name: str
    kind: str = "component"
    parent: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    ref: str | None = None  # the project model element it represents (spec: shared-project-model.md)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)  # a note (spec: collaboration.md)

    @model_serializer(mode="wrap")
    def _omit_unset_ref(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in ("ref", "description"):
            if data.get(key, "") is None:
                del data[key]  # documents written before M18 / M10 dump, fingerprint and export unchanged
        return data

    @property
    def is_container(self) -> bool:
        return self.kind in container_kinds()


class Edge(BaseModel):
    id: str
    source: str
    target: str
    label: str | None = None
    kind: str = "connection"
    direction: str | None = None  # one of EDGE_DIRECTIONS; None = default of the kind
    properties: dict[str, str] = Field(default_factory=dict)
    ref: str | None = None  # the model relation it shows (spec: model-relations.md)

    @model_serializer(mode="wrap")
    def _omit_unset_ref(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if data.get("ref", "") is None:
            del data["ref"]  # documents written before M25 dump, fingerprint and export unchanged
        return data


class Diagram(BaseModel):
    id: str
    title: str
    type: str = DIAGRAM_TYPES[0]
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)
    direction: str = DIRECTIONS[0]
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    interaction: Interaction | None = None  # sequence diagrams only (spec: sequence-diagrams.md)
    # Open world (spec: model-relations.md, ADR-0137): the relation kinds this diagram declares complete, and the
    # relations it intentionally leaves out. Both omitted from dumps while empty, so older fingerprints stay.
    complete: list[str] = Field(default_factory=list)
    hidden_relations: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_unset_declarations(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in ("complete", "hidden_relations"):
            if data.get(key, None) == []:
                del data[key]  # diagrams written before M25 dump, fingerprint and export unchanged
        return data

    @field_validator("complete", "hidden_relations")
    @classmethod
    def _canonical(cls, value: list[str]) -> list[str]:
        """Sorted and unique, as the operations keep them: an imported document cannot store another order."""
        return sorted(set(value))

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        return check_diagram_type(value)

    @model_validator(mode="after")
    def _type_direction(self) -> Diagram:
        """A diagram that was not given a direction starts with the one its type reads best in (use cases: left to right)."""
        if "direction" not in self.model_fields_set and self.type in TYPE_DIRECTIONS:
            self.direction = TYPE_DIRECTIONS[self.type]
        return self

    @model_validator(mode="after")
    def _type_content(self) -> Diagram:
        if self.type == "sequence" and self.interaction is None:
            self.interaction = Interaction()
        elif self.type != "sequence" and self.interaction is not None:
            raise ValueError(f"interaction: only sequence diagrams have one, not {self.type} diagrams")
        return self

    def has_element(self, element_id: str) -> bool:
        """Whether a node or (sequence diagrams) an interaction element has this id."""
        if element_id in self.nodes:
            return True
        return self.interaction is not None and sequence.find(self.interaction, element_id) is not None

    # ---- queries -------------------------------------------------------

    def children(self, parent_id: str | None) -> list[Node]:
        return sorted((n for n in self.nodes.values() if n.parent == parent_id), key=lambda n: n.name.lower())

    def descendants(self, node_id: str) -> list[Node]:
        out: list[Node] = []
        seen = {node_id}
        stack = [node_id]
        while stack:
            current = stack.pop()
            for child in self.children(current):
                if child.id in seen:
                    continue  # a containment cycle (corrupt stored data) must not loop forever
                seen.add(child.id)
                out.append(child)
                stack.append(child.id)
        return out

    def ancestors(self, node_id: str) -> list[str]:
        out: list[str] = []
        current = self.nodes.get(node_id)
        while current is not None and current.parent is not None and current.parent not in out:
            out.append(current.parent)  # the membership test stops at a containment cycle (imported data)
            current = self.nodes.get(current.parent)
        return out

    def find_node(self, ref: str) -> Node | None:
        """Resolve a node by id, or (case-insensitively) by name or slugified name."""
        if ref in self.nodes:
            return self.nodes[ref]
        wanted = ref.strip().lower()
        for node in self.nodes.values():
            if node.name.lower() == wanted or node.id.lower() == wanted:
                return node
        slug = slugify(ref)
        return self.nodes.get(slug)

    def path(self, node_id: str) -> str:
        parts = [self.nodes[a].name for a in reversed(self.ancestors(node_id)) if a in self.nodes]
        parts.append(self.nodes[node_id].name)
        return " / ".join(parts)

    def unique_id(self, base: str) -> str:
        candidate = slugify(base)
        if candidate not in self.nodes:
            return candidate
        i = 2
        while f"{candidate}-{i}" in self.nodes:
            i += 1
        return f"{candidate}-{i}"

    def next_edge_id(self) -> str:
        existing = {e.id for e in self.edges}
        i = len(self.edges) + 1
        while f"e{i}" in existing:
            i += 1
        return f"e{i}"

    # ---- integrity -----------------------------------------------------

    def integrity_errors(self) -> list[str]:
        errors: list[str] = []
        for node in self.nodes.values():
            if node.parent is not None:
                if node.parent not in self.nodes:
                    errors.append(f"node '{node.id}' has unknown parent '{node.parent}'")
                elif node.id in self.ancestors(node.parent) or node.parent == node.id:
                    errors.append(f"node '{node.id}' is part of a containment cycle")
        for edge in self.edges:
            if edge.source not in self.nodes:
                errors.append(f"edge '{edge.id}' has unknown source '{edge.source}'")
            if edge.target not in self.nodes:
                errors.append(f"edge '{edge.id}' has unknown target '{edge.target}'")
        if self.interaction is not None:
            errors += sequence.integrity_errors(self.interaction)
        if self.type not in RELATION_DIAGRAM_TYPES:  # model relations (ADR-0139): nothing to show them with
            errors += [f"edge '{e.id}': {self.type} edges cannot show a model relation" for e in self.edges if e.ref]
            if self.complete:
                errors.append(f"complete: a {self.type} diagram cannot declare relation kinds complete")
            if self.hidden_relations:
                errors.append(f"hidden_relations: a {self.type} diagram shows no relations to hide")
        return errors

    # ---- subsets -------------------------------------------------------

    def subset(self, focus: str | None = None, ids: Iterable[str] | None = None) -> dict[str, Any]:
        """JSON view of part of the diagram: the subtree of `focus`, and/or the nodes in `ids`
        (with their ancestors for context), plus every edge touching a selected node."""
        selected: set[str] = set()
        if focus is not None and focus in self.nodes:
            selected.add(focus)
            selected.update(d.id for d in self.descendants(focus))
        for ref in ids or []:
            node = self.find_node(ref)
            if node is not None:
                selected.add(node.id)
                selected.update(a for a in self.ancestors(node.id))
        if self.interaction is not None:  # sequence diagrams: always the whole interaction
            return {
                "id": self.id,
                "title": self.title,
                "type": self.type,
                "interaction": self.interaction.model_dump(mode="json"),
                "partial": False,
            }
        if focus is None and not ids:
            selected = set(self.nodes)
        nodes = {nid: self.nodes[nid].model_dump() for nid in sorted(selected) if nid in self.nodes}
        edges = [e.model_dump() for e in self.edges if e.source in selected or e.target in selected]
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "nodes": nodes,
            "edges": edges,
            "partial": len(nodes) < len(self.nodes),
        }

    # ---- textual summary (used in the LLM system prompt) --------------

    def _node_line(self, node: Node, depth: int, note_chars: int = NOTE_CHARS) -> str:
        props = ", ".join(f"{k}={v}" for k, v in sorted(node.properties.items()))
        props = f" {{{props}}}" if props else ""
        linked = f" (= element {node.ref})" if node.ref else ""
        note = note_suffix(node.description, note_chars)
        return f"{'  ' * (depth + 1)}- [{node.id}] «{node.kind}» {node.name}{props}{linked}{note}"

    def summary(self, budget: int | None = None) -> str:
        """Indented text rendering for the system prompt.

        With a `budget` (characters), containers are collapsed to one line each, largest first, until
        the text fits; collapsed containers show their element count and a hint to call `get_diagram`
        with `focus`. Edges into collapsed containers are summarised as a count. What still does not fit is cut
        (#115): edges from the end, then leaves, then container lines, each cut counted with a hint to call
        `get_diagram`, so `len(summary(budget)) <= budget + SUMMARY_SLACK` for every diagram shape.
        """
        full = self._summary(set())
        if budget is None or len(full) <= budget:
            return full
        # Notes give way first: shortened, then only a hint (ADR-0093); structure is collapsed last.
        for note_chars in (SHORT_NOTE_CHARS, 0):
            full = self._summary(set(), note_chars)
            if len(full) <= budget:
                return full
        # Collapse the largest containers (by subtree size) one at a time until the text fits.
        containers = sorted(
            (n for n in self.nodes.values() if n.is_container and self.children(n.id)),
            key=lambda n: (-len(self.descendants(n.id)), n.id),
        )
        collapsed: set[str] = set()
        text = full
        for container in containers:
            if any(a in collapsed for a in self.ancestors(container.id)):
                continue  # already hidden inside a collapsed ancestor
            collapsed.add(container.id)
            text = self._summary(collapsed, 0)
            if len(text) <= budget:
                return text
        # Still too long (a flat diagram, a sequence, very long lines): cut what is left, with counts (#115).
        return self._summary_parts(collapsed, 0).fit(budget)

    def _summary(self, collapsed: set[str], note_chars: int = NOTE_CHARS) -> str:
        return self._summary_parts(collapsed, note_chars).text()

    def _summary_parts(self, collapsed: set[str], note_chars: int = NOTE_CHARS) -> _SummaryParts:
        kind = f", {self.type} diagram" if self.type != DIAGRAM_TYPES[0] else ""
        parts = _SummaryParts(title=self.title, header=f"(id: {self.id}{kind})")
        if self.interaction is not None:
            parts.items = [(line, True) for line in sequence.summary_lines(self.interaction, note_chars)]
            parts.what = "lines of the interaction"
            return parts
        if self.direction != DIRECTIONS[0]:
            parts.head.append(f"Layout direction: {self.direction}")
        if not self.nodes:
            parts.head.append("  (no nodes yet)")
        hidden: set[str] = set()

        def walk(parent: str | None, depth: int) -> None:
            for node in self.children(parent):
                line = self._node_line(node, depth, note_chars)
                if node.id in collapsed:
                    inside = self.descendants(node.id)
                    hidden.update(d.id for d in inside)
                    containers = sum(1 for d in inside if d.is_container)
                    parts.items.append(
                        (
                            f"{line}  (collapsed: {len(inside)} elements, {containers} containers inside;"
                            f' call get_diagram with focus="{node.id}" for details)',
                            False,
                        )
                    )
                else:
                    parts.items.append((line, not node.is_container))
                    walk(node.id, depth + 1)

        walk(None, 0)
        shown_edges = [e for e in self.edges if e.source not in hidden and e.target not in hidden]
        for edge in shown_edges:
            label = f' "{edge.label}"' if edge.label else ""
            kind = f"{edge.kind}, direction={edge.direction}" if edge.direction else edge.kind
            props = ", ".join(f"{k}={v}" for k, v in sorted(edge.properties.items()))
            props = f" {{{props}}}" if props else ""
            parts.edges.append(f"  - [{edge.id}] {edge.source} -> {edge.target} ({kind}){label}{props}")
        if not self.edges:
            parts.tail.append("Edges: (none)")
        hidden_edges = len(self.edges) - len(shown_edges)
        if hidden_edges:
            parts.tail.append(f"  (+ {hidden_edges} edges touching collapsed containers; see get_diagram)")
        if collapsed:
            parts.tail.append(
                f"Note: {len(collapsed)} container(s) are collapsed to keep this summary short. Their contents "
                "are still part of the diagram; use get_diagram(focus=...) before changing anything inside them."
            )
        return parts


SUMMARY_TITLE_CHARS = 200  # a title is cut to this in a summary that had to be cut (titles have no bound of their own)
SUMMARY_SLACK = 1000  # what a cut summary may exceed its budget by: header, footer and the two count lines (#115)


@dataclass
class _SummaryParts:
    """The lines of a summary, grouped by what a budget may cut (#115): `items` are the element lines in tree order
    (flag: a leaf, cut before container lines), `edges` the edge lines; `head` and `tail` stay."""

    title: str
    header: str  # "(id: …)" after the title
    head: list[str] = field(default_factory=list)
    items: list[tuple[str, bool]] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)
    what: str = "elements"  # what the item lines are, for the count line

    def text(self, title: str | None = None, items: list[str] | None = None, edges: list[str] | None = None) -> str:
        lines = [f"Diagram '{self.title if title is None else title}' {self.header}", *self.head]
        lines += [line for line, _ in self.items] if items is None else items
        edges = self.edges if edges is None else edges
        if edges:
            lines += ["Edges:", *edges]
        return "\n".join([*lines, *self.tail])

    def fit(self, budget: int) -> str:
        """Edges cut from the end first, then leaves from the end, then container lines; each cut is counted with a hint
        to call get_diagram. Whatever the budget, the result exceeds it by at most SUMMARY_SLACK."""
        title = self.title if len(self.title) <= SUMMARY_TITLE_CHARS else self.title[:SUMMARY_TITLE_CHARS] + "…"
        reserve = 2 * 120  # the two count lines
        fixed = len(self.text(title, [], [])) + len("\nEdges:") + reserve
        item_sizes = [len(line) + 1 for line, _ in self.items]
        room = budget - fixed - sum(item_sizes)
        kept_edges: list[str] = []
        for line in self.edges:
            if room < len(line) + 1:
                break
            room -= len(line) + 1
            kept_edges.append(line)
        cut_edges = len(self.edges) - len(kept_edges)
        keep = [True] * len(self.items)
        if room < 0:  # the elements alone do not fit: cut leaves from the end, then container lines
            for leaves in (True, False):
                for i in range(len(self.items) - 1, -1, -1):
                    if room >= 0:
                        break
                    if keep[i] and self.items[i][1] == leaves:
                        keep[i] = False
                        room += item_sizes[i]
        items = [line for (line, _), kept in zip(self.items, keep, strict=True) if kept]
        cut_items = len(self.items) - len(items)
        if cut_items:
            items.append(
                f"  (+ {cut_items} more {self.what} not shown to keep this summary short; call get_diagram for them)"
            )
        edges = kept_edges
        if cut_edges:
            edges = [*kept_edges, f"  (+ {cut_edges} more edges not shown; call get_diagram for them)"]
        return self.text(title, items, edges)


def fingerprint(diagram: Diagram) -> str:
    """Hash of a diagram's content (timestamps excluded): tells real changes from duplicate snapshots and
    identifies the base and result of a proposal."""
    payload = diagram.model_dump(mode="json", exclude={"created_at", "updated_at"})
    if payload.get("direction") == DIRECTIONS[0]:
        del payload["direction"]  # the default keeps fingerprints of versions stored before the field existed
    if payload.get("type") == DIAGRAM_TYPES[0]:
        del payload["type"]  # likewise for diagrams stored before diagram types existed
    if payload.get("interaction") is None:
        payload.pop("interaction", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
