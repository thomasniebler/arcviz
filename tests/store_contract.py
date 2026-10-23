"""Contract every DiagramRepository backend has to fulfil.

Imported (``from tests.store_contract import *``) by a test module that provides a ``repo`` fixture;
``demo_repo`` below creates the demo diagram in it. Spec: docs/architecture/specs/persistence.md.
"""

from __future__ import annotations

import threading

import pytest

from arcviz import operations as ops
from arcviz import projects
from arcviz.store import Change, ConflictError, RevisionMismatch, StoreError, VersionInfo
from arcviz.templates import demo_diagram


@pytest.fixture
def demo_repo(repo):
    repo.create("Demo", "demo", demo_diagram("demo", "Demo"))
    return repo


# ---- diagrams ------------------------------------------------------------------


def test_create_writes_latest_and_initial_version(demo_repo):
    repo = demo_repo
    assert repo.exists("demo")
    assert repo.load("demo").title == "Demo"
    versions = repo.list_versions("demo")
    assert [v.version for v in versions] == [1]
    assert versions[0].source == "initial" and versions[0].label == "Initial state"
    infos = repo.list_diagrams()
    assert [d.id for d in infos] == ["demo"]
    assert infos[0].title == "Demo" and infos[0].version_count == 1


def test_create_rejects_bad_and_duplicate_ids(demo_repo):
    repo = demo_repo
    with pytest.raises(StoreError):
        repo.create("x", "../escape")
    with pytest.raises(StoreError):
        repo.create("x", "Has Upper")
    with pytest.raises(StoreError, match="already exists"):
        repo.create("Demo again", "demo")
    assert not repo.exists("../escape")


def test_create_derives_id_from_title(repo):
    d = repo.create("My Cloud Setup")
    assert d.id == "my-cloud-setup" and repo.exists("my-cloud-setup")


def test_load_missing_raises(repo):
    with pytest.raises(StoreError, match="not found"):
        repo.load("nope")
    with pytest.raises(StoreError, match="not found"):
        repo.load_version("nope", 1)
    assert repo.list_versions("nope") == []
    assert repo.load_chat("nope") == []


def test_latest_is_rewritten_and_versions_are_immutable(demo_repo):
    repo = demo_repo
    d = repo.load("demo")
    ops.add_node(d, "vnet", kind="vnet", id="vnet", parent="prod-deployment")
    repo.save_latest(d)
    assert "vnet" in repo.load("demo").nodes
    _, v1 = repo.load_version("demo", 1)
    assert "vnet" not in v1.nodes
    info = repo.snapshot(d, label="added vnet", source="auto")
    assert info.version == 2 and info.node_count == len(d.nodes) and info.edge_count == len(d.edges)
    meta, snap = repo.load_version("demo", 2)
    assert meta == info and "vnet" in snap.nodes
    assert repo.list_diagrams()[0].version_count == 2
    assert repo.list_diagrams()[0].updated_at == d.updated_at


def test_save_latest_refuses_inconsistent_state(demo_repo):
    d = demo_repo.load("demo")
    d.nodes["service-a"].parent = "ghost"
    with pytest.raises(StoreError, match="inconsistent"):
        demo_repo.save_latest(d)
    assert demo_repo.load("demo").nodes["service-a"].parent != "ghost"


def test_delete_removes_everything(demo_repo):
    repo = demo_repo
    repo.append_chat("demo", [{"role": "user", "content": "hi"}])
    repo.delete("demo")
    assert not repo.exists("demo")
    assert repo.list_diagrams() == []
    assert repo.list_versions("demo") == []
    assert repo.load_chat("demo") == []
    with pytest.raises(StoreError, match="not found"):
        repo.delete("demo")
    # the id can be reused and starts fresh
    repo.create("Demo", "demo", demo_diagram("demo", "Demo"))
    assert [v.version for v in repo.list_versions("demo")] == [1]


def test_list_diagrams_is_sorted_by_id(repo):
    for name in ["zeta", "alpha", "mid"]:
        repo.create(name)
    assert [d.id for d in repo.list_diagrams()] == ["alpha", "mid", "zeta"]


# ---- versions ------------------------------------------------------------------


def test_snapshot_labels_are_trimmed(demo_repo):
    d = demo_repo.load("demo")
    assert demo_repo.snapshot(d, "   ").label == "Version 2"
    assert len(demo_repo.snapshot(d, "x" * 500).label) == 120
    assert demo_repo.snapshot(d, "  spaced  ").label == "spaced"


def test_restore_creates_new_linear_version(demo_repo):
    repo = demo_repo
    d = repo.load("demo")
    ops.remove_node(d, "idp")
    repo.save_latest(d)
    repo.snapshot(d, "removed idp", source="auto")
    restored, info = repo.restore("demo", 1)
    assert "idp" in restored.nodes
    assert "idp" in repo.load("demo").nodes
    assert info.version == 3 and info.source == "restore" and info.restored_from == 1
    assert info.label.startswith("Restored version 1")
    with pytest.raises(StoreError, match="not found"):
        repo.restore("demo", 99)


def test_undo_target_walks_backwards_through_restores(demo_repo):
    repo = demo_repo
    d = repo.load("demo")
    assert repo.undo_target("demo") is None
    ops.add_node(d, "a", kind="service", id="a")
    repo.save_latest(d)
    repo.snapshot(d, "a", "auto")  # v2
    ops.add_node(d, "b", kind="service", id="b")
    repo.save_latest(d)
    repo.snapshot(d, "b", "auto")  # v3
    assert repo.undo_target("demo") == 2
    repo.restore("demo", 2)  # v4 == state of v2
    assert "a" in repo.load("demo").nodes and "b" not in repo.load("demo").nodes
    assert repo.undo_target("demo") == 1
    repo.restore("demo", 1)  # v5 == initial
    assert repo.undo_target("demo") is None


def test_undo_skips_manual_snapshots_of_the_same_state(demo_repo):
    repo = demo_repo
    d = repo.load("demo")
    ops.add_node(d, "a", kind="service", id="a")
    repo.save_latest(d)
    repo.snapshot(d, "a", "auto")  # v2
    repo.snapshot(d, "checkpoint", "manual")  # v3, same content as v2
    assert repo.undo_target("demo") == 1
    repo.restore("demo", 1)  # v4
    assert repo.undo_target("demo") is None


def _change(repo, node_id: str) -> None:
    d = repo.load("demo")
    ops.add_node(d, node_id, kind="service", id=node_id)
    repo.save_latest(d)
    repo.snapshot(d, node_id, "auto")


