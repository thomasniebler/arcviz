"""Deriving drivers and scenarios from refined requirements into a project proposal (spec: requirements-workshop.md)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import projects
from arcviz import requirement_derive as rd
from arcviz import requirement_model as rqm
from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from tests.conftest import ScriptedProvider, call


def reply(**arguments: Any) -> ModelReply:
    return ModelReply("", [call("submit_derivation", "d1", **arguments)], "tool_use", usage={"input_tokens": 5})


def refine(box: rqm.RequirementBox, rid: str, statement: str, **fields: Any) -> None:
    rqm.apply(box, "update_requirement", {"requirement": rid, "statement": statement, **fields})
    rqm.apply(box, "set_status", {"requirement": rid, "status": "refined"})


def setup() -> tuple[projects.Project, rqm.RequirementBox]:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_element", {"name": "API"})
    projects.apply(p, "add_stakeholder", {"name": "Betrieb"})
    projects.apply(p, "add_driver", {"title": "Kosten", "kind": "constraint"})
    projects.apply(p, "add_scenario", {"title": "Lastspitze"})
    box = rqm.RequirementBox()
    for raw in ("a1 a1", "b2 b2", "c3 c3", "d4 d4"):
        rqm.apply(box, "add_requirement", {"raw": raw})
    refine(
        box,
        "req1",
        "Der Shop antwortet in 200 ms.",
        kind="quality",
        priority="must",
        measure="p95 < 200 ms",
        theme="Performance",
    )
    refine(
        box,
        "req2",
        "Der Shop ist rund um die Uhr erreichbar.",
        kind="quality",
        priority="should",
        theme="Verfügbarkeit",
    )
    refine(box, "req3", "Der Betrieb kann Berichte exportieren.", kind="functional", theme="Verfügbarkeit")
    return p, box


def covered(p: projects.Project, box: rqm.RequirementBox) -> dict[str, str | None]:
    return rqm.coverage_map(box, p)


GOOD = {
    "items": [
        {
            "key": "d_perf",
            "op": "add_driver",
            "requirements": ["req1", "req9"],
            "title": "Antwortzeit",
            "kind": "quality_goal",
            "risk": "high",
            "description": "Schnelle Antworten",
            "elements": ["api", "ghost"],
        },
        {
            "op": "add_scenario",
            "key": "q_peak",
            "requirements": ["req1"],
            "title": "Bestellspitze",
            "kind": "growth",
            "driver": "@d_perf",
            "stimulus": "Black Friday",
            "response_measure": "p95 < 200 ms",
            "core": True,
            "stakeholders": ["s1", "s9"],
        },
        {"op": "add_scenario", "requirements": ["req1"], "title": "Ohne Treiber", "driver": "@nobody", "core": True},
        {"op": "add_driver", "requirements": ["req2"], "title": "Kosten"},  # exists: becomes a link
        {"op": "link_requirements", "requirements": ["req2"], "target_kind": "scenario", "target": "q1"},
        {"op": "link_requirements", "requirements": ["req2"], "target_kind": "driver", "target": "d9"},
        {"op": "add_driver", "requirements": ["req4"], "title": "Gehört nicht zum Chunk"},
        {"op": "remove_driver", "requirements": ["req1"]},
        {"op": "add_driver", "requirements": ["req2"], "title": "   "},
    ],
    "unmapped": [{"requirement": "req3", "reason": "reine Funktion"}, {"requirement": "req9", "reason": "x"}],
}


def test_select_chunk_takes_refined_uncovered_requirements_by_theme():
    p, box = setup()
    chunk = rd.select_chunk(box, covered(p, box))
    assert [r.id for r in chunk] == ["req1", "req2", "req3"]  # Performance, then Verfügbarkeit by number
    assert [r.id for r in rd.select_chunk(box, covered(p, box), theme="verfügbarkeit")] == ["req2", "req3"]
    assert [r.id for r in rd.select_chunk(box, covered(p, box), limit=1)] == ["req1"]
    assert [r.id for r in rd.select_chunk(box, covered(p, box), busy=frozenset({"req1"}))] == ["req2", "req3"]
    assert rd.remaining(box, covered(p, box), frozenset({"req1"})) == 2


def test_covered_requirements_are_not_derived_again():
    p, box = setup()
    projects.apply(p, "update_driver", {"driver": "d1", "requirements": ["req1"]})
    assert [r.id for r in rd.select_chunk(box, covered(p, box))] == ["req2", "req3"]


def test_naming_a_raw_requirement_is_refused():
    p, box = setup()
    with pytest.raises(
        rqm.RequirementError, match="Requirement 'req4' is roh: only refined requirements are derived from"
    ):
        rd.select_chunk(box, covered(p, box), ids=["req4"])
    with pytest.raises(rqm.RequirementError, match="Unknown requirement 'nope'"):
        rd.select_chunk(box, covered(p, box), ids=["nope"])
    assert [r.id for r in rd.select_chunk(box, covered(p, box), ids=["req2"])] == ["req2"]


def test_the_input_shows_the_context_and_the_chunk():
    p, box = setup()
    projects.apply(
        p, "add_decision", {"title": "Nur Azure", "status": "accepted", "decision": "Wir bleiben bei Azure."}
    )
    text = rd.derive_input(p, rd.select_chunk(box, covered(p, box)))
    assert "[d1]" in text and "[q1] Lastspitze" in text and "[s1] Betrieb" in text and "[api] API" in text
    assert "Accepted decisions (binding):" in text and "Nur Azure" in text
    assert (
        "[req1] Der Shop antwortet in 200 ms. | kind quality | priority must | measure: p95 < 200 ms | theme Performance"
        in text
    )


def test_parse_builds_valid_proposal_items():
    p, box = setup()
    chunk = rd.select_chunk(box, covered(p, box))
    items, unmapped, dropped = rd.parse_derivation(GOOD, p, box, chunk)
    assert [(i["key"], i["op"]) for i in items] == [
        ("d_perf", "add_driver"),
        ("q_peak", "add_scenario"),
        ("i3", "add_scenario"),
        ("i4", "link_requirements"),
        ("i5", "link_requirements"),
    ]
    driver, scenario, bare, reuse, link = items
    assert driver["arguments"] == {
        "title": "Antwortzeit",
        "kind": "quality_goal",
        "importance": "high",  # from the priority 'must'
        "risk": "high",
        "description": "Schnelle Antworten",
        "elements": ["api"],
    }
    assert driver["derived_from"] == ["req1"]
    assert scenario["arguments"]["driver"] == "@d_perf" and scenario["arguments"]["core"] is True
    assert scenario["arguments"]["stakeholders"] == ["s1"]
    assert "driver" not in bare["arguments"] and "core" not in bare["arguments"]  # an unknown key is dropped
    assert reuse == {
        "key": "i4",
        "op": "link_requirements",
        "arguments": {"target_kind": "driver", "target": "d1", "requirements": ["req2"]},
    }
    assert link["arguments"] == {"target_kind": "scenario", "target": "q1", "requirements": ["req2"]}
    assert unmapped == [{"requirement": "req3", "reason": "reine Funktion"}]
    assert dropped >= 7


def test_items_are_bounded_and_titles_not_repeated():
    p, box = setup()
    chunk = rd.select_chunk(box, covered(p, box))
    many = {"items": [{"op": "add_driver", "requirements": ["req1"], "title": f"Treiber {n}"} for n in range(25)]}
    items, _, dropped = rd.parse_derivation(many, p, box, chunk)
    assert len(items) == rd.MAX_ITEMS and dropped == 5
    twice = {"items": [{"op": "add_driver", "requirements": ["req1"], "title": "Gleich"}] * 2}
    assert len(rd.parse_derivation(twice, p, box, chunk)[0]) == 1


def test_a_reply_becomes_items_and_a_failure_is_reported():
    p, box = setup()
    provider = ScriptedProvider([reply(**GOOD)])
    outcome = rd.derive(provider, p, box, rd.select_chunk(box, covered(p, box)))
    assert outcome.ok and len(outcome.items) == 5 and outcome.usage == {"input_tokens": 5}
    assert provider.requests[0]["tools"] == ["submit_derivation"] and "binding" in provider.requests[0]["system"]
    assert not rd.derive(ScriptedProvider([]), p, box, rd.select_chunk(box, covered(p, box))).ok
    assert (
        rd.derive(ScriptedProvider([reply(items=[])]), p, box, rd.select_chunk(box, covered(p, box))).error
        == "The model derived nothing."
    )


# ---- over HTTP -------------------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    answer = {
        "items": [
            {"key": "d", "op": "add_driver", "requirements": ["req1"], "title": "Antwortzeit", "importance": "high"},
            {"key": "q", "op": "add_scenario", "requirements": ["req1"], "title": "Spitze", "driver": "@d"},
        ],
        "unmapped": [{"requirement": "req2", "reason": "Funktion"}],
    }
    c = TestClient(create_app(store=store, provider=ScriptedProvider([reply(**answer)])))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    c.post(
        "/api/projects/shop/requirements/import",
        json={"text": "Antwortzeit unter 200 ms\nExport monatlich\nEin Roher\n"},
    )
    for rid, statement in (("req1", "Der Shop antwortet in 200 ms."), ("req2", "Der Export läuft monatlich.")):
        c.patch(f"/api/projects/shop/requirements/{rid}", json={"statement": statement})
    c.post("/api/projects/shop/requirements/status", json={"ids": ["req1", "req2"], "status": "refined"})
    return c


def test_derive_stores_a_project_proposal_and_writes_nothing_else(client):
    done = client.post("/api/projects/shop/requirements/derive", json={}).json()
    assert done["chunk"] == ["req1", "req2"] and done["llm"]["ok"] is True
    proposal = done["proposal"]
    assert proposal["kind"] == "project" and proposal["client"] == "derive" and proposal["status"] == "pending"
    assert [(i["key"], i["op"], i["derived_from"]) for i in proposal["items"]] == [
        ("d", "add_driver", ["req1"]),
        ("q", "add_scenario", ["req1"]),
    ]
    assert done["unmapped"] == [{"requirement": "req2", "reason": "Funktion"}]
    assert done["remaining"] == 1  # req2 is not served by the pending proposal
    assert client.get("/api/projects/shop/drivers").json()["drivers"] == []
    applied = client.post(f"/api/projects/shop/proposals/{proposal['id']}/apply").json()["proposal"]
    assert applied["status"] == "applied"
    traced = client.get("/api/projects/shop/requirements/req1").json()["requirement"]
    assert traced["coverage"] == "scenario" and traced["trace"] == {
        "drivers": ["d1"],
        "scenarios": ["q1"],
        "decisions": [],
    }
    assert client.get("/api/projects/shop/scenarios").json()["scenarios"][0]["driver"] == "d1"


def test_derive_refuses_raw_requirements_and_reports_a_failed_call(client):
    assert client.post("/api/projects/shop/requirements/derive", json={"ids": ["req3"]}).status_code == 400
    client.post("/api/projects/shop/requirements/derive", json={})  # uses the scripted reply
    again = client.post("/api/projects/shop/requirements/derive", json={"theme": ""}).json()
    assert again["proposal"] is None and again["llm"]["ok"] is False
    assert client.post("/api/projects/shop/requirements/derive", json={"limit": 99}).status_code == 422
    assert client.post("/api/projects/nope/requirements/derive", json={}).status_code == 404
