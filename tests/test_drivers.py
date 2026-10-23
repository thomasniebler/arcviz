"""Architecture drivers of a project (spec: docs/architecture/specs/architecture-drivers.md, ADR-0163).

Operations, HTTP routes, bundles, the prompt block and the assistants' tools; every model reply is scripted.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange, projects
from arcviz.agent import DiagramAgent
from arcviz.agent_model import DRIVER_TOOL_NAMES
from arcviz.project_agent import ProjectAgent
from arcviz.project_model import DRIVERS_SUMMARY_BUDGET, MAX_DRIVERS, Driver, drivers_summary, ranked_drivers
from arcviz.projects import ProjectError
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from tests.conftest import ScriptedProvider, call
from tests.test_concurrency import done, tool_round


def project() -> projects.Project:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_element", {"name": "API", "kind": "component"})
    return p


def add(p: projects.Project, title: str, **args: Any) -> dict[str, Any]:
    return projects.apply(p, "add_driver", {"title": title, **args})["result"]


# ---- operations ------------------------------------------------------------------------------------------------


def test_add_driver_with_defaults_and_generated_ids():
    p = project()
    first = add(p, "Verfügbarkeit 99,9 %", importance="high", risk="high", elements=["API"], description=" x ")
    assert first == {
        "id": "d1",
        "title": "Verfügbarkeit 99,9 %",
        "kind": "quality_goal",
        "importance": "high",
        "risk": "high",
        "description": "x",
        "elements": ["api"],
    }
    assert add(p, "Nur Azure", kind="constraint")["id"] == "d2"
    projects.apply(p, "update_driver", {"driver": "d2", "status": "retired"})
    projects.apply(p, "remove_driver", {"driver": "d2"})
    assert add(p, "Budget", kind="constraint")["id"] == "d3"  # never reused
    assert p.driver_seq == 3


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"title": " "}, "must not be empty"),
        ({"title": "x" * 121}, "at most 120"),
        ({"title": "Neu", "kind": "wish"}, "Unknown driver kind 'wish'. Available: quality_goal"),
        ({"title": "Neu", "importance": "urgent"}, "Unknown driver importance"),
        ({"title": "Neu", "risk": "none"}, "Unknown driver risk"),
        ({"title": "Neu", "description": "x" * 1001}, "at most 1000"),
        ({"title": "Neu", "elements": ["gibts-nicht"]}, "Unknown element 'gibts-nicht'"),
        ({"title": "LAST"}, "Driver 'd1' is already called 'Last'"),
        ({"title": "Neu", "position": -1}, "Invalid position"),
    ],
)
def test_add_driver_refuses_bad_input_and_changes_nothing(args, message):
    p = project()
    add(p, "Last")
    before = p.model_dump()
    with pytest.raises(ProjectError, match=message):
        projects.apply(p, "add_driver", args)
    assert p.model_dump() == before


def test_at_most_max_drivers():
    p = project()
    p.drivers = [Driver(id=f"d{n}", title=f"T{n}") for n in range(1, MAX_DRIVERS + 1)]
    with pytest.raises(ProjectError, match=f"at most {MAX_DRIVERS}"):
        add(p, "Einer zu viel")


def test_update_move_and_remove_by_id_or_title():
    p = project()
    add(p, "Last")
    add(p, "Kosten")
    changed = projects.apply(p, "update_driver", {"driver": "kosten", "importance": "low", "elements": ["api", "API"]})
    assert changed["result"]["importance"] == "low" and changed["result"]["elements"] == ["api"]
    projects.apply(p, "move_driver", {"driver": "d2", "position": 0})
    assert [d.id for d in p.drivers] == ["d2", "d1"]
    with pytest.raises(ProjectError, match=r"Unknown driver 'd9'.*d2 \(Kosten\), d1 \(Last\)"):
        projects.apply(p, "update_driver", {"driver": "d9", "risk": "low"})
    with pytest.raises(ProjectError, match="already called"):
        projects.apply(p, "update_driver", {"driver": "d2", "title": "last"})
    projects.apply(p, "update_driver", {"driver": "d2", "title": "KOSTEN"})  # its own title in another case
    projects.apply(p, "update_driver", {"driver": "Last", "status": "retired"})
    assert projects.apply(p, "remove_driver", {"driver": "Last"})["result"]["driver"]["id"] == "d1"
    assert [d.id for d in p.drivers] == ["d2"]


def test_removing_an_element_keeps_the_driver_without_it():
    p = project()
    add(p, "Last", elements=["api"])
    result = projects.apply(p, "remove_element", {"element": "api"})["result"]
    assert result["drivers"] == ["d1"] and p.drivers[0].elements == []
    assert p.integrity_errors() == []


def test_integrity_reports_broken_drivers():
    p = project()
    p.drivers = [Driver(id="d1", title="A", kind="x", importance="y", elements=["nope"]), Driver(id="d1", title="B")]
    errors = p.integrity_errors()
    assert "driver 'd1' has unknown kind 'x'" in errors
    assert "driver 'd1' has unknown importance 'y'" in errors
    assert "driver 'd1' concerns unknown element 'nope'" in errors
    assert "duplicate driver id 'd1'" in errors


def test_a_project_without_drivers_dumps_as_before():
    dump = projects.new_project("Shop", "shop").model_dump()
    assert "drivers" not in dump and "driver_seq" not in dump


def test_driver_operations_are_public():
    assert set(projects.DRIVER_OPERATIONS) <= set(projects.PUBLIC_OPERATIONS)


# ---- priority and prompt ----------------------------------------------------------------------------------------


def test_ranked_by_importance_then_risk_then_list_order():
    p = project()
    add(p, "LL", importance="low", risk="low")
    add(p, "MH", importance="medium", risk="high")
    add(p, "HL", importance="high", risk="low")
    add(p, "HH", importance="high", risk="high")
    add(p, "HH2", importance="high", risk="high")
    assert [d.title for d in ranked_drivers(p.drivers)] == ["HH", "HH2", "HL", "MH", "LL"]


def test_summary_lines_and_budget():
    p = project()
    add(p, "Last", importance="high", risk="medium", description="10x Spitzenlast\nim Dezember", elements=["api"])
    assert drivers_summary(p.drivers) == (
        "- [d1] quality goal (importance high, risk medium): Last — 10x Spitzenlast im Dezember (concerns: api)"
    )
    many = [Driver(id=f"d{n}", title=f"Treiber {n}", description="x" * 900) for n in range(1, 101)]
    text = drivers_summary(many)
    assert len(text) <= DRIVERS_SUMMARY_BUDGET
    assert "more drivers of lower priority; call list_drivers" in text
    assert "xxx" not in text  # descriptions dropped before drivers are cut


# ---- HTTP -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    c = TestClient(create_app(store=store))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    assert c.post("/api/projects/shop/elements", json={"name": "API"}).status_code == 201
    return c


def test_driver_routes(client):
    r = client.post(
        "/api/projects/shop/drivers",
        json={"title": "Kosten", "kind": "constraint", "importance": "low", "elements": ["API"]},
    )
    assert r.status_code == 201
    assert r.json()["driver"]["id"] == "d1" and r.json()["driver"]["elements"] == ["api"]
    r = client.post("/api/projects/shop/drivers", json={"title": "Last", "importance": "high", "risk": "high"})
    assert [(d["id"], d["rank"]) for d in r.json()["drivers"]] == [("d1", 2), ("d2", 1)]
    r = client.patch("/api/projects/shop/drivers/d2", json={"description": "10x", "position": 0})
    assert r.status_code == 200 and r.json()["driver"]["description"] == "10x"
    assert [d["id"] for d in r.json()["drivers"]] == ["d2", "d1"]
    assert client.get("/api/projects/shop/drivers").json()["drivers"][0]["description"] == "10x"
    r = client.patch("/api/projects/shop/drivers/d2", json={"risk": "extreme", "description": "nein"})
    assert r.status_code == 400 and "Unknown driver risk" in r.json()["detail"]
    assert client.get("/api/projects/shop/drivers").json()["drivers"][0]["description"] == "10x"  # one write
    assert client.delete("/api/projects/shop/drivers/d1").status_code == 409  # retire first (ADR-0198)
    assert client.patch("/api/projects/shop/drivers/d1", json={"status": "retired"}).status_code == 200
    r = client.delete("/api/projects/shop/drivers/d1")
    assert r.status_code == 200 and r.json()["driver"]["id"] == "d1" and len(r.json()["drivers"]) == 1
    assert client.delete("/api/projects/shop/drivers/d1").status_code == 400
    assert client.get("/api/projects/nope/drivers").status_code == 404


def test_config_names_the_driver_vocabulary(client):
    model = client.get("/api/config").json()["project_model"]
    assert model["driver_kinds"] == ["quality_goal", "constraint", "requirement", "risk"]
    assert model["driver_levels"] == ["high", "medium", "low"]
    assert model["max_drivers"] == MAX_DRIVERS


# ---- bundles ---------------------------------------------------------------------------------------------------


def test_bundles_carry_drivers_and_keep_older_versions():
    plain = projects.new_project("Shop", "shop")
    assert exchange.export_project(plain, [])["schema_version"] == 1
    p = project()
    add(p, "Last", importance="high", elements=["api"])
    projects.apply(p, "update_driver", {"driver": "d1", "status": "retired"})
    projects.apply(p, "remove_driver", {"driver": "d1"})
    add(p, "Kosten", kind="constraint")
    bundle = exchange.export_project(p, [])
    assert bundle["schema_version"] == 4 and bundle["project"]["driver_seq"] == 2
    parsed = exchange.parse_project(json.loads(json.dumps(bundle)))
    assert [d.model_dump() for d in parsed.drivers] == [d.model_dump() for d in p.drivers]
    assert parsed.driver_seq == 2


def test_a_bundle_with_broken_drivers_is_refused():
    p = project()
    add(p, "Last")
    bundle = exchange.export_project(p, [])
    bundle["project"]["drivers"].append({**bundle["project"]["drivers"][0], "id": "d2", "importance": "top"})
    with pytest.raises(exchange.ExchangeError) as info:
        exchange.parse_project(bundle)
    text = " ".join(info.value.errors)
    assert "unknown importance 'top'" in text and "another driver has the title 'Last'" in text


def test_import_keeps_the_drivers(client):
    client.post("/api/projects/shop/drivers", json={"title": "Last", "elements": ["api"]})
    bundle = client.get("/api/projects/shop/export").json()
    r = client.post("/api/projects/import", json={"bundle": bundle})
    assert r.status_code == 201, r.text
    imported = r.json()["project"]["id"] if "project" in r.json() else r.json()["id"]
    drivers = client.get(f"/api/projects/{imported}/drivers").json()["drivers"]
    assert [(d["id"], d["title"], d["elements"]) for d in drivers] == [("d1", "Last", ["api"])]


# ---- the assistants --------------------------------------------------------------------------------------------


@pytest.fixture
def services(store, demo):
    project_service = ProjectService(store, store)
    project_service.create("Shop", "shop")
    project_service.place("demo", "shop")
    diagrams = DiagramService(store, project_service)
    return diagrams, project_service, ModelService(project_service, diagrams)


def results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(e["content"]) for e in events if e["type"] == "tool_result"]


def test_the_diagram_chat_sees_the_drivers_most_important_first(services):
    diagrams, project_service, models = services
    provider = ScriptedProvider([done("Hallo"), done("Hallo")])
    list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Hallo"))
    assert "Architecture drivers" not in provider.requests[0]["system"]
    assert set(provider.requests[0]["tools"]) >= DRIVER_TOOL_NAMES
    project_service.apply("shop", "add_driver", {"title": "Kosten", "kind": "constraint", "importance": "low"})
    project_service.apply("shop", "add_driver", {"title": "Verfügbarkeit", "importance": "high", "risk": "high"})
    list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Hallo"))
    system = provider.requests[1]["system"]
    assert "Weigh what you design or propose against them" in system
    first = system.index("- [d2] quality goal (importance high, risk high): Verfügbarkeit")
    assert first < system.index("- [d1] constraint (importance low, risk medium): Kosten")
    assert system.index("Architecture drivers (most important first):") < system.index("Current state:")


def test_the_diagram_chat_creates_and_lists_drivers(services):
    diagrams, project_service, models = services
    provider = ScriptedProvider(
        [
            tool_round(call("create_driver", "c1", title="Last", kind="quality_goal", importance="high", risk="low")),
            tool_round(call("list_drivers", "c2")),
            done(),
        ]
    )
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Erfasse Last"))
    created, listed = results(events)
    assert created["driver"]["id"] == "d1"
    assert [d["title"] for d in listed["drivers"]] == ["Last"]
    assert project_service.get("shop").drivers[0].importance == "high"
    assert events[-1]["error"] is None


def test_preview_mode_lists_drivers_but_refuses_changes(services):
    diagrams, project_service, models = services
    project_service.apply("shop", "add_driver", {"title": "Last"})
    provider = ScriptedProvider(
        [
            tool_round(call("list_drivers", "c1"), call("remove_driver", "c2", driver="d1")),
            done(),
        ]
    )
    agent = DiagramAgent(provider, diagrams, background=False, models=models)
    events = list(agent.chat("demo", "Lösche Last", propose=True))
    listed, refused = results(events)
    assert listed["drivers"][0]["id"] == "d1"
    assert refused["ok"] is False and "preview mode" in refused["error"]
    assert len(project_service.get("shop").drivers) == 1
    assert events[-1]["proposal"] is None  # list_drivers is a read, never an operation of the proposal (#227 review)


def test_a_driver_error_reaches_the_model(services):
    diagrams, _, models = services
    provider = ScriptedProvider([tool_round(call("update_driver", "c1", driver="d7", risk="low")), done()])
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "x"))
    (result,) = results(events)
    assert result["ok"] is False and "Unknown driver 'd7'" in result["error"]


def test_the_project_chat_sees_and_edits_drivers(services):
    diagrams, project_service, models = services
    project_service.apply("shop", "add_driver", {"title": "Last", "importance": "high"})
    provider = ScriptedProvider([tool_round(call("update_driver", "c1", driver="Last", risk="high")), done()])
    agent = ProjectAgent(provider, diagrams, project_service, models, background=False)
    list(agent.chat("shop", "Last ist riskant", active=None))
    assert "- [d1] quality goal (importance high, risk medium): Last" in provider.requests[0]["system"]
    assert set(provider.requests[0]["tools"]) >= DRIVER_TOOL_NAMES
    assert project_service.get("shop").drivers[0].risk == "high"


def test_the_chat_cannot_retire_and_deletes_only_what_the_user_retired(services):
    diagrams, project_service, models = services
    project_service.apply("shop", "add_driver", {"title": "Last"})
    provider = ScriptedProvider(
        [
            tool_round(call("update_driver", "c1", driver="d1", status="retired")),
            tool_round(call("remove_driver", "c2", driver="d1")),
            done(),
        ]
    )
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Lösche Last"))
    set_status, removed = results(events)
    assert set_status["ok"] is False and "up to the user" in set_status["error"]
    assert removed["ok"] is False and "Retire driver d1" in removed["error"]
    assert len(project_service.get("shop").drivers) == 1
    project_service.apply("shop", "update_driver", {"driver": "d1", "status": "retired"})  # the user retires it
    provider = ScriptedProvider([tool_round(call("remove_driver", "c3", driver="d1")), done()])
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Lösche Last"))
    assert results(events)[0]["ok"] is True
    assert project_service.get("shop").drivers == []