def test_undo_and_redo_walk_back_and_forth(demo_repo):
    repo = demo_repo
    assert repo.redo_target("demo") is None and repo.redo("demo") is None
    _change(repo, "a")  # v2
    _change(repo, "b")  # v3
    _, info = repo.undo("demo")  # v4 = v2
    assert info.source == "undo" and info.restored_from == 2 and info.label.startswith("Undo: restored version 2")
    assert repo.redo_target("demo") == 3
    repo.undo("demo")  # v5 = v1
    assert set(repo.load("demo").nodes).isdisjoint({"a", "b"})
    assert repo.redo_target("demo") == 2
    _, info = repo.redo("demo")  # v6 = v2
    assert info.source == "redo" and info.restored_from == 2 and info.label.startswith("Redo: restored version 2")
    assert "a" in repo.load("demo").nodes and "b" not in repo.load("demo").nodes
    assert repo.redo_target("demo") == 3
    repo.redo("demo")  # v7 = v3
    assert {"a", "b"} <= set(repo.load("demo").nodes)
    assert repo.redo_target("demo") is None and repo.redo("demo") is None
    assert repo.undo_target("demo") == 2  # undo after redo goes back again


def test_undo_after_redo_and_redo_again(demo_repo):
    repo = demo_repo
    _change(repo, "a")  # v2
    repo.undo("demo")  # v3 = v1
    repo.redo("demo")  # v4 = v2
    repo.undo("demo")  # v5 = v1
    assert repo.redo_target("demo") == 2


def test_a_new_change_clears_redo_but_a_manual_snapshot_does_not(demo_repo):
    repo = demo_repo
    _change(repo, "a")  # v2
    repo.undo("demo")  # v3
    repo.snapshot(repo.load("demo"), "checkpoint", "manual")  # v4
    assert repo.redo_target("demo") == 2
    _change(repo, "c")  # v5
    assert repo.redo_target("demo") is None
    repo.undo("demo")  # v6
    repo.restore("demo", 2)  # v7, a restore chosen in the selector also clears redo
    assert repo.redo_target("demo") is None


def test_undo_returns_none_when_there_is_nothing_to_undo(demo_repo):
    assert demo_repo.undo("demo") is None
    assert len(demo_repo.list_versions("demo")) == 1


def test_undo_after_redoing_past_a_restore_goes_back_to_the_undone_state(demo_repo):
    repo = demo_repo
    _change(repo, "a")  # v2
    _change(repo, "b")  # v3
    repo.restore("demo", 2)  # v4 = {a}, chosen in the selector
    repo.undo("demo")  # v5 = v1 (empty)
    _, info = repo.redo("demo")  # v6 = {a}
    assert info.restored_from == 2
    repo.undo("demo")  # back to the empty state, not to v3
    assert set(repo.load("demo").nodes).isdisjoint({"a", "b"})


def test_a_snapshot_after_an_undo_does_not_make_undo_reapply_the_change(demo_repo):
    repo = demo_repo
    _change(repo, "a")  # v2
    repo.undo("demo")  # v3 = v1
    repo.snapshot(repo.load("demo"), "checkpoint", "manual")  # v4, same content as v3
    assert repo.undo_target("demo") is None and repo.redo_target("demo") == 2
    repo.snapshot(repo.load("demo"), "no-op", "auto")  # v5, same content again
    assert repo.undo_target("demo") is None and repo.redo_target("demo") == 2


def test_a_no_op_version_does_not_clear_redo(demo_repo):
    repo = demo_repo
    _change(repo, "a")  # v2
    repo.undo("demo")  # v3
    repo.snapshot(repo.load("demo"), "idempotent op", "auto")  # v4, same content (e.g. an existing edge)
    assert repo.redo_target("demo") == 2


def test_legacy_restore_versions_are_not_redoable(demo_repo):
    repo = demo_repo
    _change(repo, "a")  # v2
    repo.restore("demo", 1)  # v3: an undo recorded before redo existed
    assert repo.redo_target("demo") is None


def test_concurrent_snapshots_get_distinct_version_numbers(demo_repo):
    repo = demo_repo
    d = repo.load("demo")
    results: list[int] = []
    errors: list[Exception] = []

    def work(i: int) -> None:
        try:
            results.append(repo.snapshot(d, f"snap {i}", "manual").version)
        except Exception as exc:  # pragma: no cover - would make the test fail below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, [f"{type(e).__name__}: {e}" for e in errors]
    assert sorted(results) == list(range(2, 27))
    assert [v.version for v in repo.list_versions("demo")] == list(range(1, 27))


# ---- chat ----------------------------------------------------------------------


def test_chat_append_assigns_increasing_seq(demo_repo):
    repo = demo_repo
    assert repo.load_chat("demo") == []
    repo.append_chat("demo", [{"role": "user", "content": "hi"}])
    repo.append_chat(
        "demo", [{"role": "assistant", "content": "hello", "operations": []}, {"role": "user", "content": "more"}]
    )
    chat = repo.load_chat("demo")
    assert [m["seq"] for m in chat] == [1, 2, 3]
    assert [m["role"] for m in chat] == ["user", "assistant", "user"]
    assert chat[1]["operations"] == []
    repo.clear_chat("demo")
    assert repo.load_chat("demo") == []
    repo.append_chat("demo", [{"role": "user", "content": "again"}])
    assert [m["seq"] for m in repo.load_chat("demo")] == [1]


def test_chat_append_ignores_incoming_seq(demo_repo):
    demo_repo.append_chat("demo", [{"role": "user", "content": "a", "seq": 99}])
    assert demo_repo.load_chat("demo")[0]["seq"] == 1


def test_chat_concurrent_appends_lose_nothing(demo_repo):
    repo = demo_repo

    def work(i: int) -> None:
        repo.append_chat("demo", [{"role": "user", "content": f"m{i}"}])

    threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    chat = repo.load_chat("demo")
    assert sorted(m["content"] for m in chat) == sorted(f"m{i}" for i in range(20))
    assert [m["seq"] for m in chat] == list(range(1, 21))


def test_compact_chat_replaces_prefix_with_summary_and_keeps_seq(demo_repo):
    repo = demo_repo
    entries = []
    for i in range(1, 5):
        entries += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
    repo.append_chat("demo", entries)  # seq 1..8
    summary = {"role": "summary", "content": "earlier stuff", "covers": {"from_seq": 1, "to_seq": 4, "turns": 2}}
    repo.compact_chat("demo", 4, summary)
    chat = repo.load_chat("demo")
    assert [m["seq"] for m in chat] == [4, 5, 6, 7, 8]
    assert chat[0]["role"] == "summary" and chat[0]["content"] == "earlier stuff"
    assert chat[0]["covers"]["turns"] == 2
    assert [m["content"] for m in chat[1:]] == ["u3", "a3", "u4", "a4"]
    # seq keeps counting from the highest stored value
    repo.append_chat("demo", [{"role": "user", "content": "u5"}])
    assert repo.load_chat("demo")[-1]["seq"] == 9
    # a second compaction folds the summary as well
    repo.compact_chat("demo", 8, {"role": "summary", "content": "all of it"})
    chat = repo.load_chat("demo")
    assert [(m["seq"], m["role"]) for m in chat] == [(8, "summary"), (9, "user")]
    repo.clear_chat("demo")
    assert repo.load_chat("demo") == []


