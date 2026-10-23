"""Deterministic architecture checks, registered per diagram type, and the finding schema they share
with the LLM review.

Spec: docs/architecture/specs/rule-checks.md (ADR-0022, ADR-0023). No LLM, no I/O: a rule is a pure
function of the diagram. Finding messages are German source texts translated per request with `arcviz.i18n` (ADR-0199).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from .i18n import N_, _
from .mermaid import edge_direction
from .model import (
    ACTIVITY_FINALS,
    ACTIVITY_INITIAL,
    REDUNDANCY_PROPERTY,
    REDUNDANCY_VALUES,
    Diagram,
    Edge,
    Node,
    activity_outgoing,
    edge_guard,
    flow_distances,
    is_else_guard,
    kind_trait,
    reverse_flows,
    role_of,
)
from .sequence import (
    CombinedFragment,
    DestructionOccurrenceSpecification,
    FragmentNode,
    Interaction,
    InteractionUse,
    Message,
    messages,
    walk,
)

Category = Literal["strength", "risk", "suggestion"]
Severity = Literal["info", "low", "medium", "high"]

CATEGORIES: tuple[Category, ...] = ("risk", "suggestion", "strength")  # also the sort order
SEVERITY_ORDER: tuple[Severity, ...] = ("high", "medium", "low", "info")
DEFAULT_DIAGRAM_TYPE = "deployment"


class Finding(BaseModel):
    """One result of a rule check or of the LLM review."""

    id: str
    source: Literal["rule", "llm"]
    rule: str | None = None
    category: Category
    severity: Severity
    message: str
    elements: list[str] = Field(default_factory=list)  # node ids
    edges: list[str] = Field(default_factory=list)  # edge ids
    suggestion: str | None = None  # how to improve it, phrased as an instruction


@dataclass(frozen=True)
class Rule:
    id: str
    source_title: str  # German one-liner for the rule list, marked with `N_`
    category: Category
    check: Callable[[Diagram], Iterable[Finding]]

    @property
    def title(self) -> str:
        """The title in the language of the current request."""
        return _(self.source_title)


RULES: dict[str, list[Rule]] = {}


def register(diagram_type: str, rule: Rule) -> Rule:
    rules = RULES.setdefault(diagram_type, [])
    if any(r.id == rule.id for r in rules):
        raise ValueError(f"Rule '{rule.id}' is already registered for diagram type '{diagram_type}'.")
    rules.append(rule)
    return rule


def diagram_type(diagram: Diagram) -> str:
    """The diagram's type (`Diagram.type`, milestone M13; stored diagrams without one are `deployment`)."""
    return diagram.type or DEFAULT_DIAGRAM_TYPE


def rules_for(diagram_type: str) -> list[Rule]:
    return list(RULES.get(diagram_type, []))


def sort_key(finding: Finding) -> tuple[int, int, str, list[str], list[str]]:
    return (
        SEVERITY_ORDER.index(finding.severity),
        CATEGORIES.index(finding.category),
        finding.rule or "",
        finding.elements,
        finding.edges,
    )


# Analyses several rules share (the flows of an activity, the matched replies of an interaction), computed once per
# `run_checks`; a rule called on its own computes them itself.
_RUN: ContextVar[dict[tuple[str, int], Any] | None] = ContextVar("arcviz_rules_run", default=None)


def _memo(key: tuple[str, int], compute: Callable[[], Any]) -> Any:
    cache = _RUN.get()
    if cache is None:
        return compute()
    if key not in cache:
        cache[key] = compute()
    return cache[key]


def run_checks(diagram: Diagram) -> list[Finding]:
    """Findings of every rule registered for the diagram's type, most severe first."""
    token = _RUN.set({})
    try:
        findings = [f for rule in rules_for(diagram_type(diagram)) for f in rule.check(diagram)]
    finally:
        _RUN.reset(token)
    return sorted(findings, key=sort_key)


# ---- helpers ---------------------------------------------------------------------------------

COMPUTE_KINDS = frozenset({"service", "function", "component"})
DATA_STORE_KINDS = frozenset({"database", "storage", "queue"})
REPLICATION_PROPERTIES = ("replicas", "replication", "ha", "high_availability", "zone_redundant", "geo_redundant")
FALSY = frozenset({"", "0", "1", "false", "no", "none"})


