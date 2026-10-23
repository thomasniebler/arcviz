"""Layout direction (spec: docs/architecture/specs/layout-direction.md)."""

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import mermaid
from arcviz import operations as ops
from arcviz.agent import TOOLS, DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.model import DIRECTIONS, Diagram
from arcviz.server import create_app
from arcviz.services import DiagramService
from arcviz.store.base import fingerprint
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider, call


def test_directions_and_default():
    assert DIRECTIONS == ("TB", "BT", "LR", "RL")
    assert Diagram(id="x", title="x").direction == "TB"
    # documents written before the field existed still validate
    old = demo_diagram().model_dump(mode="json")
    old.pop("direction")
    assert Diagram.model_validate(old).direction == "TB"


@pytest.mark.parametrize(("given", "stored"), [("LR", "LR"), ("rl", "RL"), (" bt ", "BT"), ("TD", "TB"), ("tb", "TB")])
def test_set_direction_normalises(given, stored):
    d = demo_diagram()
    result = ops.apply(d, "set_direction", {"direction": given})
    assert d.direction == stored
    assert result["result"] == {"direction": stored}
    assert result["touched"] == []


def test_set_direction_rejects_unknown_values_without_change():
    d = demo_diagram()
    ops.apply(d, "set_direction", {"direction": "LR"})
    with pytest.raises(ops.OperationError, match="TB, BT, LR, RL"):
        ops.apply(d, "set_direction", {"direction": "diagonal"})
    assert d.direction == "LR"


def test_rollback_restores_every_model_field():
    d = demo_diagram()
    ops.apply(d, "set_direction", {"direction": "LR"})
    before = d.model_dump()
    with pytest.raises(ops.OperationError):
        ops.apply(d, "add_node", {"name": "x", "kind": "service", "parent": "service-a"})
    assert d.model_dump() == before


def test_render_uses_the_stored_direction_for_root_and_subgraphs():
    d = demo_diagram()
    ops.set_direction(d, "LR")
    lines = [line.strip() for line in mermaid.render(d).splitlines()]
    assert lines[0] == "flowchart LR"
    assert "direction LR" in lines and "direction TB" not in lines
    assert mermaid.render(d, direction="BT").startswith("flowchart BT\n")  # explicit argument wins


def test_summary_mentions_only_a_non_default_direction():
    d = demo_diagram()
    assert "Layout direction" not in d.summary()
    ops.set_direction(d, "LR")
    assert d.summary().splitlines()[1] == "Layout direction: LR"


def test_fingerprint_ignores_the_default_direction():
    d = demo_diagram()
    payload = d.model_dump(
        mode="json", exclude={"created_at", "updated_at", "direction", "type", "interaction"}
    )  # as stored before both fields
    import hashlib

    legacy = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    assert fingerprint(d) == legacy
    ops.set_direction(d, "LR")
    assert fingerprint(d) != legacy


def test_agent_tool_sets_the_direction(store, demo):
    spec = next(t for t in TOOLS if t.name == "set_direction")
    assert spec.parameters["properties"]["direction"]["enum"] == list(DIRECTIONS)
    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("set_direction", direction="LR")], stop_reason="tool_use"),
            ModelReply(text="Done.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    events = list(
        DiagramAgent(provider, DiagramService(store), background=False).chat("demo", "Bitte von links nach rechts")
    )
    final = events[-1]
    assert final["type"] == "final" and final["mermaid"].startswith("flowchart LR")
    assert store.load("demo").direction == "LR"
    assert store.list_versions("demo")[-1].source == "auto"


def test_server_config_and_ops(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    assert client.get("/api/config").json()["directions"] == ["TB", "BT", "LR", "RL"]
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    r = client.post("/api/diagrams/demo/ops", json={"op": "set_direction", "args": {"direction": "LR"}})
    assert r.status_code == 200
    body = r.json()
    assert body["diagram"]["direction"] == "LR" and body["mermaid"].startswith("flowchart LR")
    assert client.get("/api/diagrams/demo/versions/1").json()["mermaid"].startswith("flowchart TB")
    r = client.post("/api/diagrams/demo/ops", json={"op": "set_direction", "args": {"direction": "up"}})
    assert r.status_code == 400