def test_compact_chat_is_skipped_when_the_fold_is_gone(demo_repo):
    """A summary produced while the transcript was cleared (or compacted past it) must not resurrect it."""
    repo = demo_repo
    repo.append_chat("demo", [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}])
    repo.append_chat("demo", [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}])
    assert repo.compact_chat("demo", 2, {"role": "summary", "content": "first turn"}) is True
    # a newer compaction already covered seq 2: the stale, smaller fold is ignored
    assert repo.compact_chat("demo", 1, {"role": "summary", "content": "stale"}) is False
    assert [(m["seq"], m["role"]) for m in repo.load_chat("demo")] == [(2, "summary"), (3, "user"), (4, "assistant")]
    repo.clear_chat("demo")
    assert repo.compact_chat("demo", 4, {"role": "summary", "content": "everything"}) is False
    assert repo.load_chat("demo") == []
    # the transcript keeps working afterwards
    repo.append_chat("demo", [{"role": "user", "content": "fresh"}])
    assert [(m["seq"], m["content"]) for m in repo.load_chat("demo")] == [(1, "fresh")]


def _pair(repo, label: str) -> None:
    repo.append_chat(
        "demo", [{"role": "user", "content": f"{label} u"}, {"role": "assistant", "content": f"{label} a"}]
    )


def test_compact_chat_rejects_a_stale_summary_after_clear_and_repopulate(demo_repo):
    """#67: clear resets seq, so a summary of the old transcript would match fresh entries by number."""
    repo = demo_repo
    _pair(repo, "old")  # seq 1, 2
    generation = repo.chat_generation("demo")  # read before the transcript, like the agent does
    old_seqs = [m["seq"] for m in repo.load_chat("demo")]
    repo.clear_chat("demo")  # while the summary is being written
    _pair(repo, "new")
    assert [m["seq"] for m in repo.load_chat("demo")] == old_seqs == [1, 2]  # the numbers are reused
    assert repo.compact_chat("demo", 2, {"role": "summary", "content": "OLD SUMMARY"}, generation) is False
    assert [m["content"] for m in repo.load_chat("demo")] == ["new u", "new a"]


def test_compact_chat_with_the_current_generation_keeps_entries_appended_meanwhile(demo_repo):
    repo = demo_repo
    _pair(repo, "one")
    _pair(repo, "two")
    generation = repo.chat_generation("demo")
    _pair(repo, "three")  # appended while the summary is being written
    assert repo.compact_chat("demo", 2, {"role": "summary", "content": "S"}, generation) is True
    assert [(m["seq"], m["content"]) for m in repo.load_chat("demo")] == [
        (2, "S"),
        (3, "two u"),
        (4, "two a"),
        (5, "three u"),
        (6, "three a"),
    ]
    assert repo.chat_generation("demo") == generation  # compaction and appends keep the generation


def test_compact_chat_rejects_a_summary_from_a_previous_incarnation_of_the_diagram(demo_repo):
    repo = demo_repo
    _pair(repo, "old")
    generation = repo.chat_generation("demo")
    repo.delete("demo")
    assert repo.chat_generation("demo") == ""  # unknown diagram: empty, nothing created
    assert not repo.exists("demo")
    repo.create("Demo", "demo")
    _pair(repo, "new")
    assert repo.chat_generation("demo") not in ("", generation)
    assert repo.compact_chat("demo", 2, {"role": "summary", "content": "OLD SUMMARY"}, generation) is False
    assert [m["content"] for m in repo.load_chat("demo")] == ["new u", "new a"]


def test_chat_generation_changes_on_clear_and_import_only(demo_repo):
    repo = demo_repo
    first = repo.chat_generation("demo")
    assert first and repo.chat_generation("demo") == first  # stable while nothing replaces the transcript
    _pair(repo, "x")
    assert repo.chat_generation("demo") == first
    repo.clear_chat("demo")
    second = repo.chat_generation("demo")
    assert second not in ("", first)
    repo.import_chat("demo", [{"role": "user", "content": "u", "seq": 4}])
    assert repo.chat_generation("demo") not in ("", first, second)


def test_import_chat_keeps_seq_verbatim(demo_repo):
    repo = demo_repo
    repo.append_chat("demo", [{"role": "user", "content": "old"}])
    repo.import_chat(
        "demo",
        [
            {"role": "summary", "content": "s", "seq": 7},
            {"role": "user", "content": "u", "seq": 8},
            {"role": "assistant", "content": "a", "seq": 9},
        ],
    )
    chat = repo.load_chat("demo")
    assert [(m["seq"], m["role"], m["content"]) for m in chat] == [
        (7, "summary", "s"),
        (8, "user", "u"),
        (9, "assistant", "a"),
    ]
    repo.append_chat("demo", [{"role": "user", "content": "next"}])
    assert repo.load_chat("demo")[-1]["seq"] == 10


