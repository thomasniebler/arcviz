"""Error paths and thinly tested endpoints of the HTTP API (spec: docs/architecture/specs/property-tests.md)."""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from arcviz import server
from arcviz.llm.base import ModelReply
from arcviz.server import STATIC_DIR, create_app
from tests.conftest import ScriptedProvider


@pytest.fixture
def app(store):
    return create_app(
        store=store, provider=ScriptedProvider([ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")])
    )


@pytest.fixture
def client(app):
    c = TestClient(app)
    assert c.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"}).status_code == 201
    return c


def versions(client) -> list[int]:
    return [v["version"] for v in client.get("/api/diagrams/demo/versions").json()]


# ---- diagrams ------------------------------------------------------------------


def test_create_rejects_an_unknown_template_and_moves_on_from_a_taken_id(client):
    r = client.post("/api/diagrams", json={"title": "X", "template": "nope"})
    assert (
        r.status_code == 400 and "Unknown template 'nope'" in r.json()["detail"] and "hub-spoke" in r.json()["detail"]
    )
    r = client.post("/api/diagrams", json={"title": "Again", "id": "demo"})
    assert r.status_code == 201 and r.json()["diagram"]["id"] == "demo-2"  # ADR-0089
    assert client.post("/api/diagrams", json={"title": "Bad", "id": "Bad Id"}).status_code == 400
    assert client.get("/api/diagrams/demo").json()["diagram"]["title"] == "Demo"  # untouched


def test_create_derives_the_id_from_the_title(client):
    r = client.post("/api/diagrams", json={"title": "Prod Network (EU)"})
    assert r.status_code == 201 and r.json()["diagram"]["id"] == "prod-network-eu"
    assert r.json()["diagram"]["nodes"] == {}  # template defaults to "empty"
    assert {d["id"] for d in client.get("/api/diagrams").json()} == {"demo", "prod-network-eu"}


def test_unknown_diagram_is_404_everywhere(client):
    for method, path in [
        ("GET", "/api/diagrams/nope"),
        ("DELETE", "/api/diagrams/nope"),
        ("GET", "/api/diagrams/nope/mermaid"),
        ("GET", "/api/diagrams/nope/versions"),
        ("GET", "/api/diagrams/nope/versions/1"),
        ("POST", "/api/diagrams/nope/versions/1/restore"),
        ("POST", "/api/diagrams/nope/undo"),
        ("POST", "/api/diagrams/nope/proposal/discard"),
        ("DELETE", "/api/diagrams/nope/chat"),
    ]:
        assert client.request(method, path).status_code == 404, (method, path)
    assert client.post("/api/diagrams/nope/versions", json={"label": "x"}).status_code == 404
    assert (
        client.post("/api/diagrams/nope/ops", json={"op": "rename_diagram", "args": {"title": "x"}}).status_code == 404
    )
    assert client.post("/api/diagrams/nope/chat", json={"message": "hi"}).status_code == 404


# ---- versions ------------------------------------------------------------------


def test_version_list_is_oldest_first_and_restore_of_unknown_version_is_404(client):
    client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "Renamed"}})
    listed = client.get("/api/diagrams/demo/versions").json()
    assert [v["version"] for v in listed] == [1, 2] and [v["source"] for v in listed] == ["initial", "auto"]
    assert client.post("/api/diagrams/demo/versions/99/restore").status_code == 404
    assert versions(client) == [1, 2]


def test_version_view_falls_back_to_the_whole_diagram_for_a_focus_it_lacks(client):
    client.post("/api/diagrams/demo/ops", json={"op": "add_node", "args": {"name": "late", "kind": "vnet"}})
    r = client.get("/api/diagrams/demo/versions/1", params={"focus": "late"})
    assert r.status_code == 200 and r.json()["focus"] is None and r.json()["meta"]["version"] == 1
    r = client.get("/api/diagrams/demo/versions/2", params={"focus": "late"})
    assert r.json()["focus"] == "late" and "subgraph n_late" in r.json()["mermaid"]


def test_undo_with_nothing_to_undo_is_409(client):
    r = client.post("/api/diagrams/demo/undo")
    assert r.status_code == 409 and r.json()["detail"] == "Nothing to undo."
    assert versions(client) == [1]


