"""Presence across replicas with Cosmos DB (issue #173; spec: docs/architecture/specs/collaboration.md, ADR-0104).

Two hubs stand for two replicas of the app on one Cosmos container (the in-process fake). Each replica writes one
presence item per project it has streams for (TTL, refreshed by a heartbeat) and reads the items of the other
replicas once per poll and project (`LiveHub.shared`), whatever the number of streams. A stream sees remote entries
exactly like local ones: only those of its own project, which it was access-checked for.
"""

from __future__ import annotations

import time

import pytest

from arcviz import live
from arcviz.live import LiveConfig, LiveHub, Watch
from arcviz.services import ServiceRoot
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from tests.conftest import new_loop
from tests.cosmos_fake import FakeContainer

P1 = Watch("d1", "p1", "w1", "editor", "r1", "pr1")


class Clocks:
    """A monotonic and a wall clock that the test moves forward together."""

    def __init__(self):
        self.mono = 100.0
        self.wall = time.time()  # the store filters expired items by the real wall clock

    def tick(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds


@pytest.fixture
def cosmos():
    return CosmosStore(FakeContainer())


def replica(store, name: str, clocks: Clocks, poll: float = 1.0) -> LiveHub:
    hub = LiveHub(LiveConfig(poll_seconds=poll), clock=lambda: clocks.mono, wall=lambda: clocks.wall, replica=name)
    hub.share_presence(store.presence_channel(), start=False)  # the test drives the relay by hand: sync_presence()
    return hub


def subscribe(hub: LiveHub, user: str, session: str, watch: Watch = P1):
    loop = new_loop()
    return hub.subscribe(user, user.capitalize(), session, watch, loop)


def users(entries) -> list[str]:
    return [e["user"] for e in entries]


def test_viewers_and_selections_of_another_replica_are_shown(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    ada = subscribe(a, "ada", "session-a")
    bob = subscribe(b, "bob", "session-b")
    assert a.select("ada", "session-a", "d1", "svc")
    a.sync_presence()
    b.sync_presence()
    snap = b.presence(bob)
    assert [(v["user"], v["selection"]) for v in snap["viewers"]] == [("ada", "svc"), ("bob", None)]
    assert users(snap["project_viewers"]) == ["ada", "bob"]
    clocks.tick(0.6)  # a read p1 before b wrote; its shared read is kept for half a poll
    a.sync_presence()  # a reads b's entry in turn
    assert users(a.presence(ada)["viewers"]) == ["ada", "bob"]


def test_remote_entries_reach_only_streams_of_the_same_project(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    subscribe(a, "ada", "session-a")
    carol = subscribe(b, "carol", "session-c", Watch("d7", "p2", "w2", "owner", "r", "pr"))
    a.sync_presence()
    b.sync_presence()
    snap = b.presence(carol)
    assert users(snap["viewers"]) == ["carol"] and users(snap["project_viewers"]) == ["carol"]
    assert b.remote_projects() == {"p2"}  # nothing of p1 is even read on b: no stream of b watches it


def test_one_query_per_project_and_poll_whatever_the_number_of_streams(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    subscribe(a, "ada", "session-a")
    for n in range(5):
        subscribe(b, f"user{n}", f"session-{n:02d}")
    a.sync_presence()
    container = cosmos.container
    before = len(container.queries)
    b.sync_presence()
    b.sync_presence()  # within half a poll: the shared read is reused
    assert len(container.queries) - before == 1
    clocks.tick(0.6)
    b.sync_presence()
    assert len(container.queries) - before == 2


def test_a_replica_writes_on_change_and_on_the_heartbeat_only(cosmos):
    clocks = Clocks()
    a = replica(cosmos, "replica-a", clocks)
    writes = []
    channel = a.presence_channel
    original = channel.write_presence

    def counting(*args, **kwargs):
        writes.append(args)
        return original(*args, **kwargs)

    channel.write_presence = counting  # type: ignore[method-assign]
    subscribe(a, "ada", "session-a")
    a.sync_presence()
    a.sync_presence()
    assert len(writes) == 1  # nothing changed
    assert a.select("ada", "session-a", "d1", "svc")
    a.sync_presence()
    assert len(writes) == 2  # the selection changed
    clocks.tick(a.heartbeat_seconds + 0.1)
    a.select("ada", "session-a", "d1", "svc")  # same value: no change, but the item is due for its heartbeat
    a.sync_presence()
    assert len(writes) == 3


def test_an_ended_stream_leaves_the_other_replica_after_lingering(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    ada = subscribe(a, "ada", "session-a")
    bob = subscribe(b, "bob", "session-b")
    a.sync_presence()
    b.sync_presence()
    a.unsubscribe(ada)
    clocks.tick(live.LINGER_SECONDS + 1)
    bob.seen = clocks.mono
    a.sync_presence()  # the item is deleted: a has nobody left in p1
    assert all(i["replica"] != "replica-a" for i in cosmos.read_presence("p1"))
    clocks.tick(0.6)
    b.sync_presence()
    assert users(b.presence(bob)["viewers"]) == ["bob"]


def test_a_replica_that_stopped_expires_after_the_ttl(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    subscribe(a, "ada", "session-a")
    bob = subscribe(b, "bob", "session-b")
    a.sync_presence()
    b.sync_presence()
    assert "ada" in users(b.presence(bob)["viewers"])
    clocks.tick(b.config.presence_ttl + 1)  # a crashed: no heartbeat any more
    bob.seen = clocks.mono
    b.sync_presence()
    assert users(b.presence(bob)["viewers"]) == ["bob"]


def test_a_stream_that_moved_to_another_project_leaves_the_old_one_everywhere(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    ada = subscribe(a, "ada", "session-a")
    bob = subscribe(b, "bob", "session-b", Watch(None, "p1", "w1", "viewer", None, "pr1"))
    a.sync_presence()
    b.sync_presence()
    assert "ada" in users(b.presence(bob)["project_viewers"])
    a.update_watch(ada, Watch("d1", "p2", "w1", "editor", "r1", "x"))
    a.sync_presence()  # rewritten at once: p1 without ada (deleted), p2 with her
    clocks.tick(0.6)
    b.sync_presence()
    assert users(b.presence(bob)["project_viewers"]) == ["bob"]


def test_chatting_is_shared_across_replicas(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    subscribe(a, "ada", "session-a")
    bob = subscribe(b, "bob", "session-b")
    with a.chatting("ada", "Ada", "d1", "p1"):
        a.sync_presence()
        b.sync_presence()
        assert b.presence(bob)["chatting"] == [{"user": "ada", "name": "Ada", "diagram": "d1", "project": "p1"}]


def test_a_reconnect_to_another_replica_is_shown_once(cosmos):
    clocks = Clocks()
    a, b = replica(cosmos, "replica-a", clocks), replica(cosmos, "replica-b", clocks)
    old = subscribe(a, "ada", "session-a")
    a.unsubscribe(old)  # lingers on a
    subscribe(b, "ada", "session-a")  # the same tab, now on b
    a.sync_presence()
    b.sync_presence()
    viewers = b.presence(b.find("ada", "session-a"))["viewers"]
    assert [(v["user"], v["session"]) for v in viewers] == [("ada", "session-a")]


def test_shutdown_removes_the_replicas_items(cosmos):
    clocks = Clocks()
    a = replica(cosmos, "replica-a", clocks)
    subscribe(a, "ada", "session-a")
    a.sync_presence()
    assert [i["replica"] for i in cosmos.read_presence("p1")] == ["replica-a"]
    a.close()
    assert cosmos.read_presence("p1") == []


def test_a_failing_channel_never_breaks_presence(cosmos, monkeypatch, caplog):
    clocks = Clocks()
    a = replica(cosmos, "replica-a", clocks)
    ada = subscribe(a, "ada", "session-a")

    def down(*args, **kwargs):
        raise RuntimeError("Cosmos DB unavailable")

    monkeypatch.setattr(a.presence_channel, "write_presence", down)
    monkeypatch.setattr(a.presence_channel, "read_presence", down)
    a.sync_presence()  # logged, not raised
    assert users(a.presence(ada)["viewers"]) == ["ada"]  # local presence is unaffected


def test_the_relay_thread_shares_presence_without_a_driver(cosmos):
    a = LiveHub(LiveConfig(poll_seconds=0.1), replica="replica-a")
    b = LiveHub(LiveConfig(poll_seconds=0.1), replica="replica-b")
    a.share_presence(cosmos.presence_channel())
    b.share_presence(cosmos.presence_channel())
    try:
        subscribe(a, "ada", "session-a")
        bob = subscribe(b, "bob", "session-b")
        deadline = time.monotonic() + 5
        while "ada" not in users(b.presence(bob)["viewers"]):
            assert time.monotonic() < deadline, "the relay never brought ada over"
            time.sleep(0.05)
    finally:
        a.close()
        b.close()


# ---- the Cosmos items -----------------------------------------------------------------------------------------


def test_presence_items_live_in_one_partition_per_project_with_a_ttl(cosmos):
    cosmos.write_presence("p1", "replica-a", {"replica": "replica-a", "expires": time.time() + 30, "viewers": []}, 30)
    cosmos.write_presence("p1", "replica-b", {"replica": "replica-b", "expires": time.time() + 30, "viewers": []}, 30)
    items = {key: item for key, item in cosmos.container.items.items() if key[0] == "presence:p1"}
    assert sorted(k[1] for k in items) == ["replica-a", "replica-b"]
    assert all(item["ttl"] == 30 and item["type"] == "presence" for item in items.values())
    assert sorted(i["replica"] for i in cosmos.read_presence("p1")) == ["replica-a", "replica-b"]
    cosmos.delete_presence("p1", "replica-a")
    cosmos.delete_presence("p1", "replica-a")  # gone already: fine
    assert [i["replica"] for i in cosmos.read_presence("p1")] == ["replica-b"]
    assert cosmos.list_diagrams() == [] and cosmos.list_projects() == []  # never taken for data


def test_expired_items_are_not_returned_and_old_ones_are_collected(cosmos):
    now = time.time()
    cosmos.write_presence("p1", "fresh", {"replica": "fresh", "expires": now + 30}, 30)
    cosmos.write_presence("p1", "expired", {"replica": "expired", "expires": now - 1}, 30)
    cosmos.write_presence("p1", "ancient", {"replica": "ancient", "expires": now - 1000}, 30)  # no container TTL
    assert [i["replica"] for i in cosmos.read_presence("p1")] == ["fresh"]
    left = sorted(k[1] for k in cosmos.container.items if k[0] == "presence:p1")
    assert left == ["expired", "fresh"]  # far past its TTL: deleted by the reader (best effort)


def test_file_and_sqlite_keep_presence_in_memory(tmp_path):
    hub = LiveHub()
    ServiceRoot(FileStore(tmp_path / "data"), hub)
    assert hub.presence_channel is None
    shared = LiveHub()
    ServiceRoot(CosmosStore(FakeContainer()), shared)
    assert shared.presence_channel is not None
    shared.close()