def test_concurrent_create_of_the_same_id_succeeds_exactly_once(repo):
    diagram = demo_diagram("race", "Race")
    outcomes: list[str] = []

    def work() -> None:
        try:
            repo.create("Race", "race", diagram)
            outcomes.append("ok")
        except StoreError as exc:
            outcomes.append("exists" if "already exists" in str(exc) else f"other: {exc}")

    threads = [threading.Thread(target=work) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1 and outcomes.count("exists") == 11, outcomes
    assert [v.version for v in repo.list_versions("race")] == [1]


def test_create_latest_is_atomic_create_if_absent(demo_repo):
    with pytest.raises(StoreError, match="already exists"):
        demo_repo.create_latest(
            demo_repo.load("demo"), VersionInfo(version=0, label="x", created_at="t", source="initial")
        )
    assert len(demo_repo.list_versions("demo")) == 1  # the refused create wrote no version either


def test_load_chat_hides_entries_older_than_the_newest_summary(demo_repo):
    repo = demo_repo
    repo.import_chat(
        "demo",
        [
            {"role": "user", "content": "leftover", "seq": 1},
            {"role": "assistant", "content": "leftover", "seq": 2},
            {"role": "summary", "content": "s", "seq": 2},
            {"role": "user", "content": "kept", "seq": 3},
        ][:0]
        + [
            {"role": "user", "content": "leftover", "seq": 1},
            {"role": "summary", "content": "s", "seq": 2},
            {"role": "user", "content": "kept", "seq": 3},
        ],
    )
    assert [(e["seq"], e["role"]) for e in repo.load_chat("demo")] == [(2, "summary"), (3, "user")]
    repo.append_chat("demo", [{"role": "assistant", "content": "a"}])
    assert repo.load_chat("demo")[-1]["seq"] == 4


# ---- layout direction (docs/architecture/specs/layout-direction.md) ------------------


def test_direction_survives_save_and_versions(demo_repo):
    repo = demo_repo
    d = repo.load("demo")
    ops.set_direction(d, "LR")
    repo.save_latest(d)
    repo.snapshot(d, label="LR", source="auto")
    assert repo.load("demo").direction == "LR"
    assert repo.load_version("demo", 2)[1].direction == "LR"
    assert repo.load_version("demo", 1)[1].direction == "TB"
    assert repo.list_versions("demo")[1].fingerprint != repo.list_versions("demo")[0].fingerprint


# ---- readiness probe ---------------------------------------------------------


def test_ping_succeeds_and_creates_nothing(repo):
    assert repo.ping() is None
    repo.create("Demo", "demo")
    assert repo.ping() is None
    assert [d.id for d in repo.list_diagrams()] == ["demo"]


# ---- conditional writes (#65, spec: docs/architecture/specs/concurrent-mutations.md) -------------------


def test_the_issue_reproduction_second_stale_write_conflicts(repo):
    """Two independent copies of an empty diagram: the second save must not drop the first one's node."""
    repo.create("Race", "race")
    a, rev_a = repo.load_with_revision("race")
    b, rev_b = repo.load_with_revision("race")
    assert rev_a == rev_b
    ops.add_node(a, "A", kind="service", id="a")
    repo.save_latest(a, expected=rev_a)
    ops.add_node(b, "B", kind="service", id="b")
    with pytest.raises(ConflictError):
        repo.save_latest(b, expected=rev_b)
    assert set(repo.load("race").nodes) == {"a"}


def test_every_write_gets_a_new_revision(demo_repo):
    repo = demo_repo
    d, first = repo.load_with_revision("demo")
    second = repo.save_latest(d, expected=first)  # unchanged content still gets a new revision
    assert second != first and repo.load_with_revision("demo")[1] == second
    third, info = repo.commit(d, second, label="checkpoint", source="manual")
    assert third not in (first, second) and info.version == 2
    assert repo.load_with_revision("demo")[1] == third


def test_diagram_infos_describe_exactly_the_given_existing_diagrams(demo_repo):
    """#181: the infos of the given ids only (no listing of the store), sorted by id; unknown ids left out."""
    repo = demo_repo
    repo.create("Zeta", "zeta")
    repo.create("Alpha", "alpha")
    repo.snapshot_latest("alpha", "manual")
    infos = repo.diagram_infos(["zeta", "nope", "alpha", "alpha"])
    assert [i.id for i in infos] == ["alpha", "zeta"]
    by_id = {i.id: i for i in repo.list_diagrams()}
    assert infos == [by_id["alpha"], by_id["zeta"]]
    assert infos[0].version_count == 2 and infos[0].title == "Alpha"
    assert repo.diagram_infos([]) == []


def test_delete_on_the_expected_revision_deletes(demo_repo):
    """#175, ADR-0123: a conditional delete."""
    repo = demo_repo
    repo.delete("demo", expected=repo.read_revision("demo"))
    assert not repo.exists("demo") and repo.list_versions("demo") == [] and repo.load_chat("demo") == []


def test_delete_on_a_stale_revision_conflicts_and_deletes_nothing(demo_repo):
    repo = demo_repo
    d, old = repo.load_with_revision("demo")
    repo.append_chat("demo", [{"role": "user", "content": "hi"}])
    repo.save_latest(d, expected=old)
    with pytest.raises(ConflictError):
        repo.delete("demo", expected=old)
    assert repo.exists("demo") and len(repo.list_versions("demo")) == 1 and len(repo.load_chat("demo")) == 1


def test_delete_of_an_unknown_diagram_with_a_revision_is_not_found(repo):
    with pytest.raises(StoreError, match="not found") as caught:
        repo.delete("nope", expected="r")
    assert not isinstance(caught.value, ConflictError)


def test_read_revision_is_the_revision_of_the_latest_state(demo_repo):
    """#174: the revision alone, exactly as `load_with_revision` returns it, after every kind of write."""
    repo = demo_repo
    d, rev = repo.load_with_revision("demo")
    assert repo.read_revision("demo") == rev
    rev = repo.save_latest(d, expected=rev)
    assert repo.read_revision("demo") == rev == repo.load_with_revision("demo")[1]
    rev, _ = repo.commit(d, rev, label="v")
    assert repo.read_revision("demo") == rev
    rev = repo.write_latest(d)
    assert repo.read_revision("demo") == rev
    repo.snapshot_latest("demo", "manual")
    assert repo.read_revision("demo") == repo.load_with_revision("demo")[1] != rev


def test_read_revision_of_an_unknown_or_deleted_diagram_raises(demo_repo):
    repo = demo_repo
    with pytest.raises(StoreError, match="not found"):
        repo.read_revision("nope")
    repo.delete("demo")
    with pytest.raises(StoreError, match="not found"):
        repo.read_revision("demo")


def test_commit_writes_latest_and_the_version_together_or_not_at_all(demo_repo):
    repo = demo_repo
    d, rev = repo.load_with_revision("demo")
    ops.add_node(d, "a", kind="service", id="a")
    new_rev, info = repo.commit(d, rev, label="add a")
    assert info.version == 2 and info.source == "auto" and info.label == "add a"
    _, snap = repo.load_version("demo", 2)
    assert snap == repo.load("demo") and "a" in snap.nodes
    ops.add_node(d, "b", kind="service", id="b")
    with pytest.raises(ConflictError):
        repo.commit(d, rev, label="stale")  # the old revision
    assert "b" not in repo.load("demo").nodes and [v.version for v in repo.list_versions("demo")] == [1, 2]
    assert repo.load_with_revision("demo")[1] == new_rev


def test_create_writes_the_initial_version_atomically(repo):
    diagram = demo_diagram("fresh", "Fresh")
    rev, info = repo.create_latest(
        diagram, VersionInfo(version=0, label="Initial state", created_at="t", source="initial")
    )
    assert info.version == 1 and repo.load_with_revision("fresh") == (diagram, rev)
    assert repo.load_version("fresh", 1)[1] == diagram
    with pytest.raises(StoreError, match="already exists"):
        repo.create_latest(diagram, VersionInfo(version=0, label="again", created_at="t", source="initial"))


def _add(node_id: str):
    def change(diagram):
        ops.add_node(diagram, node_id, kind="service", id=node_id)
        return Change(diagram, node_id, label=f"add {node_id}")

    return change


def test_update_retries_on_a_concurrent_write_and_keeps_both_changes(demo_repo):
    repo = demo_repo
    calls = []

    def change(diagram):
        calls.append(len(calls))
        if len(calls) == 1:  # another request commits after this one has read the state
            repo.update("demo", _add("other"))
        ops.add_node(diagram, "mine", kind="service", id="mine")
        return Change(diagram, "ok", label="add mine")

    updated = repo.update("demo", change)
    assert len(calls) == 2 and updated.result == "ok"
    assert {"other", "mine"} <= set(repo.load("demo").nodes)
    assert [v.label for v in repo.list_versions("demo")][-2:] == ["add other", "add mine"]
    assert repo.load_version("demo", updated.version.version)[1] == repo.load("demo")


def test_update_gives_up_with_a_conflict_and_writes_nothing(demo_repo):
    repo = demo_repo
    before = repo.load("demo")

    def change(diagram):
        repo.save_latest(repo.load("demo"))  # someone else writes every time
        ops.add_node(diagram, "x", kind="service", id="x")
        return Change(diagram, None, label="never")

    with pytest.raises(ConflictError, match="try again"):
        repo.update("demo", change, attempts=3)
    assert repo.load("demo").nodes == before.nodes and len(repo.list_versions("demo")) == 1


def test_update_without_a_change_writes_nothing(demo_repo):
    rev = demo_repo.load_with_revision("demo")[1]
    assert demo_repo.update("demo", lambda d: None) is None
    assert demo_repo.load_with_revision("demo")[1] == rev


# ---- If-Match: one attempt on an expected revision (spec: collaboration.md, ADR-0094) -------------------------


def test_update_on_the_expected_revision_writes(demo_repo):
    rev = demo_repo.load_with_revision("demo")[1]
    updated = demo_repo.update("demo", _add("x"), expected=rev)
    assert updated is not None and updated.revision != rev and "x" in demo_repo.load("demo").nodes


def test_update_on_a_stale_revision_raises_and_writes_nothing(demo_repo):
    repo = demo_repo
    stale = repo.load_with_revision("demo")[1]
    repo.update("demo", _add("other"))
    current = repo.load_with_revision("demo")
    calls = []

    def change(diagram):
        calls.append(1)
        return _add("mine")(diagram)

    with pytest.raises(RevisionMismatch) as raised:
        repo.update("demo", change, expected=stale)
    assert isinstance(raised.value, ConflictError) and raised.value.revision == current[1]
    assert calls == [] and repo.load_with_revision("demo") == current
    assert [v.label for v in repo.list_versions("demo")][-1] == "add other"


def test_update_on_the_expected_revision_is_not_retried_after_a_concurrent_write(demo_repo):
    repo = demo_repo
    rev = repo.load_with_revision("demo")[1]
    calls = []

    def change(diagram):
        calls.append(1)
        repo.update("demo", _add(f"other{len(calls)}"))  # someone writes between the read and the write
        ops.add_node(diagram, "mine", kind="service", id="mine")
        return Change(diagram, None, label="add mine")

    with pytest.raises(RevisionMismatch):
        repo.update("demo", change, expected=rev)
    assert calls == [1] and "mine" not in repo.load("demo").nodes


def test_versioning_operations_honour_an_expected_revision(demo_repo):
    repo = demo_repo
    _change(repo, "a1")
    stale = repo.load_with_revision("demo")[1]
    _change(repo, "a2")
    count = len(repo.list_versions("demo"))
    for attempt in (
        lambda: repo.snapshot_latest("demo", "snap", expected=stale),
        lambda: repo.restore("demo", 1, expected=stale),
        lambda: repo.undo("demo", expected=stale),
        lambda: repo.redo("demo", expected=stale),
    ):
        with pytest.raises(RevisionMismatch):
            attempt()
    assert len(repo.list_versions("demo")) == count
    rev = repo.load_with_revision("demo")[1]
    assert repo.snapshot_latest("demo", "snap", expected=rev).label == "snap"
    rev = repo.load_with_revision("demo")[1]
    assert repo.undo("demo", expected=rev) is not None
    rev = repo.load_with_revision("demo")[1]
    assert repo.redo("demo", expected=rev) is not None
    rev = repo.load_with_revision("demo")[1]
    assert repo.restore("demo", 1, expected=rev)[1].source == "restore"


def test_concurrent_updates_lose_nothing(demo_repo):
    repo = demo_repo
    errors: list[Exception] = []

    def work(i: int) -> None:
        try:
            repo.update("demo", _add(f"n{i}"), attempts=100)
        except Exception as exc:  # pragma: no cover - would make the test fail below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, [f"{type(e).__name__}: {e}" for e in errors]
    latest = repo.load("demo")
    assert {f"n{i}" for i in range(12)} <= set(latest.nodes)
    versions = repo.list_versions("demo")
    assert [v.version for v in versions] == list(range(1, 14))
    assert repo.load_version("demo", 13)[1] == latest  # the newest version is the latest state


def _race_once(repo, competing) -> list[bool]:
    """Make the next commit_latest run `competing` first (a write between the caller's read and write)."""
    original = repo.commit_latest
    injected: list[bool] = []

    def racing_commit(diagram, expected, info):
        if not injected:
            injected.append(True)
            competing()
        return original(diagram, expected, info)

    repo.commit_latest = racing_commit
    return injected


def test_restore_is_retried_around_a_concurrent_write(demo_repo):
    repo = demo_repo
    repo.update("demo", _add("a"))  # v2
    injected = _race_once(repo, lambda: repo.update("demo", _add("b")))  # v3
    _, info = repo.restore("demo", 1)
    assert injected and info.version == 4 and info.restored_from == 1
    assert set(repo.load("demo").nodes) == set(repo.load_version("demo", 1)[1].nodes)


def test_undo_target_is_recomputed_after_a_concurrent_write(demo_repo):
    repo = demo_repo
    repo.update("demo", _add("a"))  # v2; undo would go back to v1
    injected = _race_once(repo, lambda: repo.update("demo", _add("b")))  # v3 lands first
    _, info = repo.undo("demo")
    assert injected and info.restored_from == 2  # undoes the newest change (b), not a as well
    assert "a" in repo.load("demo").nodes and "b" not in repo.load("demo").nodes


def test_snapshot_latest_records_the_current_state(demo_repo):
    repo = demo_repo
    repo.update("demo", _add("a"))
    info = repo.snapshot_latest("demo", "checkpoint")
    assert info.source == "manual" and info.version == 3
    assert repo.load_version("demo", 3)[1] == repo.load("demo")


def test_nothing_resurrects_a_deleted_diagram(demo_repo):
    repo = demo_repo
    d, rev = repo.load_with_revision("demo")
    repo.delete("demo")
    with pytest.raises(ConflictError):
        repo.save_latest(d, expected=rev)
    with pytest.raises(ConflictError):
        repo.save_latest(d)  # not even unconditionally
    with pytest.raises(ConflictError):
        repo.commit(d, rev, label="late version")
    with pytest.raises(StoreError, match="not found"):
        repo.append_chat("demo", [{"role": "user", "content": "late"}])
    with pytest.raises(StoreError, match="not found"):
        repo.update("demo", _add("x"))
    with pytest.raises(StoreError, match="not found"):
        repo.restore("demo", 1)
    assert not repo.exists("demo") and repo.list_diagrams() == []
    assert repo.list_versions("demo") == [] and repo.load_chat("demo") == []


# ---- projects (spec: docs/architecture/specs/projects.md) ---------------------------------------------


def _project(project_id="shop", title="Shop"):
    project = projects.new_project(title, project_id)
    projects.apply(project, "add_section", {"title": "Backend"})
    projects.apply(project, "add_diagram", {"diagram": "demo", "section": "backend"})
    return project


def test_project_create_read_list(repo):
    assert repo.list_projects() == []
    original = _project()
    revision = repo.create_project(original)
    project, read_revision = repo.read_project("shop")
    assert read_revision == revision and project == original
    assert project.member("demo").section == "backend"
    repo.create_project(_project("analytics", "Analytics"))
    assert [(p.id, r) for p, r in repo.list_projects()] == [
        ("analytics", repo.read_project("analytics")[1]),
        ("shop", revision),
    ]


def test_project_create_rejects_duplicates_and_bad_ids(repo):
    repo.create_project(_project())
    with pytest.raises(StoreError, match="already exists"):
        repo.create_project(_project())
    with pytest.raises(StoreError):
        repo.create_project(_project("../escape"))
    with pytest.raises(StoreError, match="not found"):
        repo.read_project("nope")


def test_project_commit_is_conditional_on_the_revision(repo):
    revision = repo.create_project(_project())
    project, _ = repo.read_project("shop")
    projects.apply(project, "rename_project", {"title": "Webshop"})
    new_revision = repo.commit_project(project, revision)
    assert new_revision != revision
    assert repo.read_project("shop") == (project, new_revision)
    stale = project.model_copy(deep=True)
    projects.apply(stale, "rename_project", {"title": "Lost update"})
    with pytest.raises(ConflictError):
        repo.commit_project(stale, revision)
    assert repo.read_project("shop")[0].title == "Webshop"


def test_project_commit_never_creates_a_project(repo):
    with pytest.raises(ConflictError):
        repo.commit_project(_project(), "0")
    assert repo.list_projects() == []


def test_project_delete_is_conditional(repo):
    revision = repo.create_project(_project())
    project, _ = repo.read_project("shop")
    newer = repo.commit_project(project, revision)
    with pytest.raises(ConflictError):
        repo.delete_project("shop", revision)
    repo.delete_project("shop", newer)
    assert repo.list_projects() == []
    with pytest.raises(StoreError, match="not found"):
        repo.read_project("shop")
    with pytest.raises(ConflictError):
        repo.delete_project("shop", newer)
    repo.create_project(_project())  # the id can be used again


def test_projects_and_diagrams_do_not_see_each_other(demo_repo):
    repo = demo_repo
    repo.create_project(_project("demo", "Same id as the diagram"))
    assert [d.id for d in repo.list_diagrams()] == ["demo"]
    assert [p.id for p, _ in repo.list_projects()] == ["demo"]
    repo.delete("demo")
    assert [p.id for p, _ in repo.list_projects()] == ["demo"]
    assert repo.list_diagrams() == []


def test_concurrent_project_commits_never_both_win(repo):
    revision = repo.create_project(_project())
    results: list[str] = []

    def commit(title):
        project, _ = repo.read_project("shop")
        projects.apply(project, "rename_project", {"title": title})
        try:
            repo.commit_project(project, revision)
            results.append(title)
        except ConflictError:
            pass

    threads = [threading.Thread(target=commit, args=(f"T{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1
    assert repo.read_project("shop")[0].title == results[0]


# ---- project transcripts (spec: docs/architecture/specs/project-chat.md, ADR-0057) ---------------------


@pytest.fixture
def project_repo(repo):
    repo.create_project(projects.new_project("Shop", "shop"))
    return repo


def _project_pair(repo, label: str) -> None:
    repo.append_project_chat(
        "shop", [{"role": "user", "content": f"{label} u"}, {"role": "assistant", "content": f"{label} a"}]
    )


def test_project_chat_append_assigns_increasing_seq(project_repo):
    repo = project_repo
    assert repo.load_project_chat("shop") == []
    repo.append_project_chat("shop", [{"role": "user", "content": "hi", "seq": 42}])
    repo.append_project_chat("shop", [{"role": "assistant", "content": "hello", "operations": []}])
    chat = repo.load_project_chat("shop")
    assert [(m["seq"], m["role"]) for m in chat] == [(1, "user"), (2, "assistant")]
    assert chat[1]["operations"] == []
    repo.clear_project_chat("shop")
    assert repo.load_project_chat("shop") == []
    repo.append_project_chat("shop", [{"role": "user", "content": "again"}])
    assert [m["seq"] for m in repo.load_project_chat("shop")] == [1]


def test_project_chat_is_separate_from_diagram_chats(demo_repo):
    repo = demo_repo
    repo.create_project(projects.new_project("Same id", "demo"))
    repo.append_chat("demo", [{"role": "user", "content": "diagram"}])
    repo.append_project_chat("demo", [{"role": "user", "content": "project"}])
    assert [m["content"] for m in repo.load_chat("demo")] == ["diagram"]
    assert [m["content"] for m in repo.load_project_chat("demo")] == ["project"]
    assert repo.chat_generation("demo") != repo.project_chat_generation("demo")


def test_project_chat_needs_an_existing_project(repo):
    with pytest.raises(StoreError, match="not found"):
        repo.append_project_chat("nope", [{"role": "user", "content": "x"}])
    with pytest.raises(StoreError, match="not found"):
        repo.clear_project_chat("nope")
    assert repo.load_project_chat("nope") == []
    assert repo.project_chat_generation("nope") == ""
    assert repo.list_projects() == []  # nothing was created on the way


def test_project_chat_concurrent_appends_lose_nothing(project_repo):
    repo = project_repo

    def work(i: int) -> None:
        repo.append_project_chat("shop", [{"role": "user", "content": f"m{i}"}])

    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    chat = repo.load_project_chat("shop")
    assert sorted(m["content"] for m in chat) == sorted(f"m{i}" for i in range(12))
    assert [m["seq"] for m in chat] == list(range(1, 13))


def test_project_chat_compaction_keeps_seq_and_honours_the_generation(project_repo):
    repo = project_repo
    _project_pair(repo, "one")
    _project_pair(repo, "two")
    generation = repo.project_chat_generation("shop")
    assert generation and repo.project_chat_generation("shop") == generation
    _project_pair(repo, "three")  # appended while the summary is being written
    assert repo.compact_project_chat("shop", 2, {"role": "summary", "content": "S"}, generation) is True
    assert [(m["seq"], m["content"]) for m in repo.load_project_chat("shop")] == [
        (2, "S"),
        (3, "two u"),
        (4, "two a"),
        (5, "three u"),
        (6, "three a"),
    ]
    assert repo.compact_project_chat("shop", 1, {"role": "summary", "content": "stale"}) is False
    repo.clear_project_chat("shop")
    assert repo.project_chat_generation("shop") not in ("", generation)
    _project_pair(repo, "new")  # seq 1, 2 again
    assert repo.compact_project_chat("shop", 2, {"role": "summary", "content": "OLD"}, generation) is False
    assert [m["content"] for m in repo.load_project_chat("shop")] == ["new u", "new a"]
    repo.append_project_chat("shop", [{"role": "user", "content": "x"}])
    assert repo.compact_project_chat("shop", 2, {"role": "summary", "content": "N"}) is True
    assert [(m["seq"], m["role"]) for m in repo.load_project_chat("shop")] == [(2, "summary"), (3, "user")]


def test_deleting_a_project_deletes_its_transcript(project_repo):
    repo = project_repo
    _project_pair(repo, "old")
    generation = repo.project_chat_generation("shop")
    repo.delete_project("shop", repo.read_project("shop")[1])
    assert repo.load_project_chat("shop") == []
    assert repo.project_chat_generation("shop") == ""
    with pytest.raises(StoreError, match="not found"):
        repo.append_project_chat("shop", [{"role": "user", "content": "late"}])
    repo.create_project(projects.new_project("Shop again", "shop"))
    assert repo.load_project_chat("shop") == []  # a new project never inherits the old transcript
    assert repo.project_chat_generation("shop") not in ("", generation)
    assert repo.compact_project_chat("shop", 2, {"role": "summary", "content": "OLD"}, generation) is False


# ---- keyed documents: workspaces and the user directory (spec: workspaces-and-sharing.md) -----------


@pytest.mark.parametrize("kind", ["workspace", "user", "placement"])
def test_document_create_read_list(repo, kind):
    assert repo.list_documents(kind) == []
    revision = repo.create_document(kind, "team-b", {"id": "team-b", "title": "B"})
    repo.create_document(kind, "team-a", {"id": "team-a", "title": "Ä", "nested": {"x": [1, 2]}})
    body, read_revision = repo.read_document(kind, "team-b")
    assert body == {"id": "team-b", "title": "B"} and read_revision == revision
    listed = repo.list_documents(kind)
    assert [(key, b["id"]) for key, b, _ in listed] == [("team-a", "team-a"), ("team-b", "team-b")]  # by key
    assert listed[0][1]["nested"] == {"x": [1, 2]} and listed[0][1]["title"] == "Ä"
    assert listed[1][2] == revision


def test_documents_of_different_kinds_and_projects_do_not_see_each_other(demo_repo):
    repo = demo_repo
    repo.create_document("workspace", "default", {"id": "default"})
    repo.create_project(projects.new_project("Default", "default"))
    assert repo.list_documents("user") == []
    assert [key for key, _, _ in repo.list_documents("workspace")] == ["default"]
    with pytest.raises(StoreError, match="not found"):
        repo.read_document("user", "default")
    assert [p.id for p, _ in repo.list_projects()] == ["default"]
    assert [d.id for d in repo.list_diagrams()] == ["demo"]


def test_document_create_rejects_duplicates_bad_keys_and_kinds(repo):
    repo.create_document("workspace", "team", {"v": 1})
    with pytest.raises(StoreError, match="already exists"):
        repo.create_document("workspace", "team", {"v": 2})
    assert repo.read_document("workspace", "team")[0] == {"v": 1}
    for key in ("../escape", "Has Upper", "a:b"):
        with pytest.raises(StoreError):
            repo.create_document("workspace", key, {})
    with pytest.raises(StoreError, match="kind"):
        repo.create_document("diagram", "team", {})
    with pytest.raises(StoreError, match="not found"):
        repo.read_document("workspace", "nobody")


def test_document_commit_and_delete_are_conditional(repo):
    first = repo.create_document("workspace", "team", {"v": 1})
    second = repo.commit_document("workspace", "team", {"v": 2}, first)
    assert second != first and repo.read_document("workspace", "team") == ({"v": 2}, second)
    with pytest.raises(ConflictError):
        repo.commit_document("workspace", "team", {"v": 3}, first)  # stale revision
    with pytest.raises(ConflictError):
        repo.delete_document("workspace", "team", first)
    assert repo.read_document("workspace", "team")[0] == {"v": 2}
    with pytest.raises(ConflictError):
        repo.commit_document("workspace", "ghost", {"v": 1}, second)  # never creates
    with pytest.raises(StoreError, match="not found"):
        repo.read_document("workspace", "ghost")
    repo.delete_document("workspace", "team", second)
    assert repo.list_documents("workspace") == []
    with pytest.raises(ConflictError):
        repo.delete_document("workspace", "team", second)
    repo.create_document("workspace", "team", {"v": 9})  # the key is free again
    assert repo.read_document("workspace", "team")[0] == {"v": 9}


def test_concurrent_document_commits_never_both_win(repo):
    revision = repo.create_document("workspace", "team", {"n": 0})
    barrier = threading.Barrier(4)
    wins: list[int] = []
    lock = threading.Lock()

    def writer(n: int) -> None:
        barrier.wait()
        try:
            repo.commit_document("workspace", "team", {"n": n}, revision)
        except ConflictError:
            return
        with lock:
            wins.append(n)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
    assert repo.read_document("workspace", "team")[0] == {"n": wins[0]}


# ---- authors of versions (spec: workspaces-and-sharing.md, ADR-0086) ----------------------------------------


def test_versions_record_their_author_and_omit_an_unknown_one(repo):
    from arcviz.workspaces import Identity

    ada = Identity("ada", "Ada Lovelace", "ada@example.org")
    repo.create("Demo", "demo", demo_diagram("demo", "Demo"), author=ada)
    repo.snapshot_latest("demo", "Stand", author=ada)
    repo.update("demo", lambda d: Change(d, None, label="anonym"))  # no author: the system context
    diagram, revision = repo.load_with_revision("demo")
    ops.add_node(diagram, "x", kind="service", id="x")
    repo.commit(diagram, revision, label="mit x", author=Identity("bob", "Bob"))
    repo.undo("demo", author=ada)
    repo.redo("demo", author=Identity("bob", "Bob"))
    repo.restore("demo", 1, author=ada)
    versions = repo.list_versions("demo")
    assert [(v.source, v.author, v.author_name) for v in versions] == [
        ("initial", "ada", "Ada Lovelace"),
        ("manual", "ada", "Ada Lovelace"),
        ("auto", None, None),
        ("auto", "bob", "Bob"),
        ("undo", "ada", "Ada Lovelace"),
        ("redo", "bob", "Bob"),
        ("restore", "ada", "Ada Lovelace"),
    ]
    assert "author" not in versions[2].model_dump() and "author_name" not in versions[2].model_dump()
    assert repo.load_version("demo", 4)[0].author == "bob"


# ---- transcript writes bound to a generation (#143, ADR-0127) ---------------------------------------------------------


def test_append_chat_with_a_generation_writes_only_into_that_transcript(demo_repo):
    repo = demo_repo
    generation = repo.chat_generation("demo")
    repo.append_chat("demo", [{"role": "user", "content": "a"}], generation)
    assert [m["content"] for m in repo.load_chat("demo")] == ["a"]
    repo.clear_chat("demo")
    with pytest.raises(ConflictError):
        repo.append_chat("demo", [{"role": "user", "content": "late"}], generation)
    assert repo.load_chat("demo") == []
    repo.append_chat("demo", [{"role": "user", "content": "b"}])  # unconditional, as before
    assert [m["content"] for m in repo.load_chat("demo")] == ["b"]


def test_append_chat_with_a_generation_after_delete_and_recreate(demo_repo):
    repo = demo_repo
    generation = repo.chat_generation("demo")
    repo.delete("demo")
    repo.create("Demo again", "demo", demo_diagram("demo", "Demo again"))
    with pytest.raises((ConflictError, StoreError)):
        repo.append_chat("demo", [{"role": "user", "content": "late"}], generation)
    assert repo.load_chat("demo") == []


def test_clear_chat_with_a_generation_clears_only_that_transcript(demo_repo):
    repo = demo_repo
    _pair(repo, "one")
    stale = repo.chat_generation("demo")
    repo.clear_chat("demo")
    _pair(repo, "two")
    with pytest.raises(ConflictError):
        repo.clear_chat("demo", stale)
    assert [m["content"] for m in repo.load_chat("demo")] == ["two u", "two a"]
    current = repo.chat_generation("demo")
    repo.clear_chat("demo", current)
    assert repo.load_chat("demo") == [] and repo.chat_generation("demo") not in ("", current)


def test_project_chat_writes_bound_to_a_generation(project_repo):
    repo = project_repo
    generation = repo.project_chat_generation("shop")
    _project_pair(repo, "one")
    repo.append_project_chat("shop", [{"role": "user", "content": "x"}], generation)
    project, revision = repo.read_project("shop")
    repo.delete_project("shop", revision)
    repo.create_project(projects.new_project("Shop again", "shop"))  # re-created under the same id
    _project_pair(repo, "new")
    with pytest.raises(ConflictError):
        repo.append_project_chat("shop", [{"role": "user", "content": "late"}], generation)
    with pytest.raises(ConflictError):
        repo.clear_project_chat("shop", generation)
    assert [m["content"] for m in repo.load_project_chat("shop")] == ["new u", "new a"]
    repo.clear_project_chat("shop", repo.project_chat_generation("shop"))
    assert repo.load_project_chat("shop") == []


# ---- version history read once, paged and pruned (#114, ADR-0129, ADR-0130) -------------------------------------------


def _counting(repo, name: str, calls: list[str]) -> None:
    real = getattr(repo, name)

    def wrapper(*args, **kwargs):
        calls.append(name)
        return real(*args, **kwargs)

    setattr(repo, name, wrapper)


def test_a_diagram_response_lists_the_version_metadata_once_and_loads_no_snapshot(demo_repo):
    from arcviz.services import DiagramService

    repo = demo_repo
    for i in range(3):
        repo.snapshot_latest("demo", f"s{i}")
    repo.undo("demo")
    calls: list[str] = []
    for name in ("list_versions", "load_version", "undo_target", "redo_target"):
        _counting(repo, name, calls)
    state = DiagramService(repo).state("demo")
    assert calls == ["list_versions"]
    assert state.undo_target == undo_target_reference(repo) and state.versions_total == len(state.versions)


def undo_target_reference(repo) -> int | None:
    from arcviz.history import undo_target_of

    return undo_target_of(repo.list_versions("demo"))


def test_history_is_the_listing_with_its_targets(demo_repo):
    repo = demo_repo
    repo.update("demo", lambda d: Change(_with_node(d, "a"), None, label="a"))
    repo.update("demo", lambda d: Change(_with_node(d, "b"), None, label="b"))
    repo.undo("demo")
    history = repo.history("demo")
    assert [v.version for v in history.versions] == [v.version for v in repo.list_versions("demo")]
    assert history.undo_target == repo.undo_target("demo") and history.redo_target == repo.redo_target("demo")
    assert history.redo_target is not None


def _with_node(diagram, node_id: str):
    ops.apply(diagram, "add_node", {"name": node_id, "kind": "service", "id": node_id})
    return diagram


def test_the_service_pages_the_versions(demo_repo):
    from arcviz.services import DiagramService

    repo = demo_repo
    for i in range(7):
        repo.snapshot_latest("demo", f"s{i}")
    service = DiagramService(repo)
    state = service.state("demo", page=3)
    assert [v.version for v in state.versions] == [6, 7, 8]
    assert state.versions_total == 8 and state.versions_more is True
    assert [v.version for v in service.versions("demo", before=6, limit=2)] == [4, 5]
    assert [v.version for v in service.versions("demo", before=3)] == [1, 2]
    assert len(service.versions("demo")) == 8


def test_retention_prunes_beyond_keep_and_keeps_what_undo_redo_and_manual_need(demo_repo):
    from arcviz.history import HistoryConfig

    repo = demo_repo
    repo.snapshot_latest("demo", "manual early")  # v2, manual: kept
    for i in range(20):
        repo.update("demo", lambda d, i=i: Change(_with_node(d, f"n{i}"), None, label=f"c{i}"))
    before_undo = repo.undo_target("demo")
    repo.retention = HistoryConfig(keep=10, slack=0)
    repo.update("demo", lambda d: Change(_with_node(d, "last"), None, label="last"))  # triggers the prune
    numbers = [v.version for v in repo.list_versions("demo")]
    assert 1 in numbers and 2 in numbers  # the first version and the manual snapshot
    assert numbers[-10:] == list(range(numbers[-1] - 9, numbers[-1] + 1))
    assert len(numbers) == 12
    assert repo.undo_target("demo") == numbers[-2]  # undo still reaches the previous state
    assert before_undo is not None
    restored, info = repo.undo("demo")
    assert info.version == numbers[-1] + 1  # numbers are never reused after a prune
    assert repo.redo_target("demo") is not None  # the redo stack survives later prunes
    repo.update("demo", lambda d: Change(d, None, label="no-op"))
    assert repo.redo_target("demo") is not None


def test_retention_is_off_by_default_and_manual_pruning_needs_its_setting(demo_repo):
    from arcviz.history import HistoryConfig

    repo = demo_repo
    for i in range(30):
        repo.snapshot_latest("demo", f"m{i}")
    assert len(repo.list_versions("demo")) == 31  # nothing pruned without a policy
    repo.retention = HistoryConfig(keep=10, slack=0)
    repo.snapshot_latest("demo", "one more")
    assert len(repo.list_versions("demo")) == 32  # manual snapshots are kept
    repo.retention = HistoryConfig(keep=10, slack=0, prune_manual=True)
    repo.snapshot_latest("demo", "and another")
    assert [v.version for v in repo.list_versions("demo")] == [1, *range(24, 34)]


def test_delete_versions_skips_what_is_gone(demo_repo):
    repo = demo_repo
    for i in range(3):
        repo.snapshot_latest("demo", f"s{i}")
    repo.delete_versions("demo", [2, 3])
    repo.delete_versions("demo", [2, 99])
    assert [v.version for v in repo.list_versions("demo")] == [1, 4]
    with pytest.raises(StoreError):
        repo.load_version("demo", 2)
