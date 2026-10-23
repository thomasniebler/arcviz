"""One link take-back and one bounded id-attempt helper for both agents and both services (#195).

The link take-back after a kind race (#155, ADR-0128) and the bounded create loop over `ports.new_ids` (ADR-0131) were
copied between the diagram and the project agent / service, and the copies drifted: the project agent ignored a
`forbidden` take-back. These tests pin the shared behaviour for both copies."""

from __future__ import annotations

import json

import pytest

from arcviz import ports
from arcviz.agent import ROLE_LOST, ROLE_LOST_TURN
from arcviz.llm.base import ModelReply
from arcviz.model import Diagram
from arcviz.ports import StoreError
from arcviz.services import DiagramService, LiveWrite, ModelService, ProjectService
from tests.conftest import ScriptedProvider, call
from tests.test_kind_link_race import partition_of
from tests.test_server_model import nodes

# ---- link take-back: the same outcome handling in both agents ----------------------------------------------------


def _world(store, monkeypatch, then_add: bool = True):
    """Project "shop" with the activity diagram "order" and the element "order-service"; the model links a partition
    to it while its kind changes to "data" (the link must be taken back), then adds a node."""
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    replies = [
        ModelReply(
            text="",
            tool_calls=[call("link_element", "c1", id="PARTITION", element="order-service")],
            stop_reason="tool_use",
        )
    ]
    if then_add:
        replies.append(
            ModelReply(
                text="", tool_calls=[call("add_node", "c2", name="Prüfen", kind="action")], stop_reason="tool_use"
            )
        )
    replies.append(ModelReply(text="fertig", tool_calls=[], stop_reason="end_turn"))
    provider = ScriptedProvider(replies)
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    client.post(
        "/api/diagrams", json={"title": "Bestellprozess", "id": "order", "template": "order-process", "project": "shop"}
    )
    client.post("/api/projects/shop/elements", json={"name": "Order Service"})
    partition = partition_of(client)
    provider.replies[0].tool_calls[0].arguments["id"] = partition
    real_link_args = ModelService.link_args

    def kind_change_after_reading(self, project_id, view, element_ref):
        args = real_link_args(self, project_id, view, element_ref)
        ProjectService(store, store).apply_all(
            "shop",
            [("update_element", {"element": "order-service", "name": None, "description": None, "kind": "data"})],
            internal=True,
        )
        return args

    monkeypatch.setattr(ModelService, "link_args", kind_change_after_reading)
    return client, partition


def _error(result: dict) -> str:
    return json.loads(result["content"])["error"]


def _turn(client, scope: str) -> list[dict]:
    if scope == "diagram":
        response = client.post("/api/diagrams/order/chat", json={"message": "verknüpfen"})
    else:
        response = client.post("/api/projects/shop/chat", json={"message": "verknüpfen", "active": "order"})
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _take_back_outcome(monkeypatch, outcome: str) -> list[str]:
    """The take-back write (the second write-through of the turn) ends with `outcome`; returns the calls' ids."""
    real_write_through = DiagramService.write_through
    calls: list[str] = []

    def patched(self, diagram_id, diagram, revision, change, failed):
        calls.append(diagram_id)
        if len(calls) == 2:
            return LiveWrite(outcome, diagram, revision, None)
        return real_write_through(self, diagram_id, diagram, revision, change, failed)

    monkeypatch.setattr(DiagramService, "write_through", patched)
    return calls


@pytest.mark.parametrize("scope", ["diagram", "project"])
def test_a_forbidden_take_back_ends_the_turns_writes_in_both_agents(store, monkeypatch, scope):
    """The drift of #195: a take-back refused because the caller lost editor keeps the link (recorded, reported) and
    blocks every later change of the turn, which ends with the role-lost error, in the project agent too."""
    client, partition = _world(store, monkeypatch)
    calls = _take_back_outcome(monkeypatch, "forbidden")
    events = _turn(client, scope)
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["is_error"] and "could not be taken back" in results[0]["content"]
    assert results[1]["is_error"] and _error(results[1]) == ROLE_LOST  # refused before any write
    assert len(calls) == 2  # the add_node never reached write_through
    final = events[-1]
    assert final["type"] == "final" and final["error"] == ROLE_LOST_TURN
    assert [op["op"] for op in final["operations"]] == ["link_element"]  # the link stays stored: recorded
    assert nodes(client, "order")[partition]["ref"] == "order-service"
    assert not any(n["name"] == "Prüfen" for n in nodes(client, "order").values())


@pytest.mark.parametrize("scope", ["diagram", "project"])
def test_a_forbidden_write_ends_the_turns_writes_in_both_agents(store, monkeypatch, scope):
    """A write refused because the caller lost editor blocks the turn's later writes in both agents (the project
    agent used to try every later call again)."""
    client, _ = _world(store, monkeypatch)
    real_write_through = DiagramService.write_through
    calls: list[str] = []

    def forbidden_first(self, diagram_id, diagram, revision, change, failed):
        calls.append(diagram_id)
        if len(calls) == 1:
            return LiveWrite("forbidden", diagram, revision, None)
        return real_write_through(self, diagram_id, diagram, revision, change, failed)

    monkeypatch.setattr(DiagramService, "write_through", forbidden_first)
    events = _turn(client, scope)
    results = [e for e in events if e["type"] == "tool_result"]
    assert [_error(r) for r in results] == [ROLE_LOST, ROLE_LOST]
    assert calls == ["order"]
    assert events[-1]["error"] == ROLE_LOST_TURN and events[-1]["operations"] == []


