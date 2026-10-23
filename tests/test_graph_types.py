"""Graph profiles and typed fields (spec: docs/architecture/specs/uml-graph-types.md, ADR-0183, ADR-0177)."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange, graph_types, operations
from arcviz.graph_types import PROFILES, Field, FieldError, clean_fields, field_errors, profile_of
from arcviz.model import Diagram, Edge, Node
from arcviz.operations import OperationError, apply
from arcviz.server import create_app
from arcviz.store import FileStore
from arcviz.templates import TEMPLATES

FIELDS = (Field("note_lines", max_items=3, max_length=10), Field("tag", max_length=8))


@pytest.fixture
def with_fields(monkeypatch):
    """The activity profile with typed fields on actions and control flows, a rule on decision nodes."""
    activity = PROFILES["activity"]

    def edge_problem(diagram, edge):
        if edge.properties.get("tag") == "forbid":
            return "tag 'forbid' is not allowed"
        return activity.edge_problem(diagram, edge)

    def node_problem(diagram, node):
        parent = diagram.nodes.get(node.parent) if node.parent else None
        if node.kind == "decision_node" and parent is not None and parent.name == "Locked":
            return "decisions do not go into Locked"
        return None

    patched = dataclasses.replace(
        activity,
        node_fields={"opaque_action": FIELDS},
        edge_fields={"control_flow": FIELDS[1:]},
        edge_problem=edge_problem,
        node_problem=node_problem,
    )
    monkeypatch.setitem(PROFILES, "activity", patched)
    return patched


def activity() -> Diagram:
    d = TEMPLATES["order-process"]("x", "X")
    return d


# ---- the registry --------------------------------------------------------------------------------------------


def test_only_the_graph_types_with_a_fixed_vocabulary_have_a_profile():
    assert set(PROFILES) == {"activity", "state_machine", "class", "use_case", "component"}
    assert profile_of("deployment") is None and profile_of("sequence") is None
    assert graph_types.guard_types() == ("activity", "state_machine")
    assert not graph_types.edge_may_be_undirected("activity")
    assert graph_types.edge_may_be_undirected("deployment") and graph_types.edge_may_be_undirected("sequence")


def test_profile_kinds_match_the_catalogue_of_the_type():
    from arcviz.model import kinds_for

    for diagram_type, profile in PROFILES.items():
        assert profile.kinds is kinds_for(diagram_type)
        assert profile.default_node_kind in profile.kinds.all_kinds
        assert profile.default_edge_kind in profile.kinds.edge_kinds


# ---- clean_fields --------------------------------------------------------------------------------------------


def test_clean_fields_text_and_lists():
    assert clean_fields("x", FIELDS, {"tag": " a  b ", "note_lines": [" one", "", "two "]}) == {
        "tag": "a  b",
        "note_lines": "one\ntwo",
    }
    assert clean_fields("x", FIELDS, {"tag": "", "note_lines": []}) == {"tag": None, "note_lines": None}
    assert clean_fields("x", FIELDS, None) == {}


@pytest.mark.parametrize(
    "raw, message",
    [
        ("tag", "must be an object"),
        ({"nope": "x"}, "have no field 'nope'"),
        ({"tag": 3}, "must be a string"),
        ({"note_lines": "a"}, "must be a list of strings"),
        ({"note_lines": [1]}, "must be a list of strings"),
        ({"note_lines": ["a", "b", "c", "d"]}, "at most 3 lines"),
        ({"tag": "x" * 9}, "at most 8 characters"),
        ({"note_lines": ["x" * 11]}, "at most 10 characters"),
    ],
)
def test_clean_fields_errors(raw, message):
    with pytest.raises(FieldError, match=message):
        clean_fields("«x» nodes", FIELDS, raw)


def test_a_line_break_in_a_value_becomes_a_space():
    assert clean_fields("x", FIELDS, {"tag": "a\nb"}) == {"tag": "a b"}
    assert clean_fields("x", FIELDS, {"note_lines": ["a\nb"]}) == {"note_lines": "a b"}


# ---- operations: the fields argument -------------------------------------------------------------------------


def test_fields_are_stored_as_properties(with_fields):
    d = activity()
    result = apply(
        d, "add_node", {"name": "Prüfen", "kind": "opaque_action", "fields": {"tag": "T", "note_lines": ["a", "b"]}}
    )
    node = d.nodes[result["touched"][0]]
    assert node.properties == {"tag": "T", "note_lines": "a\nb"}
    apply(d, "update_node", {"id": node.id, "fields": {"tag": "", "note_lines": ["c"]}})
    assert node.properties == {"note_lines": "c"}


def test_edge_fields_and_guard_side_by_side(with_fields):
    d = activity()
    a = apply(d, "add_node", {"name": "A1"})["touched"][0]
    b = apply(d, "add_node", {"name": "B1"})["touched"][0]
    apply(d, "add_edge", {"source": a, "target": b, "guard": "ok", "fields": {"tag": "E"}})
    edge = d.edges[-1]
    assert edge.properties == {"guard": "ok", "tag": "E"}
    apply(d, "update_edge", {"id": edge.id, "fields": {"tag": ""}})
    assert d.edges[-1].properties == {"guard": "ok"}


def test_fields_refused_where_the_kind_has_none(with_fields):
    d = activity()
    with pytest.raises(OperationError, match="have no field 'tag'"):
        apply(d, "add_node", {"name": "Wahl", "kind": "decision_node", "fields": {"tag": "x"}})


@pytest.mark.parametrize("diagram_type", ["deployment", "sequence"])
def test_types_without_a_profile_have_no_fields(diagram_type):
    d = TEMPLATES["demo" if diagram_type == "deployment" else "checkout"]("x", "X")
    op = "add_node" if diagram_type == "deployment" else "add_lifeline"
    args = {"name": "N", "fields": {"a": "b"}}
    with pytest.raises(OperationError):
        apply(d, op, args)


def test_reserved_keys_are_not_free_properties(with_fields):
    d = activity()
    node = next(n for n in d.nodes.values() if n.kind == "opaque_action")
    with pytest.raises(OperationError, match=r"use fields\.tag"):
        apply(d, "update_node", {"id": node.id, "properties": {"tag": "x"}})
    with pytest.raises(OperationError, match=r"use fields\.note_lines"):
        apply(d, "update_node", {"id": node.id, "remove_properties": ["note_lines"]})
    with pytest.raises(OperationError, match=r"use fields\.tag"):
        apply(d, "add_node", {"name": "N", "properties": {"tag": "x"}})
    # a key that no field of the type owns stays free
    apply(d, "update_node", {"id": node.id, "properties": {"owner": "x"}})
    assert d.nodes[node.id].properties["owner"] == "x"


def test_a_kind_change_drops_the_fields_the_new_kind_lacks(with_fields):
    d = activity()
    result = apply(d, "add_node", {"name": "P", "kind": "opaque_action", "fields": {"tag": "T"}})
    node = d.nodes[result["touched"][0]]
    apply(d, "update_node", {"id": node.id, "kind": "merge_node"})
    assert "tag" not in node.properties


def test_a_rejected_call_changes_nothing(with_fields):
    d = activity()
    before = d.model_dump()
    with pytest.raises(OperationError):
        apply(d, "add_node", {"name": "P", "kind": "opaque_action", "fields": {"tag": "too long for it"}})
    assert d.model_dump() == before


# ---- profile rules reach operations --------------------------------------------------------------------------


def test_edge_problem_applies_to_add_and_update(with_fields):
    d = activity()
    a = apply(d, "add_node", {"name": "A1"})["touched"][0]
    b = apply(d, "add_node", {"name": "B1"})["touched"][0]
    with pytest.raises(OperationError, match="not allowed"):
        apply(d, "add_edge", {"source": a, "target": b, "fields": {"tag": "forbid"}})
    apply(d, "add_edge", {"source": a, "target": b})
    edge_id = d.edges[-1].id
    with pytest.raises(OperationError, match="Cannot connect"):
        apply(d, "update_edge", {"id": edge_id, "fields": {"tag": "forbid"}})


def test_node_problem_applies_to_add_and_move(with_fields):
    d = activity()
    apply(d, "add_node", {"name": "Locked", "kind": "activity_partition"})
    other = apply(d, "add_node", {"name": "Free", "kind": "activity_partition"})["touched"][0]
    with pytest.raises(OperationError, match="decisions do not go into Locked"):
        apply(d, "add_node", {"name": "D", "kind": "decision_node", "parent": "locked"})
    decision = apply(d, "add_node", {"name": "D", "kind": "decision_node", "parent": other})["touched"][0]
    with pytest.raises(OperationError, match="decisions do not go into Locked"):
        apply(d, "move_node", {"id": decision, "parent": "locked"})


def test_activity_rules_keep_their_messages():
    d = activity()
    start = next(n.id for n in d.nodes.values() if n.kind == "initial_node")
    action = next(n.id for n in d.nodes.values() if n.kind == "opaque_action")
    with pytest.raises(OperationError, match="Cannot connect .* an InitialNode has no incoming edges"):
        apply(d, "add_edge", {"source": action, "target": start})
    with pytest.raises(OperationError, match="ActivityEdges are always directed"):
        apply(d, "add_edge", {"source": start, "target": action, "direction": "both"})
    with pytest.raises(
        OperationError, match="guard is only defined for edges of activity and state_machine diagrams, not deployment"
    ):
        apply(TEMPLATES["demo"]("x", "X"), "add_edge", {"source": "service-a", "target": "service-b", "guard": "g"})


# ---- the importer --------------------------------------------------------------------------------------------


def test_stored_fields_are_checked_on_import(with_fields):
    d = activity()
    node = next(n for n in d.nodes.values() if n.kind == "opaque_action")
    node.properties["note_lines"] = "ok\n\nbad"
    node.properties["tag"] = "far too long for the field"
    errors = field_errors(d, PROFILES["activity"])
    assert any("empty or not plain" in e for e in errors) and any("longer than 8" in e for e in errors)
    with pytest.raises(exchange.ExchangeError):
        exchange.parse_document(exchange.export_document(d))


def test_a_clean_document_round_trips(with_fields):
    d = activity()
    node = next(n for n in d.nodes.values() if n.kind == "opaque_action")
    apply(d, "update_node", {"id": node.id, "fields": {"note_lines": ["a", "b"], "tag": "t"}})
    again = exchange.parse_document(exchange.export_document(d))
    assert again.nodes[node.id].properties["note_lines"] == "a\nb"


def test_candidate_elements_do_not_leak_into_the_diagram(with_fields):
    d = activity()
    ids = set(d.nodes)
    edges = len(d.edges)
    with pytest.raises(OperationError):
        apply(d, "add_node", {"name": "D", "kind": "decision_node", "parent": "nope"})
    assert set(d.nodes) == ids and len(d.edges) == edges
    assert isinstance(Node(id="a", name="a"), Node) and isinstance(Edge(id="e", source="a", target="b"), Edge)


# ---- /api/config ---------------------------------------------------------------------------------------------


def test_config_lists_typed_fields(tmp_path, with_fields):
    client = TestClient(create_app(store=FileStore(tmp_path)))
    config = client.get("/api/config").json()["diagram_types"]
    assert config["activity"]["node_fields"] == {
        "opaque_action": [
            {"name": "note_lines", "list": True, "flag": False, "max_items": 3, "max_length": 10},
            {"name": "tag", "list": False, "flag": False, "max_items": 1, "max_length": 8},
        ]
    }
    assert config["activity"]["edge_fields"]["control_flow"][0]["name"] == "tag"
    assert config["deployment"]["node_fields"] == {} and config["sequence"]["edge_fields"] == {}


def test_every_operation_that_takes_fields_is_a_graph_operation():
    for op in ("add_node", "update_node", "add_edge", "update_edge"):
        assert "fields" in operations._parameters(operations.OPERATIONS[op])[0]
