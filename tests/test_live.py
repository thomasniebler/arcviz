"""The live hub, the stream loop, LiveService and the services' events (issues #86, #171).

Spec: docs/architecture/specs/collaboration.md, ADR-0095..0098.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from arcviz import live
from arcviz.agent import DiagramAgent
from arcviz.live import LiveConfig, LiveHub, StreamLimit, Watch, run_stream
from arcviz.llm.base import ModelReply
from arcviz.services import ServiceRoot, UserService
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity, personal_workspace_id
from tests.conftest import ScriptedProvider, call, new_loop

W = Watch("d1", "p1", "w1", "editor", "r1", "pr1")


def person(name: str) -> Identity:
    return Identity(name, name.capitalize(), f"{name}@example.org")


async def direct(fn, *args):
    return fn(*args)


class Recorder:
    """An EventSink that records."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def publish(self, event):
        self.events.append(event)


def collect(
    hub: LiveHub, sub, recheck, until=lambda events: False, actions=None, timeout=5.0, run_sync=direct
) -> list[Any]:
    """Run one stream on a fresh loop; `actions(events)` may publish while it runs; stops when `until(events)`."""

    async def main():
        events: list[Any] = []
        sub.loop = asyncio.get_running_loop()  # subscribed outside: deliver on the loop that runs the stream
        gen = run_stream(hub, sub, recheck, run_sync=run_sync)
        try:
            async with asyncio.timeout(timeout):
                async for event in gen:
                    events.append(event)
                    if actions is not None:
                        actions(events)
                    if until(events):
                        break
        finally:
            await gen.aclose()
        return events

    return asyncio.run(main())


def drain(sub) -> list[dict[str, Any]]:
    items = []
    while not sub.queue.empty():
        items.append(sub.queue.get_nowait())
    return items


def subscribe(hub: LiveHub, user="ada", session="session-1", watch=W):
    loop = new_loop()
    return hub.subscribe(user, user.capitalize(), session, watch, loop), loop


# ---- configuration ------------------------------------------------------------------------------------


def test_config_from_the_environment():
    assert LiveConfig.from_env({}) == LiveConfig(5.0, 900.0)
    assert LiveConfig.from_env({"ARCVIZ_LIVE_POLL_SECONDS": "0.5", "ARCVIZ_LIVE_MAX_SECONDS": "60"}) == LiveConfig(
        0.5, 60.0
    )
    for bad in ("x", "0", "-1", "nan"):
        with pytest.raises(ValueError, match="ARCVIZ_LIVE_POLL_SECONDS"):
            LiveConfig.from_env({"ARCVIZ_LIVE_POLL_SECONDS": bad})
    assert LiveConfig(1.0).presence_ttl == 15.0 and LiveConfig(10.0).presence_ttl == 30.0


# ---- the hub ------------------------------------------------------------------------------------------------


def test_publish_reaches_matching_streams_only():
    async def main():
        hub = LiveHub()
        loop = asyncio.get_running_loop()
        a = hub.subscribe("ada", "Ada", "sess-aaaa", W, loop)
        b = hub.subscribe("bob", "Bob", "sess-bbbb", Watch("d2", "p1", "w1", "viewer", "r", "pr"), loop)
        c = hub.subscribe("cid", "Cid", "sess-cccc", Watch(None, "p2", "w2", "viewer", None, "pr"), loop)
        for sub in (a, b, c):
            await sub.next(0.05)  # the presence pokes of the subscriptions
        hub.publish({"type": "changed", "diagram": "d1"})
        hub.publish({"type": "project", "project": "p1"})
        hub.publish({"type": "access", "workspace": "w2"})
        hub.publish({"type": "access"})
        await asyncio.sleep(0)
        got = {name: [e["type"] for e in await sub.next(0.05)] for name, sub in (("a", a), ("b", b), ("c", c))}
        assert got == {
            "a": ["changed", "project", "access"],
            "b": ["project", "access"],
            "c": ["access", "access"],
        }

    asyncio.run(main())


def test_publish_from_another_thread_is_delivered_on_the_loop():
    async def main():
        hub = LiveHub()
        sub = hub.subscribe("ada", "Ada", "sess-aaaa", W, asyncio.get_running_loop())
        await sub.next(0.05)
        thread = threading.Thread(target=hub.publish, args=({"type": "changed", "diagram": "d1"},))
        thread.start()
        items = await sub.next(1.0)
        thread.join()
        assert [i["type"] for i in items] == ["changed"]

    asyncio.run(main())