def _finding(
    rule: str,
    category: Category,
    severity: Severity,
    message: str,
    elements: list[str],
    edges: list[str] | None = None,
    suggestion: str | None = None,
) -> Finding:
    edges = edges or []
    finding_id = f"{rule}:{'+'.join(elements)}" + (f":{'+'.join(edges)}" if edges else "")
    return Finding(
        id=finding_id,
        source="rule",
        rule=rule,
        category=category,
        severity=severity,
        message=message,
        elements=elements,
        edges=edges,
        suggestion=suggestion,
    )


finding = _finding  # for the rule modules of the other diagram types (state_rules.py, class_rules.py)


def _traffic(diagram: Diagram) -> Iterator[tuple[Edge, Node, Node]]:
    """(edge, from, to) for every direction traffic flows along an edge; `deploys` edges carry none."""
    for edge in diagram.edges:
        source, target = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
        if source is None or target is None or edge.kind == "deploys":
            continue
        yield edge, source, target
        if edge_direction(edge) in ("both", "none"):
            yield edge, target, source


def _replication_edges(diagram: Diagram, node_id: str) -> list[str]:
    return [e.id for e in diagram.edges if e.kind == "replication" and node_id in (e.source, e.target)]


def _replicated_by_property(node: Node) -> bool:
    return any(node.properties.get(key, "").strip().lower() not in FALSY for key in REPLICATION_PROPERTIES)


def _built_in_redundancy(node: Node) -> bool:
    """The platform replicates the store: the node says so (`redundancy=built_in`), or, unless it says `none`, its kind."""
    declared = node.properties.get(REDUNDANCY_PROPERTY, "").strip().lower()
    if declared in REDUNDANCY_VALUES:
        return declared == "built_in"
    return kind_trait(node, REDUNDANCY_PROPERTY) == "built_in"


def _databases(diagram: Diagram) -> list[Node]:
    return sorted((n for n in diagram.nodes.values() if role_of(n) == "database"), key=lambda n: n.id)


# ---- deployment rules ------------------------------------------------------------------------


def database_without_replication(diagram: Diagram) -> Iterator[Finding]:
    for db in _databases(diagram):
        if _replication_edges(diagram, db.id) or _replicated_by_property(db) or _built_in_redundancy(db):
            continue
        yield _finding(
            "database-without-replication",
            "risk",
            "high",
            _("Die Datenbank „{db_name}“ hat keine Replikation und ist ein Single Point of Failure.", db_name=db.name),
            [db.id],
            suggestion=_(
                "Füge für die Datenbank „{db_name}“ (id {db_id}) eine Replik hinzu und verbinde beide mit einer Kante der Art replication.",
                db_name=db.name,
                db_id=db.id,
            ),
        )


def replicated_database(diagram: Diagram) -> Iterator[Finding]:
    for db in _databases(diagram):
        edges = _replication_edges(diagram, db.id)
        if not edges and not _replicated_by_property(db):
            if _built_in_redundancy(db):
                yield _finding(
                    "replicated-database",
                    "strength",
                    "info",
                    _("Die Datenbank „{db_name}“ wird von der Plattform repliziert (managed).", db_name=db.name),
                    [db.id],
                )
            continue
        yield _finding(
            "replicated-database",
            "strength",
            "info",
            _("Die Datenbank „{db_name}“ ist repliziert.", db_name=db.name),
            [db.id],
            edges,
        )


def public_entry_without_gateway(diagram: Diagram) -> Iterator[Finding]:
    for edge, origin, target in _traffic(diagram):
        if role_of(origin) == "external" and role_of(target) in COMPUTE_KINDS:
            yield _finding(
                "public-entry-without-gateway",
                "risk",
                "medium",
                _(
                    "„{origin_name}“ greift direkt auf „{target_name}“ zu, ohne Gateway dazwischen.",
                    origin_name=origin.name,
                    target_name=target.name,
                ),
                [origin.id, target.id],
                [edge.id],
                suggestion=_(
                    "Leite den Zugriff von „{origin_name}“ (id {origin_id}) auf „{target_name}“ (id {target_id}) über ein Gateway: ersetze die Kante {edge_id} durch eine Kante zu einem Gateway und eine Kante vom Gateway zum Ziel.",
                    origin_name=origin.name,
                    origin_id=origin.id,
                    target_name=target.name,
                    target_id=target.id,
                    edge_id=edge.id,
                ),
            )


