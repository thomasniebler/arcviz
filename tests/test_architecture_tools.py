"""The MCP tools for stakeholders, drivers, scenarios, decisions and requirements as plain calls (spec:
architecture-objects-over-mcp.md, ADR-0186, ADR-0187); the protocol is tests/test_mcp_server.py."""

from __future__ import annotations

from typing import Any

import pytest

from arcviz.agent_tools import AgentTools, ToolFailure
from arcviz.services import ServiceRoot
from arcviz.workspaces import Identity

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "vic", "out")}


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "vic", "viewer")
    ada.projects.apply("shop", "add_driver", {"title": "Availability", "importance": "high", "risk": "high"})
    ada.projects.apply("shop", "add_stakeholder", {"name": "Operations", "role": "Ops", "drivers": ["d1"]})
    ada.projects.apply(
        "shop", "add_scenario", {"title": "Node failure", "driver": "d1", "core": True, "response_measure": "< 30 s"}
    )
    ada.projects.apply(
        "shop", "add_decision", {"title": "Second region", "status": "accepted", "drivers": ["d1"], "scenario": "q1"}
    )
    ada.requirements.import_items(
        "shop",
        [
            {"raw": "Der Shop ist rund um die Uhr erreichbar", "external_id": "R-1"},
            {"raw": "Bestellungen sind exportierbar"},
        ],
    )
    return root


def tools(root: ServiceRoot, who: str = "ada", scope: str = "write") -> AgentTools:
    return AgentTools(root.for_actor(PEOPLE[who]), scope)


# ---- reading ---------------------------------------------------------------------------------------------------


def test_the_architecture_objects_can_be_read(root):
    t = tools(root, "vic", "read")
    assert t.list_stakeholders("shop")["stakeholders"][0]["drivers"] == ["d1"]
    driver = t.list_drivers("shop")["drivers"][0]
    assert driver["id"] == "d1" and driver["scenarios"] == 1 and driver["requirements"] == []
    full = t.get_driver("shop", "Availability")
    assert [s["id"] for s in full["scenarios"]] == ["q1"] and full["decisions"][0]["id"] == "adr1"
    assert full["stakeholders"] == [{"id": "s1", "name": "Operations"}]
    assert t.list_scenarios("shop", driver="d1")["scenarios"][0]["core"] is True
    assert (
        t.list_scenarios("shop")["scenarios"][0]["qualities"] == []
        and t.list_drivers("shop")["drivers"][0]["qualities"] == []
    )
    assert t.get_scenario("shop", "q1")["decisions"][0]["id"] == "adr1"
    listed = t.list_decisions("shop", status="accepted")["decisions"]
    assert listed[0]["label"] == "ADR-1" and t.get_decision("shop", "ADR-1")["title"] == "Second region"


def test_unknown_names_say_what_exists(root):
    t = tools(root, "vic", "read")
    with pytest.raises(ToolFailure, match="Unknown driver 'x' in project 'shop'. Drivers: d1"):
        t.get_driver("shop", "x")
    with pytest.raises(ToolFailure, match="Scenarios: q1"):
        t.get_scenario("shop", "x")
    with pytest.raises(ToolFailure, match="Decisions: adr1"):
        t.get_decision("shop", "x")
    with pytest.raises(ToolFailure, match="Unknown status"):
        t.list_decisions("shop", status="weird")
    with pytest.raises(ToolFailure, match="Unknown driver"):
        t.list_scenarios("shop", driver="x")


def test_requirements_are_listed_paged_and_traced(root):
    t = tools(root, "vic", "read")
    page = t.list_requirements("shop", limit=1)
    assert page["total"] == 2 and page["truncated"] is True and page["next"] == 1
    assert page["requirements"][0]["text"].startswith("Der Shop") and page["requirements"][0]["suggested"] is False
    assert t.list_requirements("shop", offset=1)["requirements"][0]["id"] == "req2"
    assert t.get_requirement("shop", "R-1")["requirement"]["id"] == "req1"
    assert t.get_traceability("shop", "req1") == {
        "requirement": "req1",
        "coverage": None,
        "trace": {"drivers": [], "scenarios": [], "decisions": []},
    }
    assert t.get_traceability("shop")["uncovered"] == 0  # nothing is refined yet
    with pytest.raises(ToolFailure, match="Unknown requirement 'nope'"):
        t.get_requirement("shop", "nope")


