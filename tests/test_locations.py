"""The diagram→project locator (#181; spec: docs/architecture/specs/scale-and-robustness.md, ADR-0120).

A keyed document `location` per diagram names its project. It is a hint: the project it names is re-read and must
hold the diagram, the guard checks the role. A missing or stale entry falls back to the scan only for diagrams that
exist, and heals. Runs on the file, SQLite and Cosmos (fake) stores.
"""

from __future__ import annotations

import pytest

from arcviz import exchange
from arcviz.ports import StoreError
from arcviz.services import ServiceRoot
from arcviz.services.access import DiagramNotFound
from arcviz.services.locations import LOCATION, read_location
from arcviz.services.projects import ProjectService
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity
from tests.cosmos_fake import FakeContainer

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "vic", "out")}


@pytest.fixture(params=["file", "sqlite", "cosmos"])
def repo(request, tmp_path):
    if request.param == "file":
        yield FileStore(tmp_path / "data")
    elif request.param == "sqlite":
        store = SqliteStore(tmp_path / "data" / "arcviz.sqlite3")
        yield store
        store.close()
    else:
        yield CosmosStore(FakeContainer())


@pytest.fixture
def root(repo):
    """ada owns "shop" with "orders" (vic a viewer); out owns "secret" with "hidden"."""
    root = ServiceRoot(repo)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    out = root.for_actor(PEOPLE["out"])
    out.projects.create("Secret", "secret")
    out.diagrams.create("Hidden", "hidden", demo_diagram("hidden", "Hidden"), "secret")
    return root


def svc(root, name):
    return root.for_actor(PEOPLE[name])


def located(repo, diagram_id):
    """The project a reader finds through the entry: the newest holder among the projects it lists (None: none)."""
    from arcviz.services.locations import listed_holders, newest

    found = newest(listed_holders(repo, diagram_id, read_location(repo, diagram_id)))
    return found.project.id if found is not None else None


def set_entry(repo, diagram_id, *project_ids):
    body = {"diagram": diagram_id, "projects": list(project_ids)}
    try:
        _, revision = repo.read_document(LOCATION, diagram_id)
    except Exception:
        repo.create_document(LOCATION, diagram_id, body)
    else:
        repo.commit_document(LOCATION, diagram_id, body, revision)


def no_scans(monkeypatch, repo):
    """Listing every project or every diagram fails the test."""

    def scan(*_a, **_k):
        raise AssertionError("scanned the store")

    monkeypatch.setattr(repo, "list_projects", scan)
    monkeypatch.setattr(repo, "list_diagrams", scan)


# ---- writes ---------------------------------------------------------------------------------------------------


def test_create_writes_the_entry(root, repo):
    assert located(repo, "orders") == "shop" and located(repo, "hidden") == "secret"


def test_a_move_points_the_entry_to_the_new_project(root, repo):
    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    ada.diagrams.move("orders", "lab")
    assert located(repo, "orders") == "lab"
    assert repo.read_project("shop")[0].member("orders") is None
    assert ada.diagrams.access("orders")[1] == "lab"
    ada.diagrams.move("orders", "shop")
    assert located(repo, "orders") == "shop"


def test_a_move_within_the_project_keeps_the_entry(root, repo):
    ada = svc(root, "ada")
    ada.projects.apply("shop", "add_section", {"title": "Backend"})
    before = read_location(repo, "orders").projects
    ada.diagrams.move("orders", "shop", section="backend")
    assert read_location(repo, "orders").projects == before


def test_a_delete_drops_the_entry(root, repo):
    svc(root, "ada").diagrams.delete("orders")
    assert located(repo, "orders") is None
    assert repo.read_project("shop")[0].member("orders") is None


def test_an_imported_bundle_writes_entries(root, repo):
    ada = svc(root, "ada")
    bundle = ada.bundles.export("shop")
    project = ada.bundles.import_bundle(bundle, "Kopie")
    for member in project.diagrams:
        assert located(repo, member.diagram) == project.id


def test_an_imported_diagram_writes_its_entry(root, repo):
    ada = svc(root, "ada")
    diagram = exchange.parse_document(exchange.export_document(demo_diagram("x", "X")))
    new_id = ada.diagrams.import_new(diagram, "X", project="shop")
    assert located(repo, new_id) == "shop"


