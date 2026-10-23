"""Findings of the second M21 review (PR #180): a recorded default project that is not the caller's, listing entries
removed after a concurrent re-share or re-create, Cosmos container settings kept when Time to Live is turned on, the
owner's e-mail address, listing reads that fail, crash-safe renames, counts from the listing (spec:
docs/architecture/specs/personal-workspaces.md, ADR-0100..0104)."""

from __future__ import annotations

import pytest

from arcviz.ports import StoreError
from arcviz.projects import Membership, new_project
from arcviz.services import ServiceRoot, migrate_to_personal_workspaces
from arcviz.services.access import WORKSPACE
from arcviz.services.listing import read_listing
from arcviz.services.workspaces import RENAME
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity, Workspace, personal_project_id, personal_workspace_id
from tests.conftest import proxy_users

ADA, BOB, EVE = (Identity(n, n.capitalize(), f"{n}@example.org", email_trusted=True) for n in ("ada", "bob", "eve"))


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for who in (ADA, BOB, EVE):
        root.users.seen(who)
    return root


# ---- 1. a recorded default project that is no longer the caller's -------------------------------------------------


def test_a_recorded_default_project_taken_by_somebody_else_is_replaced(root, store):
    store.create_project(new_project("Fremd", personal_project_id("ada"), "", personal_workspace_id("bob")))
    first = root.for_actor(ADA).projects.ensure_own_default()  # a recorded variant
    root.for_actor(ADA).projects.delete(first.id)  # ada deletes it ...
    taken = root.for_actor(BOB).projects.create("Bobs", first.id)  # ... and bob takes the free id
    assert taken.id == first.id
    again = root.for_actor(ADA).projects.ensure_own_default()
    assert again.workspace == personal_workspace_id("ada") and again.id != first.id
    assert store.read_document(WORKSPACE, personal_workspace_id("ada"))[0]["default_project"] == again.id
    root.for_actor(ADA).diagrams.create("Skizze", "skizze", demo_diagram("skizze", "Skizze"))
    assert root.for_actor(ADA).projects.locate("skizze") == (again.id, None)  # never bob's project, never a 404


# ---- 2. listing entries are never removed behind a concurrent write ------------------------------------------------


def test_a_re_share_during_a_revoke_keeps_the_project_listed(root, store, monkeypatch):
    root.for_actor(BOB).projects.create("Shop", "shop")
    root.for_actor(BOB).projects.share("shop", "eve", "viewer")
    original = store.commit_project
    raced: list[str] = []

    def racing(project, expected):
        result = original(project, expected)
        if project.id == "shop" and "eve" not in project.roles and not raced:  # the revoke is written ...
            raced.append("x")
            root.for_actor(BOB).projects.share("shop", "eve", "editor")  # ... and the owner shares again at once
        return result

    monkeypatch.setattr(store, "commit_project", racing)
    root.for_actor(BOB).projects.revoke("shop", "eve")
    assert store.read_project("shop")[0].roles == {"eve": "editor"}
    assert [p.id for p in root.for_actor(EVE).projects.list_projects() if p.shared] == ["shop"]


def test_a_re_create_during_a_delete_keeps_the_project_listed(root, store, monkeypatch):
    root.for_actor(BOB).projects.create("Shop", "shop")
    original = store.delete_project
    raced: list[str] = []

    def racing(project_id, expected):
        result = original(project_id, expected)
        if project_id == "shop" and not raced:
            raced.append("x")
            root.for_actor(BOB).projects.create("Shop", "shop")  # created again right after the delete
        return result

    monkeypatch.setattr(store, "delete_project", racing)
    root.for_actor(BOB).projects.delete("shop")
    assert "shop" in [p.id for p in root.for_actor(BOB).projects.list_projects()]


# ---- 3. turning Time to Live on keeps every other container setting ------------------------------------------------


class Database:
    def __init__(self, properties, allowed=True):
        self.properties, self.allowed, self.calls = properties, allowed, []

    def replace_container(self, container, partition_key, **kwargs):
        self.calls.append({"container": container, "partition_key": partition_key, **kwargs})