def test_strangers_see_nothing(root):
    t = tools(root, "out", "read")
    for call in (lambda: t.list_drivers("shop"), lambda: t.list_requirements("shop"), lambda: t.list_decisions("shop")):
        with pytest.raises(ToolFailure, match="not found"):
            call()


# ---- writing ---------------------------------------------------------------------------------------------------


def test_a_read_token_writes_nothing(root):
    t = tools(root, "ada", "read")
    for call in (
        lambda: t.add_requirements("shop", [{"raw": "x"}]),
        lambda: t.update_requirement("shop", "req1", {"theme": "x"}),
        lambda: t.suggest_refinement("shop", [{"requirement": "req1", "statement": "S."}]),
        lambda: t.add_stakeholder("shop", "Marketing"),
        lambda: t.update_stakeholder("shop", "s1", {"role": "x"}),
        lambda: t.propose_architecture_items("shop", [{"key": "a", "op": "add_driver", "arguments": {"title": "X"}}]),
    ):
        with pytest.raises(ToolFailure, match="read-only"):
            call()


def test_a_viewer_with_a_write_token_is_still_a_viewer(root):
    with pytest.raises(ToolFailure, match="viewer|permission|role|editor"):
        tools(root, "vic", "write").add_requirements("shop", [{"raw": "x"}])


def test_add_requirements_is_bounded_and_idempotent(root):
    t = tools(root)
    with pytest.raises(ToolFailure, match="At most 50"):
        t.add_requirements("shop", [{"raw": f"r {n}"} for n in range(51)])
    out = t.add_requirements(
        "shop",
        [{"raw": "Neu eins"}, {"raw": "Der Shop ist rund um die Uhr erreichbar"}, {"raw": "x", "priority": "urgent"}],
    )
    assert out["added"] == ["req3"] and len(out["skipped_duplicates"]) == 1 and len(out["rejected"]) == 1
    assert out["rejected"][0]["row"] == 3
    stored = t.get_requirement("shop", "req3")["requirement"]
    assert stored["client"] == "mcp" and stored["author"] == "ada"


def test_update_requirement_is_limited_to_staging_fields(root):
    t = tools(root)
    done = t.update_requirement("shop", "req1", {"theme": "Verfügbarkeit", "priority": "must"})
    assert done["requirement"]["theme"] == "Verfügbarkeit" and done["requirement"]["priority"] == "must"
    with pytest.raises(ToolFailure, match="cannot change statement"):
        t.update_requirement("shop", "req1", {"statement": "x"})
    with pytest.raises(ToolFailure, match="cannot change measure, kind"):
        t.update_requirement("shop", "req1", {"measure": "x", "kind": "quality"})


def test_suggest_refinement_changes_nothing_in_force(root):
    t = tools(root)
    with pytest.raises(ToolFailure, match="At most 25"):
        t.suggest_refinement("shop", [{"requirement": "req1", "statement": "S."}] * 26)
    out = t.suggest_refinement(
        "shop",
        [
            {
                "requirement": "req1",
                "statement": "Der Shop ist zu 99,9 % verfügbar.",
                "kind": "quality",
                "measure": "99,9 %",
            },
            {"requirement": "req9", "statement": "S."},
            {"requirement": "req2", "statement": "S.", "status": "refined"},
        ],
    )
    assert out["stored"] == ["req1"] and [r["requirement"] for r in out["rejected"]] == ["req9", "req2"]
    got = t.get_requirement("shop", "req1")["requirement"]
    assert got["status"] == "raw" and got["statement"] == "" and got["suggestion"]["client"] == "mcp"
    assert got["suggestion"]["by"] == "ada"


