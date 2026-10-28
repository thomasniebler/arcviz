"""The arc42 gap analysis (spec: docs/architecture/specs/arc42-gap-analysis.md, ADR-0211)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import arc42, arc42_gap_review, arc42_gaps, projects
from arcviz.llm.base import ModelReply
from arcviz.model import Diagram
from arcviz.project_review import ProjectSnapshot
from arcviz.projects import Membership
from arcviz.server import create_app
from tests.conftest import ScriptedProvider, call


def op(p: projects.Project, operation: str, **args: Any) -> Any:
    return projects.apply(p, operation, args)["result"]


def report(p: projects.Project, diagrams: list[Diagram] | None = None) -> arc42_gaps.GapReport:
    members = [
        (Membership(diagram=d.id, section=None, added_at="2026-10-28T00:00:00+00:00"), d) for d in diagrams or []
    ]
    snapshot = ProjectSnapshot(p, members)
    return arc42_gaps.analyse(arc42.build(snapshot), p)


def codes(r: arc42_gaps.GapReport, section: str | None = None) -> list[str]:
    return [g.id for s in r.sections if section in (None, s.id) for g in s.gaps]


def status(r: arc42_gaps.GapReport) -> dict[str, str]:
    return {s.id: s.status for s in r.sections}


def full() -> projects.Project:
    """A project with something in every section."""
    p = projects.new_project("Shop", "shop", "Ein Shop.")
    op(p, "set_vision", statement="Schnell einkaufen", goals=["Wenige Klicks"])
    op(p, "add_driver", title="Verfügbarkeit", kind="quality_goal", importance="high", risk="high")
    op(p, "add_driver", title="Budget", kind="constraint")
    op(p, "add_driver", title="Zahlungsdienst fällt aus", kind="risk")
    op(p, "add_scenario", title="Ausfall", driver="d1", core=True, stimulus="Knoten fällt aus", response="Failover")
    op(p, "add_stakeholder", name="Betrieb")
    op(p, "add_element", name="API", kind="component", description="Die Schnittstelle")
    op(p, "add_element", name="Knoten", kind="node")
    op(p, "add_relation", kind="deployed_on", source="API", target="Knoten")
    op(p, "set_arc42_note", section="08", text="Logging: strukturiert.")
    op(
        p,
        "add_decision",
        title="Aktiv/Aktiv",
        status="accepted",
        decision="Zwei Regionen",
        assumptions=[{"statement": "Failover < 60 s"}],
    )
    return p


DIAGRAMS = [
    Diagram(id="ctx", title="Kontext Übersicht", type="deployment"),
    Diagram(id="seq", title="Ablauf", type="sequence"),
]


def test_an_empty_project_misses_everything_and_vision_comes_first():
    r = report(projects.new_project("Leer", "leer"))
    assert [s.id for s in r.sections] == [f"{n:02d}" for n in range(1, 13)]
    assert set(status(r).values()) == {"missing"} and r.complete == 0 and r.total == 12
    assert codes(r, "01") == [
        "vision-missing",
        "quality-goals-missing",
        "stakeholders-missing",
    ]  # all high: vision first
    assert r.next is not None and r.next.id == "vision-missing" and r.next.section == "01"
    vision = next(g for g in r.sections[0].gaps if g.id == "vision-missing")
    assert vision.severity == "high" and vision.action == {"kind": "tab", "target": "vision"}
    assert "Vision" in vision.message


def test_a_complete_project_has_no_gaps():
    r = report(full(), DIAGRAMS)
    assert codes(r) == [] and set(status(r).values()) == {"complete"} and r.complete == 12 and r.next is None


def test_every_gap_code_can_be_raised_and_has_an_action():
    raised = set(codes(report(projects.new_project("Leer", "leer"))))
    p = full()
    op(p, "set_vision", statement="x")
    op(p, "add_driver", title="Latenz", kind="quality_goal")
    op(p, "add_decision", title="Ohne Test", status="accepted", decision="x")
    raised |= set(codes(report(p, DIAGRAMS)))
    assert raised == set(arc42_gaps.CATALOG)
    for code, (severity, action) in arc42_gaps.CATALOG.items():
        assert severity in ("high", "medium", "low") and action["kind"] in ("tab", "model", "new-diagram"), code


def test_status_missing_partial_complete():
    p = full()
    op(p, "set_vision", statement="Nur eine Aussage")
    r = report(p, DIAGRAMS)
    assert status(r)["01"] == "partial" and codes(r, "01") == ["vision-without-goals"]
    assert report(p).sections[2].status == "missing"  # no overview diagram: section 03 has nothing
    assert next(g for g in report(p).sections[2].gaps).action == {"kind": "new-diagram", "diagram_type": "deployment"}


def test_a_note_covers_the_emptiness_of_sections_two_to_twelve_but_not_the_vision():
    p = projects.new_project("Leer", "leer")
    op(p, "set_arc42_note", section="03", text="Kontext: siehe Wiki.")
    op(p, "set_arc42_note", section="01", text="Einführung folgt.")
    r = report(p)
    assert status(r)["03"] == "complete" and codes(r, "03") == []
    assert status(r)["01"] == "partial" and "vision-missing" in codes(r, "01")


def test_quality_goals_without_a_scenario_are_named():
    p = full()
    op(p, "add_driver", title="Latenz", kind="quality_goal")
    r = report(p, DIAGRAMS)
    gap = next(g for g in r.sections[9].gaps if g.id == "quality-goal-without-scenario")
    assert gap.items == ["d4"] and "Latenz" in gap.message and gap.action == {"kind": "tab", "target": "scenarios"}
    op(p, "update_driver", driver="d4", status="retired")  # retired goals do not count
    assert "quality-goal-without-scenario" not in codes(report(p, DIAGRAMS))


def test_accepted_decisions_without_assumptions_are_named_and_drafts_are_not():
    p = full()
    op(p, "add_decision", title="Ohne Test", status="accepted", decision="x")
    op(p, "add_decision", title="Entwurf", status="draft")
    gap = next(g for g in report(p, DIAGRAMS).sections[8].gaps)
    assert gap.id == "decision-without-assumptions" and gap.items == ["adr2"] and "ADR-2" in gap.message
    assert gap.severity == "low" and gap.action == {"kind": "tab", "target": "decisions", "item": "adr2"}
    assert report(p, DIAGRAMS).sections[8].status == "partial"


def test_the_analysis_is_deterministic_and_sorted_by_severity():
    p = projects.new_project("Leer", "leer")
    assert report(p).model_dump() == report(p).model_dump()
    ranks = {"high": 0, "medium": 1, "low": 2}
    for s in report(p).sections:
        assert [ranks[g.severity] for g in s.gaps] == sorted(ranks[g.severity] for g in s.gaps)


def test_gap_codes_of_the_arc42_document_are_known():
    doc = arc42.build(ProjectSnapshot(projects.new_project("Leer", "leer"), []))
    assert {g.code for s in doc.sections for g in s.gap_items} <= set(arc42_gaps.CATALOG)
    assert doc.sections[0].gaps == [g.text for g in doc.sections[0].gap_items]


# ---- the model pass ------------------------------------------------------------------------------------------------


def reply(**arguments: Any) -> ModelReply:
    return ModelReply("", [call("submit_gap_review", "g1", **arguments)], "tool_use", usage={"input_tokens": 3})


def test_the_pass_returns_validated_remarks_and_shows_the_digest():
    p = full()
    doc = arc42.build(ProjectSnapshot(p, []))
    provider = ScriptedProvider(
        [
            reply(
                remarks=[
                    {"section": "01", "kind": "thin", "text": "Die Ziele sind zu vage."},
                    {"section": "99", "kind": "thin", "text": "Gibt es nicht."},
                    {"section": "09", "kind": "inconsistent", "text": "Widerspricht 04.", "conflicts_with": "04"},
                    {"section": "02", "kind": "whatever", "text": "Unbekannte Art."},
                    {"section": "03", "kind": "outdated", "text": "  "},
                ]
            )
        ]
    )
    answer = arc42_gap_review.review(provider, doc)
    assert [(r["section"], r["kind"]) for r in answer["remarks"]] == [("01", "thin"), ("09", "inconsistent")]
    assert answer["remarks"][1]["conflicts_with"] == "04" and answer["llm"] == {
        "ok": True,
        "error": None,
        "dropped": 3,
        "usage": {"input_tokens": 3},
    }
    request = provider.requests[0]
    assert request["tools"] == ["submit_gap_review"]
    assert "## 1 Einführung und Ziele" in request["messages"][0]["content"]


def test_the_digest_is_bounded():
    p = full()
    op(p, "set_arc42_note", section="05", text="x" * 8000)
    for n in range(10):
        op(p, "set_arc42_note", section=f"{n + 1:02d}", text="y" * 8000)
    text = arc42_gap_review.digest(arc42.build(ProjectSnapshot(p, [])))
    assert len(text) <= arc42_gap_review.DIGEST_BUDGET + 200


@pytest.mark.parametrize(
    ("replies", "ok", "dropped"),
    [
        ([reply(remarks="nein")], False, 0),
        ([reply(remarks=[{"section": "x"}, 7])], True, 2),
        ([reply(remarks=[])], True, 0),
        ([ModelReply("Nein.", [], "refusal")], False, 0),
    ],
)
def test_unusable_answers_are_failures_not_errors(replies, ok, dropped):
    answer = arc42_gap_review.review(ScriptedProvider(replies), arc42.build(ProjectSnapshot(full(), [])))
    assert answer["remarks"] == [] and answer["llm"]["ok"] is ok and answer["llm"]["dropped"] == dropped


def test_remarks_are_capped_per_section():
    many = [{"section": "01", "kind": "thin", "text": f"Bemerkung {n}"} for n in range(9)]
    answer = arc42_gap_review.review(ScriptedProvider([reply(remarks=many)]), arc42.build(ProjectSnapshot(full(), [])))
    assert (
        len(answer["remarks"]) == arc42_gap_review.MAX_REMARKS
        and answer["llm"]["dropped"] == 9 - arc42_gap_review.MAX_REMARKS
    )


# ---- routes --------------------------------------------------------------------------------------------------------


def test_the_routes(store):
    provider = ScriptedProvider([reply(remarks=[{"section": "01", "kind": "thin", "text": "Vage."}])])
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    r = client.get("/api/projects/shop/arc42/gaps")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 12 and body["complete"] == 0 and body["next"]["id"] == "vision-missing"
    assert body["sections"][0]["id"] == "01" and body["sections"][0]["status"] == "missing"
    before = client.get("/api/projects/shop").json()
    r = client.post("/api/projects/shop/arc42/gaps/review")
    assert r.status_code == 200 and r.json()["remarks"][0]["text"] == "Vage." and r.json()["llm"]["ok"] is True
    assert client.get("/api/projects/shop").json() == before
    assert client.get("/api/projects/nope/arc42/gaps").status_code == 404
    assert client.post("/api/projects/nope/arc42/gaps/review").status_code == 404
