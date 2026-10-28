"""Stakeholders, quality scenarios and decisions of a project (spec: docs/architecture/specs/decision-support.md).

Operations, cascades, HTTP routes, bundles and the Markdown ADR.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import decision_model as dm
from arcviz import exchange, projects
from arcviz.projects import ProjectError
from arcviz.server import create_app


def project() -> projects.Project:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_element", {"name": "API", "kind": "component"})
    projects.apply(p, "add_driver", {"title": "Verfügbarkeit", "importance": "high", "risk": "high"})
    return p


def op(p: projects.Project, operation: str, **args: Any) -> Any:
    return projects.apply(p, operation, args)["result"]


# ---- stakeholders ------------------------------------------------------------------------------------------------


def test_stakeholders():
    p = project()
    s = op(
        p,
        "add_stakeholder",
        name="Betrieb",
        role="Ops",
        concerns="Nachtschichten",
        influence="high",
        drivers=["Verfügbarkeit"],
    )
    assert s == {
        "id": "s1",
        "name": "Betrieb",
        "role": "Ops",
        "concerns": "Nachtschichten",
        "influence": "high",
        "drivers": ["d1"],
    }
    with pytest.raises(ProjectError, match="already called"):
        op(p, "add_stakeholder", name="BETRIEB")
    with pytest.raises(ProjectError, match="Unknown influence 'huge'"):
        op(p, "add_stakeholder", name="X", influence="huge")
    assert op(p, "update_stakeholder", stakeholder="betrieb", role="SRE")["role"] == "SRE"
    op(p, "remove_stakeholder", stakeholder="s1")
    assert op(p, "add_stakeholder", name="Fachbereich")["id"] == "s2"  # never reused


# ---- scenarios ---------------------------------------------------------------------------------------------------


def test_scenarios_in_six_parts_with_one_driver_and_core():
    p = project()
    q = op(
        p,
        "add_scenario",
        title="Region fällt aus",
        kind="failure",
        source="Cloud-Anbieter",
        stimulus="Region A fällt aus",
        environment="Normalbetrieb",
        artifact="Gesamtsystem",
        response="Umschalten auf Region B",
        response_measure="< 5 min Ausfall",
        driver="d1",
        core=True,
        elements=["API"],
    )
    assert q["id"] == "q1" and q["driver"] == "d1" and q["core"] is True and q["elements"] == ["api"]
    assert [q[k] for k in dm.SCENARIO_PARTS] == [
        "Cloud-Anbieter",
        "Region A fällt aus",
        "Normalbetrieb",
        "Gesamtsystem",
        "Umschalten auf Region B",
        "< 5 min Ausfall",
    ]
    with pytest.raises(ProjectError, match="core scenario states a core goal"):
        op(p, "add_scenario", title="Ohne Treiber", core=True)
    with pytest.raises(ProjectError, match="Unknown scenario kind 'wish'"):
        op(p, "add_scenario", title="X", kind="wish")
    with pytest.raises(ProjectError, match="at most 500"):
        op(p, "add_scenario", title="X", stimulus="x" * 501)
    unassigned = op(p, "update_scenario", scenario="q1", driver="")
    assert unassigned["driver"] is None and unassigned["core"] is False


# ---- decisions ---------------------------------------------------------------------------------------------------


OPTIONS = [
    {"title": "Aktiv/Passiv", "description": "Zweite Region kalt", "pros": ["günstig"], "cons": ["RTO 30 min"]},
    {"title": "Aktiv/Aktiv", "pros": ["RTO < 1 min"], "cons": ["doppelte Kosten", "Datenkonsistenz"]},
]


def test_decisions_status_and_choice():
    p = project()
    op(p, "add_scenario", title="Region fällt aus", driver="d1")
    d = op(p, "add_decision", title="Zweite Region", options=OPTIONS, chosen=1, scenario="q1", drivers=["d1"])
    assert d["id"] == "adr1" and d["status"] == "draft" and d["decided_at"] is None and d["chosen"] == 1
    accepted = op(p, "update_decision", decision="ADR-1", status="accepted", text="Aktiv/Aktiv wegen RTO.")
    assert accepted["decided_at"] and accepted["decision"] == "Aktiv/Aktiv wegen RTO."
    with pytest.raises(ProjectError, match="names the decision that supersedes it"):
        op(p, "update_decision", decision="adr1", status="superseded")
    op(p, "add_decision", title="Drei Regionen")
    superseded = op(p, "update_decision", decision="adr1", status="superseded", superseded_by="ADR-2")
    assert superseded["superseded_by"] == "adr2"
    with pytest.raises(ProjectError, match="cannot supersede itself"):
        op(p, "update_decision", decision="adr2", superseded_by="adr2")
    with pytest.raises(ProjectError, match="Option 5 does not exist"):
        op(p, "update_decision", decision="adr1", chosen=5)
    assert op(p, "update_decision", decision="adr1", chosen=-1)["chosen"] is None
    op(p, "remove_decision", decision="adr2")  # adr1 is no longer superseded by anything
    assert p.decisions[0].superseded_by is None and p.decisions[0].status == "rejected"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ([{"title": ""}], "needs a title"),
        ([{"title": "A", "pros": ["x"] * 7}], "at most 6"),
        ([{"title": f"O{n}"} for n in range(6)], "at most 5 options"),
        (["nope"], "must be a list"),
    ],
)
def test_decision_options_are_bounded(options, message):
    with pytest.raises(ProjectError, match=message):
        op(project(), "add_decision", title="X", options=options)


def test_cascades_keep_items_but_drop_references():
    p = project()
    op(p, "add_stakeholder", name="Betrieb", drivers=["d1"])
    op(p, "add_scenario", title="Region fällt aus", driver="d1", core=True, elements=["api"])
    op(p, "add_decision", title="Zweite Region", scenario="q1", drivers=["d1"], elements=["api"])
    op(p, "update_driver", driver="d1", status="retired")
    result = op(p, "remove_driver", driver="d1")
    assert result["unassigned_scenarios"] == ["q1"]
    assert p.scenarios[0].driver is None and p.scenarios[0].core is False
    assert p.stakeholders[0].drivers == [] and p.decisions[0].drivers == []
    op(p, "remove_element", element="api")
    assert p.scenarios[0].elements == [] and p.decisions[0].elements == []
    op(p, "update_scenario", scenario="q1", status="retired")
    op(p, "remove_scenario", scenario="q1")
    assert p.decisions[0].scenario is None
    assert p.integrity_errors() == []


def test_a_project_without_decision_support_dumps_as_before():
    dump = projects.new_project("Shop", "shop").model_dump()
    for key in ("stakeholders", "scenarios", "decisions", "stakeholder_seq", "scenario_seq", "decision_seq"):
        assert key not in dump


def test_the_operations_are_public():
    assert set(projects.DECISION_SUPPORT_OPERATIONS) <= set(projects.PUBLIC_OPERATIONS)
    assert len(projects.DECISION_SUPPORT_OPERATIONS) == 9


# ---- Markdown ADR ------------------------------------------------------------------------------------------------


def test_markdown_adr():
    p = project()
    op(p, "add_scenario", title="Region fällt aus", driver="d1")
    op(
        p,
        "add_decision",
        title="Zweite Region",
        options=OPTIONS,
        chosen=1,
        scenario="q1",
        drivers=["d1"],
        elements=["api"],
        context="Region A kann ausfallen.",
        decision="Aktiv/Aktiv.",
        consequences="Mehr Kosten.",
        status="accepted",
    )
    text = dm.decision_markdown(p.decisions[0], p.drivers, p.elements, p.scenarios)
    assert text.startswith("# ADR-1: Zweite Region\n")
    assert "- Status: Angenommen (" in text
    assert "- Szenario: Region fällt aus (q1)" in text and "- Elemente: API" in text
    assert "## Entscheidungstreiber\n\n- Verfügbarkeit" in text
    assert "### 2. Aktiv/Aktiv (gewählt)" in text and "- Contra: doppelte Kosten" in text
    assert "## Entscheidung\n\nGewählte Option: „Aktiv/Aktiv“, weil Aktiv/Aktiv." in text
    assert "### Konsequenzen\n\nMehr Kosten." in text


# ---- prompt lines ------------------------------------------------------------------------------------------------


def test_prompt_lines():
    p = project()
    op(p, "add_stakeholder", name="Betrieb", role="Ops", concerns="ruhige Nächte", influence="high", drivers=["d1"])
    op(p, "add_stakeholder", name="Einkauf", influence="low")
    assert dm.stakeholder_lines(p.stakeholders).splitlines() == [
        "- [s1] Betrieb (Ops, influence high): ruhige Nächte; cares about d1",
        "- [s2] Einkauf (influence low)",
    ]
    op(
        p,
        "add_scenario",
        title="Ausfall",
        stimulus="Region A fällt aus",
        response_measure="< 5 min",
        driver="d1",
        core=True,
    )
    op(p, "add_scenario", title="Last", driver="d1")
    assert dm.core_scenario_lines(p.scenarios, "d1") == ["  core: [q1] Region A fällt aus → < 5 min"]
    op(p, "add_decision", title="Entwurf")
    assert dm.decision_lines(p.decisions) is None  # drafts do not bind
    op(p, "update_decision", decision="adr1", status="accepted", text="So machen wir es.")
    assert dm.decision_lines(p.decisions) == "- [adr1] ADR-1 Entwurf — So machen wir es."


# ---- HTTP --------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    c = TestClient(create_app(store=store))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    c.post("/api/projects/shop/elements", json={"name": "API"})
    c.post("/api/projects/shop/drivers", json={"title": "Verfügbarkeit"})
    return c


@pytest.mark.parametrize(
    ("plural", "singular", "body", "change"),
    [
        ("stakeholders", "stakeholder", {"name": "Betrieb", "drivers": ["d1"]}, {"role": "Ops"}),
        ("scenarios", "scenario", {"title": "Ausfall", "driver": "d1", "core": True}, {"stimulus": "Region weg"}),
        ("decisions", "decision", {"title": "Zweite Region", "options": OPTIONS}, {"status": "accepted"}),
    ],
)
def test_routes(client, plural, singular, body, change):
    base = f"/api/projects/shop/{plural}"
    r = client.post(base, json=body)
    assert r.status_code == 201, r.text
    item = r.json()[singular]
    assert [i["id"] for i in client.get(base).json()[plural]] == [item["id"]]
    r = client.patch(f"{base}/{item['id']}", json=change)
    assert r.status_code == 200 and all(r.json()[singular][k] == v for k, v in change.items())
    r = client.patch(f"{base}/{item['id']}", json={"nonsense": 1})
    assert r.status_code == 400 and "nonsense" in r.json()["detail"]
    retire = {"scenario": {"status": "retired"}, "decision": {"status": "rejected"}}.get(singular)
    if retire:  # graded deletion: retire (or reject) first, ADR-0198
        assert client.delete(f"{base}/{item['id']}").status_code == 409
        assert client.patch(f"{base}/{item['id']}", json=retire).status_code == 200
    r = client.delete(f"{base}/{item['id']}")
    assert r.status_code == 200 and r.json()[plural] == []
    assert client.delete(f"{base}/{item['id']}").status_code == 400
    assert client.get(f"/api/projects/nope/{plural}").status_code == 404


def test_markdown_route(client):
    client.post("/api/projects/shop/decisions", json={"title": "Zweite Region", "options": OPTIONS, "chosen": 0})
    r = client.get("/api/projects/shop/decisions/adr1/markdown")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert 'filename="adr-1.md"' in r.headers["content-disposition"]
    assert r.text.startswith("# ADR-1: Zweite Region")
    assert client.get("/api/projects/shop/decisions/adr9/markdown").status_code == 404


# ---- bundles -----------------------------------------------------------------------------------------------------


def test_bundles_carry_decision_support():
    p = project()
    op(p, "add_stakeholder", name="Betrieb", drivers=["d1"])
    op(p, "add_scenario", title="Ausfall", driver="d1", core=True)
    op(p, "add_decision", title="Zweite Region", scenario="q1", options=OPTIONS, chosen=0)
    bundle = exchange.export_project(p, [])
    assert bundle["schema_version"] == 5
    parsed = exchange.parse_project(json.loads(json.dumps(bundle)))
    assert [s.model_dump() for s in parsed.decision_support["scenarios"]] == [s.model_dump() for s in p.scenarios]
    assert parsed.decision_support["decision_seq"] == 1
    only_drivers = project()
    assert exchange.export_project(only_drivers, [])["schema_version"] == 4


def test_import_keeps_decision_support(client):
    client.post("/api/projects/shop/scenarios", json={"title": "Ausfall", "driver": "d1"})
    client.post("/api/projects/shop/decisions", json={"title": "Zweite Region", "scenario": "q1", "status": "accepted"})
    bundle = client.get("/api/projects/shop/export").json()
    r = client.post("/api/projects/import", json={"bundle": bundle})
    assert r.status_code == 201, r.text
    imported = r.json()["project"]["id"] if "project" in r.json() else r.json()["id"]
    decisions = client.get(f"/api/projects/{imported}/decisions").json()["decisions"]
    assert [(d["id"], d["scenario"], d["status"]) for d in decisions] == [("adr1", "q1", "accepted")]
