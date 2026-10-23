"""Graded deletion (spec: docs/architecture/specs/graded-deletion.md, ADR-0198).

Drivers and scenarios are retired before they can be deleted, decisions only when they are not accepted, requirements
delete directly (one or many). Retired drivers and scenarios stay out of prompts, rules and coverage.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import decision_model as dm
from arcviz import exchange, projects
from arcviz import requirement_model as rqm
from arcviz.agent_model import architecture_context, drivers_prompt
from arcviz.project_model import Driver, drivers_summary
from arcviz.projects import ProjectError, ProjectStateError
from arcviz.server import create_app


def op(p: projects.Project, operation: str, **args: Any) -> Any:
    return projects.apply(p, operation, args)["result"]


def project() -> projects.Project:
    p = projects.new_project("Shop", "shop")
    op(p, "add_driver", title="Verfügbarkeit", importance="high", risk="high")
    op(p, "add_scenario", title="Ausfall", driver="d1", core=True, stimulus="Knoten fällt aus")
    return p


# ---- drivers ----------------------------------------------------------------------------------------------------


def test_active_driver_dumps_without_status_and_retired_with_it():
    p = project()
    assert "status" not in p.drivers[0].model_dump()
    op(p, "update_driver", driver="d1", status="retired")
    assert p.drivers[0].model_dump()["status"] == "retired"
    op(p, "update_driver", driver="d1", status="active")
    assert "status" not in p.drivers[0].model_dump()


def test_unknown_status_is_refused():
    with pytest.raises(ProjectError, match="status"):
        op(project(), "update_driver", driver="d1", status="archived")


def test_an_active_driver_cannot_be_deleted_a_retired_one_can():
    p = project()
    with pytest.raises(ProjectStateError, match="Retire driver d1"):
        op(p, "remove_driver", driver="d1")
    assert [d.id for d in p.drivers] == ["d1"]
    op(p, "update_driver", driver="d1", status="retired")
    result = op(p, "remove_driver", driver="d1")
    assert result["unassigned_scenarios"] == ["q1"]  # the cascade is unchanged
    assert p.drivers == [] and p.scenarios[0].driver is None


def test_retired_drivers_are_left_out_of_the_prompts():
    p = project()
    op(p, "add_driver", title="Kosten")
    op(p, "update_driver", driver="d1", status="retired")
    summary = drivers_summary(p.drivers)
    assert "Kosten" in summary and "Verfügbarkeit" not in summary
    block = architecture_context(p).drivers or ""
    assert "Kosten" in block and "Verfügbarkeit" not in block and "Ausfall" not in block
    op(p, "update_driver", driver="d2", status="retired")
    assert architecture_context(p).drivers is None
    assert drivers_prompt(p.drivers) is None


def test_a_scenario_cannot_be_linked_to_a_retired_driver_but_keeps_an_existing_link():
    p = project()
    op(p, "update_driver", driver="d1", status="retired")
    with pytest.raises(ProjectError, match="retired"):
        op(p, "add_scenario", title="Neu", driver="d1")
    op(p, "update_scenario", driver="d1", scenario="q1", title="Ausfall 2")  # the same driver again: no new link
    assert p.scenarios[0].driver == "d1"


# ---- scenarios --------------------------------------------------------------------------------------------------


def test_scenarios_are_retired_before_they_are_deleted():
    p = project()
    with pytest.raises(ProjectStateError, match="Retire scenario q1"):
        op(p, "remove_scenario", scenario="q1")
    op(p, "update_scenario", scenario="q1", status="retired")
    assert p.scenarios[0].model_dump()["status"] == "retired"
    op(p, "remove_scenario", scenario="q1")
    assert p.scenarios == []


def test_a_retired_core_scenario_is_not_in_the_drivers_block():
    p = project()
    assert "Knoten fällt aus" in (dm.drivers_with_core_scenarios(p.drivers, p.scenarios) or "")
    op(p, "update_scenario", scenario="q1", status="retired")
    assert "Knoten fällt aus" not in (dm.drivers_with_core_scenarios(p.drivers, p.scenarios) or "")


# ---- decisions --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "deletable"), [("draft", True), ("rejected", True), ("superseded", True), ("accepted", False)]
)
def test_decision_deletion_by_status(status: str, deletable: bool):
    p = project()
    op(p, "add_decision", title="Cosmos DB")
    p.decisions[0].status = status
    if deletable:
        op(p, "remove_decision", decision="adr1")
        assert p.decisions == []
    else:
        with pytest.raises(ProjectStateError, match="ADR-1 is accepted"):
            op(p, "remove_decision", decision="adr1")
        assert len(p.decisions) == 1


def test_a_decision_cannot_be_newly_linked_to_a_retired_driver():
    p = project()
    op(p, "update_driver", driver="d1", status="retired")
    with pytest.raises(ProjectError, match="retired"):
        op(p, "add_decision", title="X", drivers=["d1"])


# ---- coverage ---------------------------------------------------------------------------------------------------


def test_a_requirement_covered_only_by_a_retired_driver_is_uncovered():
    box = rqm.RequirementBox()
    rqm.apply(box, "add_requirement", {"raw": "Das System ist verfügbar"})
    rqm.apply(box, "update_requirement", {"requirement": "req1", "statement": "Verfügbarkeit 99,9 %"})
    rqm.apply(box, "set_status", {"requirement": "req1", "status": "refined"})
    p = project()
    p.drivers[0].requirements = ["req1"]
    assert rqm.coverage_map(box, p)["req1"] == "driver"
    op(p, "update_driver", driver="d1", status="retired")
    assert rqm.coverage_map(box, p)["req1"] == "uncovered"


# ---- REST -------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def new_project(client: TestClient) -> str:
    return client.post("/api/projects", json={"title": "Shop"}).json()["project"]["id"]


def test_rest_delete_needs_retirement_and_answers_409_before(client: TestClient):
    pid = new_project(client)
    client.post(f"/api/projects/{pid}/drivers", json={"title": "Verfügbarkeit"})
    refused = client.delete(f"/api/projects/{pid}/drivers/d1")
    assert refused.status_code == 409 and "Retire driver d1" in refused.json()["detail"]
    retired = client.patch(f"/api/projects/{pid}/drivers/d1", json={"status": "retired"})
    assert retired.status_code == 200 and retired.json()["drivers"][0]["status"] == "retired"
    assert client.delete(f"/api/projects/{pid}/drivers/d1").status_code == 200


def test_rest_scenarios_and_decisions(client: TestClient):
    pid = new_project(client)
    client.post(f"/api/projects/{pid}/scenarios", json={"title": "Last"})
    assert client.delete(f"/api/projects/{pid}/scenarios/q1").status_code == 409
    assert client.patch(f"/api/projects/{pid}/scenarios/q1", json={"status": "retired"}).status_code == 200
    assert client.delete(f"/api/projects/{pid}/scenarios/q1").status_code == 200
    client.post(f"/api/projects/{pid}/decisions", json={"title": "Cosmos"})
    client.patch(f"/api/projects/{pid}/decisions/adr1", json={"status": "accepted"})
    assert client.delete(f"/api/projects/{pid}/decisions/adr1").status_code == 409
    client.patch(f"/api/projects/{pid}/decisions/adr1", json={"status": "rejected"})
    assert client.delete(f"/api/projects/{pid}/decisions/adr1").status_code == 200


def test_rest_bulk_delete_of_requirements(client: TestClient):
    pid = new_project(client)
    for text in ("eins", "zwei", "drei"):
        client.post(f"/api/projects/{pid}/requirements", json={"raw": text})
    result = client.post(f"/api/projects/{pid}/requirements/delete", json={"ids": ["req1", "req3", "req9"]})
    assert result.status_code == 200
    body = result.json()
    assert body["removed"] == ["req1", "req3"]
    assert [s["id"] for s in body["skipped"]] == ["req9"]
    left = client.get(f"/api/projects/{pid}/requirements").json()
    assert [r["id"] for r in left["requirements"]] == ["req2"]


def test_bulk_delete_is_not_taken_for_a_requirement_id(client: TestClient):
    pid = new_project(client)
    assert client.post(f"/api/projects/{pid}/requirements/delete", json={"ids": []}).status_code == 200
    too_many = client.post(f"/api/projects/{pid}/requirements/delete", json={"ids": [f"req{n}" for n in range(501)]})
    assert too_many.status_code == 422


def test_requirement_bulk_delete_strips_references(client: TestClient):
    pid = new_project(client)
    client.post(f"/api/projects/{pid}/requirements", json={"raw": "eins"})
    client.post(f"/api/projects/{pid}/drivers", json={"title": "Verfügbarkeit", "requirements": ["req1"]})
    client.post(f"/api/projects/{pid}/requirements/delete", json={"ids": ["req1"]})
    drivers = client.get(f"/api/projects/{pid}/drivers").json()["drivers"]
    assert "requirements" not in json.dumps(drivers[0])


def test_driver_model_default_status():
    assert Driver(id="d1", title="x").status == "active"


def test_a_bundle_carries_the_status_of_a_retired_object():
    p = project()
    op(p, "update_driver", driver="d1", status="retired")
    op(p, "update_scenario", scenario="q1", status="retired")
    bundle = json.loads(json.dumps(exchange.export_project(p, [])))
    parsed = exchange.parse_project(bundle)
    assert [d.status for d in parsed.drivers] == ["retired"] and [
        s.status for s in parsed.decision_support["scenarios"]
    ] == ["retired"]
    plain = json.dumps(exchange.export_project(project(), []))
    assert '"retired"' not in plain  # an active object dumps without status


def test_a_retired_driver_is_not_named_in_the_stakeholder_lines():
    p = project()
    op(p, "add_stakeholder", name="Betrieb", drivers=["d1"])
    assert "cares about d1" in (dm.stakeholder_lines(p.stakeholders, drivers=p.drivers) or "")
    op(p, "update_driver", driver="d1", status="retired")
    assert "cares about" not in (dm.stakeholder_lines(p.stakeholders, drivers=p.drivers) or "")
    assert "cares about" not in (architecture_context(p).stakeholders or "")
    assert p.stakeholders[0].drivers == ["d1"]  # the link itself stays until the driver is deleted
