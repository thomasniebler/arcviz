"""Data written by release v0.1.8 loads unchanged and moves into the admin's personal workspace (issues #163, #179).

Spec: docs/architecture/specs/personal-workspaces.md (*Migration*), ADR-0102. The fixtures in
tests/fixtures/v0_1_8/ were written by the v0.1.8 code (`generate.py` there says how): a file store, a sqlite
store and the items of the Cosmos fake, plus `expected.json` with what v0.1.8 answered for them. Every test copies
the fixture first, so the fixtures are never changed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange, render
from arcviz.model import fingerprint
from arcviz.server import create_app
from arcviz.services import AccessPolicy, ProjectService, migrate_to_personal_workspaces
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.workspaces import Identity, personal_workspace_id
from tests.conftest import ScriptedProvider
from tests.cosmos_fake import FakeContainer

FIXTURES = Path(__file__).parent / "fixtures" / "v0_1_8"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
ADMIN = Identity("admin", "Administrator")
HOME = personal_workspace_id("admin")
PROJECTS = ["default", "labor", "webshop"]


@pytest.fixture(params=["file", "sqlite", "cosmos"])
def legacy(request, tmp_path):
    if request.param == "file":
        shutil.copytree(FIXTURES / "file", tmp_path / "file")
        return FileStore(tmp_path / "file")
    if request.param == "sqlite":
        shutil.copy(FIXTURES / "arcviz.sqlite3", tmp_path / "arcviz.sqlite3")
        return SqliteStore(tmp_path / "arcviz.sqlite3")
    container = FakeContainer()
    for item in json.loads((FIXTURES / "cosmos_items.json").read_text(encoding="utf-8")):
        container.items[(item["diagram_id"], item["id"])] = item
    return CosmosStore(container)


def without_times(project: dict[str, Any]) -> dict[str, Any]:
    """A project dump without the timestamps that differ between the three fixture stores."""
    return {**project, "diagrams": [{k: v for k, v in m.items() if k != "added_at"} for m in project["diagrams"]]}


def project_dump(store, project_id: str) -> dict[str, Any]:
    return store.read_project(project_id)[0].model_dump(mode="json", exclude={"created_at", "updated_at"})


def short_chat(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: e[k] for k in ("role", "content", "seq")} for e in entries]


def test_fixtures_were_written_by_v018():
    assert EXPECTED["version"] == "0.1.8"
    assert sorted(EXPECTED["projects"]) == PROJECTS
    assert all("workspace" not in p for p in EXPECTED["projects"].values())  # written before workspaces
    assert EXPECTED["projects"]["webshop"]["elements"][0]["id"] == "order-service"  # with a shared model (M18)
    assert not (FIXTURES / "file" / "workspaces").exists() and not (FIXTURES / "file" / "users").exists()


@pytest.mark.parametrize("diagram_id", sorted(EXPECTED["diagrams"]))
def test_diagrams_without_notes_dump_without_a_description(legacy, diagram_id):
    """M10 notes (spec: collaboration.md, ADR-0092): data without notes keeps its JSON, also in versions."""
    assert '"description"' not in json.dumps(legacy.load(diagram_id).model_dump(mode="json"))
    for info in legacy.list_versions(diagram_id):
        _, version = legacy.load_version(diagram_id, info.version)
        assert '"description"' not in json.dumps(version.model_dump(mode="json"))


@pytest.mark.parametrize("diagram_id", sorted(EXPECTED["diagrams"]))
def test_diagrams_versions_and_transcripts_load_unchanged(legacy, diagram_id):
    want = EXPECTED["diagrams"][diagram_id]
    diagram = legacy.load(diagram_id)
    assert fingerprint(diagram) == want["fingerprint"]
    exported = exchange.export_document(diagram)
    assert {**exported, "exported_at": None} == {**want["export"], "exported_at": None}
    assert diagram.summary() == want["summary"]
    render.payload(diagram)
    assert [v.model_dump() for v in legacy.list_versions(diagram_id)] == want["versions"]  # no author keys appear
    assert legacy.undo_target(diagram_id) == want["undo_target"]
    assert legacy.redo_target(diagram_id) == want["redo_target"]
    assert short_chat(legacy.load_chat(diagram_id)) == want["chat"]


def test_projects_load_unchanged_before_the_migration(legacy):
    for project_id, want in EXPECTED["projects"].items():
        assert without_times(project_dump(legacy, project_id)) == without_times(want)
        assert short_chat(legacy.load_project_chat(project_id)) == EXPECTED["project_chats"][project_id]


def test_the_migration_moves_everything_into_the_admins_workspace_once(legacy):
    report = migrate_to_personal_workspaces(legacy, ADMIN)
    assert report.moved == dict.fromkeys(PROJECTS, "admin") and report.shares == 0 and not report.repair.changed
    assert report.created == ["admin"] and not report.waiting_for_admin and report.hidden == 0
    workspace = legacy.read_document("workspace", HOME)[0]
    assert workspace["owner"] == "admin" and workspace["personal"] is True
    for project_id, want in EXPECTED["projects"].items():
        got = project_dump(legacy, project_id)
        assert got.pop("workspace") == HOME
        assert without_times(got) == without_times(want)  # nothing else changed, no shares written
        assert short_chat(legacy.load_project_chat(project_id)) == EXPECTED["project_chats"][project_id]
    for diagram_id, want in EXPECTED["diagrams"].items():  # diagram data is never touched
        assert [v.model_dump() for v in legacy.list_versions(diagram_id)] == want["versions"]
        assert fingerprint(legacy.load(diagram_id)) == want["fingerprint"]

    again = migrate_to_personal_workspaces(legacy, ADMIN)
    assert not again.changed and again.moved == {} and again.created == []
    other = migrate_to_personal_workspaces(legacy, Identity("someone-else", "X"))
    assert not other.changed  # migrated projects stay with their owner


def test_after_the_migration_the_admin_owns_the_data_and_nobody_else_sees_it(legacy):
    migrate_to_personal_workspaces(legacy, ADMIN)
    policy = AccessPolicy(legacy, legacy)
    admin = ProjectService(legacy, legacy, policy.guard(ADMIN))
    assert [(p.id, p.role, p.shared) for p in admin.list_projects()] == [  # by title
        ("labor", "owner", False),
        ("default", "owner", False),
        ("webshop", "owner", False),
    ]
    assert admin.locate("checkout") == ("webshop", "zahlung")
    stranger = ProjectService(legacy, legacy, policy.guard(Identity("eve", "Eve")))
    assert stranger.list_projects() == [] and stranger.locate("checkout") is None


def test_proxy_mode_without_admin_keeps_the_data_hidden_until_one_is_configured(legacy):
    waiting = migrate_to_personal_workspaces(legacy, None)
    assert waiting.waiting_for_admin and waiting.hidden == 3 and waiting.moved == {}
    policy = AccessPolicy(legacy, legacy)
    for user in ("admin", "anyone"):  # not even a user called "admin" sees it
        assert ProjectService(legacy, legacy, policy.guard(Identity(user, user))).list_projects() == []
    later = migrate_to_personal_workspaces(legacy, Identity("8d1f-oid", "Ada"))
    assert later.moved == dict.fromkeys(PROJECTS, "8d1f-oid")
    owner = ProjectService(legacy, legacy, policy.guard(Identity("8d1f-oid", "Ada")))
    assert len(owner.list_projects()) == 3


def test_the_server_migrates_on_start(tmp_path, monkeypatch):
    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    store = FileStore(tmp_path / "file")
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    mine = client.get("/api/workspace").json()
    assert (mine["id"], mine["project_count"]) == (HOME, 4)  # the three migrated ones and the admin's own default
    assert client.get("/api/diagrams/checkout").status_code == 200
    TestClient(create_app(store=store, provider=ScriptedProvider([])))  # a second start changes nothing
    assert [p.workspace for p, _ in store.list_projects()] == [HOME] * 4  # with the admin's own default project


def test_cli_migrates_and_waits_for_an_admin(tmp_path, capsys):
    from arcviz.cli import migrate_main

    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    spec = f"file:{tmp_path / 'file'}"
    assert migrate_main(["--workspaces", spec]) == 0
    out = capsys.readouterr().out
    assert "stay hidden" in out and "--admin USER" in out
    assert migrate_main(["--workspaces", spec, "--admin", "ada"]) == 0
    out = capsys.readouterr().out
    assert "created the personal workspace of ada" in out
    assert "moved project webshop -> the personal workspace of ada" in out
    assert migrate_main(["--workspaces", spec]) == 0
    assert "workspaces: nothing to migrate" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        migrate_main(["--projects", spec, "--admin", "ada"])
    with pytest.raises(SystemExit):
        migrate_main(["--workspaces", spec, "--set-owner", "default=bob"])  # gone with team workspaces


def test_a_failing_repair_never_stops_the_start(tmp_path, monkeypatch, caplog):
    from arcviz.ports import StoreError
    from arcviz.services import ProjectService

    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    store = FileStore(tmp_path / "file")

    def broken(self):
        raise StoreError("Cosmos DB: 429 too many requests")

    monkeypatch.setattr(ProjectService, "reconcile", broken)
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))  # starts
    assert "repair" in caplog.text and "429" in caplog.text
    # the workspace migration itself still ran (its guarantees do not depend on the repair)
    assert [p.workspace for p, _ in store.list_projects()] == [HOME] * 3
    assert client.get("/api/diagrams/checkout").status_code == 200


def test_a_failing_migration_never_stops_the_start(tmp_path, monkeypatch, caplog):
    from arcviz.ports import StoreError
    from arcviz.services import ServiceRoot

    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    store = FileStore(tmp_path / "file")

    def broken(self, admin):
        raise StoreError("Cosmos DB write failed: 503")

    monkeypatch.setattr(ServiceRoot, "migrate", broken)
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    assert "arcviz-migrate --workspaces" in caplog.text and "503" in caplog.text
    assert client.get("/healthz").status_code == 200
    legacy = [
        p for p, _ in store.list_projects() if not p.id.startswith("home_")
    ]  # not the admin's own default project
    assert all(p.workspace is None for p in legacy)  # nothing half-assigned


def test_cli_reassigns_the_projects_of_a_missing_workspace(tmp_path, capsys):
    from arcviz.cli import migrate_main

    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    spec = f"file:{tmp_path / 'file'}"
    assert migrate_main(["--workspaces", spec, "--admin", "ada"]) == 0
    store = FileStore(tmp_path / "file")
    project, revision = store.read_project("webshop")
    store.commit_project(project.model_copy(update={"workspace": "gone", "roles": {"x": "editor"}}), revision)
    capsys.readouterr()
    assert migrate_main(["--workspaces", spec]) == 0
    out = capsys.readouterr().out
    assert "workspace gone does not exist" in out and "--reassign gone=USER" in out
    assert migrate_main(["--workspaces", spec, "--reassign", "gone=bob"]) == 0
    assert "reassigned project webshop -> the personal workspace of bob" in capsys.readouterr().out
    moved = store.read_project("webshop")[0]
    assert moved.workspace == personal_workspace_id("bob") and moved.roles == {}  # nobody else gains access
    with pytest.raises(SystemExit):
        migrate_main(["--workspaces", spec, "--reassign", "gone"])
    assert migrate_main(["--workspaces", spec, "--reassign", "default=bob"]) == 1  # waits for an admin instead


def test_cli_reassign_strips_before_validating(tmp_path):
    from arcviz.cli import migrate_main

    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    with pytest.raises(SystemExit):
        migrate_main(["--workspaces", f"file:{tmp_path / 'file'}", "--reassign", " =default"])


def test_projects_of_a_lost_personal_workspace_can_be_reassigned(legacy):
    from arcviz.services import reassign_workspace

    migrate_to_personal_workspaces(legacy, ADMIN)
    _, revision = legacy.read_document("workspace", HOME)
    legacy.delete_document("workspace", HOME, revision)  # lost, e.g. restored from an older backup
    report = migrate_to_personal_workspaces(legacy, None)
    assert report.stranded == {HOME: sorted(PROJECTS)}
    assert sorted(reassign_workspace(legacy, HOME, "bob")) == sorted(PROJECTS)
    assert {legacy.read_project(p)[0].workspace for p in PROJECTS} == {personal_workspace_id("bob")}


def test_diagrams_from_before_projects_go_to_the_admin_even_without_any_project(tmp_path):
    from arcviz.model import Diagram

    store = FileStore(tmp_path / "data")
    store.create("Alt", "alt", Diagram(id="alt", title="Alt"))  # written before projects existed: an orphan
    report = migrate_to_personal_workspaces(store, ADMIN)
    assert report.stranded == {} and report.repair.adopted == ["alt"]
    project = store.read_project("default")[0]
    assert project.workspace == HOME and project.member("alt") is not None
    admin = ProjectService(store, store, AccessPolicy(store, store).guard(ADMIN))
    assert admin.locate("alt") == ("default", None)