def test_a_full_queue_marks_an_overflow_instead_of_failing():
    async def main():
        hub = LiveHub()
        sub = hub.subscribe("ada", "Ada", "sess-aaaa", W, asyncio.get_running_loop())
        for _ in range(live.QUEUE_SIZE + 10):
            hub.publish({"type": "changed", "diagram": "d1"})
        await asyncio.sleep(0)
        assert sub.overflow and sub.queue.full()

    asyncio.run(main())


def test_caps_per_user_and_process_and_a_reconnect_replaces():
    hub = LiveHub(max_streams=3, max_streams_per_user=2)
    first, loop = subscribe(hub, "ada", "session-1")
    subscribe(hub, "ada", "session-2")
    with pytest.raises(StreamLimit) as raised:
        subscribe(hub, "ada", "session-3")
    assert raised.value.scope == "user"
    again, _ = subscribe(hub, "ada", "session-1")  # the same session replaces its predecessor
    assert again is not first and first.ended is not None and hub.streams() == 2
    loop.run_until_complete(asyncio.sleep(0))
    assert first.close_reason == "replaced" and {"type": "_wake"} in drain(first)
    subscribe(hub, "bob", "session-1")
    with pytest.raises(StreamLimit) as raised:
        subscribe(hub, "cid", "session-1")
    assert raised.value.scope == "process"


def test_an_invalid_session_gets_a_fresh_one():
    hub = LiveHub()
    sub, _ = subscribe(hub, "ada", "x")
    assert live.SESSION_RE.match(sub.session) and sub.session != "x"


def test_close_ends_every_stream_and_refuses_new_ones():
    hub = LiveHub()
    sub, loop = subscribe(hub)
    hub.close()
    loop.run_until_complete(asyncio.sleep(0))
    assert sub.close_reason == "shutdown"
    with pytest.raises(StreamLimit) as raised:
        subscribe(hub, "bob")
    assert raised.value.scope == "process"


def test_presence_snapshot_selection_linger_and_ttl():
    now = [100.0]
    hub = LiveHub(LiveConfig(poll_seconds=1.0), clock=lambda: now[0])
    ada, _ = subscribe(hub, "ada", "session-a")
    bob, _ = subscribe(hub, "bob", "session-b", Watch("d1", "p1", "w1", "viewer", "r1", "pr1"))
    cid, _ = subscribe(hub, "cid", "session-c", Watch("d2", "p1", "w1", "viewer", "r", "pr"))
    assert hub.select("bob", "session-b", "d1", "svc") is True
    assert hub.select("bob", "session-b", "d2", "svc") is False  # not that diagram
    assert hub.select("ada", "session-b", "d1", "svc") is False  # not ada's stream
    with pytest.raises(ValueError):
        hub.select("bob", "session-b", "d1", "x" * 201)
    snap = hub.presence(ada)
    assert [(v["user"], v["selection"]) for v in snap["viewers"]] == [("ada", None), ("bob", "svc")]
    assert [v["user"] for v in snap["project_viewers"]] == ["ada", "bob", "cid"]
    assert [v["user"] for v in hub.presence(cid)["viewers"]] == ["cid"]
    hub.unsubscribe(bob)
    assert [v["user"] for v in hub.presence(ada)["viewers"]] == ["ada", "bob"]  # lingers
    now[0] += live.LINGER_SECONDS + 1
    ada.seen = cid.seen = now[0]
    assert [v["user"] for v in hub.presence(ada)["viewers"]] == ["ada"]
    now[0] += 16  # cid's stream stopped re-checking (a task that died): dropped after the TTL
    ada.seen = now[0]
    assert [v["user"] for v in hub.presence(ada)["project_viewers"]] == ["ada"]


def test_chatting_is_shown_to_the_diagram_and_the_project():
    hub = LiveHub()
    ada, _ = subscribe(hub, "ada")
    other, _ = subscribe(hub, "bob", "session-2", Watch(None, "p1", "w1", "viewer", None, "pr"))
    with hub.chatting("cid", "Cid", "d1", "p1"):
        assert hub.presence(ada)["chatting"] == [{"user": "cid", "name": "Cid", "diagram": "d1", "project": "p1"}]
        assert hub.presence(other)["chatting"][0]["user"] == "cid"
    assert hub.presence(ada)["chatting"] == []


