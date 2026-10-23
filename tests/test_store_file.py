"""File backend specifics: on-disk layout, human-readable JSON, cross-process reservation, legacy data."""

import json

import pytest

from arcviz.store import DiagramStore, StoreError
from arcviz.store.file import FileStore
from arcviz.templates import demo_diagram


@pytest.fixture
def file_store(tmp_path):
    return FileStore(tmp_path / "data")


@pytest.fixture
def demo(file_store):
    return file_store.create("Demo", "demo", demo_diagram("demo", "Demo"))


def test_layout(file_store, demo, tmp_path):
    root = tmp_path / "data" / "diagrams" / "demo"
    assert (root / "latest.json").exists()
    assert (root / "versions" / "0001.json").exists()
    file_store.append_chat("demo", [{"role": "user", "content": "hi"}])
    assert (root / "chat.json").exists()
    file_store.delete("demo")
    assert not root.exists()


def test_files_are_human_readable_json_and_survive_restart(file_store, demo, tmp_path):
    data = json.loads((tmp_path / "data" / "diagrams" / "demo" / "latest.json").read_text())
    assert data["id"] == "demo" and "service-a" in data["nodes"]
    version = json.loads((tmp_path / "data" / "diagrams" / "demo" / "versions" / "0001.json").read_text())
    assert set(version) == {"meta", "diagram"}
    survives = FileStore(tmp_path / "data")  # new "session" over the same directory
    assert survives.load("demo").title == "Demo"


def test_version_allocation_skips_numbers_taken_by_another_process(file_store, demo, tmp_path):
    d = file_store.load("demo")
    foreign = tmp_path / "data" / "diagrams" / "demo" / "versions" / "0002.json"
    foreign.write_text("")  # reserved by another process, not written yet
    info = file_store.snapshot(d, "mine", "manual")
    assert info.version == 3
    assert [v.version for v in file_store.list_versions("demo")] == [1, 3]


def test_legacy_chat_without_seq_is_numbered_on_load(file_store, demo, tmp_path):
    chat = tmp_path / "data" / "diagrams" / "demo" / "chat.json"
    chat.write_text(json.dumps([{"role": "user", "content": "old"}, {"role": "assistant", "content": "reply"}]))
    entries = file_store.load_chat("demo")
    assert [e["seq"] for e in entries] == [1, 2]
    file_store.append_chat("demo", [{"role": "user", "content": "new"}])
    assert [e["seq"] for e in file_store.load_chat("demo")] == [1, 2, 3]


def test_diagram_store_alias_is_the_file_backend(tmp_path):
    assert DiagramStore is FileStore


def test_ping_raises_when_the_data_directory_is_gone(tmp_path):
    import shutil

    from arcviz.store import StoreError

    store = FileStore(tmp_path / "data")
    store.ping()
    shutil.rmtree(tmp_path / "data")
    with pytest.raises(StoreError, match="not a writable directory"):
        store.ping()


def test_an_empty_document_left_by_a_crashed_create_is_reclaimed(file_store, tmp_path):
    from arcviz.projects import new_project

    (tmp_path / "data" / "placements").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "placements" / "plan.json").write_text("")  # claimed, then the process died
    assert file_store.list_documents("placement") == []
    file_store.create_document("placement", "plan", {"project": "shop"})
    assert file_store.read_document("placement", "plan")[0] == {"project": "shop"}
    (tmp_path / "data" / "projects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "projects" / "shop.json").write_text("")
    file_store.create_project(new_project("Shop", "shop"))
    assert file_store.read_project("shop")[0].title == "Shop"


def test_a_whitespace_only_claim_is_reclaimed_like_an_empty_one(file_store, tmp_path):
    (tmp_path / "data" / "placements").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "placements" / "plan.json").write_text(" \n")
    file_store.create_document("placement", "plan", {"project": "shop"})
    assert file_store.read_document("placement", "plan")[0] == {"project": "shop"}


