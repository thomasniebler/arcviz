"""Project domain model and its operations (spec: docs/architecture/specs/projects.md)."""

from __future__ import annotations

import pytest

from arcviz import projects
from arcviz.operations import OperationError
from arcviz.projects import Membership, Project, ProjectError, Section


def make() -> Project:
    project = projects.new_project("Shop", "shop")
    projects.apply(project, "add_section", {"title": "Backend"})
    projects.apply(project, "add_section", {"title": "Services", "parent": "backend"})
    projects.apply(project, "add_section", {"title": "Frontend"})
    for diagram, section in (("overview", None), ("orders", "services"), ("payments", "services"), ("web", "frontend")):
        projects.apply(project, "add_diagram", {"diagram": diagram, "section": section})
    return project


def test_new_project_is_empty_and_consistent():
    project = projects.new_project("  Shop ", "shop")
    assert project.title == "Shop" and project.sections == [] and project.diagrams == []
    assert project.integrity_errors() == []


def test_tree_nests_sections_and_keeps_order():
    assert make().tree() == {
        "diagrams": ["overview"],
        "sections": [
            {
                "id": "backend",
                "title": "Backend",
                "diagrams": [],
                "sections": [
                    {"id": "services", "title": "Services", "diagrams": ["orders", "payments"], "sections": []}
                ],
            },
            {"id": "frontend", "title": "Frontend", "diagrams": ["web"], "sections": []},
        ],
    }


def test_add_section_derives_a_unique_id_and_returns_it():
    project = make()
    out = projects.apply(project, "add_section", {"title": "Backend"})
    assert out["ok"] and out["result"]["id"] == "backend-2"
    assert projects.apply(project, "add_section", {"title": "X", "id": "custom"})["result"]["id"] == "custom"
    with pytest.raises(ProjectError, match="already"):
        projects.apply(project, "add_section", {"title": "Y", "id": "custom"})


def test_add_section_at_a_position_among_siblings():
    project = make()
    projects.apply(project, "add_section", {"title": "Docs", "position": 0})
    assert [s["id"] for s in project.tree()["sections"]] == ["docs", "backend", "frontend"]


def test_section_references_accept_title_case_insensitive():
    project = make()
    projects.apply(project, "rename_section", {"section": "FRONTEND", "title": "Web UI"})
    assert project.find_section("frontend").title == "Web UI"


def test_move_section_reparents_and_reorders():
    project = make()
    projects.apply(project, "move_section", {"section": "services", "parent": None, "position": 0})
    assert [s["id"] for s in project.tree()["sections"]] == ["services", "backend", "frontend"]
    projects.apply(project, "move_section", {"section": "frontend", "parent": "backend"})
    assert project.find_section("frontend").parent == "backend"


@pytest.mark.parametrize("target", ["backend", "services"])
def test_move_section_into_itself_or_a_descendant_is_refused(target):
    project = make()
    before = project.model_copy(deep=True)
    with pytest.raises(ProjectError, match="into itself"):
        projects.apply(project, "move_section", {"section": "backend", "parent": target})
    assert project == before


def test_remove_section_moves_its_contents_into_its_place():
    project = make()
    projects.apply(project, "remove_section", {"section": "backend"})
    tree = project.tree()
    assert [s["id"] for s in tree["sections"]] == ["services", "frontend"]
    assert project.member("orders").section == "services"
    projects.apply(project, "remove_section", {"section": "services"})
    assert project.tree()["diagrams"] == ["overview", "orders", "payments"]
    assert project.integrity_errors() == []


def test_move_diagram_within_the_project():
    project = make()
    projects.apply(project, "move_diagram", {"diagram": "payments", "section": "services", "position": 0})
    assert project.tree()["sections"][0]["sections"][0]["diagrams"] == ["payments", "orders"]
    projects.apply(project, "move_diagram", {"diagram": "web", "section": None})
    assert project.tree()["diagrams"] == ["overview", "web"]
    projects.apply(project, "move_diagram", {"diagram": "web", "section": "", "position": 0})
    assert project.tree()["diagrams"] == ["web", "overview"]


