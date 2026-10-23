"""Pending locator tokens of writers that died are cleared, without clocks across replicas (#189, ADR-0133).

A token names the writer incarnation that issued it (`<boot>.<random>`); every running process keeps a keyed document
of kind `writer` alive (a heartbeat: a conditional write that changes its revision). Another process declares a writer
dead only after seeing the *same* revision for a whole lease on its own monotonic clock, and only with a conditional
write on that revision (a fence the dead writer cannot pass: its next heartbeat fails and it stops placing). Tokens of
a writer that stopped cleanly or was declared dead are settled; tokens of a live writer and tokens from before M24
never are, except by the operator command in a maintenance window."""

from __future__ import annotations

import pytest

from arcviz.ports import ConflictError, StoreError
from arcviz.projects import new_project
from arcviz.services import ProjectService
from arcviz.services import locations as loc
from arcviz.services.writers import Heartbeat, Reaper, WriterConfig, settle_pending
from arcviz.templates import demo_diagram

LEASE = WriterConfig(heartbeat=1.0).lease  # 10 heartbeats


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def world(store):
    """Project "shop" exists; diagram "orders" exists but is in no project yet."""
    store.create_project(new_project("Shop", "shop", "", None))
    store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    return store


def writer_process(store, clock: Clock) -> Heartbeat:
    """A writer process: its own incarnation (boot id), registered with a heartbeat document."""
    heartbeat = Heartbeat(store, store, WriterConfig(heartbeat=1.0), clock=clock, writer=loc.Writer(clock=clock))
    heartbeat.register()
    return heartbeat


def crashed_placement(store, heartbeat: Heartbeat, project: str = "shop") -> str:
    """A placement that listed `project` as pending and then crashed: no membership, no settle."""
    with loc.writing_as(store, heartbeat.writer):
        return loc.add_location(store, "orders", project)


def watch(reaper: Reaper, clock: Clock, seconds: float, step: float = 1.0):
    """The reaper's thread: a pass now and one per heartbeat (1 s here) for `seconds`; returns the last report."""
    report = reaper.observe()
    for _ in range(round(seconds / step)):
        clock.advance(step)
        report = reaper.observe()
    return report


def pending(store) -> dict[str, list[str]]:
    entry = loc.read_location(store, "orders")
    return entry.pending if entry is not None else {}


def listed(store) -> list[str]:
    entry = loc.read_location(store, "orders")
    return entry.projects if entry is not None else []


# ---- tokens --------------------------------------------------------------------------------------------------------


def test_a_token_names_the_writer_incarnation_and_legacy_tokens_name_none():
    writer = loc.Writer()
    token = writer.token()
    assert loc.boot_of(token) == writer.boot and token.startswith(writer.boot + ".")
    assert loc.boot_of("0123456789abcdef") is None  # an M22 token: secrets.token_hex(8), no incarnation
    assert loc.boot_of("x.y") is None and loc.boot_of("") is None
    assert loc.Writer().boot != writer.boot  # every process start is a new incarnation


def test_a_writer_that_could_not_renew_for_half_a_lease_issues_no_tokens(world):
    clock = Clock()
    a = writer_process(world, clock)
    a.writer.token()  # fresh
    clock.advance(LEASE / 2 + 0.1)  # no successful heartbeat since
    with pytest.raises(StoreError, match="heartbeat"):
        a.writer.token()
    a.beat()
    a.writer.token()  # renewed: tokens again


# ---- a crashed writer is settled, a live one never ---------------------------------------------------------------


def test_the_tokens_of_a_crashed_writer_are_settled_after_a_lease_on_the_observers_clock(world):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    crashed_placement(world, a)
    assert listed(world) == ["shop"] and pending(world)["shop"]
    with pytest.raises(ConflictError):  # M22: a create of this id fails closed while the token is there
        ProjectService(world, world).vacate("orders")
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    watch(b, b_clock, LEASE - 1)  # from the first sight of a's heartbeat
    assert pending(world)["shop"]  # not a whole lease yet: never settled early
    b_clock.advance(1.0)
    report = b.observe()
    assert report.dead == [a.writer.boot] and report.settled == 1
    assert pending(world) == {} and listed(world) == []  # the project does not hold it: dropped from the entry
    ProjectService(world, world).vacate("orders")  # the id is usable again
    assert [k for k, _, _ in world.list_documents(loc.WRITER)] == [a.writer.boot]  # kept for another lease
    watch(b, b_clock, LEASE)
    assert world.list_documents(loc.WRITER) == []  # then dropped


