"""Findings of the M21 review (PR #180): share lists never reach non-owners, squatted reserved ids, per-project
migration failures, the owner choice, user enumeration through sharing, listings proportional to the caller, Cosmos
TTL (spec: docs/architecture/specs/personal-workspaces.md, ADR-0100..0104)."""

from __future__ import annotations

import pytest

from arcviz import limits
from arcviz.ports import StoreError
from arcviz.projects import ProjectError, new_project
from arcviz.services import ServiceRoot, migrate_to_personal_workspaces
from arcviz.services.access import WORKSPACE
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity, Member, Workspace, personal_project_id, personal_workspace_id
from tests.conftest import proxy_users

ADA, BOB, EVE = (Identity(n, n.capitalize(), f"{n}@example.org", email_trusted=True) for n in ("ada", "bob", "eve"))


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for who in (ADA, BOB, EVE):
        root.users.seen(who)
    return root


# ---- 1. the share list never reaches a non-owner -----------------------------------------------------------


def test_project_responses_never_carry_the_share_list(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    for name in ("bob", "eve"):
        users(name, "GET", "/api/me")
    users("ada", "POST", "/api/projects", json={"title": "Shop"})
    users("ada", "POST", "/api/projects/shop/access", json={"user": "bob", "role": "viewer"})
    users("ada", "POST", "/api/projects/shop/access", json={"user": "eve", "role": "editor"})
    for name in ("ada", "bob", "eve"):
        view = users(name, "GET", "/api/projects/shop").json()
        assert "roles" not in view["project"], name
        renamed = users(name, "PATCH", "/api/projects/shop", json={"description": "x"})
        if renamed.status_code == 200:
            assert "roles" not in renamed.json()["project"]
        for summary in users(name, "GET", "/api/projects").json():
            assert "roles" not in summary
        bundle = users(name, "GET", "/api/projects/shop/export").json()
        assert "roles" not in bundle["project"] and "shares" not in bundle["project"]
        assert "bob" not in str(bundle) and "eve" not in str(bundle)


# ---- 2. squatted reserved ids --------------------------------------------------------------------------------


def test_the_migration_renames_a_project_that_squats_a_reserved_id(store):
    team = Workspace(id="team", title="Team", owner="eve", members=[Member(user="bob", added_at="t")])
    store.create_document(WORKSPACE, "team", team.model_dump(mode="json"))
    squat = new_project("Falle", personal_project_id("ada"), "", "team")  # M20 allowed any valid id
    store.create_project(squat)
    store.append_project_chat(squat.id, [{"role": "user", "content": "hallo"}])
    other = new_project("Auch", "user_" + "0" * 32, "", "team")
    store.create_project(other)
    report = migrate_to_personal_workspaces(store, None)
    assert set(report.renamed) == {squat.id, other.id}
    renamed = store.read_project(report.renamed[squat.id])[0]
    assert renamed.title == "Falle" and renamed.workspace == personal_workspace_id("eve")
    assert [e["content"] for e in store.load_project_chat(renamed.id)] == ["hallo"]
    with pytest.raises(StoreError):
        store.read_project(squat.id)
    root = ServiceRoot(store)
    root.users.seen(ADA)
    assert root.for_actor(ADA).projects.ensure_own_default().id == personal_project_id("ada")


def test_a_squatted_default_project_id_falls_back_to_a_recorded_free_one(root, store, caplog):
    store.create_project(new_project("Fremd", personal_project_id("ada"), "", personal_workspace_id("bob")))
    ada = root.for_actor(ADA).projects
    first = ada.ensure_own_default()
    assert first.id != personal_project_id("ada") and first.workspace == personal_workspace_id("ada")
    assert store.read_document(WORKSPACE, personal_workspace_id("ada"))[0]["default_project"] == first.id
    assert root.for_actor(ADA).projects.ensure_own_default().id == first.id  # recorded: stable, no error
    root.for_actor(ADA).diagrams.create("Skizze", "skizze", demo_diagram("skizze", "Skizze"))
    assert root.for_actor(ADA).projects.locate("skizze") == (first.id, None)
    assert caplog.text.count("squatted") == 1  # logged once


# ---- 3. one bad project never blocks the others --------------------------------------------------------------


def test_a_failing_project_is_reported_and_the_others_migrate(store, monkeypatch):
    team = Workspace(id="team", title="Team", owner="eve")
    store.create_document(WORKSPACE, "team", team.model_dump(mode="json"))
    for pid in ("aaa", "bbb", "ccc"):
        store.create_project(new_project(pid.upper(), pid, "", "team"))
    original = store.commit_project

    def broken(project, expected):
        if project.id == "bbb":
            raise StoreError("Cosmos DB write failed: 500")
        return original(project, expected)

    monkeypatch.setattr(store, "commit_project", broken)
    report = migrate_to_personal_workspaces(store, None)
    assert set(report.moved) == {"aaa", "ccc"} and "bbb" in report.failed and "500" in report.failed["bbb"]
    monkeypatch.undo()
    again = migrate_to_personal_workspaces(store, None)  # a later run retries it
    assert again.moved == {"bbb": "eve"} and again.failed == {}


# ---- 4. the owner choice ---------------------------------------------------------------------------------------


def test_the_workspace_owner_keeps_a_project_they_own_explicitly(store):
    team = Workspace(id="team", title="Team", owner="olga", members=[Member(user="ed", added_at="t")])
    store.create_document(WORKSPACE, "team", team.model_dump(mode="json"))
    project = new_project("Plan", "plan", "", "team")
    project.roles = {"olga": "owner", "ed": "owner"}  # the creator (olga) made ed an owner too
    store.create_project(project)
    report = migrate_to_personal_workspaces(store, None)
    assert report.moved == {"plan": "olga"} and "plan" not in report.reowned
    assert store.read_project("plan")[0].roles == {"ed": "editor"}


def test_the_default_project_goes_to_the_owner_of_the_m20_default_workspace(store):
    default = Workspace(id="default", title="Standard", owner="admin", members=[Member(user="ed", added_at="t")])
    store.create_document(WORKSPACE, "default", default.model_dump(mode="json"))
    project = new_project("Standardprojekt", "default", "", "default")
    project.roles = {"ed": "owner"}  # explicit roles never take the reserved project away from its owner
    store.create_project(project)
    report = migrate_to_personal_workspaces(store, Identity("admin", "Admin"))
    assert report.moved == {"default": "admin"} and report.reowned == {}
    assert store.read_project("default")[0].roles == {"ed": "editor"}


# ---- 5. no user enumeration through sharing --------------------------------------------------------------------


def test_sharing_reveals_nothing_beyond_what_the_owner_typed(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    users("bob", "GET", "/api/me")
    home = personal_project_id("ada")
    users("ada", "GET", "/api/me")
    shared = users("ada", "POST", f"/api/projects/{home}/access", json={"user": "bob", "role": "viewer"}).json()
    assert shared["added"] == {"user": "bob", "name": "Bob"}  # no e-mail for a share made by id
    assert all("email" not in s for s in shared["shares"])
    unknown = users("ada", "POST", f"/api/projects/{home}/access", json={"user": "ghost", "role": "viewer"})
    users("mallory", "GET", "/api/me")  # an untrusted duplicate would make an address ambiguous
    users.client.get("/api/me", headers={"x-forwarded-user": "twin", "x-forwarded-email": "bob@example.org"})
    ambiguous = users("ada", "POST", f"/api/projects/{home}/access", json={"user": "bob@example.org", "role": "viewer"})
    assert unknown.status_code == ambiguous.status_code == 400
    generic = unknown.json()["detail"].replace("ghost", "X")
    assert generic == ambiguous.json()["detail"].replace("bob@example.org", "X")  # one message for every miss


def test_sharing_has_its_own_rate_class_on_by_default():
    assert limits.rate_class("POST", "/api/projects/p/access") == "share"
    assert limits.rate_class("DELETE", "/api/projects/p/access/bob") == "share"
    assert limits.rate_class("GET", "/api/projects/p/access") is None
    assert limits.LimitConfig.from_env({}).rates["share"] > 0


# ---- 6. only "already exists" is a concurrent create -----------------------------------------------------------


def test_a_failing_create_of_the_default_project_reports_its_cause(root, store, monkeypatch):
    root.for_actor(ADA).guard.own_workspace()

    def broken(project):
        raise StoreError("Cosmos DB write failed: 503")

    monkeypatch.setattr(store, "create_project", broken)
    with pytest.raises(StoreError, match="503"):
        root.for_actor(ADA).projects.ensure_own_default()


# ---- 7. listings are proportional to the caller ----------------------------------------------------------------


def test_listings_never_scan_every_project(root, store, monkeypatch):
    root.for_actor(ADA).projects.create("Shop", "shop")
    root.for_actor(BOB).projects.create("Bobs", "bobs")
    root.for_actor(BOB).projects.share("bobs", "ada", "editor")
    root.for_actor(EVE).projects.create("Geheim", "geheim")

    def scan():
        raise AssertionError("a caller's listing scanned every project")

    monkeypatch.setattr(store, "list_projects", scan)
    ada = root.for_actor(ADA)
    assert sorted(p.id for p in ada.projects.list_projects()) == ["bobs", "shop"]
    mine = ada.workspaces.mine()
    assert (mine.project_count, mine.shared_count) == (1, 1)
    assert ada.projects.visible_diagrams() == set()
    root.for_actor(BOB).projects.revoke("bobs", "ada")
    assert [p.id for p in root.for_actor(ADA).projects.list_projects()] == ["shop"]


def test_the_index_is_validated_on_read_and_repaired_by_the_migration(root, store):
    root.for_actor(ADA).projects.create("Shop", "shop")
    root.for_actor(BOB).projects.create("Bobs", "bobs")
    project, revision = store.read_project("bobs")
    store.commit_project(project.model_copy(update={"roles": {"ada": "viewer"}}), revision)  # a share without index
    assert [p.id for p in root.for_actor(ADA).projects.list_projects()] == ["shop"]
    report = migrate_to_personal_workspaces(store, None)
    assert report.indexed >= 1
    assert sorted(p.id for p in root.for_actor(ADA).projects.list_projects()) == ["bobs", "shop"]
    store.delete_project("shop", store.read_project("shop")[1])  # gone: a stale index entry is skipped
    assert [p.id for p in root.for_actor(ADA).projects.list_projects()] == ["bobs"]


# ---- 9. Cosmos TTL --------------------------------------------------------------------------------------------


class FakeDatabase:
    def __init__(self, ttl, allowed=True):
        self.ttl, self.allowed, self.replaced = ttl, allowed, []

    def replace_container(self, container, partition_key, default_ttl=None, **kwargs):
        if not self.allowed:
            from azure.cosmos import exceptions

            err = exceptions.CosmosHttpResponseError(message="forbidden")
            err.status_code = 403
            raise err
        self.replaced.append(default_ttl)
        self.ttl = default_ttl


class FakeProxy:
    def __init__(self, database):
        self.database = database
        self.id = "arcviz"

    def read(self):
        props = {"id": "arcviz", "partitionKey": {"paths": ["/diagram_id"]}}
        if self.database.ttl is not None:
            props["defaultTtl"] = self.database.ttl
        return props


def test_cosmos_ttl_is_turned_on_at_start_up_or_reported(caplog):
    from arcviz.store.cosmos import ensure_ttl

    on = FakeDatabase(ttl=None)
    assert ensure_ttl(on, FakeProxy(on)) == "enabled" and on.replaced == [-1]
    already = FakeDatabase(ttl=-1)
    assert ensure_ttl(already, FakeProxy(already)) == "on" and already.replaced == []
    denied = FakeDatabase(ttl=None, allowed=False)
    assert ensure_ttl(denied, FakeProxy(denied)) == "off"
    assert "Time to Live" in caplog.text


# ---- 8. cleanups -----------------------------------------------------------------------------------------------


def test_one_presence_protocol_and_no_dead_parameters():
    import inspect

    from arcviz import live, ports, server
    from arcviz.services import ProjectExchangeService

    assert not hasattr(ports, "PresencePort") and hasattr(live, "PresenceChannel")
    assert "workspace" not in inspect.signature(ProjectExchangeService.import_bundle).parameters
    assert "def person(" not in inspect.getsource(server)


def test_an_unknown_reference_never_creates_anything(root, store):
    root.for_actor(ADA).projects.create("Shop", "shop")
    with pytest.raises(ProjectError):
        root.for_actor(ADA).projects.share("shop", "nobody", "viewer")
    assert store.read_project("shop")[0].roles == {}
