"""The project review rules about requirements (spec: requirements.md, *Project review rules*)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arcviz import project_review as pr
from arcviz import projects
from arcviz import requirement_model as rqm
from arcviz.requirement_model import RequirementBox


def box_with(*raws: str) -> RequirementBox:
    box = RequirementBox()
    for raw in raws:
        rqm.apply(box, "add_requirement", {"raw": raw})
    return box


def refine(box: RequirementBox, rid: str, statement: str, **fields: object) -> None:
    rqm.apply(box, "update_requirement", {"requirement": rid, "statement": statement, **fields})
    rqm.apply(box, "set_status", {"requirement": rid, "status": "refined"})


def project() -> projects.Project:
    return projects.new_project("Shop", "shop")


def findings(rule: str, p: projects.Project, box: RequirementBox | None) -> list[pr.ProjectFinding]:
    snapshot = pr.ProjectSnapshot(p, [], box)
    return [f for f in pr.run_project_checks(snapshot) if f.rule == rule]


def test_the_rules_are_registered_and_silent_without_requirements() -> None:
    ids = {r.id for r in pr.PROJECT_RULES}
    for rule in (
        "requirement-unrefined",
        "requirement-uncovered",
        "requirement-no-stakeholder",
        "quality-requirement-no-measure",
        "requirement-conflict-open",
        "requirement-duplicate-open",
        "scenario-no-requirement",
        "decision-no-basis",
        "stakeholder-no-requirement",
    ):
        assert rule in ids
        assert findings(rule, project(), None) == []
        assert findings(rule, project(), RequirementBox()) == []


def test_unrefined_only_after_a_day() -> None:
    box = box_with("alpha alpha", "beta beta")
    assert findings("requirement-unrefined", project(), box) == []
    box.requirements[0].created_at = (datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds")
    (f,) = findings("requirement-unrefined", project(), box)
    assert f.requirements == ["req1"] and "1 Anforderung" in f.message and f.severity == "low"
    refine(box, "req1", "Alpha.")
    assert findings("requirement-unrefined", project(), box) == []


def test_uncovered_is_grouped_by_theme_and_cleared_by_a_trace() -> None:
    p = project()
    box = box_with("alpha alpha", "beta beta", "gamma gamma")
    refine(box, "req1", "Alpha.", theme="Daten")
    refine(box, "req2", "Beta.", theme="Daten")
    refine(box, "req3", "Gamma.")
    found = {f.id: f for f in findings("requirement-uncovered", p, box)}
    assert set(found) == {"requirement-uncovered:Daten", "requirement-uncovered:none"}
    assert found["requirement-uncovered:Daten"].requirements == ["req1", "req2"]
    assert found["requirement-uncovered:Daten"].severity == "medium"
    projects.apply(p, "add_driver", {"title": "Daten", "requirements": ["req1", "req2", "req3"]})
    assert findings("requirement-uncovered", p, box) == []


def test_no_stakeholder_and_no_measure() -> None:
    p = project()
    projects.apply(p, "add_stakeholder", {"name": "Betrieb"})
    box = box_with("alpha alpha", "beta beta", "gamma gamma")
    refine(box, "req1", "Alpha.", kind="quality")
    refine(box, "req2", "Beta.", kind="quality", measure="p95 < 200 ms", stakeholders=["s1"])
    (no_sh,) = findings("requirement-no-stakeholder", p, box)
    assert no_sh.requirements == ["req1"]
    (no_measure,) = findings("quality-requirement-no-measure", p, box)
    assert no_measure.requirements == ["req1"] and no_measure.severity == "medium"


def test_open_conflicts_and_duplicates() -> None:
    p = project()
    box = box_with(
        "Das System speichert alle Kundendaten dauerhaft verschlüsselt in der Datenbank", "zweite andere", "dritte hier"
    )
    rqm.apply(
        box,
        "update_requirement",
        {"requirement": "req2", "issues": [{"kind": "conflict", "other": "req3", "text": "Datenhaltung"}]},
    )
    (conflict,) = findings("requirement-conflict-open", p, box)
    assert conflict.requirements == ["req2", "req3"] and "Datenhaltung" in conflict.message
    rqm.apply(box, "resolve_issue", {"requirement": "req2", "index": 0})
    assert findings("requirement-conflict-open", p, box) == []
    refine(box, "req1", "Das System speichert alle Kundendaten dauerhaft verschlüsselt in der Datenbank.")
    rqm.apply(
        box,
        "add_requirement",
        {"raw": "Das System speichert alle Kundendaten dauerhaft verschlüsselt in der Datenbank"},
    )
    refine(box, "req4", "Das System speichert alle Kundendaten dauerhaft verschlüsselt in der Datenbank.")
    (dup,) = findings("requirement-duplicate-open", p, box)
    assert dup.requirements == ["req1", "req4"]
    rqm.apply(box, "set_status", {"requirement": "req4", "status": "duplicate", "duplicate_of": "req1"})
    assert findings("requirement-duplicate-open", p, box) == []


def test_scenarios_decisions_and_stakeholders() -> None:
    p = project()
    projects.apply(p, "add_stakeholder", {"name": "Betrieb", "influence": "high"})
    projects.apply(p, "add_stakeholder", {"name": "Marketing", "influence": "low"})
    projects.apply(p, "add_scenario", {"title": "Ausfall"})
    projects.apply(p, "add_decision", {"title": "Replikation", "status": "accepted"})
    projects.apply(p, "add_decision", {"title": "Entwurf"})
    box = box_with("alpha alpha")
    assert findings("scenario-no-requirement", p, box) == []  # nothing refined yet
    refine(box, "req1", "Alpha.")
    (sc,) = findings("scenario-no-requirement", p, box)
    assert "Ausfall" in sc.message
    (de,) = findings("decision-no-basis", p, box)
    assert de.id == "decision-no-basis:adr1" and "ADR-1" in de.message
    (sh,) = findings("stakeholder-no-requirement", p, box)
    assert "Betrieb" in sh.message and "Marketing" not in sh.message
    projects.apply(p, "update_scenario", {"scenario": "q1", "requirements": ["req1"]})
    projects.apply(p, "update_decision", {"decision": "adr1", "requirements": ["req1"]})
    rqm.apply(box, "update_requirement", {"requirement": "req1", "stakeholders": ["s1"]})
    for rule in ("scenario-no-requirement", "decision-no-basis", "stakeholder-no-requirement"):
        assert findings(rule, p, box) == []
