"""Requirements in project bundles (spec: requirements.md, *Export and import*): schema version 7."""

from __future__ import annotations

import pytest

from arcviz import exchange
from arcviz import projects as pm
from arcviz.exchange import ExchangeError
from arcviz.services import DiagramService, ModelService, ProjectExchangeService, ProjectService, RequirementService
from arcviz.store.file import FileStore


def world(tmp_path, name: str = "a"):
    store = FileStore(tmp_path / name)
    projects = ProjectService(store, store)
    diagrams = DiagramService(store, projects)
    requirements = RequirementService(store, projects)
    return store, projects, diagrams, requirements, ProjectExchangeService(diagrams, projects, requirements)


def fill(projects: ProjectService, requirements: RequirementService) -> None:
    projects.create("Shop", "shop")
    projects.apply("shop", "add_stakeholder", {"name": "Betrieb"})
    ModelService(projects, DiagramService(projects._projects, projects)).add_element("shop", "API", "component")  # noqa: SLF001
    requirements.import_items(
        "shop",
        [
            {"raw": "alpha alpha", "stakeholders": ["s1"], "elements": ["api"]},
            {"raw": "beta beta"},
            {"raw": "gamma gamma"},
        ],
    )
    requirements.apply("shop", "update_requirement", {"requirement": "req1", "statement": "Alpha."})
    requirements.apply("shop", "set_status", {"requirement": "req1", "status": "refined"})
    requirements.apply("shop", "set_status", {"requirement": "req3", "status": "duplicate", "duplicate_of": "req2"})
    projects.apply("shop", "add_driver", {"title": "Daten", "requirements": ["req1", "req2"]})
    projects.apply(
        "shop", "add_scenario", {"title": "Ausfall", "requirements": ["req2"], "stakeholders": ["s1"], "origin": "p1/q"}
    )


def test_requirements_are_exported_as_schema_version_7(tmp_path):
    _, projects, _, requirements, bundles = world(tmp_path)
    fill(projects, requirements)
    bundle = bundles.export("shop")
    assert bundle["schema_version"] == 7
    exported = bundle["project"]
    assert [r["id"] for r in exported["requirements"]] == ["req1", "req2", "req3"] and exported["requirement_seq"] == 3
    assert exported["drivers"][0]["requirements"] == ["req1", "req2"]
    assert "origin" not in exported["scenarios"][0] and exported["scenarios"][0]["stakeholders"] == ["s1"]


def test_a_bundle_without_the_new_fields_keeps_its_version(tmp_path):
    _, projects, _, _, bundles = world(tmp_path)
    projects.create("Plain", "plain")
    projects.apply("plain", "add_driver", {"title": "Kosten"})
    assert bundles.export("plain")["schema_version"] == 4


def test_a_stakeholder_link_alone_makes_it_version_7(tmp_path):
    _, projects, _, _, bundles = world(tmp_path)
    projects.create("Shop", "shop")
    projects.apply("shop", "add_stakeholder", {"name": "Betrieb"})
    projects.apply("shop", "add_scenario", {"title": "Ausfall", "stakeholders": ["s1"]})
    assert bundles.export("shop")["schema_version"] == 7


def test_round_trip_into_another_store(tmp_path):
    _, projects, _, requirements, bundles = world(tmp_path, "a")
    fill(projects, requirements)
    bundle = bundles.export("shop")
    _, other_projects, _, other_requirements, other_bundles = world(tmp_path, "b")
    imported = other_bundles.import_bundle(bundle)
    page = other_requirements.page(imported.id, limit=200)
    assert [r["id"] for r in page["requirements"]] == ["req1", "req2", "req3"]
    first, second, third = page["requirements"]
    assert first["status"] == "refined" and first["stakeholders"] == ["s1"] and first["elements"] == ["api"]
    assert third["duplicate_of"] == "req2"
    project = other_projects.get(imported.id)
    assert project.drivers[0].requirements == ["req1", "req2"]
    assert project.scenarios[0].requirements == ["req2"] and project.scenarios[0].origin == ""
    assert other_requirements.apply(imported.id, "add_requirement", {"raw": "delta delta"})["result"]["id"] == "req4"


def test_renumbering_follows_the_links():
    bundle = {
        "format": "arcviz-project",
        "schema_version": 7,
        "project": {
            "title": "X",
            "sections": [],
            "diagrams": [],
            "drivers": [{"id": "d1", "title": "T", "requirements": ["r-b", "ghost"]}],
            "driver_seq": 1,
            "requirements": [
                {"id": "r-a", "raw": "a text", "created_at": "t", "updated_at": "t"},
                {
                    "id": "r-b",
                    "raw": "b text",
                    "status": "duplicate",
                    "duplicate_of": "r-a",
                    "created_at": "t",
                    "updated_at": "t",
                },
            ],
        },
        "diagrams": [],
    }
    parsed = exchange.parse_project(bundle)
    assert parsed.requirements is not None
    assert [(r.id, r.duplicate_of) for r in parsed.requirements.requirements] == [("req1", None), ("req2", "req1")]
    assert parsed.drivers[0].requirements == ["req2"]
    assert any("1 link(s)" in w for w in parsed.warnings)


def test_invalid_requirements_are_refused_with_every_problem():
    bundle = {
        "format": "arcviz-project",
        "schema_version": 7,
        "project": {
            "title": "X",
            "sections": [],
            "diagrams": [],
            "requirements": [
                {"id": "r1", "raw": "a", "status": "refined", "created_at": "t", "updated_at": "t"},
                {"id": "r1", "raw": "b", "created_at": "t", "updated_at": "t"},
            ],
        },
        "diagrams": [],
    }
    with pytest.raises(ExchangeError) as exc:
        exchange.parse_project(bundle)
    text = " | ".join(exc.value.errors)
    assert "duplicate id r1" in text and "refined without a statement" in text


def test_a_newer_version_is_still_refused():
    with pytest.raises(ExchangeError, match="schema_version 9"):
        exchange.parse_project({"format": "arcviz-project", "schema_version": 9, "project": {}, "diagrams": []})


def test_dumps_of_old_projects_are_unchanged():
    p = pm.new_project("Shop", "shop")
    pm.apply(p, "add_driver", {"title": "Kosten"})
    assert "requirements" not in p.drivers[0].model_dump()
