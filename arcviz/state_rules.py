"""Review findings of state machine diagrams (spec: docs/architecture/specs/state-machine-diagrams.md, ADR-0022).

Registered for the type `state_machine` in `rules.RULES`; imported at the end of `rules.py`. Every rule is a pure function
of the diagram and linear in its nodes and edges.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from .i18n import N_, _
from .model import Diagram, Edge, Node, is_else_guard
from .rules import Finding, Rule, finding, register
from .state_machine import CHOICE, COMPOSITE, FINALS, FORK, INITIAL, JOIN, REGION, STATE

TYPE = "state_machine"


def _by_parent(diagram: Diagram) -> dict[str | None, list[Node]]:
    out: dict[str | None, list[Node]] = defaultdict(list)
    for node in diagram.nodes.values():
        out[node.parent].append(node)
    return out


def _outgoing(diagram: Diagram) -> dict[str, list[Edge]]:
    out: dict[str, list[Edge]] = defaultdict(list)
    for edge in diagram.edges:
        if edge.source in diagram.nodes and edge.target in diagram.nodes:
            out[edge.source].append(edge)
    return out


def _scopes(diagram: Diagram, by_parent: dict[str | None, list[Node]]) -> Iterator[Node | None]:
    """Where one initial state belongs: the top level, every region, every composite state without regions."""
    yield None
    for node in diagram.nodes.values():
        if node.kind == REGION or (node.kind == COMPOSITE and not any(c.kind == REGION for c in by_parent[node.id])):
            yield node


def _name(node: Node) -> str:
    return _("„{name}“", name=node.name)


def no_initial(diagram: Diagram) -> Iterator[Finding]:
    by_parent = _by_parent(diagram)
    for scope in _scopes(diagram, by_parent):
        children = by_parent.get(scope.id if scope else None, [])
        states = [c for c in children if c.kind in (STATE, COMPOSITE)]
        if not states or any(c.kind == INITIAL for c in children):
            continue
        where = _("in „{name}“", name=scope.name) if scope else _("im Zustandsautomaten")
        target = scope or states[0]
        yield finding(
            "state-no-initial",
            "suggestion",
            "low",
            _("Es gibt keinen Anfangszustand {where}: unklar, in welchem Zustand der Automat beginnt.", where=where),
            [target.id],
            suggestion=_(
                "Füge {where} einen initial_pseudostate hinzu und verbinde ihn mit dem ersten Zustand (zum Beispiel {first_id}).",
                where=where,
                first_id=states[0].id,
            ),
        )


def multiple_initial(diagram: Diagram) -> Iterator[Finding]:
    by_parent = _by_parent(diagram)
    for scope in _scopes(diagram, by_parent):
        initials = [c for c in by_parent.get(scope.id if scope else None, []) if c.kind == INITIAL]
        if len(initials) > 1:
            where = _("in „{name}“", name=scope.name) if scope else _("im Zustandsautomaten")
            yield finding(
                "state-multiple-initial",
                "risk",
                "medium",
                _(
                    "Es gibt {count} Anfangszustände {where}; ein Automat beginnt nur an einer Stelle.",
                    count=len(initials),
                    where=where,
                ),
                [i.id for i in initials],
                suggestion=_("Behalte einen initial_pseudostate und entferne die übrigen."),
            )


def initial_targets(diagram: Diagram) -> Iterator[Finding]:
    out = _outgoing(diagram)
    for node in diagram.nodes.values():
        if node.kind != INITIAL or len(out[node.id]) == 1:
            continue
        edges = out[node.id]
        message = (
            _("Der Anfangszustand „{node_name}“ hat keinen Übergang.", node_name=node.name)
            if not edges
            else _(
                "Vom Anfangszustand „{node_name}“ gehen {count} Übergänge aus; es darf nur einer sein.",
                node_name=node.name,
                count=len(edges),
            )
        )
        yield finding(
            "state-initial-targets",
            "risk",
            "medium",
            message,
            [node.id],
            [e.id for e in edges],
            suggestion=_("Verbinde {node_id} mit genau einem Zustand, in dem der Automat beginnt.", node_id=node.id),
        )


def _reachable(diagram: Diagram, by_parent: dict[str | None, list[Node]], out: dict[str, list[Edge]]) -> set[str]:
    """Every node an automaton can be in or pass: from the top-level initial states along the transitions; entering a
    node enters its enclosing composite states and regions, and entering one of those takes its initial states."""
    reached: set[str] = set()
    stack = [n.id for n in by_parent.get(None, []) if n.kind == INITIAL]
    while stack:
        current = stack.pop()
        if current in reached:
            continue
        reached.add(current)
        node = diagram.nodes[current]
        if node.parent is not None:
            stack.append(node.parent)
        if node.kind in (COMPOSITE, REGION):
            stack.extend(c.id for c in by_parent.get(current, []) if c.kind in (INITIAL, REGION))
        stack.extend(e.target for e in out.get(current, []))
    return reached


def unreachable(diagram: Diagram) -> Iterator[Finding]:
    by_parent = _by_parent(diagram)
    if not any(n.kind == INITIAL for n in by_parent.get(None, [])):
        return  # state-no-initial says it; everything would be unreachable
    reached = _reachable(diagram, by_parent, _outgoing(diagram))
    for node in diagram.nodes.values():
        if node.kind == REGION or node.id in reached:
            continue
        if node.parent is not None and node.parent not in reached:
            continue  # the enclosing state is reported, not everything inside it
        yield finding(
            "state-unreachable",
            "risk",
            "medium",
            _("„{node_name}“ ist vom Anfangszustand aus nicht erreichbar.", node_name=node.name),
            [node.id],
            suggestion=_("Füge einen Übergang nach {node_id} hinzu oder entferne den Zustand.", node_id=node.id),
        )


def dead_end(diagram: Diagram) -> Iterator[Finding]:
    if not any(n.kind in FINALS for n in diagram.nodes.values()):
        return
    out = _outgoing(diagram)
    for node in diagram.nodes.values():
        if node.kind != STATE or out.get(node.id):
            continue
        ancestor, left = node.parent, False
        while ancestor is not None and not left:  # leaving an enclosing state leaves this one as well
            left = bool(out.get(ancestor))
            ancestor = diagram.nodes[ancestor].parent if ancestor in diagram.nodes else None
        if left:
            continue
        yield finding(
            "state-dead-end",
            "suggestion",
            "low",
            _(
                "Aus „{node_name}“ führt kein Übergang heraus, obwohl der Automat einen Endzustand hat.",
                node_name=node.name,
            ),
            [node.id],
            suggestion=_(
                "Füge von {node_id} einen Übergang hinzu, oder führe den Zustand zu einem final_state.", node_id=node.id
            ),
        )


def nondeterministic(diagram: Diagram) -> Iterator[Finding]:
    out = _outgoing(diagram)
    for node in diagram.nodes.values():
        if node.kind not in (STATE, COMPOSITE):
            continue
        groups: dict[str, list[Edge]] = defaultdict(list)
        for edge in out.get(node.id, []):
            groups[(edge.label or "").strip().lower()].append(edge)
        for trigger, edges in groups.items():
            if len(edges) > 1 and any(not e.properties.get("guard", "").strip() for e in edges):
                what = _("das Ereignis „{trigger}“", trigger=trigger) if trigger else _("den Abschluss")
                yield finding(
                    "state-nondeterministic",
                    "risk",
                    "medium",
                    _(
                        "Aus „{node_name}“ führen {count} Übergänge auf {what}, und nicht jeder hat eine Bedingung: es ist nicht festgelegt, welcher gilt.",
                        node_name=node.name,
                        count=len(edges),
                        what=what,
                    ),
                    [node.id],
                    [e.id for e in edges],
                    suggestion=_(
                        "Gib den Übergängen aus {node_id} auf dasselbe Ereignis einander ausschließende Bedingungen (guard).",
                        node_id=node.id,
                    ),
                )


def choice_without_else(diagram: Diagram) -> Iterator[Finding]:
    out = _outgoing(diagram)
    for node in diagram.nodes.values():
        edges = out.get(node.id, [])
        if node.kind != CHOICE or not edges:
            continue
        guards = [e.properties.get("guard", "").strip() for e in edges]
        if any(is_else_guard(g) for g in guards):
            continue
        unguarded = [e for e, g in zip(edges, guards, strict=True) if not g]
        yield finding(
            "state-choice-without-else",
            "risk",
            "medium" if unguarded else "low",
            (
                _(
                    "Die Entscheidung „{node_name}“ hat keinen else-Zweig und Übergänge ohne Bedingung.",
                    node_name=node.name,
                )
                if unguarded
                else _(
                    "Die Entscheidung „{node_name}“ hat keinen else-Zweig; deckt keine Bedingung einen Fall ab, bleibt der Automat stehen.",
                    node_name=node.name,
                )
            ),
            [node.id],
            [e.id for e in (unguarded or edges)],
            suggestion=_("Gib jedem Übergang aus {node_id} eine Bedingung und einen davon „else“.", node_id=node.id),
        )


def _chain(diagram: Diagram, node: Node) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    while node.parent is not None and node.parent in diagram.nodes and node.parent not in seen:
        seen.add(node.parent)
        out.append(node.parent)
        node = diagram.nodes[node.parent]
    out.reverse()
    return out


def crosses_regions(diagram: Diagram) -> Iterator[Finding]:
    for edge in diagram.edges:
        src, tgt = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
        if src is None or tgt is None or src.kind == FORK or tgt.kind == JOIN:
            continue
        a, b = _chain(diagram, src), _chain(diagram, tgt)
        i = 0
        while i < len(a) and i < len(b) and a[i] == b[i]:
            i += 1
        if i < len(a) and i < len(b) and diagram.nodes[a[i]].kind == REGION and diagram.nodes[b[i]].kind == REGION:
            yield finding(
                "state-crosses-regions",
                "risk",
                "low",
                _(
                    "Der Übergang von „{src_name}“ nach „{tgt_name}“ verbindet zwei parallele Regionen.",
                    src_name=src.name,
                    tgt_name=tgt.name,
                ),
                [src.id, tgt.id],
                [edge.id],
                suggestion=_(
                    "Parallele Regionen laufen unabhängig; verbinde sie über einen fork_pseudostate oder join_pseudostate, oder lege beide Zustände in dieselbe Region."
                ),
            )


def final_unreachable(diagram: Diagram) -> Iterator[Finding]:
    targets = {e.target for e in diagram.edges}
    for node in diagram.nodes.values():
        if node.kind == "final_state" and node.id not in targets:
            yield finding(
                "state-final-unreachable",
                "suggestion",
                "low",
                _("Der Endzustand „{node_name}“ hat keinen eingehenden Übergang.", node_name=node.name),
                [node.id],
                suggestion=_("Führe einen Übergang nach {node_id}, oder entferne den Endzustand.", node_id=node.id),
            )


for _rule in (
    Rule("state-no-initial", N_("Bereich ohne Anfangszustand"), "suggestion", no_initial),
    Rule("state-multiple-initial", N_("Bereich mit mehreren Anfangszuständen"), "risk", multiple_initial),
    Rule("state-initial-targets", N_("Anfangszustand ohne genau einen Übergang"), "risk", initial_targets),
    Rule("state-unreachable", N_("Zustand vom Anfang aus nicht erreichbar"), "risk", unreachable),
    Rule("state-dead-end", N_("Zustand ohne ausgehenden Übergang"), "suggestion", dead_end),
    Rule("state-nondeterministic", N_("Übergänge auf dasselbe Ereignis ohne Bedingung"), "risk", nondeterministic),
    Rule("state-choice-without-else", N_("Entscheidung ohne else-Zweig"), "risk", choice_without_else),
    Rule("state-crosses-regions", N_("Übergang zwischen parallelen Regionen"), "risk", crosses_regions),
    Rule("state-final-unreachable", N_("Endzustand ohne eingehenden Übergang"), "suggestion", final_unreachable),
):
    register(TYPE, _rule)