def test_an_empty_latest_left_by_a_crashed_diagram_create_is_reclaimed(file_store, tmp_path):
    """#168: a create that died between claiming latest.json and writing it left an empty file (and maybe its version
    1). The id is not wedged: it reads as absent, and a new create takes it; the leftovers are moved aside, never
    deleted and never inherited."""
    directory = tmp_path / "data" / "diagrams" / "orders"
    (directory / "versions").mkdir(parents=True)
    (directory / "latest.json").write_text("")
    (directory / "versions" / "0001.json").write_text('{"meta": {}, "diagram": {}}')  # the dead create's version 1
    assert not file_store.exists("orders")
    assert [d.id for d in file_store.list_diagrams()] == []
    created = file_store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    assert file_store.load("orders").title == created.title
    assert [v.version for v in file_store.list_versions("orders")] == [1]
    assert file_store.list_versions("orders")[0].source == "initial"
    aside = list((tmp_path / "data" / "reclaimed").glob("orders-*/versions/0001.json"))
    assert len(aside) == 1 and aside[0].read_text() == '{"meta": {}, "diagram": {}}'
    with pytest.raises(StoreError, match="already exists"):
        file_store.create("Orders", "orders")  # a real diagram is never reclaimed


def test_an_empty_latest_of_a_diagram_with_history_is_never_reclaimed(file_store, tmp_path):
    """Review of #168: after a file-system crash an empty latest.json can belong to a live diagram. With more than one
    version or a transcript it is refused, and nothing is touched."""
    file_store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    file_store.snapshot_latest("orders", "zwei")
    file_store.append_chat("orders", [{"role": "user", "content": "hallo"}])
    directory = tmp_path / "data" / "diagrams" / "orders"
    (directory / "latest.json").write_text("")
    before = sorted(str(p.relative_to(directory)) for p in directory.rglob("*"))
    with pytest.raises(StoreError, match="not reclaimed"):
        file_store.create("Neu", "orders")
    assert sorted(str(p.relative_to(directory)) for p in directory.rglob("*")) == before
    assert len(file_store.list_versions("orders")) == 2 and len(file_store.load_chat("orders")) == 1
    assert (directory / "latest.json").read_text() == ""


def test_exists_treats_undecodable_content_as_present_and_reports_permission_errors(file_store, tmp_path, monkeypatch):
    """Review: `exists()` never lets a decode or permission error escape as such."""
    from pathlib import Path

    directory = tmp_path / "data" / "diagrams" / "odd"
    directory.mkdir(parents=True)
    (directory / "latest.json").write_bytes(b"\xff\xfe\x00")  # short and not UTF-8
    assert file_store.exists("odd")
    real = Path.stat

    def denied(self, *args, **kwargs):
        if self.name == "latest.json":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(StoreError, match="Permission denied") as caught:
        file_store.exists("odd")
    assert isinstance(caught.value.__cause__, PermissionError)


def test_writes_are_fsynced(file_store, monkeypatch):
    """Review of #168: every atomic write syncs the file before the rename and the directory after it."""
    import arcviz.store.file as file_module

    synced = []
    real = file_module.os.fsync
    monkeypatch.setattr(file_module.os, "fsync", lambda fd: synced.append(fd) or real(fd))
    file_store.create("Demo", "demo")
    assert len(synced) >= 4  # version file + its directory, latest.json + its directory


def test_a_whitespace_only_latest_is_reclaimed_too(file_store, tmp_path):
    directory = tmp_path / "data" / "diagrams" / "orders"
    directory.mkdir(parents=True)
    (directory / "latest.json").write_text(" \n")
    file_store.create("Orders", "orders")
    assert file_store.exists("orders")