def external_to_datastore(diagram: Diagram) -> Iterator[Finding]:
    for edge in diagram.edges:
        source, target = diagram.nodes.get(edge.source), diagram.nodes.get(edge.target)
        if source is None or target is None or edge.kind == "deploys":
            continue
        for external, store in ((source, target), (target, source)):
            if role_of(external) == "external" and role_of(store) in DATA_STORE_KINDS:
                yield _finding(
                    "external-to-datastore",
                    "risk",
                    "high",
                    _(
                        "Das externe System „{external_name}“ ist direkt mit dem Datenspeicher „{store_name}“ verbunden.",
                        external_name=external.name,
                        store_name=store.name,
                    ),
                    [external.id, store.id],
                    [edge.id],
                    suggestion=_(
                        "Entkopple „{external_name}“ (id {external_id}) von „{store_name}“ (id {store_id}): entferne die Kante {edge_id} und lass einen Service den Zugriff übernehmen.",
                        external_name=external.name,
                        external_id=external.id,
                        store_name=store.name,
                        store_id=store.id,
                        edge_id=edge.id,
                    ),
                )
                break


def unconnected_element(diagram: Diagram) -> Iterator[Finding]:
    connected = {e.source for e in diagram.edges} | {e.target for e in diagram.edges}
    for node in sorted(diagram.nodes.values(), key=lambda n: n.id):
        if node.is_container or diagram.children(node.id) or node.id in connected:
            continue
        yield _finding(
            "unconnected-element",
            "suggestion",
            "low",
            _("„{node_name}“ ist mit keinem anderen Element verbunden.", node_name=node.name),
            [node.id],
            suggestion=_(
                "Verbinde „{node_name}“ (id {node_id}) mit den Elementen, mit denen es kommuniziert, oder entferne es, falls es nicht mehr gebraucht wird.",
                node_name=node.name,
                node_id=node.id,
            ),
        )


def gateway_entry(diagram: Diagram) -> Iterator[Finding]:
    for edge, origin, target in _traffic(diagram):
        if role_of(origin) == "external" and role_of(target) == "gateway":
            yield _finding(
                "gateway-entry",
                "strength",
                "info",
                _(
                    "Zugriffe von „{origin_name}“ laufen über das Gateway „{target_name}“.",
                    origin_name=origin.name,
                    target_name=target.name,
                ),
                [origin.id, target.id],
                [edge.id],
            )


for _rule in (
    Rule(
        "database-without-replication",
        N_("Datenbank ohne Replikation (Single Point of Failure)"),
        "risk",
        database_without_replication,
    ),
    Rule(
        "public-entry-without-gateway",
        N_("Externer Zugriff auf Services ohne Gateway"),
        "risk",
        public_entry_without_gateway,
    ),
    Rule(
        "external-to-datastore",
        N_("Externes System direkt an einem Datenspeicher"),
        "risk",
        external_to_datastore,
    ),
    Rule("unconnected-element", N_("Element ohne Verbindungen"), "suggestion", unconnected_element),
    Rule("replicated-database", N_("Replizierte Datenbank"), "strength", replicated_database),
    Rule("gateway-entry", N_("Externer Zugriff über ein Gateway"), "strength", gateway_entry),
):
    register(DEFAULT_DIAGRAM_TYPE, _rule)


# ---- activity rules (#123) -------------------------------------------------------------------
# UML 2.5.1 allows much that these rules mention (an Activity without InitialNode, a fork whose flows end in
# FlowFinalNodes, ...), so only an undetermined decision is a risk; everything else is a low suggestion. Every rule
# is linear in nodes and flows: reachability towards joins and final nodes is one reverse search.


def _activity_nodes(diagram: Diagram) -> list[Node]:
    """The ActivityNodes (ActivityPartitions group nodes but are none), sorted by id."""
    return sorted((n for n in diagram.nodes.values() if n.kind != "activity_partition"), key=lambda n: n.id)


def _outgoing(diagram: Diagram) -> dict[str, list[Edge]]:
    return _memo(("outgoing", id(diagram)), lambda: activity_outgoing(diagram))


def _initial_nodes(diagram: Diagram) -> list[str]:
    return [n.id for n in _activity_nodes(diagram) if n.kind == ACTIVITY_INITIAL]


