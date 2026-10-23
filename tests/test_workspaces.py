"""One personal workspace per user, created on first sign-in (issue #177; spec:
docs/architecture/specs/personal-workspaces.md, ADR-0100)."""

from __future__ import annotations

import threading

import pytest

from arcviz.ports import StoreError
from arcviz.projects import ProjectError
from arcviz.services import ServiceRoot, WorkspaceNotFound
from arcviz.services.access import WORKSPACE, ensure_personal_workspace
from arcviz.templates import demo_diagram
from arcviz.workspaces import (
    Identity,
    Workspace,
    is_personal_project_id,
    personal_project_id,
    personal_workspace_id,
    user_key,
)
from tests.conftest import proxy_users

ADA = Identity("ada", "Ada", "ada@example.org", email_trusted=True)
BOB = Identity("bob", "Bob", "bob@example.org", email_trusted=True)


def test_ids_are_deterministic_and_never_an_m20_slug():
    assert personal_workspace_id("ada") == personal_workspace_id("ada") != personal_workspace_id("Ada")
    wid = personal_workspace_id("a/b:c ä")
    assert wid.startswith("user_") and len(wid) == 37 and "_" in wid  # slugs of M20 titles never contain "_"
    assert user_key("x").startswith("u-") and len(user_key("x")) == 34


def test_ensure_creates_once_and_returns_the_same(store):
    first = ensure_personal_workspace(store, ADA)
    assert first.id == personal_workspace_id("ada") and first.owner == "ada" and first.personal
    assert first.is_personal_of("ada") and not first.is_personal_of("bob")
    again = ensure_personal_workspace(store, ADA)
    assert again == first
    assert [key for key, _, _ in store.list_documents(WORKSPACE)] == [first.id]


def test_replicas_creating_it_at_once_end_with_one(store):
    results, errors = [], []

    def create():
        try:
            results.append(ensure_personal_workspace(store, BOB))
        except Exception as exc:  # pragma: no cover - would fail the test
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len({r.created_at for r in results}) == 1
    assert len(store.list_documents(WORKSPACE)) == 1


def test_a_foreign_document_under_the_id_is_never_used(store):
    wid = personal_workspace_id("ada")
    store.create_document(WORKSPACE, wid, Workspace(id=wid, title="X", owner="mallory").model_dump(mode="json"))
    with pytest.raises(StoreError, match="not the personal workspace"):
        ensure_personal_workspace(store, ADA)
    root = ServiceRoot(store)
    root.users.seen(ADA)
    with pytest.raises(StoreError):
        root.for_actor(ADA).projects.create("Shop")  # nothing is created in somebody else's workspace


def test_mine_counts_own_and_shared_projects(store):
    root = ServiceRoot(store)
    for who in (ADA, BOB):
        root.users.seen(who)
    root.for_actor(ADA).projects.create("Shop")
    root.for_actor(ADA).projects.create("Lab")
    root.for_actor(BOB).projects.create("Bobs")
    root.for_actor(BOB).projects.share("bobs", "ada", "viewer")
    mine = root.for_actor(ADA).workspaces.mine()
    assert (mine.id, mine.owner.id, mine.project_count, mine.shared_count) == (
        personal_workspace_id("ada"),
        "ada",
        2,
        1,
    )


def test_a_project_goes_into_the_own_workspace_only(store):
    root = ServiceRoot(store)
    root.users.seen(ADA)
    root.users.seen(BOB)
    with pytest.raises(WorkspaceNotFound):
        root.for_actor(ADA).projects.create("Shop", workspace=personal_workspace_id("bob"))
    created = root.for_actor(ADA).projects.create("Shop", workspace=personal_workspace_id("ada"))
    assert created.workspace == personal_workspace_id("ada")


# ---- HTTP -------------------------------------------------------------------------------------------


