"""Graph types: the diagram types whose content is `Diagram.nodes` and `Diagram.edges` and that have a fixed UML
vocabulary (spec: docs/architecture/specs/uml-graph-types.md, ADR-0183).

A `GraphProfile` holds what the operations, the importer, the tools and the renderer need to know about such a type:
its kinds, defaults, typed fields (ADR-0177) and well-formedness rules. Deployment diagrams (a configurable catalogue)
and sequence diagrams (an own model) have no profile. Pure domain code: imports `model` only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import class_diagram, component_diagram, state_machine, use_case
from .model import (
    ACTIVITY_KINDS,
    CLASS_KINDS,
    COMPONENT_KINDS,
    STATE_MACHINE_KINDS,
    USE_CASE_KINDS,
    Diagram,
    Edge,
    KindCatalog,
    Node,
    activity_edge_problem,
    activity_rule_errors,
    single_line,
)


class FieldError(ValueError):
    """A typed field value that is not allowed; the message is written for the model and the user."""


@dataclass(frozen=True)
class Field:
    """A typed value of a node or edge kind, stored as the property `name` (ADR-0177).

    `max_items == 1` is one line of text; more is a list of lines (stored newline-joined, each line one line of text).
    """

    name: str
    max_items: int = 1
    max_length: int = 120
    help: str = ""  # one sentence for the tool schema the model reads
    flag: bool = False  # true or absent: stored as "true", false removes it
    check: Callable[[str], str | None] | None = None  # why a (non-empty) text is not allowed

    @property
    def is_list(self) -> bool:
        return self.max_items > 1


NodeCheck = Callable[[Diagram, Node], str | None]
EdgeCheck = Callable[[Diagram, Edge], str | None]
RuleErrors = Callable[[Diagram], list[str]]


def _no_problem(diagram: Diagram, element: Any) -> str | None:
    return None


def _no_errors(diagram: Diagram) -> list[str]:
    return []


@dataclass(frozen=True)
class GraphProfile:
    type: str
    kinds: KindCatalog
    default_node_kind: str
    default_edge_kind: str
    directed_noun: str | None = None  # set: edges are always directed (`direction` must be unset); names them in errors
    guard: bool = False  # edges carry the top-level `guard` argument, stored as the property `guard` (ADR-0034)
    node_fields: Mapping[str, tuple[Field, ...]] = field(default_factory=dict)  # by node kind
    edge_fields: Mapping[str, tuple[Field, ...]] = field(default_factory=dict)  # by edge kind
    # `node` / `edge` are candidates: the element as it would be stored (its parent and kind, its ends and kind).
    node_problem: NodeCheck = _no_problem  # why `node` may not have this kind, parent or these children
    edge_problem: EdgeCheck = _no_problem  # why `edge` may not join its ends
    rule_errors: RuleErrors = _no_errors  # whole-diagram rules of an import

    def fields_of_node(self, kind: str) -> tuple[Field, ...]:
        return self.node_fields.get(kind, ())

    def fields_of_edge(self, kind: str) -> tuple[Field, ...]:
        return self.edge_fields.get(kind, ())

    def reserved_node_keys(self) -> frozenset[str]:
        return frozenset(f.name for fields in self.node_fields.values() for f in fields)

    def reserved_edge_keys(self) -> frozenset[str]:
        return frozenset(f.name for fields in self.edge_fields.values() for f in fields)


# ---- typed fields -------------------------------------------------------------------------------------------------


def clean_fields(owner: str, defined: tuple[Field, ...], raw: Any) -> dict[str, str | None]:
    """The property values a `fields` argument stands for: `{name: stored text}`, None where the field is removed
    (an empty text or an empty list). `owner` names the element in errors ("«class» nodes of class diagrams")."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise FieldError("fields must be an object of field names and values.")
    known = {f.name: f for f in defined}
    unknown = [str(k) for k in raw if k not in known]
    if unknown:
        allowed = ", ".join(known) or "none"
        raise FieldError(f"{owner} have no field {', '.join(repr(u) for u in unknown)}. Fields: {allowed}.")
    return {name: _clean_value(known[name], value) for name, value in raw.items()}


def _clean_value(spec: Field, value: Any) -> str | None:
    if spec.flag:
        if not isinstance(value, bool):
            raise FieldError(f"field '{spec.name}' must be true or false.")
        return "true" if value else None
    if spec.is_list:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise FieldError(f"field '{spec.name}' must be a list of strings.")
        lines = [line for line in (single_line(item).strip() for item in value) if line]
        if len(lines) > spec.max_items:
            raise FieldError(f"field '{spec.name}' takes at most {spec.max_items} lines, got {len(lines)}.")
        for line in lines:
            _check_length(spec, line)
        return "\n".join(lines) or None
    if not isinstance(value, str):
        raise FieldError(f"field '{spec.name}' must be a string (an empty string removes it).")
    text = single_line(value).strip()
    _check_length(spec, text)
    if text and spec.check is not None:
        problem = spec.check(text)
        if problem:
            raise FieldError(f"field '{spec.name}': {problem}.")
    return text or None


