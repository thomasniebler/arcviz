"""Runs the store contract against every backend. File-layout specifics live in test_store_file.py."""

from __future__ import annotations

import pytest

from arcviz.store import ConflictError, StoreError
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from tests.cosmos_fake import FakeContainer
from tests.store_contract import *  # noqa: F401,F403


@pytest.fixture(params=["file", "sqlite", "cosmos"])
def repo(request, tmp_path):
    if request.param == "file":
        yield FileStore(tmp_path / "data")
        return
    if request.param == "sqlite":
        store = SqliteStore(tmp_path / "data" / "arcviz.sqlite3")
        yield store
        store.close()
        return
    yield CosmosStore(FakeContainer())


def test_sqlite_close_closes_the_connections_of_every_thread(tmp_path):
    """#182: every per-thread connection is closed by `close()`; the store opens a new one when used again."""
    import sqlite3
    import threading

    store = SqliteStore(tmp_path / "db.sqlite3")
    store.create("Demo", "demo")
    opened = []

    def work() -> None:
        store.exists("demo")
        opened.append(store._local.conn)

    threads = [threading.Thread(target=work) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.close()
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    assert store.exists("demo")  # a fresh connection
    store.close()


def test_a_collected_sqlite_store_closes_its_connections(tmp_path):
    """#182: a store that is never closed explicitly closes its connections when it is collected (no ResourceWarning,
    which the suite turns into an error)."""
    import gc
    import sqlite3

    store = SqliteStore(tmp_path / "db.sqlite3")
    store.create("Demo", "demo")
    conn = store._local.conn
    del store
    gc.collect()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_cosmos_delete_handles_more_than_one_batch(tmp_path):
    repo = CosmosStore(FakeContainer())
    repo.create("Demo", "demo")
    repo.append_chat("demo", [{"role": "user", "content": str(i)} for i in range(250)])
    repo.delete("demo")
    assert repo.list_diagrams() == [] and repo.load_chat("demo") == []


def test_cosmos_compact_survives_large_fold():
    repo = CosmosStore(FakeContainer())
    repo.create("Demo", "demo")
    repo.append_chat("demo", [{"role": "user", "content": str(i)} for i in range(250)])
    repo.compact_chat("demo", 240, {"role": "summary", "content": "s"})
    chat = repo.load_chat("demo")
    assert [m["seq"] for m in chat] == [240] + list(range(241, 251))
    assert chat[0]["role"] == "summary"


def test_cosmos_small_compaction_is_one_atomic_batch():
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    repo.append_chat("demo", [{"role": "user", "content": str(i)} for i in range(10)])
    container.batches.clear()
    repo.compact_chat("demo", 6, {"role": "summary", "content": "s"})
    assert len(container.batches) == 1
    # the generation guard (every compaction is bound to a generation, ADR-0127), then seq 6 becomes the summary
    assert [op for op, *_ in container.batches[0]] == ["replace", "replace"] + ["delete"] * 5
    assert [m["seq"] for m in repo.load_chat("demo")] == [6, 7, 8, 9, 10]


def test_cosmos_staged_compaction_hides_leftovers_after_a_failure_and_cleans_up_later():
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    repo.append_chat("demo", [{"role": "user", "content": str(i)} for i in range(250)])
    container.batches.clear()
    container.fail_batches_after = 1  # summary written, first delete batch ok, second fails
    with pytest.raises(StoreError):
        repo.compact_chat("demo", 240, {"role": "summary", "content": "s"})
    chat = repo.load_chat("demo")
    assert [m["seq"] for m in chat] == [240] + list(range(241, 251))  # reader never sees the leftovers
    raw = [k for (pk, k) in container.items if k.startswith("chat:")]
    assert len(raw) > len(chat)  # but they are still physically there
    container.fail_batches_after = None
    repo.compact_chat("demo", 244, {"role": "summary", "content": "s2"})
    raw = sorted(k for (pk, k) in container.items if k.startswith("chat:"))
    assert raw == [f"chat:{n:06d}" for n in range(244, 251)]  # leftovers gone
    assert [m["seq"] for m in repo.load_chat("demo")] == list(range(244, 251))


def test_cosmos_retries_transient_errors_during_allocation(monkeypatch):
    import arcviz.store.cosmos as cosmos_module

    monkeypatch.setattr(cosmos_module.time, "sleep", lambda _s: None)
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    container.transient_failures = 3  # three 503s, then success; well within TRANSIENT_ATTEMPTS
    info = repo.snapshot(repo.load("demo"), "after a hiccup")
    assert info.version == 2 and [v.version for v in repo.list_versions("demo")] == [1, 2]
    container.transient_failures = 50  # more than the retry budget: surfaces as StoreError
    with pytest.raises(StoreError, match="service unavailable"):
        repo.snapshot(repo.load("demo"), "gives up")


def test_cosmos_lost_create_response_is_reconciled_not_duplicated(monkeypatch):
    import arcviz.store.cosmos as cosmos_module

    monkeypatch.setattr(cosmos_module.time, "sleep", lambda _s: None)
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    container.lose_next_create_response = True  # version 2 is written, but the client sees a connection error
    info = repo.snapshot(repo.load("demo"), "survives a lost response")
    assert info.version == 2
    assert [v.version for v in repo.list_versions("demo")] == [1, 2]  # no duplicate under version 3
    container.lose_next_create_response = True
    repo.append_chat("demo", [{"role": "user", "content": "once"}])
    assert [m["content"] for m in repo.load_chat("demo")] == ["once"]
    # a lost response on create_latest is a success too, a real duplicate still fails
    container.lose_next_create_response = True
    repo.create("Other", "other")
    assert repo.exists("other")
    with pytest.raises(StoreError, match="already exists"):
        repo.create("Other", "other")


def test_cosmos_lost_batch_response_is_replayed_safely(monkeypatch):
    import arcviz.store.cosmos as cosmos_module

    monkeypatch.setattr(cosmos_module.time, "sleep", lambda _s: None)
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    repo.append_chat("demo", [{"role": "user", "content": str(i)} for i in range(10)])
    container.lose_next_batch_response = True  # the compaction batch lands, the client retries it
    repo.compact_chat("demo", 6, {"role": "summary", "content": "s"})
    assert [m["seq"] for m in repo.load_chat("demo")] == [6, 7, 8, 9, 10]
    container.lose_next_batch_response = True
    repo.delete("demo")
    assert not repo.exists("demo") and repo.load_chat("demo") == []


def test_cosmos_ping_raises_when_the_service_fails():
    from azure.cosmos import exceptions

    from tests.cosmos_fake import _http_error

    container = FakeContainer()

    def broken(*_args, **_kwargs):
        raise _http_error(exceptions.CosmosHttpResponseError, 401, "unauthorized (injected)")

    container.read_item = broken  # type: ignore[method-assign]
    with pytest.raises(StoreError, match="unauthorized"):
        CosmosStore(container).ping()


def test_sqlite_adds_the_chat_generation_column_to_an_old_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE diagrams (id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL, latest TEXT NOT NULL);"
    )
    conn.close()
    repo = SqliteStore(path)
    repo.create("Demo", "demo")
    generation = repo.chat_generation("demo")
    assert generation and repo.chat_generation("demo") == generation