def test_a_token_published_after_the_fence_is_still_settled(world):
    """Codex review on #199: a placement paused after taking its token (before the fence) publishes it after the
    settlement; the writer document is still there, so the next pass settles it instead of leaving an unknown token."""
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    late = a.writer.token()  # taken before the fence, not yet written
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    assert watch(b, b_clock, LEASE).dead == [a.writer.boot]
    loc._update(world, "orders", lambda entry: (["shop"], {"shop": [late]}))  # the paused write lands now
    b_clock.advance(1.0)
    assert b.observe().settled == 0  # no scan per tick: the final pass comes a lease after the first one
    watch(b, b_clock, LEASE)
    assert pending(world) == {}
    assert [k for k, _, _ in world.list_documents(loc.WRITER)] == [a.writer.boot]  # a late token: another lease
    watch(b, b_clock, LEASE)
    assert world.list_documents(loc.WRITER) == []


def test_a_heartbeat_whose_create_answer_was_lost_adopts_its_document(world, monkeypatch):
    """Codex review on #199: the create was written but its answer lost; registering again adopts the document, so
    the writer renews it (and notices a fence) instead of retrying the create for ever."""
    clock = Clock()
    a = Heartbeat(world, world, WriterConfig(heartbeat=1.0), clock=clock, writer=loc.Writer(clock=clock))
    real = world.create_document

    def lost_answer(kind, key, body):
        real(kind, key, body)
        raise StoreError("timeout")

    monkeypatch.setattr(world, "create_document", lost_answer)
    a.register()  # the create raised, the read-back finds the document: adopted
    monkeypatch.setattr(world, "create_document", real)
    _, revision = world.read_document(loc.WRITER, a.writer.boot)
    assert a.revision == revision and a.writer.lease == LEASE
    a.beat()
    assert world.read_document(loc.WRITER, a.writer.boot)[0]["beats"] == 1 and not a.writer.fenced


def test_a_heartbeat_that_finds_its_document_fenced_on_registering_stops(world):
    clock = Clock()
    a = Heartbeat(world, world, WriterConfig(heartbeat=1.0), clock=clock, writer=loc.Writer(clock=clock))
    world.create_document(loc.WRITER, a.writer.boot, {"boot": a.writer.boot, "state": "dead", "beats": 0})
    a.register()
    assert a.writer.fenced


def test_a_live_writer_is_never_settled(world):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    token = crashed_placement(world, a)  # in flight (a slow placement), not crashed
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    for _ in range(30):  # three leases on b's clock, a beating all along
        b.observe()
        b_clock.advance(LEASE / 10)
        a_clock.advance(LEASE / 10)
        a.beat()
    assert token in pending(world)["shop"]
    body, _ = world.read_document(loc.WRITER, a.writer.boot)
    assert body["state"] == "alive"


def test_a_project_that_holds_the_diagram_stays_listed_when_its_token_is_settled(world):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    crashed_placement(world, a)
    ProjectService(world, world).apply_all("shop", [("add_diagram", {"diagram": "orders"})], internal=True)
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    watch(b, b_clock, LEASE)
    assert pending(world) == {} and listed(world) == ["shop"]  # the superset invariant holds


def test_the_fenced_writer_stops_placing_and_relists_what_it_wrote(world):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    token = crashed_placement(world, a)  # a pauses (no heartbeat) right after listing "shop"
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    watch(b, b_clock, LEASE)  # declared dead: the token is settled, "shop" dropped (it does not hold the diagram yet)
    assert listed(world) == []
    # a resumes: its membership write lands, then it settles; the settle lists the project again (re-list)
    ProjectService(world, world).apply_all("shop", [("add_diagram", {"diagram": "orders"})], internal=True)
    with loc.writing_as(world, a.writer):
        loc.settle_location(world, "orders", "shop", token, relist=True)
    assert listed(world) == ["shop"]
    a.beat()  # its heartbeat finds the fence
    assert a.writer.fenced
    with loc.writing_as(world, a.writer), pytest.raises(StoreError, match="restart"):
        loc.add_location(world, "orders", "shop")


