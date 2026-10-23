from arcviz import operations as ops
from arcviz import projects
from arcviz.cli import migrate_main as main
from arcviz.model import Diagram
from arcviz.services import ProjectService
from arcviz.store.file import FileStore
from arcviz.store.migrate import migrate
from arcviz.store.sqlite import SqliteStore
from arcviz.templates import demo_diagram


def _populate(repo):
    repo.create("Demo", "demo", demo_diagram("demo", "Demo"))
    d = repo.load("demo")
    ops.add_node(d, "x", kind="service", id="x")
    repo.save_latest(d)
    repo.snapshot(d, "added x", "auto")
    repo.append_chat(
        "demo", [{"role": "user", "content": "add x"}, {"role": "assistant", "content": "done", "version": 2}]
    )
    repo.compact_chat("demo", 1, {"role": "summary", "content": "s"})
    repo.create("Empty", "empty")


def test_migrate_copies_everything_and_is_idempotent(tmp_path):
    src = FileStore(tmp_path / "src")
    _populate(src)
    dst = SqliteStore(tmp_path / "dst.sqlite3")

    report = migrate(src, dst)
    assert sorted(report.copied) == ["demo", "empty"] and report.skipped == []
    assert dst.load("demo").model_dump() == src.load("demo").model_dump()
    assert [v.model_dump() for v in dst.list_versions("demo")] == [v.model_dump() for v in src.list_versions("demo")]
    assert dst.load_chat("demo") == src.load_chat("demo")
    assert [m["seq"] for m in dst.load_chat("demo")] == [1, 2]
    assert dst.list_versions("empty")[0].created_at == src.list_versions("empty")[0].created_at

    again = migrate(src, dst)
    assert again.copied == [] and sorted(again.skipped) == ["demo", "empty"]
    assert len(dst.list_versions("demo")) == 2  # nothing duplicated

    forced = migrate(src, dst, diagram_ids=["demo"], overwrite=True)
    assert forced.copied == ["demo"] and len(dst.list_versions("demo")) == 2


def test_cli_roundtrip_to_file_export(tmp_path, capsys):
    src = SqliteStore(tmp_path / "src.sqlite3")
    _populate(src)
    code = main(["--from", f"sqlite:{tmp_path / 'src.sqlite3'}", "--to", f"file:{tmp_path / 'export'}"])
    assert code == 0
    out = capsys.readouterr().out
    assert "demo" in out and "empty" in out
    exported = FileStore(tmp_path / "export")
    assert exported.load("demo").nodes.keys() == src.load("demo").nodes.keys()
    assert (tmp_path / "export" / "diagrams" / "demo" / "versions" / "0002.json").exists()


def _with_project(repo):
    _populate(repo)
    project = projects.new_project("Shop", "shop")
    projects.apply(project, "add_section", {"title": "Backend"})
    projects.apply(project, "add_diagram", {"diagram": "demo", "section": "backend"})
    repo.create_project(project)
    return project


def test_migrate_copies_projects_and_skips_existing_ones(tmp_path):
    src = FileStore(tmp_path / "src")
    project = _with_project(src)
    dst = SqliteStore(tmp_path / "dst.sqlite3")
    report = migrate(src, dst)
    assert report.projects_copied == ["shop"] and report.projects_skipped == []
    assert dst.read_project("shop")[0] == project
    again = migrate(src, dst)
    assert again.projects_copied == [] and again.projects_skipped == ["shop"]
    projects.apply(project, "rename_project", {"title": "Renamed"})
    src.commit_project(project, src.read_project("shop")[1])
    forced = migrate(src, dst, overwrite=True)
    assert forced.projects_copied == ["shop"] and dst.read_project("shop")[0].title == "Renamed"


def test_migrate_copies_workspaces_and_users(tmp_path):
    src = FileStore(tmp_path / "src")
    src.create_document("workspace", "team", {"id": "team", "title": "Team", "owner": "ada"})
    src.create_document("user", "u-1", {"id": "ada", "name": "Ada"})
    dst = SqliteStore(tmp_path / "dst.sqlite3")
    report = migrate(src, dst)
    assert report.documents_copied == ["workspace:team", "user:u-1"]
    assert dst.read_document("workspace", "team")[0]["owner"] == "ada"
    assert dst.read_document("user", "u-1")[0] == {"id": "ada", "name": "Ada"}
    again = migrate(src, dst)
    assert again.documents_copied == [] and again.documents_skipped == ["workspace:team", "user:u-1"]
    src.commit_document(
        "workspace", "team", {"id": "team", "title": "Neu", "owner": "ada"}, src.read_document("workspace", "team")[1]
    )
    forced = migrate(src, dst, overwrite=True)
    assert "workspace:team" in forced.documents_copied and dst.read_document("workspace", "team")[0]["title"] == "Neu"


def test_cli_copy_places_diagrams_without_a_project_on_the_target(tmp_path, capsys):
    src = FileStore(tmp_path / "src")
    _with_project(src)
    code = main(["--from", f"file:{tmp_path / 'src'}", "--to", f"sqlite:{tmp_path / 'dst'}"])
    assert code == 0
    out = capsys.readouterr().out
    assert "project shop" in out and "adopted empty -> default" in out
    dst = SqliteStore(tmp_path / "dst" / "arcviz.sqlite3")
    service = ProjectService(dst, dst)
    assert service.locate("demo") == ("shop", "backend") and service.locate("empty") == ("default", None)


