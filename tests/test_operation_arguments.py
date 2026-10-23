"""Argument validation before any mutation, and full rollback on every exception (issue #66).

Spec: docs/architecture/specs/operation-arguments.md, ADR-0045.
"""

from __future__ import annotations

import inspect
import json
import typing

import pytest
from fastapi.testclient import TestClient

from arcviz import operations as ops
from arcviz.agent import DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.model import Diagram
from arcviz.server import create_app
from arcviz.services import DiagramService
from arcviz.templates import TEMPLATES, demo_diagram
from tests.conftest import ScriptedProvider, call

# One start diagram per type, with elements so that references resolve.
STARTS = {
    "deployment": "demo",
    "activity": "order-process",
    "sequence": "checkout",
    "state_machine": "order-lifecycle",
    "class": "webshop-domain",
    "use_case": "webshop-use-cases",
    "component": "webshop-components",
}


def start(diagram_type: str) -> Diagram:
    return TEMPLATES[STARTS[diagram_type]]("t", "T")


def dump(d: Diagram) -> dict:
    return d.model_dump(mode="json")  # includes updated_at: a rejected call must not even touch the timestamp


def rejected(d: Diagram, op: str, args, match: str) -> None:
    before = dump(d)
    with pytest.raises(ops.OperationError, match=match):
        ops.apply(d, op, args)
    assert dump(d) == before


def test_the_issue_example_mixed_valid_name_and_invalid_kind_changes_nothing():
    d = demo_diagram()
    rejected(d, "update_node", {"id": "idp", "name": "changed", "kind": 42}, "Invalid arguments for update_node")
    assert d.nodes["idp"].name != "changed"


@pytest.mark.parametrize(
    "op, args, match",
    [
        ("add_node", {"name": 42}, r"'name' .*must be a string, got a number"),
        ("add_node", {"name": None}, r"'name' .*must be a string, got null"),
        ("add_node", {"kind": "service"}, r"missing required argument 'name'"),
        ("add_node", {"name": "x", "bogus": 1}, r"unknown argument 'bogus'.*Allowed: name, kind, id, parent"),
        ("add_node", {"name": "x", "kind": "nope"}, r"Unknown node kind 'nope'"),  # invalid enum value
        ("add_node", {"name": "x", "properties": ["a", "b"]}, r"'properties' .*object.*got an array"),
        ("add_node", {"name": "x", "properties": "cidr=1"}, r"'properties' .*object.*got a string"),
        ("add_node", {"name": "x", "properties": {"a": {"b": 1}}}, r"'properties' .*got an object"),
        ("update_node", {"id": "idp", "properties": {"a": None}}, r"'properties'"),
        ("update_node", {"id": "idp", "remove_properties": "a"}, r"'remove_properties' .*list of strings"),
        ("update_node", {"id": "idp", "remove_properties": [1]}, r"'remove_properties'"),
        ("update_node", {"id": ["idp"]}, r"'id' .*must be a string, got an array"),
        ("remove_node", {"id": "idp", "cascade": "false"}, r"'cascade' .*must be true or false, got a string"),
        ("move_node", {"id": "idp"}, r"missing required argument 'parent'"),
        ("move_node", {"id": "idp", "parent": 7}, r"'parent' .*a string or null"),
        ("add_edge", {"source": "idp", "target": {"id": "x"}}, r"'target'"),
        ("update_edge", {"id": "e1", "label": 5}, r"'label'"),
        ("remove_edge", {"id": 1}, r"'id'"),
        ("rename_diagram", {"title": True}, r"'title' .*got a boolean"),
        ("set_direction", {"direction": 1.5}, r"'direction'"),
        ("apply_batch", {"operations": {"op": "add_node"}}, r"'operations' .*must be a list"),
        ("apply_batch", {"operations": [{"op": "add_node", "args": {"name": 1}}]}, r"Batch step 1 \(add_node\)"),
        ("add_node", "name=x", r"Invalid arguments for add_node: expected an object"),
        ("add_node", None, r"Invalid arguments for add_node: expected an object"),
    ],
)
def test_ill_typed_arguments_are_controlled_errors_and_change_nothing(op, args, match):
    d = demo_diagram()
    rejected(d, op, args, match)