# ---- the stream loop ------------------------------------------------------------------------------------


def test_a_stream_says_hello_presence_and_ends_after_its_lifetime():
    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=0.2))
    sub, _ = subscribe(hub)
    events = collect(hub, sub, lambda w: w)
    assert events[0] == {
        "type": "hello",
        "session": "session-1",
        "diagram": "d1",
        "project": "p1",
        "revision": "r1",
        "project_revision": "pr1",
        "role": "editor",
        "poll_seconds": 0.05,
    }
    assert events[1]["type"] == "presence" and [v["user"] for v in events[1]["viewers"]] == ["ada"]
    assert None in events  # keep-alives after quiet polls
    assert events[-1] == {"type": "bye", "reason": "expired"}
    assert hub.streams() == 0  # unsubscribed


def test_a_bus_event_triggers_a_recheck_and_a_changed_event_with_the_author():
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=5))
    sub, _ = subscribe(hub)
    state = {"watch": W}

    def actions(events):
        if len(events) == 2:  # after hello + presence: someone writes
            state["watch"] = Watch("d1", "p1", "w1", "editor", "r2", "pr1")
            hub.publish({"type": "changed", "diagram": "d1", "author": "bob", "author_name": "Bob", "origin": "user"})

    events = collect(
        hub, sub, lambda w: state["watch"], until=lambda e: e[-1] and e[-1]["type"] == "changed", actions=actions
    )
    assert events[-1] == {
        "type": "changed",
        "diagram": "d1",
        "revision": "r2",
        "author": "bob",
        "author_name": "Bob",
        "origin": "user",
    }


def test_the_poll_finds_changes_of_other_replicas_without_an_author():
    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=5))
    sub, _ = subscribe(hub)
    reads = iter([Watch("d1", "p2", "w1", "viewer", "r9", "pr7")])
    events = collect(hub, sub, lambda w: next(reads, w), until=lambda e: any(x and x["type"] == "access" for x in e))
    kinds = [e["type"] for e in events if e]
    assert kinds[:5] == ["hello", "presence", "changed", "project", "access"]
    changed = next(e for e in events if e and e["type"] == "changed")
    assert changed["revision"] == "r9" and changed["author"] is None
    assert next(e for e in events if e and e["type"] == "project") == {
        "type": "project",
        "project": "p2",
        "revision": "pr7",
    }
    assert next(e for e in events if e and e["type"] == "access") == {"type": "access", "role": "viewer"}


def test_a_failed_recheck_ends_the_stream_with_gone():
    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=5))
    sub, _ = subscribe(hub)
    events = collect(hub, sub, lambda w: None)
    assert events[-1] == {"type": "gone"} and hub.streams() == 0


def test_an_error_while_rechecking_ends_the_stream_for_a_reconnect(caplog):
    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=5))
    sub, _ = subscribe(hub)

    def broken(watch):
        raise RuntimeError("store down")

    assert collect(hub, sub, broken)[-1] == {"type": "bye", "reason": "error"}


def test_chat_events_and_presence_changes_reach_the_stream():
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=5))
    sub, _ = subscribe(hub)

    def actions(events):
        if len(events) == 2:
            hub.publish({"type": "chat", "diagram": "d1"})
            other = hub.subscribe("bob", "Bob", "session-b", W, sub.loop)
            hub.select("bob", other.session, "d1", "svc")

    events = collect(
        hub,
        sub,
        lambda w: w,
        until=lambda e: any(
            x and x["type"] == "presence" and len(x["viewers"]) == 2 and x["viewers"][1]["selection"] for x in e
        ),
        actions=actions,
    )
    assert {"type": "chat", "diagram": "d1", "project": "p1"} in events


def test_shutdown_ends_an_open_stream_at_once():
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=30))
    sub, _ = subscribe(hub)

    def actions(events):
        if len(events) == 2:
            hub.close()

    events = collect(hub, sub, lambda w: w, actions=actions, timeout=2)
    assert events[-1] == {"type": "bye", "reason": "shutdown"}


# ---- LiveService and the services' events ---------------------------------------------------------------


@pytest.fixture
def world(store):
    """ada owns project "shop" with "orders", shared with ed (editor) and vic (viewer)."""
    hub = LiveHub(LiveConfig(poll_seconds=30))
    root = ServiceRoot(store, hub)
    users = UserService(store)
    for name in ("ada", "ed", "vic", "out"):
        users.seen(person(name))
    ada = root.for_actor(person("ada"))
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    return root, hub, store