def test_debris_of_an_interrupted_purge_does_not_block_the_id(file_store, tmp_path):
    """Third/fourth review #6/#2: a purge that stopped half-way leaves its tombstone, versions and a transcript; that is
    debris, not a live diagram: a create takes the id and moves the debris aside."""
    directory = tmp_path / "data" / "diagrams" / "orders"
    (directory / "versions").mkdir(parents=True)
    for n in (1, 2, 3):
        (directory / "versions" / f"000{n}.json").write_text('{"meta": {}, "diagram": {}}')
    (directory / "chat.json").write_text("[]")
    (directory / ".purging").write_text("latest removed")  # the tombstone of a purge that got past latest.json
    created = file_store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    assert created.id == "orders" and [v.version for v in file_store.list_versions("orders")] == [1]
    assert file_store.load_chat("orders") == []
    aside = list((tmp_path / "data" / "reclaimed").glob("orders-*"))
    assert len(aside) == 1 and (aside[0] / "chat.json").exists() and len(list((aside[0] / "versions").iterdir())) == 3


def test_a_purge_removes_latest_first(file_store, tmp_path, monkeypatch):
    """Third review #6: if a purge stops half-way, the diagram is already gone for readers (latest.json is removed
    first), so what is left is debris, never a diagram with missing history."""
    from pathlib import Path

    file_store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    file_store.snapshot_latest("orders", "zwei")
    real = Path.unlink
    removed = []

    def unlink(self, *args, **kwargs):
        removed.append(self.name)
        if len(removed) == 2:
            raise OSError("disk went away")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(StoreError):  # never a bare OSError (#117)
        file_store.delete("orders")
    monkeypatch.undo()
    assert removed[0] == "latest.json" and not file_store.exists("orders")


def test_history_without_latest_and_without_a_tombstone_is_refused(file_store, tmp_path):
    """Fourth review #2: versions and a transcript without latest.json and without a purge tombstone are a diagram whose
    latest state was lost (an operator case): the create is refused and the directory left exactly as it was."""
    directory = tmp_path / "data" / "diagrams" / "orders"
    (directory / "versions").mkdir(parents=True)
    for n in (1, 2):
        (directory / "versions" / f"000{n}.json").write_text('{"meta": {}, "diagram": {}}')
    (directory / "chat.json").write_text("[]")
    before = sorted(str(p.relative_to(directory)) for p in directory.rglob("*"))
    with pytest.raises(StoreError, match="not reclaimed"):
        file_store.create("Orders", "orders")
    assert sorted(str(p.relative_to(directory)) for p in directory.rglob("*")) == before  # our claim removed again
    assert not (tmp_path / "data" / "reclaimed").exists()


def test_purge_writes_its_tombstone_first_and_removes_it_last(file_store, tmp_path, monkeypatch):
    from pathlib import Path

    file_store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    real = Path.unlink

    def stop_at_chat(self, *args, **kwargs):
        if self.name == "chat-generation" or self.name.endswith(".json") and self.parent.name == "versions":
            raise OSError("disk went away")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stop_at_chat)
    with pytest.raises(StoreError):  # never a bare OSError (#117)
        file_store.delete("orders")
    monkeypatch.undo()
    directory = tmp_path / "data" / "diagrams" / "orders"
    assert (directory / ".purging").exists() and not (directory / "latest.json").exists()
    file_store.create("Orders", "orders")  # debris: the id is free again
    file_store.delete("orders")
    assert not directory.exists()


def test_a_purge_that_fails_before_latest_is_gone_removes_its_tombstone(file_store, tmp_path, monkeypatch):
    """Fifth review #4: the tombstone never stays next to a latest.json."""
    from pathlib import Path

    file_store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    real = Path.unlink

    def refuse_latest(self, *args, **kwargs):
        if self.name == "latest.json":
            raise OSError("disk went away")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_latest)
    with pytest.raises(StoreError):  # never a bare OSError (#117)
        file_store.delete("orders")
    monkeypatch.undo()
    directory = tmp_path / "data" / "diagrams" / "orders"
    assert not (directory / ".purging").exists() and file_store.exists("orders")


