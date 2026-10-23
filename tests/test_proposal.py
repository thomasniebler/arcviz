"""Propose-then-confirm mode (spec: docs/architecture/specs/propose-confirm.md)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import operations
from arcviz.agent import DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.proposal import ProposalConflict, apply_proposal, diagram_diff, discard_proposal
from arcviz.server import create_app
from arcviz.services import DiagramService
from arcviz.store import fingerprint
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider, call

# ---- diff ---------------------------------------------------------------------------------------


def test_diff_lists_added_removed_and_changed_elements():
    before = demo_diagram("demo", "Demo")
    after = before.model_copy(deep=True)
    operations.apply(after, "add_node", {"name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"})
    operations.apply(after, "move_node", {"id": "service-a", "parent": "prod-vnet"})
    operations.apply(after, "update_node", {"id": "service-b", "properties": {"runtime": "java"}})
    removed = next(
        n
        for n in before.nodes.values()
        if not any(m.parent == n.id for m in before.nodes.values()) and n.id != "service-a"
    )
    operations.apply(after, "remove_node", {"id": removed.id})
    operations.apply(after, "rename_diagram", {"title": "Neu"})
    edge = operations.apply(after, "add_edge", {"source": "service-a", "target": "service-b", "label": "grpc"})[
        "result"
    ]

    d = diagram_diff(before, after)
    assert d["title"] == ["Demo", "Neu"]
    assert d["nodes"]["added"] == [
        {"id": "prod-vnet", "name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"}
    ]
    assert [n["id"] for n in d["nodes"]["removed"]] == [removed.id]
    changed = {c["id"]: c["changes"] for c in d["nodes"]["changed"]}
    assert changed["service-a"] == {"parent": ["prod-deployment", "prod-vnet"]}
    assert changed["service-b"]["properties.runtime"][1] == "java"
    assert {
        "id": edge["id"],
        "source": "service-a",
        "target": "service-b",
        "label": "grpc",
        "kind": "connection",
        "direction": None,
        "properties": {},
    } in d["edges"]["added"]
    assert all(e["source"] == removed.id or e["target"] == removed.id for e in d["edges"]["removed"])


def test_diff_of_identical_diagrams_is_empty():
    d = demo_diagram("demo", "Demo")
    diff = diagram_diff(d, d.model_copy(deep=True))
    assert diff == {
        "title": None,
        "direction": None,
        "nodes": {"added": [], "removed": [], "changed": []},
        "edges": {"added": [], "removed": [], "changed": []},
    }


def test_diff_reports_edge_changes():
    before = demo_diagram("demo", "Demo")
    edge = before.edges[0]
    after = before.model_copy(deep=True)
    operations.apply(after, "update_edge", {"id": edge.id, "label": "changed-label"})
    assert diagram_diff(before, after)["edges"]["changed"] == [
        {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "changes": {"label": [edge.label, "changed-label"]},
        }
    ]


# ---- propose turn --------------------------------------------------------------------------------


def vnet_script() -> list[ModelReply]:
    return [
        ModelReply(
            text="",
            tool_calls=[call("add_node", "c1", name="prod-vnet", kind="vnet", parent="prod-deployment")],
            stop_reason="tool_use",
        ),
        ModelReply(
            text="",
            tool_calls=[
                call("move_node", "c2", id="service-a", parent="prod-vnet"),
                call("get_diagram", "c3", focus="prod-vnet"),
            ],
            stop_reason="tool_use",
        ),
        ModelReply(text="Vorschlag: VNet mit service-a.", tool_calls=[], stop_reason="end_turn"),
    ]


def test_propose_turn_persists_nothing_but_the_transcript(store, demo):
    base = fingerprint(store.load("demo"))
    provider = ScriptedProvider(vnet_script())
    events = list(DiagramAgent(provider, DiagramService(store)).chat("demo", "VNet bitte", propose=True))

    get_result = [e for e in events if e["type"] == "tool_result" and e["name"] == "get_diagram"][0]
    assert "service-a" in get_result["content"]  # the dry run sees its own changes
    final = events[-1]
    assert final["version"] is None and final["error"] is None
    assert final["diagram"]["nodes"]["service-a"]["parent"] == "prod-vnet"
    assert "class n_prod_vnet,n_service_a changed" in final["mermaid"]
    proposal = final["proposal"]
    assert proposal["base"] == base
    assert proposal["operations"] == [
        {"op": "add_node", "arguments": {"name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"}},
        {"op": "move_node", "arguments": {"id": "service-a", "parent": "prod-vnet"}},
    ]
    assert [n["id"] for n in proposal["diff"]["nodes"]["added"]] == ["prod-vnet"]

    # nothing persisted: latest, versions unchanged
    assert fingerprint(store.load("demo")) == base and "prod-vnet" not in store.load("demo").nodes
    assert [v.version for v in store.list_versions("demo")] == [1]
    chat = store.load_chat("demo")
    assert chat[-1]["proposed"] is True and chat[-1]["version"] is None and len(chat[-1]["operations"]) == 2


def test_apply_mode_has_no_proposal(store, demo):
    events = list(DiagramAgent(ScriptedProvider(vnet_script()), DiagramService(store)).chat("demo", "VNet bitte"))
    assert events[-1]["proposal"] is None and events[-1]["version"]["version"] == 2


def test_propose_turn_without_changes_has_no_proposal(store, demo):
    provider = ScriptedProvider([ModelReply(text="Drei Services.", tool_calls=[], stop_reason="end_turn")])
    final = list(DiagramAgent(provider, DiagramService(store)).chat("demo", "Wie viele Services?", propose=True))[-1]
    assert final["proposal"] is None and "proposed" not in store.load_chat("demo")[-1]


def propose(store) -> dict:
    return list(
        DiagramAgent(ScriptedProvider(vnet_script()), DiagramService(store)).chat("demo", "VNet bitte", propose=True)
    )[-1]


# ---- confirm / discard ---------------------------------------------------------------------------


def test_apply_proposal_reproduces_the_preview_as_one_version(store, demo):
    final = propose(store)
    p = final["proposal"]
    diagram, version, changed = apply_proposal(
        DiagramService(store), "demo", p["operations"], p["base"], p["result"], label="VNet bitte"
    )
    assert fingerprint(diagram) == p["result"] == fingerprint(store.load("demo"))
    assert version.version == 2 and version.source == "auto" and version.label == "VNet bitte"
    assert changed == ["prod-vnet", "service-a"]
    note = store.load_chat("demo")[-1]
    assert note == {
        **note,
        "role": "assistant",
        "content": "Proposal applied as version v2.",
        "proposal_outcome": "applied",
        "version": 2,
    }


def test_stale_base_is_a_conflict_and_writes_nothing(store, demo):
    p = propose(store)["proposal"]
    d = store.load("demo")
    operations.apply(d, "rename_diagram", {"title": "Zwischendurch"})
    store.save_latest(d)
    chat_len = len(store.load_chat("demo"))
    with pytest.raises(ProposalConflict, match="changed since the proposal"):
        apply_proposal(DiagramService(store), "demo", p["operations"], p["base"], p["result"])
    assert "prod-vnet" not in store.load("demo").nodes and len(store.load_chat("demo")) == chat_len


def test_write_during_the_replay_is_detected_before_saving(store, demo, monkeypatch):
    p = propose(store)["proposal"]
    real_apply = operations.apply

    def apply_and_race(diagram, op, args):
        if op == "move_node":  # another request saves while the proposal is being replayed
            other = store.load("demo")
            real_apply(other, "rename_diagram", {"title": "Parallel"})
            store.save_latest(other)
        return real_apply(diagram, op, args)

    monkeypatch.setattr("arcviz.services.proposals.operations.apply", apply_and_race)
    with pytest.raises(ProposalConflict, match="changed since the proposal"):
        apply_proposal(DiagramService(store), "demo", p["operations"], p["base"], p["result"])
    saved = store.load("demo")
    assert saved.title == "Parallel" and "prod-vnet" not in saved.nodes  # the parallel write survives


def test_failing_replay_is_a_conflict(store, demo):
    p = propose(store)["proposal"]
    ops = [*p["operations"], {"op": "move_node", "arguments": {"id": "nope", "parent": None}}]
    with pytest.raises(ProposalConflict, match="nope"):
        apply_proposal(DiagramService(store), "demo", ops, p["base"], p["result"])
    assert [v.version for v in store.list_versions("demo")] == [1]


def test_result_mismatch_is_a_conflict(store, demo):
    p = propose(store)["proposal"]
    with pytest.raises(ProposalConflict, match="did not reproduce"):
        apply_proposal(DiagramService(store), "demo", p["operations"][:1], p["base"], p["result"])
    assert "prod-vnet" not in store.load("demo").nodes


def test_discard_writes_a_note_only(store, demo):
    propose(store)
    discard_proposal(DiagramService(store), "demo")
    assert store.load_chat("demo")[-1]["proposal_outcome"] == "discarded"
    assert [v.version for v in store.list_versions("demo")] == [1]


# ---- HTTP ---------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    app = create_app(store=store, provider=ScriptedProvider(vnet_script() + vnet_script()))
    c = TestClient(app)
    c.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    return c


def chat(client, mode: str) -> dict:
    r = client.post("/api/diagrams/demo/chat", json={"message": "VNet bitte", "mode": mode})
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")][-1]


def test_http_propose_apply_and_conflict(client):
    p = chat(client, "propose")["proposal"]
    assert client.get("/api/diagrams/demo").json()["versions"][-1]["version"] == 1

    r = client.post(
        "/api/diagrams/demo/proposal/apply", params={"focus": "prod-deployment"}, json={**p, "label": "VNet bitte"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["version"]["version"] == 2 and body["focus"] == "prod-deployment"
    assert "prod-vnet" in body["diagram"]["nodes"] and "class n_prod_vnet,n_service_a changed" in body["mermaid"]
    assert body["chat"][-1]["proposal_outcome"] == "applied"

    r = client.post("/api/diagrams/demo/proposal/apply", json=p)  # same proposal again: base is gone
    assert r.status_code == 409 and "changed since the proposal" in r.json()["detail"]
    assert client.post("/api/diagrams/nope/proposal/apply", json=p).status_code == 404


def test_http_discard(client):
    chat(client, "propose")
    r = client.post("/api/diagrams/demo/proposal/discard")
    assert r.status_code == 200 and r.json()["chat"][-1]["proposal_outcome"] == "discarded"
    assert "prod-vnet" not in r.json()["diagram"]["nodes"]


def test_http_unknown_mode_is_rejected(client):
    assert client.post("/api/diagrams/demo/chat", json={"message": "x", "mode": "maybe"}).status_code == 422


def test_diff_reports_edge_direction_and_property_changes():
    before = demo_diagram("demo", "Demo")
    edge = before.edges[0]
    after = before.model_copy(deep=True)
    operations.apply(after, "update_edge", {"id": edge.id, "direction": "both", "properties": {"protocol": "https"}})
    (changed,) = diagram_diff(before, after)["edges"]["changed"]
    assert changed["changes"] == {"direction": [None, "both"], "properties.protocol": [None, "https"]}


def test_diff_reports_a_layout_direction_change():
    before = demo_diagram("demo", "Demo")
    after = before.model_copy(deep=True)
    operations.apply(after, "set_direction", {"direction": "LR"})
    assert diagram_diff(before, after)["direction"] == ["TB", "LR"]
    assert diagram_diff(before, before)["direction"] is None
