"""ETag/If-Match and the live routes through the server (issues #86, #171; spec: collaboration.md)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import limits, live
from arcviz.server import create_app
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider, proxy_users


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_LIVE_MAX_SECONDS", "1.0")
    monkeypatch.setenv("ARCVIZ_LIVE_POLL_SECONDS", "0.1")
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    return TestClient(create_app(store=store, provider=ScriptedProvider([])))


def events_of(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


# ---- ETag -------------------------------------------------------------------------------------------------


def test_diagram_gets_answer_the_latest_revision_as_etag(client, store):
    revision = store.load_with_revision("demo")[1]
    detail = client.get("/api/diagrams/demo")
    assert detail.headers["etag"] == f'"{revision}"' and detail.json()["revision"] == revision
    for path in ("/api/diagrams/demo/render", "/api/diagrams/demo/mermaid", "/api/diagrams/demo/export"):
        assert client.get(path).headers["etag"] == f'"{revision}"', path
    assert "etag" not in client.get("/api/diagrams/demo/export?version=1").headers
    after = client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "X"}}).json()
    assert after["revision"] != revision and after["revision"] == store.load_with_revision("demo")[1]


# ---- If-Match ----------------------------------------------------------------------------------------------

RENAME = {"op": "rename_diagram", "args": {"title": "Neu"}}


@pytest.mark.parametrize("header", ['"{rev}"', "{rev}", '"{rev}", "other"'])
def test_if_match_on_the_current_revision_applies(client, store, header):
    revision = store.load_with_revision("demo")[1]
    r = client.post("/api/diagrams/demo/ops", json=RENAME, headers={"If-Match": header.format(rev=revision)})
    assert r.status_code == 200 and r.json()["diagram"]["title"] == "Neu"


@pytest.mark.parametrize("header", ["*", ""])
def test_a_wildcard_or_empty_if_match_is_no_condition(client, header):
    client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "Zwischen"}})
    assert client.post("/api/diagrams/demo/ops", json=RENAME, headers={"If-Match": header}).status_code == 200


def stale(client, store) -> str:
    revision = store.load_with_revision("demo")[1]
    client.post("/api/diagrams/demo/ops", json={"op": "update_node", "args": {"id": "service-a", "name": "A2"}})
    return f'"{revision}"'


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/diagrams/demo/ops", RENAME),
        ("POST", "/api/diagrams/demo/versions", {"label": "Stand"}),
        ("POST", "/api/diagrams/demo/versions/1/restore", None),
        ("POST", "/api/diagrams/demo/undo", None),
        ("POST", "/api/diagrams/demo/redo", None),
        ("POST", "/api/diagrams/demo/unlink", {"view": "service-a"}),
    ],
)
def test_a_stale_if_match_is_412_and_writes_nothing(client, store, method, path, body):
    header = stale(client, store)
    before = store.load_with_revision("demo")
    versions = len(store.list_versions("demo"))
    r = client.request(method, path, json=body, headers={"If-Match": header})
    assert r.status_code == 412, r.text
    assert r.json()["revision"] == before[1]
    assert store.load_with_revision("demo") == before and len(store.list_versions("demo")) == versions


def test_without_if_match_a_concurrent_change_is_still_reapplied(client, store):
    stale(client, store)
    assert client.post("/api/diagrams/demo/ops", json=RENAME).status_code == 200


def test_access_is_checked_before_the_revision(store, monkeypatch):
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    users = proxy_users(store, monkeypatch)
    revision = store.load_with_revision("demo")[1]
    stale_header = {"If-Match": '"nope"'}
    r = users.client.post("/api/diagrams/demo/ops", json=RENAME, headers={**users.headers("out"), **stale_header})
    assert r.status_code == 404
    r = users.client.post("/api/diagrams/demo/ops", json=RENAME, headers={**users.headers("vic"), **stale_header})
    assert r.status_code == 404  # signed in, but no member: not visible
    assert users.client.get("/api/diagrams/demo", headers=users.headers("admin")).headers["etag"] == f'"{revision}"'


# ---- live streams ---------------------------------------------------------------------------------------


def stream(client, path: str, results: list, **kwargs):
    results.append(client.get(path, **kwargs))


def open_stream(client, path: str, results: list) -> threading.Thread:
    """Start a stream in a thread and wait until the hub has it (writes before that would only show in `hello`)."""
    hub = client.app.state.hub
    before = hub.streams()
    thread = threading.Thread(target=stream, args=(client, path, results))
    thread.start()
    deadline = time.monotonic() + 10
    while hub.streams() <= before and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)  # past the first presence snapshot
    return thread


def recorded_streams(monkeypatch) -> list[Any]:
    """Every event the live streams yield, in order, as the server writes them (None: a keep-alive). Tests wait on
    conditions over it instead of on time windows (#213)."""
    seen: list[Any] = []
    real = live.run_stream

    async def recording(*args, **kwargs):
        async for event in real(*args, **kwargs):
            seen.append(event)
            yield event

    monkeypatch.setattr(live, "run_stream", recording)
    return seen


def wait_for(condition, what: str, timeout: float = 30.0) -> None:
    """Wait until `condition()` holds; the timeout only turns a hang into a failure, no assertion depends on it."""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def test_a_stream_reports_a_concurrent_change(store, monkeypatch):
    """#213: the stream lives until the test has seen what it waits for (no lifetime to race the write), then the hub
    ends it. The poll only adds keep-alives here: a revision it saw first is attributed once the bus event arrives."""
    monkeypatch.setenv("ARCVIZ_LIVE_MAX_SECONDS", "600")
    monkeypatch.setenv("ARCVIZ_LIVE_POLL_SECONDS", "0.1")
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    seen = recorded_streams(monkeypatch)
    results: list = []
    thread = open_stream(client, "/api/diagrams/demo/live?session=tab-one-123", results)
    revision = client.post("/api/diagrams/demo/ops", json=RENAME).json()["revision"]

    def attributed() -> bool:
        return any(e and e["type"] == "changed" and e["revision"] == revision and e["author"] for e in list(seen))

    wait_for(attributed, "the attributed change")
    wait_for(lambda: None in list(seen), "a keep-alive")
    client.app.state.hub.close()
    thread.join(30)
    assert not thread.is_alive()
    response = results[0]
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = events_of(response.text)
    assert events[0]["type"] == "hello" and events[0]["session"] == "tab-one-123" and events[0]["diagram"] == "demo"
    changed = [e for e in events if e["type"] == "changed"]
    assert changed[-1]["revision"] == revision == store.load_with_revision("demo")[1]
    # attributed, also when the poll saw the new revision before the bus event arrived (then it is named again)
    assert changed[-1]["author"] == "admin" and changed[-1]["origin"] == "user"
    assert events[-1] == {"type": "bye", "reason": "shutdown"}
    assert ": keep-alive" in response.text


def test_a_stream_ends_after_its_lifetime(client):
    """The lifetime is the only clock here, and nothing races it: whenever the end comes, it is `bye expired`."""
    results: list = []
    thread = open_stream(client, "/api/diagrams/demo/live?session=tab-one-123", results)
    thread.join(30)
    events = events_of(results[0].text)
    assert events[0]["type"] == "hello" and events[-1] == {"type": "bye", "reason": "expired"}


def test_a_change_after_a_stream_ended_is_in_the_next_hello(client, store):
    """#213: a stream cannot lose a change for its client. What it did not report before its `bye` (written after its
    last re-check, or too close to its end) is in the `revision` of the reconnect's `hello`, which live.js compares
    with the revision it shows."""
    results: list = []
    open_stream(client, "/api/diagrams/demo/live?session=tab-one-123", results).join(30)
    hello = events_of(results[0].text)[0]
    revision = client.post("/api/diagrams/demo/ops", json=RENAME).json()["revision"]
    assert revision != hello["revision"]
    open_stream(client, "/api/diagrams/demo/live?session=tab-one-123", results).join(30)
    assert events_of(results[1].text)[0]["revision"] == revision


def test_a_project_stream_reports_project_changes(client, store):
    results: list = []
    thread = open_stream(client, "/api/projects/default/live", results)
    client.post("/api/projects/default/sections", json={"title": "Neu"})
    thread.join(10)
    events = events_of(results[0].text)
    assert events[0]["type"] == "hello" and events[0]["diagram"] is None
    assert any(e["type"] == "project" and e["project"] == "default" for e in events)


def test_a_deleted_diagram_ends_its_stream_with_gone(store, monkeypatch):
    """#218 (like #213): no lifetime to race. The stream lives until it reported the delete, and the test waits on that
    condition through the recorded events, not on a time window."""
    monkeypatch.setenv("ARCVIZ_LIVE_MAX_SECONDS", "600")
    monkeypatch.setenv("ARCVIZ_LIVE_POLL_SECONDS", "0.1")
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    seen = recorded_streams(monkeypatch)
    results: list = []
    thread = open_stream(client, "/api/diagrams/demo/live", results)
    assert client.delete("/api/diagrams/demo").status_code == 204
    wait_for(lambda: {"type": "gone"} in list(seen), "gone")
    thread.join(30)
    assert not thread.is_alive()
    assert events_of(results[0].text)[-1] == {"type": "gone"}


def test_unknown_diagrams_and_projects_have_no_stream(client):
    assert client.get("/api/diagrams/nope/live").status_code == 404
    assert client.get("/api/projects/nope/live").status_code == 404


def test_presence_needs_an_open_stream_and_is_delivered(client):
    assert (
        client.post("/api/diagrams/demo/presence", json={"session": "tab-one-123", "selection": "x"}).status_code == 409
    )
    results: list = []
    thread = open_stream(client, "/api/diagrams/demo/live?session=tab-one-123", results)
    r = client.post("/api/diagrams/demo/presence", json={"session": "tab-one-123", "selection": "service-a"})
    assert r.status_code == 204
    assert (
        client.post("/api/diagrams/demo/presence", json={"session": "tab-one-123", "selection": "x" * 300}).status_code
        == 400
    )
    thread.join(10)
    presence = [e for e in events_of(results[0].text) if e["type"] == "presence"]
    assert presence[-1]["viewers"][0]["selection"] == "service-a"


def test_viewers_may_stream_and_outsiders_get_404(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_LIVE_MAX_SECONDS", "0.3")
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    users = proxy_users(store, monkeypatch)
    assert users("admin", "GET", "/api/diagrams/demo/live").status_code == 200
    assert users("out", "GET", "/api/diagrams/demo/live").status_code == 404
    body = {"session": "tab-one-123", "selection": None}
    assert users("out", "POST", "/api/diagrams/demo/presence", json=body).status_code == 404


def test_the_stream_caps(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_LIVE_MAX_SECONDS", "1")
    monkeypatch.setenv("ARCVIZ_LIVE_MAX_STREAMS_PER_USER", "1")
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    results: list = []
    thread = open_stream(client, "/api/diagrams/demo/live?session=tab-one-123", results)
    second = client.get("/api/diagrams/demo/live?session=tab-two-1234")
    assert second.status_code == 429 and second.headers["retry-after"] == "30"
    thread.join(10)
    client.app.state.hub.close()
    assert client.get("/api/diagrams/demo/live").status_code == 503


def test_presence_bodies_are_small_and_live_routes_have_no_rate_class():
    config = limits.LimitConfig()
    assert config.body_limit("/api/diagrams/x/presence") == limits.PRESENCE_MAX_BODY
    assert config.body_limit("/api/diagrams/x/ops") == limits.DEFAULT_MAX_BODY_BYTES
    assert limits.rate_class("POST", "/api/diagrams/x/presence") is None
    assert limits.rate_class("POST", "/api/diagrams/x/ops") == "ops"
    # resources that happen to be called "live" or "presence" are ordinary writes
    assert limits.rate_class("PATCH", "/api/projects/p/elements/presence") == "ops"
    assert limits.rate_class("DELETE", "/api/diagrams/live") == "ops"
    assert config.body_limit("/api/projects/p/elements/presence") == limits.DEFAULT_MAX_BODY_BYTES
    env = {"ARCVIZ_LIVE_MAX_STREAMS": "5", "ARCVIZ_LIVE_MAX_STREAMS_PER_USER": "2"}
    assert (limits.LimitConfig.from_env(env).max_streams, limits.LimitConfig.from_env(env).max_streams_per_user) == (
        5,
        2,
    )
    with pytest.raises(ValueError, match="ARCVIZ_LIVE_MAX_STREAMS"):
        limits.LimitConfig.from_env({"ARCVIZ_LIVE_MAX_STREAMS": "many"})


def test_probes_are_unaffected_by_open_streams(client):
    results: list = []
    thread = open_stream(client, "/api/diagrams/demo/live", results)
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    thread.join(10)


def test_an_invalid_live_configuration_stops_the_start(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_LIVE_POLL_SECONDS", "soon")
    with pytest.raises(ValueError, match="ARCVIZ_LIVE_POLL_SECONDS"):
        create_app(store=store, provider=ScriptedProvider([]))


def test_a_chat_turn_marks_its_caller_as_chatting(store, monkeypatch):
    from arcviz.llm.base import ModelReply

    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    seen: list = []
    provider = ScriptedProvider([ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")])
    app = create_app(store=store, provider=provider)
    hub = app.state.hub
    original = hub.chatting

    def spy(user, name, diagram, project):
        seen.append((user, diagram, project))
        return original(user, name, diagram, project)

    monkeypatch.setattr(hub, "chatting", spy)
    with TestClient(app).stream("POST", "/api/diagrams/demo/chat", json={"message": "Hallo"}) as response:
        "".join(response.iter_text())
    assert seen == [("admin", "demo", "default")]


# ---- If-Match on deletes (#175, ADR-0123) -------------------------------------------------------------------


def test_a_diagram_delete_honours_if_match(client, store):
    header = stale(client, store)
    r = client.delete("/api/diagrams/demo", headers={"If-Match": header})
    assert r.status_code == 412 and r.json()["revision"] == store.read_revision("demo")
    assert store.exists("demo")
    current = client.get("/api/diagrams/demo").headers["etag"]
    assert client.delete("/api/diagrams/demo", headers={"If-Match": current}).status_code == 204
    assert not store.exists("demo")


def test_a_project_delete_honours_if_match(client, store):
    created = client.post("/api/projects", json={"title": "Leer"}).json()
    project_id = created["project"]["id"]
    got = client.get(f"/api/projects/{project_id}")
    revision = got.json()["revision"]
    assert got.headers["etag"] == f'"{revision}"' and created["revision"] == revision
    client.patch(f"/api/projects/{project_id}", json={"title": "Umbenannt"})
    r = client.delete(f"/api/projects/{project_id}", headers={"If-Match": f'"{revision}"'})
    assert r.status_code == 412 and r.json()["revision"] == store.read_project(project_id)[1]
    current = client.get(f"/api/projects/{project_id}").headers["etag"]
    assert client.delete(f"/api/projects/{project_id}", headers={"If-Match": current}).status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_deletes_check_access_before_the_revision(store, monkeypatch):
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    users = proxy_users(store, monkeypatch)
    users("admin", "GET", "/api/projects")  # the admin's data migrated
    users("vic", "GET", "/api/projects")
    project = users("admin", "GET", "/api/diagrams/demo").json()["access"]["project"]
    users("admin", "POST", f"/api/projects/{project}/access", json={"user": "vic", "role": "editor"})
    stale_header = {"If-Match": '"nope"'}
    for user, status in (("out", 404), ("vic", 403)):
        headers = {**users.headers(user), **stale_header}
        assert users.client.delete("/api/diagrams/demo", headers=headers).status_code == status, user
        assert users.client.delete(f"/api/projects/{project}", headers=headers).status_code == status, user
    headers = {**users.headers("admin"), **stale_header}
    assert users.client.delete("/api/diagrams/demo", headers=headers).status_code == 412