class Proxy:
    id = "arcviz"

    def __init__(self, properties):
        self.properties = properties

    def read(self):
        return dict(self.properties)


CUSTOM = {
    "id": "arcviz",
    "partitionKey": {"paths": ["/diagram_id"], "kind": "Hash", "version": 2},
    "indexingPolicy": {"indexingMode": "consistent", "excludedPaths": [{"path": "/diagram/*"}], "includedPaths": []},
    "conflictResolutionPolicy": {"mode": "LastWriterWins", "conflictResolutionPath": "/_ts"},
    "analyticalStorageTtl": -1,
    "computedProperties": [{"name": "low", "query": "SELECT VALUE LOWER(c.id) FROM c"}],
    "uniqueKeyPolicy": {"uniqueKeys": []},
    "_etag": "x",
    "_rid": "y",
}


def test_turning_ttl_on_passes_every_container_setting_back_unchanged():
    from arcviz.store.cosmos import ensure_ttl

    database = Database(CUSTOM)
    assert ensure_ttl(database, Proxy(CUSTOM)) == "enabled"
    [call] = database.calls
    assert call["default_ttl"] == -1
    assert call["partition_key"] == CUSTOM["partitionKey"]  # kind and version included
    assert call["indexing_policy"] == CUSTOM["indexingPolicy"]  # a custom indexing policy survives
    assert call["conflict_resolution_policy"] == CUSTOM["conflictResolutionPolicy"]
    assert call["analytical_storage_ttl"] == -1 and call["computed_properties"] == CUSTOM["computedProperties"]


def test_a_container_with_settings_a_replace_cannot_carry_is_left_alone(caplog):
    from arcviz.store.cosmos import ensure_ttl

    unique = {**CUSTOM, "uniqueKeyPolicy": {"uniqueKeys": [{"paths": ["/x"]}]}}
    database = Database(unique)
    assert ensure_ttl(database, Proxy(unique)) == "off" and database.calls == []
    assert "Time to Live" in caplog.text


# ---- 4. recipients never get the owner's e-mail address ------------------------------------------------------------


