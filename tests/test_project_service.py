"""ProjectService: project read-modify-writes, placement and the membership repair (spec: projects.md)."""

from __future__ import annotations

import threading

import pytest

from arcviz import projects
from arcviz.model import Diagram
from arcviz.ports import ConflictError, StoreError
from arcviz.projects import ProjectError
from arcviz.services.projects import DEFAULT_PROJECT_ID, DEFAULT_PROJECT_TITLE, ProjectNotEmpty, ProjectService


@pytest.fixture
def service(store):
    return ProjectService(store, store)


def _diagram(store, diagram_id):
    store.create(diagram_id.title(), diagram_id, Diagram(id=diagram_id, title=diagram_id.title()))


def test_create_derives_the_id_and_moves_on_from_taken_ones(service):
    project = service.create("Mein Shop", description="Alles")
    assert project.id == "mein-shop" and project.description == "Alles"
    assert service.create("Mein Shop").id == "mein-shop-2"  # ADR-0089
    assert service.create("X", project_id="mein-shop").id == "mein-shop-3"
    with pytest.raises(StoreError, match="Invalid"):
        service.create("X", project_id="Bad Id")


def test_apply_runs_public_operations_and_refuses_internal_ones(service):
    service.create("Shop")
    out = service.apply("shop", "add_section", {"title": "Backend"})
    assert out["result"]["id"] == "backend"
    assert service.get("shop").find_section("backend") is not None
    with pytest.raises(ProjectError, match="internal"):
        service.apply("shop", "add_diagram", {"diagram": "x"})
    with pytest.raises(StoreError, match="not found"):
        service.apply("nope", "rename_project", {"title": "x"})


def test_apply_retries_on_a_concurrent_write(service, store):
    service.create("Shop")
    real_commit = store.commit_project
    interfered = []

    def commit(project, expected):
        if not interfered:  # another request renames the project first
            interfered.append(1)
            current, revision = store.read_project("shop")
            projects.apply(current, "set_description", {"description": "theirs"})
            real_commit(current, revision)
        return real_commit(project, expected)

    store.commit_project = commit
    service.apply("shop", "rename_project", {"title": "Mine"})
    project = service.get("shop")
    assert project.title == "Mine" and project.description == "theirs"


def test_apply_gives_up_after_repeated_conflicts(service, store):
    service.create("Shop")

    def always_conflict(project, expected):
        raise ConflictError("busy")

    store.commit_project = always_conflict
    with pytest.raises(ConflictError):
        service.apply("shop", "rename_project", {"title": "x"})


def test_place_puts_a_diagram_into_the_default_project(service, store):
    _diagram(store, "a")
    service.place("a")
    default = service.get(DEFAULT_PROJECT_ID)
    assert default.title == DEFAULT_PROJECT_TITLE and default.member("a").section is None
    assert service.locate("a") == (DEFAULT_PROJECT_ID, None)


def test_place_moves_across_projects_and_keeps_one_membership(service, store):
    _diagram(store, "a")
    service.create("Shop")
    service.apply("shop", "add_section", {"title": "Backend"})
    service.place("a")
    service.place("a", "shop", "backend")
    assert service.locate("a") == ("shop", "backend")
    assert service.get(DEFAULT_PROJECT_ID).member("a") is None
    service.place("a", "shop", None, 0)  # within the project
    assert service.locate("a") == ("shop", None)


def test_check_target_validates_before_anything_is_written(service):
    service.create("Shop")
    with pytest.raises(StoreError, match="not found"):
        service.check_target("nope", None)
    with pytest.raises(ProjectError, match="Unknown section"):
        service.check_target("shop", "nope")
    assert service.check_target(None, None) == (DEFAULT_PROJECT_ID, None)


def test_unplace_removes_every_membership(service, store):
    _diagram(store, "a")
    service.place("a")
    service.unplace("a")
    assert service.locate("a") is None
    service.unplace("a")  # nothing to do


def test_delete_refuses_a_project_with_diagrams(service, store):
    _diagram(store, "a")
    service.create("Shop")
    service.place("a", "shop")
    with pytest.raises(ProjectNotEmpty):
        service.delete("shop")
    service.unplace("a")
    service.delete("shop")
    with pytest.raises(StoreError, match="not found"):
        service.get("shop")


def test_view_lists_members_in_order_and_skips_dangling_ones(service, store):
    for d in ("a", "b"):
        _diagram(store, d)
    service.create("Shop")
    service.apply("shop", "add_section", {"title": "Backend"})
    service.place("b", "shop", "backend")
    service.place("a", "shop")
    store.delete("b")  # deleted behind the service's back: dangling
    view = service.view("shop")
    assert [(d.id, d.section) for d in view.diagrams] == [("a", None)]
    assert view.tree["sections"][0]["diagrams"] == ["b"]  # the stored tree is not rewritten by a read


def test_reconcile_adopts_orphans_into_the_default_project(service, store):
    for d in ("b", "a"):
        _diagram(store, d)
    report = service.reconcile()
    assert report.adopted == ["a", "b"] and report.dropped == [] and report.deduplicated == []
    assert service.get(DEFAULT_PROJECT_ID).tree()["diagrams"] == ["a", "b"]
    assert service.reconcile().changed is False  # idempotent


def test_reconcile_drops_dangling_memberships(service, store):
    _diagram(store, "a")
    service.place("a")
    store.delete("a")
    report = service.reconcile()
    assert report.dropped == ["a"] and service.get(DEFAULT_PROJECT_ID).diagrams == []