def test_add_and_remove_diagram_membership():
    project = make()
    with pytest.raises(ProjectError, match="already"):
        projects.apply(project, "add_diagram", {"diagram": "orders"})
    projects.apply(project, "remove_diagram", {"diagram": "orders"})
    assert project.member("orders") is None
    with pytest.raises(ProjectError, match="not in project"):
        projects.apply(project, "remove_diagram", {"diagram": "orders"})


def test_unknown_section_lists_the_known_ids():
    with pytest.raises(ProjectError, match="Known sections: backend, frontend, services"):
        projects.apply(make(), "move_diagram", {"diagram": "web", "section": "nope"})


def test_negative_position_is_refused():
    with pytest.raises(ProjectError, match="position"):
        projects.apply(make(), "move_diagram", {"diagram": "web", "position": -1})


def test_rename_and_describe_project():
    project = make()
    projects.apply(project, "rename_project", {"title": " Webshop "})
    projects.apply(project, "set_description", {"description": " Alles rund um den Shop "})
    assert project.title == "Webshop" and project.description == "Alles rund um den Shop"
    with pytest.raises(ProjectError, match="empty"):
        projects.apply(project, "rename_project", {"title": "  "})


@pytest.mark.parametrize(
    ("op", "args", "message"),
    [
        ("nope", {}, "Unknown project operation 'nope'"),
        ("rename_project", {}, "missing required argument 'title'"),
        ("rename_project", {"title": 3}, "'title' must be a string"),
        ("add_section", {"title": "x", "colour": "red"}, "unknown argument 'colour'"),
        ("move_diagram", {"diagram": "web", "position": "1"}, "'position' must be a number or null"),
    ],
)
def test_arguments_are_validated_before_anything_changes(op, args, message):
    project = make()
    before = project.model_copy(deep=True)
    with pytest.raises(ProjectError, match=message):
        projects.apply(project, op, args)
    assert project == before


def test_project_error_is_an_operation_error():
    assert issubclass(ProjectError, OperationError)


def test_any_exception_rolls_back(monkeypatch):
    project = make()
    before = project.model_copy(deep=True)

    def broken(p, title):
        p.title = "half"
        raise RuntimeError("defect")

    monkeypatch.setitem(projects.OPERATIONS, "rename_project", broken)
    with pytest.raises(RuntimeError):
        projects.apply(project, "rename_project", {"title": "x"})
    assert project == before


def test_successful_operation_touches_updated_at():
    project = make()
    project.updated_at = "2000-01-01T00:00:00+00:00"
    projects.apply(project, "rename_project", {"title": "New"})
    assert project.updated_at > "2000"


def test_integrity_errors():
    project = Project(
        id="p",
        title="P",
        sections=[
            Section(id="a", title="A", parent="b"),
            Section(id="b", title="B", parent="a"),
            Section(id="a", title="dup"),
            Section(id="c", title="C", parent="missing"),
        ],
        diagrams=[
            Membership(diagram="d", section="zzz", added_at="t"),
            Membership(diagram="d", added_at="t"),
        ],
        created_at="t",
        updated_at="t",
    )
    errors = " | ".join(project.integrity_errors())
    for fragment in (
        "duplicate section id 'a'",
        "unknown parent 'missing'",
        "cycle",
        "unknown section 'zzz'",
        "diagram 'd' is listed more than once",
    ):
        assert fragment in errors


def test_public_operations_exclude_membership_changes():
    assert "add_diagram" not in projects.PUBLIC_OPERATIONS
    assert "remove_diagram" not in projects.PUBLIC_OPERATIONS
    assert not set(projects.ELEMENT_OPERATIONS) & set(projects.PUBLIC_OPERATIONS)  # the model service's
    assert not set(projects.RELATION_OPERATIONS) & set(projects.PUBLIC_OPERATIONS)
    internal = {"add_diagram", "remove_diagram", *projects.ELEMENT_OPERATIONS, *projects.RELATION_OPERATIONS}
    assert set(projects.PUBLIC_OPERATIONS) | internal == set(projects.OPERATIONS)


def test_placements_in_the_same_second_still_order():
    project = projects.new_project("P", "p")
    projects.apply(project, "add_diagram", {"diagram": "a"})
    projects.apply(project, "add_diagram", {"diagram": "b"})
    first, second = (m.added_at for m in project.diagrams)
    assert "." in first and first <= second  # microsecond precision, so the repair can order them