def test_a_tombstone_of_a_purge_that_never_removed_latest_is_no_licence_to_reclaim(file_store, tmp_path):
    """Fifth review #4: only a tombstone recording "latest removed" makes a directory debris."""
    directory = tmp_path / "data" / "diagrams" / "orders"
    (directory / "versions").mkdir(parents=True)
    for n in (1, 2):
        (directory / "versions" / f"000{n}.json").write_text('{"meta": {}, "diagram": {}}')
    (directory / ".purging").write_text("started")
    with pytest.raises(StoreError, match="not reclaimed"):
        file_store.create("Orders", "orders")
    assert not (directory / "latest.json").exists() and len(list((directory / "versions").iterdir())) == 2


# ---- durability and ordering (#117) -------------------------------------------------------------------------------


def test_versions_are_ordered_numerically_beyond_9999(file_store, demo):
    """#117: `10000.json` sorts before `9999.json` as a string; listing, numbering and undo go by the number."""
    d = file_store.load("demo")
    for number in (9998, 9999):
        info = file_store.list_versions("demo")[0].model_copy(update={"version": number, "label": f"v{number}"})
        file_store.put_version("demo", d, info)
    changed = d.model_copy(update={"title": "Neu"})
    file_store.snapshot(changed, "zehntausend", "auto")
    assert [v.version for v in file_store.list_versions("demo")] == [1, 9998, 9999, 10000]
    assert file_store.load_version("demo", 10000)[1].title == "Neu"
    assert file_store.snapshot(d, "next", "manual").version == 10001
    assert [i.version_count for i in file_store.list_diagrams()] == [5]


@pytest.mark.parametrize("content", ["", "  \n", '{"meta": {"version": 2', "\x00\x00"])
def test_an_empty_or_partial_version_file_is_a_store_error(file_store, demo, tmp_path, content):
    """#117: a version file a crash left empty or cut off is reported as a StoreError (HTTP 404), never a
    JSONDecodeError (HTTP 500); the listing skips it, so the rest of the history stays usable."""
    (tmp_path / "data" / "diagrams" / "demo" / "versions" / "0002.json").write_text(content)
    with pytest.raises(StoreError, match="Version 2 of diagram 'demo'"):
        file_store.load_version("demo", 2)
    assert [v.version for v in file_store.list_versions("demo")] == [1]
    assert file_store.snapshot(file_store.load("demo"), "next", "manual").version == 3  # never over the damaged file


def test_a_version_file_that_is_not_utf8_is_a_store_error_and_skipped(file_store, demo, tmp_path):
    """#117 (review): undecodable bytes in a snapshot without a sidecar never fail the listing."""
    (tmp_path / "data" / "diagrams" / "demo" / "versions" / "0002.json").write_bytes(b'{"meta": "\xff\xfe"}')
    with pytest.raises(StoreError, match="Version 2 of diagram 'demo'"):
        file_store.load_version("demo", 2)
    assert [v.version for v in file_store.list_versions("demo")] == [1]


def test_a_purge_racing_a_temporary_file_succeeds(file_store, demo, tmp_path, monkeypatch):
    """#117: a `.tmp-*` file that a concurrent write renames away (gone when the purge unlinks it) or creates (the
    directory is not empty when the purge removes it) does not fail the purge."""
    from pathlib import Path

    versions = tmp_path / "data" / "diagrams" / "demo" / "versions"
    (versions / ".tmp-renamed.json").write_text("{}")
    real_unlink, real_rmdir = Path.unlink, Path.rmdir
    raced = []

    def unlink(self, *args, **kwargs):
        if self.name == ".tmp-renamed.json" and not raced:
            raced.append("renamed")
            real_unlink(self)  # the concurrent write renamed it away just before
        return real_unlink(self, *args, **kwargs)

    def rmdir(self):
        if self == versions and "created" not in raced:
            raced.append("created")
            (versions / ".tmp-new.json").write_text("{}")  # a concurrent write starts in the directory
        return real_rmdir(self)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(Path, "rmdir", rmdir)
    file_store.delete("demo")
    assert raced == ["renamed", "created"]
    assert not (tmp_path / "data" / "diagrams" / "demo").exists()