def test_cli_projects_puts_existing_diagrams_into_the_default_project(tmp_path, capsys):
    repo = FileStore(tmp_path / "data")
    _populate(repo)
    assert main(["--projects", f"file:{tmp_path / 'data'}"]) == 0
    out = capsys.readouterr().out
    assert "adopted demo -> default" in out and "adopted empty -> default" in out
    from arcviz.services.locations import read_location

    assert read_location(repo, "demo").projects == ["default"]  # adopted with its locator entry (#181)
    assert ProjectService(repo, repo).get("default").tree()["diagrams"] == ["demo", "empty"]
    assert main(["--projects", f"file:{tmp_path / 'data'}"]) == 0
    assert "nothing to repair" in capsys.readouterr().out


def test_cli_needs_either_a_copy_or_projects(tmp_path, capsys):
    import pytest

    with pytest.raises(SystemExit):
        main([])
    with pytest.raises(SystemExit):
        main(["--projects", "file:x", "--from", "file:y", "--to", "file:z"])
    assert main(["--projects", "nope:x"]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_projects_lists_quarantined_orphans_and_adopts_them_on_request(tmp_path, capsys):
    import pytest

    repo = FileStore(tmp_path / "data")
    ProjectService(repo, repo).create("Shop", "shop")
    repo.create("Plan", "plan", Diagram(id="plan", title="Plan"))
    repo.create_document("placement", "plan", {"project": "gone", "at": "2020-01-01T00:00:00+00:00"})
    spec = f"file:{tmp_path / 'data'}"
    assert main(["--projects", spec]) == 0
    out = capsys.readouterr().out
    assert "quarantined plan" in out and "--adopt plan=PROJECT" in out
    assert ProjectService(repo, repo).locate("plan") is None
    assert main(["--projects", spec, "--adopt", "plan=shop"]) == 0
    assert "adopted plan -> shop" in capsys.readouterr().out
    assert ProjectService(repo, repo).locate("plan") == ("shop", None)
    assert repo.list_documents("placement") == []
    with pytest.raises(SystemExit):
        main(["--projects", spec, "--adopt", "plan"])
    with pytest.raises(SystemExit):
        main(["--adopt", "plan=shop"])


def test_migrate_keeps_the_numbers_of_a_pruned_history_file_to_sqlite_to_cosmos(tmp_path):
    """Review of #194: a history with gaps from the retention policy (ADR-0130) keeps its numbers and a valid
    `restored_from` through file -> sqlite -> cosmos (fake)."""
    from arcviz.history import HistoryConfig
    from arcviz.store import Change
    from arcviz.store.cosmos import CosmosStore
    from tests.cosmos_fake import FakeContainer

    src = FileStore(tmp_path / "src")
    src.create("Demo", "demo", demo_diagram("demo", "Demo"))
    for i in range(14):
        src.update("demo", lambda d, i=i: Change(_with(d, f"n{i}"), None, label=f"c{i}"))
    src.retention = HistoryConfig(keep=10, slack=0)
    src.update("demo", lambda d: Change(_with(d, "last"), None, label="last"))
    src.undo("demo")  # restored_from names a kept version
    numbers = [v.version for v in src.list_versions("demo")]
    assert numbers[0] == 1 and numbers[1] > 2  # a gap after the first version

    middle = SqliteStore(tmp_path / "mid.sqlite3")
    target = CosmosStore(FakeContainer())
    try:
        migrate(src, middle)
        migrate(middle, target)
        for repo in (middle, target):
            assert [v.model_dump() for v in repo.list_versions("demo")] == [
                v.model_dump() for v in src.list_versions("demo")
            ]
            undone = repo.list_versions("demo")[-1]
            assert undone.source == "undo" and repo.load_version("demo", undone.restored_from)
            assert repo.undo_target("demo") == src.undo_target("demo")
            assert repo.redo_target("demo") == src.redo_target("demo")
            nxt = repo.snapshot_latest("demo", "after the migration")
            assert nxt.version == numbers[-1] + 1  # numbering continues after the highest
    finally:
        middle.close()


def _with(diagram, node_id):
    ops.add_node(diagram, node_id, kind="service", id=node_id)
    return diagram


def test_an_interrupted_migration_is_never_continued_into_a_mixed_history(tmp_path):
    """Second review of #194: versions without a latest on the target (a run that died before write_latest) are an
    error without --overwrite, and replaced with it."""
    import pytest

    from arcviz.store import StoreError

    src = FileStore(tmp_path / "src")
    _populate(src)
    dst = SqliteStore(tmp_path / "dst.sqlite3")
    try:
        real = dst.write_latest

        def crash(diagram):
            raise RuntimeError("killed")

        dst.write_latest = crash  # the first run dies after copying the versions of "demo"
        with pytest.raises(RuntimeError):
            migrate(src, dst, diagram_ids=["demo"])
        dst.write_latest = real
        assert not dst.exists("demo") and dst.list_versions("demo")
        # meanwhile the source moved on: a re-run must not put its history next to the stale one
        src.snapshot_latest("demo", "after the crash")
        with pytest.raises(StoreError, match="--overwrite"):
            migrate(src, dst, diagram_ids=["demo"])
        migrate(src, dst, diagram_ids=["demo"], overwrite=True)
        assert [v.model_dump() for v in dst.list_versions("demo")] == [
            v.model_dump() for v in src.list_versions("demo")
        ]
    finally:
        dst.close()