def missing_initial_node(diagram: Diagram) -> Iterator[Finding]:
    nodes = _activity_nodes(diagram)
    if not nodes or _initial_nodes(diagram):
        return
    targets = {e.target for e in diagram.edges}
    entries = [n.id for n in nodes if n.id not in targets]
    yield _finding(
        "missing-initial-node",
        "suggestion",
        "low",
        _("Die Aktivität hat keinen Startknoten; es ist nicht eindeutig, wo der Ablauf beginnt."),
        entries or [n.id for n in nodes],  # where the flows begin; in a closed loop every node
        suggestion=(
            _(
                "Füge einen Startknoten (initial_node) hinzu und verbinde ihn mit der ersten Aktion (Kandidaten ohne eingehenden Fluss: {entries}).",
                entries=", ".join(entries),
            )
            if entries
            else _("Füge einen Startknoten (initial_node) hinzu und verbinde ihn mit der ersten Aktion.")
        ),
    )


def unreachable_node(diagram: Diagram) -> Iterator[Finding]:
    starts = _initial_nodes(diagram)
    if not starts:
        return  # missing-initial-node says it
    reached = flow_distances(_outgoing(diagram), starts)
    for node in _activity_nodes(diagram):
        if node.id in reached:
            continue
        yield _finding(
            "unreachable-node",
            "suggestion",
            "low",
            _("„{node_name}“ ist von keinem Startknoten aus erreichbar und wird nie ausgeführt.", node_name=node.name),
            [node.id],
            suggestion=_(
                "Verbinde „{node_name}“ (id {node_id}) mit dem Ablauf, der vom Startknoten ausgeht, oder entferne es, falls es nicht mehr gebraucht wird.",
                node_name=node.name,
                node_id=node.id,
            ),
        )


def _decisions(diagram: Diagram) -> Iterator[tuple[Node, list[Edge]]]:
    """Every DecisionNode with at least two outgoing flows (one way out decides nothing)."""
    outgoing = _outgoing(diagram)
    for node in _activity_nodes(diagram):
        if node.kind == "decision_node" and len(outgoing.get(node.id, [])) >= 2:
            yield node, outgoing[node.id]


def decision_without_guards(diagram: Diagram) -> Iterator[Finding]:
    for node, outgoing in _decisions(diagram):
        unguarded = [e.id for e in outgoing if not edge_guard(e)]
        if not unguarded:
            continue
        yield _finding(
            "decision-without-guards",
            "risk",
            "medium",
            _(
                "Die Verzweigung „{node_name}“ hat ausgehende Flüsse ohne Bedingung; welcher Weg genommen wird, ist nicht bestimmt.",
                node_name=node.name,
            ),
            [node.id],
            unguarded,
            suggestion=_(
                "Gib jedem ausgehenden Fluss der Verzweigung „{node_name}“ (id {node_id}) eine Bedingung (guard), zum Beispiel „else“ für den übrigen Fall; betroffen: {unguarded_ids}.",
                node_name=node.name,
                node_id=node.id,
                unguarded_ids=", ".join(unguarded),
            ),
        )


def decision_without_else(diagram: Diagram) -> Iterator[Finding]:
    for node, outgoing in _decisions(diagram):
        guards = [edge_guard(e) for e in outgoing]
        if not all(guards) or any(is_else_guard(g) for g in guards):
            continue  # decision-without-guards covers unguarded flows
        yield _finding(
            "decision-without-else",
            "suggestion",
            "low",
            _(
                "Die Verzweigung „{node_name}“ hat keinen else-Zweig; deckt keine Bedingung einen Fall ab, bleibt der Ablauf stehen.",
                node_name=node.name,
            ),
            [node.id],
            [e.id for e in outgoing],
            suggestion=_(
                "Prüfe, ob die Bedingungen der Verzweigung „{node_name}“ (id {node_id}) alle Fälle abdecken; sonst ersetze die Bedingung des übrigen Falls durch „else“.",
                node_name=node.name,
                node_id=node.id,
            ),
        )


def _reaching(diagram: Diagram, kinds: Iterable[str]) -> dict[str, int]:
    """Every node from which a node of one of `kinds` is reachable (one reverse search from all of them)."""
    targets = [n.id for n in _activity_nodes(diagram) if n.kind in kinds]
    incoming = _memo(("incoming", id(diagram)), lambda: reverse_flows(_outgoing(diagram)))
    return flow_distances(incoming, targets)


