"""A chat turn is finalized when its client disconnects (#112, spec: docs/architecture/specs/hardening.md, ADR-0126).

The app runs under a real uvicorn server; the client closes the stream after the first `tool_result`. With the
garbage collector disabled, the version and the transcript entry must still be written, on a worker thread, while
the event loop stays free.
"""

from __future__ import annotations

import gc
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn

from arcviz import agent as agent_module
from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from arcviz.store import FileStore
from tests.conftest import ScriptedProvider, call


class SlowSecondCall(ScriptedProvider):
    """The first model call returns at once, every later one after `delay` seconds (the client leaves meanwhile)."""

    def __init__(self, replies: list[ModelReply], delay: float):
        super().__init__(replies)
        self.delay = delay

    def _complete(self, system, messages, tools):
        if self.requests:
            time.sleep(self.delay)
        return super()._complete(system, messages, tools)


def replies() -> list[ModelReply]:
    return [
        ModelReply(
            text="",
            tool_calls=[call("add_node", "c1", name="prod-vnet", kind="vnet", parent="prod-deployment")],
            stop_reason="tool_use",
        ),
        ModelReply(
            text="",
            tool_calls=[call("add_node", "c2", name="cache", kind="service", parent="prod-deployment")],
            stop_reason="tool_use",
        ),
        ModelReply(text="fertig", tool_calls=[], stop_reason="end_turn"),
    ]


@pytest.fixture
def no_gc() -> Iterator[None]:
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


class Server:
    def __init__(self, app: Any):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, name="uvicorn-loop", daemon=True)

    def __enter__(self) -> Server:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def leave_after_first_tool_result(client: httpx.Client, path: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1]["type"] == "tool_result":
                    break
    return events  # leaving the block closes the connection mid-turn


def wait_for(predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def finalizer_threads(monkeypatch) -> list[str]:
    seen: list[str] = []
    original = agent_module.DiagramAgent._finalize

    def recording(self, *args, **kwargs):
        seen.append(threading.current_thread().name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(agent_module.DiagramAgent, "_finalize", recording)
    return seen


def test_a_disconnected_turn_is_versioned_and_transcribed_without_gc(tmp_path, no_gc, finalizer_threads):
    store = FileStore(tmp_path / "data")
    app = create_app(store=store, provider=SlowSecondCall(replies(), delay=1.0))
    with Server(app) as server, httpx.Client(base_url=server.base, timeout=10) as client:
        created = client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
        assert created.status_code == 201
        diagram_id = created.json()["diagram"]["id"]
        events = leave_after_first_tool_result(client, f"/api/diagrams/{diagram_id}/chat", {"message": "vnet bitte"})
        assert events[-1]["type"] == "tool_result"

        assert wait_for(lambda: [v.source for v in store.list_versions(diagram_id)] == ["initial", "auto"])
        assert wait_for(lambda: any(e.get("role") == "assistant" for e in store.load_chat(diagram_id)))
        entry = store.load_chat(diagram_id)[-1]
        assert "interrupted" in entry["error"] and entry["version"] == 2
        assert "prod-vnet" in store.load(diagram_id).nodes
        # finalized once, on a worker thread, never on the event loop's thread
        assert finalizer_threads and "uvicorn-loop" not in finalizer_threads and len(finalizer_threads) == 1
        # the loop stayed responsive meanwhile
        assert client.get("/healthz").status_code == 200


def test_a_disconnected_project_turn_is_transcribed_without_gc(tmp_path, no_gc):
    store = FileStore(tmp_path / "data")
    app = create_app(store=store, provider=SlowSecondCall(replies(), delay=1.0))
    with Server(app) as server, httpx.Client(base_url=server.base, timeout=10) as client:
        created = client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"}).json()
        diagram_id, project_id = created["diagram"]["id"], created["access"]["project"]
        events = leave_after_first_tool_result(
            client, f"/api/projects/{project_id}/chat", {"message": "vnet bitte", "active": diagram_id}
        )
        assert events[-1]["type"] == "tool_result"

        assert wait_for(lambda: [v.source for v in store.list_versions(diagram_id)] == ["initial", "auto"])
        assert wait_for(lambda: any(e.get("role") == "assistant" for e in store.load_project_chat(project_id)))
        assert "interrupted" in store.load_project_chat(project_id)[-1]["error"]


def test_the_chat_route_does_no_store_call_on_the_event_loop(tmp_path, monkeypatch):
    """Every store call of the chat routes before streaming runs on a worker thread (#112)."""
    store = FileStore(tmp_path / "data")
    loop_calls: list[str] = []
    original = FileStore.read_latest

    def recording(self, diagram_id):
        if threading.current_thread().name == "uvicorn-loop":
            loop_calls.append(diagram_id)
        return original(self, diagram_id)

    monkeypatch.setattr(FileStore, "read_latest", recording)
    app = create_app(
        store=store, provider=ScriptedProvider([ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")])
    )
    with Server(app) as server, httpx.Client(base_url=server.base, timeout=10) as client:
        diagram_id = client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"}).json()[
            "diagram"
        ]["id"]
        loop_calls.clear()
        response = client.post(f"/api/diagrams/{diagram_id}/chat", json={"message": "hallo"})
        assert response.status_code == 200 and '"final"' in response.text
        assert client.post("/api/diagrams/unknown/chat", json={"message": "hallo"}).status_code == 404
    assert loop_calls == []
