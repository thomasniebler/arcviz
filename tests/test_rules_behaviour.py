"""Rule checks of activity and sequence diagrams (spec: docs/architecture/specs/rule-checks.md, #123)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from arcviz import operations, rules
from arcviz.model import Diagram, Edge, Node
from arcviz.rules import Finding
from arcviz.server import create_app
from arcviz.templates import TEMPLATES
from tests.conftest import ScriptedProvider


def by_rule(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


# ---- activity diagrams -----------------------------------------------------------------------


def activity(*nodes: tuple[str, str], flows: tuple = ()) -> Diagram:
    """Nodes `(id, kind)` and control flows `(source, target[, guard])`."""
    d = Diagram(id="a", title="A", type="activity")
    for node_id, kind in nodes:
        operations.apply(d, "add_node", {"name": node_id, "kind": kind, "id": node_id})
    for source, target, *guard in flows:
        args = {"source": source, "target": target, "kind": "control_flow"}
        if guard:
            args["guard"] = guard[0]
        operations.apply(d, "add_edge", args)
    return d


LINE = (("start", "initial_node"), ("a", "opaque_action"), ("end", "activity_final_node"))


def test_activity_without_initial_node():
    d = activity(("a", "opaque_action"), ("b", "opaque_action"), flows=(("a", "b"),))
    [finding] = by_rule(rules.run_checks(d), "missing-initial-node")
    assert (finding.category, finding.severity) == ("suggestion", "low")
    assert finding.elements == ["a"]  # where the flows begin
    loop = activity(("a", "opaque_action"), ("b", "opaque_action"), flows=(("a", "b"), ("b", "a")))
    assert by_rule(rules.run_checks(loop), "missing-initial-node")[0].elements == ["a", "b"]  # no entry: all
    assert (
        by_rule(rules.run_checks(activity(*LINE, flows=(("start", "a"), ("a", "end")))), "missing-initial-node") == []
    )
    assert rules.run_checks(Diagram(id="e", title="E", type="activity")) == []  # nothing drawn yet: nothing to say


def test_node_unreachable_from_the_initial_node():
    d = activity(*LINE, ("lost", "opaque_action"), flows=(("start", "a"), ("a", "end"), ("lost", "end")))
    [finding] = by_rule(rules.run_checks(d), "unreachable-node")
    assert finding.elements == ["lost"] and (finding.category, finding.severity) == ("suggestion", "low")
    assert "„lost“" in finding.message and "lost" in finding.suggestion
    no_initial = activity(("a", "opaque_action"), ("b", "opaque_action"))
    assert by_rule(rules.run_checks(no_initial), "unreachable-node") == []  # missing-initial-node covers it


def test_decision_with_unguarded_outgoing_flows():
    nodes = (*LINE, ("d", "decision_node"), ("b", "opaque_action"))
    d = activity(*nodes, flows=(("start", "d"), ("d", "a", "ok"), ("d", "b"), ("a", "end"), ("b", "end")))
    [finding] = by_rule(rules.run_checks(d), "decision-without-guards")
    assert (finding.category, finding.severity) == ("risk", "medium")
    assert finding.elements == ["d"] and finding.edges == ["e3"]  # only the flow without a guard
    guarded = activity(*nodes, flows=(("start", "d"), ("d", "a", "x"), ("d", "b", "else"), ("a", "end"), ("b", "end")))
    assert by_rule(rules.run_checks(guarded), "decision-without-guards") == []
    single = activity(*nodes, flows=(("start", "d"), ("d", "a"), ("a", "end")))
    assert by_rule(rules.run_checks(single), "decision-without-guards") == []  # one way out: nothing to decide


def test_decision_without_else():
    nodes = (*LINE, ("d", "decision_node"), ("b", "opaque_action"))
    d = activity(*nodes, flows=(("start", "d"), ("d", "a", "x > 0"), ("d", "b", "x < 0"), ("a", "end"), ("b", "end")))
    [finding] = by_rule(rules.run_checks(d), "decision-without-else")
    assert (finding.category, finding.severity) == ("suggestion", "low") and finding.elements == ["d"]
    assert sorted(finding.edges) == ["e2", "e3"]
    for otherwise in ("else", "Else", "[else]"):
        with_else = activity(
            *nodes, flows=(("start", "d"), ("d", "a", "x > 0"), ("d", "b", otherwise), ("a", "end"), ("b", "end"))
        )
        assert by_rule(rules.run_checks(with_else), "decision-without-else") == [], otherwise
    unguarded = activity(*nodes, flows=(("start", "d"), ("d", "a", "x"), ("d", "b"), ("a", "end"), ("b", "end")))
    assert by_rule(rules.run_checks(unguarded), "decision-without-else") == []  # decision-without-guards says it


def test_fork_without_join():
    nodes = (*LINE, ("f", "fork_node"), ("b", "opaque_action"), ("stop", "flow_final_node"))
    d = activity(*nodes, flows=(("start", "f"), ("f", "a"), ("f", "b"), ("a", "end"), ("b", "stop")))
    [finding] = by_rule(rules.run_checks(d), "fork-without-join")
    assert (finding.category, finding.severity) == ("suggestion", "low") and finding.elements == ["f"]
    joined = activity(
        *nodes,
        ("j", "join_node"),
        flows=(("start", "f"), ("f", "a"), ("f", "b"), ("a", "j"), ("b", "j"), ("j", "end")),
    )
    assert by_rule(rules.run_checks(joined), "fork-without-join") == []


def test_flow_without_final_node():
    nodes = (*LINE, ("b", "opaque_action"), ("c", "opaque_action"), ("x", "opaque_action"), ("y", "opaque_action"))
    flows = (("start", "a"), ("a", "end"), ("start", "b"), ("b", "c"), ("start", "x"), ("x", "y"), ("y", "x"))
    found = by_rule(rules.run_checks(activity(*nodes, flows=flows)), "flow-without-final-node")
    # one finding per place the flow gets stuck: the dead end c (not b before it) and the loop x <-> y
    assert [f.elements for f in found] == [["c"], ["x", "y"]]
    assert all((f.category, f.severity) == ("suggestion", "low") for f in found)
    assert (
        by_rule(rules.run_checks(activity(*LINE, flows=(("start", "a"), ("a", "end")))), "flow-without-final-node")
        == []
    )


# ---- sequence diagrams -----------------------------------------------------------------------


def sequence(*steps: tuple) -> Diagram:
    """Lifelines a, b, c, then `(op, args)` steps."""
    d = Diagram(id="s", title="S", type="sequence")
    for name in ("a", "b", "c"):
        operations.apply(d, "add_lifeline", {"name": name, "id": name})
    for op, args in steps:
        operations.apply(d, op, args)
    return d


def msg(sender: str | None, receiver: str | None, sort: str = "synchCall", **more) -> tuple:
    return ("add_message", {"sender": sender, "receiver": receiver, "name": f"{sort}", "sort": sort, **more})


def test_synchronous_call_without_reply():
    d = sequence(msg("a", "b", id="m1"), msg("b", "c", id="m2"), msg("c", "b", "reply"), msg("b", "b", id="self"))
    [finding] = by_rule(rules.run_checks(d), "call-without-reply")
    assert finding.elements == ["m1"] and (finding.category, finding.severity) == ("suggestion", "low")
    answered = sequence(msg("a", "b"), msg("b", "a", "reply"))
    assert by_rule(rules.run_checks(answered), "call-without-reply") == []
    asynchronous = sequence(msg("a", "b", "asynchCall"), msg("a", "b", "asynchSignal"))
    assert by_rule(rules.run_checks(asynchronous), "call-without-reply") == []


def test_reply_answered_in_alternatives():
    """A call before an alt may be answered differently in each operand: no reply is an orphan."""
    d = sequence(
        msg("a", "b", id="m1"),
        msg("b", "a", "reply", id="ok"),
        ("add_combined_fragment", {"operator": "alt", "guard": "gut", "contents": ["ok"], "id": "cf"}),
        ("add_interaction_operand", {"fragment": "cf", "guard": "else"}),
        msg("b", "a", "reply", id="err", operand="op2"),
        msg("b", "a", "reply", id="late"),
    )
    found = rules.run_checks(d)
    assert [f.elements for f in by_rule(found, "reply-without-call")] == [["late"]]  # answered in both operands
    assert by_rule(found, "call-without-reply") == []


def test_reply_without_call():
    d = sequence(msg("a", "b", id="m1"), msg("b", "a", "reply", id="r1"), msg("b", "a", "reply", id="r2"))
    [finding] = by_rule(rules.run_checks(d), "reply-without-call")
    assert finding.elements == ["r2"] and (finding.category, finding.severity) == ("risk", "medium")
    wrong_way = sequence(msg("a", "b", id="m1"), msg("a", "b", "reply", id="r1"))
    assert [f.elements for f in by_rule(rules.run_checks(wrong_way), "reply-without-call")] == [["r1"]]
    to_asynch = sequence(msg("a", "b", "asynchCall"), msg("b", "a", "reply"))
    assert by_rule(rules.run_checks(to_asynch), "reply-without-call") == []


def test_fragment_covering_a_destroyed_lifeline():
    d = sequence(
        msg("a", "b", id="m1"),
        msg("a", "c", "deleteMessage", id="kill"),
        msg("a", "b", id="m2"),
        ("add_combined_fragment", {"operator": "opt", "covered": ["a", "b", "c"], "contents": ["m2"], "id": "cf"}),
        ("add_interaction_use", {"refers_to": "Abschluss", "covered": ["a", "b"], "id": "ref1"}),
    )
    [finding] = by_rule(rules.run_checks(d), "fragment-after-destruction")
    assert finding.elements == ["cf", "c"] and (finding.category, finding.severity) == ("suggestion", "low")
    before = sequence(
        msg("a", "b", id="m1"),
        ("add_combined_fragment", {"operator": "opt", "covered": ["a", "b", "c"], "contents": ["m1"], "id": "cf"}),
        msg("a", "c", "deleteMessage"),
    )
    assert by_rule(rules.run_checks(before), "fragment-after-destruction") == []


def test_alt_with_a_single_operand():
    d = sequence(
        msg("a", "b", id="m1"),
        ("add_combined_fragment", {"operator": "alt", "guard": "x", "contents": ["m1"], "id": "cf"}),
    )
    [finding] = by_rule(rules.run_checks(d), "alt-with-single-operand")
    assert finding.elements == ["cf"] and (finding.category, finding.severity) == ("suggestion", "low")
    operations.apply(d, "add_interaction_operand", {"fragment": "cf", "guard": "else"})
    assert by_rule(rules.run_checks(d), "alt-with-single-operand") == []


def test_execution_that_never_finishes_although_its_call_is_answered():
    steps = (msg("a", "b", id="m1"), msg("b", "c", id="m2"), msg("c", "b", "reply", id="m3"))
    d = sequence(
        *steps, msg("b", "a", "reply", id="m4"), ("add_execution_specification", {"lifeline": "b", "start": "m1"})
    )
    [finding] = by_rule(rules.run_checks(d), "unfinished-execution")
    assert (finding.category, finding.severity) == ("suggestion", "low")
    assert finding.elements == ["ex1", "m4"]  # the matching reply, not the nested one (m3)
    finished = sequence(
        *steps,
        msg("b", "a", "reply", id="m4"),
        ("add_execution_specification", {"lifeline": "b", "start": "m1", "finish": "m4"}),
    )
    assert by_rule(rules.run_checks(finished), "unfinished-execution") == []
    unanswered = sequence(*steps, ("add_execution_specification", {"lifeline": "b", "start": "m1"}))
    assert by_rule(rules.run_checks(unanswered), "unfinished-execution") == []
    alternatives = sequence(
        *steps,
        msg("b", "a", "reply", id="ok"),
        ("add_combined_fragment", {"operator": "alt", "guard": "gut", "contents": ["ok"], "id": "cf"}),
        ("add_interaction_operand", {"fragment": "cf", "guard": "else"}),
        msg("b", "a", "reply", id="err", operand="op2"),
        ("add_execution_specification", {"lifeline": "b", "start": "m1"}),
    )
    assert by_rule(rules.run_checks(alternatives), "unfinished-execution") == []  # no single reply to end with


def test_the_callers_execution_is_left_alone():
    d = sequence(
        msg("a", "b", id="m1"),
        msg("b", "a", "reply", id="m2"),
        ("add_execution_specification", {"lifeline": "a", "start": "m1"}),  # the caller's: the reply does not end it
    )
    assert by_rule(rules.run_checks(d), "unfinished-execution") == []


def test_replies_are_matched_once_per_run(monkeypatch):
    calls = []
    original = rules._compute_replies
    monkeypatch.setattr(rules, "_compute_replies", lambda interaction: calls.append(1) or original(interaction))
    rules.run_checks(sequence(msg("a", "b"), msg("b", "a", "reply")))
    assert len(calls) == 1


def test_activity_rules_stay_fast_on_large_diagrams():
    """Reachability towards joins and final nodes is one reverse search, not one search per node (#123 review)."""
    n = 6000
    nodes = {f"a{i}": Node(id=f"a{i}", name=f"a{i}", kind="opaque_action") for i in range(n)}
    nodes |= {f"f{i}": Node(id=f"f{i}", name=f"f{i}", kind="fork_node") for i in range(n // 2)}
    edges = [Edge(id=f"e{i}", source=f"a{i}", target=f"a{i + 1}", kind="control_flow") for i in range(n - 1)]
    edges += [Edge(id=f"x{i}", source=f"f{i}", target="a0", kind="control_flow") for i in range(n // 2)]
    d = Diagram(id="big", title="Big", type="activity", nodes=nodes, edges=edges)
    started = time.perf_counter()
    found = rules.run_checks(d)
    assert time.perf_counter() - started < 3
    assert [f.elements for f in by_rule(found, "flow-without-final-node")] == [[f"a{n - 1}"]]
    assert len(by_rule(found, "fork-without-join")) == n // 2


# ---- every type ------------------------------------------------------------------------------


@pytest.mark.parametrize("diagram_type", ["activity", "sequence"])
def test_rules_are_registered_per_type(diagram_type):
    ids = [r.id for r in rules.rules_for(diagram_type)]
    assert ids and len(ids) == len(set(ids))
    assert all(r.title and r.category in rules.CATEGORIES for r in rules.rules_for(diagram_type))


@pytest.mark.parametrize(
    ("template", "expected"),
    [("order-process", ["decision-without-else:verfuegbar:e11+e12"]), ("checkout", [])],  # ja/nein, no else
)
def test_findings_of_the_behaviour_templates(template, expected):
    d = TEMPLATES[template]("x", "X")
    assert [f.id for f in rules.run_checks(d)] == expected
    assert rules.rules_for(d.type)


def test_checks_endpoint_for_behaviour_diagrams(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    for template in ("order-process", "checkout"):
        created = client.post("/api/diagrams", json={"title": template, "template": template}).json()
        body = client.get(f"/api/diagrams/{created['diagram']['id']}/checks").json()
        assert body["diagram_type"] == created["diagram"]["type"]
        assert [r["id"] for r in body["rules"]] == [r.id for r in rules.rules_for(body["diagram_type"])]
        assert body["rules"]
