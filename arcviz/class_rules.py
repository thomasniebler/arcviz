"""Review findings of class diagrams (spec: docs/architecture/specs/class-diagrams.md, ADR-0022).

Registered for the type `class` in `rules.RULES`; imported at the end of `rules.py`. Every rule is a pure function of the
diagram and linear in its nodes and edges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from .class_diagram import (
    ASSOCIATIONS,
    CLASS,
    CLASSIFIERS,
    ENUMERATION,
    INTERFACE,
    PACKAGE,
    is_abstract,
    lines_of,
    member_key,
)
from .i18n import N_, _
from .model import Diagram, Edge, Node
from .rules import Finding, Rule, components, finding, register

TYPE = "class"


def _name(node: Node) -> str:
    return _("„{name}“", name=node.name)


def _top_package(diagram: Diagram, node: Node) -> str | None:
    """The outermost package that holds `node`, None when it is outside every package."""
    top: str | None = None
    seen = {node.id}
    current = node
    while current.parent in diagram.nodes and current.parent not in seen:
        current = diagram.nodes[current.parent]
        seen.add(current.id)
        if current.kind == PACKAGE:
            top = current.id
    return top


def package_cycle(diagram: Diagram) -> Iterator[Finding]:
    """Packages that depend on each other: an edge from a node in one top-level package to a node in another one."""
    outgoing: dict[str, list[Edge]] = defaultdict(list)  # package -> one pseudo edge per coupling to another package
    carried: dict[tuple[str, str], list[str]] = defaultdict(list)
    for edge in diagram.edges:
        src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
        if src is None or tgt is None:
            continue
        a, b = _top_package(diagram, src), _top_package(diagram, tgt)
        if a is None or b is None or a == b:
            continue
        if not carried[(a, b)]:
            outgoing[a].append(Edge(id=f"{a}>{b}", source=a, target=b))
        carried[(a, b)].append(edge.id)
    packages = sorted({p for pair in carried for p in pair})
    for component in components(packages, outgoing):
        if len(component) < 2:
            continue
        members = sorted(component)
        names = ", ".join(_name(diagram.nodes[p]) for p in members)
        edges = sorted(e for (a, b), ids in carried.items() if a in component and b in component for e in ids)
        yield finding(
            "class-package-cycle",
            "risk",
            "medium",
            _("Die Pakete {names} hängen im Kreis voneinander ab.", names=names),
            members,
            edges,
            suggestion=_(
                "Löse die Abhängigkeiten zwischen {member_ids} auf: führe eine Schnittstelle ein oder verschiebe die gemeinsam genutzten Klassen in ein eigenes Paket.",
                member_ids=", ".join(members),
            ),
        )


def multiple_wholes(diagram: Diagram) -> Iterator[Finding]:
    """A composition points from the part to its whole, so a part with several outgoing compositions has several wholes."""
    by_part: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "composition":
            by_part[edge.source].append(edge)
    for part_id, edges in by_part.items():
        if len(edges) > 1 and part_id in diagram.nodes:
            part = diagram.nodes[part_id]
            yield finding(
                "class-multiple-wholes",
                "risk",
                "medium",
                _(
                    "{name_part} ist Teil von {count} Ganzen (Komposition); ein Teil gehört genau einem Ganzen.",
                    name_part=_name(part),
                    count=len(edges),
                ),
                [part.id, *(e.target for e in edges)],
                [e.id for e in edges],
                suggestion=_(
                    "Lasse {part_id} nur von einem Ganzen komponieren; die übrigen Beziehungen werden zu Assoziationen oder Aggregationen.",
                    part_id=part.id,
                ),
            )


def composition_cycle(diagram: Diagram) -> Iterator[Finding]:
    outgoing: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "composition" and edge.source in diagram.nodes and edge.target in diagram.nodes:
            outgoing[edge.source].append(edge)
    nodes = sorted({n for e in sum(outgoing.values(), []) for n in (e.source, e.target)})
    for component in components(nodes, outgoing):
        members = sorted(component)
        loops = len(component) > 1 or any(e.target == component[0] for e in outgoing.get(component[0], []))
        if not loops:
            continue
        edges = [e.id for m in members for e in outgoing.get(m, []) if e.target in component]
        yield finding(
            "class-composition-cycle",
            "risk",
            "medium",
            _(
                "Die Kompositionen zwischen {names} bilden einen Kreis: niemand könnte als Erstes entstehen.",
                names=", ".join(_name(diagram.nodes[m]) for m in members),
            ),
            members,
            edges,
            suggestion=_("Ersetze eine Komposition im Kreis durch eine Assoziation."),
        )


def multiple_inheritance(diagram: Diagram) -> Iterator[Finding]:
    parents: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.kind == "generalization":
            parents[edge.source].append(edge)
    for source, edges in parents.items():
        node = diagram.nodes.get(source)
        if node is not None and node.kind == CLASS and len(edges) > 1:
            yield finding(
                "class-multiple-inheritance",
                "suggestion",
                "low",
                _(
                    "Die Klasse {name_node} erbt von {count} Klassen; Mehrfachvererbung ist selten nötig.",
                    name_node=_name(node),
                    count=len(edges),
                ),
                [node.id],
                [e.id for e in edges],
                suggestion=_(
                    "Prüfe, ob {node_id} von einer Klasse erben und die übrigen als Schnittstellen realisieren kann.",
                    node_id=node.id,
                ),
            )


def interface_unrealized(diagram: Diagram) -> Iterator[Finding]:
    realized = {e.target for e in diagram.edges if e.kind in ("realization", "generalization")}
    for node in diagram.nodes.values():
        if node.kind == INTERFACE and node.id not in realized:
            yield finding(
                "class-interface-unrealized",
                "suggestion",
                "low",
                _("Die Schnittstelle {name_node} wird von keiner Klasse realisiert.", name_node=_name(node)),
                [node.id],
                suggestion=_(
                    "Lasse eine Klasse {node_id} realisieren (realization von der Klasse zur Schnittstelle).",
                    node_id=node.id,
                ),
            )


def abstract_leaf(diagram: Diagram) -> Iterator[Finding]:
    specialised = {e.target for e in diagram.edges if e.kind == "generalization"}
    for node in diagram.nodes.values():
        if node.kind == CLASS and is_abstract(node) and node.id not in specialised:
            yield finding(
                "class-abstract-leaf",
                "suggestion",
                "low",
                _(
                    "Die abstrakte Klasse {name_node} hat keine Unterklasse und kann nicht instanziiert werden.",
                    name_node=_name(node),
                ),
                [node.id],
                suggestion=_(
                    "Füge eine Unterklasse von {node_id} hinzu oder mache die Klasse konkret.", node_id=node.id
                ),
            )


def isolated(diagram: Diagram) -> Iterator[Finding]:
    classifiers = [n for n in diagram.nodes.values() if n.kind in CLASSIFIERS]
    if len(classifiers) < 3:
        return
    connected = {n for e in diagram.edges for n in (e.source, e.target)}
    for node in classifiers:
        if node.id not in connected:
            yield finding(
                "class-isolated",
                "suggestion",
                "low",
                _("{name_node} hat keine Beziehung zu anderen Klassen.", name_node=_name(node)),
                [node.id],
                suggestion=_(
                    "Verbinde {node_id} mit den Klassen, die es nutzt oder von denen es genutzt wird, oder entferne es.",
                    node_id=node.id,
                ),
            )


def empty_classifier(diagram: Diagram) -> Iterator[Finding]:
    for node in diagram.nodes.values():
        if node.kind == CLASS and not lines_of(node, "attributes") and not lines_of(node, "operations"):
            what = _("Attribute und Operationen")
        elif node.kind == INTERFACE and not lines_of(node, "operations"):
            what = _("Operationen")
        else:
            continue
        yield finding(
            "class-empty",
            "suggestion",
            "low",
            _("{name_node} hat noch keine {what}.", name_node=_name(node), what=what),
            [node.id],
            suggestion=_(
                "Ergänze {what} von {node_id}, oder lasse es als Begriff im Glossar stehen.", what=what, node_id=node.id
            ),
        )


def association_without_multiplicity(diagram: Diagram) -> Iterator[Finding]:
    for edge in diagram.edges:
        if edge.kind in ASSOCIATIONS and not (
            edge.properties.get("source_multiplicity") or edge.properties.get("target_multiplicity")
        ):
            src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
            if src is None or tgt is None:
                continue
            yield finding(
                "class-association-without-multiplicity",
                "suggestion",
                "low",
                _(
                    "Die Beziehung zwischen {name_src} und {name_tgt} hat keine Multiplizitäten.",
                    name_src=_name(src),
                    name_tgt=_name(tgt),
                ),
                [src.id, tgt.id],
                [edge.id],
                suggestion=_(
                    "Gib an beiden Enden von {edge_id} an, wie viele Objekte beteiligt sind (zum Beispiel 1 und 0..*).",
                    edge_id=edge.id,
                ),
            )


def duplicate_member(diagram: Diagram) -> Iterator[Finding]:
    for node in diagram.nodes.values():
        for field, operation in (("attributes", False), ("operations", True)):
            seen: dict[str, str] = {}
            duplicates: list[str] = []
            for line in lines_of(node, field):
                key = member_key(line, operation)
                if key in seen and seen[key] not in duplicates:
                    duplicates.append(seen[key])
                seen.setdefault(key, line)
            for line in duplicates:
                yield finding(
                    "class-duplicate-member",
                    "risk",
                    "low",
                    _("{name_node} führt „{line}“ mehrfach auf.", name_node=_name(node), line=line),
                    [node.id],
                    suggestion=_(
                        "Entferne die doppelte Zeile aus {field} von {node_id}.", field=field, node_id=node.id
                    ),
                )


def enumeration_empty(diagram: Diagram) -> Iterator[Finding]:
    for node in diagram.nodes.values():
        if node.kind == ENUMERATION and not lines_of(node, "literals"):
            yield finding(
                "class-enumeration-empty",
                "suggestion",
                "low",
                _("Die Aufzählung {name_node} hat keine Werte.", name_node=_name(node)),
                [node.id],
                suggestion=_("Ergänze die Werte (literals) von {node_id}.", node_id=node.id),
            )


for _rule in (
    Rule("class-package-cycle", N_("Pakete mit zyklischen Abhängigkeiten"), "risk", package_cycle),
    Rule("class-multiple-wholes", N_("Teil mit mehreren Ganzen"), "risk", multiple_wholes),
    Rule("class-composition-cycle", N_("Kompositionen im Kreis"), "risk", composition_cycle),
    Rule("class-duplicate-member", N_("Doppeltes Attribut oder doppelte Operation"), "risk", duplicate_member),
    Rule("class-multiple-inheritance", N_("Klasse mit mehreren Oberklassen"), "suggestion", multiple_inheritance),
    Rule(
        "class-interface-unrealized", N_("Schnittstelle ohne realisierende Klasse"), "suggestion", interface_unrealized
    ),
    Rule("class-abstract-leaf", N_("Abstrakte Klasse ohne Unterklasse"), "suggestion", abstract_leaf),
    Rule("class-isolated", N_("Klasse ohne Beziehung"), "suggestion", isolated),
    Rule("class-empty", N_("Klasse ohne Attribute und Operationen"), "suggestion", empty_classifier),
    Rule(
        "class-association-without-multiplicity",
        N_("Beziehung ohne Multiplizitäten"),
        "suggestion",
        association_without_multiplicity,
    ),
    Rule("class-enumeration-empty", N_("Aufzählung ohne Werte"), "suggestion", enumeration_empty),
):
    register(TYPE, _rule)