def test_watch_and_recheck(world):
    root, hub, store = world
    vic = root.for_actor(person("vic")).live
    watch = vic.watch_diagram("orders")
    assert (watch.diagram, watch.project, watch.workspace, watch.role) == (
        "orders",
        "shop",
        personal_workspace_id("ada"),
        "viewer",
    )
    assert watch.revision == store.load_with_revision("orders")[1]
    with pytest.raises(Exception, match="not found"):
        root.for_actor(person("out")).live.watch_diagram("orders")
    with pytest.raises(Exception, match="not found"):
        root.for_actor(person("out")).live.watch_project("shop")
    root.for_actor(person("ed")).diagrams.apply_operation("orders", "rename_diagram", {"title": "O"}, "x")
    again = vic.recheck(watch)
    assert again is not None and again.revision != watch.revision and again.project_revision == watch.project_revision


def test_recheck_ends_for_a_revoked_share_a_deleted_or_moved_away_diagram(world):
    root, hub, store = world
    ada = root.for_actor(person("ada"))
    vic = root.for_actor(person("vic")).live
    watch = vic.watch_diagram("orders")
    ada.projects.create("Lab", "lab")
    ada.projects.share("lab", "vic", "viewer")
    ada.diagrams.move("orders", "lab")  # moved within sight: the stream follows
    moved = vic.recheck(watch)
    assert moved is not None and moved.project == "lab"
    ada.projects.create("Secret", "secret")  # not shared with vic
    ada.diagrams.move("orders", "secret")  # moved out of sight
    assert vic.recheck(moved) is None
    ada.diagrams.move("orders", "shop")
    watch = vic.watch_diagram("orders")
    ada.projects.revoke("shop", "vic")
    assert vic.recheck(watch) is None
    ed = root.for_actor(person("ed")).live
    watch = ed.watch_diagram("orders")
    ada.diagrams.delete("orders")
    assert ed.recheck(watch) is None


def test_a_role_change_is_seen_by_the_recheck(world):
    root, hub, store = world
    ed = root.for_actor(person("ed")).live
    watch = ed.watch_diagram("orders")
    root.for_actor(person("ada")).projects.share("shop", "ed", "viewer")
    assert ed.recheck(watch).role == "viewer"


def test_presence_needs_view_access_and_an_open_stream(world):
    root, hub, store = world
    vic = root.for_actor(person("vic")).live
    loop = new_loop()
    from arcviz.services import UnknownSession

    with pytest.raises(UnknownSession):
        vic.select("orders", "session-v1", "svc")
    sub = vic.open_diagram("orders", "session-v1", loop)
    vic.select("orders", "session-v1", "service-a")
    assert sub.selection == "service-a"
    with pytest.raises(Exception, match="not found"):
        root.for_actor(person("out")).live.select("orders", "session-v1", None)
    with root.for_actor(person("ed")).live.chatting(diagram_id="orders"):
        assert hub.presence(sub)["chatting"] == [{"user": "ed", "name": "Ed", "diagram": "orders", "project": "shop"}]


def test_the_services_publish_their_writes(world):
    root, hub, store = world
    recorder = Recorder()
    root.hub = recorder  # type: ignore[assignment]  # the services get the sink from the root
    ed = root.for_actor(person("ed"))
    ed.diagrams.apply_operation("orders", "rename_diagram", {"title": "O"}, "rename")
    ed.diagrams.snapshot("orders", "snap")
    ed.diagrams.undo("orders")
    ed.diagrams.redo("orders")
    ed.diagrams.restore("orders", 1)
    ed.diagrams.append_chat("orders", [{"role": "user", "content": "hi"}])
    ed.diagrams.clear_chat("orders")
    ed.projects.apply("shop", "add_section", {"title": "Neu"})
    root.for_actor(person("ada")).projects.revoke("shop", "vic")
    root.for_actor(person("ada")).diagrams.delete("orders")
    changed = [e for e in recorder.events if e["type"] == "changed"]
    assert len(changed) == 5 and all(
        e == {"type": "changed", "diagram": "orders", "author": "ed", "author_name": "Ed", "origin": "user"}
        for e in changed
    )
    assert {"type": "chat", "diagram": "orders"} in recorder.events
    assert {"type": "project", "project": "shop"} in recorder.events
    assert {"type": "access", "diagram": "orders"} in recorder.events


