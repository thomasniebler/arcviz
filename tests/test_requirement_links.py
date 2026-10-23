"""Links from drivers, scenarios and decisions to requirements and stakeholders (spec: requirements.md, ADR-0189)."""

from __future__ import annotations

from typing import Any

import pytest

from arcviz import projects, requirement_model
from arcviz.projects import ProjectError


def project() -> projects.Project:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_stakeholder", {"name": "Betrieb"})
    projects.apply(p, "add_driver", {"title": "Verfügbarkeit"})
    projects.apply(p, "add_scenario", {"title": "Ausfall eines Knotens"})
    projects.apply(p, "add_decision", {"title": "Replikation"})
    return p


def op(p: projects.Project, operation: str, **args: Any) -> Any:
    return projects.apply(p, operation, args)["result"]


def test_new_fields_are_omitted_from_dumps_while_empty() -> None:
    p = project()
    for item in (p.drivers[0], p.scenarios[0], p.decisions[0]):
        dump = item.model_dump()
        assert not {"requirements", "stakeholders", "origin"} & dump.keys()
    op(p, "update_driver", driver="d1", requirements=["req1"])
    assert p.drivers[0].model_dump()["requirements"] == ["req1"]


def test_old_documents_load_and_dump_unchanged() -> None:
    p = project()
    before = p.model_dump(mode="json")
    again = projects.Project.model_validate(before)
    assert again.model_dump(mode="json") == before


def test_update_replaces_and_link_requirements_adds() -> None:
    p = project()
    op(p, "update_driver", driver="d1", requirements=["req1", "req2"])
    out = op(p, "link_requirements", target_kind="driver", target="d1", requirements=["req2", "req3"])
    assert out["requirements"] == ["req1", "req2", "req3"]  # a union: the earlier trace stays
    again = op(p, "link_requirements", target_kind="driver", target="Verfügbarkeit", requirements=["req3"])
    assert again["requirements"] == ["req1", "req2", "req3"]  # idempotent, and the target is found by title
    op(p, "link_requirements", target_kind="scenario", target="q1", requirements=["req1"])
    op(p, "link_requirements", target_kind="decision", target="ADR-1", requirements=["req1"])
    assert p.scenarios[0].requirements == ["req1"] and p.decisions[0].requirements == ["req1"]
    op(p, "update_driver", driver="d1", requirements=[])  # a person's edit in the dialog replaces
    assert p.drivers[0].requirements == []


def test_link_requirements_errors_name_the_alternatives() -> None:
    p = project()
    with pytest.raises(ProjectError, match="Available: driver, scenario, decision"):
        op(p, "link_requirements", target_kind="element", target="x", requirements=["req1"])
    with pytest.raises(ProjectError, match="Unknown driver 'd9'"):
        op(p, "link_requirements", target_kind="driver", target="d9", requirements=["req1"])
    with pytest.raises(ProjectError, match="must not be empty"):
        op(p, "link_requirements", target_kind="driver", target="d1", requirements=[" "])
    too_many = [f"req{n}" for n in range(1, requirement_model.MAX_LINKED + 2)]
    with pytest.raises(ProjectError, match="At most 20 requirements"):
        op(p, "link_requirements", target_kind="driver", target="d1", requirements=too_many)
    assert p.drivers[0].requirements == []  # the failed operation changed nothing


def test_add_operations_take_links_and_origin() -> None:
    p = project()
    driver = op(p, "add_driver", title="Skalierbarkeit", requirements=["req4"], origin="p3/d_scale")
    assert driver["requirements"] == ["req4"] and driver["origin"] == "p3/d_scale"
    scenario = op(
        p, "add_scenario", title="Lastspitze", requirements=["req4"], stakeholders=["Betrieb"], origin="p3/q1"
    )
    assert scenario["stakeholders"] == ["s1"] and scenario["origin"] == "p3/q1"
    decision = op(p, "add_decision", title="Autoscaling", requirements=["req4"], stakeholders=["s1"], origin="p3/a")
    assert decision["stakeholders"] == ["s1"] and decision["origin"] == "p3/a"
    with pytest.raises(ProjectError, match="origin is at most"):
        op(p, "add_driver", title="X", origin="o" * 41)


def test_stakeholder_links_are_checked_and_cascade() -> None:
    p = project()
    op(p, "update_scenario", scenario="q1", stakeholders=["s1"])
    op(p, "update_decision", decision="adr1", stakeholders=["Betrieb"])
    with pytest.raises(ProjectError, match="Unknown stakeholder 'nobody'"):
        op(p, "update_scenario", scenario="q1", stakeholders=["nobody"])
    op(p, "remove_stakeholder", stakeholder="s1")
    assert p.scenarios[0].stakeholders == [] and p.decisions[0].stakeholders == []
    assert p.integrity_errors() == []


def test_integrity_rejects_a_dangling_stakeholder_link() -> None:
    p = project()
    p.scenarios[0].stakeholders = ["s9"]
    assert any("unknown stakeholder 's9'" in e for e in p.integrity_errors())
