"""Request size and rate limits (issue #85; spec: docs/architecture/specs/request-limits.md, ADR-0048)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange, limits
from arcviz.llm.base import ModelReply
from arcviz.operations import MAX_BATCH
from arcviz.server import create_app
from tests.conftest import ScriptedProvider


def app_client(store, monkeypatch, provider=None, **env: str) -> TestClient:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return TestClient(create_app(store=store, provider=provider or ScriptedProvider([])))


def op_body(name: str) -> dict:
    return {"op": "add_node", "args": {"name": name, "kind": "service", "parent": "prod-deployment"}}


# ---- sizes ---------------------------------------------------------------------------


def test_a_body_over_the_limit_is_413_and_writes_nothing(store, demo, monkeypatch):
    client = app_client(store, monkeypatch, ARCVIZ_MAX_BODY_BYTES="200")
    big = op_body("x" * 300)
    r = client.post("/api/diagrams/demo/ops", json=big)
    assert r.status_code == 413 and r.json()["detail"] == "Request body too large (max 200 bytes)."
    assert len(store.list_versions("demo")) == 1
    assert client.post("/api/diagrams/demo/ops", json=op_body("small")).status_code == 200


def test_a_chunked_body_without_content_length_is_limited_too(store, demo, monkeypatch):
    client = app_client(store, monkeypatch, ARCVIZ_MAX_BODY_BYTES="200")
    payload = json.dumps(op_body("y" * 300)).encode()

    def chunks():
        for i in range(0, len(payload), 50):
            yield payload[i : i + 50]

    r = client.post("/api/diagrams/demo/ops", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and "content-length" not in {k.lower() for k in r.request.headers}
    small = json.dumps(op_body("ok")).encode()
    r = client.post(
        "/api/diagrams/demo/ops", content=iter([small[:10], small[10:]]), headers={"content-type": "application/json"}
    )
    assert r.status_code == 200


def test_a_body_exactly_at_the_limit_passes(store, demo, monkeypatch):
    body = json.dumps(op_body("edge")).encode()
    client = app_client(store, monkeypatch, ARCVIZ_MAX_BODY_BYTES=str(len(body)))
    r = client.post("/api/diagrams/demo/ops", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 200


def test_the_import_has_its_own_limit(store, demo, monkeypatch):
    document = exchange.export_document(store.load("demo"))
    size = len(json.dumps({"document": document}).encode())
    client = app_client(store, monkeypatch, ARCVIZ_MAX_BODY_BYTES="100", ARCVIZ_MAX_IMPORT_BYTES=str(size + 100))
    assert client.post("/api/diagrams/import", json={"document": document, "id": "copy"}).status_code == 201
    client = app_client(store, monkeypatch, ARCVIZ_MAX_IMPORT_BYTES="100")
    r = client.post("/api/diagrams/import", json={"document": document, "id": "copy-2"})
    assert r.status_code == 413 and "max 100 bytes" in r.json()["detail"] and not store.exists("copy-2")


def test_a_chat_message_over_the_limit_is_413_before_any_model_call(store, demo, monkeypatch):
    provider = ScriptedProvider([ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")])
    client = app_client(store, monkeypatch, provider, ARCVIZ_MAX_CHAT_CHARS="10")
    r = client.post("/api/diagrams/demo/chat", json={"message": "x" * 11})
    assert r.status_code == 413 and r.json()["detail"] == "Chat message too long (11 characters, max 10)."
    assert provider.requests == [] and store.load_chat("demo") == []
    r = client.post("/api/diagrams/demo/chat", json={"message": "x" * 10})
    assert r.status_code == 200 and provider.requests


def test_a_proposal_with_too_many_operations_is_413(client_default):
    client, store = client_default
    operations = [{"op": "rename_diagram", "arguments": {"title": "t"}}] * (limits.MAX_PROPOSAL_OPERATIONS + 1)
    r = client.post("/api/diagrams/demo/proposal/apply", json={"operations": operations, "base": "b", "result": "r"})
    assert r.status_code == 413 and "max 500" in r.json()["detail"]


def test_an_oversized_batch_stays_a_400_operation_error(client_default):
    client, _ = client_default
    steps = [{"op": "rename_diagram", "args": {"title": "t"}}] * (MAX_BATCH + 1)
    r = client.post("/api/diagrams/demo/ops", json={"op": "apply_batch", "args": {"operations": steps}})
    assert r.status_code == 400 and f"at most {MAX_BATCH}" in r.json()["detail"]


def test_zero_disables_a_size_limit(store, demo, monkeypatch):
    client = app_client(store, monkeypatch, ARCVIZ_MAX_BODY_BYTES="0", ARCVIZ_MAX_CHAT_CHARS="0")
    assert client.post("/api/diagrams/demo/ops", json=op_body("z" * 5000)).status_code == 200


def test_config_reports_the_limits(store, demo, monkeypatch):
    client = app_client(store, monkeypatch, ARCVIZ_RATE_LIMIT_CHAT="6")
    assert client.get("/api/config").json()["limits"] == {
        "max_body_bytes": limits.DEFAULT_MAX_BODY_BYTES,
        "max_import_bytes": limits.DEFAULT_MAX_IMPORT_BYTES,
        "max_chat_chars": limits.DEFAULT_MAX_CHAT_CHARS,
        "max_proposal_operations": limits.MAX_PROPOSAL_OPERATIONS,
        "max_batch": MAX_BATCH,
        "rate_limits": {"chat": 6, "review": 0, "ops": 0, "share": 30, "agent": 0},
    }


@pytest.mark.parametrize("value", ["lots", "-1", "1.5"])
def test_invalid_configuration_stops_the_start(store, monkeypatch, value):
    monkeypatch.setenv("ARCVIZ_MAX_BODY_BYTES", value)
    with pytest.raises(ValueError, match="ARCVIZ_MAX_BODY_BYTES must be a non-negative integer"):
        create_app(store=store, provider=ScriptedProvider([]))


# ---- rates ---------------------------------------------------------------------------


@pytest.fixture
def client_default(store, demo, monkeypatch):
    return app_client(store, monkeypatch), store


def test_rate_limiting_is_off_by_default(client_default):
    client, _ = client_default
    for i in range(30):
        assert client.post("/api/diagrams/demo/ops", json=op_body(f"n{i}")).status_code == 200


def test_rate_limits_apply_per_class_and_answer_429_with_retry_after(store, demo, monkeypatch):
    replies = [ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")] * 5
    client = app_client(store, monkeypatch, ScriptedProvider(replies), ARCVIZ_RATE_LIMIT_CHAT="2")
    assert client.post("/api/diagrams/demo/chat", json={"message": "a"}).status_code == 200
    assert client.post("/api/diagrams/demo/chat/compact").status_code == 200  # same class
    r = client.post("/api/diagrams/demo/chat", json={"message": "b"})
    assert r.status_code == 429 and r.json()["detail"].startswith("Too many chat requests; try again in ")
    assert 1 <= int(r.headers["retry-after"]) <= 30
    # other classes, reads and probes are not affected
    assert client.post("/api/diagrams/demo/ops", json=op_body("x")).status_code == 200
    assert client.get("/api/diagrams/demo").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_ops_and_review_classes(store, demo, monkeypatch):
    client = app_client(store, monkeypatch, ARCVIZ_RATE_LIMIT_OPS="1", ARCVIZ_RATE_LIMIT_REVIEW="1")
    assert client.post("/api/diagrams/demo/ops", json=op_body("x")).status_code == 200
    assert client.post("/api/diagrams/demo/undo").status_code == 429  # every mutation is an "ops" request
    assert client.delete("/api/diagrams/demo/chat").status_code == 429
    assert client.post("/api/diagrams/demo/review").status_code == 200
    r = client.post("/api/diagrams/demo/review")
    assert r.status_code == 429 and "review" in r.json()["detail"]


def test_rate_class_of_requests():
    assert limits.rate_class("POST", "/api/diagrams/d/chat") == "chat"
    assert limits.rate_class("POST", "/api/diagrams/d/chat/compact") == "chat"
    assert limits.rate_class("POST", "/api/diagrams/d/review") == "review"
    assert limits.rate_class("POST", "/api/projects/p/requirements/refine") == "review"
    assert limits.rate_class("POST", "/api/projects/p/requirements/derive") == "review"
    assert limits.rate_class("POST", "/api/projects/p/requirements/chat") == "chat"
    assert limits.rate_class("POST", "/api/projects/p/requirements/import") == "ops"
    assert limits.rate_class("DELETE", "/api/diagrams/d/chat") == "ops"
    assert limits.rate_class("POST", "/api/diagrams") == "ops"
    assert limits.rate_class("GET", "/api/diagrams/d") is None
    assert limits.rate_class("POST", "/login") is None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_token_bucket_bursts_refills_and_separates_clients():
    clock = Clock()
    limiter = limits.RateLimiter({"chat": 3}, clock=clock)
    assert [limiter.check("chat", "a") for _ in range(3)] == [None, None, None]
    wait = limiter.check("chat", "a")
    assert wait == pytest.approx(20.0)  # 3 per minute: one token every 20 s
    assert limiter.check("chat", "b") is None  # another client has its own bucket
    clock.now += 20
    assert limiter.check("chat", "a") is None and limiter.check("chat", "a") is not None
    assert limiter.check("ops", "a") is None  # a class without a limit


def test_idle_buckets_are_evicted_first():
    clock = Clock()
    limiter = limits.RateLimiter({"chat": 2}, clock=clock, max_buckets=3)
    limiter.check("chat", "busy")
    limiter.check("chat", "busy")
    for client in ("idle-1", "idle-2", "idle-3"):
        clock.now += 60  # long enough for every bucket to be full again, except the busy one's last use
        limiter.check("chat", client)
    limiter.check("chat", "busy")
    assert len(limiter) <= 3