def test_snapshot_honours_the_focus(client):
    r = client.post("/api/diagrams/demo/versions", params={"focus": "prod-deployment"}, json={})
    assert r.status_code == 201
    assert r.json()["version"]["label"] == "Manual snapshot" and r.json()["focus"] == "prod-deployment"


# ---- direct operations -----------------------------------------------------------


@pytest.mark.parametrize(
    "body, message",
    [
        ({"op": "explode", "args": {}}, "Unknown operation 'explode'"),
        ({"op": "add_node", "args": {"name": "x", "bogus": 1}}, "Invalid arguments for add_node"),
        ({"op": "add_node", "args": {}}, "Invalid arguments for add_node"),
        ({"op": "move_node", "args": {"id": "prod-deployment", "parent": "prod-deployment"}}, "containment cycle"),
        ({"op": "apply_batch", "args": {"operations": []}}, "at least one operation"),
    ],
)
def test_rejected_operations_are_400_and_create_no_version(client, body, message):
    before = client.get("/api/diagrams/demo").json()["diagram"]
    r = client.post("/api/diagrams/demo/ops", json=body)
    assert r.status_code == 400 and message in r.json()["detail"]
    assert client.get("/api/diagrams/demo").json()["diagram"] == before
    assert versions(client) == [1]


def test_operation_label_and_focus(client):
    r = client.post(
        "/api/diagrams/demo/ops",
        params={"focus": "prod-deployment"},
        json={
            "op": "add_node",
            "args": {"name": "cache", "kind": "storage", "parent": "prod-deployment"},
            "label": "Cache",
        },
    )
    body = r.json()
    assert r.status_code == 200 and body["version"]["label"] == "Cache" and body["focus"] == "prod-deployment"
    assert body["result"]["touched"] == ["cache"] and "class n_cache changed" in body["mermaid"]
    r = client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "T"}})
    assert r.json()["version"]["label"] == 'rename_diagram {"title": "T"}'  # default label: op and args


def test_invalid_request_bodies_are_422(client):
    assert client.post("/api/diagrams/demo/ops", json={"args": {}}).status_code == 422
    assert client.post("/api/diagrams/demo/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/diagrams/demo/chat", json={"message": "x", "mode": "dry"}).status_code == 422
    assert client.post("/api/diagrams", json={}).status_code == 422


# ---- chat ------------------------------------------------------------------------


def test_clear_chat_keeps_the_versions(client):
    client.post("/api/diagrams/demo/chat", json={"message": "hallo"})
    assert [e["role"] for e in client.get("/api/diagrams/demo").json()["chat"]] == ["user", "assistant"]
    assert client.delete("/api/diagrams/demo/chat").status_code == 204
    assert client.get("/api/diagrams/demo").json()["chat"] == []
    assert versions(client) == [1]


def test_failed_compaction_is_502(app, client):
    def boom(diagram_id):
        raise RuntimeError("model down")

    stub = types.SimpleNamespace(compact=boom)
    stub.bound = lambda *services: stub  # the route binds the agent to the caller's services
    app.state.agent = stub
    r = client.post("/api/diagrams/demo/chat/compact")
    assert r.status_code == 502 and r.json()["detail"] == "Compaction failed: model down"


# ---- frontend and entry point -------------------------------------------------------


def test_index_and_every_frontend_module_are_served_as_javascript(client):
    r = client.get("/")
    assert r.status_code == 200 and '<script type="module" src="/static/app.js">' in r.text
    modules = sorted(p.name for p in STATIC_DIR.glob("*.js"))
    assert "app.js" in modules and len(modules) > 10
    for name in modules:
        r = client.get(f"/static/{name}")
        # browsers refuse module scripts with any other MIME type
        assert r.status_code == 200 and r.headers["content-type"].split(";")[0] in (
            "text/javascript",
            "application/javascript",
        ), name
    assert client.get("/static/nope.js").status_code == 404


def test_main_reads_host_and_port(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCVIZ_HOST", "0.0.0.0")
    monkeypatch.setenv("ARCVIZ_PORT", "9123")
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: seen.update(app=app, host=host, port=port))
    server.main()
    assert seen["host"] == "0.0.0.0" and seen["port"] == 9123 and seen["app"].title == "Arcviz agent"
