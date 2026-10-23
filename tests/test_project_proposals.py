"""Project proposals: items that a person applies in part (spec: architecture-objects-over-mcp.md, ADR-0187)."""

from __future__ import annotations

from typing import Any

import pytest

from arcviz import proposals
from arcviz.services import Forbidden, ServiceRoot
from arcviz.services.access import ProjectNotFound
from arcviz.services.proposals import ProposalError
from arcviz.workspaces import Identity

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "ed", "vic", "out")}

DRIVER = {"key": "d_avail", "op": "add_driver", "arguments": {"title": "Verfügbarkeit", "importance": "high"}}
SCENARIO = {
    "key": "q_fail",
    "op": "add_scenario",
    "arguments": {"title": "Ausfall eines Knotens", "driver": "@d_avail", "stimulus": "Knoten fällt aus", "core": True},
}


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.projects.apply("shop", "add_driver", {"title": "Kosten"})
    ada.requirements.import_items("shop", [{"raw": "alpha alpha"}, {"raw": "beta beta"}])
    return root


def as_(root: ServiceRoot, name: str):
    return root.for_actor(PEOPLE[name])


def propose(root: ServiceRoot, items: Any = None, who: str = "ed", **kw: Any):
    return as_(root, who).proposals.create_project("shop", [DRIVER, SCENARIO] if items is None else items, **kw)


def project(root: ServiceRoot):
    return as_(root, "ada").projects.get("shop")


# ---- create ----------------------------------------------------------------------------------------------------


def test_a_project_proposal_is_stored_and_changes_nothing(root):
    before = project(root).model_dump()
    p = propose(root, rationale="Aus den Anforderungen")
    assert (p.id, p.kind, p.status, p.diagram) == ("p1", "project", "pending", "")
    assert [(i.key, i.op, i.state) for i in p.items] == [
        ("d_avail", "add_driver", "pending"),
        ("q_fail", "add_scenario", "pending"),
    ]
    assert p.items[0].title == "Verfügbarkeit" and p.rationale == "Aus den Anforderungen"
    assert project(root).model_dump() == before
    listed = as_(root, "vic").proposals.list("shop")
    assert [x.id for x in listed] == ["p1"] and listed[0].kind == "project"


def test_diagram_proposals_dump_as_before(root):
    p = propose(root)
    dumped = p.model_dump(mode="json")
    assert dumped["kind"] == "project" and len(dumped["items"]) == 2
    legacy = proposals.StoredProposal(
        id="p9", project="shop", diagram="d", author="a", created_at="t", operations=[], base="b", result="r"
    ).model_dump(mode="json")
    assert "kind" not in legacy and "items" not in legacy


def test_items_are_checked_in_order_on_a_copy(root):
    with pytest.raises(ProposalError, match=r"Item 'q_fail' refers to @d_avail, which is not an earlier item"):
        propose(root, [SCENARIO, DRIVER])
    with pytest.raises(ProposalError, match=r"Item 'a' \(add_driver\) cannot be applied: .*title"):
        propose(root, [{"key": "a", "op": "add_driver", "arguments": {"title": "Kosten"}}])  # the title exists
    with pytest.raises(ProposalError, match="Unknown driver 'd9'"):
        propose(root, [{"key": "a", "op": "update_driver", "arguments": {"driver": "d9", "title": "X"}}])
    assert as_(root, "ada").proposals.list("shop") == []  # nothing stored, nothing written
    assert [d.id for d in project(root).drivers] == ["d1"]


def test_invalid_shapes_are_named(root):
    for items, message in (
        ([], "at least one item"),
        ("x", "at least one item"),
        ([{"key": "Bad Key", "op": "add_driver"}], "needs a key"),
        ([DRIVER, DRIVER], "used twice"),
        ([{"key": "a", "op": "remove_driver", "arguments": {}}], "cannot be proposed"),
        ([{"key": "a", "op": "add_driver", "arguments": {"title": "X", "origin": "p1/a"}}], "origin is set by Arcviz"),
        ([{"key": "a", "op": "add_driver", "arguments": "x"}], "must be an object"),
        (
            [{"key": "a", "op": "add_driver", "arguments": {"title": "X"}, "derived_from": "req1"}],
            "list of requirement ids",
        ),
    ):
        with pytest.raises(ProposalError, match=message):
            propose(root, items)
    with pytest.raises(ProposalError, match="at most 60 items"):
        propose(root, [{"key": f"k{n}", "op": "add_driver", "arguments": {"title": f"T{n}"}} for n in range(61)])


def test_an_agent_cannot_decide_a_decision(root):
    for field, value in (("status", "accepted"), ("superseded_by", "adr1")):
        with pytest.raises(ProposalError, match="a draft until a person accepts it"):
            propose(root, [{"key": "a", "op": "add_decision", "arguments": {"title": "X", field: value}}])
    ok = propose(root, [{"key": "a", "op": "add_decision", "arguments": {"title": "Replikation"}}])
    as_(root, "ed").proposals.apply("shop", ok.id)
    assert project(root).decisions[0].status == "draft"


