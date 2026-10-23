"""Fixes from the owner's review of the live channel (PR #172; spec: docs/architecture/specs/collaboration.md)."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz.live import LiveConfig, LiveHub, Watch, run_stream
from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from arcviz.services import LiveService, ServiceRoot
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity
from tests.conftest import ScriptedProvider, new_loop, proxy_users

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "ed", "vic", "out")}


@pytest.fixture
def world(store):
    """ada owns project "shop" (ed editor, vic viewer) with diagram "orders"; out owns project "secret" with diagram
    "hidden"."""
    hub = LiveHub(LiveConfig(poll_seconds=30))
    root = ServiceRoot(store, hub)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    out = root.for_actor(PEOPLE["out"])
    out.projects.create("Secret", "secret")
    out.diagrams.create("Hidden", "hidden", demo_diagram("hidden", "Hidden"), "secret")
    return root, hub, store


def live(root, name) -> LiveService:
    return root.for_actor(PEOPLE[name]).live


# 1. presence injection through the project chat's `active` -----------------------------------------------


def test_project_chat_presence_never_names_a_diagram_outside_the_project(world):
    root, hub, store = world
    ed = live(root, "ed")
    with ed.chatting(diagram_id="hidden", project_id="shop"):  # another user's diagram
        assert list(hub._chatting.values()) == [{"user": "ed", "name": "Ed", "diagram": None, "project": "shop"}]
    with ed.chatting(diagram_id="orders", project_id="shop"):
        assert list(hub._chatting.values())[0]["diagram"] == "orders"
    root.for_actor(PEOPLE["ada"]).projects.create("Lab", "lab")
    root.for_actor(PEOPLE["ada"]).projects.share("lab", "ed", "editor")
    with ed.chatting(diagram_id="orders", project_id="lab"):  # visible, but not in that project
        assert list(hub._chatting.values())[0]["diagram"] is None


def test_the_project_chat_route_checks_active(store, monkeypatch):
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))  # the start-up puts it into the default project
    users = proxy_users(
        store, monkeypatch, ScriptedProvider([ModelReply(text="ok", tool_calls=[], stop_reason="end_turn")])
    )
    app = users.client.app
    seen: list = []
    original = app.state.hub.chatting

    def spy(user, name, diagram, project):
        seen.append((user, diagram, project))
        return original(user, name, diagram, project)

    monkeypatch.setattr(app.state.hub, "chatting", spy)
    store.create("Fremd", "fremd", demo_diagram("fremd", "Fremd"))  # after the start-up: in no project, nobody sees it
    with users.client.stream(
        "POST",
        "/api/projects/default/chat",
        json={"message": "Hallo", "active": "fremd"},
        headers=users.headers("admin"),
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200, body
    assert seen == [("admin", None, "default")]


# 2. presence updates: throttled, no project-wide scan per call --------------------------------------------


def test_selection_pokes_are_throttled_and_coalesced():
    async def main():
        hub = LiveHub()
        loop = asyncio.get_running_loop()
        watch = Watch("d1", "p1", "w1", "viewer", "r", "pr")
        mine = hub.subscribe("ada", "Ada", "session-a", watch, loop)
        other = hub.subscribe("bob", "Bob", "session-b", watch, loop)
        await asyncio.sleep(0.01)
        while not other.queue.empty():
            other.queue.get_nowait()
        for i in range(20):
            assert hub.select("ada", mine.session, "d1", f"e{i}")
        await asyncio.sleep(0.01)
        early = other.queue.qsize()
        await asyncio.sleep(0.4)  # the trailing poke after the throttle window
        pokes = other.queue.qsize()
        assert early <= 1 and 1 <= pokes <= 2
        assert mine.selection == "e19"

    asyncio.run(main())


def test_a_selection_with_an_open_stream_needs_no_project_scan(world, monkeypatch):
    root, hub, store = world
    vic = live(root, "vic")
    vic.open_diagram("orders", "session-v1", new_loop())
    scans = []
    original = store.list_projects
    monkeypatch.setattr(store, "list_projects", lambda: scans.append(1) or original())
    vic = live(root, "vic")  # a new request: a new guard without cached locations
    vic.select("orders", "session-v1", "service-a")
    assert scans == []


# 3. one read per change for all streams of a diagram; slow re-checks are no stale streams ---------------


def test_rechecks_of_many_streams_share_one_read_per_change(world, monkeypatch):
    root, hub, store = world
    watches = {name: live(root, name).watch_diagram("orders") for name in ("ada", "ed", "vic")}
    loads = []
    original = store.read_revision
    monkeypatch.setattr(store, "read_revision", lambda d: loads.append(d) or original(d))
    root.for_actor(PEOPLE["ed"]).diagrams.apply_operation("orders", "rename_diagram", {"title": "Neu"}, "x")
    loads.clear()
    for name, watch in watches.items():
        assert live(root, name).recheck(watch).revision == original("orders")
    assert loads == ["orders"]
    root.for_actor(PEOPLE["ada"]).projects.revoke("shop", "vic")  # still checked per user
    assert live(root, "vic").recheck(watches["vic"]) is None
    assert live(root, "ed").recheck(watches["ed"]) is not None


def test_a_slow_recheck_does_not_make_its_stream_stale():
    now = [0.0]
    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=5), clock=lambda: now[0])

    async def main():
        loop = asyncio.get_running_loop()
        sub = hub.subscribe("ada", "Ada", "session-a", Watch("d1", "p1", "w1", "editor", "r1", "pr"), loop)
        checks = []

        def recheck(watch):
            if not checks:
                now[0] += 14  # the store is slow (just under the TTL); meanwhile another stream opens
                hub.subscribe("bob", "Bob", "session-b", Watch("d2", "p2", "w1", "viewer", "r", "pr"), loop)
            checks.append(1)
            return watch

        async def direct(fn, *args):
            return fn(*args)

        events = []
        gen = run_stream(hub, sub, recheck, run_sync=direct)
        async for event in gen:
            events.append(event)
            if len(checks) >= 2:
                break
        await gen.aclose()
        assert sub.close_reason is None and not any(e and e.get("type") == "bye" for e in events)

    asyncio.run(main())


# 6. project transcripts are no diagram chat --------------------------------------------------------------


def test_a_project_chat_event_does_not_ask_diagram_streams_to_reload_their_transcript():
    hub = LiveHub(LiveConfig(poll_seconds=30, max_seconds=5))

    async def main():
        loop = asyncio.get_running_loop()
        sub = hub.subscribe("ada", "Ada", "session-a", Watch("d1", "p1", "w1", "editor", "r1", "pr"), loop)

        async def direct(fn, *args):
            return fn(*args)

        events: list[Any] = []
        gen = run_stream(hub, sub, lambda w: w, run_sync=direct)
        async for event in gen:
            events.append(event)
            if len(events) == 2:
                hub.publish({"type": "chat", "project": "p1"})
                hub.publish({"type": "chat", "diagram": "d1"})
            if sum(1 for e in events if e and e["type"] == "chat") == 2:
                break
        await gen.aclose()
        chats = [e for e in events if e and e["type"] == "chat"]
        assert {"type": "chat", "diagram": None, "project": "p1"} in chats  # the diagram UI ignores it
        assert {"type": "chat", "diagram": "d1", "project": "p1"} in chats

    asyncio.run(main())


# 8. LiveService's public methods are classified in tests/test_access.py (with the access matrix of each)


# 9. the app closes its hub on SIGTERM, however it is served ---------------------------------------------


def test_the_app_lifespan_closes_the_hub_on_a_shutdown_signal(store):
    app = create_app(store=store, provider=ScriptedProvider([]))
    called = []
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: called.append(signum))

    async def main():
        async with app.router.lifespan_context(app):
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)

    try:
        asyncio.run(main())
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert app.state.hub.closing and called == [signal.SIGTERM]


# 10. If-Match lists ------------------------------------------------------------------------------------


RENAME = {"op": "rename_diagram", "args": {"title": "Neu"}}


@pytest.mark.parametrize("header", ['"stale", "{rev}"', '"{rev}", "stale"', '"stale", *'])
def test_if_match_matches_any_listed_tag_and_a_wildcard_anywhere(store, header):
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    revision = store.load_with_revision("demo")[1]
    r = client.post("/api/diagrams/demo/ops", json=RENAME, headers={"If-Match": header.format(rev=revision)})
    assert r.status_code == 200, r.text
    r = client.post("/api/diagrams/demo/ops", json=RENAME, headers={"If-Match": '"a", "b"'})
    assert r.status_code == 412


# ---- second review round ------------------------------------------------------------------------------------


def test_shared_reads_are_bounded_and_keep_only_the_revision_of_a_diagram(world):
    import time as _time

    root, hub, store = world
    hub.shared_capacity = 3
    for i in range(10):
        hub.shared("project", f"p{i}", lambda i=i: i)
    assert len(hub._shared) <= 3
    live(root, "vic").watch_diagram("orders")
    cached = hub._shared[("diagram", "orders")].value
    assert isinstance(cached, str)  # the revision only, never the whole diagram
    del _time


def test_a_hanging_recheck_is_bounded_by_the_stream_lifetime():
    import time as _time

    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=0.4))

    async def main():
        loop = asyncio.get_running_loop()
        sub = hub.subscribe("ada", "Ada", "session-a", Watch("d1", "p1", "w1", "editor", "r1", "pr"), loop)
        calls = []

        def recheck(watch):
            calls.append(1)
            if len(calls) == 1:
                _time.sleep(2)  # the store hangs
            return watch

        started = loop.time()
        events = [event async for event in run_stream(hub, sub, recheck)]
        return loop.time() - started, events

    took, events = asyncio.run(main())
    assert took < 1.2 and events[-1]["type"] == "bye"
    assert hub.streams() == 0


def test_the_pruning_exemption_of_a_running_recheck_expires():
    now = [0.0]
    hub = LiveHub(LiveConfig(poll_seconds=1.0), clock=lambda: now[0])
    loop = new_loop()
    hung = hub.subscribe("ada", "Ada", "session-a", Watch("d1", "p1", "w1", "editor", "r", "pr"), loop)
    hung.checking = True
    hung.checking_since = 10.0  # the stream was last seen at 0, its re-check started at 10
    now[0] += 20  # not seen for longer than the TTL (15 s), but exempt while the check is young...
    hub.subscribe("bob", "Bob", "session-b", Watch("d2", "p1", "w1", "viewer", "r", "pr"), loop)
    assert hung.close_reason is None
    now[0] += 20  # ...but not for ever
    hub.subscribe("cid", "Cid", "session-c", Watch("d3", "p1", "w1", "viewer", "r", "pr"), loop)
    assert hung.close_reason == "stale"


def test_an_untargeted_access_event_invalidates_a_read_in_flight():
    import threading

    hub = LiveHub()
    loading, release = threading.Event(), threading.Event()
    results = {}

    def first():
        def load():
            loading.set()
            release.wait(2)
            return "before"

        results["a"] = hub.shared("workspace", "w1", load)

    def second():
        results["b"] = hub.shared("workspace", "w1", lambda: "after")

    a = threading.Thread(target=first)
    a.start()
    loading.wait(2)
    b = threading.Thread(target=second)
    b.start()  # waits for the read in flight
    import time as _time

    _time.sleep(0.1)
    hub.publish({"type": "access"})  # e.g. a member removed somewhere: everything is read again
    release.set()
    a.join(2)
    b.join(2)
    assert results == {"a": "before", "b": "after"}


@pytest.mark.parametrize("header", ['W/"{rev}"', 'W/"{rev}", "stale"'])
def test_if_match_uses_the_strong_comparison(store, header):
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    revision = store.load_with_revision("demo")[1]
    r = client.post("/api/diagrams/demo/ops", json=RENAME, headers={"If-Match": header.format(rev=revision)})
    assert r.status_code == 412  # a weak entity tag never matches (RFC 9110)


def test_a_selection_right_after_a_move_follows_the_diagram(world):
    root, hub, store = world
    vic = live(root, "vic")
    vic.open_diagram("orders", "session-v1", new_loop())
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Lab", "lab")
    ada.projects.share("lab", "vic", "viewer")
    ada.projects.share("lab", "ed", "editor")
    ada.diagrams.move("orders", "lab")
    live(root, "vic").select("orders", "session-v1", "service-a")  # the stream still knows "shop"
    assert hub.find("vic", "session-v1").selection == "service-a"


def test_the_hub_opens_again_with_every_lifespan(store):
    app = create_app(store=store, provider=ScriptedProvider([]))

    async def cycle():
        async with app.router.lifespan_context(app):
            assert not app.state.hub.closing

    asyncio.run(cycle())
    assert app.state.hub.closing
    asyncio.run(cycle())


def test_a_client_gone_before_the_response_starts_leaves_no_subscription(store):
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    app = create_app(store=store, provider=ScriptedProvider([]))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/diagrams/demo/live",
        "raw_path": b"/api/diagrams/demo/live",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "root_path": "",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            raise OSError("client gone")

    async def main():
        with contextlib.suppress(Exception):  # the middleware logs a client gone and returns (M9); no subscription
            await app(scope, receive, send)

    asyncio.run(main())
    assert app.state.hub.streams() == 0


# third review round ------------------------------------------------------------------------------------------


def test_a_selection_after_a_move_is_shown_in_the_new_project_only(world):
    root, hub, store = world
    loop = new_loop()
    vic = live(root, "vic")
    vic.open_diagram("orders", "session-v1", loop)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Lab", "lab")
    ada.projects.share("lab", "vic", "viewer")
    ada.projects.share("lab", "ed", "editor")
    watcher = live(root, "ed").open_project("shop", "session-e1", loop)  # stays in "shop"
    ada.diagrams.move("orders", "lab")
    live(root, "vic").select("orders", "session-v1", "service-a")
    sub = hub.find("vic", "session-v1")
    assert sub.watch.project == "lab" and sub.selection == "service-a"
    assert all(v["user"] != "vic" for v in hub.presence(watcher)["project_viewers"])
    lab = live(root, "ada").open_project("lab", "session-a1", loop)
    assert [v["user"] for v in hub.presence(lab)["project_viewers"]] == ["ada", "vic"]


@pytest.mark.parametrize("ending", ["shutdown", "replaced"])
def test_closing_a_stream_ends_a_hanging_recheck_at_once(ending):
    import time as _time

    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=30))

    async def main():
        loop = asyncio.get_running_loop()
        sub = hub.subscribe("ada", "Ada", "session-a", Watch("d1", "p1", "w1", "editor", "r1", "pr"), loop)
        started = asyncio.Event()

        def recheck(watch):
            loop.call_soon_threadsafe(started.set)
            _time.sleep(1.5)  # the store hangs
            return watch

        async def end():
            await started.wait()
            if ending == "shutdown":
                hub.close()
            else:
                hub.subscribe("ada", "Ada", "session-a", sub.watch, loop)

        closer = asyncio.ensure_future(end())
        begun = loop.time()
        events = [event async for event in run_stream(hub, sub, recheck)]
        await closer
        return loop.time() - begun, events

    took, events = asyncio.run(main())
    assert took < 1.0 and events[-1] == {"type": "bye", "reason": ending}


def test_a_recheck_is_capped_at_the_shared_wait():
    import time as _time

    hub = LiveHub(LiveConfig(poll_seconds=0.05, max_seconds=30))
    hub.shared_wait = 0.3

    async def main():
        loop = asyncio.get_running_loop()
        sub = hub.subscribe("ada", "Ada", "session-a", Watch("d1", "p1", "w1", "editor", "r1", "pr"), loop)

        def recheck(watch):
            _time.sleep(1.5)
            return watch

        begun = loop.time()
        events = [event async for event in run_stream(hub, sub, recheck)]
        return loop.time() - begun, events

    took, events = asyncio.run(main())
    assert took < 1.0 and events[-1] == {"type": "bye", "reason": "error"}


def test_a_stuck_shared_read_never_blocks_its_waiters_for_long():
    import threading
    import time as _time

    from arcviz.live import SharedReadTimeout

    hub = LiveHub()
    hub.shared_wait = 0.2
    loading, release = threading.Event(), threading.Event()

    def stuck():
        loading.set()
        release.wait(5)
        return "late"

    first = threading.Thread(target=lambda: hub.shared("project", "p1", stuck))
    first.start()
    loading.wait(2)
    begun = _time.monotonic()
    with pytest.raises(SharedReadTimeout):
        hub.shared("project", "p1", lambda: "never")  # waits for the read in flight, but not for ever
    assert _time.monotonic() - begun < 1.0
    assert hub.shared("project", "p1", lambda: "fresh") == "fresh"  # the stuck read was replaced
    release.set()
    first.join(2)


def test_eviction_keeps_a_read_in_flight_and_the_generations_bounded():
    import threading

    hub = LiveHub(shared_capacity=1)
    loading, release = threading.Event(), threading.Event()
    loads: list[str] = []
    results: dict[str, Any] = {}

    def slow():
        loads.append("p0")
        loading.set()
        release.wait(5)
        return "v0"

    first = threading.Thread(target=lambda: results.setdefault("a", hub.shared("project", "p0", slow)))
    first.start()
    loading.wait(2)
    for i in range(1, 6):  # far beyond the capacity while p0 is read
        hub.shared("project", f"p{i}", lambda i=i: i)
    second = threading.Thread(
        target=lambda: results.setdefault("b", hub.shared("project", "p0", lambda: loads.append("again") or "v1"))
    )
    second.start()
    release.set()
    first.join(2)
    second.join(2)
    assert loads == ["p0"] and results == {"a": "v0", "b": "v0"}
    for i in range(500):  # events about documents nobody reads leave nothing behind
        hub.publish({"type": "changed", "diagram": f"d{i}"})
    assert len(hub._generations) <= len(hub._shared) <= 2
    with pytest.raises(ValueError):
        LiveHub(shared_capacity=0)
    with pytest.raises(ValueError):
        hub.shared_capacity = 0


# M22 #174: the poll reads the revision, never the diagram ------------------------------------------------------


def test_a_recheck_reads_the_revision_only(world, monkeypatch):
    root, hub, store = world
    vic = live(root, "vic")
    watch = vic.watch_diagram("orders")
    loads = []
    for name in ("load", "load_with_revision", "read_latest"):
        original = getattr(store, name)
        monkeypatch.setattr(store, name, lambda *a, _o=original, _n=name, **k: loads.append(_n) or _o(*a, **k))
    ada = root.for_actor(PEOPLE["ada"])
    ada.diagrams.apply_operation("orders", "add_node", {"name": "Cache", "kind": "service"}, "add")
    loads.clear()
    fresh = vic.recheck(watch)
    assert fresh is not None and fresh.revision == store.read_revision("orders") != watch.revision
    assert loads == []