def test_file_store_gives_a_legacy_transcript_a_generation(tmp_path):
    repo = FileStore(tmp_path / "data")
    repo.create("Demo", "demo")
    (tmp_path / "data" / "diagrams" / "demo" / "chat-generation").unlink(missing_ok=True)
    generation = repo.chat_generation("demo")
    assert generation and repo.chat_generation("demo") == generation


# ---- Cosmos DB: conditional writes and lost responses (#65) ----------------------------------


def _cosmos_demo():
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    return container, repo


def test_cosmos_commit_whose_response_is_lost_is_not_reported_as_a_conflict(monkeypatch):
    monkeypatch.setattr("arcviz.store.cosmos._backoff", lambda *a: None)
    container, repo = _cosmos_demo()
    d, rev = repo.load_with_revision("demo")
    d.title = "Changed"
    container.lose_next_batch_response = True  # the batch lands, the client sees a connection error
    new_rev, info = repo.commit(d, rev, label="change")
    assert repo.load_with_revision("demo")[1] == new_rev and repo.load("demo").title == "Changed"
    assert [v.version for v in repo.list_versions("demo")] == [1, 2] and info.version == 2


def test_cosmos_commit_is_replayed_after_a_transient_failure(monkeypatch):
    monkeypatch.setattr("arcviz.store.cosmos._backoff", lambda *a: None)
    container, repo = _cosmos_demo()
    d, rev = repo.load_with_revision("demo")
    container.transient_batch_failures = 2
    new_rev, _ = repo.commit(d, rev, label="after two 503s")
    assert repo.load_with_revision("demo")[1] == new_rev and len(repo.list_versions("demo")) == 2