def test_update_items_must_not_replace_the_trace(root):
    with pytest.raises(ProposalError, match="use link_requirements"):
        propose(root, [{"key": "a", "op": "update_driver", "arguments": {"driver": "d1", "requirements": ["req1"]}}])


def test_named_requirements_must_exist(root):
    with pytest.raises(ProposalError, match=r"unknown requirement\(s\) req9. Requirements: req1, req2"):
        propose(root, [{**DRIVER, "derived_from": ["req9"]}])
    with pytest.raises(ProposalError, match="unknown requirement"):
        propose(root, [{"key": "a", "op": "add_driver", "arguments": {"title": "X", "requirements": ["req9"]}}])


def test_the_limits(root, monkeypatch):
    monkeypatch.setattr(proposals, "MAX_PROJECT_PROPOSAL_BYTES", 300)
    import arcviz.services.proposals as service

    monkeypatch.setattr(service, "MAX_PROJECT_PROPOSAL_BYTES", 300)
    with pytest.raises(ProposalError, match="too large"):
        propose(root, [{"key": "a", "op": "add_driver", "arguments": {"title": "X", "description": "d" * 400}}])
    monkeypatch.undo()
    for n in range(proposals.MAX_PENDING_PROJECT):
        propose(root, [{"key": "a", "op": "add_driver", "arguments": {"title": f"T{n}"}}])
    with pytest.raises(ProposalError, match="5 pending project proposals"):
        propose(root, [{"key": "a", "op": "add_driver", "arguments": {"title": "One too many"}}])


def test_access(root):
    with pytest.raises(Forbidden):
        propose(root, who="vic")
    with pytest.raises(ProjectNotFound):
        propose(root, who="out")


# ---- apply ---------------------------------------------------------------------------------------------------------


def test_applying_everything_resolves_the_keys_and_marks_the_origin(root):
    p = propose(root, [{**DRIVER, "derived_from": ["req1"]}, {**SCENARIO, "derived_from": ["req1", "req2"]}])
    done = as_(root, "ed").proposals.apply("shop", p.id)
    assert done.status == "applied" and [i.state for i in done.items] == ["applied", "applied"]
    assert [i.created for i in done.items] == ["d2", "q1"]
    assert all(i.arguments == {} for i in done.items)  # a decided proposal keeps no arguments
    state = project(root)
    driver = next(d for d in state.drivers if d.id == "d2")
    assert driver.requirements == ["req1"] and driver.origin == f"{p.id}/d_avail"
    scenario = state.scenarios[0]
    assert scenario.driver == "d2" and scenario.core and scenario.requirements == ["req1", "req2"]
    assert as_(root, "ed").requirements.get("shop", "req1")["coverage"] is None  # raw: no coverage, the trace is stored
    assert as_(root, "ed").requirements.get("shop", "req1")["trace"]["drivers"] == ["d2"]


def test_partial_apply_and_dependencies(root):
    p = propose(root)
    svc = as_(root, "ed").proposals
    with pytest.raises(ProposalError, match="Item 'q_fail' needs 'd_avail'"):
        svc.apply("shop", p.id, accept=["q_fail"])
    with pytest.raises(ProposalError, match="No pending item 'nope'"):
        svc.apply("shop", p.id, accept=["nope"])
    assert project(root).drivers[-1].id == "d1" and svc.get("shop", p.id).status == "pending"
    first = svc.apply("shop", p.id, accept=["d_avail"])
    assert first.status == "pending" and [i.state for i in first.items] == ["applied", "pending"]
    assert first.items[0].arguments != {}  # still open: the pending item keeps its arguments
    second = svc.apply("shop", p.id, accept=["q_fail"])  # now the key resolves to the driver created before
    assert second.status == "applied" and project(root).scenarios[0].driver == "d2"
    with pytest.raises(ProposalError, match="already applied"):
        svc.apply("shop", p.id)


def test_an_update_goes_stale_when_its_target_changed(root):
    as_(root, "ada").projects.apply("shop", "update_driver", {"driver": "d1", "description": "alt"})
    p = propose(root, [{"key": "u", "op": "update_driver", "arguments": {"driver": "d1", "description": "neu"}}])
    assert p.items[0].base
    as_(root, "ada").projects.apply("shop", "update_driver", {"driver": "d1", "risk": "high"})
    done = as_(root, "ed").proposals.apply("shop", p.id)
    assert done.items[0].state == "stale" and "changed" in (done.items[0].error or "")
    assert done.status == "stale" and project(root).drivers[0].description == "alt"


def test_an_update_applies_when_the_target_is_unchanged_and_links_its_requirements(root):
    p = propose(
        root,
        [{"key": "u", "op": "update_driver", "arguments": {"driver": "d1", "risk": "high"}, "derived_from": ["req2"]}],
    )
    done = as_(root, "ed").proposals.apply("shop", p.id)
    assert done.status == "applied"
    d1 = project(root).drivers[0]
    assert d1.risk == "high" and d1.requirements == ["req2"]


