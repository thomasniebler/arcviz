"""Version comparison of sequence diagrams (spec: docs/architecture/specs/version-comparison.md, #108)."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from arcviz import diff, operations, render, sequence
from arcviz.model import Diagram
from arcviz.server import create_app
from arcviz.store import FileStore
from arcviz.templates import TEMPLATES


def ok(d: Diagram, op: str, **args) -> None:
    operations.apply(d, op, args)


def base() -> Diagram:
    """Lifelines a, b, c; m1 a->b, then alt cf [x] {m2, m3} / [else] {m4}, then m5, execution ex1 on b m1..m5."""
    d = Diagram(id="s", title="S", type="sequence")
    for name in ("a", "b", "c"):
        ok(d, "add_lifeline", name=name, id=name)
    ok(d, "add_message", sender="a", receiver="b", name="m1", id="m1")
    ok(d, "add_message", sender="b", receiver="c", name="m2", id="m2")
    ok(d, "add_message", sender="c", receiver="b", name="m3", sort="reply", id="m3")
    ok(d, "add_combined_fragment", operator="alt", guard="x", contents=["m2", "m3"], id="cf")
    ok(d, "add_interaction_operand", fragment="cf", guard="else")
    ok(d, "add_message", sender="b", receiver="a", name="m4", operand="op2", id="m4")
    ok(d, "add_message", sender="b", receiver="a", name="m5", sort="reply", id="m5")
    ok(d, "add_execution_specification", lifeline="b", start="m1", finish="m5", id="ex1")
    return d


def ids(fragments) -> list[str]:
    return [f.id for f in fragments]


def test_identical_states_have_no_difference():
    d = base()
    changes = diff.compare(d, d.model_copy(deep=True))
    assert changes.to_dict()["summary"] == {"added": 0, "removed": 0, "changed": 0}
    assert changes.added_elements == changes.removed_elements == changes.changed_elements == []


def test_compare_reports_added_removed_and_changed_elements():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "add_lifeline", name="d", id="d")
    ok(new, "update_message", id="m1", name="go")  # changed: own attribute
    ok(new, "update_interaction_operand", id="op2", guard="sonst")  # changed: operand guard
    ok(new, "update_lifeline", id="c", type="Service")  # changed: lifeline
    ok(new, "remove_fragment", id="m4")
    changes = diff.compare(old, new)
    assert changes.added_elements == ["d"]
    assert changes.removed_elements == ["m4"]
    assert changes.changed_elements == ["c", "m1", "op2"]
    assert changes.added_nodes == changes.removed_nodes == changes.changed_nodes == []
    assert changes.to_dict()["summary"] == {"added": 1, "removed": 1, "changed": 3}


def test_moved_elements_count_as_changed_but_not_their_neighbours():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "move_fragment", id="m3", before="m2")  # reordered inside its operand: only m3 moved
    ok(new, "move_lifeline", id="a", before=None)  # a to the right end: only a moved
    assert diff.compare(old, new).changed_elements == ["a", "m3"]
    other = old.model_copy(deep=True)
    ok(other, "move_fragment", id="m4", operand="op1")  # into another operand
    assert diff.compare(old, other).changed_elements == ["m4"]


def test_merged_puts_a_removed_message_after_its_former_predecessor():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "remove_fragment", id="m3")
    ok(new, "add_message", sender="b", receiver="c", name="new", operand="op1", id="n1")
    view, marks, edge_marks = diff.merged(old, new, diff.compare(old, new))
    cf = sequence.find(view.interaction, "cf")
    assert ids(cf.operands[0].fragments) == ["m2", "m3", "n1"]
    assert marks == {"m3": "removed", "n1": "added"} and edge_marks == {}


def test_merged_falls_back_to_the_nearest_surviving_predecessor_then_the_operand_start():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "remove_fragment", id="m2")
    ok(new, "remove_fragment", id="m4")
    ok(new, "add_message", sender="a", receiver="b", name="first", operand="op2", id="n1")
    view, marks, _ = diff.merged(old, new, diff.compare(old, new))
    cf = sequence.find(view.interaction, "cf")
    assert ids(cf.operands[0].fragments) == ["m2", "m3"]  # m2 had no predecessor: start of its operand
    assert ids(cf.operands[1].fragments) == ["m4", "n1"]
    assert marks["m2"] == marks["m4"] == "removed"


def test_merged_restores_removed_fragments_with_their_contents_and_lifelines_in_place():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "move_fragment", id="m2", before="cf")  # survives outside the fragment that is removed next
    ok(new, "remove_fragment", id="cf")
    ok(new, "remove_lifeline", id="c")  # takes m2 with it
    changes = diff.compare(old, new)
    assert set(changes.removed_elements) == {"c", "cf", "op1", "op2", "m2", "m3", "m4"}
    view, marks, _ = diff.merged(old, new, changes)
    assert [lf.id for lf in view.interaction.lifelines] == ["a", "b", "c"]
    assert ids(view.interaction.fragments) == ["m1", "cf", "m5"]
    cf = sequence.find(view.interaction, "cf")
    assert [op.id for op in cf.operands] == ["op1", "op2"]
    assert ids(cf.operands[0].fragments) == ["m2", "m3"] and ids(cf.operands[1].fragments) == ["m4"]
    all_ids = sequence.element_ids(view.interaction)
    assert len(all_ids) == len(set(all_ids)) and set(all_ids) == set(sequence.element_ids(old.interaction))
    assert {k for k, v in marks.items() if v == "removed"} == set(changes.removed_elements)


def test_merged_keeps_removed_executions_and_draws_the_marks():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "remove_fragment", id="ex1")
    ok(new, "update_message", id="m5", name="fertig")
    view, marks, edge_marks = diff.merged(old, new, diff.compare(old, new))
    assert [e.id for e in view.interaction.executions] == ["ex1"]
    payload = render.payload(view, marks=marks, edge_marks=edge_marks)
    root = ET.fromstring(payload["svg"])
    classes = {g.get("data-id"): g.get("class", "") for g in root.iter() if g.get("data-id")}
    assert "removed" in classes["ex1"].split() and "changed" in classes["m5"].split()
    assert "removed" not in classes["m1"].split()


def test_an_id_reused_by_another_kind_of_element_is_removed_and_added():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "remove_fragment", id="m4")
    ok(new, "add_lifeline", name="m4", id="m4")  # imported or hand-made data may reuse ids across kinds
    changes = diff.compare(old, new)
    assert (
        changes.removed_elements == ["m4"] and changes.added_elements == ["m4"] and "m4" not in changes.changed_elements
    )
    view, marks, _ = diff.merged(old, new, changes)
    all_ids = sequence.element_ids(view.interaction)
    assert len(all_ids) == len(set(all_ids)) and "-m4" in all_ids  # the removed message under another id
    assert marks["m4"] == "added" and marks["-m4"] == "removed"


def test_a_lifeline_id_reused_by_a_message_keeps_the_restored_messages_drawable():
    """The removed lifeline comes back under another id and its removed messages point at it (#108 review)."""
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "remove_lifeline", id="c")  # takes m2, m3 with it
    ok(new, "add_message", sender="a", receiver="b", name="c", id="c")
    changes = diff.compare(old, new)
    view, marks, _ = diff.merged(old, new, changes)
    lifelines = {lf.id for lf in view.interaction.lifelines}
    assert "-c" in lifelines and marks["-c"] == "removed" and marks["c"] == "added"
    for message in sequence.messages(view.interaction):
        assert {message.sender, message.receiver} - {None} <= lifelines, message
    ET.fromstring(render.payload(view, marks=marks)["svg"])  # renders instead of a KeyError