def test_cosmos_commit_uses_if_match_on_the_latest_etag():
    import copy

    container, repo = _cosmos_demo()
    d, rev = repo.load_with_revision("demo")
    sent: list = []
    original = container.execute_item_batch
    container.execute_item_batch = lambda ops, **kw: (sent.append(copy.deepcopy(ops)), original(ops, **kw))[1]
    repo.commit(d, rev, label="x")
    (batch,) = sent
    (op, args, options), (create, (version,)) = batch
    assert op == "replace" and args[0] == "latest" and options["if_match_etag"].startswith('"')
    assert create == "create" and version["id"] == "version:0002"


def test_cosmos_append_chat_is_refused_after_a_concurrent_delete():
    container, repo = _cosmos_demo()
    container.before_write = lambda: (setattr(container, "before_write", None), repo.delete("demo"))
    with pytest.raises(StoreError, match="not found"):
        repo.append_chat("demo", [{"role": "user", "content": "late"}])
    assert repo.load_chat("demo") == [] and not repo.exists("demo")


def test_cosmos_replay_after_a_transient_failure_keeps_the_if_match_condition(monkeypatch):
    """The SDK pops `if_match_etag` from the options it gets; a replay must not become unconditional."""
    monkeypatch.setattr("arcviz.store.cosmos._backoff", lambda *a: None)
    container, repo = _cosmos_demo()
    mine, rev = repo.load_with_revision("demo")
    mine.title = "MINE"
    calls: list[int] = []

    def hook() -> None:
        calls.append(1)
        if len(calls) == 1:
            container.transient_batch_failures = 1  # the first attempt fails before it is applied
        elif len(calls) == 2:  # before the replay: another request commits
            other, other_rev = repo.load_with_revision("demo")
            other.title = "OTHER"
            repo.save_latest(other, expected=other_rev)

    container.before_write = hook
    with pytest.raises(ConflictError):
        repo.save_latest(mine, expected=rev)  # without a version item, only If-Match guards the replay
    container.before_write = None
    assert repo.load("demo").title == "OTHER"


def test_cosmos_purge_removes_latest_first():
    container, repo = _cosmos_demo()
    repo.append_chat("demo", [{"role": "user", "content": "x"}])
    deleted: list[str] = []
    original = container.delete_item
    container.delete_item = lambda item, partition_key, **kw: (deleted.append(item), original(item, partition_key))
    repo.delete("demo")
    assert deleted[:1] == ["latest"] and repo.load_chat("demo") == [] and not repo.exists("demo")


def test_cosmos_staged_compaction_stops_deleting_when_the_chat_is_cleared_meanwhile():
    """A clear between the delete batches of a large fold: fresh entries reuse old ids and must survive."""
    container, repo = _cosmos_demo()
    repo.append_chat("demo", [{"role": "user", "content": f"old {i}"} for i in range(250)])
    generation = repo.chat_generation("demo")
    original = container.execute_item_batch
    calls: list[int] = []

    def batch(ops, **kw):
        calls.append(1)
        if len(calls) == 2:  # the summary is written; before the first cleanup batch: clear and refill
            container.execute_item_batch = original
            repo.clear_chat("demo")
            repo.append_chat("demo", [{"role": "user", "content": f"new {i}"} for i in range(5)])
            container.execute_item_batch = batch
        return original(ops, **kw)

    container.execute_item_batch = batch
    assert repo.compact_chat("demo", 240, {"role": "summary", "content": "OLD"}, generation) is True
    container.execute_item_batch = original
    assert [m["content"] for m in repo.load_chat("demo")] == [f"new {i}" for i in range(5)]