def test_stakeholders(root):
    t = tools(root)
    added = t.add_stakeholder("shop", "Marketing", role="Vertrieb", influence="low", drivers=["d1"])
    assert added["stakeholder"]["id"] == "s2" and added["stakeholder"]["drivers"] == ["d1"]
    changed = t.update_stakeholder("shop", "Marketing", {"influence": "high"})
    assert changed["stakeholder"]["influence"] == "high"
    with pytest.raises(ToolFailure, match="Unknown driver"):
        t.add_stakeholder("shop", "Recht", drivers=["d9"])
    with pytest.raises(ToolFailure, match="unknown argument"):
        t.update_stakeholder("shop", "s1", {"colour": "red"})


def test_propose_architecture_items_stores_a_proposal_and_changes_nothing(root):
    t = tools(root)
    items: list[dict[str, Any]] = [
        {"key": "d", "op": "add_driver", "arguments": {"title": "Antwortzeit"}, "derived_from": ["req1"]},
        {"key": "q", "op": "add_scenario", "arguments": {"title": "Spitze", "driver": "@d"}},
        {
            "key": "l",
            "op": "link_requirements",
            "arguments": {"target_kind": "driver", "target": "d1", "requirements": ["req2"]},
        },
    ]
    stored = t.propose_architecture_items("shop", items, "aus den Anforderungen")["proposal"]
    assert stored["kind"] == "project" and stored["status"] == "pending" and "diagram" not in stored
    assert [(i["key"], i["op"], i["state"]) for i in stored["items"]] == [
        ("d", "add_driver", "pending"),
        ("q", "add_scenario", "pending"),
        ("l", "link_requirements", "pending"),
    ]
    assert [d["id"] for d in t.list_drivers("shop")["drivers"]] == ["d1"]
    listed = t.list_proposals("shop")["proposals"][0]
    assert listed["kind"] == "project" and listed["items"][0]["key"] == "d" and "arguments" not in listed["items"][0]
    full = t.get_proposal("shop", stored["id"])["proposal"]
    assert full["items"][0]["arguments"]["title"] == "Antwortzeit"


def test_a_proposed_decision_can_only_be_a_draft(root):
    t = tools(root)
    with pytest.raises(ToolFailure, match="a draft until a person accepts it"):
        t.propose_architecture_items(
            "shop", [{"key": "a", "op": "add_decision", "arguments": {"title": "X", "status": "accepted"}}]
        )
    with pytest.raises(ToolFailure, match="cannot be proposed"):
        t.propose_architecture_items("shop", [{"key": "a", "op": "remove_driver", "arguments": {"driver": "d1"}}])
    with pytest.raises(ToolFailure, match="use link_requirements"):
        t.propose_architecture_items(
            "shop", [{"key": "a", "op": "update_driver", "arguments": {"driver": "d1", "requirements": ["req1"]}}]
        )


def test_the_tool_surface_has_no_way_to_decide():
    names = {n for n in dir(AgentTools) if not n.startswith("_")}
    assert not {
        n for n in names if any(w in n for w in ("apply", "discard", "accept", "delete", "remove_req", "set_status"))
    }


def test_retired_drivers_and_scenarios_are_listed_only_on_request(root):
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.apply("shop", "update_driver", {"driver": "d1", "status": "retired"})
    ada.projects.apply("shop", "update_scenario", {"scenario": "q1", "status": "retired"})
    t = tools(root)
    assert t.list_drivers("shop")["drivers"] == [] and t.list_scenarios("shop")["scenarios"] == []
    drivers = t.list_drivers("shop", include_retired=True)["drivers"]
    scenarios = t.list_scenarios("shop", include_retired=True)["scenarios"]
    assert [d["status"] for d in drivers] == ["retired"] and [s["status"] for s in scenarios] == ["retired"]
    context = t.get_architecture_context("shop")
    assert context["drivers"] == [] and context["core_scenarios"] == []
    assert t.get_driver("shop", "d1")["status"] == "retired"  # reading one by id still works
