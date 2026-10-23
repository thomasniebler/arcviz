"""Overlapping mutations through the agent and the HTTP API (issue #65).

Spec: docs/architecture/specs/concurrent-mutations.md, ADR-0044. The interleavings are deterministic:
a competing write is injected at a precise point (inside a model call, or right before a commit).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import operations as ops
from arcviz.agent import DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from arcviz.services import DiagramService
from arcviz.store import Change, StoreError, fingerprint
from tests.conftest import ScriptedProvider, call


def direct_edit(store, node_id: str) -> None:
    """What POST /ops does: a separate request adding a node, with its own version."""

    def change(diagram):
        ops.add_node(diagram, node_id, kind="service", id=node_id, parent="prod-deployment")
        return Change(diagram, None, label=f"direct {node_id}")

    store.update("demo", change)


class HookedProvider(ScriptedProvider):
    """Runs `hooks[i]` (if any) at the start of the i-th model call, i.e. while the turn is in flight."""

    def __init__(self, replies: list[ModelReply], hooks: dict[int, Callable[[], None]]):
        super().__init__(replies)
        self.hooks = hooks
        self.calls = 0

    def _complete(self, system, messages, tools):
        hook = self.hooks.get(self.calls)
        self.calls += 1
        if hook:
            hook()
        return super()._complete(system, messages, tools)


def tool_round(*calls: Any) -> ModelReply:
    return ModelReply(text="", tool_calls=list(calls), stop_reason="tool_use")


def done(text: str = "Fertig.") -> ModelReply:
    return ModelReply(text=text, tool_calls=[], stop_reason="end_turn")


def add(node_id: str, call_id: str) -> Any:
    return call("add_node", call_id, name=node_id, id=node_id, kind="service", parent="prod-deployment")


def run(store, provider) -> list[dict[str, Any]]:
    return list(DiagramAgent(provider, DiagramService(store), background=False).chat("demo", "Baue etwas"))


# ---- agent ---------------------------------------------------------------------------


def test_a_direct_edit_between_two_tool_calls_is_kept_and_the_second_call_is_rebased(store, demo):
    provider = HookedProvider(
        [tool_round(add("one", "c1")), tool_round(add("two", "c2")), done()],
        hooks={1: lambda: direct_edit(store, "direct")},  # after "one" was committed, before "two"
    )
    events = run(store, provider)
    latest = store.load("demo")
    assert {"one", "direct", "two"} <= set(latest.nodes)
    final = events[-1]
    assert final["type"] == "final" and set(final["diagram"]["nodes"]) == set(latest.nodes)
    live = [e for e in events if e["type"] == "tool_result"][-1]
    assert "direct" in live["diagram"]["nodes"]  # the live view follows the rebased state
    # the turn's version is the newest one and equals latest
    assert final["version"]["version"] == store.list_versions("demo")[-1].version
    assert store.load_version("demo", final["version"]["version"])[1] == latest


def test_finalization_does_not_overwrite_a_newer_direct_edit(store, demo):
    provider = HookedProvider(
        [tool_round(add("one", "c1")), done()],
        hooks={1: lambda: direct_edit(store, "direct")},  # after the turn's last committed call
    )
    events = run(store, provider)
    latest = store.load("demo")
    assert {"one", "direct"} <= set(latest.nodes)
    assert events[-1]["version"] is None  # skipped: the newer state already contains "one"
    assert set(events[-1]["diagram"]["nodes"]) == set(latest.nodes)  # final shows the stored state
    assert [v.label for v in store.list_versions("demo")] == ["Initial state", "direct direct"]
    assert store.load_chat("demo")[-1]["version"] is None


def test_finalization_does_not_overwrite_a_newer_restore(store, demo):
    provider = HookedProvider(
        [tool_round(add("one", "c1")), done()],
        hooks={1: lambda: store.restore("demo", 1)},  # the user restores the initial state meanwhile
    )
    events = run(store, provider)
    assert "one" not in store.load("demo").nodes
    assert events[-1]["version"] is None and "one" not in events[-1]["diagram"]["nodes"]
    assert store.list_versions("demo")[-1].source == "restore"
    assert fingerprint(store.load("demo")) == fingerprint(store.load_version("demo", 1)[1])


def test_deleting_the_diagram_during_a_turn_does_not_resurrect_it(store, demo):
    provider = HookedProvider(
        [tool_round(add("one", "c1")), tool_round(add("two", "c2")), done("Gelöscht?")],
        hooks={1: lambda: store.delete("demo")},
    )
    events = run(store, provider)
    results = [e for e in events if e["type"] == "tool_result"]
    assert not results[0]["is_error"]
    assert results[1]["is_error"] and "deleted" in json.loads(results[1]["content"])["error"]
    assert events[-1]["type"] == "final" and events[-1]["version"] is None
    assert events[-1]["error"] == "The diagram was deleted during this turn."
    assert not store.exists("demo") and store.list_diagrams() == []
    assert store.load_chat("demo") == [] and store.list_versions("demo") == []


def test_a_call_that_no_longer_applies_after_a_concurrent_change_is_an_error_tool_result(store, demo):
    def remove_target() -> None:
        def change(diagram):
            ops.remove_node(diagram, "idp")
            return Change(diagram, None, label="remove idp")

        store.update("demo", change)

    provider = HookedProvider(
        [tool_round(add("one", "c1")), tool_round(call("update_node", "c2", id="idp", name="IdP")), done()],
        hooks={1: remove_target},  # the second call's target disappears before the call is committed
    )
    events = run(store, provider)
    second = [e for e in events if e["type"] == "tool_result"][1]
    assert second["is_error"] and "Unknown node 'idp'" in json.loads(second["content"])["error"]
    assert "idp" not in store.load("demo").nodes and "one" in store.load("demo").nodes


# ---- HTTP API ------------------------------------------------------------------------


@pytest.fixture
def client(store, demo):
    return TestClient(create_app(store=store, provider=ScriptedProvider([])))


def race_next_commit(store, competing: Callable[[], None], times: int = 1) -> list[int]:
    """Run `competing` right before the next `times` commits, i.e. after the request has read the state."""
    original = store.commit_latest
    fired: list[int] = []
    busy: list[bool] = []  # the competing write's own commit passes straight through

    def racing(diagram, expected, info):
        if len(fired) < times and not busy:
            fired.append(1)
            busy.append(True)
            try:
                competing()
            finally:
                busy.pop()
        return original(diagram, expected, info)

    store.commit_latest = racing
    return fired


def test_ops_retries_and_keeps_the_concurrent_change(client, store):
    fired = race_next_commit(store, lambda: direct_edit(store, "direct"))
    r = client.post(
        "/api/diagrams/demo/ops",
        json={"op": "add_node", "args": {"name": "mine", "kind": "service", "parent": "prod-deployment"}},
    )
    assert r.status_code == 200 and fired
    assert {"direct", "mine"} <= set(r.json()["diagram"]["nodes"])
    assert {"direct", "mine"} <= set(store.load("demo").nodes)
    assert [v["label"] for v in r.json()["versions"]][-2:] == ["direct direct", r.json()["version"]["label"]]


def test_ops_answers_409_when_every_attempt_conflicts(client, store):
    counter = iter(range(100))
    race_next_commit(store, lambda: direct_edit(store, f"d{next(counter)}"), times=100)
    r = client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "Mine"}})
    assert r.status_code == 409 and "try again" in r.json()["detail"]
    assert store.load("demo").title != "Mine"


def test_ops_on_a_diagram_deleted_meanwhile_is_404(client, store):
    race_next_commit(store, lambda: store.delete("demo"))
    r = client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "Mine"}})
    assert r.status_code == 404 and not store.exists("demo")


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/diagrams/demo/versions/1/restore", None),
        ("/api/diagrams/demo/versions", {"label": "checkpoint"}),
    ],
)
def test_restore_and_snapshot_are_retried(client, store, path, body):
    fired = race_next_commit(store, lambda: direct_edit(store, "direct"))
    r = client.post(path, json=body) if body is not None else client.post(path)
    assert r.status_code in (200, 201) and fired
    newest = store.list_versions("demo")[-1]
    assert newest.version == r.json()["version"]["version"]
    assert store.load_version("demo", newest.version)[1] == store.load("demo")


def test_undo_is_retried_and_undoes_the_newest_change(client, store):
    direct_edit(store, "first")
    race_next_commit(store, lambda: direct_edit(store, "second"))
    r = client.post("/api/diagrams/demo/undo")
    assert r.status_code == 200
    nodes = set(store.load("demo").nodes)
    assert "first" in nodes and "second" not in nodes


def test_proposal_confirm_is_atomic_with_its_base_check(client, store):
    base = fingerprint(store.load("demo"))
    preview = store.load("demo")
    ops.apply(preview, "add_node", {"name": "cache", "kind": "storage", "parent": "prod-deployment"})
    body = {
        "operations": [
            {"op": "add_node", "arguments": {"name": "cache", "kind": "storage", "parent": "prod-deployment"}}
        ],
        "base": base,
        "result": fingerprint(preview),
    }
    race_next_commit(store, lambda: direct_edit(store, "direct"))  # a real change after the replay
    r = client.post("/api/diagrams/demo/proposal/apply", json=body)
    assert r.status_code == 409
    assert "cache" not in store.load("demo").nodes and "direct" in store.load("demo").nodes

    store.snapshot_latest("demo", "unrelated")  # content unchanged: the confirm is retried and succeeds
    base = fingerprint(store.load("demo"))
    preview = store.load("demo")
    ops.apply(preview, "add_node", {"name": "cache", "kind": "storage", "parent": "prod-deployment"})
    body |= {"base": base, "result": fingerprint(preview)}
    race_next_commit(store, lambda: store.snapshot_latest("demo", "meanwhile"))
    r = client.post("/api/diagrams/demo/proposal/apply", json=body)
    assert r.status_code == 200 and "cache" in store.load("demo").nodes


def test_a_store_conflict_escaping_a_handler_is_409(client, store, monkeypatch):
    from arcviz.store import ConflictError

    def conflict(*args, **kwargs):
        raise ConflictError("Diagram 'demo' was changed by another request; try again.")

    monkeypatch.setattr(store, "redo", conflict)
    r = client.post("/api/diagrams/demo/redo")
    assert r.status_code == 409 and "try again" in r.json()["detail"]


def test_append_chat_for_an_unknown_diagram_is_refused(store):
    with pytest.raises(StoreError, match="not found"):
        store.append_chat("ghost", [{"role": "user", "content": "x"}])