def _check_length(spec: Field, text: str) -> None:
    if len(text) > spec.max_length:
        raise FieldError(f"field '{spec.name}' takes at most {spec.max_length} characters per line, got {len(text)}.")


def reserved_key_error(keys: frozenset[str], used: list[str], owner: str) -> str | None:
    """The message for `properties`/`remove_properties` keys that a typed field owns, else None."""
    clash = sorted(k for k in used if k in keys)
    if not clash:
        return None
    names = ", ".join(f"fields.{k}" for k in clash)
    return f"The key(s) {', '.join(repr(k) for k in clash)} belong to typed fields of {owner}: use {names}."


def field_errors(diagram: Diagram, profile: GraphProfile) -> list[str]:
    """What an import must satisfy about stored typed fields: bounds, lines of text without control characters, and no
    property that a field of the type owns on a kind that has no such field."""
    errors: list[str] = []
    node_keys, edge_keys = profile.reserved_node_keys(), profile.reserved_edge_keys()
    for node in diagram.nodes.values():
        owner, own = f"node '{node.id}'", profile.fields_of_node(node.kind)
        errors += _stored_errors(owner, own, node.properties)
        errors += _foreign_keys(owner, node.kind, own, node_keys, node.properties)
    for edge in diagram.edges:
        owner, own = f"edge '{edge.id}'", profile.fields_of_edge(edge.kind)
        errors += _stored_errors(owner, own, edge.properties)
        errors += _foreign_keys(owner, edge.kind, own, edge_keys, edge.properties)
    return errors


def _foreign_keys(
    owner: str, kind: str, own: tuple[Field, ...], reserved: frozenset[str], properties: Mapping[str, str]
) -> list[str]:
    names = {f.name for f in own}
    return [
        f"{owner}: '{key}' is a field of other kinds, not of «{kind}»" for key in properties if key in reserved - names
    ]