def test_a_cleanly_stopped_writer_is_settled_at_once(world):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    token = crashed_placement(world, a)
    a.writer.abandon(token, "orders", "shop")  # the placement ended, its settle failed: nothing more is written
    a.close()  # nothing in flight: the document says "stopped"
    body, _ = world.read_document(loc.WRITER, a.writer.boot)
    assert body["state"] == "stopped" and a.writer.fenced
    report = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16).observe()  # no waiting
    assert report.stopped == [a.writer.boot] and pending(world) == {}


def test_a_writer_with_a_placement_in_flight_does_not_claim_to_have_stopped(world):
    clock = Clock()
    a = writer_process(world, clock)
    crashed_placement(world, a)  # issued, neither settled nor abandoned: still in flight
    a.close(wait=0.0)
    body, _ = world.read_document(loc.WRITER, a.writer.boot)
    assert body["state"] == "alive"  # left to the lease


def test_an_abandoned_token_is_settled_by_the_writers_own_heartbeat(world, monkeypatch):
    clock = Clock()
    a = writer_process(world, clock)
    real = loc._update
    calls = []

    def failing_once(documents, diagram_id, change):
        calls.append(diagram_id)
        if len(calls) == 2:  # the settle
            raise StoreError("throttled")
        return real(documents, diagram_id, change)

    monkeypatch.setattr(loc, "_update", failing_once)
    with loc.writing_as(world, a.writer):
        token = loc.add_location(world, "orders", "shop")
        loc.settle_location(world, "orders", "shop", token)
    assert token in pending(world)["shop"] and token in a.writer.abandoned
    a.tick()
    assert pending(world) == {} and not a.writer.abandoned


def test_failed_reads_settle_nothing(world, monkeypatch):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    crashed_placement(world, a)
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    watch(b, b_clock, LEASE - 1)
    b_clock.advance(1.0)
    real = world.list_documents

    def broken(kind):
        if kind == loc.LOCATION:
            raise StoreError("timeout")
        return real(kind)

    monkeypatch.setattr(world, "list_documents", broken)
    b.observe()  # declared dead, but the entries cannot be listed: nothing settled, the document stays
    assert pending(world)["shop"]
    monkeypatch.setattr(world, "list_documents", real)
    b.observe()  # the next pass finishes it
    assert pending(world) == {}


def test_tokens_from_before_m24_are_never_settled_automatically(world):
    world.create_document(
        loc.LOCATION, "orders", {"diagram": "orders", "projects": ["shop"], "pending": {"shop": ["0123456789abcdef"]}}
    )
    b_clock = Clock()
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    watch(b, b_clock, 10 * LEASE)
    assert pending(world) == {"shop": ["0123456789abcdef"]}


# ---- the placement path ----------------------------------------------------------------------------------------


def test_a_placement_settles_its_token_and_counts_nothing_in_flight_afterwards(world):
    clock = Clock()
    a = writer_process(world, clock)
    with loc.writing_as(world, a.writer):
        ProjectService(world, world).adopt("orders", "shop")
    assert pending(world) == {} and listed(world) == ["shop"] and a.writer.in_flight() == 0


def test_a_failed_placement_whose_re_read_succeeds_settles_its_token(world, monkeypatch):
    clock = Clock()
    a = writer_process(world, clock)

    def failing(self, project_id, change, *args, **kwargs):
        raise StoreError("throttled")

    monkeypatch.setattr(ProjectService, "_update", failing)
    with loc.writing_as(world, a.writer), pytest.raises(StoreError):
        ProjectService(world, world).adopt("orders", "shop")
    assert pending(world) == {} and a.writer.in_flight() == 0


# ---- the operator command -------------------------------------------------------------------------------------


def test_settle_pending_waits_a_lease_and_settles_only_proven_writers(world):
    a = writer_process(world, Clock())
    crashed_placement(world, a)
    world.create_document(
        loc.LOCATION, "legacy-1", {"diagram": "legacy-1", "projects": ["shop"], "pending": {"shop": ["0" * 16]}}
    )
    clock = Clock()
    report = settle_pending(world, world, wait=LEASE, lease=LEASE, everything=False, sleep=clock.advance, clock=clock)
    assert report.dead == [a.writer.boot] and report.settled == 1 and report.legacy == 1 and not report.refused
    assert pending(world) == {} and loc.read_location(world, "legacy-1").pending == {"shop": ["0" * 16]}


def test_settle_pending_never_declares_a_writer_dead_before_a_lease(world):
    a = writer_process(world, Clock())
    crashed_placement(world, a)
    clock = Clock()
    report = settle_pending(
        world, world, wait=LEASE / 2, lease=LEASE, everything=False, sleep=clock.advance, clock=clock
    )
    assert report.dead == [] and report.alive == [a.writer.boot] and pending(world)["shop"]