def _cosmos_project():
    from arcviz import projects

    container = FakeContainer()
    repo = CosmosStore(container)
    revision = repo.create_project(projects.new_project("Shop", "shop"))
    return container, repo, revision


def test_cosmos_project_lives_in_its_own_partition():
    container, repo, _ = _cosmos_project()
    assert ("project:shop", "project") in container.items
    assert repo.list_diagrams() == []


def test_cosmos_project_commit_whose_response_is_lost_is_not_a_conflict(monkeypatch):
    monkeypatch.setattr("arcviz.store.cosmos._backoff", lambda *a: None)
    container, repo, revision = _cosmos_project()
    project, _ = repo.read_project("shop")
    project.title = "Webshop"
    container.lose_next_batch_response = True
    new_revision = repo.commit_project(project, revision)
    assert repo.read_project("shop") == (project, new_revision)


def test_cosmos_project_delete_after_a_transient_failure(monkeypatch):
    monkeypatch.setattr("arcviz.store.cosmos._backoff", lambda *a: None)
    container, repo, revision = _cosmos_project()
    container.transient_batch_failures = 2
    repo.delete_project("shop", revision)
    assert repo.list_projects() == []


def test_cosmos_project_commit_checks_the_etag(monkeypatch):
    container, repo, revision = _cosmos_project()
    project, _ = repo.read_project("shop")

    def concurrent_write():  # another replica writes between our read and our batch
        container.before_write = None
        item = container.read_item("project", partition_key="project:shop")
        container.replace_item("project", {**item, "revision": "theirs"})

    container.before_write = concurrent_write
    with pytest.raises(ConflictError):
        repo.commit_project(project, revision)
    assert repo.read_project("shop")[1] == "theirs"


def test_cosmos_project_delete_never_removes_a_project_created_again_meanwhile():
    """A create of the same id between the delete of the project item and the transcript cleanup survives."""
    from arcviz import projects
    from arcviz.store.cosmos import Q_ALL_OF_TYPE

    container, repo, revision = _cosmos_project()
    repo.append_project_chat("shop", [{"role": "user", "content": "old"}])
    original = repo._query
    recreated: list[int] = []

    def query(q, partition=None, **params):
        if q == Q_ALL_OF_TYPE and partition == "project:shop" and not recreated:  # the cleanup's listing
            recreated.append(1)
            repo.create_project(projects.new_project("Shop again", "shop"))
            repo.append_project_chat("shop", [{"role": "user", "content": "new"}])
        return original(q, partition, **params)

    repo._query = query
    repo.delete_project("shop", revision)
    repo._query = original
    assert recreated and repo.read_project("shop")[0].title == "Shop again"
    assert [m["content"] for m in repo.load_project_chat("shop")] == ["new"]


def test_cosmos_project_cleanup_never_deletes_entries_of_a_project_created_after_its_check():
    """The recreation lands between the cleanup's "project gone?" check and its deletes: the new transcript
    entry reuses the id chat:000001 and must survive (the deletes are conditional on the listed ETags)."""
    from arcviz import projects

    container, repo, revision = _cosmos_project()
    repo.append_project_chat("shop", [{"role": "user", "content": "old"}])
    project, revision = repo.read_project("shop")
    original = repo._read
    state = {"deleted": False, "recreated": False}

    def read(item_id, partition):
        found = original(item_id, partition)
        if state["deleted"] and not state["recreated"] and item_id == "project" and partition == "project:shop":
            state["recreated"] = True  # the check sees no project, then the recreation happens
            repo._read = original
            repo.create_project(projects.new_project("Shop again", "shop"))
            repo.append_project_chat("shop", [{"role": "user", "content": "new"}])
        return found

    original_batch = repo._project_batch

    def project_batch(*args, **kwargs):
        original_batch(*args, **kwargs)
        state["deleted"] = True

    repo._project_batch = project_batch
    repo._read = read
    repo.delete_project("shop", revision)
    repo._read = original
    assert state["recreated"]
    assert [m["content"] for m in repo.load_project_chat("shop")] == ["new"]