def test_property_values_may_be_scalars_and_are_stored_as_strings():
    d = demo_diagram()
    ops.apply(d, "update_node", {"id": "idp", "properties": {"port": 443, "tls": True, "ratio": 0.5}})
    assert d.nodes["idp"].properties == {"port": "443", "tls": "True", "ratio": "0.5"}


def _string_parameters() -> list[tuple[str, str, str]]:
    """(type, op, parameter) for every string-typed parameter of every operation of every type."""
    out = []
    for diagram_type, names in ops.TYPE_OPERATIONS.items():
        for name in names:
            hints = typing.get_type_hints(ops.OPERATIONS[name])
            for param in list(inspect.signature(ops.OPERATIONS[name]).parameters)[1:]:
                is_list = typing.get_origin(hints[param]) is list  # list[str] (set_completeness) is no string
                if not is_list and (str in typing.get_args(hints[param]) or hints[param] is str):
                    out.append((diagram_type, name, param))
    return out


@pytest.mark.parametrize("diagram_type, op, param", _string_parameters())
@pytest.mark.parametrize("junk", [42, ["x"], {"x": 1}], ids=["number", "array", "object"])
def test_every_string_parameter_of_every_operation_rejects_other_types(diagram_type, op, param, junk):
    """Covers the per-type operations (activity, sequence) as well as the shared ones."""
    d = start(diagram_type)
    rejected(d, op, {param: junk}, f"Invalid arguments for {op}: .*'{param}'")


def test_an_unexpected_exception_rolls_back_and_is_not_disguised(monkeypatch):
    """Internal defects are not input errors: the exception propagates, but the diagram is restored."""

    def broken(diagram: Diagram, name: str) -> None:
        diagram.title = "half-done"
        diagram.nodes.clear()
        raise RuntimeError("defect")

    monkeypatch.setitem(ops.OPERATIONS, "rename_diagram", broken)
    d = demo_diagram()
    before = dump(d)
    with pytest.raises(RuntimeError, match="defect"):
        ops.apply(d, "rename_diagram", {"name": "x"})
    assert dump(d) == before


# ---- REST ----------------------------------------------------------------------------


@pytest.fixture
def client(store, demo):
    return TestClient(create_app(store=store, provider=ScriptedProvider([])))


def test_rest_rejects_ill_typed_arguments_with_400_and_writes_nothing(client, store):
    before = store.load("demo")
    for args in ({"name": 42}, {"name": "x", "properties": ["a"]}):
        r = client.post("/api/diagrams/demo/ops", json={"op": "add_node", "args": args})
        assert r.status_code == 400 and r.json()["detail"].startswith("Invalid arguments for add_node: ")
    r = client.post(
        "/api/diagrams/demo/ops", json={"op": "update_node", "args": {"id": "idp", "name": "changed", "kind": 42}}
    )
    assert r.status_code == 400
    assert store.load("demo") == before and len(store.list_versions("demo")) == 1


# ---- agent ---------------------------------------------------------------------------


def test_agent_returns_an_error_tool_result_and_persists_no_partial_change(store, demo):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("update_node", "c1", id="idp", name="changed", kind=42)],
                stop_reason="tool_use",
            ),
            ModelReply(text="Konnte die Art nicht setzen.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    agent = DiagramAgent(provider, DiagramService(store), background=False)
    events = list(agent.chat("demo", "Ändere idp"))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and "Invalid arguments for update_node" in json.loads(result["content"])["error"]
    final = events[-1]
    assert final["type"] == "final" and final["error"] is None and final["version"] is None
    assert store.load("demo").nodes["idp"].name == demo.nodes["idp"].name
    # the model saw the error and could correct itself
    assert "Invalid arguments" in json.dumps(provider.requests[1]["messages"][-1])


def test_agent_reports_an_internal_defect_as_such_and_finishes_the_turn(store, demo, monkeypatch):
    def broken(diagram: Diagram, title: str) -> None:
        diagram.title = "half-done"
        raise RuntimeError("defect")

    monkeypatch.setitem(ops.OPERATIONS, "rename_diagram", broken)
    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("rename_diagram", "c1", title="X")], stop_reason="tool_use"),
            ModelReply(text="Fehler.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    events = list(DiagramAgent(provider, DiagramService(store), background=False).chat("demo", "Umbenennen"))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] and "Internal error" in json.loads(result["content"])["error"]
    assert events[-1]["type"] == "final"
    assert store.load("demo").title == demo.title and len(store.list_versions("demo")) == 1