def test_recipients_see_the_owner_by_id_and_name_only(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    for name in ("bob", "eve"):
        users(name, "GET", "/api/me")
    users("bob", "POST", "/api/projects", json={"title": "Shop"})
    users("bob", "POST", "/api/projects/shop/access", json={"user": "eve", "role": "viewer"})
    assert users("eve", "GET", "/api/projects/shop/access").json()["owner"] == {"id": "bob", "name": "Bob"}
    assert users("bob", "GET", "/api/projects/shop/access").json()["owner"]["email"] == "bob@example.org"


# ---- 5. a failing listing read is an error, never an empty list ----------------------------------------------------


def test_only_a_missing_listing_reads_as_empty(root, store, monkeypatch):
    assert read_listing(store, "nobody") == ([], [])
    root.for_actor(ADA).projects.create("Shop", "shop")

    def throttled(kind, key):
        raise StoreError("Cosmos DB read failed: 429 Too Many Requests")

    monkeypatch.setattr(store, "read_document", throttled)
    with pytest.raises(StoreError, match="429"):
        read_listing(store, "ada")


# ---- 6. renames: reported once, crash-safe and idempotent ----------------------------------------------------------


def _squatting_team(store):
    team = Workspace(id="team", title="Team", owner="eve")
    store.create_document(WORKSPACE, "team", team.model_dump(mode="json"))
    for diagram_id in ("eins", "zwei"):
        store.create(diagram_id.capitalize(), diagram_id)
    squat = new_project("Falle", personal_project_id("ada"), "", "team")
    squat.diagrams = [Membership(diagram=d, added_at="2025-01-01T00:00:00+00:00") for d in ("eins", "zwei")]
    store.create_project(squat)
    store.append_project_chat(squat.id, [{"role": "user", "content": "hallo"}])
    return squat.id


def test_a_rename_that_succeeds_on_the_retry_is_not_reported_as_failed(store, monkeypatch):
    old = _squatting_team(store)
    original = store.create_project
    failed: list[str] = []

    def flaky(project):
        if project.id.startswith(old) and not failed:
            failed.append("x")
            raise StoreError("Cosmos DB write failed: 503")
        return original(project)

    monkeypatch.setattr(store, "create_project", flaky)
    report = migrate_to_personal_workspaces(store, None)
    assert report.renamed == {old: f"{old}-2"} and report.failed == {}


class Crash(BaseException):
    """The process dies (not an Exception: nothing catches it)."""


WRITES = (
    "create_project",
    "commit_project",
    "delete_project",
    "append_project_chat",
    "create_document",
    "commit_document",
    "delete_document",
)


def _holders(store) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for project, _ in store.list_projects():
        for member in project.diagrams:
            out.setdefault(member.diagram, []).append(project.id)
    return out


def test_a_rename_interrupted_at_any_write_resumes_with_the_same_id(tmp_path):
    from arcviz.store.file import FileStore

    point = 0
    while True:
        store = FileStore(tmp_path / f"crash{point}")
        old = _squatting_team(store)
        count = {"n": 0}
        originals = {name: getattr(store, name) for name in WRITES}

        def guarded(name, original, count=count, point=point):
            def call(*args, **kwargs):
                if count["n"] == point:
                    raise Crash(name)
                count["n"] += 1
                return original(*args, **kwargs)

            return call

        for name, original in originals.items():
            setattr(store, name, guarded(name, original))
        crashed = True
        try:
            migrate_to_personal_workspaces(store, None)
            crashed = False
        except Crash:
            pass
        for name, original in originals.items():
            setattr(store, name, original)
        assert all(len(p) <= 1 for p in _holders(store).values()), (point, _holders(store))  # never in two projects
        report = migrate_to_personal_workspaces(store, None)  # the restart
        ids = sorted(p.id for p, _ in store.list_projects())
        assert ids == [f"{old}-2"], (point, ids)  # the same target, never `-3`; the original is gone
        moved = store.read_project(f"{old}-2")[0]
        assert [m.diagram for m in moved.diagrams] == ["eins", "zwei"], point
        assert moved.workspace == personal_workspace_id("eve"), point
        assert [e["content"] for e in store.load_project_chat(moved.id)] == ["hallo"], point
        assert store.list_documents(RENAME) == [], point  # the intent is done
        assert report.failed == {}, point
        if not crashed:
            break
        point += 1
    assert point > 5  # every write of the rename was a crash point


# ---- 7. small ---------------------------------------------------------------------------------------------------


def test_mine_counts_from_the_listing_with_role_checks(root, store):
    root.for_actor(ADA).projects.create("Shop", "shop")
    root.for_actor(BOB).projects.create("Bobs", "bobs")
    root.for_actor(BOB).projects.share("bobs", "ada", "viewer")
    root.for_actor(BOB).projects.revoke("bobs", "ada")  # a stale listing entry is not counted
    mine = root.for_actor(ADA).workspaces.mine()
    assert (mine.project_count, mine.shared_count) == (1, 0)
    assert "bobs" in read_listing(store, "ada")[1]  # stale, but within the grace period (#183): re-validated on read


def test_the_project_list_names_a_shared_projects_owner_without_the_e_mail(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    for name in ("bob", "eve"):
        users(name, "GET", "/api/me")
    users("bob", "POST", "/api/projects", json={"title": "Shop"})
    users("bob", "POST", "/api/projects/shop/access", json={"user": "eve", "role": "viewer"})
    shared = [p for p in users("eve", "GET", "/api/projects").json() if p["shared"]]
    assert [p["owner"] for p in shared] == [{"id": "bob", "name": "Bob"}]
    own = [p for p in users("bob", "GET", "/api/projects").json() if p["id"] == "shop"]
    assert own[0]["owner"]["email"] == "bob@example.org"


def test_a_recorded_rename_whose_project_is_gone_is_cleared(store):
    store.create_document(RENAME, "home_" + "a" * 32, {"from": "home_" + "a" * 32, "to": "home_" + "a" * 32 + "-2"})
    migrate_to_personal_workspaces(store, None)
    assert store.list_documents(RENAME) == []
