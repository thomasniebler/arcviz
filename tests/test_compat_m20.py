"""M20 team workspaces move into personal workspaces, keeping every access explicit (issue #179).

Spec: docs/architecture/specs/personal-workspaces.md (*Migration*), ADR-0102, ADR-0103. The fixtures in
tests/fixtures/m20/ were written by the M20 code (`generate.py` there says how and what they hold), with
`expected.json`: every person's effective role on every project as M20 computed it. After the migration everybody
has that role (nobody loses or gains read or write access), except that a project has exactly one owner: one that a
member created (an explicit M20 owner) belongs to that member, and the former workspace owner keeps it as an editor.
Twice changes nothing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz.model import fingerprint
from arcviz.server import create_app
from arcviz.services import ServiceRoot, migrate_to_personal_workspaces
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.workspaces import Identity, personal_workspace_id
from tests.conftest import ScriptedProvider, Users
from tests.cosmos_fake import FakeContainer

FIXTURES = Path(__file__).parent / "fixtures" / "m20"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
ADMIN = Identity("admin", "Administrator")
PEOPLE = sorted(next(iter(EXPECTED["roles"].values())))
OWNERS = {"default": "admin", "webshop": "admin", "plattform": "ed", "labor": "olga", "uebergabe": "ed"}
REOWNED = {"plattform": ("ed", "olga"), "uebergabe": ("ed", "olga")}  # created by ed in olga's workspaces


def expected_now() -> dict[str, dict[str, str | None]]:
    """The M20 roles with the one change of the migration: a former workspace owner of a re-owned project edits it."""
    roles = json.loads(json.dumps(EXPECTED["roles"]))
    for project_id, (_, former) in REOWNED.items():
        assert roles[project_id][former] == "owner"
        roles[project_id][former] = "editor"
    return roles


@pytest.fixture(params=["file", "sqlite", "cosmos"])
def m20(request, tmp_path):
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


def roles_now(store) -> dict[str, dict[str, str | None]]:
    """Every person's effective role on every project, as the current code computes it."""
    root = ServiceRoot(store)
    out: dict[str, dict[str, str | None]] = {}
    for name in PEOPLE:
        listed = {p.id: p.role for p in root.for_actor(Identity(name, name)).projects.list_projects()}
        for project_id in EXPECTED["roles"]:
            out.setdefault(project_id, {})[name] = listed.get(project_id)
    return out


def test_fixtures_were_written_by_m20():
    assert EXPECTED["era"] == "M20"
    assert sorted(EXPECTED["workspaces"]) == ["default", "handover", "leer", "team"]
    assert EXPECTED["roles"]["plattform"]["ed"] == "owner"  # a member who created a project in olga's team
    assert EXPECTED["roles"]["labor"]["gone"] is None  # an inert role of a former member
    assert EXPECTED["projects"]["labor"]["roles"]["gone"] == "editor"
    assert not (FIXTURES / "file" / "workspaces" / f"{personal_workspace_id('admin')}.json").exists()


def test_before_the_migration_nobody_sees_team_projects(m20):
    """ADR-0103: until a project is in a personal workspace, nobody has access (no M20 fallback, no inert roles)."""
    assert all(role is None for roles in roles_now(m20).values() for role in roles.values())


def test_the_migration_keeps_every_effective_role(m20):
    report = migrate_to_personal_workspaces(m20, ADMIN)
    assert report.moved == OWNERS and report.stranded == {} and not report.waiting_for_admin
    assert report.reowned == REOWNED
    assert sorted(report.created) == ["admin", "ed", "olga"]
    assert roles_now(m20) == expected_now()  # nobody lost or gained read or write access
    for project_id, owner in OWNERS.items():
        project = m20.read_project(project_id)[0]
        assert project.workspace == personal_workspace_id(owner)
        assert owner not in project.roles  # the owner owns it through the workspace
    assert m20.read_project("labor")[0].roles == {"ed": "viewer", "vic": "viewer", "zed": "viewer"}  # gone dropped
    assert m20.read_project("plattform")[0].roles == {"olga": "editor", "vic": "editor", "zed": "viewer"}
    assert m20.read_project("uebergabe")[0].roles == {"olga": "editor"}  # created by ed before the handover
    assert all("owner" not in p.roles.values() for p, _ in m20.list_projects())  # exactly one owner per project
    assert report.shares == sum(len(m20.read_project(p)[0].roles) for p in OWNERS)


def test_diagrams_and_m20_documents_stay_untouched(m20):
    migrate_to_personal_workspaces(m20, ADMIN)
    for diagram_id, want in EXPECTED["diagrams"].items():
        assert fingerprint(m20.load(diagram_id)) == want["fingerprint"]
        got = [{k: v for k, v in i.model_dump().items() if k != "created_at"} for i in m20.list_versions(diagram_id)]
        assert got == want["versions"]  # authors included
    kept = {key: body for key, body, _ in m20.list_documents("workspace") if not key.startswith("user_")}
    assert {k: {"owner": b["owner"], "members": sorted(m["user"] for m in b["members"])} for k, b in kept.items()} == (
        EXPECTED["workspaces"]
    )  # read-only history; the policy ignores them