def test_reconcile_keeps_the_newest_of_duplicate_memberships(service, store):
    _diagram(store, "a")
    service.create("Old")
    service.create("New")
    service.apply("old", "add_diagram", {"diagram": "a"}, internal=True)
    service.apply("new", "add_diagram", {"diagram": "a"}, internal=True)  # newer added_at: the intended one
    report = service.reconcile()
    assert report.deduplicated == ["a"] and service.locate("a") == ("new", None)


def test_reconcile_does_not_drop_a_diagram_created_after_the_listing(service, store, monkeypatch):
    _diagram(store, "a")
    service.place("a")
    real = store.list_diagrams
    monkeypatch.setattr(store, "list_diagrams", lambda: [d for d in real() if d.id != "a"])  # an old listing
    report = service.reconcile()
    assert report.dropped == [] and service.locate("a") == (DEFAULT_PROJECT_ID, None)


def test_reconcile_does_not_adopt_a_diagram_that_is_being_moved(service, store, monkeypatch):
    _diagram(store, "a")
    service.create("Shop")
    service.place("a", "shop")
    real = store.list_projects
    calls = []

    def first_read_misses_the_move():
        calls.append(1)
        found = real()
        if len(calls) == 1:  # the first read happened between "remove from source" and "add to target"
            return [(p, r) for p, r in found if p.id != "shop"]
        return found

    monkeypatch.setattr(store, "list_projects", first_read_misses_the_move)
    report = service.reconcile()
    assert report.adopted == [] and service.locate("a") == ("shop", None)


def test_list_projects_repairs_and_sorts_by_title(service, store):
    _diagram(store, "a")
    service.create("Zoo", "zoo")
    service.create("alpha", "alpha")
    summaries = service.list_projects()
    assert [s.id for s in summaries] == ["alpha", DEFAULT_PROJECT_ID, "zoo"]
    assert {s.id: s.diagram_count for s in summaries} == {"alpha": 0, DEFAULT_PROJECT_ID: 1, "zoo": 0}


def test_without_diagrams_no_default_project_is_created(service):
    assert service.list_projects() == []


def test_concurrent_repairs_converge(service, store):
    for d in ("a", "b", "c"):
        _diagram(store, d)
    errors = []

    def repair():
        try:
            ProjectService(store, store).reconcile()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=repair) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(m.diagram for m in service.get(DEFAULT_PROJECT_ID).diagrams) == ["a", "b", "c"]


def test_create_with_sections_gives_up_on_a_write_failure(service, store, monkeypatch):
    def broken(project):
        raise StoreError("Cosmos DB write failed: 413 entity too large")

    monkeypatch.setattr(store, "create_project", broken)
    with pytest.raises(StoreError, match="413"):
        service.create_with_sections("Shop", "", [])


def test_create_with_sections_takes_the_next_free_id(service):
    service.create("Shop")
    assert service.create_with_sections("Shop", "", []).id == "shop-2"


def test_stale_dedup_does_not_remove_a_newer_placement(service, store, monkeypatch):
    _diagram(store, "d")
    service.create("A")
    service.create("B")
    service.apply("a", "add_diagram", {"diagram": "d"}, internal=True)
    service.apply("b", "add_diagram", {"diagram": "d"}, internal=True)  # mid-move a -> b
    stale = store.list_projects()
    service.place("d", "b")  # the move finishes ...
    service.place("d", "a")  # ... and the user moves it back: a fresh membership in a
    real = store.list_projects
    calls = []

    def first_read_is_stale():
        calls.append(1)
        return stale if len(calls) == 1 else real()

    monkeypatch.setattr(store, "list_projects", first_read_is_stale)
    service.reconcile()
    assert service.locate("d") == ("a", None)


# ---- project transcript and members (spec: docs/architecture/specs/project-chat.md) -----------------


def test_project_transcript_methods_mirror_the_diagram_service(service):
    service.create("Shop")
    assert service.chat("shop") == []
    assert service.append_chat("shop", [{"role": "user", "content": "hi"}]) is True
    generation = service.chat_generation("shop")
    assert generation
    service.append_chat("shop", [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "more"}])
    assert service.compact_chat("shop", 2, {"role": "summary", "content": "S"}, generation) is True
    assert [(e["seq"], e["role"]) for e in service.chat("shop")] == [(2, "summary"), (3, "user")]
    service.clear_chat("shop")
    assert service.chat("shop") == [] and service.chat_generation("shop") != generation


def test_project_transcript_of_a_missing_project(service):
    assert service.chat("nope") == []
    assert service.append_chat("nope", [{"role": "user", "content": "late"}]) is False  # nothing recreated
    with pytest.raises(StoreError, match="not found"):
        service.clear_chat("nope")


def test_load_members_returns_the_existing_diagrams_in_membership_order(service, store):
    service.create("Shop")
    service.apply("shop", "add_section", {"title": "Backend"})
    for diagram_id in ("b", "a", "gone"):
        _diagram(store, diagram_id)
    service.place("b", "shop", "backend")
    service.place("a", "shop")
    service.place("gone", "shop")
    store.delete("gone")  # deleted without unplacing: a dangling membership
    project, members = service.load_members("shop")
    assert project.id == "shop"
    assert [(m.diagram, m.section, d.id) for m, d in members] == [("b", "backend", "b"), ("a", None, "a")]
    with pytest.raises(StoreError, match="not found"):
        service.load_members("nope")
