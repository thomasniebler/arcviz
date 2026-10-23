"""Review findings of component diagrams (spec: docs/architecture/specs/component-diagrams.md, ADR-0022).

Registered for the type `component` in `rules.RULES`; imported at the end of `rules.py`. Every rule is a pure function of
the diagram and linear in its nodes and edges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from .component_diagram import BUILDING_BLOCKS, COMPONENT, SUBSYSTEM, port_names
from .i18n import N_, _
from .model import Diagram, Edge, Node
from .rules import Finding, Rule, components, finding, register

TYPE = "component"
MAX_FAN_OUT = 7  # a component with more distinct dependencies than this is a finding


def _name(node: Node) -> str:
    return _("„{name}“", name=node.name)


def _providers(diagram: Diagram) -> dict[str, list[Edge]]:
    """Interface id -> the realizations that provide it."""
    out: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "realization":
            out[edge.target].append(edge)
    return out


def interface_unprovided(diagram: Diagram) -> Iterator[Finding]:
    provided = _providers(diagram)
    used: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "usage":
            used[edge.target].append(edge)
    for interface_id, edges in used.items():
        node = diagram.nodes.get(interface_id)
        if node is None or provided.get(interface_id):
            continue
        yield finding(
            "component-interface-unprovided",
            "risk",
            "medium",
            _(
                "Die Schnittstelle {name_node} wird benötigt, aber von keiner Komponente bereitgestellt.",
                name_node=_name(node),
            ),
            [node.id, *dict.fromkeys(e.source for e in edges)],
            [e.id for e in edges],
            suggestion=_(
                "Lasse eine Komponente {node_id} bereitstellen (realization von der Komponente zur Schnittstelle).",
                node_id=node.id,
            ),
        )


def interface_unused(diagram: Diagram) -> Iterator[Finding]:
    provided = _providers(diagram)
    used = {e.target for e in diagram.edges if e.kind == "usage"}
    for interface_id, edges in provided.items():
        node = diagram.nodes.get(interface_id)
        if node is None or interface_id in used:
            continue
        yield finding(
            "component-interface-unused",
            "suggestion",
            "low",
            _(
                "Die Schnittstelle {name_node} wird bereitgestellt, aber von keiner Komponente genutzt.",
                name_node=_name(node),
            ),
            [node.id],
            [e.id for e in edges],
            suggestion=_(
                "Lasse eine Komponente {node_id} nutzen (usage), oder entferne die Schnittstelle.", node_id=node.id
            ),
        )


def _dependencies(diagram: Diagram) -> tuple[dict[str, list[Edge]], dict[str, set[str]]]:
    """The dependencies between components and subsystems: connections and dependencies as drawn, and a usage followed by
    the realizations of its interface (the user depends on every provider). Returns pseudo edges by source for the
    strongly connected components and, by source, the set of distinct targets."""
    providers = _providers(diagram)
    outgoing: dict[str, list[Edge]] = defaultdict(list)
    targets: dict[str, set[str]] = defaultdict(set)

    def add(source: str, target: str, via: str) -> None:
        if source != target:
            outgoing[source].append(Edge(id=via, source=source, target=target))
            targets[source].add(target)

    for edge in diagram.edges:
        src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
        if src is None or tgt is None or src.kind not in BUILDING_BLOCKS:
            continue
        if edge.kind in ("connection", "dependency") and tgt.kind in BUILDING_BLOCKS:
            add(src.id, tgt.id, edge.id)
        elif edge.kind == "usage":
            for provider in providers.get(tgt.id, []):
                add(src.id, provider.source, edge.id)
    return outgoing, targets


def dependency_cycle(diagram: Diagram) -> Iterator[Finding]:
    outgoing, _incoming = _dependencies(diagram)
    nodes = sorted({n for edges in outgoing.values() for e in edges for n in (e.source, e.target)})
    for component in components(nodes, outgoing):
        if len(component) < 2:
            continue
        members = [n for n in diagram.nodes if n in component]
        edges = sorted({e.id for m in members for e in outgoing.get(m, []) if e.target in component})
        yield finding(
            "component-dependency-cycle",
            "risk",
            "medium",
            _(
                "Die Komponenten {names} hängen im Kreis voneinander ab.",
                names=", ".join(_name(diagram.nodes[m]) for m in members),
            ),
            members,
            edges,
            suggestion=_(
                "Löse den Kreis auf: kehre eine Abhängigkeit über eine Schnittstelle um oder lagere das Gemeinsame in eine eigene Komponente aus."
            ),
        )


def fan_out(diagram: Diagram) -> Iterator[Finding]:
    _sources, targets = _dependencies(diagram)
    for source, found in targets.items():
        if len(found) > MAX_FAN_OUT:
            node = diagram.nodes[source]
            yield finding(
                "component-fan-out",
                "suggestion",
                "low",
                _(
                    "{name_node} hängt von {len_found} anderen Komponenten ab; das spricht für zu viele Aufgaben.",
                    name_node=_name(node),
                    len_found=len(found),
                ),
                [node.id],
                suggestion=_("Prüfe, ob sich {node_id} in kleinere Komponenten teilen lässt.", node_id=node.id),
            )


def isolated(diagram: Diagram) -> Iterator[Finding]:
    blocks = [n for n in diagram.nodes.values() if n.kind == COMPONENT]
    if len(blocks) < 3:
        return
    connected = {n for e in diagram.edges for n in (e.source, e.target)}
    for node in blocks:
        if node.id not in connected:
            yield finding(
                "component-isolated",
                "suggestion",
                "low",
                _("Die Komponente {name_node} hat keine Beziehung zu anderen Bausteinen.", name_node=_name(node)),
                [node.id],
                suggestion=_(
                    "Verbinde {node_id} mit den Bausteinen, die es nutzt oder bereitstellt, oder entferne es.",
                    node_id=node.id,
                ),
            )


def port_unconnected(diagram: Diagram) -> Iterator[Finding]:
    named: dict[str, set[str]] = defaultdict(set)
    for edge in diagram.edges:
        for end, key in ((edge.source, "source_port"), (edge.target, "target_port")):
            if edge.properties.get(key):
                named[end].add(edge.properties[key])
    for node in diagram.nodes.values():
        if node.kind != COMPONENT or not named.get(node.id):
            continue  # no edge names a port of this component: nothing to compare with
        for port in port_names(node):
            if port not in named[node.id]:
                yield finding(
                    "component-port-unconnected",
                    "suggestion",
                    "low",
                    _(
                        "Der Port „{port}“ von {name_node} ist mit keiner Kante verbunden.",
                        port=port,
                        name_node=_name(node),
                    ),
                    [node.id],
                    suggestion=_(
                        "Verbinde den Port {port} von {node_id} (source_port oder target_port einer Kante), oder entferne ihn.",
                        port=port,
                        node_id=node.id,
                    ),
                )


def subsystem_empty(diagram: Diagram) -> Iterator[Finding]:
    parents = {n.parent for n in diagram.nodes.values()}
    for node in diagram.nodes.values():
        if node.kind == SUBSYSTEM and node.id not in parents:
            yield finding(
                "component-subsystem-empty",
                "suggestion",
                "low",
                _("Das Teilsystem {name_node} enthält keine Komponente.", name_node=_name(node)),
                [node.id],
                suggestion=_("Lege die Komponenten von {node_id} an oder entferne das Teilsystem.", node_id=node.id),
            )


for _rule in (
    Rule("component-interface-unprovided", N_("Benötigte Schnittstelle ohne Anbieter"), "risk", interface_unprovided),
    Rule("component-dependency-cycle", N_("Komponenten mit zyklischen Abhängigkeiten"), "risk", dependency_cycle),
    Rule("component-interface-unused", N_("Bereitgestellte Schnittstelle ohne Nutzer"), "suggestion", interface_unused),
    Rule("component-fan-out", N_("Komponente mit vielen Abhängigkeiten"), "suggestion", fan_out),
    Rule("component-isolated", N_("Komponente ohne Beziehung"), "suggestion", isolated),
    Rule("component-port-unconnected", N_("Port ohne Verbindung"), "suggestion", port_unconnected),
    Rule("component-subsystem-empty", N_("Teilsystem ohne Komponente"), "suggestion", subsystem_empty),
):
    register(TYPE, _rule)