def test_the_migration_is_idempotent(m20):
    migrate_to_personal_workspaces(m20, ADMIN)
    before = {p.id: p.model_dump(mode="json") for p, _ in m20.list_projects()}
    again = migrate_to_personal_workspaces(m20, ADMIN)
    assert not again.changed and again.moved == {} and again.created == [] and again.shares == 0
    assert {p.id: p.model_dump(mode="json") for p, _ in m20.list_projects()} == before


def test_a_project_added_to_a_team_after_the_migration_is_migrated_with_its_members(m20):
    """A rolling upgrade: an M20 replica still writes into a team workspace after the M21 one migrated."""
    from arcviz.projects import new_project

    migrate_to_personal_workspaces(m20, ADMIN)
    late = new_project("Spät", "spaet", "", "team")
    late.roles = {"vic": "editor"}
    m20.create_project(late)
    report = migrate_to_personal_workspaces(m20, ADMIN)
    assert report.moved == {"spaet": "olga"}
    assert m20.read_project("spaet")[0].roles == {"ed": "viewer", "vic": "editor", "zed": "viewer"}


def test_a_conflicting_write_is_retried_with_fresh_data(m20, monkeypatch):
    original = m20.commit_project
    raced: list[str] = []

    def racing(project, expected):
        if project.id == "plattform" and not raced:  # an M20 replica changes a role in between
            raced.append("x")
            current, revision = m20.read_project("plattform")
            original(current.model_copy(update={"roles": {**current.roles, "zed": "editor"}}), revision)
        return original(project, expected)

    monkeypatch.setattr(m20, "commit_project", racing)
    migrate_to_personal_workspaces(m20, ADMIN)
    assert m20.read_project("plattform")[0].roles["zed"] == "editor"  # the newer role wins, nothing lost


def test_a_failed_workspace_create_assigns_nothing(m20, monkeypatch):
    from arcviz.ports import StoreError

    def broken(*args, **kwargs):
        raise StoreError("Cosmos DB write failed: 503")

    monkeypatch.setattr(m20, "create_document", broken)
    report = migrate_to_personal_workspaces(m20, ADMIN)  # reported per project, never raised (one bad one blocks none)
    assert set(report.failed) == set(OWNERS) and all("503" in e for e in report.failed.values())
    assert report.moved == {}
    assert all(not p.workspace.startswith("user_") for p, _ in m20.list_projects())  # nothing half-assigned
    monkeypatch.undo()
    assert migrate_to_personal_workspaces(m20, ADMIN).moved == OWNERS  # the next run retries them


def test_the_server_migrates_on_start_and_users_see_own_and_shared(tmp_path, monkeypatch):
    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    monkeypatch.setenv("ARCVIZ_AUTH", "proxy")
    monkeypatch.setenv("ARCVIZ_AUTH_PROXY_PRESET", "oauth2-proxy")
    monkeypatch.setenv("ARCVIZ_ADMIN_USER", "admin")
    users = Users(TestClient(create_app(store=FileStore(tmp_path / "file"), provider=ScriptedProvider([]))))

    def listed(user: str) -> dict[str, tuple[str, bool]]:  # without the user's own new default project
        projects = users(user, "GET", "/api/projects").json()
        return {p["id"]: (p["role"], p["shared"]) for p in projects if not p["id"].startswith("home_")}

    assert listed("olga") == {"labor": ("owner", False), "plattform": ("editor", True), "uebergabe": ("editor", True)}
    assert listed("ed") == {
        "default": ("viewer", True),
        "labor": ("viewer", True),
        "plattform": ("owner", False),
        "uebergabe": ("owner", False),
        "webshop": ("editor", True),
    }
    assert listed("out") == {}
    assert users("gone", "GET", "/api/projects/labor").status_code == 404
    assert users("vic", "PATCH", "/api/projects/labor", json={"title": "X"}).status_code == 403


def test_several_explicit_owners_the_first_by_id_owns_the_others_edit(m20):
    project, revision = m20.read_project("plattform")
    m20.commit_project(project.model_copy(update={"roles": {**project.roles, "zed": "owner"}}), revision)
    report = migrate_to_personal_workspaces(m20, ADMIN)
    assert report.reowned["plattform"] == ("ed", "olga")  # "ed" < "zed"
    moved = m20.read_project("plattform")[0]
    assert moved.workspace == personal_workspace_id("ed")
    assert moved.roles == {"olga": "editor", "vic": "editor", "zed": "editor"}


def test_cli_names_every_project_that_went_to_its_explicit_owner(tmp_path, capsys):
    from arcviz.cli import migrate_main

    shutil.copytree(FIXTURES / "file", tmp_path / "file")
    assert migrate_main(["--workspaces", f"file:{tmp_path / 'file'}", "--admin", "admin"]) == 0
    out = capsys.readouterr().out
    for project_id in REOWNED:
        assert f"note: project {project_id} moved to its explicit owner ed; the former workspace owner olga" in out
    assert "moved project labor -> the personal workspace of olga" in out
