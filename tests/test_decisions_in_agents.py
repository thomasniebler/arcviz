"""Accepted decisions, stakeholders and core scenarios in the assistants and the AI reviews (spec:
docs/architecture/specs/decision-support.md, ADR-0168). Every model reply is scripted."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import project_review, review
from arcviz.agent import DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.project_agent import ProjectAgent
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from tests.conftest import ScriptedProvider, call
from tests.test_concurrency import done, tool_round


@pytest.fixture
def services(store, demo):
    projects = ProjectService(store, store)
    projects.create("Shop", "shop")
    projects.place("demo", "shop")
    diagrams = DiagramService(store, projects)
    for op, args in (
        ("add_driver", {"title": "Verfügbarkeit", "importance": "high", "risk": "high"}),
        ("add_stakeholder", {"name": "Betrieb", "role": "Ops", "influence": "high", "drivers": ["d1"]}),
        (
            "add_scenario",
            {
                "title": "Ausfall",
                "stimulus": "Region A fällt aus",
                "response_measure": "< 5 min",
                "driver": "d1",
                "core": True,
            },
        ),
        ("add_decision", {"title": "Zwei Regionen", "status": "accepted", "decision": "Aktiv/Aktiv in zwei Regionen."}),
        ("add_decision", {"title": "Nur ein Entwurf", "decision": "Vielleicht Kafka."}),
    ):
        projects.apply("shop", op, args)
    return diagrams, projects, ModelService(projects, diagrams)


def results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(e["content"]) for e in events if e["type"] == "tool_result"]


def test_the_diagram_chat_sees_the_architecture_block(services):
    diagrams, _, models = services
    provider = ScriptedProvider([done("Hallo")])
    list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Hallo"))
    system = provider.requests[0]["system"]
    assert "  core: [q1] Region A fällt aus → < 5 min" in system
    assert "Stakeholders (most influential first):\n- [s1] Betrieb (Ops, influence high); cares about d1" in system
    assert "Accepted architecture decisions (binding, newest first):\n- [adr1] ADR-1 Zwei Regionen" in system
    assert "do not make that change silently" in system
    assert "Nur ein Entwurf" not in system  # drafts do not bind
    assert (
        system.index("Architecture drivers")
        < system.index("Accepted architecture decisions")
        < system.index("Current state:")
    )


def test_the_project_chat_sees_it_too(services):
    diagrams, projects, models = services
    provider = ScriptedProvider([done("Hallo")])
    list(ProjectAgent(provider, diagrams, projects, models, background=False).chat("shop", "Hallo", active=None))
    assert "- [adr1] ADR-1 Zwei Regionen" in provider.requests[0]["system"]


def test_reading_tools_and_create_scenario(services):
    diagrams, projects, models = services
    provider = ScriptedProvider(
        [
            tool_round(call("list_decisions", "c1"), call("list_stakeholders", "c2"), call("list_scenarios", "c3")),
            tool_round(call("create_scenario", "c4", title="Last x10", kind="growth", stimulus="Zehnfache Last")),
            done(),
        ]
    )
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Was wenn x10?"))
    decisions, stakeholders, scenarios, created = results(events)
    assert [d["id"] for d in decisions["decisions"]] == ["adr1", "adr2"]  # also drafts
    assert stakeholders["stakeholders"][0]["name"] == "Betrieb" and scenarios["scenarios"][0]["id"] == "q1"
    assert created["scenario"]["id"] == "q2"
    assert [s.title for s in projects.get("shop").scenarios] == ["Ausfall", "Last x10"]


def test_create_scenario_takes_iso_characteristics_and_refuses_unknown_ones(services):
    diagrams, projects, models = services
    provider = ScriptedProvider(
        [
            tool_round(
                call("create_scenario", "c1", title="Last x10", kind="growth", stimulus="x", qualities=["flexibility"]),
                call("create_scenario", "c2", title="Murks", kind="growth", stimulus="x", qualities=["blau"]),
            ),
            done(),
        ]
    )
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Was wenn x10?"))
    created, refused = results(events)
    assert created["scenario"]["qualities"] == ["flexibility"]
    assert refused["ok"] is False and "ISO 25010" in refused["error"]
    assert [s.qualities for s in projects.get("shop").scenarios] == [[], ["flexibility"]]
    tool = next(t for t in diagrams_tools() if t.name == "create_scenario")
    assert tool.parameters["properties"]["qualities"]["maxItems"] == 2


def diagrams_tools():
    from arcviz.agent_model import SUPPORT_TOOLS

    return SUPPORT_TOOLS


def test_preview_mode_reads_but_creates_no_scenario(services):
    diagrams, projects, models = services
    provider = ScriptedProvider(
        [
            tool_round(
                call("list_decisions", "c1"), call("create_scenario", "c2", title="X", kind="growth", stimulus="x")
            ),
            done(),
        ]
    )
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "x", propose=True))
    listed, refused = results(events)
    assert listed["ok"] is True and refused["ok"] is False and "preview mode" in refused["error"]
    assert len(projects.get("shop").scenarios) == 1 and events[-1]["proposal"] is None


def test_the_reviews_check_against_accepted_decisions(services):
    diagrams, projects, _ = services
    text = review.review_input(diagrams.get("demo"), [], "- [adr1] ADR-1 Zwei Regionen")
    assert "Accepted architecture decisions of the project (binding):\n- [adr1] ADR-1 Zwei Regionen" in text
    assert "Widerspricht ADR-<n>:" in review.REVIEW_SYSTEM_PROMPT
    snapshot = project_review.ProjectSnapshot(*projects.load_members("shop"))
    assert "- [adr1] ADR-1 Zwei Regionen" in project_review.review_input(snapshot, [])
    assert "Widerspricht ADR-<n>:" in project_review.PROJECT_REVIEW_SYSTEM_PROMPT


def test_the_review_route_passes_the_decisions(store):
    reply = ModelReply("", [call("submit_review", "r1", summary="ok", findings=[])], "tool_use")
    provider = ScriptedProvider([reply])
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    client.post("/api/diagrams", json={"title": "P", "id": "p", "template": "empty", "project": "shop"})
    client.post("/api/projects/shop/decisions", json={"title": "Zwei Regionen", "status": "accepted"})
    assert client.post("/api/diagrams/p/review").status_code == 200
    assert "[adr1] ADR-1 Zwei Regionen" in json.dumps(provider.requests[0]["messages"], ensure_ascii=False)