def test_cosmos_create_project_removes_leftovers_only_if_unchanged():
    """create_project cleans a failed cleanup's leftovers, but never an entry rewritten meanwhile (another create
    of the same id that won the race wrote it under the reused id)."""
    from arcviz import projects

    container, repo, revision = _cosmos_project()
    repo.append_project_chat("shop", [{"role": "user", "content": "old"}])
    real_items = repo._transcript_items
    repo._transcript_items = lambda partition: []  # the delete's cleanup is lost (a crash)
    repo.delete_project("shop", revision)
    repo._transcript_items = real_items
    assert ("project:shop", "chat:000001") in container.items  # a leftover

    original = repo._delete_if_unchanged

    def delete_if_unchanged(partition, item_id, etag):
        repo._delete_if_unchanged = original
        item = container.read_item(item_id, partition_key=partition)  # rewritten after our listing
        container.replace_item(item_id, {**item, "entry": {"role": "user", "content": "new"}})
        original(partition, item_id, etag)

    repo._delete_if_unchanged = delete_if_unchanged
    repo.create_project(projects.new_project("Shop again", "shop"))
    assert [m["content"] for m in repo.load_project_chat("shop")] == ["new"]


def test_cosmos_create_project_leaves_the_entries_of_a_concurrent_winner():
    """Two creates of the same id: the loser lists the transcript after the winner created the project and
    appended to it; it must delete nothing and fail."""
    from arcviz import projects

    container, repo, revision = _cosmos_project()
    repo.append_project_chat("shop", [{"role": "user", "content": "old"}])
    real_items = repo._transcript_items
    repo._transcript_items = lambda partition: []  # the delete's cleanup is lost (a crash): leftovers remain
    repo.delete_project("shop", revision)
    repo._transcript_items = real_items

    other = CosmosStore(container)  # the concurrent winner, another replica

    def items_after_the_winner(partition):
        repo._transcript_items = real_items
        other.create_project(projects.new_project("Winner", "shop"))
        other.append_project_chat("shop", [{"role": "user", "content": "winner"}])
        return real_items(partition)

    repo._transcript_items = items_after_the_winner
    with pytest.raises(StoreError, match="already exists"):
        repo.create_project(projects.new_project("Loser", "shop"))
    assert repo.read_project("shop")[0].title == "Winner"
    assert [m["content"] for m in repo.load_project_chat("shop")] == ["winner"]


def test_read_revision_of_a_state_stored_without_one_is_the_legacy_revision(tmp_path):
    """#174: data from before revisions: every backend answers LEGACY_REVISION, like read_latest."""
    import json

    from arcviz.store.base import LEGACY_REVISION

    file_store = FileStore(tmp_path / "data")
    file_store.create("Demo", "demo")
    path = tmp_path / "data" / "diagrams" / "demo" / "latest.json"
    data = json.loads(path.read_text())
    del data["revision"]
    path.write_text(json.dumps(data))
    assert file_store.read_revision("demo") == LEGACY_REVISION == file_store.load_with_revision("demo")[1]

    container = FakeContainer()
    cosmos = CosmosStore(container)
    cosmos.create("Demo", "demo")
    del container.items[("demo", "latest")]["revision"]
    assert cosmos.read_revision("demo") == LEGACY_REVISION == cosmos.load_with_revision("demo")[1]


def test_cosmos_read_revision_never_reads_the_diagram():
    """#174, ADR-0122: one single-partition projection query, no point read of `latest`."""
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    reads = []
    original = container.read_item
    container.read_item = lambda item, partition_key, **kw: reads.append(item) or original(item, partition_key, **kw)
    container.queries.clear()
    assert repo.read_revision("demo") == repo._read_latest("demo")["revision"]
    assert reads == ["latest"]  # only the comparison above
    assert container.queries == ["SELECT VALUE c.revision FROM c WHERE c.id = @id"]


