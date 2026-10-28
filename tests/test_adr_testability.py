"""ADR testability and the MADR export (spec: docs/architecture/specs/adr-testability.md, ADR-0209, ADR-0210)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import decision_model as dm
from arcviz import decision_testability, exchange, projects
from arcviz.llm.base import ModelReply
from arcviz.projects import ProjectError
from arcviz.server import create_app
from tests.conftest import ScriptedProvider, call

OPTIONS = [
    {"title": "Eine Region", "pros": ["günstig"], "cons": ["Ausfall"]},
    {"title": "Aktiv/Aktiv", "description": "Zwei Regionen.", "pros": ["ausfallsicher"], "cons": ["teuer"]},
]
ASSUMPTION = {"statement": "Failover dauert unter 60 s", "test": "Region in Staging abschalten", "effort": "low"}


def project() -> projects.Project:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_driver", {"title": "Verfügbarkeit", "importance": "high", "risk": "high"})
    return p


def op(p: projects.Project, operation: str, **args: Any) -> Any:
    return projects.apply(p, operation, args)["result"]


# ---- model and operations ------------------------------------------------------------------------------------------


def test_assumptions_and_falsification_are_stored_and_replaced():
    p = project()
    op(p, "add_decision", title="Zweite Region", assumptions=[ASSUMPTION], falsification="Failover > 5 min")
    d = p.decisions[0]
    assert d.assumptions[0].status == "untested" and d.assumptions[0].effort == "low"
    assert d.falsification == "Failover > 5 min"
    op(
        p,
        "update_decision",
        decision="adr1",
        assumptions=[{**ASSUMPTION, "status": "confirmed", "evidence": "Test 12"}],
    )
    assert p.decisions[0].assumptions[0].status == "confirmed" and p.decisions[0].falsification == "Failover > 5 min"
    op(p, "update_decision", decision="adr1", assumptions=[], falsification="")
    assert p.decisions[0].assumptions == [] and p.decisions[0].falsification == ""


def test_a_decision_without_them_dumps_as_before():
    p = project()
    op(p, "add_decision", title="Zweite Region", options=OPTIONS)
    dump = p.decisions[0].model_dump()
    assert "assumptions" not in dump and "falsification" not in dump
    op(p, "update_decision", decision="adr1", assumptions=[ASSUMPTION])
    assert "status" not in p.decisions[0].model_dump()["assumptions"][0]  # untested is omitted


@pytest.mark.parametrize(
    ("assumptions", "message"),
    [
        ([{"statement": " "}], "needs a statement"),
        ([{"statement": "x", "effort": "huge"}], "effort"),
        ([{"statement": "x", "status": "maybe"}], "status"),
        ([{"statement": "x"}] * (dm.MAX_ASSUMPTIONS + 1), "at most"),
        (["x"], "assumptions"),
        ([{"statement": "x" * 301}], "at most 300"),
    ],
)
def test_assumptions_are_validated(assumptions, message):
    with pytest.raises(ProjectError, match=message):
        op(project(), "add_decision", title="T", assumptions=assumptions)


# ---- clean_assumptions (model output) ------------------------------------------------------------------------------


def test_clean_assumptions_drops_what_cannot_be_used():
    kept, dropped = decision_testability.clean_assumptions(
        [ASSUMPTION, {"statement": ""}, {"statement": "y", "effort": "x"}, "z", {"statement": "q" * 999, "test": 3}]
    )
    assert [a.statement[:6] for a in kept] == ["Failov", "y", "qqqqqq"]
    assert kept[1].effort == "medium" and len(kept[2].statement) == dm.MAX_ASSUMPTION and kept[2].test == ""
    assert dropped == 2


def test_clean_assumptions_is_bounded():
    kept, dropped = decision_testability.clean_assumptions([{"statement": f"s{n}"} for n in range(20)])
    assert len(kept) == dm.MAX_ASSUMPTIONS and dropped == 12


# ---- the suggestion ------------------------------------------------------------------------------------------------


def reply(**arguments: Any) -> ModelReply:
    return ModelReply("", [call("submit_testability", "t1", **arguments)], "tool_use", usage={"input_tokens": 3})


VALUES = {"title": "Zweite Region", "context": "Region A fällt aus.", "decision": "Aktiv/Aktiv", "options": OPTIONS}


def test_suggest_returns_validated_items_and_asks_for_cheap_tests():
    provider = ScriptedProvider([reply(assumptions=[ASSUMPTION, {"statement": ""}], falsification="Failover > 5 min")])
    answer = decision_testability.suggest(provider, VALUES)
    assert answer["assumptions"][0]["statement"] == ASSUMPTION["statement"] and answer["llm"]["dropped"] == 1
    assert answer["falsification"] == "Failover > 5 min" and answer["llm"]["ok"] is True
    request = provider.requests[0]
    assert request["tools"] == ["submit_testability"] and "title: Zweite Region" in request["messages"][0]["content"]
    assert "Aktiv/Aktiv" in request["messages"][0]["content"]


@pytest.mark.parametrize(
    "replies",
    [
        [reply(assumptions=[{"statement": ""}], falsification="")],
        [reply(assumptions="nein", falsification=3)],
        [ModelReply("Nein.", [], "refusal")],
    ],
)
def test_an_unusable_answer_is_a_failure_not_an_error(replies):
    answer = decision_testability.suggest(ScriptedProvider(replies), VALUES)
    assert answer["assumptions"] == [] and answer["falsification"] == "" and answer["llm"]["ok"] is False


# ---- the route -----------------------------------------------------------------------------------------------------


def test_the_route_suggests_and_stores_nothing(store):
    provider = ScriptedProvider([reply(assumptions=[ASSUMPTION], falsification="x")])
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    before = client.get("/api/projects/shop").json()
    r = client.post("/api/projects/shop/decisions/testability", json=VALUES)
    assert r.status_code == 200 and r.json()["assumptions"][0]["effort"] == "low"
    assert client.get("/api/projects/shop").json() == before
    assert client.post("/api/projects/shop/decisions/testability", json={"title": " "}).status_code == 400
    assert client.post("/api/projects/nope/decisions/testability", json=VALUES).status_code == 404


def test_the_decision_routes_carry_the_fields(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    r = client.post(
        "/api/projects/shop/decisions", json={"title": "T", "assumptions": [ASSUMPTION], "falsification": "f"}
    )
    assert r.status_code == 201 and r.json()["decision"]["assumptions"][0]["statement"] == ASSUMPTION["statement"]
    r = client.patch("/api/projects/shop/decisions/adr1", json={"assumptions": [{"statement": ""}]})
    assert r.status_code == 400


# ---- the MADR export -----------------------------------------------------------------------------------------------


def madr(**args: Any) -> str:
    p = project()
    op(p, "add_scenario", title="Region fällt aus", driver="d1")
    op(p, "add_decision", title="Zweite Region", options=OPTIONS, scenario="q1", drivers=["d1"], **args)
    return dm.decision_markdown(p.decisions[0], p.drivers, p.elements, p.scenarios)


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#")]


def test_the_layout_is_madr_4():
    text = madr(
        status="accepted",
        chosen=1,
        context="Region A kann ausfallen.",
        decision="Verfügbarkeit zählt.",
        consequences="Mehr Kosten.",
        assumptions=[ASSUMPTION],
        falsification="Failover > 5 min",
    )
    assert headings(text) == [
        "# ADR-1: Zweite Region",
        "## Kontext und Problemstellung",
        "## Entscheidungstreiber",
        "## Betrachtete Optionen",
        "## Entscheidung",
        "### Konsequenzen",
        "## Vor- und Nachteile der Optionen",
        "### 1. Eine Region",
        "### 2. Aktiv/Aktiv (gewählt)",
        "## Annahmen und Testbarkeit",
    ]
    assert "Gewählte Option: „Aktiv/Aktiv“, weil Verfügbarkeit zählt." in text
    assert "1. Eine Region\n2. Aktiv/Aktiv" in text and "- Verfügbarkeit" in text
    assert "| Failover dauert unter 60 s | Region in Staging abschalten | niedrig | ungetestet |" in text
    assert "Widerlegt, wenn: Failover > 5 min" in text and "- Szenario: Region fällt aus (q1)" in text


def test_pipes_and_newlines_cannot_break_the_table():
    text = madr(assumptions=[{"statement": "a | b\nc", "test": "x|y"}])
    rows = [line for line in text.splitlines() if line.startswith("| a")]
    assert rows and rows[0].count("|") == 8 and "\\|" in rows[0]


def test_the_section_follows_the_status():
    assert "## Annahmen und Testbarkeit" not in madr()  # draft without testability: left out
    accepted = madr(status="accepted")
    assert "## Annahmen und Testbarkeit\n\n–" in accepted  # the visible gap
    assert "## Annahmen und Testbarkeit" in madr(falsification="x")


def test_an_undecided_adr_says_so():
    assert "Noch keine Option gewählt." in madr()


# ---- bundles -------------------------------------------------------------------------------------------------------


def test_bundles_use_version_10_only_with_testability():
    p = project()
    op(p, "add_decision", title="T")
    assert exchange.export_project(p, [])["schema_version"] == 5
    op(p, "update_decision", decision="adr1", assumptions=[ASSUMPTION], falsification="f")
    bundle = exchange.export_project(p, [])
    assert bundle["schema_version"] == exchange.PROJECT_SCHEMA_VERSION == 10
    parsed = exchange.parse_project(json.loads(json.dumps(bundle)))
    assert parsed.decision_support["decisions"][0].assumptions[0].statement == ASSUMPTION["statement"]
    assert parsed.decision_support["decisions"][0].falsification == "f"
