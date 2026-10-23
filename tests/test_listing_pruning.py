"""Stale per-user listing entries are pruned safely (#183; spec: docs/architecture/specs/scale-and-robustness.md,
ADR-0124).

An entry is stale when its project is definitely gone or foreign (not in the user's personal workspace, no share for
them). It is removed on read and at start-up, conditional on the listing revision read before the projects, and only
when its stamp is older than the grace period, so a re-share or a re-create racing the prune always wins.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from arcviz.ports import StoreError
from arcviz.services import ServiceRoot
from arcviz.services import listing as listing_module
from arcviz.services.listing import LISTING, read_listing, update_listing
from arcviz.services.workspaces import migrate_to_personal_workspaces
from arcviz.workspaces import Identity, user_key

ADA, BOB = (Identity(n, n.capitalize(), f"{n}@example.org") for n in ("ada", "bob"))


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in (ADA, BOB):
        root.users.seen(identity)
    root.for_actor(ADA).projects.create("Shop", "shop")
    root.for_actor(BOB).projects.create("Bobs", "bobs")
    root.for_actor(BOB).projects.share("bobs", "ada", "viewer")
    return root


def later(monkeypatch, hours: float = 2) -> None:
    """Move the listing's clock past the grace period."""
    moment = datetime.now(UTC) + timedelta(hours=hours)
    monkeypatch.setattr(listing_module, "_now", lambda: moment)


def shared_of(store, user="ada"):
    return read_listing(store, user)[1]


def test_a_revoked_share_is_pruned_on_read_after_the_grace(root, store, monkeypatch):
    root.for_actor(BOB).projects.revoke("bobs", "ada")
    root.for_actor(ADA).projects.list_projects()
    assert "bobs" in shared_of(store)  # within the grace: kept
    later(monkeypatch)
    assert "bobs" not in [p.id for p in root.for_actor(ADA).projects.list_projects()]
    assert "bobs" not in shared_of(store)
    assert "shop" in read_listing(store, "ada")[0]  # what the caller sees stays


def test_a_deleted_project_is_pruned_from_every_field(root, store, monkeypatch):
    root.for_actor(ADA).projects.delete("shop")
    later(monkeypatch)
    root.for_actor(ADA).workspaces.mine()
    assert "shop" not in read_listing(store, "ada")[0]


def test_a_visible_project_is_never_pruned(root, store, monkeypatch):
    later(monkeypatch, hours=1000)
    root.for_actor(ADA).projects.list_projects()
    own, shared = read_listing(store, "ada")
    assert "shop" in own and "bobs" in shared


def test_a_re_share_racing_the_prune_wins(root, store, monkeypatch):
    """The prune read the listing, then the project before the re-share: its conditional write loses."""
    root.for_actor(BOB).projects.revoke("bobs", "ada")
    later(monkeypatch)
    original = store.read_project
    raced = []

    def read_then_reshare(project_id):
        found = original(project_id)
        if project_id == "bobs" and not raced:
            raced.append(1)
            root.for_actor(BOB).projects.share("bobs", "ada", "editor")  # adds (stamps) the entry, then shares
        return found

    monkeypatch.setattr(store, "read_project", read_then_reshare)
    root.for_actor(ADA).projects.list_projects()
    assert raced and "bobs" in shared_of(store)
    monkeypatch.setattr(store, "read_project", original)
    assert "bobs" in [p.id for p in root.for_actor(ADA).projects.list_projects()]


def test_a_re_share_just_before_the_prune_is_kept_by_its_stamp(root, store, monkeypatch):
    root.for_actor(BOB).projects.revoke("bobs", "ada")
    later(monkeypatch)
    update_listing(store, "ada", add=[("shared", "bobs")])  # a share's first step; its project write is in flight
    root.for_actor(ADA).projects.list_projects()
    assert "bobs" in shared_of(store)


def test_a_failed_project_read_prunes_nothing(root, store, monkeypatch):
    root.for_actor(BOB).projects.revoke("bobs", "ada")
    root.for_actor(BOB).projects.delete("bobs")
    later(monkeypatch)
    original = store.read_project

    def flaky(project_id):
        if project_id == "bobs":
            raise StoreError("Cosmos DB read failed: timeout")
        return original(project_id)

    monkeypatch.setattr(store, "read_project", flaky)
    root.for_actor(ADA).projects.list_projects()
    assert "bobs" in shared_of(store)


def test_entries_without_a_stamp_are_stamped_first_and_pruned_after_the_grace(root, store, monkeypatch):
    """Entries written before M22, or by an older replica during a rolling upgrade (which may be adding them for a share
    still in flight), are never dropped at once: the prune stamps them, the grace runs from there."""
    body, revision = store.read_document(LISTING, user_key("ada"))
    store.commit_document(LISTING, user_key("ada"), {k: v for k, v in body.items() if k != "added"}, revision)
    root.for_actor(BOB).projects.revoke("bobs", "ada")
    root.for_actor(ADA).projects.list_projects()
    assert "bobs" in shared_of(store)
    assert "shared:bobs" in store.read_document(LISTING, user_key("ada"))[0]["added"]
    later(monkeypatch)
    root.for_actor(ADA).projects.list_projects()
    assert "bobs" not in shared_of(store)


def test_an_add_always_restamps_and_changes_the_revision(root, store):
    before = store.read_document(LISTING, user_key("ada"))[1]
    assert update_listing(store, "ada", add=[("shared", "bobs")])  # listed already
    body, after = store.read_document(LISTING, user_key("ada"))
    assert after != before and "shared:bobs" in body["added"]


def test_the_start_up_migration_prunes_every_listing(root, store, monkeypatch):
    root.for_actor(BOB).projects.revoke("bobs", "ada")
    root.for_actor(ADA).projects.delete("shop")
    later(monkeypatch)
    report = migrate_to_personal_workspaces(store, None)
    assert report.pruned == 2
    own, shared = read_listing(store, "ada")
    assert "shop" not in own and "bobs" not in shared
    assert "bobs" in read_listing(store, "bob")[0]
    assert migrate_to_personal_workspaces(store, None).pruned == 0
