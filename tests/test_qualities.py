"""ISO/IEC 25010 characteristics of quality scenarios (spec: docs/architecture/specs/iso25010-qualities.md, ADR-0202).

The model is scripted (ScriptedProvider); a suggestion stores nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange, projects, quality_suggestion
from arcviz.llm.base import ModelReply
from arcviz.projects import ProjectError
from arcviz.quality_model import MAX_QUALITIES, QUALITY_CHARACTERISTICS, clean_qualities, driver_qualities
from arcviz.server import create_app
from tests.conftest import ScriptedProvider, call


def project() -> projects.Project:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_driver", {"title": "Verfügbarkeit"})
    return p


def reply(**arguments: Any) -> ModelReply:
    return ModelReply("", [call("submit_qualities", "q1", **arguments)], "tool_use", usage={"input_tokens": 3})


# ---- the catalogue and the cleaning of model output -----------------------------------------------------------------


def test_the_nine_characteristics_of_the_2023_edition():
    assert len(QUALITY_CHARACTERISTICS) == 9 and MAX_QUALITIES == 2
    assert {"interaction_capability", "flexibility", "safety"} <= set(QUALITY_CHARACTERISTICS)


@pytest.mark.parametrize(
    ("raw", "kept", "dropped"),
    [
        (["security"], ["security"], 0),
        (["reliability", "security", "safety"], ["reliability", "security"], 1),
        (["reliability", "reliability"], ["reliability"], 1),
        (["speed", 7, None, "security"], ["security"], 3),
        ([], [], 0),
    ],
)
def test_cleaning_keeps_known_distinct_characteristics_up_to_two(raw, kept, dropped):
    assert clean_qualities(raw) == (kept, dropped)


# ---- the model: operations, dumps, integrity ------------------------------------------------------------------------


def test_a_scenario_names_one_or_two_characteristics():
    p = project()
    projects.apply(p, "add_scenario", {"title": "A", "driver": "d1", "qualities": ["reliability"]})
    projects.apply(p, "add_scenario", {"title": "B", "qualities": ["security", "reliability"]})
    assert [s.qualities for s in p.scenarios] == [["reliability"], ["security", "reliability"]]
    assert p.integrity_errors() == []


@pytest.mark.parametrize(
    "bad",
    [["gibberish"], ["security", "reliability", "safety"], ["security", "security"], [""]],
)
def test_other_values_are_refused_with_the_available_ones(bad):
    p = project()
    with pytest.raises(ProjectError, match="ISO 25010"):
        projects.apply(p, "add_scenario", {"title": "A", "qualities": bad})
    projects.apply(p, "add_scenario", {"title": "B"})
    with pytest.raises(ProjectError, match="reliability"):
        projects.apply(p, "update_scenario", {"scenario": "q1", "qualities": bad})
    assert p.scenarios[0].qualities == []


def test_update_replaces_and_empty_clears():
    p = project()
    projects.apply(p, "add_scenario", {"title": "A", "qualities": ["security"]})
    projects.apply(p, "update_scenario", {"scenario": "q1", "qualities": ["maintainability"]})
    assert p.scenarios[0].qualities == ["maintainability"]
    projects.apply(p, "update_scenario", {"scenario": "q1", "title": "A2"})  # not named: unchanged
    assert p.scenarios[0].qualities == ["maintainability"]
    projects.apply(p, "update_scenario", {"scenario": "q1", "qualities": []})
    assert p.scenarios[0].qualities == []


def test_dumps_are_unchanged_while_empty():
    p = project()
    projects.apply(p, "add_scenario", {"title": "A"})
    assert "qualities" not in p.scenarios[0].model_dump()
    projects.apply(p, "update_scenario", {"scenario": "q1", "qualities": ["security"]})
    assert p.scenarios[0].model_dump()["qualities"] == ["security"]


def test_integrity_reports_a_stored_unknown_characteristic():
    p = project()
    projects.apply(p, "add_scenario", {"title": "A"})
    p.scenarios[0].qualities = ["gibberish"]
    assert any("invalid quality" in e for e in p.integrity_errors())


def test_a_driver_covers_the_characteristics_of_its_active_scenarios_in_standard_order():
    p = project()
    projects.apply(p, "add_scenario", {"title": "A", "driver": "d1", "qualities": ["security", "reliability"]})
    projects.apply(p, "add_scenario", {"title": "B", "driver": "d1", "qualities": ["reliability"]})
    projects.apply(p, "add_scenario", {"title": "C", "driver": "d1", "qualities": ["safety"]})
    assert driver_qualities("d1", p.scenarios) == ["reliability", "security", "safety"]
    p.scenarios[2].status = "retired"
    assert driver_qualities("d1", p.scenarios) == ["reliability", "security"]
    assert driver_qualities("d2", p.scenarios) == []


# ---- the review rule and the prompt ---------------------------------------------------------------------------------


def test_the_review_flags_scenarios_without_a_characteristic():
    from arcviz.project_review import ProjectSnapshot, run_project_checks

    p = project()
    projects.apply(p, "add_scenario", {"title": "A", "qualities": ["security"]})
    projects.apply(p, "add_scenario", {"title": "B"})
    findings = [f for f in run_project_checks(ProjectSnapshot(p, [])) if f.rule == "scenario-no-quality"]
    assert len(findings) == 1 and "„B“" in findings[0].message and "„A“" not in findings[0].message
    projects.apply(p, "update_scenario", {"scenario": "q2", "qualities": ["safety"]})
    assert not [f for f in run_project_checks(ProjectSnapshot(p, [])) if f.rule == "scenario-no-quality"]


def test_core_scenarios_name_their_characteristics_in_the_prompt():
    from arcviz import decision_model as dm

    p = project()
    projects.apply(
        p,
        "add_scenario",
        {"title": "A", "stimulus": "Ausfall", "driver": "d1", "core": True, "qualities": ["reliability"]},
    )
    assert dm.core_scenario_lines(p.scenarios, "d1") == ["  core: [q1] Ausfall (reliability)"]
    assert "ISO 25010: reliability" in dm.scenario_text(p.scenarios[0])


# ---- the suggestion -------------------------------------------------------------------------------------------------

TEXT = {"title": "Region fällt aus", "kind": "failure", "stimulus": "Region A fällt aus", "response_measure": "< 5 min"}


def test_a_valid_suggestion_is_returned_with_its_reason():
    provider = ScriptedProvider([reply(qualities=["reliability"], reason="Ausfallsicherheit.")])
    answer = quality_suggestion.suggest(provider, TEXT)
    assert answer == {
        "qualities": ["reliability"],
        "reason": "Ausfallsicherheit.",
        "llm": {"ok": True, "error": None, "dropped": 0, "usage": {"input_tokens": 3}},
    }
    request = provider.requests[0]
    assert (
        request["tools"] == ["submit_qualities"] and "stimulus: Region A fällt aus" in request["messages"][0]["content"]
    )


def test_gibberish_is_dropped_and_never_reaches_the_editor():
    provider = ScriptedProvider([reply(qualities=["blau", "reliability", "security", "safety"], reason="x")])
    answer = quality_suggestion.suggest(provider, TEXT)
    assert answer["qualities"] == ["reliability", "security"] and answer["llm"]["dropped"] == 2


@pytest.mark.parametrize(
    "replies",
    [
        [reply(qualities=["blau", 3], reason="x")],
        [reply(qualities="reliability", reason="x")],
        [reply(reason="nur Text")],
        [ModelReply("Ich mag nicht.", [], "refusal")],
    ],
)
def test_an_answer_without_a_valid_characteristic_is_a_failure_not_an_error(replies):
    answer = quality_suggestion.suggest(ScriptedProvider(replies), TEXT)
    assert answer["qualities"] == [] and answer["reason"] is None and answer["llm"]["ok"] is False


def test_the_tool_schema_only_offers_the_known_names():
    schema = quality_suggestion.QUALITIES_TOOL.parameters["properties"]["qualities"]
    assert schema["items"]["enum"] == list(QUALITY_CHARACTERISTICS) and schema["maxItems"] == 2


def test_the_route_suggests_and_stores_nothing(store):
    provider = ScriptedProvider([reply(qualities=["performance_efficiency"], reason="Antwortzeit.")])
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    before = client.get("/api/projects/shop").json()
    r = client.post("/api/projects/shop/scenarios/qualities", json=TEXT)
    assert r.status_code == 200 and r.json()["qualities"] == ["performance_efficiency"]
    assert client.get("/api/projects/shop").json() == before
    assert client.post("/api/projects/shop/scenarios/qualities", json={"title": "  "}).status_code == 400
    assert client.post("/api/projects/nope/scenarios/qualities", json=TEXT).status_code == 404


def test_the_scenario_routes_carry_the_characteristics_and_the_drivers_view_derives_them(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    client.post("/api/projects/shop/drivers", json={"title": "Verfügbarkeit"})
    made = client.post(
        "/api/projects/shop/scenarios", json={"title": "A", "driver": "d1", "qualities": ["reliability", "security"]}
    )
    assert made.status_code == 201 and made.json()["scenario"]["qualities"] == ["reliability", "security"]
    assert client.post("/api/projects/shop/scenarios", json={"title": "B", "qualities": ["x"]}).status_code == 400
    changed = client.patch("/api/projects/shop/scenarios/q1", json={"qualities": ["safety"]})
    assert changed.json()["scenario"]["qualities"] == ["safety"]
    drivers = client.get("/api/projects/shop/drivers").json()["drivers"]
    assert drivers[0]["qualities"] == ["safety"]


# ---- bundles --------------------------------------------------------------------------------------------------------


def test_a_bundle_with_characteristics_needs_the_newest_schema_and_round_trips():
    p = project()
    projects.apply(p, "add_scenario", {"title": "A", "driver": "d1"})
    assert exchange.export_project(p, [])["schema_version"] == exchange.DECISION_SUPPORT_PROJECT_SCHEMA_VERSION
    projects.apply(p, "update_scenario", {"scenario": "q1", "qualities": ["security"]})
    bundle = exchange.export_project(p, [])
    assert bundle["schema_version"] == exchange.QUALITIES_PROJECT_SCHEMA_VERSION < exchange.PROJECT_SCHEMA_VERSION
    assert bundle["project"]["scenarios"][0]["qualities"] == ["security"]
    parsed = exchange.parse_project(bundle)
    assert parsed.decision_support["scenarios"][0].qualities == ["security"]