def fork_without_join(diagram: Diagram) -> Iterator[Finding]:
    joined = _reaching(diagram, {"join_node"})
    for node in _activity_nodes(diagram):
        if node.kind != "fork_node" or node.id in joined:
            continue
        yield _finding(
            "fork-without-join",
            "suggestion",
            "low",
            _("Die Parallelisierung „{node_name}“ wird nirgends wieder zusammengeführt.", node_name=node.name),
            [node.id],
            suggestion=_(
                "Führe die parallelen Abläufe nach „{node_name}“ (id {node_id}) mit einem join_node zusammen, falls sie gemeinsam weitergehen sollen.",
                node_name=node.name,
                node_id=node.id,
            ),
        )


def _components(nodes: list[str], outgoing: dict[str, list[Edge]]) -> list[list[str]]:
    """Strongly connected components (iterative Tarjan), each one after every component it reaches."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[list[str]] = []
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index[node] = low[node] = len(index)
                stack.append(node)
                on_stack.add(node)
            edges = outgoing.get(node, [])
            if i < len(edges):
                work.append((node, i + 1))
                nxt = edges[i].target
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                out.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return out


def flow_without_final_node(diagram: Diagram) -> Iterator[Finding]:
    """One finding per place the flow gets stuck: the actions of a strongly connected component from which no final
    node is reachable and from which no other such action outside the component is reachable (a dead end, or a
    loop without exit)."""
    outgoing = _outgoing(diagram)
    finishing = _reaching(diagram, ACTIVITY_FINALS)
    nodes = _activity_nodes(diagram)
    stuck = {n.id for n in nodes if n.kind == "opaque_action" and n.id not in finishing}
    if not stuck:
        return
    component_of: dict[str, int] = {}
    leads_to_stuck: list[bool] = []  # the component, or one it reaches, holds a stuck action
    for number, component in enumerate(_components([n.id for n in nodes], outgoing)):  # sinks first
        component_of.update(dict.fromkeys(component, number))
        later = {component_of[e.target] for m in component for e in outgoing.get(m, [])} - {number}
        downstream = any(leads_to_stuck[c] for c in later)
        group = sorted(m for m in component if m in stuck)
        leads_to_stuck.append(bool(group) or downstream)
        if not group or downstream:
            continue  # nothing stuck here, or the flow goes on to another stuck action: that one is reported
        names = ", ".join(_("„{name}“", name=diagram.nodes[i].name) for i in group)
        yield _finding(
            "flow-without-final-node",
            "suggestion",
            "low",
            _("Nach {names} erreicht der Ablauf keinen Endknoten.", names=names),
            group,
            suggestion=_(
                "Führe den Ablauf nach {group_ids} zu einem Endknoten (activity_final_node oder flow_final_node).",
                group_ids=", ".join(group),
            ),
        )


for _rule in (
    Rule("decision-without-guards", N_("Verzweigung mit Flüssen ohne Bedingung"), "risk", decision_without_guards),
    Rule("missing-initial-node", N_("Aktivität ohne Startknoten"), "suggestion", missing_initial_node),
    Rule("unreachable-node", N_("Knoten vom Start aus nicht erreichbar"), "suggestion", unreachable_node),
    Rule("decision-without-else", N_("Verzweigung ohne else-Zweig"), "suggestion", decision_without_else),
    Rule("fork-without-join", N_("Parallelisierung ohne Zusammenführung"), "suggestion", fork_without_join),
    Rule("flow-without-final-node", N_("Ablauf ohne Endknoten"), "suggestion", flow_without_final_node),
):
    register("activity", _rule)


# ---- sequence rules (#123) -------------------------------------------------------------------
# Replies are optional in UML 2.5.1 (17.12.23), so a missing reply is a low suggestion; a reply nobody asked for is
# a risk. Messages after a lifeline's destruction are already refused by `sequence.integrity_errors`.

CALL_SORTS = frozenset({"synchCall", "asynchCall"})


@dataclass
class _Replies:
    """Calls matched with their replies in time order (`sequence.walk`), per operand of an alt."""

    answers: dict[str, list[str]]  # call id -> its replies (several: answered differently in the operands of an alt)
    orphans: list[str]  # replies without a preceding call between the same lifelines
    unanswered: list[Message]  # calls that no operand answers


def _match_replies(diagram: Diagram) -> _Replies:
    """Computed once per `run_checks` for the three rules that read it (keyed by the diagram, which outlives the run;
    the interaction may be a temporary)."""
    return _memo(("replies", id(diagram)), lambda: _compute_replies(_interaction_of(diagram)))


def _compute_replies(interaction: Interaction) -> _Replies:
    result = _Replies({}, [], [])

    def run(fragments: list[FragmentNode], pending: list[Message]) -> list[Message]:
        for fragment in fragments:
            if isinstance(fragment, Message):
                if fragment.kind != "complete" or fragment.sender == fragment.receiver:
                    continue  # found/lost messages and self messages are never matched
                if fragment.sort == "reply":
                    call = next(
                        (
                            p
                            for p in reversed(pending)
                            if (p.sender, p.receiver) == (fragment.receiver, fragment.sender)
                        ),
                        None,
                    )
                    if call is None:
                        result.orphans.append(fragment.id)
                    else:
                        pending.remove(call)
                        result.answers.setdefault(call.id, []).append(fragment.id)
                elif fragment.sort in CALL_SORTS:
                    pending.append(fragment)
            elif isinstance(fragment, CombinedFragment):
                if fragment.operator == "alt" and len(fragment.operands) > 1:
                    # alternatives start from the same state; a call answered in any of them counts as answered
                    outs = [run(op.fragments, list(pending)) for op in fragment.operands]
                    still = set.intersection(*({q.id for q in out} for out in outs))
                    before = {p.id for p in pending}
                    added = {q.id: q for out in outs for q in out if q.id not in before}  # first seen order
                    pending = [p for p in pending if p.id in still] + list(added.values())
                else:
                    for op in fragment.operands:
                        pending = run(op.fragments, pending)
        return pending

    result.unanswered = run(interaction.fragments, [])
    return result


def _lifeline_names(interaction: Interaction) -> dict[str, str]:
    return {lifeline.id: lifeline.name for lifeline in interaction.lifelines}


def _interaction_of(diagram: Diagram) -> Interaction:
    return diagram.interaction if diagram.interaction is not None else Interaction()


def call_without_reply(diagram: Diagram) -> Iterator[Finding]:
    interaction = _interaction_of(diagram)
    names = _lifeline_names(interaction)
    for call in _match_replies(diagram).unanswered:
        if call.sort != "synchCall" or call.sender is None or call.receiver is None:
            continue
        yield _finding(
            "call-without-reply",
            "suggestion",
            "low",
            _(
                "Der synchrone Aufruf „{call_name}“ von „{sender_name}“ an „{receiver_name}“ bekommt keine Antwort.",
                call_name=call.name,
                sender_name=names[call.sender],
                receiver_name=names[call.receiver],
            ),
            [call.id],
            suggestion=_(
                "Füge nach dem Aufruf {call_id} eine Antwortnachricht (sort reply) von {call_receiver} an {call_sender} hinzu, oder mache den Aufruf asynchron, falls keine Antwort erwartet wird.",
                call_id=call.id,
                call_receiver=call.receiver,
                call_sender=call.sender,
            ),
        )


def reply_without_call(diagram: Diagram) -> Iterator[Finding]:
    interaction = _interaction_of(diagram)
    names = _lifeline_names(interaction)
    by_id = {m.id: m for m in messages(interaction)}
    for reply_id in _match_replies(diagram).orphans:
        reply = by_id[reply_id]
        sender, receiver = reply.sender or "", reply.receiver or ""
        yield _finding(
            "reply-without-call",
            "risk",
            "medium",
            _(
                "Die Antwort „{reply_name}“ von „{sender_name}“ an „{receiver_name}“ beantwortet keinen vorangehenden Aufruf.",
                reply_name=reply.name,
                sender_name=names[sender],
                receiver_name=names[receiver],
            ),
            [reply.id],
            suggestion=_(
                "Füge vor der Antwort {reply_id} den Aufruf von {receiver} an {sender} hinzu, kehre die Richtung der Antwort um oder ändere ihre Art (sort), falls sie keine Antwort ist.",
                reply_id=reply.id,
                receiver=receiver,
                sender=sender,
            ),
        )


def fragment_after_destruction(diagram: Diagram) -> Iterator[Finding]:
    interaction = _interaction_of(diagram)
    names = _lifeline_names(interaction)
    destroyed: set[str] = set()
    for fragment, _owner, _position in walk(interaction.fragments):
        if isinstance(fragment, Message) and fragment.sort == "deleteMessage" and fragment.receiver:
            destroyed.add(fragment.receiver)
        elif isinstance(fragment, DestructionOccurrenceSpecification):
            destroyed.add(fragment.lifeline)
        elif isinstance(fragment, CombinedFragment | InteractionUse):
            for lifeline in fragment.covered:
                if lifeline not in destroyed:
                    continue
                what = _("Die Interaktionsreferenz") if isinstance(fragment, InteractionUse) else _("Das Fragment")
                yield _finding(
                    "fragment-after-destruction",
                    "suggestion",
                    "low",
                    _(
                        "{what} {fragment_id} überdeckt „{lifeline_name}“, obwohl die Lebenslinie vorher zerstört wurde.",
                        what=what,
                        fragment_id=fragment.id,
                        lifeline_name=names[lifeline],
                    ),
                    [fragment.id, lifeline],
                    suggestion=_(
                        "Entferne {lifeline} aus den überdeckten Lebenslinien von {fragment_id}.",
                        lifeline=lifeline,
                        fragment_id=fragment.id,
                    ),
                )


def alt_with_single_operand(diagram: Diagram) -> Iterator[Finding]:
    for fragment, _owner, _position in walk(_interaction_of(diagram).fragments):
        if isinstance(fragment, CombinedFragment) and fragment.operator == "alt" and len(fragment.operands) == 1:
            yield _finding(
                "alt-with-single-operand",
                "suggestion",
                "low",
                _("Das alt-Fragment {fragment_id} hat nur eine Alternative.", fragment_id=fragment.id),
                [fragment.id],
                suggestion=_(
                    "Ändere den Operator von {fragment_id} in opt oder füge eine weitere Alternative (zum Beispiel mit Bedingung else) hinzu.",
                    fragment_id=fragment.id,
                ),
            )


def unfinished_execution(diagram: Diagram) -> Iterator[Finding]:
    interaction = _interaction_of(diagram)
    names = _lifeline_names(interaction)
    answers = _match_replies(diagram).answers
    calls = {m.id: m for m in messages(interaction)}
    for execution in interaction.executions:
        replies = answers.get(execution.start, [])
        if execution.finish is not None or len(replies) != 1:
            continue  # unanswered, or answered differently per alternative: "until the last message" fits
        if calls[execution.start].receiver != execution.lifeline:
            continue  # the caller's execution: the reply it receives does not end its own activity
        reply = replies[0]
        yield _finding(
            "unfinished-execution",
            "suggestion",
            "low",
            _(
                "Die Ausführung {execution_id} auf „{lifeline_name}“ endet erst mit der letzten Nachricht der Lebenslinie, obwohl {reply} den startenden Aufruf beantwortet.",
                execution_id=execution.id,
                lifeline_name=names.get(execution.lifeline, execution.lifeline),
                reply=reply,
            ),
            [execution.id, reply],
            suggestion=_(
                "Lass die Ausführung {execution_id} mit der Antwort {reply} enden (finish).",
                execution_id=execution.id,
                reply=reply,
            ),
        )


for _rule in (
    Rule("reply-without-call", N_("Antwort ohne Aufruf"), "risk", reply_without_call),
    Rule("call-without-reply", N_("Synchroner Aufruf ohne Antwort"), "suggestion", call_without_reply),
    Rule(
        "alt-with-single-operand", N_("alt-Fragment mit nur einer Alternative"), "suggestion", alt_with_single_operand
    ),
    Rule(
        "fragment-after-destruction",
        N_("Fragment überdeckt eine zerstörte Lebenslinie"),
        "suggestion",
        fragment_after_destruction,
    ),
    Rule("unfinished-execution", N_("Ausführung ohne Ende trotz Antwort"), "suggestion", unfinished_execution),
):
    register("sequence", _rule)


components = _components  # strongly connected components, for the same modules

# The rules of the other graph types live in their own modules, which register themselves (imported last: they need
# `Rule`, `register` and `finding` from here).
from . import class_rules, component_rules, state_rules, use_case_rules  # noqa: E402,F401
