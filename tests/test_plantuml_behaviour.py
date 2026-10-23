"""PlantUML export of activity and sequence diagrams (spec: docs/architecture/specs/diagram-exchange.md, #107).

One test per construct. With `ARCVIZ_PLANTUML_JAR` pointing at a PlantUML jar (and `java` on the path), the
exports are also rendered by PlantUML itself and must not report a syntax error.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from arcviz import exchange, operations
from arcviz import sequence as interaction_model
from arcviz.model import Diagram, Edge, Node, activity_edge_problem, activity_rule_errors, kinds_for
from arcviz.server import create_app
from arcviz.store import FileStore
from arcviz.templates import TEMPLATES

EXPORTED: list[Diagram] = []  # every diagram a construct test exported, for the PlantUML render test


def body(diagram: Diagram) -> list[str]:
    """The export without `@startuml`, the title and `@enduml`."""
    EXPORTED.append(diagram)
    lines = exchange.export_plantuml(diagram).splitlines()
    assert lines[0] == "@startuml" and lines[1].startswith("title ") and lines[-1] == "@enduml"
    return lines[2:-1]


# ---- activity diagrams -----------------------------------------------------------------------


def activity(*nodes: tuple, flows: tuple = ()) -> Diagram:
    """Nodes `(id, kind[, partition])`, flows `(source, target[, guard[, label[, kind]]])`; names = ids."""
    d = Diagram(id="a", title="A", type="activity")
    for node_id, kind, *parent in nodes:
        args = {"name": node_id, "kind": kind, "id": node_id}
        if parent:
            args["parent"] = parent[0]
        operations.apply(d, "add_node", args)
    for source, target, *more in flows:
        args = {"source": source, "target": target, "kind": "control_flow"}
        for key, value in zip(("guard", "label", "kind"), more, strict=False):
            if value:
                args[key] = value
        operations.apply(d, "add_edge", args)
    return d


START, END = ("start", "initial_node"), ("end", "activity_final_node")


def test_activity_sequence_of_actions():
    d = activity(
        START, ("a", "opaque_action"), ("b", "opaque_action"), END, flows=(("start", "a"), ("a", "b"), ("b", "end"))
    )
    assert body(d) == ["start", ":a;", ":b;", "stop"]


def test_activity_flow_final_and_dead_end():
    d = activity(START, ("a", "opaque_action"), ("ff", "flow_final_node"), ("b", "opaque_action"))
    operations.apply(d, "add_edge", {"source": "start", "target": "a", "kind": "control_flow"})
    operations.apply(d, "add_edge", {"source": "a", "target": "ff", "kind": "control_flow"})
    assert body(d)[:3] == ["start", ":a;", "end"]
    assert body(d)[3:] == [":b;", "detach"]  # a flow that ends without a final node, drawn on its own


def test_activity_decision_with_two_guards_puts_else_last():
    d = activity(
        START,
        ("ok?", "decision_node"),
        ("a", "opaque_action"),
        ("b", "opaque_action"),
        ("m", "merge_node"),
        END,
        flows=(("start", "ok?"), ("ok?", "b", "else"), ("ok?", "a", "ja"), ("a", "m"), ("b", "m"), ("m", "end")),
    )
    assert body(d) == ["start", "if (ok?) then (ja)", "  :a;", "else (else)", "  :b;", "endif", "stop"]


def test_activity_decision_with_more_branches_is_a_switch():
    d = activity(
        START,
        ("art", "decision_node"),
        *((x, "opaque_action") for x in "abc"),
        END,
        flows=(
            ("start", "art"),
            ("art", "a", "x"),
            ("art", "b", "y"),
            ("art", "c", "z"),
            ("a", "end"),
            ("b", "end"),
            ("c", "end"),
        ),
    )
    assert body(d) == [
        "start",
        "switch (art)",
        "case (x)",
        "  :a;",
        "case (y)",
        "  :b;",
        "case (z)",
        "  :c;",
        "endswitch",
        "stop",
    ]


def test_activity_fork_and_join():
    d = activity(
        START,
        ("f", "fork_node"),
        ("a", "opaque_action"),
        ("b", "opaque_action"),
        ("j", "join_node"),
        ("c", "opaque_action"),
        END,
        flows=(("start", "f"), ("f", "a"), ("f", "b"), ("a", "j"), ("b", "j"), ("j", "c"), ("c", "end")),
    )
    assert body(d) == ["start", "fork", "  :a;", "fork again", "  :b;", "end fork", ":c;", "stop"]


def test_activity_object_nodes_and_an_implicit_fork_with_labels():
    d = activity(
        START,
        ("a", "opaque_action"),
        ("store", "data_store_node"),
        ("buffer", "central_buffer_node"),
        END,
        flows=(
            ("start", "a"),
            ("a", "end"),
            ("a", "store", None, "Daten", "object_flow"),
            ("store", "buffer", None, None, "object_flow"),
        ),
    )
    assert body(d) == [
        "start",
        ":a;",
        "fork",
        "  stop",
        "fork again",
        "  -> Daten;",
        "  :«datastore»\\nstore; <<object>>",
        "  :«centralBuffer»\\nbuffer; <<object>>",
        "  detach",
        "end fork",
    ]


def test_activity_partitions_are_swimlanes():
    d = activity(
        ("Kunde", "activity_partition"),
        ("Shop", "activity_partition"),
        ("start", "initial_node", "kunde"),
        ("a", "opaque_action", "kunde"),
        ("b", "opaque_action", "shop"),
        ("c", "opaque_action"),
        flows=(("start", "a"), ("a", "b"), ("b", "c")),
    )
    assert body(d) == ["|Kunde|", "start", ":a;", "|Shop|", ":b;", "| |", ":c;", "detach"]


def test_activity_first_swimlane_comes_before_any_structure():
    d = activity(
        ("Shop", "activity_partition"),
        ("d", "decision_node"),
        ("a", "opaque_action", "shop"),
        ("b", "opaque_action", "shop"),
        flows=(("d", "a", "x"), ("d", "b", "else")),
    )
    assert body(d) == [
        "|Shop|",
        "if (d) then (x)",
        "  |Shop|",
        "  :a;",
        "  detach",
        "else (else)",
        "  :b;",
        "  detach",
        "endif",
    ]


def test_activity_loop_becomes_a_connector():
    d = activity(
        START,
        ("m", "merge_node"),
        ("a", "opaque_action"),
        ("again?", "decision_node"),
        END,
        flows=(("start", "m"), ("m", "a"), ("a", "again?"), ("again?", "m", "ja"), ("again?", "end", "nein")),
    )
    assert body(d) == [
        "start",
        "(1)",  # where the loop comes back: before the first action after the merge node
        ":a;",
        "if (again?) then (ja)",
        "  (1)",
        "  detach",
        "else (nein)",  # both branches reach the final node: it follows the endif
        "endif",
        "stop",
    ]


def test_activity_labels_survive_jumps_and_structure_ends():
    d = activity(
        START,
        ("a", "opaque_action"),
        ("b", "opaque_action"),
        ("f", "fork_node"),
        ("j", "join_node"),
        flows=(("start", "a"), ("a", "b"), ("b", "a", "retry"), ("start", "f"), ("f", "j", None, "direkt"), ("f", "j")),
    )
    lines = [line.strip() for line in body(d)]
    at = lines.index(":b;")
    assert lines[at + 1 : at + 4] == ["-> [retry];", "(1)", "detach"]  # the back edge keeps its guard
    assert "-> direkt;" in lines  # a labelled flow straight to the join keeps its label


def test_activity_guard_and_label_on_a_plain_flow():
    d = activity(START, ("a", "opaque_action"), flows=(("start", "a", "bereit", "los"),))
    assert body(d) == ["start", "-> [bereit] los;", ":a;", "detach"]


def test_activity_nodes_on_a_loop_nothing_leads_into_are_drawn_too():
    d = activity(("a", "opaque_action"), ("b", "opaque_action"), flows=(("a", "b"), ("b", "a")))
    assert body(d) == ["(1)", ":a;", ":b;", "(1)", "detach"]


def test_activity_text_is_shown_literally():
    d = Diagram(id="a", title="A", type="activity")
    operations.apply(d, "add_node", {"name": "P|1", "kind": "activity_partition", "id": "p"})
    operations.apply(d, "add_node", {"name": "a; <b>x</b> (1)", "kind": "opaque_action", "id": "a", "parent": "p"})
    operations.apply(d, "add_node", {"name": "ok (wirklich)?", "kind": "decision_node", "id": "d", "parent": "p"})
    operations.apply(d, "add_node", {"name": "b", "kind": "opaque_action", "id": "b", "parent": "p"})
    operations.apply(d, "add_edge", {"source": "a", "target": "d", "kind": "control_flow", "label": "x; y"})
    operations.apply(d, "add_edge", {"source": "d", "target": "b", "kind": "control_flow", "guard": "a)b"})
    operations.apply(d, "add_edge", {"source": "d", "target": "a", "kind": "control_flow", "guard": "else"})
    lines = body(d)
    assert lines[0] == "|P&#124;1|"
    assert lines[2] == ":a&#59; &#60;b>x&#60;/b> (1);"
    assert lines[3] == "-> x&#59; y;"
    assert lines[4] == "if (ok &#40;wirklich&#41;?) then (a&#41;b)"


def test_activity_jump_to_a_merge_that_ends_the_output():
    """An inner branch jumps to a merge without outgoing flow, which then draws nothing at the very end."""
    d = activity(
        START,
        ("d1", "decision_node"),
        ("a", "opaque_action"),
        ("d2", "decision_node"),
        ("b", "opaque_action"),
        ("e", "opaque_action"),
        ("m", "merge_node"),
        ("end", "activity_final_node"),
        flows=(("start", "d1"), ("d1", "a", "x"), ("d1", "b", "y"), ("a", "d2"), ("d2", "m", "p"), ("d2", "e", "q")),
    )
    operations.apply(d, "add_edge", {"source": "e", "target": "end", "kind": "control_flow"})
    operations.apply(d, "add_edge", {"source": "b", "target": "m", "kind": "control_flow"})
    lines = [line.strip() for line in body(d)]
    assert lines.count("(1)") == 2 and lines.count("detach") == 1  # b jumps to where the merge is drawn


KINDS = sorted(kinds_for("activity").leaf_kinds)


@st.composite
def activity_graphs(draw) -> Diagram:
    """Any valid activity diagram of a few nodes: random kinds, random flows that the activity rules allow."""
    d = Diagram(id="g", title="G", type="activity")
    if draw(st.booleans()):
        d.nodes["p"] = Node(id="p", name="P", kind="activity_partition")
    for i in range(draw(st.integers(1, 9))):
        parent = "p" if "p" in d.nodes and draw(st.booleans()) else None
        d.nodes[f"n{i}"] = Node(id=f"n{i}", name=f"n{i}", kind=draw(st.sampled_from(KINDS)), parent=parent)
    ids = [n for n in d.nodes if n != "p"]
    for k in range(draw(st.integers(0, 14))):
        source, target = draw(st.sampled_from(ids)), draw(st.sampled_from(ids))
        if activity_edge_problem(d.nodes[source].kind, d.nodes[target].kind):
            continue
        guard = draw(st.sampled_from(["", "", "x", "else", "a;b (c)"]))
        d.edges.append(
            Edge(
                id=f"e{k}",
                source=source,
                target=target,
                kind="control_flow",
                properties={"guard": guard} if guard else {},
            )
        )
    assert d.integrity_errors() == [] and activity_rule_errors(d) == []
    return d


@settings(max_examples=1500, deadline=None)
@given(activity_graphs())
def test_every_activity_diagram_exports(diagram):
    """The structured walk never fails, draws every action once and uses connectors in pairs (#107 review)."""
    lines = [line.strip() for line in exchange.export_plantuml(diagram).splitlines()]
    actions = [n for n in diagram.nodes.values() if n.kind.endswith(("action", "buffer_node", "store_node"))]
    assert len([line for line in lines if line.startswith(":")]) == len(actions)
    connectors = [line for line in lines if re.fullmatch(r"\(\d+\)", line)]
    assert all(connectors.count(c) >= 2 for c in connectors)


def nested_decisions(depth: int) -> Diagram:
    d = Diagram(id="deep", title="Deep", type="activity")
    d.nodes["end"] = Node(id="end", name="end", kind="activity_final_node")
    for i in range(depth):
        d.nodes[f"d{i}"] = Node(id=f"d{i}", name=f"d{i}", kind="decision_node")
        d.nodes[f"a{i}"] = Node(id=f"a{i}", name=f"a{i}", kind="opaque_action")
        d.edges.append(Edge(id=f"x{i}", source=f"d{i}", target=f"a{i}", kind="control_flow", properties={"guard": "x"}))
        nxt = f"d{i + 1}" if i + 1 < depth else "end"
        d.edges.append(Edge(id=f"y{i}", source=f"d{i}", target=nxt, kind="control_flow", properties={"guard": "else"}))
    return d


def test_long_activities_export_in_linear_time():
    """The meeting point of every structure comes from one post-dominator pass, not a search per branch."""
    d = Diagram(id="long", title="Long", type="activity")
    n = 3000
    for i in range(n):
        d.nodes |= {f"d{i}": Node(id=f"d{i}", name="d", kind="decision_node")}
        d.nodes |= {f"a{i}": Node(id=f"a{i}", name="a", kind="opaque_action")}
        d.nodes |= {f"m{i}": Node(id=f"m{i}", name="m", kind="merge_node")}
        d.edges += [
            Edge(id=f"x{i}", source=f"d{i}", target=f"a{i}", kind="control_flow", properties={"guard": "x"}),
            Edge(id=f"y{i}", source=f"d{i}", target=f"m{i}", kind="control_flow", properties={"guard": "else"}),
            Edge(id=f"z{i}", source=f"a{i}", target=f"m{i}", kind="control_flow"),
        ]
        if i + 1 < n:
            d.edges.append(Edge(id=f"w{i}", source=f"m{i}", target=f"d{i + 1}", kind="control_flow"))
    started = time.perf_counter()
    assert exchange.export_plantuml(d).count("endif") == n  # one after the other, none nested
    assert time.perf_counter() - started < 3


def test_deeply_nested_activity_is_refused_cleanly(tmp_path):
    assert exchange.export_plantuml(nested_decisions(50)).count("endif") == 50
    with pytest.raises(exchange.ExchangeError, match="nested"):
        exchange.export_plantuml(nested_decisions(500))
    client = TestClient(create_app(store=FileStore(tmp_path)))
    imported = client.post("/api/diagrams/import", json={"document": exchange.export_document(nested_decisions(500))})
    diagram_id = imported.json()["diagram"]["id"]
    r = client.get(f"/api/diagrams/{diagram_id}/export?format=plantuml")
    assert r.status_code == 400 and "nested" in r.json()["detail"]


def test_export_failures_on_deep_input_are_400_and_defects_stay_500(tmp_path, monkeypatch):
    client = TestClient(create_app(store=FileStore(tmp_path)), raise_server_exceptions=False)
    diagram_id = client.post("/api/diagrams", json={"title": "x", "template": "order-process"}).json()["diagram"]["id"]

    def too_deep(diagram):
        raise RecursionError("too deep")

    monkeypatch.setattr(exchange, "_activity_plantuml", too_deep)
    r = client.get(f"/api/diagrams/{diagram_id}/export?format=plantuml")
    assert r.status_code == 400 and "nested too deeply" in r.json()["detail"]

    def defect(diagram):
        raise RuntimeError("a bug")

    monkeypatch.setattr(exchange, "_activity_plantuml", defect)
    assert client.get(f"/api/diagrams/{diagram_id}/export?format=plantuml").status_code == 500  # a defect, logged


def test_deep_sequence_and_deployment_nesting_is_refused_cleanly():
    seq = Diagram(id="s", title="S", type="sequence")
    seq.interaction.lifelines = [interaction_model.Lifeline(id="a", name="a")]
    fragments = seq.interaction.fragments
    for i in range(exchange.MAX_PLANTUML_NESTING + 5):
        frame = interaction_model.CombinedFragment(id=f"cf{i}", operator="opt", covered=["a"])
        frame.operands = [interaction_model.InteractionOperand(id=f"op{i}")]
        fragments.append(frame)
        fragments = frame.operands[0].fragments
    with pytest.raises(exchange.ExchangeError, match="nested"):
        exchange.export_plantuml(seq)
    deployment = Diagram(id="d", title="D")
    for i in range(exchange.MAX_PLANTUML_NESTING + 5):
        parent = f"g{i - 1}" if i else None
        deployment.nodes[f"g{i}"] = Node(id=f"g{i}", name=f"g{i}", kind="group", parent=parent)
    deployment.nodes["leaf"] = Node(
        id="leaf", name="leaf", kind="service", parent=f"g{exchange.MAX_PLANTUML_NESTING + 4}"
    )
    with pytest.raises(exchange.ExchangeError, match="nested"):
        exchange.export_plantuml(deployment)


# ---- sequence diagrams -----------------------------------------------------------------------


def sequence(*steps: tuple, lifelines: tuple = ("a", "b", "c")) -> Diagram:
    d = Diagram(id="s", title="S", type="sequence")
    for name in lifelines:
        operations.apply(d, "add_lifeline", {"name": name, "id": name})
    for op, args in steps:
        operations.apply(d, op, args)
    return d


def msg(sender, receiver, sort="synchCall", name="m", **more) -> tuple:
    return ("add_message", {"sender": sender, "receiver": receiver, "name": name, "sort": sort, **more})


def test_sequence_participants_and_actors():
    d = Diagram(id="s", title="S", type="sequence")
    operations.apply(d, "add_lifeline", {"name": "Kunde", "actor": True, "id": "kunde"})
    operations.apply(d, "add_lifeline", {"name": "Shop", "type": "WebApp", "id": "shop-1"})
    assert body(d) == ['actor "Kunde" as L_kunde', 'participant "Shop : WebApp" as L_shop_1']


def test_sequence_message_sorts():
    sorts = ("synchCall", "asynchCall", "asynchSignal", "reply")
    d = sequence(*(msg("a", "b", sort, name=sort) for sort in sorts), msg("a", "a", name="self"))
    assert body(d)[3:] == [
        "L_a -> L_b : synchCall",
        "L_a ->> L_b : asynchCall",
        "L_a ->> L_b : asynchSignal",
        "L_a -->> L_b : reply",
        "L_a -> L_a : self",
    ]


def test_sequence_found_and_lost_messages():
    d = sequence(msg(None, "a", "asynchSignal", name="found"), msg("a", None, name="lost"))
    assert body(d)[3:] == ["[o->> L_a : found", "L_a ->o] : lost"]


def test_sequence_create_delete_and_destruction():
    d = sequence(
        msg("a", "b", "createMessage", name="new"),
        msg("a", "b", "deleteMessage", name="weg"),
        ("add_destruction_occurrence_specification", {"lifeline": "c"}),
    )
    assert body(d)[3:] == ["create L_b", "L_a -->> L_b : new", "L_a -> L_b : weg", "destroy L_b", "destroy L_c"]


def test_sequence_destruction_before_any_message_and_self_creation():
    d = sequence(("add_destruction_occurrence_specification", {"lifeline": "c"}), msg("a", "a", "createMessage"))
    # PlantUML destroys a participant only after some message, and refuses a participant creating itself
    assert body(d)[3:] == ["L_c -[hidden]> L_c", "destroy L_c", "L_a -->> L_a : m"]


def test_sequence_alt_with_else_and_keyword_operators():
    steps = [msg("a", "b", id="m1"), msg("a", "b", id="m2")]
    steps.append(("add_combined_fragment", {"operator": "alt", "guard": "x > 0", "contents": ["m1"], "id": "cf1"}))
    steps.append(("add_interaction_operand", {"fragment": "cf1", "guard": "else"}))
    steps.append(msg("b", "a", name="n", operand="op2"))
    for k, operator in enumerate(("opt", "loop", "break", "critical"), start=3):
        steps.append(msg("a", "b", name=operator, id=f"x{k}"))
        steps.append(("add_combined_fragment", {"operator": operator, "contents": [f"x{k}"]}))
    steps.append(msg("a", "b", name="p", id="m9"))
    steps.append(("add_combined_fragment", {"operator": "par", "guard": "", "contents": ["m9"], "id": "cf9"}))
    lines = body(sequence(*steps))[3:]
    assert lines[:5] == ["alt x > 0", "  L_a -> L_b : m", "else else", "  L_b -> L_a : n", "end"]
    for operator in ("opt", "loop", "break", "critical", "par"):
        assert operator in lines, lines
    assert lines.count("end") == 6


@pytest.mark.parametrize("operator", ["neg", "assert", "strict", "seq", "ignore", "consider"])
def test_sequence_operators_without_keyword_are_groups(operator):
    extra = {"messages": ["ping", "pong"]} if operator in ("ignore", "consider") else {}
    d = sequence(
        msg("a", "b", id="m1"),
        ("add_combined_fragment", {"operator": operator, "guard": "g [1]", "contents": ["m1"], "id": "cf", **extra}),
    )
    listed = " {ping, pong}" if extra else ""
    assert body(d)[3:] == [f"group {operator}{listed} [g &#91;1&#93;]", "  L_a -> L_b : m", "end"]


def test_sequence_interaction_use():
    d = sequence(("add_interaction_use", {"refers_to": "Versand <b>", "covered": ["a", "c"]}))
    assert body(d)[3:] == ["ref over L_a, L_c : Versand &#60;b>"]


def test_sequence_executions_become_activations():
    d = sequence(
        msg("a", "b", id="m1"),
        msg("b", "c", id="m2"),
        msg("c", "b", "reply", id="m3"),
        msg("b", "a", "reply", id="m4"),
        ("add_execution_specification", {"lifeline": "b", "start": "m1", "finish": "m4"}),
        ("add_execution_specification", {"lifeline": "c", "start": "m2"}),  # until c's last message: m3
    )
    assert body(d)[3:] == [
        "L_a -> L_b : m",
        "activate L_b",
        "L_b -> L_c : m",
        "activate L_c",
        "L_c -->> L_b : m",
        "deactivate L_c",
        "L_b -->> L_a : m",
        "deactivate L_b",
    ]


def test_sequence_activations_of_one_message_open_before_they_close():
    d = sequence(
        msg("a", "b", id="m1"),
        ("add_execution_specification", {"lifeline": "b", "start": "m1", "finish": "m1"}),
        ("add_execution_specification", {"lifeline": "b", "start": "m1"}),
    )
    assert body(d)[3:] == ["L_a -> L_b : m", "activate L_b", "activate L_b", "deactivate L_b", "deactivate L_b"]


def test_sequence_text_is_shown_literally():
    d = Diagram(id="s", title="S", type="sequence")
    operations.apply(d, "add_lifeline", {"name": 'x" as y', "type": "<T>", "id": "x"})
    operations.apply(d, "add_message", {"sender": "x", "receiver": "x", "name": "a\nb **c**"})
    assert body(d) == ['participant "x&#34; as y : &#60;T>" as L_x', "L_x -> L_x : a b ~*~*c~*~*"]


# ---- API and PlantUML itself -----------------------------------------------------------------


def test_export_endpoint_for_behaviour_diagrams(tmp_path):
    client = TestClient(create_app(store=FileStore(tmp_path)))
    for template in ("order-process", "checkout"):
        created = client.post("/api/diagrams", json={"title": template, "template": template}).json()
        diagram_id = created["diagram"]["id"]
        r = client.get(f"/api/diagrams/{diagram_id}/export?format=plantuml")
        assert r.status_code == 200 and r.text.startswith("@startuml\n") and r.text.endswith("@enduml\n")
        assert r.headers["content-disposition"] == f'attachment; filename="{diagram_id}.puml"'
    cfg = client.get("/api/config").json()["diagram_types"]
    assert all(cfg[t]["features"]["plantuml"] for t in ("deployment", "activity", "sequence"))


JAR = os.environ.get("ARCVIZ_PLANTUML_JAR")


@pytest.mark.skipif(not JAR or not shutil.which("java"), reason="needs ARCVIZ_PLANTUML_JAR and java")
def test_plantuml_renders_the_exports(tmp_path):
    """Every construct above and every template, rendered by PlantUML (checked with 1.2026.8)."""
    EXPORTED.clear()
    for name, test in sorted(globals().items()):
        if name.startswith(("test_activity_", "test_sequence_")) and not inspect.signature(test).parameters:
            test()
    for operator in ("neg", "assert", "strict", "seq", "ignore", "consider"):
        test_sequence_operators_without_keyword_are_groups(operator)
    diagrams = [*EXPORTED, *(TEMPLATES[t]("x", "X") for t in sorted(TEMPLATES) if t != "empty")]
    for n, diagram in enumerate(diagrams):
        (tmp_path / f"d{n:03}.puml").write_text(exchange.export_plantuml(diagram), encoding="utf-8")
    result = subprocess.run(
        ["java", "-jar", str(JAR), "-tsvg", "-failfast2", str(tmp_path / "*.puml")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list(tmp_path.glob("*.svg"))) == len(diagrams)