def test_the_first_request_creates_the_personal_workspace(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    assert users("ada", "GET", "/api/me").json()["workspace"] == personal_workspace_id("ada")
    assert [key for key, _, _ in store.list_documents(WORKSPACE)] == [personal_workspace_id("ada")]
    mine = users("ada", "GET", "/api/workspace").json()
    assert mine["id"] == personal_workspace_id("ada") and mine["owner"]["name"] == "Ada"
    assert mine["project_count"] == 1 and mine["shared_count"] == 0  # the own default project


def test_a_failing_first_attempt_is_repaired_by_the_first_write(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    original = store.create_document
    calls = {"n": 0}

    def flaky(kind, key, body):
        if kind == WORKSPACE:
            calls["n"] += 1
        if kind == WORKSPACE and calls["n"] == 1:
            raise StoreError("Cosmos DB unavailable")
        return original(kind, key, body)

    monkeypatch.setattr(store, "create_document", flaky)
    assert users("ada", "GET", "/api/me").status_code == 200  # never fails the request
    assert store.list_documents(WORKSPACE) == []
    assert users("ada", "POST", "/api/projects", json={"title": "Shop"}).status_code == 201
    assert [key for key, _, _ in store.list_documents(WORKSPACE)] == [personal_workspace_id("ada")]


# ---- the default project of every user -----------------------------------------------------------------------


def test_every_user_gets_an_own_default_project_once(store):
    root = ServiceRoot(store)
    for who in (ADA, BOB):
        root.users.seen(who)
    first = root.for_actor(ADA).projects.ensure_own_default()
    assert first.id == personal_project_id("ada") and first.title == "Standardprojekt"
    assert first.workspace == personal_workspace_id("ada") and first.roles == {}
    assert root.for_actor(ADA).projects.ensure_own_default().id == first.id  # idempotent
    assert [p.id for p in root.for_actor(BOB).projects.list_projects()] == []  # nobody else sees it
    assert is_personal_project_id(first.id) and not is_personal_project_id("home_x")


def test_replicas_creating_the_default_project_at_once_end_with_one(store):
    root = ServiceRoot(store)
    root.users.seen(BOB)
    ensure_personal_workspace(store, BOB)
    results, errors = [], []

    def create():
        try:
            results.append(root.for_actor(BOB).projects.ensure_own_default().id)
        except Exception as exc:  # pragma: no cover - would fail the test
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and set(results) == {personal_project_id("bob")}
    assert [p.id for p, _ in store.list_projects()] == [personal_project_id("bob")]


def test_a_diagram_without_a_project_goes_into_the_callers_default_project(store):
    root = ServiceRoot(store, admin=ADA)
    for who in (ADA, BOB):
        root.users.seen(who)
    bob = root.for_actor(BOB)
    bob.diagrams.create("Skizze", "skizze", demo_diagram("skizze", "Skizze"))
    assert bob.projects.locate("skizze") == (personal_project_id("bob"), None)
    root.for_actor(ADA).diagrams.create("Mein", "mein", demo_diagram("mein", "Mein"))
    assert root.for_actor(ADA).projects.locate("mein") == (personal_project_id("ada"), None)  # not the reserved one
    assert all(p.id != "default" for p, _ in store.list_projects())


def test_nobody_takes_another_users_default_project_id(store):
    root = ServiceRoot(store)
    for who in (ADA, BOB):
        root.users.seen(who)
    squatted = root.for_actor(BOB).projects.create("Falle", personal_project_id("ada"))  # an exact home_ id is diverted
    assert squatted.id == personal_project_id("ada") + "-2"
    assert root.for_actor(ADA).projects.ensure_own_default().workspace == personal_workspace_id("ada")


def test_a_foreign_project_under_the_default_id_is_never_used(store):
    from arcviz.projects import new_project

    store.create_project(new_project("Fremd", personal_project_id("ada"), "", personal_workspace_id("bob")))
    root = ServiceRoot(store)
    root.users.seen(ADA)
    fallback = root.for_actor(ADA).projects.ensure_own_default()  # a recorded variant, never the foreign project
    assert fallback.id != personal_project_id("ada") and fallback.workspace == personal_workspace_id("ada")
    with pytest.raises(ProjectError):
        ServiceRoot(store).for_actor(ADA).projects.__class__(store, store).ensure_own_default()  # needs a caller


def test_the_first_request_also_creates_the_default_project(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    users("bob", "GET", "/api/me")
    listed = users("bob", "GET", "/api/projects").json()
    assert [(p["id"], p["title"], p["role"], p["shared"]) for p in listed] == [
        (personal_project_id("bob"), "Standardprojekt", "owner", False)
    ]
    created = users("bob", "POST", "/api/diagrams", json={"title": "Neu", "template": "empty"})
    assert created.status_code == 201
    assert users("bob", "GET", f"/api/projects/{personal_project_id('bob')}").json()["diagrams"][0]["id"] == "neu"