def test_an_agent_turn_publishes_its_live_writes(world):
    root, hub, store = world
    recorder = Recorder()
    root.hub = recorder  # type: ignore[assignment]
    ed = root.for_actor(person("ed"))
    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("rename_diagram", "c1", title="Neu")], stop_reason="tool_use"),
            ModelReply(text="ok", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    agent = DiagramAgent(provider, ed.diagrams, models=ed.models, compact_after=0)
    list(agent.chat("orders", "Umbenennen"))
    origins = [e["origin"] for e in recorder.events if e["type"] == "changed"]
    assert origins == ["assistant", "assistant"]  # write_through, then commit_turn
    assert {"type": "chat", "diagram": "orders"} in recorder.events


def test_an_unguarded_root_publishes_nothing_and_needs_no_hub(store):
    root = ServiceRoot(store)
    services = root.for_actor(person("admin"))
    with pytest.raises(RuntimeError):
        services.live.open_diagram("demo", None, new_loop())


def test_sharing_publishes_a_project_event_for_the_streams(world):
    root, hub, store = world
    recorder = Recorder()
    root.hub = recorder  # type: ignore[assignment]
    ada = root.for_actor(person("ada")).projects
    ada.share("shop", "out", "viewer")
    ada.revoke("shop", "out")
    assert recorder.events == [{"type": "project", "project": "shop"}] * 2


def test_a_change_the_poll_saw_first_is_attributed_when_its_bus_event_arrives():
    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=5))
    sub, _ = subscribe(hub)
    newer = Watch("d1", "p1", "w1", "editor", "r2", "pr1")
    published = []

    def actions(events):
        changed = [e for e in events if e and e["type"] == "changed"]
        if len(changed) == 1 and not published:  # the poll saw r2 first, without an author
            assert changed[0]["author"] is None
            published.append(1)
            hub.publish({"type": "changed", "diagram": "d1", "author": "bob", "author_name": "Bob", "origin": "user"})

    events = collect(
        hub,
        sub,
        lambda w: newer,
        until=lambda e: sum(1 for x in e if x and x["type"] == "changed") == 2,
        actions=actions,
    )
    changed = [e for e in events if e and e["type"] == "changed"]
    assert [c["revision"] for c in changed] == ["r2", "r2"] and changed[1]["author_name"] == "Bob"


def test_a_full_queue_never_loses_the_end_of_a_stream():
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=30))
    sub, _ = subscribe(hub)

    def actions(events):
        if len(events) == 2:
            for _ in range(live.QUEUE_SIZE + 5):
                hub.publish({"type": "chat", "diagram": "d1"})
            hub.close()

    events = collect(hub, sub, lambda w: w, actions=actions, timeout=3)
    assert events[-1] == {"type": "bye", "reason": "shutdown"}


def test_a_stream_whose_loop_stopped_is_dropped_and_ended():
    now = [0.0]
    hub = LiveHub(LiveConfig(poll_seconds=1.0), clock=lambda: now[0])
    hung, _ = subscribe(hub, "ada", "session-a")
    now[0] += 20  # its loop has not woken up for longer than the TTL (15 s)
    fresh, _ = subscribe(hub, "bob", "session-b")
    assert hung.close_reason == "stale" and hub.streams() == 1
    assert [v["user"] for v in hub.presence(fresh)["viewers"]] == ["bob"]


def test_presence_follows_a_stream_whose_diagram_moved_to_another_project():
    hub = LiveHub()
    ada, _ = subscribe(hub, "ada", "session-a")
    other, _ = subscribe(hub, "bob", "session-b", Watch(None, "p2", "w1", "viewer", None, "x"))
    assert [v["user"] for v in hub.presence(other)["project_viewers"]] == ["bob"]
    hub.update_watch(ada, Watch("d1", "p2", "w1", "editor", "r1", "y"))
    assert [v["user"] for v in hub.presence(other)["project_viewers"]] == ["ada", "bob"]
    hub.unsubscribe(ada)
    again, _ = subscribe(hub, "ada", "session-a", Watch("d9", "p3", "w1", "editor", "r", "z"))  # a reconnect
    assert [v["user"] for v in hub.presence(other)["project_viewers"]] == ["bob"]
    assert [v["user"] for v in hub.presence(again)["viewers"]] == ["ada"]


