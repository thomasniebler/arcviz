"""Review findings of use case diagrams (spec: docs/architecture/specs/use-case-diagrams.md, ADR-0022).

Registered for the type `use_case` in `rules.RULES`; imported at the end of `rules.py`. Every rule is a pure function of the
diagram and linear in its nodes and edges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from .i18n import N_, _
from .model import Diagram, Edge, Node
from .rules import Finding, Rule, components, finding, register
from .use_case import ACTOR, SUBJECT, USE_CASE

TYPE = "use_case"


def _name(node: Node) -> str:
    return _("„{name}“", name=node.name)


def _use_cases(diagram: Diagram) -> list[Node]:
    return [n for n in diagram.nodes.values() if n.kind == USE_CASE]


def without_actor(diagram: Diagram) -> Iterator[Finding]:
    """A user goal is a use case that is neither included nor an extension; it needs an actor that starts it."""
    included = {e.target for e in diagram.edges if e.kind == "include"}
    extending = {e.source for e in diagram.edges if e.kind == "extend"}
    served = {
        end
        for e in diagram.edges
        if e.kind == "association"
        for end in (e.source, e.target)
        if diagram.nodes.get(end) is not None and diagram.nodes[end].kind == USE_CASE
    }
    for node in _use_cases(diagram):
        if node.id in included or node.id in extending or node.id in served:
            continue
        yield finding(
            "usecase-without-actor",
            "risk",
            "medium",
            _("Der Anwendungsfall {name_node} hat keinen Akteur, der ihn auslöst.", name_node=_name(node)),
            [node.id],
            suggestion=_(
                "Verbinde {node_id} mit dem Akteur, der das Ziel verfolgt (association), oder lasse ihn von einem anderen Anwendungsfall einbinden (include).",
                node_id=node.id,
            ),
        )


def actor_unused(diagram: Diagram) -> Iterator[Finding]:
    used = {end for e in diagram.edges for end in (e.source, e.target)}
    for node in diagram.nodes.values():
        if node.kind == ACTOR and node.id not in used:
            yield finding(
                "usecase-actor-unused",
                "suggestion",
                "low",
                _("Der Akteur {name_node} nutzt keinen Anwendungsfall.", name_node=_name(node)),
                [node.id],
                suggestion=_(
                    "Verbinde {node_id} mit den Anwendungsfällen, die er nutzt, oder entferne ihn.", node_id=node.id
                ),
            )


def without_specification(diagram: Diagram) -> Iterator[Finding]:
    for node in _use_cases(diagram):
        if not (node.description or "").strip():
            yield finding(
                "usecase-without-specification",
                "suggestion",
                "low",
                _(
                    "Spezifikation fehlt: {name_node} hat keine Beschreibung (Ziel, Vorbedingung, Ablauf).",
                    name_node=_name(node),
                ),
                [node.id],
                suggestion=_(
                    "Schreibe die Spezifikation von {node_id} in die Notiz: Ziel, Vorbedingung, Nachbedingung, Hauptablauf, Alternativen.",
                    node_id=node.id,
                ),
            )


def extend_without_condition(diagram: Diagram) -> Iterator[Finding]:
    for edge in diagram.edges:
        if edge.kind != "extend" or edge.properties.get("condition", "").strip():
            continue
        src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
        if src is None or tgt is None:
            continue
        yield finding(
            "usecase-extend-without-condition",
            "suggestion",
            "low",
            _(
                "{name_src} erweitert {name_tgt} ohne Bedingung: unklar, wann die Erweiterung gilt.",
                name_src=_name(src),
                name_tgt=_name(tgt),
            ),
            [src.id, tgt.id],
            [edge.id],
            suggestion=_(
                "Gib an {edge_id} die Bedingung an, unter der {src_id} {tgt_id} erweitert (condition).",
                edge_id=edge.id,
                src_id=src.id,
                tgt_id=tgt.id,
            ),
        )


def extend_cycle(diagram: Diagram) -> Iterator[Finding]:
    outgoing: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "extend" and edge.source in diagram.nodes and edge.target in diagram.nodes:
            outgoing[edge.source].append(edge)
    nodes = sorted({n for edges in outgoing.values() for e in edges for n in (e.source, e.target)})
    for component in components(nodes, outgoing):
        if len(component) < 2:
            continue
        members = [n for n in diagram.nodes if n in component]
        edges = [e.id for e in diagram.edges if e.kind == "extend" and e.source in component and e.target in component]
        yield finding(
            "usecase-extend-cycle",
            "risk",
            "medium",
            _(
                "Die Erweiterungen zwischen {names} bilden einen Kreis.",
                names=", ".join(_name(diagram.nodes[m]) for m in members),
            ),
            members,
            edges,
            suggestion=_("Löse den Kreis auf: ein Anwendungsfall kann sich nicht (über andere) selbst erweitern."),
        )


def include_used_once(diagram: Diagram) -> Iterator[Finding]:
    into: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "include":
            into[edge.target].append(edge)
    for target, edges in into.items():
        if len(edges) == 1 and target in diagram.nodes:
            node = diagram.nodes[target]
            yield finding(
                "usecase-include-used-once",
                "suggestion",
                "low",
                _(
                    "{name_node} wird nur von einem Anwendungsfall eingebunden; das Herauslösen lohnt sich erst bei Wiederverwendung.",
                    name_node=_name(node),
                ),
                [node.id],
                [edges[0].id],
                suggestion=_(
                    "Behalte {node_id} getrennt, wenn ein zweiter Anwendungsfall ihn nutzt; sonst gehört der Ablauf in den einbindenden Anwendungsfall.",
                    node_id=node.id,
                ),
            )


def outside_subject(diagram: Diagram) -> Iterator[Finding]:
    if not any(n.kind == SUBJECT for n in diagram.nodes.values()):
        return
    for node in _use_cases(diagram):
        if node.parent is None:
            yield finding(
                "usecase-outside-subject",
                "suggestion",
                "low",
                _("Der Anwendungsfall {name_node} liegt außerhalb jedes Systems (Subject).", name_node=_name(node)),
                [node.id],
                suggestion=_("Verschiebe {node_id} in das System, das ihn bereitstellt (move_node).", node_id=node.id),
            )


def subject_empty(diagram: Diagram) -> Iterator[Finding]:
    parents = {n.parent for n in diagram.nodes.values()}
    for node in diagram.nodes.values():
        if node.kind == SUBJECT and node.id not in parents:
            yield finding(
                "usecase-subject-empty",
                "suggestion",
                "low",
                _("Das System {name_node} enthält keinen Anwendungsfall.", name_node=_name(node)),
                [node.id],
                suggestion=_("Lege die Anwendungsfälle von {node_id} an oder entferne das System.", node_id=node.id),
            )


def duplicate_name(diagram: Diagram) -> Iterator[Finding]:
    for kind in (USE_CASE, ACTOR):
        groups: dict[str, list[Node]] = defaultdict(list)
        for node in diagram.nodes.values():
            if node.kind == kind:
                groups[node.name.strip().casefold()].append(node)
        for nodes in groups.values():
            if len(nodes) > 1:
                what = _("Anwendungsfälle") if kind == USE_CASE else _("Akteure")
                yield finding(
                    "usecase-duplicate-name",
                    "risk",
                    "low",
                    _("Mehrere {what} heißen „{nodes_0_name}“.", what=what, nodes_0_name=nodes[0].name),
                    [n.id for n in nodes],
                    suggestion=_("Benenne die {what} eindeutig oder führe sie zusammen.", what=what),
                )


for _rule in (
    Rule("usecase-without-actor", N_("Anwendungsfall ohne Akteur"), "risk", without_actor),
    Rule("usecase-extend-cycle", N_("Erweiterungen im Kreis"), "risk", extend_cycle),
    Rule("usecase-duplicate-name", N_("Gleiche Namen"), "risk", duplicate_name),
    Rule("usecase-actor-unused", N_("Akteur ohne Anwendungsfall"), "suggestion", actor_unused),
    Rule("usecase-without-specification", N_("Anwendungsfall ohne Spezifikation"), "suggestion", without_specification),
    Rule("usecase-extend-without-condition", N_("Erweiterung ohne Bedingung"), "suggestion", extend_without_condition),
    Rule(
        "usecase-include-used-once",
        N_("Eingebundener Anwendungsfall nur einmal genutzt"),
        "suggestion",
        include_used_once,
    ),
    Rule("usecase-outside-subject", N_("Anwendungsfall außerhalb des Systems"), "suggestion", outside_subject),
    Rule("usecase-subject-empty", N_("System ohne Anwendungsfall"), "suggestion", subject_empty),
):
    register(TYPE, _rule)