def test_settle_pending_all_clears_everything_in_a_maintenance_window(world):
    a = writer_process(world, Clock())
    crashed_placement(world, a)
    world.create_document(
        loc.LOCATION, "legacy-1", {"diagram": "legacy-1", "projects": ["shop"], "pending": {"shop": ["0" * 16]}}
    )
    clock = Clock()
    report = settle_pending(world, world, wait=2.0, lease=LEASE, everything=True, sleep=clock.advance, clock=clock)
    assert report.dead == [a.writer.boot] and report.legacy == 1 and not report.refused
    assert pending(world) == {} and loc.read_location(world, "legacy-1") is None  # dropped with its last project


def test_settle_pending_all_refuses_when_a_writer_is_alive(world):
    a_clock = Clock()
    a = writer_process(world, a_clock)
    crashed_placement(world, a)
    clock = Clock()

    def sleep_while_a_beats(seconds: float) -> None:
        clock.advance(seconds)
        a.beat()

    report = settle_pending(
        world, world, wait=2.0, lease=LEASE, everything=True, sleep=sleep_while_a_beats, clock=clock
    )
    assert report.refused == [a.writer.boot] and report.settled == 0 and pending(world)["shop"]


def test_the_cli_settles_pending_tokens(tmp_path, capsys, monkeypatch):
    from arcviz.cli import migrate_main
    from arcviz.store import FileStore

    monkeypatch.setenv("ARCVIZ_WRITER_HEARTBEAT_SECONDS", "1")
    store = FileStore(tmp_path)
    store.create_document(
        loc.LOCATION, "legacy-1", {"diagram": "legacy-1", "projects": ["shop"], "pending": {"shop": ["0" * 16]}}
    )
    assert migrate_main(["--settle-pending", f"file:{tmp_path}", "--all", "--wait", "2"]) == 0
    out = capsys.readouterr().out
    assert "settled 1 pending token" in out and loc.read_location(store, "legacy-1") is None
    for too_short in (["--wait", "9"], ["--all", "--wait", "1"]):  # below a lease; below two heartbeats with --all
        with pytest.raises(SystemExit):
            migrate_main(["--settle-pending", f"file:{tmp_path}", *too_short])
    with pytest.raises(SystemExit):
        migrate_main(["--projects", f"file:{tmp_path}", "--all"])


# ---- the server runs the heartbeat ------------------------------------------------------------------------------


def test_the_server_registers_a_writer_and_stops_it_cleanly(store):
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    app = create_app(store=store)
    with TestClient(app) as client:
        boots = [key for key, _, _ in store.list_documents(loc.WRITER)]
        assert len(boots) == 1
        body, _ = store.read_document(loc.WRITER, boots[0])
        assert body["state"] == "alive"
        ready = client.get("/readyz").json()["checks"]
        assert ready["writer"] == {"ok": True}
    body, _ = store.read_document(loc.WRITER, boots[0])
    assert body["state"] == "stopped"


def test_readiness_fails_for_a_fenced_writer(store):
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    app = create_app(store=store)
    with TestClient(app) as client:
        app.state.heartbeat.writer.fenced = True
        r = client.get("/readyz")
        assert r.status_code == 503 and r.json()["checks"]["writer"]["ok"] is False


# ---- coordinator review of #199 (#189): outages, liveness, the membership write, cost, lifespans -----------------


def test_a_store_outage_longer_than_a_lease_fences_nobody(world, monkeypatch):
    """Every heartbeat stops while the store is down; afterwards a reaper must not judge the others on the timer it
    started before the outage. It only judges while its own heartbeat is fresh, and starts over after a gap."""
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    b = Heartbeat(world, world, WriterConfig(heartbeat=1.0), clock=b_clock, writer=loc.Writer(clock=b_clock))
    b.register()
    watch_b = lambda: b.reap()  # noqa: E731
    watch_b()
    b_clock.advance(1.0)
    watch_b()
    real_commit = world.commit_document

    def down(*args, **kwargs):
        raise StoreError("service unavailable")

    monkeypatch.setattr(world, "commit_document", down)  # the outage: no heartbeat moves, b's included
    for _ in range(int(2 * LEASE)):
        b_clock.advance(1.0)
        a_clock.advance(1.0)
        b.beat()
        a.beat()
        watch_b()
    monkeypatch.setattr(world, "commit_document", real_commit)  # back: b renews first, then watches again
    b.beat()
    report = watch_b()
    assert report.dead == [] and not a.writer.fenced
    assert world.read_document(loc.WRITER, a.writer.boot)[0]["state"] == "alive"
    a.beat()
    assert not a.writer.fenced  # a gets a whole lease again


