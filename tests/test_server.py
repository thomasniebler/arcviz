import json

import pytest
from fastapi.testclient import TestClient

from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from tests.conftest import ScriptedProvider, call


@pytest.fixture
def client(store):
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("add_node", "c1", name="prod-vnet", kind="vnet", parent="prod-deployment")],
                stop_reason="tool_use",
            ),
            ModelReply(text="Fertig.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    app = create_app(store=store, provider=provider)
    return TestClient(app)


def test_crud_versions_and_undo(client):
    assert client.get("/api/diagrams").json() == []
    r = client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    assert r.status_code == 201 and "subgraph n_prod_deployment" in r.json()["mermaid"]

    r = client.post(
        "/api/diagrams/demo/ops",
        json={"op": "add_node", "args": {"name": "cache", "kind": "storage", "parent": "prod-deployment"}},
    )
    assert r.status_code == 200 and r.json()["version"]["version"] == 2
    assert "class n_cache changed" in r.json()["mermaid"]

    r = client.post(
        "/api/diagrams/demo/ops", json={"op": "add_node", "args": {"name": "x", "kind": "service", "parent": "cache"}}
    )
    assert r.status_code == 400 and "cannot contain" in r.json()["detail"]

    r = client.post("/api/diagrams/demo/versions", json={"label": "before undo"})
    assert r.status_code == 201 and r.json()["version"]["source"] == "manual"

    r = client.post("/api/diagrams/demo/undo")
    assert r.status_code == 200
    assert "cache" not in r.json()["diagram"]["nodes"]
    assert r.json()["version"]["restored_from"] == 1  # manual snapshot v3 duplicated v2; undo skips it

    assert client.get("/api/diagrams/demo/versions/2").json()["diagram"]["nodes"]["cache"]["kind"] == "storage"
    assert client.get("/api/diagrams/demo/versions/99").status_code == 404

    r = client.post("/api/diagrams/demo/versions/2/restore")
    assert "cache" in r.json()["diagram"]["nodes"]

    assert client.delete("/api/diagrams/demo").status_code == 204
    assert client.get("/api/diagrams/demo").status_code == 404


def test_chat_streams_sse_and_persists(client):
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    with client.stream("POST", "/api/diagrams/demo/chat", json={"message": "vnet bitte"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "text", "done", "final"]
    assert events[-1]["reply"] == "Fertig." and events[-1]["version"]["version"] == 2

    detail = client.get("/api/diagrams/demo").json()
    assert "prod-vnet" in detail["diagram"]["nodes"]
    assert detail["chat"][-1]["content"] == "Fertig."
    assert detail["undo_target"] == 1

    cfg = client.get("/api/config").json()
    assert cfg["provider"] == "scripted"
    assert "vnet" in cfg["container_kinds"] and "service" not in cfg["container_kinds"]
    assert cfg["git"] == {"enabled": False}
    assert cfg["store"] in ("file", "sqlite")
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_history_turns_env(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_HISTORY_TURNS", "7")
    provider = ScriptedProvider([ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")])
    app = create_app(store=store, provider=provider)
    client = TestClient(app)
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    client.post("/api/diagrams/demo/chat", json={"message": "hallo"})
    assert app.state.agent.history_turns == 7


def test_chat_without_provider_returns_503(store, monkeypatch):
    app = create_app(store=store)
    monkeypatch.setenv("ARCVIZ_PROVIDER", "nonsense")  # after start-up, which would refuse it (ADR-0107)
    c = TestClient(app)
    c.post("/api/diagrams", json={"title": "Demo", "id": "demo"})
    r = c.post("/api/diagrams/demo/chat", json={"message": "hi"})
    assert r.status_code == 503 and "ARCVIZ_PROVIDER must be one of" in r.json()["detail"]


def test_focus_parameter_on_detail_mermaid_and_chat(client):
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})

    r = client.get("/api/diagrams/demo", params={"focus": "prod-deployment"})
    assert r.status_code == 200
    body = r.json()
    assert body["focus"] == "prod-deployment"
    assert body["breadcrumb"] == [{"id": "prod-deployment", "name": "prod-deployment", "kind": "deployment"}]
    assert body["mermaid_ids"]["prod-deployment"] == "n_prod_deployment"
    assert "class n_idp stub" in body["mermaid"]

    # a focus that does not exist falls back to the whole diagram on the detail endpoint ...
    r = client.get("/api/diagrams/demo", params={"focus": "gone"})
    assert r.status_code == 200 and r.json()["focus"] is None and r.json()["breadcrumb"] == []
    # ... but is a 404 on the bare mermaid endpoint
    assert client.get("/api/diagrams/demo/mermaid", params={"focus": "gone"}).status_code == 404
    assert (
        "class n_idp stub"
        in client.get("/api/diagrams/demo/mermaid", params={"focus": "prod-deployment"}).json()["mermaid"]
    )

    # live chat renders use the UI's focus
    r = client.post("/api/diagrams/demo/chat", json={"message": "VNet bitte", "focus": "prod-deployment"})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    final = events[-1]
    assert final["type"] == "final" and final["focus"] == "prod-deployment"
    assert "subgraph n_prod_vnet" in final["mermaid"] and "class n_idp stub" in final["mermaid"]
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["focus"] == "prod-deployment" and "class n_idp stub" in tool_result["mermaid"]


def test_create_app_builds_store_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_STORE", "sqlite")
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "d"))
    app = create_app(provider=ScriptedProvider([]))
    c = TestClient(app)
    assert c.get("/api/config").json()["store"] == "sqlite"
    assert (tmp_path / "d" / "arcviz.sqlite3").exists()


def test_ops_endpoint_applies_a_batch_atomically(client):
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    ok = [
        {"op": "add_node", "args": {"name": "cache", "kind": "storage", "parent": "prod-deployment"}},
        {"op": "add_edge", "args": {"source": "service-a", "target": "cache"}},
    ]
    r = client.post("/api/diagrams/demo/ops", json={"op": "apply_batch", "args": {"operations": ok}})
    assert r.status_code == 200 and r.json()["version"]["version"] == 2
    assert r.json()["result"]["touched"] == ["cache", "service-a"]

    bad = [{"op": "add_node", "args": {"name": "x", "kind": "service"}}, {"op": "remove_node", "args": {"id": "nope"}}]
    r = client.post("/api/diagrams/demo/ops", json={"op": "apply_batch", "args": {"operations": bad}})
    assert r.status_code == 400 and "Batch step 2 (remove_node)" in r.json()["detail"]
    assert "x" not in client.get("/api/diagrams/demo").json()["diagram"]["nodes"]
    assert len(client.get("/api/diagrams/demo/versions").json()) == 2


def test_redo_endpoint_and_redo_target(client):
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    assert client.post("/api/diagrams/demo/redo").status_code == 409
    client.post("/api/diagrams/demo/ops", json={"op": "add_node", "args": {"name": "cache", "kind": "storage"}})
    r = client.post("/api/diagrams/demo/undo")
    assert r.json()["version"]["source"] == "undo" and r.json()["redo_target"] == 2
    assert "cache" not in r.json()["diagram"]["nodes"]
    assert client.get("/api/diagrams/demo").json()["redo_target"] == 2
    r = client.post("/api/diagrams/demo/redo")
    assert r.status_code == 200 and r.json()["version"]["source"] == "redo"
    assert r.json()["version"]["restored_from"] == 2 and "cache" in r.json()["diagram"]["nodes"]
    assert r.json()["redo_target"] is None and r.json()["undo_target"] == 1
    assert client.post("/api/diagrams/demo/redo").json()["detail"] == "Nothing to redo."
    assert client.post("/api/diagrams/missing/redo").status_code == 404


# ---- health and readiness ------------------------------------------------------


class _BrokenStore:
    """Wraps a store; only the readiness probe fails."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def ping(self):
        raise RuntimeError("disk on fire")


class _UnconfiguredProvider(ScriptedProvider):
    def config_error(self):
        return "ANTHROPIC_API_KEY is not set."


def test_healthz_has_no_dependencies(store):
    c = TestClient(create_app(store=_BrokenStore(store), provider=_UnconfiguredProvider([])))
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_readyz_when_store_and_provider_are_fine(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ready",
        "checks": {"store": {"ok": True}, "provider": {"ok": True}, "writer": {"ok": True}},
    }


def test_readyz_reports_every_failing_check(store):
    c = TestClient(create_app(store=_BrokenStore(store), provider=_UnconfiguredProvider([])))
    r = c.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {
        "status": "unavailable",
        "checks": {
            "store": {"ok": False, "error": "disk on fire"},
            "provider": {"ok": False, "error": "ANTHROPIC_API_KEY is not set."},
            "writer": {"ok": True},
        },
    }


def test_readyz_when_the_provider_cannot_be_created(store, monkeypatch):
    c = TestClient(create_app(store=store))
    monkeypatch.setenv("ARCVIZ_PROVIDER", "nonsense")  # after start-up, which would refuse it (ADR-0107)
    r = c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["store"] == {"ok": True}
    assert "ARCVIZ_PROVIDER must be one of" in r.json()["checks"]["provider"]["error"]
    assert "ARCVIZ_PROVIDER must be one of" in c.get("/api/config").json()["provider_error"]


def test_readyz_creates_the_provider_once_and_the_chat_reuses_it(store, monkeypatch):
    created = []

    def fake_create_provider():
        created.append(ScriptedProvider([ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")]))
        return created[-1]

    monkeypatch.setattr("arcviz.server.create_provider", fake_create_provider)
    app = create_app(store=store)
    c = TestClient(app)
    assert c.get("/api/config").json()["provider_ready"] is False
    assert c.get("/readyz").status_code == 200
    assert c.get("/readyz").status_code == 200
    assert c.get("/api/config").json()["provider_ready"] is True
    c.post("/api/diagrams", json={"title": "Demo", "id": "demo"})
    c.post("/api/diagrams/demo/chat", json={"message": "hi"})
    assert len(created) == 1 and app.state.agent.provider is created[0]