def test_contents_of_a_container_whose_id_now_names_another_element_are_still_drawn():
    old = base()
    new = old.model_copy(deep=True)
    ok(new, "remove_fragment", id="cf")
    ok(new, "add_lifeline", name="op1", id="op1")  # the removed operand's id, reused by a lifeline
    ok(new, "add_lifeline", name="cf", id="cf")  # and the removed fragment's id
    changes = diff.compare(old, new)
    view, _, _ = diff.merged(old, new, changes)
    shown = sequence.element_ids(view.interaction)
    assert len(shown) == len(set(shown))
    restored = sequence.find(view.interaction, "-cf")
    assert isinstance(restored, sequence.CombinedFragment) and [op.id for op in restored.operands] == ["-op1", "op2"]
    assert {"m2", "m3", "m4"} <= set(shown)


def test_long_reorders_are_compared_without_a_quadratic_table():
    old = Diagram(id="s", title="S", type="sequence")
    ok(old, "add_lifeline", name="a", id="a")
    n = 8000
    old.interaction.fragments = [
        sequence.Message(id=f"m{i}", name="x", sender="a", receiver="a") for i in range(n)
    ]  # built directly: thousands of apply() calls would take long
    new = old.model_copy(deep=True)
    new.interaction.fragments.reverse()
    started = time.perf_counter()
    changes = diff.compare(old, new)
    assert time.perf_counter() - started < 3
    assert len(changes.changed_elements) == n - 1  # one keeps its place, every other one moved


def test_compare_endpoint_for_sequence_diagrams(tmp_path):
    client = TestClient(create_app(store=FileStore(tmp_path)))
    created = client.post("/api/diagrams", json={"title": "Checkout", "template": "checkout"}).json()
    diagram_id = created["diagram"]["id"]
    client.post(f"/api/diagrams/{diagram_id}/ops", json={"op": "remove_fragment", "args": {"id": "m6"}})
    client.post(f"/api/diagrams/{diagram_id}/ops", json={"op": "update_message", "args": {"id": "m1", "name": "go"}})
    r = client.get(f"/api/diagrams/{diagram_id}/compare?base=1")
    assert r.status_code == 200
    body = r.json()
    assert body["renderer"] == "svg" and body["mermaid"] is None and body["focus"] is None
    assert body["diff"]["removed_elements"] == ["m6"] and body["diff"]["changed_elements"] == ["m1"]
    assert body["diff"]["summary"] == {"added": 0, "removed": 1, "changed": 1}
    assert 'data-id="m6"' in body["svg"]
    cfg = client.get("/api/config").json()["diagram_types"]["sequence"]
    assert cfg["features"]["compare"]


def test_template_compared_with_an_empty_state():
    old = TEMPLATES["checkout"]("c", "C")
    new = Diagram(id="c", title="C", type="sequence")
    changes = diff.compare(old, new)
    view, marks, _ = diff.merged(old, new, changes)
    assert sequence.element_ids(view.interaction) == sequence.element_ids(old.interaction)
    assert set(marks.values()) == {"removed"}