def test_a_failing_purge_raises_a_store_error(file_store, demo, monkeypatch):
    """#117: whatever the file system reports, purge raises StoreError (never a bare OSError, which is a 500)."""
    from pathlib import Path

    real = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.parent.name == "versions":
            raise PermissionError("read-only")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(StoreError, match="could not be deleted completely") as caught:
        file_store.delete("demo")
    assert isinstance(caught.value.__cause__, PermissionError)


def test_the_transcript_generation_and_the_tombstone_are_fsynced(file_store, demo, monkeypatch):
    """#117: every file the store replaces is synced with its directory, also the plain-text ones."""
    import arcviz.store.file as file_module

    synced = []
    real = file_module.os.fsync
    monkeypatch.setattr(file_module.os, "fsync", lambda fd: synced.append(fd) or real(fd))
    file_module.FileStore._t_new_generation(file_store._diagram_owner("demo"))
    assert len(synced) == 2  # the generation file and its directory
    synced.clear()
    file_store.delete("demo")
    assert len(synced) >= 4  # the tombstone, twice, each with its directory


def test_a_purge_that_cannot_finish_leaves_its_tombstone(file_store, demo, tmp_path, monkeypatch):
    """#117: when the directory cannot be removed even after the tombstone was, the tombstone is written again, so what
    is left is debris that the next create of the id moves aside."""
    from pathlib import Path

    directory = tmp_path / "data" / "diagrams" / "demo"
    real = Path.rmdir

    def refuse(self):
        if self == directory:
            raise OSError("busy")
        return real(self)

    monkeypatch.setattr(Path, "rmdir", refuse)
    with pytest.raises(StoreError, match="could not be deleted completely"):
        file_store.delete("demo")
    monkeypatch.undo()
    assert (directory / ".purging").read_text() == "latest removed" and not file_store.exists("demo")
    file_store.create("Demo", "demo")  # the id is free: the debris is moved aside
    assert file_store.exists("demo")


@pytest.mark.parametrize("content", [b"{", b'{"version": "x"}', b"\xff\xfe", b""])
def test_a_damaged_metadata_sidecar_is_rebuilt_from_its_snapshot(file_store, demo, tmp_path, content):
    """Coordinator review #3: the sidecar is a cache of the snapshot's metadata; a cut-off, invalid or undecodable one
    never fails the history, it is read again from the snapshot and rewritten."""
    versions = tmp_path / "data" / "diagrams" / "demo" / "versions"
    (versions / "0001.meta.json").write_bytes(content)
    assert [v.version for v in file_store.list_versions("demo")] == [1]
    assert json.loads((versions / "0001.meta.json").read_text())["version"] == 1  # repaired
    assert file_store.history("demo") is not None


def test_a_version_whose_sidecar_and_snapshot_are_damaged_is_skipped(file_store, demo, tmp_path):
    d = file_store.load("demo")
    file_store.snapshot(d, "zwei", "manual")
    versions = tmp_path / "data" / "diagrams" / "demo" / "versions"
    (versions / "0002.meta.json").write_text("{")
    (versions / "0002.json").write_bytes(b"\xff")
    assert [v.version for v in file_store.list_versions("demo")] == [1]
    with pytest.raises(StoreError, match="Version 2 of diagram 'demo'"):
        file_store.load_version("demo", 2)


def test_a_purge_tolerates_a_directory_that_is_already_gone(file_store, demo, tmp_path, monkeypatch):
    """Coordinator review #7: a subdirectory that disappears before the purge removes it is fine, not an error."""
    import shutil
    from pathlib import Path

    versions = tmp_path / "data" / "diagrams" / "demo" / "versions"
    real = Path.rmdir

    def gone_meanwhile(self):
        if self == versions and versions.exists():
            shutil.rmtree(versions)  # removed by somebody else just before
            raise FileNotFoundError(str(self))
        return real(self)

    monkeypatch.setattr(Path, "rmdir", gone_meanwhile)
    file_store.delete("demo")
    assert not (tmp_path / "data" / "diagrams" / "demo").exists()