def test_a_transient_read_failure_is_no_gone(world, monkeypatch):
    from arcviz.ports import StoreError

    root, hub, store = world
    vic = root.for_actor(person("vic")).live
    watch = vic.watch_diagram("orders")
    original = store.read_revision
    calls = []

    def flaky(diagram_id):
        calls.append(diagram_id)
        if len(calls) == 1:
            raise StoreError("Cosmos DB read failed: timeout")
        return original(diagram_id)

    monkeypatch.setattr(store, "read_revision", flaky)
    hub.publish({"type": "changed", "diagram": "orders"})  # the shared read is stale: the next re-check reads
    with pytest.raises(StoreError, match="timeout"):  # the stream ends with `bye: error`, the client reconnects
        vic.recheck(watch)
    assert vic.recheck(watch) is not None


# ---- versioned watches (#176, ADR-0105) ---------------------------------------------------------------------


def test_a_watch_update_based_on_an_older_version_is_refused():
    hub = LiveHub()
    ada, _ = subscribe(hub, "ada", "session-a")
    other, _ = subscribe(hub, "bob", "session-b", Watch(None, "p1", "w1", "viewer", None, "x"))
    started = ada.watch_version  # a re-check starts here ...
    moved = Watch("d1", "p2", "w1", "editor", "r1", "y")
    assert hub.update_watch(ada, moved) is True  # ... the diagram moves meanwhile (a selection re-points the stream)
    stale = Watch("d1", "p1", "w1", "editor", "r1", "pr1")
    assert hub.update_watch(ada, stale, based_on=started) is False  # the stale re-check never wins
    assert ada.watch == moved and ada.watch_version == started + 1
    assert [v["user"] for v in hub.presence(other)["project_viewers"]] == ["bob"]  # not back in the old project
    assert hub.update_watch(ada, stale, based_on=ada.watch_version) is True  # a re-check of the newest watch may


def test_a_stale_recheck_is_dropped_and_the_stream_checks_the_newer_watch():
    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=5))
    sub, _ = subscribe(hub)
    moved = Watch("d1", "p2", "w1", "editor", "r1", "pr2")
    seen: list[Watch] = []

    def recheck(watch):
        seen.append(watch)
        if len(seen) == 1:  # the diagram moves while this (first) re-check runs
            hub.update_watch(sub, moved)
            return W  # what the re-check read before the move: the old project
        return watch

    events = collect(hub, sub, recheck, until=lambda e: len(seen) >= 2 and any(x and x["type"] == "project" for x in e))
    assert seen[1] == moved  # the next re-check starts from the newer watch, not from the stale result
    assert sub.watch.project == "p2"
    assert {"type": "project", "project": "p2", "revision": "pr2"} in events


# ---- #218: a delete always ends the stream with `gone` ----------------------------------------------------------


def slow(seconds: float):
    async def run(fn, *args):
        await asyncio.sleep(seconds)
        return fn(*args)

    return run


def test_an_event_is_checked_in_full_even_when_the_lifetime_ends_meanwhile():
    """#218: the re-check of a delete started 0.1 s before the end of the lifetime and took longer; it was cut to the
    rest of the lifetime and the stream ended with `bye expired`, so the client never heard `gone`."""
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=0.3))
    sub, _ = subscribe(hub)

    def actions(events):
        if len(events) == 2:
            hub.publish({"type": "access", "diagram": "d1"})  # the delete

    events = collect(hub, sub, lambda w: None, actions=actions, run_sync=slow(0.4))
    assert events[-1] == {"type": "gone"}


def test_a_delete_pending_at_the_end_of_the_lifetime_is_still_reported():
    """#218: a delete that arrived while another re-check ran past the lifetime was dropped by `bye expired`."""
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=0.2))
    sub, _ = subscribe(hub)
    state = {"deleted": False}

    def recheck(watch):
        if not state["deleted"]:  # the first re-check (a change): meanwhile the diagram is deleted
            state["deleted"] = True
            hub.publish({"type": "access", "diagram": "d1"})
            return watch
        return None

    def actions(events):
        if len(events) == 2:
            hub.publish({"type": "changed", "diagram": "d1", "author": "bob", "author_name": "Bob", "origin": "user"})

    events = collect(hub, sub, recheck, actions=actions, run_sync=slow(0.3))
    assert events[-1] == {"type": "gone"}


def test_a_quiet_stream_still_ends_with_its_lifetime():
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=0.2))
    sub, _ = subscribe(hub)
    assert collect(hub, sub, lambda w: w)[-1] == {"type": "bye", "reason": "expired"}