def test_link_requirements_keeps_the_earlier_trace(root):
    as_(root, "ada").projects.apply("shop", "update_driver", {"driver": "d1", "requirements": ["req1"]})
    p = propose(
        root,
        [
            {
                "key": "l",
                "op": "link_requirements",
                "arguments": {"target_kind": "driver", "target": "d1", "requirements": ["req2"]},
            }
        ],
    )
    as_(root, "ed").proposals.apply("shop", p.id)
    assert project(root).drivers[0].requirements == ["req1", "req2"]


def test_a_failing_item_does_not_stop_the_others(root):
    p = propose(
        root,
        [
            {"key": "a", "op": "add_driver", "arguments": {"title": "Neuer Treiber"}},
            {"key": "b", "op": "add_driver", "arguments": {"title": "Zweiter"}},
        ],
    )
    as_(root, "ada").projects.apply("shop", "add_driver", {"title": "Zweiter"})  # taken meanwhile: b will fail
    done = as_(root, "ed").proposals.apply("shop", p.id)
    assert [i.state for i in done.items] == ["applied", "failed"] and done.status == "applied"
    assert "title" in (done.items[1].error or "")


def test_a_repeated_apply_after_a_crash_creates_no_second_object(root, monkeypatch):
    p = propose(root, [DRIVER])
    svc = as_(root, "ed").proposals
    original = type(svc)._finish_project

    def crash(self, *args, **kwargs):
        raise RuntimeError("process died before the proposal was marked")

    monkeypatch.setattr(type(svc), "_finish_project", crash)
    with pytest.raises(RuntimeError):
        svc.apply("shop", p.id)
    monkeypatch.setattr(type(svc), "_finish_project", original)
    assert [d.origin for d in project(root).drivers if d.origin] == [f"{p.id}/d_avail"]  # written, not yet marked
    stored = svc.get("shop", p.id)
    assert stored.status == "applying"  # the claim stays until it expires
    from datetime import UTC, datetime, timedelta

    claimed_at = datetime.now(UTC) - timedelta(seconds=proposals.CLAIM_TTL_SECONDS + 5)
    body, revision = root._repo.read_document(proposals.DOCUMENT, "shop")
    body["proposals"][0]["decided_at"] = claimed_at.isoformat(timespec="seconds")
    root._repo.commit_document(proposals.DOCUMENT, "shop", body, revision)
    done = svc.apply("shop", p.id)
    assert done.status == "applied" and done.items[0].created == "d2"
    assert len([d for d in project(root).drivers if d.origin]) == 1  # found by its origin, not created again


# ---- discard -------------------------------------------------------------------------------------------------------


def test_discard_items_and_dependents(root):
    p = propose(root)
    svc = as_(root, "ed").proposals
    with pytest.raises(ProposalError, match="No pending item 'nope'"):
        svc.discard("shop", p.id, keys=["nope"])
    gone = svc.discard("shop", p.id, keys=["d_avail"])  # the scenario needs it: discarded too
    assert [i.state for i in gone.items] == ["discarded", "discarded"] and gone.status == "discarded"
    assert project(root).drivers[-1].id == "d1"


def test_discard_one_independent_item_keeps_the_rest(root):
    p = propose(
        root,
        [
            {"key": "a", "op": "add_driver", "arguments": {"title": "A"}},
            {"key": "b", "op": "add_driver", "arguments": {"title": "B"}},
        ],
    )
    svc = as_(root, "ed").proposals
    partly = svc.discard("shop", p.id, keys=["a"])
    assert partly.status == "pending" and [i.state for i in partly.items] == ["discarded", "pending"]
    done = svc.apply("shop", p.id)
    assert done.status == "applied" and [d.title for d in project(root).drivers] == ["Kosten", "B"]


def test_discard_all(root):
    p = propose(root)
    done = as_(root, "ed").proposals.discard("shop", p.id)
    assert done.status == "discarded" and {i.state for i in done.items} == {"discarded"}


def test_keys_on_a_diagram_proposal_are_refused(root):
    from arcviz.templates import demo_diagram

    ada = as_(root, "ada")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    stored = ada.proposals.create(
        "shop", "orders", [{"op": "add_node", "arguments": {"name": "Cache", "kind": "service", "id": "cache"}}]
    )
    with pytest.raises(ProposalError, match="applied whole"):
        ada.proposals.apply("shop", stored.id, accept=["x"])
    with pytest.raises(ProposalError, match="discarded whole"):
        ada.proposals.discard("shop", stored.id, keys=["x"])


def test_viewers_cannot_decide(root):
    p = propose(root)
    with pytest.raises(Forbidden):
        as_(root, "vic").proposals.apply("shop", p.id)
    with pytest.raises(Forbidden):
        as_(root, "vic").proposals.discard("shop", p.id)
    with pytest.raises(ProjectNotFound):
        as_(root, "out").proposals.apply("shop", p.id)