def test_a_reaper_that_was_paused_starts_its_timers_over(world):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    b.observe()
    b_clock.advance(LEASE + 1)  # b itself did not run for a lease (paused, starved)
    assert b.observe().dead == [] and world.read_document(loc.WRITER, a.writer.boot)[0]["state"] == "alive"


def test_a_fenced_process_fails_liveness_and_a_new_lifespan_is_a_new_writer(store):
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    app = create_app(store=store)
    with TestClient(app) as client:
        first = app.state.heartbeat.writer
        assert client.get("/healthz").status_code == 200
        first.fenced = True  # another replica declared this process dead
        r = client.get("/healthz")
        assert r.status_code == 503 and r.json()["status"] == "fenced"
    with TestClient(app) as client:  # the next lifespan (a restart in the same process, uvicorn reload, tests)
        second = app.state.heartbeat.writer
        assert second is not first and second.boot != first.boot and not second.fenced
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").json()["checks"]["writer"] == {"ok": True}
        assert client.post("/api/projects", json={"title": "Neu"}).status_code == 201
        created = client.post("/api/diagrams", json={"title": "Neu", "template": "empty"})
        assert created.status_code == 201  # placing works: the new writer issues tokens


def test_a_writer_fenced_between_its_token_and_the_membership_write_writes_nothing(world, monkeypatch):
    """The fence is checked again inside the membership write, not only when the token is taken."""
    clock = Clock()
    a = writer_process(world, clock)
    real_add = loc.add_location

    def add_then_pause(documents, diagram_id, project_id):
        token = real_add(documents, diagram_id, project_id)
        clock.advance(LEASE / 2 + 1)  # paused after listing: no renewal for half a lease
        return token

    import arcviz.services.projects as projects_module

    monkeypatch.setattr(projects_module, "add_location", add_then_pause)
    with loc.writing_as(world, a.writer), pytest.raises(StoreError, match="heartbeat"):
        ProjectService(world, world).adopt("orders", "shop")
    project, _ = world.read_project("shop")
    assert project.member("orders") is None and pending(world) == {}


def test_a_slow_scan_never_delays_the_heartbeat(world):
    """The reaper runs on its own thread: while it is stuck listing the locator entries, the beats go on."""
    import threading
    import time

    stopped = loc.Writer()
    world.create_document(loc.WRITER, stopped.boot, {"boot": stopped.boot, "state": "stopped", "beats": 0})
    release = threading.Event()
    scanning = threading.Event()
    real = world.list_documents

    def slow(kind):
        if kind == loc.LOCATION:
            scanning.set()
            release.wait(5)
        return real(kind)

    world.list_documents = slow  # type: ignore[method-assign]
    heartbeat = Heartbeat(world, world, WriterConfig(heartbeat=0.05), writer=loc.Writer())
    try:
        heartbeat.start()
        assert scanning.wait(5)
        before = world.read_document(loc.WRITER, heartbeat.writer.boot)[0]["beats"]
        time.sleep(0.5)
        after = world.read_document(loc.WRITER, heartbeat.writer.boot)[0]["beats"]
        assert after >= before + 3, (before, after)
    finally:
        release.set()
        world.list_documents = real  # type: ignore[method-assign]
        heartbeat.close(wait=0)


def test_settling_scans_the_entries_once_per_dead_writer_not_every_pass(world, monkeypatch):
    a_clock, b_clock = Clock(), Clock()
    a = writer_process(world, a_clock)
    crashed_placement(world, a)
    real = world.list_documents
    scans: list[str] = []

    def counted(kind):
        if kind == loc.LOCATION:
            scans.append(kind)
        return real(kind)

    monkeypatch.setattr(world, "list_documents", counted)
    b = Reaper(world, world, LEASE, clock=b_clock, own="b" * 16)
    watch(b, b_clock, 3 * LEASE)
    assert len(scans) == 2  # the first settlement, the final pass a lease later; nothing in between
    assert world.list_documents(loc.WRITER) == []
