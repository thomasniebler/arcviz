"""What-if analysis of a quality scenario (spec: docs/architecture/specs/decision-support.md, ADR-0167).

The model is scripted (ScriptedProvider); nothing is stored by an analysis.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import projects, scenario_analysis
from arcviz.llm.base import ModelReply
from arcviz.project_review import ProjectSnapshot
from arcviz.server import create_app
from tests.conftest import ScriptedProvider, call


def snapshot() -> ProjectSnapshot:
    p = projects.new_project("Shop", "shop")
    for op, args in (
        ("add_element", {"name": "API"}),
        ("add_driver", {"title": "Verfügbarkeit", "importance": "high", "risk": "high"}),
        ("add_driver", {"title": "Kosten", "kind": "constraint", "importance": "medium"}),
        ("add_stakeholder", {"name": "Betrieb", "role": "Ops", "influence": "high", "drivers": ["d1"]}),
        (
            "add_scenario",
            {
                "title": "Region fällt aus",
                "kind": "failure",
                "stimulus": "Region A fällt aus",
                "response_measure": "< 5 min",
                "driver": "d1",
                "core": True,
            },
        ),
        ("add_decision", {"title": "Nur Azure", "status": "accepted", "decision": "Wir bleiben bei Azure."}),
        ("add_decision", {"title": "Entwurf ohne Bindung"}),
    ):
        projects.apply(p, op, args)
    return ProjectSnapshot(p, [])


def reply(**arguments: Any) -> ModelReply:
    return ModelReply("", [call("submit_scenario_analysis", "a1", **arguments)], "tool_use", usage={"input_tokens": 7})


GOOD = {
    "summary": "Heute fällt alles aus.",
    "drivers": [
        {"driver": "d1", "effect": "violates", "reason": "Keine zweite Region."},
        {"driver": "d9", "effect": "supports", "reason": "unbekannt"},
        {"driver": "d2", "effect": "great", "reason": "falscher Effekt"},
    ],
    "elements": [{"element": "api", "reason": "einzige Instanz"}, {"element": "ghost", "reason": "x"}],
    "sensitivity_points": ["Datenbank-Replikation"],
    "tradeoffs": ["Verfügbarkeit gegen Kosten"],
    "options": [
        {"title": "Aktiv/Passiv", "description": "Kalte zweite Region", "pros": ["günstig"], "cons": ["RTO 30 min"]},
        {
            "title": "Aktiv/Aktiv",
            "description": "",
            "pros": ["RTO < 1 min"],
            "cons": ["doppelte Kosten"],
            "instruction": "Füge im Diagramm platform eine zweite Region hinzu.",
        },
        {"title": "", "description": "ohne Titel", "pros": [], "cons": []},
    ],
    "recommended": 1,
}


def test_the_input_carries_the_architecture_context():
    snap = snapshot()
    text = scenario_analysis.analysis_input(snap, snap.project.scenarios[0])
    assert "- [d1] quality goal (importance high, risk high): Verfügbarkeit" in text
    assert "  core: [q1] Region A fällt aus → < 5 min" in text
    assert "- [s1] Betrieb (Ops, influence high)" in text
    assert "[adr1] ADR-1 Nur Azure — Wir bleiben bei Azure." in text and "Entwurf ohne Bindung" not in text
    assert "Scenario to analyse (serves driver [d1] Verfügbarkeit):" in text and "Stimulus: Region A fällt aus" in text


def test_a_valid_analysis_is_kept_item_by_item():
    snap = snapshot()
    provider = ScriptedProvider([reply(**GOOD)])
    answer = scenario_analysis.analyse(provider, snap, snap.project.scenarios[0])
    assert answer["llm"]["ok"] is True and answer["llm"]["dropped"] == 4
    assert [d["driver"] for d in answer["drivers"]] == ["d1"] and [e["element"] for e in answer["elements"]] == ["api"]
    assert [o["title"] for o in answer["options"]] == ["Aktiv/Passiv", "Aktiv/Aktiv"]
    assert answer["options"][1]["instruction"].startswith("Füge") and "instruction" not in answer["options"][0]
    assert answer["recommended"] == 1 and answer["scenario"]["id"] == "q1"
    assert provider.requests[0]["tools"] == ["submit_scenario_analysis"]
    assert "Accepted decisions are binding" in provider.requests[0]["system"]


def test_a_recommendation_outside_the_options_is_dropped():
    snap = snapshot()
    answer = scenario_analysis.analyse(
        ScriptedProvider([reply(**{**GOOD, "recommended": 7})]), snap, snap.project.scenarios[0]
    )
    assert answer["recommended"] is None


@pytest.mark.parametrize(
    "replies",
    [
        [ModelReply("Ich mag nicht.", [], "refusal")],
        [reply(**{**GOOD, "options": []})],
    ],
)
def test_a_failed_analysis_never_raises(replies):
    snap = snapshot()
    answer = scenario_analysis.analyse(ScriptedProvider(replies), snap, snap.project.scenarios[0])
    assert answer["llm"]["ok"] is False and answer["options"] == []


def test_the_route_writes_nothing(store):
    provider = ScriptedProvider([reply(**GOOD)])
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    client.post("/api/projects/shop/elements", json={"name": "API"})
    client.post("/api/projects/shop/drivers", json={"title": "Verfügbarkeit"})
    client.post("/api/projects/shop/scenarios", json={"title": "Region fällt aus", "driver": "d1"})
    before = client.get("/api/projects/shop").json()
    r = client.post("/api/projects/shop/scenarios/q1/analysis")
    assert r.status_code == 200 and r.json()["options"][0]["title"] == "Aktiv/Passiv"
    assert client.get("/api/projects/shop").json() == before
    assert client.post("/api/projects/shop/scenarios/q9/analysis").status_code == 404