# ---- reads ----------------------------------------------------------------------------------------------------


def test_diagram_requests_never_scan(root, repo, monkeypatch):
    no_scans(monkeypatch, repo)
    for name in ("ada", "vic"):
        services = svc(root, name)
        assert services.diagrams.get("orders").id == "orders"
        assert services.diagrams.state("orders").revision is not None
        assert services.diagrams.access("orders")[1] == "shop"
    with pytest.raises(DiagramNotFound):
        svc(root, "out").diagrams.get("orders")


def test_an_unknown_id_never_scans(root, repo, monkeypatch):
    no_scans(monkeypatch, repo)
    with pytest.raises(DiagramNotFound):
        svc(root, "ada").diagrams.get("nope")


def test_the_diagram_list_and_project_views_never_scan(root, repo, monkeypatch):
    no_scans(monkeypatch, repo)
    assert [d.id for d in svc(root, "ada").diagrams.list_diagrams()] == ["orders"]
    assert [d.id for d in svc(root, "vic").diagrams.list_diagrams()] == ["orders"]
    assert [d.id for d in svc(root, "out").diagrams.list_diagrams()] == ["hidden"]
    view = svc(root, "vic").projects.view("shop")
    assert [d.id for d in view.diagrams] == ["orders"] and view.diagrams[0].version_count == 1


def test_a_missing_entry_heals_on_read(root, repo):
    repo.delete_document(LOCATION, "orders", repo.read_document(LOCATION, "orders")[1])
    assert svc(root, "vic").diagrams.get("orders").id == "orders"
    assert located(repo, "orders") == "shop"


def test_a_stale_entry_heals_on_read(root, repo):
    set_entry(repo, "orders", "secret")  # a project that does not hold it
    assert svc(root, "ada").diagrams.access("orders")[1] == "shop"
    assert located(repo, "orders") == "shop"


def test_an_entry_of_a_deleted_project_heals_on_read(root, repo):
    set_entry(repo, "orders", "gone")
    assert svc(root, "ada").diagrams.get("orders").id == "orders"
    assert located(repo, "orders") == "shop"


def test_a_forged_entry_grants_nothing(root, repo):
    """An entry that names a project of the caller for somebody else's diagram: the project does not hold it, so the
    real holder is found, and the caller's role there decides (none: 404)."""
    set_entry(repo, "hidden", "shop")
    with pytest.raises(DiagramNotFound):
        svc(root, "ada").diagrams.get("hidden")
    assert located(repo, "hidden") == "secret"
    assert svc(root, "out").diagrams.get("hidden").id == "hidden"


def test_an_orphan_is_not_found(root, repo):
    repo.create("Lost", "lost")  # in no project (a crash between create and place)
    with pytest.raises(DiagramNotFound):
        svc(root, "ada").diagrams.get("lost")
    assert located(repo, "lost") is None


# ---- repair ---------------------------------------------------------------------------------------------------


def test_the_repair_backfills_every_entry_and_is_idempotent(root, repo):
    for key, _, revision in repo.list_documents(LOCATION):
        repo.delete_document(LOCATION, key, revision)
    report = ProjectService(repo, repo).reconcile()
    assert report.located == 2
    assert located(repo, "orders") == "shop" and located(repo, "hidden") == "secret"
    assert ProjectService(repo, repo).reconcile().located == 0


def test_the_repair_fixes_stale_entries_and_drops_those_of_deleted_diagrams(root, repo):
    set_entry(repo, "orders", "secret")
    set_entry(repo, "ghost", "shop")  # no such diagram
    report = ProjectService(repo, repo).reconcile()
    assert report.located == 2
    assert "shop" in read_location(repo, "orders").projects  # the holder added; extra names are kept (a superset)
    assert located(repo, "ghost") is None
    assert svc(root, "ada").diagrams.access("orders")[1] == "shop"


def test_the_start_up_migration_backfills(root, repo):
    for key, _, revision in repo.list_documents(LOCATION):
        repo.delete_document(LOCATION, key, revision)
    report = root.migrate(None)
    assert report.repair.located == 2 and located(repo, "orders") == "shop"


# ---- Cosmos DB: no cross-partition query on a per-request path --------------------------------------------------