def test_file_read_revision_parses_latest_only_after_a_write(tmp_path, monkeypatch):
    """#174: the file store answers from (inode, mtime, size) while latest.json is unchanged."""
    import json as json_module
    import time

    import arcviz.store.file as file_module

    monkeypatch.setattr(file_module, "_clock_ns", lambda: time.time_ns() + 60 * 10**9)  # every file is old enough
    repo = FileStore(tmp_path / "data")
    repo.create("Demo", "demo")
    first = repo.read_revision("demo")
    parsed = []
    real_loads = json_module.loads
    monkeypatch.setattr(file_module.json, "loads", lambda text: parsed.append(1) or real_loads(text))
    assert repo.read_revision("demo") == first and parsed == []
    d = repo.load("demo")
    second = repo.save_latest(d)
    parsed.clear()
    assert repo.read_revision("demo") == second != first and parsed == [1]


def test_file_read_revision_never_caches_a_file_written_just_now(tmp_path, monkeypatch):
    """Review of #174: a rewrite within one timestamp tick could reuse inode, mtime and size; such files are re-read.
    The clock is injected: "now" is the file's own modification time."""
    import json as json_module

    import arcviz.store.file as file_module

    repo = FileStore(tmp_path / "data")
    repo.create("Demo", "demo")
    mtime = (tmp_path / "data" / "diagrams" / "demo" / "latest.json").stat().st_mtime_ns
    monkeypatch.setattr(file_module, "_clock_ns", lambda: mtime)
    repo.read_revision("demo")
    parsed = []
    real_loads = json_module.loads
    monkeypatch.setattr(file_module.json, "loads", lambda text: parsed.append(1) or real_loads(text))
    repo.read_revision("demo")
    assert parsed == [1]


def test_file_revision_cache_is_bounded_and_forgets_deleted_diagrams(tmp_path, monkeypatch):
    """Review of #174: the cache keeps at most _REVISIONS_CACHED diagrams (least recently used go) and drops a deleted
    diagram's entry."""
    import time

    import arcviz.store.file as file_module

    monkeypatch.setattr(file_module, "_clock_ns", lambda: time.time_ns() + 60 * 10**9)
    monkeypatch.setattr(file_module, "_REVISIONS_CACHED", 2)
    repo = FileStore(tmp_path / "data")
    for name in ("a", "b", "c"):
        repo.create(name.upper(), name)
        repo.read_revision(name)
    assert list(repo._revisions) == ["b", "c"]
    repo.delete("c")
    assert list(repo._revisions) == ["b"]


def test_sqlite_releases_the_connection_of_a_thread_that_ended(tmp_path):
    """Review of #182: a long-running server's worker threads come and go; each one's connection is closed when it
    ends, not kept open until the store is closed."""
    import gc
    import threading

    store = SqliteStore(tmp_path / "db.sqlite3")
    store.create("Demo", "demo")

    def work() -> None:
        store.exists("demo")

    for _ in range(20):
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()
    gc.collect()
    assert len(store._connections) == 1  # the main thread's, nothing of the 20 that ended
    store.close()


def test_cosmos_conditional_delete_losing_to_a_concurrent_delete_is_not_found():
    """Review of #175: like file and SQLite (atomic there), a conditional delete whose diagram another request deleted
    in between answers "not found" (HTTP 404), never a revision conflict (412)."""
    container = FakeContainer()
    repo = CosmosStore(container)
    repo.create("Demo", "demo")
    revision = repo.read_revision("demo")
    raced = []

    def delete_first():
        if not raced:
            raced.append(1)
            container.before_write = None
            CosmosStore(container).delete("demo")

    container.before_write = delete_first
    with pytest.raises(StoreError, match="not found") as caught:
        repo.delete("demo", expected=revision)
    assert not isinstance(caught.value, ConflictError) and raced