@pytest.mark.parametrize("scope", ["diagram", "project"])
@pytest.mark.parametrize("outcome", ["written", "rejected"])
def test_a_link_taken_back_is_neither_recorded_nor_kept_in_both_agents(store, monkeypatch, scope, outcome):
    client, partition = _world(store, monkeypatch, then_add=False)
    if outcome == "rejected":
        _take_back_outcome(monkeypatch, "rejected")  # nothing left to unlink
    events = _turn(client, scope)
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["is_error"] and "could not be taken back" not in results[0]["content"]
    assert events[-1]["operations"] == [] and not events[-1]["error"]
    if outcome == "written":
        assert "ref" not in nodes(client, "order")[partition]


@pytest.mark.parametrize("scope", ["diagram", "project"])
@pytest.mark.parametrize("outcome", ["busy", "deleted"])
def test_a_link_that_stays_is_recorded_in_both_agents(store, monkeypatch, scope, outcome):
    client, _ = _world(store, monkeypatch)
    _take_back_outcome(monkeypatch, outcome)
    events = _turn(client, scope)
    results = [e for e in events if e["type"] == "tool_result"]
    assert "could not be taken back" in results[0]["content"]
    assert [op["op"] for op in events[-1]["operations"]][:1] == ["link_element"]
    if outcome == "deleted":  # the turn treats the diagram as gone: the next change is refused, nothing is written
        assert results[1]["is_error"] and "deleted" in results[1]["content"]


# ---- one bounded id-attempt helper ---------------------------------------------------------------------------


def test_id_attempts_yields_at_most_create_attempts_candidates_then_fails(monkeypatch):
    monkeypatch.setattr(ports, "new_ids", lambda base: iter(lambda: base, None))  # always the same (taken) id
    tried = []
    with pytest.raises(StoreError, match=f"No free id for a new widget after {ports.CREATE_ATTEMPTS} attempts"):
        for candidate in ports.id_attempts("w", "widget"):
            tried.append(candidate)
    assert tried == ["w"] * ports.CREATE_ATTEMPTS


def test_id_attempts_reads_the_generator_at_call_time(monkeypatch):
    """`ports.new_ids` stays the one seam the tests and the e2e server pin (readable ids)."""
    monkeypatch.setattr(ports, "new_ids", lambda base: iter([f"{base}-x", f"{base}-y"]))
    attempts = ports.id_attempts("a", "diagram")
    assert [next(attempts), next(attempts)] == ["a-x", "a-y"]
    with pytest.raises(StoreError, match="No free id for a new diagram"):  # a generator that ends: nothing free
        next(attempts)


def test_both_services_give_up_after_the_shared_number_of_attempts(store, monkeypatch):
    from arcviz.templates import demo_diagram

    store.create("Taken", "taken", demo_diagram("taken", "Taken"))
    ProjectService(store, store).create("Taken", "taken")
    monkeypatch.setattr(ports, "new_ids", lambda base: iter(lambda: "taken", None))
    diagrams = DiagramService(store, ProjectService(store, store))
    with pytest.raises(StoreError, match=f"No free id for a new diagram after {ports.CREATE_ATTEMPTS} attempts"):
        diagrams.create("Neu", "taken", Diagram(id="taken", title="Neu"))
    with pytest.raises(StoreError, match=f"No free id for a new project after {ports.CREATE_ATTEMPTS} attempts"):
        ProjectService(store, store).create("Neu", "taken")


def test_the_attempt_bound_is_defined_once():
    import arcviz.services.diagrams as diagrams
    import arcviz.services.projects as projects

    assert not hasattr(diagrams, "CREATE_ATTEMPTS") and not hasattr(projects, "CREATE_ATTEMPTS")


def test_a_forbidden_write_to_one_diagram_does_not_block_another_in_a_project_turn(store, monkeypatch):
    """Coordinator review of #199: a refusal is about the diagram written (it may have left for a project the caller
    only views); the project turn keeps editing the others it may still edit."""
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("add_node", "c1", name="A", kind="service")], stop_reason="tool_use"),
            ModelReply(text="", tool_calls=[call("open_diagram", "c2", diagram="plat")], stop_reason="tool_use"),
            ModelReply(text="", tool_calls=[call("add_node", "c3", name="B", kind="service")], stop_reason="tool_use"),
            ModelReply(text="fertig", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    for title, diagram_id in (("Order", "order"), ("Plattform", "plat")):
        client.post("/api/diagrams", json={"title": title, "id": diagram_id, "template": "empty", "project": "shop"})
    real_write_through = DiagramService.write_through

    def forbidden_for_order(self, diagram_id, diagram, revision, change, failed):
        if diagram_id == "order":
            return LiveWrite("forbidden", diagram, revision, None)
        return real_write_through(self, diagram_id, diagram, revision, change, failed)

    monkeypatch.setattr(DiagramService, "write_through", forbidden_for_order)
    response = client.post("/api/projects/shop/chat", json={"message": "baue", "active": "order"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    results = [e for e in events if e["type"] == "tool_result"]
    assert _error(results[0]) == ROLE_LOST
    assert not results[1]["is_error"] and not results[2]["is_error"]
    assert any(n["name"] == "B" for n in nodes(client, "plat").values())
    assert events[-1]["error"] == ROLE_LOST_TURN  # the refused write is still reported
