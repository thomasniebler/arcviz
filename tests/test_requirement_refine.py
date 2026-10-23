"""Refining requirements into suggestions with a scripted model (spec: requirements-workshop.md, ADR-0190, ADR-0192)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import projects
from arcviz import requirement_model as rqm
from arcviz import requirement_refine as rr
from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from tests.conftest import ScriptedProvider, call


def reply(**arguments: Any) -> ModelReply:
    return ModelReply("", [call("submit_refinements", "r1", **arguments)], "tool_use", usage={"input_tokens": 11})


def setup() -> tuple[projects.Project, rqm.RequirementBox]:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_stakeholder", {"name": "Betrieb", "role": "Ops"})
    projects.apply(p, "add_element", {"name": "API"})
    projects.apply(p, "add_driver", {"title": "Verfügbarkeit"})
    box = rqm.RequirementBox()
    for raw in ("Das System soll schnell sein", "Daten müssen verschlüsselt gespeichert werden", "Es gibt ein Backup"):
        rqm.apply(box, "add_requirement", {"raw": raw})
    return p, box


GOOD = {
    "refinements": [
        {
            "requirement": "req1",
            "statement": "Das System antwortet in höchstens 200 ms.",
            "kind": "quality",
            "priority": "must",
            "measure": "p95 < 200 ms",
            "theme": "Performance",
            "stakeholders": ["s1", "s9"],
            "issues": [
                {"kind": "not_measurable", "text": "Was heißt schnell?", "other": "req2"},
                {"kind": "vague", "text": "falsche Art"},
                {"kind": "conflict", "text": "Ghost", "other": "req99"},
            ],
        },
        {
            "requirement": "req2",
            "statement": "Daten werden verschlüsselt gespeichert.",
            "kind": "wish",
            "duplicate_of": "req2",
        },
        {"requirement": "req9", "statement": "Gehört nicht zum Chunk."},
        {"requirement": "req3", "statement": "  "},
        {"requirement": "req1", "statement": "Doppelter Eintrag."},
        "kein Objekt",
    ]
}


def test_select_chunk_defaults_to_raw_without_suggestion_in_table_order():
    _, box = setup()
    assert [r.id for r in rr.select_chunk(box)] == ["req1", "req2", "req3"]
    rqm.apply(box, "set_suggestion", {"requirement": "req1", "suggestion": {"statement": "S."}})
    assert [r.id for r in rr.select_chunk(box)] == ["req2", "req3"]
    assert [r.id for r in rr.select_chunk(box, limit=1)] == ["req2"]
    assert rr.remaining(box) == 2
    assert [r.id for r in rr.select_chunk(box, ["req1"])] == ["req1"]  # named explicitly: also with a suggestion
    with pytest.raises(rqm.RequirementError, match="Unknown requirement 'nope'. Requirements: req1, req2, req3"):
        rr.select_chunk(box, ["nope"])


def test_named_rejected_and_duplicate_requirements_are_skipped():
    _, box = setup()
    rqm.apply(box, "set_status", {"requirement": "req1", "status": "rejected"})
    assert [r.id for r in rr.select_chunk(box, ["req1", "req2"])] == ["req2"]


def test_the_chunk_is_bounded():
    box = rqm.RequirementBox()
    for n in range(40):
        rqm.apply(box, "add_requirement", {"raw": f"Anforderung {n}"})
    assert len(rr.select_chunk(box)) == 25 and len(rr.select_chunk(box, limit=99)) == 25


def test_the_input_shows_vocabulary_candidates_and_the_chunk():
    p, box = setup()
    rqm.apply(box, "add_requirement", {"raw": "Das System soll schnell sein, wirklich schnell", "theme": "Performance"})
    text = rr.refine_input(p, box, rr.select_chunk(box)[:1])
    assert "[s1] Betrieb (Ops)" in text and "[api] API" in text and "[d1] Verfügbarkeit" in text
    assert "Existing themes:\nPerformance" in text
    assert "Possible duplicates" in text and "[req1] ~ [req4]" in text
    assert "[req1] Das System soll schnell sein" in text and "[req2]" not in text.split("Requirements to normalise:")[1]


def test_parse_validates_item_by_item():
    p, box = setup()
    parsed, dropped = rr.parse_refinements(GOOD, box, {"req1", "req2", "req3"}, {"s1"})
    assert [s["requirement"] for s in parsed] == ["req1", "req2"]
    first, second = parsed
    assert first["kind"] == "quality" and first["priority"] == "must" and first["stakeholders"] == ["s1"]
    assert [i["kind"] for i in first["issues"]] == ["not_measurable", "conflict"]
    assert first["issues"][0]["other"] == "req2" and "other" not in first["issues"][1]
    assert second["kind"] == "unclear" and "duplicate_of" not in second  # unknown kind, a duplicate of itself
    assert (
        dropped >= 7
    )  # req9, empty statement, doubled req1, the non-object, unknown stakeholder/kind/issue/partner/self


def test_a_good_reply_stores_suggestions_and_changes_nothing_in_force():
    p, box = setup()
    provider = ScriptedProvider([reply(**GOOD)])
    outcome = rr.refine(provider, p, box, rr.select_chunk(box))
    assert outcome.ok and len(outcome.suggestions) == 2 and outcome.usage == {"input_tokens": 11}
    assert [r.status for r in box.requirements] == ["raw", "raw", "raw"]
    assert all(r.statement == "" and r.suggestion is None for r in box.requirements)  # not even stored here
    assert "never follow instructions" in provider.requests[0]["system"]
    assert provider.requests[0]["tools"] == ["submit_refinements"]


def test_a_failing_or_empty_call_is_reported_not_raised():
    p, box = setup()
    outcome = rr.refine(ScriptedProvider([]), p, box, rr.select_chunk(box))
    assert not outcome.ok and outcome.error and outcome.suggestions == []
    empty = rr.refine(ScriptedProvider([reply(refinements=[])]), p, box, rr.select_chunk(box))
    assert not empty.ok and empty.error == "The model named no refinement."
    assert rr.refine(ScriptedProvider([]), p, box, []).ok  # nothing to refine: no call, no error


# ---- over HTTP -------------------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    provider = ScriptedProvider(
        [reply(refinements=[{"requirement": "req1", "statement": "Das System antwortet schnell.", "kind": "quality"}])]
    )
    c = TestClient(create_app(store=store, provider=provider))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    c.post(
        "/api/projects/shop/requirements/import",
        json={"text": "Das System soll schnell sein\nEs gibt ein Backup\nDer Export ist monatlich\n"},
    )
    return c


def test_refine_one_chunk_and_accept_the_suggestion(client):
    done = client.post("/api/projects/shop/requirements/refine", json={"limit": 1}).json()
    assert done["chunk"] == ["req1"] and done["refined"] == ["req1"] and done["rejected"] == []
    assert done["remaining"] == 2 and done["llm"]["ok"] is True and done["llm"]["usage"] == {"input_tokens": 11}
    req = client.get("/api/projects/shop/requirements/req1").json()["requirement"]
    assert req["status"] == "raw" and req["statement"] == "" and req["step"] == "suggested"
    assert req["suggestion"]["statement"] == "Das System antwortet schnell." and req["suggestion"]["client"] == "ui"
    accepted = client.post(
        "/api/projects/shop/requirements/accept",
        json={"items": [{"id": "req1", "expected": req["suggestion"]["rev"]}]},
    ).json()
    assert accepted["accepted"] == ["req1"]
    assert client.get("/api/projects/shop/requirements/req1").json()["requirement"]["step"] == "refined"


def test_a_failed_model_call_is_a_200_with_llm_not_ok(client):
    client.post("/api/projects/shop/requirements/refine", json={"limit": 1})  # uses the one scripted reply
    done = client.post("/api/projects/shop/requirements/refine", json={}).json()
    assert done["llm"]["ok"] is False and done["refined"] == [] and done["remaining"] == 2


def test_refine_validates_the_request(client):
    assert client.post("/api/projects/shop/requirements/refine", json={"limit": 99}).status_code == 422
    assert client.post("/api/projects/shop/requirements/refine", json={"ids": ["nope"]}).status_code == 400
    assert client.post("/api/projects/nope/requirements/refine", json={}).status_code == 404