# ---- review of #194: a Cosmos clear deletes exactly the old generation's entries (ADR-0127) ---------------------------


def _cosmos_with_chat():
    from tests.store_contract import demo_diagram

    store = CosmosStore(FakeContainer())
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    store.append_chat("demo", [{"role": "user", "content": "old 1"}, {"role": "assistant", "content": "old 2"}])
    return store


def test_a_cosmos_clear_keeps_an_entry_appended_in_the_new_generation_before_its_cleanup():
    store = _cosmos_with_chat()
    real_run_batch = store._run_batch

    def append_right_after_the_new_generation(partition, ops, landed, what):
        status = real_run_batch(partition, ops, landed, what)
        if what == "clear" and status is None:  # another turn appends in the new generation before the cleanup
            store.append_chat("demo", [{"role": "user", "content": "new"}], store.chat_generation("demo"))
        return status

    store._run_batch = append_right_after_the_new_generation
    store.clear_chat("demo")
    assert [m["content"] for m in store.load_chat("demo")] == ["new"]


def test_a_cosmos_clear_is_not_stopped_by_an_append_in_the_new_generation():
    store = _cosmos_with_chat()
    container = store.container
    real_batch = container.execute_item_batch
    appended = []

    def append_before_the_cleanup(batch_operations, partition_key, **kwargs):
        if not appended and any(op[0] == "delete" for op in batch_operations):
            appended.append(True)  # bumps the generation item while the cleanup runs
            store.append_chat("demo", [{"role": "user", "content": "new"}], store.chat_generation("demo"))
        return real_batch(batch_operations, partition_key=partition_key, **kwargs)

    container.execute_item_batch = append_before_the_cleanup
    store.clear_chat("demo")
    assert [m["content"] for m in store.load_chat("demo")] == ["new"]  # no old entry comes back


def test_a_cosmos_append_without_a_generation_racing_a_clear_never_writes_a_dead_generation():
    """Second review of #194: the append binds itself to the generation it read, inside its batch."""
    store = _cosmos_with_chat()
    real = store._t_generation
    raced = []

    def clear_right_after_the_read(partition, anchor):
        value = real(partition, anchor)
        if not raced:
            raced.append(True)
            store.clear_chat("demo")  # between the append's generation read and its write
        return value

    store._t_generation = clear_right_after_the_read
    store.append_chat("demo", [{"role": "user", "content": "late"}])
    store._t_generation = real
    current = store.chat_generation("demo")
    tags = {
        i.get("generation")
        for i in store._query("SELECT c.id, c.generation FROM c WHERE c.type = @type", "demo", type="chat")
    }
    assert tags == {current}  # no item carries the replaced generation
    store.clear_chat("demo")
    assert store.load_chat("demo") == []  # so a later clear removes it too


def test_a_cosmos_compaction_racing_a_clear_never_replaces_the_new_transcript():
    store = _cosmos_with_chat()
    real_query = store._query
    raced = []

    def clear_and_refill_after_listing(query, partition=None, **params):
        result = real_query(query, partition, **params)
        if "c.seq <= @seq" in query and not raced:
            raced.append(True)
            store.clear_chat("demo")
            store.append_chat("demo", [{"role": "user", "content": "neu 1"}, {"role": "assistant", "content": "neu 2"}])
        return result

    store._query = clear_and_refill_after_listing
    compacted = store.compact_chat("demo", 2, {"role": "summary", "content": "old summary"})
    store._query = real_query
    assert compacted is False
    assert [m["content"] for m in store.load_chat("demo")] == ["neu 1", "neu 2"]


def test_cosmos_load_hides_items_of_another_generation():
    store = _cosmos_with_chat()
    container = store.container
    stale = {
        "id": "chat:000099",
        "type": "chat",
        "diagram_id": "demo",
        "seq": 99,
        "entry": {"role": "user", "content": "stale"},
        "generation": "dead",
    }
    container.upsert_item(stale)  # left by a clear whose cleanup did not finish
    assert "stale" not in [m["content"] for m in store.load_chat("demo")]