def _stored_errors(owner: str, defined: tuple[Field, ...], properties: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    for spec in defined:
        stored = properties.get(spec.name)
        if stored is None:
            continue
        if spec.flag:
            if stored != "true":
                errors.append(f"{owner}: field '{spec.name}' is 'true' when set, got '{stored}'")
            continue
        lines = stored.split("\n") if spec.is_list else [stored]
        if len(lines) > spec.max_items:
            errors.append(f"{owner}: field '{spec.name}' takes at most {spec.max_items} lines, got {len(lines)}")
        for line in lines:
            if not line or single_line(line) != line or line != line.strip():
                errors.append(f"{owner}: field '{spec.name}' has a line that is empty or not plain one-line text")
            elif len(line) > spec.max_length:
                errors.append(f"{owner}: field '{spec.name}' has a line longer than {spec.max_length} characters")
            elif spec.check is not None and spec.check(line):
                errors.append(f"{owner}: field '{spec.name}': {spec.check(line)}")
    return errors


# ---- the registry -------------------------------------------------------------------------------------------------


def _activity_edge_problem(diagram: Diagram, edge: Edge) -> str | None:
    src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
    if src is None or tgt is None:
        return None
    return activity_edge_problem(src.kind, tgt.kind)


def _association_fields() -> tuple[Field, ...]:
    """Multiplicities and role names on the two ends of an association, aggregation or composition."""
    mult = "multiplicity of the {} end: 1, *, 0..1, 1..*, 2..5."
    role = "Role name at the {} end."
    return (
        Field(
            "source_multiplicity",
            max_length=8,
            help=f"The {mult.format('source')}",
            check=class_diagram.multiplicity_problem,
        ),
        Field(
            "target_multiplicity",
            max_length=8,
            help=f"The {mult.format('target')}",
            check=class_diagram.multiplicity_problem,
        ),
        Field("source_role", max_length=40, help=role.format("source")),
        Field("target_role", max_length=40, help=role.format("target")),
    )


PROFILES: dict[str, GraphProfile] = {
    "activity": GraphProfile(
        type="activity",
        kinds=ACTIVITY_KINDS,
        default_node_kind="opaque_action",
        default_edge_kind="control_flow",
        directed_noun="ActivityEdges",
        guard=True,
        edge_problem=_activity_edge_problem,
        rule_errors=activity_rule_errors,
    ),
    "state_machine": GraphProfile(
        type="state_machine",
        kinds=STATE_MACHINE_KINDS,
        default_node_kind="state",
        default_edge_kind="transition",
        directed_noun="Transitions",
        guard=True,
        node_fields={
            "state": (
                Field("entry", help="What happens when the state is entered, e.g. 'Rechnung senden'."),
                Field("do", help="What goes on while the state is active, e.g. 'Zahlung prüfen'."),
                Field("exit", help="What happens when the state is left."),
            )
        },
        edge_fields={"transition": (Field("effect", help="What happens on the transition, shown after '/'."),)},
        node_problem=state_machine.node_problem,
        edge_problem=state_machine.edge_problem,
        rule_errors=state_machine.rule_errors,
    ),
    "class": GraphProfile(
        type="class",
        kinds=CLASS_KINDS,
        default_node_kind="class",
        default_edge_kind="association",
        node_fields={
            "class": (
                Field(
                    "attributes",
                    class_diagram.MEMBER_LINES,
                    class_diagram.MEMBER_LENGTH,
                    "Attributes, one per line, e.g. '- id: UUID'.",
                ),
                Field(
                    "operations",
                    class_diagram.MEMBER_LINES,
                    class_diagram.MEMBER_LENGTH,
                    "Operations, one per line, e.g. '+ place(items: Item[*]): Order'.",
                ),
                Field("abstract", flag=True, help="true marks an abstract class, shown as «abstract»."),
            ),
            "interface": (
                Field(
                    "operations", class_diagram.MEMBER_LINES, class_diagram.MEMBER_LENGTH, "Operations, one per line."
                ),
            ),
            "enumeration": (
                Field(
                    "literals",
                    class_diagram.MEMBER_LINES,
                    class_diagram.LITERAL_LENGTH,
                    "The values, one per line, e.g. 'OFFEN'.",
                ),
            ),
            "data_type": (
                Field(
                    "attributes", class_diagram.MEMBER_LINES, class_diagram.MEMBER_LENGTH, "Attributes, one per line."
                ),
                Field(
                    "operations", class_diagram.MEMBER_LINES, class_diagram.MEMBER_LENGTH, "Operations, one per line."
                ),
            ),
        },
        edge_fields=dict.fromkeys(class_diagram.ASSOCIATIONS, _association_fields()),
        edge_problem=class_diagram.edge_problem,
        rule_errors=class_diagram.rule_errors,
    ),
    "use_case": GraphProfile(
        type="use_case",
        kinds=USE_CASE_KINDS,
        default_node_kind="use_case",
        default_edge_kind="association",
        edge_fields={
            "extend": (
                Field("condition", help="When the extension applies, shown as [condition] after «extend»."),
                Field(
                    "extension_point", max_length=60, help="The extension point of the extended use case, shown in ( )."
                ),
            )
        },
        node_problem=use_case.node_problem,
        edge_problem=use_case.edge_problem,
        rule_errors=use_case.rule_errors,
    ),
    "component": GraphProfile(
        type="component",
        kinds=COMPONENT_KINDS,
        default_node_kind="component",
        default_edge_kind="connection",
        node_fields={
            "component": (
                Field(
                    component_diagram.PORTS,
                    component_diagram.MAX_PORTS,
                    component_diagram.PORT_LENGTH,
                    "Ports, one per line: the name, optionally ': Type', e.g. 'orders-in: OrderApi'.",
                ),
            )
        },
        edge_fields={
            "connection": (
                Field(
                    "source_port",
                    max_length=component_diagram.PORT_LENGTH,
                    help="Name of a port of the source component.",
                ),
                Field(
                    "target_port",
                    max_length=component_diagram.PORT_LENGTH,
                    help="Name of a port of the target component.",
                ),
            ),
            "realization": (
                Field(
                    "source_port",
                    max_length=component_diagram.PORT_LENGTH,
                    help="Name of the port of the component that provides the interface.",
                ),
            ),
            "usage": (
                Field(
                    "source_port",
                    max_length=component_diagram.PORT_LENGTH,
                    help="Name of the port of the component that requires the interface.",
                ),
            ),
        },
        node_problem=component_diagram.node_problem,
        edge_problem=component_diagram.edge_problem,
        rule_errors=component_diagram.rule_errors,
    ),
}


def profile_of(diagram_type: str) -> GraphProfile | None:
    """The profile of a graph type with a fixed vocabulary; None for deployment and sequence diagrams."""
    return PROFILES.get(diagram_type)


def edge_may_be_undirected(diagram_type: str) -> bool:
    """Whether edges of this type may carry a `direction` (everything but the always-directed graph types)."""
    profile = PROFILES.get(diagram_type)
    return profile is None or profile.directed_noun is None


def guard_types() -> tuple[str, ...]:
    return tuple(t for t, p in PROFILES.items() if p.guard)