def test_cosmos_requests_issue_no_cross_partition_query():
    container = FakeContainer()
    repo = CosmosStore(container)
    root = ServiceRoot(repo)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    ada.projects.create("Lab", "lab")
    container.cross_partition.clear()
    vic = root.for_actor(PEOPLE["vic"])
    vic.diagrams.state("orders")
    vic.diagrams.access("orders")
    vic.diagrams.list_diagrams()
    vic.projects.view("shop")
    vic.projects.list_projects()
    ada = root.for_actor(PEOPLE["ada"])
    ada.diagrams.apply_operation("orders", "rename_diagram", {"title": "Neu"}, "x")
    ada.diagrams.move("orders", "lab")
    ada.diagrams.create("Second", "second", demo_diagram("second", "Second"), "lab")
    ada.diagrams.delete("second")
    assert container.cross_partition == []


# ---- review findings ------------------------------------------------------------------------------------------


def test_a_stale_membership_never_captures_a_re_created_diagram(root, repo, monkeypatch):
    """The cleanup after a delete failed: "shop" still lists the dead id. A re-create of the id (by anyone, here out)
    removes that membership, so ada's project never shows out's diagram."""
    from arcviz.services.projects import ProjectService as Service

    ada = svc(root, "ada")
    monkeypatch.setattr(Service, "unplace", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    ada.diagrams.delete("orders")  # the membership cleanup fails (logged)
    monkeypatch.undo()
    assert repo.read_project("shop")[0].member("orders") is not None  # the stale membership
    svc(root, "out").diagrams.create("Mine", "orders", demo_diagram("orders", "Mine"), "secret")
    assert repo.read_project("shop")[0].member("orders") is None
    assert [d.id for d in ada.projects.view("shop").diagrams] == []
    assert ada.projects.load_members("shop")[1] == []


def test_a_failed_placement_leaves_no_membership(root, repo, monkeypatch):
    """The membership is written, then placing fails: the new diagram is removed again and so is its membership."""
    from arcviz.services.projects import ProjectService as Service

    original = Service._update
    failed = []

    def written_then_failing(self, project_id, change, *limit):
        result = original(self, project_id, change, *limit)
        if project_id == "shop" and not failed:
            failed.append(1)
            raise RuntimeError("failed after the membership write")
        return result

    monkeypatch.setattr(Service, "_update", written_then_failing)
    with pytest.raises(RuntimeError):
        svc(root, "ada").diagrams.create("New", "fresh", demo_diagram("fresh", "New"), "shop")
    monkeypatch.undo()
    assert not repo.exists("fresh") and repo.read_project("shop")[0].member("fresh") is None
    assert located(repo, "fresh") is None


# ---- second review: duplicates, re-created ids, bounded scans -------------------------------------------------


def crashed_move(root, repo, monkeypatch):
    """Move "orders" from "shop" to ada's "zoo", crashing before the source membership is removed: both hold it, the
    target's membership is the newer one, and the target sorts after the source (so list order cannot pick it)."""
    from arcviz.services.projects import ProjectService as Service

    ada = svc(root, "ada")
    ada.projects.create("Zoo", "zoo")
    shop, revision = repo.read_project("shop")
    for member in shop.diagrams:
        member.added_at = "2000-01-01T00:00:00+00:00"  # placed long ago
    repo.commit_project(shop, revision)
    original = Service._remove_member

    def crash(self, project_id, diagram_id, added_at=None):
        if project_id == "shop":
            raise RuntimeError("crash before the source is cleaned")
        return original(self, project_id, diagram_id, added_at)

    monkeypatch.setattr(Service, "_remove_member", crash)
    with pytest.raises(RuntimeError):
        ada.diagrams.move("orders", "zoo")
    monkeypatch.undo()


def test_a_duplicate_resolves_to_the_newest_membership(root, repo, monkeypatch):
    """Review #2: after a crashed move the target wins (the newest added_at, like the repair), so members of the stale
    source lose access; also when the entry is lost and the scan heals it."""
    crashed_move(root, repo, monkeypatch)
    assert repo.read_project("shop")[0].member("orders") and repo.read_project("zoo")[0].member("orders")
    assert svc(root, "ada").diagrams.access("orders")[1] == "zoo"
    with pytest.raises(DiagramNotFound):
        svc(root, "vic").diagrams.get("orders")  # vic sees shop only
    repo.delete_document(LOCATION, "orders", repo.read_document(LOCATION, "orders")[1])
    with pytest.raises(DiagramNotFound):
        svc(root, "vic").diagrams.get("orders")  # the scan picks the newest too
    assert svc(root, "ada").diagrams.access("orders")[1] == "zoo"


def test_a_delete_removes_every_membership(root, repo, monkeypatch):
    crashed_move(root, repo, monkeypatch)
    svc(root, "ada").diagrams.delete("orders")
    assert repo.read_project("shop")[0].member("orders") is None
    assert repo.read_project("zoo")[0].member("orders") is None
    assert located(repo, "orders") is None


def test_a_re_created_id_is_never_shown_by_a_stale_duplicate(root, repo, monkeypatch):
    """Review #1, the exact scenario: X in P and Q (a crashed move), X deleted with a failing cleanup, then somebody
    else creates X in T. Neither P nor Q may show the new diagram."""
    from arcviz.services.projects import ProjectService as Service

    crashed_move(root, repo, monkeypatch)
    monkeypatch.setattr(Service, "unplace", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    svc(root, "ada").diagrams.delete("orders")
    monkeypatch.undo()
    assert repo.read_project("shop")[0].member("orders") and repo.read_project("zoo")[0].member("orders")
    created = svc(root, "out").diagrams.create("Mine", "orders", demo_diagram("orders", "Mine"), "secret")
    assert created.id == "orders"
    for project_id in ("shop", "zoo"):
        assert repo.read_project(project_id)[0].member("orders") is None, project_id
        assert svc(root, "ada").projects.view(project_id).diagrams == []
        assert svc(root, "ada").projects.load_members(project_id)[1] == []
    assert [d.id for d in svc(root, "ada").diagrams.list_diagrams()] == []
    assert located(repo, "orders") == "secret" and svc(root, "out").diagrams.get("orders").title == "Mine"


def test_an_orphan_is_scanned_at_most_once_per_ttl(root, repo, monkeypatch):
    """Review #4: an orphan (exists, in no project) costs one scan per TTL for all callers, not one per request."""
    from datetime import datetime, timedelta

    import arcviz.services.locations as locations_module

    monkeypatch.setattr(locations_module, "SCAN_COOLDOWN", 0.0)  # this test is about the stored TTL only
    repo.create("Lost", "lost")
    scans = []
    original = repo.list_projects
    monkeypatch.setattr(repo, "list_projects", lambda: scans.append(1) or original())
    for name in ("ada", "out", "ada", "vic"):
        with pytest.raises(DiagramNotFound):
            svc(root, name).diagrams.get("lost")
    assert len(scans) == 1
    later = datetime.now(locations_module.UTC) + locations_module.SCAN_TTL + timedelta(seconds=5)
    monkeypatch.setattr(locations_module, "_now", lambda: later)
    for name in ("ada", "out"):
        with pytest.raises(DiagramNotFound):
            svc(root, name).diagrams.get("lost")
    assert len(scans) == 2
    monkeypatch.setattr(repo, "list_projects", original)
    ProjectService(repo, repo).adopt("lost", "shop")  # placing it overrides the negative answer at once
    assert svc(root, "ada").diagrams.get("lost").id == "lost"


# ---- third review: failed reads are never "absent" ---------------------------------------------------------------

THROTTLED = "Cosmos DB read failed: 429 too many requests"


def failing_reads(monkeypatch, repo, project_id):
    """Reads of `project_id` fail transiently (a throttle), all others work; returns a switch to end it."""
    original = repo.read_project
    state = {"on": True}

    def read(pid):
        if state["on"] and pid == project_id:
            raise StoreError(THROTTLED)
        return original(pid)

    monkeypatch.setattr(repo, "read_project", read)
    return state


def stale_membership(root, repo, monkeypatch, diagram_id="ghost"):
    """ada's "shop" keeps a membership of a deleted diagram (its cleanup crashed); the entry lists "shop"."""
    from arcviz.services.projects import ProjectService as Service

    ada = svc(root, "ada")
    ada.diagrams.create("Ghost", diagram_id, demo_diagram(diagram_id, "Ghost"), "shop")
    monkeypatch.setattr(Service, "unplace", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    ada.diagrams.delete(diagram_id)
    monkeypatch.undo()
    assert repo.read_project("shop")[0].member(diagram_id) is not None


def test_vacate_fails_closed_on_a_failed_read(root, repo, monkeypatch):
    """Third review #1/#3 and fourth review #3: the stale holder cannot be read, so the id is not used (nothing created
    under it, its placement record released) and the create takes the next free id instead of failing."""
    from arcviz.services.projects import PLACEMENT

    stale_membership(root, repo, monkeypatch)
    throttle = failing_reads(monkeypatch, repo, "shop")
    created = svc(root, "out").diagrams.create("Mine", "ghost", demo_diagram("ghost", "Mine"), "secret")
    assert created.id == "ghost-2" and not repo.exists("ghost")
    assert [key for key, _, _ in repo.list_documents(PLACEMENT)] == []
    throttle["on"] = False
    assert repo.read_project("shop")[0].member("ghost") is not None  # untouched until it can be read
    again = svc(root, "out").diagrams.create("Mine", "ghost", demo_diagram("ghost", "Mine"), "secret")
    assert again.id == "ghost" and repo.read_project("shop")[0].member("ghost") is None


def test_a_placement_fails_closed_when_a_listed_holder_cannot_be_read(root, repo, monkeypatch):
    """Third review #2: a duplicate holder the entry lists cannot be read: the move aborts before anything is written,
    instead of leaving the duplicate behind as "not a holder"."""
    from arcviz.services.locations import add_location

    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    ada.projects.create("Dup", "dup")
    add_location(repo, "orders", "dup")
    ada.projects.apply("dup", "add_diagram", {"diagram": "orders"}, internal=True)  # a crash leftover
    service = ProjectService(repo, repo)
    source = service.holder("orders")
    failing_reads(monkeypatch, repo, "dup")
    with pytest.raises(StoreError, match="429"):
        service.place("orders", "lab", source=source)
    monkeypatch.undo()
    assert repo.read_project("lab")[0].member("orders") is None
    assert repo.read_project("dup")[0].member("orders") is not None


def test_a_failed_heal_never_hides_a_placed_diagram(root, repo, monkeypatch):
    """Third review #4: the entry is gone and writing it fails: the diagram is still found (by the scan), and no
    stamp is left that would hide it from the next request."""
    repo.delete_document(LOCATION, "orders", repo.read_document(LOCATION, "orders")[1])
    original = repo.create_document

    def failing(kind, key, body):
        if kind == LOCATION:
            raise StoreError(THROTTLED)
        return original(kind, key, body)

    monkeypatch.setattr(repo, "create_document", failing)
    for _ in range(2):
        assert svc(root, "vic").diagrams.get("orders").id == "orders"
    assert read_location(repo, "orders") is None


def test_add_location_always_changes_the_revision():
    """Third review #5: a listing of a project already listed still bumps the entry, so a concurrent remove re-checks."""
    from arcviz.services.locations import add_location

    container = FakeContainer()
    repo = CosmosStore(container)
    add_location(repo, "d", "p")
    first = read_location(repo, "d").revision
    add_location(repo, "d", "p")
    assert read_location(repo, "d").revision != first


def test_a_project_listed_a_moment_ago_is_not_dropped_before_its_membership_arrives(root, repo):
    """Third review #5, the interleaving of a move P→Q racing Q→P: one writer has listed "lab" and is about to add the
    membership; a remover that finds "lab" not holding it (yet) must not drop it, or the reader would miss it."""
    from arcviz.services.locations import add_location, remove_location

    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    add_location(repo, "orders", "lab")  # the writer's first step
    remove_location(repo, repo, "orders", "lab")  # the racing remover: "lab" does not hold it yet
    ada.projects.apply("lab", "add_diagram", {"diagram": "orders"}, internal=True)  # the writer's second step
    ada.projects.apply("shop", "remove_diagram", {"diagram": "orders"}, internal=True)
    assert "lab" in read_location(repo, "orders").projects
    assert located(repo, "orders") == "lab"


def test_a_delete_reuses_the_project_its_check_read(root, repo, monkeypatch):
    """Third review #7: the memberships of a diagram about to be deleted reuse the project the guard just read."""
    holder = repo.read_project("shop")[0]
    reads = []
    original = repo.read_project
    monkeypatch.setattr(repo, "read_project", lambda pid: reads.append(pid) or original(pid))
    observed = svc(root, "ada").projects.memberships("orders", holder)
    assert [p for p, _ in observed] == ["shop"] and reads == []


# ---- fourth review: no clocks in the invariant, bounded scans, best-effort repair ----------------------------------


def test_a_pending_project_is_never_dropped_whatever_the_clocks_say(root, repo, monkeypatch):
    """Fourth review #1: a writer listed "lab" and is about to add the membership; a remover on a replica whose clock
    runs a day ahead must not drop it (no wall-clock comparison in the invariant)."""
    from datetime import timedelta

    import arcviz.services.locations as locations_module
    from arcviz.services.locations import add_location, remove_location, settle_location

    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    token = add_location(repo, "orders", "lab")
    skewed = locations_module._now() + timedelta(days=1)
    monkeypatch.setattr(locations_module, "_now", lambda: skewed)
    remove_location(repo, repo, "orders", "lab")
    assert "lab" in read_location(repo, "orders").projects
    ada.projects.apply("lab", "add_diagram", {"diagram": "orders"}, internal=True)
    settle_location(repo, "orders", "lab", token)
    assert read_location(repo, "orders").pending == {}


def test_a_placement_settles_its_pending_mark(root, repo):
    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    ada.diagrams.move("orders", "lab")
    entry = read_location(repo, "orders")
    assert entry.pending == {} and entry.projects == ["lab"]  # the source dropped at once: nothing pending there


def test_a_failed_placement_settles_its_mark_once_it_is_sure_nothing_was_written(root, repo, monkeypatch):
    from arcviz.services.projects import ProjectService as Service

    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    original = Service._update

    def refuse_lab(self, project_id, change, *limit):
        if project_id == "lab":
            raise StoreError("Cosmos DB write failed: 503")
        return original(self, project_id, change, *limit)

    monkeypatch.setattr(Service, "_update", refuse_lab)
    with pytest.raises(StoreError):
        ada.diagrams.move("orders", "lab")
    assert read_location(repo, "orders").pending == {}
    assert svc(root, "ada").diagrams.access("orders")[1] == "shop"


def test_the_repair_never_clears_pending_tokens(root, repo):
    """Fifth review #1/#2: only a writer clears its token; the repair adds names but never removes pending ones, and
    never deletes an entry that carries pending tokens (a snapshot cannot tell a crashed writer from one in flight)."""
    from arcviz.services.locations import add_location

    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    add_location(repo, "orders", "lab")
    ada.projects.apply("lab", "add_diagram", {"diagram": "orders"}, internal=True)
    add_location(repo, "ghost", "lab")  # a writer about to place a diagram that does not exist (yet)
    ProjectService(repo, repo).reconcile()
    assert read_location(repo, "orders").pending.get("lab")
    assert read_location(repo, "ghost") is not None and read_location(repo, "ghost").pending.get("lab")


def test_placing_a_diagram_that_is_gone_fails(root, repo):
    """Fifth review #3: no membership is ever written for a diagram that does not exist."""
    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    with pytest.raises(StoreError, match="not found"):
        ProjectService(repo, repo).place("nope", "lab")
    assert repo.read_project("lab")[0].member("nope") is None and read_location(repo, "nope") is None


def test_a_move_racing_the_diagrams_delete_leaves_no_membership(root, repo, monkeypatch):
    """Fifth review #3, the root cause: the diagram is deleted while it is being moved, after its delete read the
    memberships; the move notices and undoes its own membership, so nothing dangles for a later create of the id."""
    from arcviz.services.projects import ProjectService as Service

    ada = svc(root, "ada")
    ada.projects.create("Lab", "lab")
    original = Service._update

    def deleted_meanwhile(self, project_id, change, *limit):
        result = original(self, project_id, change, *limit)
        if project_id == "lab" and repo.exists("orders"):
            repo.delete("orders")  # the store delete of a concurrent request
        return result

    monkeypatch.setattr(Service, "_update", deleted_meanwhile)
    with pytest.raises(StoreError, match="not found"):
        ada.diagrams.move("orders", "lab")
    monkeypatch.undo()
    assert repo.read_project("lab")[0].member("orders") is None
    assert not read_location(repo, "orders").pending.get("lab")


def test_vacate_fails_closed_while_a_placement_of_the_id_is_pending(root, repo):
    """Fifth review #3: a listed project with a pending token means a writer is in flight: the id is not used."""
    from arcviz.services.locations import add_location

    add_location(repo, "ghost", "shop")
    created = svc(root, "out").diagrams.create("Mine", "ghost", demo_diagram("ghost", "Mine"), "secret")
    assert created.id == "ghost-2" and not repo.exists("ghost")


def test_a_failed_adoption_settles_its_tokens(root, repo, monkeypatch):
    """Fifth review #5: the repair's adoption fails after listing the orphan: its token is cleared once a re-read shows
    no membership was written."""
    from arcviz.services.projects import PLACEMENT
    from arcviz.services.projects import ProjectService as Service

    repo.create("Lost", "lost")
    repo.create_document(PLACEMENT, "lost", {"project": "shop", "at": "2026-01-01T00:00:00+00:00"})
    original = Service._update

    def refuse_shop(self, project_id, change, *limit):
        if project_id == "shop":
            raise StoreError("Cosmos DB write failed: 503")
        return original(self, project_id, change, *limit)

    monkeypatch.setattr(Service, "_update", refuse_shop)
    report = ProjectService(repo, repo).reconcile()
    assert "lost" in report.skipped
    assert not read_location(repo, "lost").pending.get("shop")


def test_concurrent_lookups_share_one_scan(root, repo, monkeypatch):
    """Fourth review #4: single flight per id: callers arriving while a scan runs wait for it instead of scanning."""
    import threading
    import time

    repo.delete_document(LOCATION, "orders", repo.read_document(LOCATION, "orders")[1])
    original = repo.list_projects
    scans = []

    def slow():
        scans.append(1)
        time.sleep(0.2)
        return original()

    monkeypatch.setattr(repo, "list_projects", slow)
    results = []

    def look():
        results.append(svc(root, "vic").diagrams.get("orders").id)

    threads = [threading.Thread(target=look) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["orders"] * 4 and len(scans) == 1


def test_a_scan_whose_heal_failed_is_reused_for_the_cooldown_with_fresh_roles(root, repo, monkeypatch):
    """Fourth review #4: the heal write fails; the next requests within the cooldown reuse the scan's holder ids and
    do not scan again, but read the project again, so a revoked share is effective at once."""
    repo.delete_document(LOCATION, "orders", repo.read_document(LOCATION, "orders")[1])
    original_create = repo.create_document

    def failing(kind, key, body):
        if kind == LOCATION:
            raise StoreError(THROTTLED)
        return original_create(kind, key, body)

    monkeypatch.setattr(repo, "create_document", failing)
    scans = []
    original = repo.list_projects
    monkeypatch.setattr(repo, "list_projects", lambda: scans.append(1) or original())
    assert svc(root, "vic").diagrams.get("orders").id == "orders"
    assert svc(root, "vic").diagrams.get("orders").id == "orders"
    assert len(scans) == 1 and read_location(repo, "orders") is None  # no stamp written on failure
    svc(root, "ada").projects.revoke("shop", "vic")
    with pytest.raises(DiagramNotFound):
        svc(root, "vic").diagrams.get("orders")


def test_the_repair_goes_on_when_one_item_fails(root, repo, monkeypatch):
    """Fourth review #5: a transient failure on one item (here a duplicate in an unreadable project) is logged and
    skipped; the other items (an orphan's adoption, the locator repair) still run."""
    from arcviz.services.projects import PLACEMENT

    ada = svc(root, "ada")
    ada.projects.create("Dup", "dup")
    ada.projects.create("Lab", "lab")
    ada.projects.apply("dup", "add_diagram", {"diagram": "orders"}, internal=True)  # a newer duplicate (a crash)
    repo.create("Lost", "lost")  # an orphan, created for "lab"
    repo.create_document(PLACEMENT, "lost", {"project": "lab", "at": "2026-01-01T00:00:00+00:00"})
    throttle = failing_reads(monkeypatch, repo, "shop")  # the dedupe must remove the older membership in "shop"
    report = ProjectService(repo, repo).reconcile()
    assert "orders" in report.skipped
    throttle["on"] = False
    assert report.adopted == ["lost"] and repo.read_project("lab")[0].member("lost") is not None
    assert repo.read_project("shop")[0].member("orders") is not None  # left for the next run
