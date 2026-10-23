"""`arcviz-migrate --requirements`: dangling references are removed on both sides (spec: requirements.md)."""

from __future__ import annotations

from arcviz.cli import migrate_main
from arcviz.services import ServiceRoot
from arcviz.store.file import FileStore
from arcviz.workspaces import Identity


def test_the_repair_removes_dangling_references(tmp_path, capsys):
    store = FileStore(tmp_path / "data")
    root = ServiceRoot(store)
    ada = Identity("ada", "Ada", "ada@example.org")
    root.users.seen(ada)
    svc = root.for_actor(ada)
    svc.projects.create("Shop", "shop")
    svc.projects.apply("shop", "add_stakeholder", {"name": "Betrieb"})
    svc.requirements.apply("shop", "add_requirement", {"raw": "alpha alpha", "stakeholders": ["s1"]})
    svc.projects.apply("shop", "add_driver", {"title": "Kosten", "requirements": ["req1"]})
    project, revision = store.read_project("shop")
    project.stakeholders.clear()  # a stakeholder removed behind the services' back
    project.drivers[0].requirements.append("req77")  # and a link to a requirement that is not there
    store.commit_project(project, revision)
    assert migrate_main(["--requirements", f"file:{tmp_path / 'data'}"]) == 0
    out = capsys.readouterr().out
    assert "project shop: 1 dangling reference(s) in the requirements, 1 in the project removed" in out
    assert "1 project(s) repaired" in out
    assert svc.projects.get("shop").drivers[0].requirements == ["req1"]
    assert svc.requirements.get("shop", "req1")["stakeholders"] == []
    assert migrate_main(["--requirements", f"file:{tmp_path / 'data'}"]) == 0
    assert "nothing to repair" in capsys.readouterr().out


def test_the_option_excludes_the_others(tmp_path, capsys):
    try:
        migrate_main(["--requirements", f"file:{tmp_path}", "--projects", f"file:{tmp_path}"])
    except SystemExit as exc:
        assert exc.code == 2
    assert "use one of" in capsys.readouterr().err
