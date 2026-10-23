"""Stored proposals of agents (spec docs/architecture/specs/agent-access.md, ADR-0172): created over the service, listed,
applied or discarded by people; nothing is written to a diagram before a person applies it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from arcviz import proposals
from arcviz.model import fingerprint
from arcviz.ports import ConflictError, StoreError
from arcviz.proposal import ProposalConflict
from arcviz.services import Forbidden, ServiceRoot
from arcviz.services import proposals as proposals_service
from arcviz.services.access import ProjectNotFound
from arcviz.services.proposals import ProposalError
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "ed", "vic", "out")}
ADD_CACHE = [{"op": "add_node", "arguments": {"name": "Cache", "kind": "service", "id": "cache"}}]


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    ada.diagrams.create("Other", "other", demo_diagram("other", "Other"))
    return root


def as_(root: ServiceRoot, name: str):
    return root.for_actor(PEOPLE[name])


def propose(
    root: ServiceRoot, who: str = "ed", operations: Any = None, rationale: str = "Weil", diagram: str = "orders"
):
    return as_(root, who).proposals.create("shop", diagram, ADD_CACHE if operations is None else operations, rationale)


# ---- create ----------------------------------------------------------------------------------------------------


def test_a_proposal_is_stored_with_its_preview_and_changes_no_diagram(root):
    before = as_(root, "ada").diagrams.get("orders")
    versions = len(as_(root, "ada").diagrams.versions("orders"))
    p = propose(root)
    assert (p.id, p.status, p.project, p.diagram) == ("p1", "pending", "shop", "orders")
    assert p.author == "ed" and p.author_name == "Ed" and p.client == "mcp"
    assert p.base == fingerprint(before) and p.result != p.base
    assert [n["id"] for n in p.diff["nodes"]["added"]] == ["cache"]
    assert p.rationale == "Weil" and p.operations == ADD_CACHE
    after = as_(root, "ada").diagrams.get("orders")
    assert fingerprint(after) == p.base and "cache" not in after.nodes
    assert len(as_(root, "ada").diagrams.versions("orders")) == versions


def test_ids_count_up_and_are_never_reused(root):
    assert [propose(root).id, propose(root).id] == ["p1", "p2"]
    as_(root, "ed").proposals.discard("shop", "p2")
    assert propose(root).id == "p3"


def test_only_editors_propose(root):
    with pytest.raises(Forbidden):
        propose(root, "vic")
    with pytest.raises(ProjectNotFound):
        propose(root, "out")
    assert as_(root, "ada").proposals.list("shop") == []


def test_the_diagram_must_belong_to_the_project(root):
    with pytest.raises(ProposalError, match="not in project 'shop'"):
        propose(root, diagram="other")
    with pytest.raises(ProposalError, match="not in project 'shop'"):
        propose(root, diagram="nope")


@pytest.mark.parametrize(
    ("operations", "message"),
    [
        ([], "at least one operation"),
        ([{"op": "add_node", "arguments": {"name": "X", "kind": "bogus"}}], "bogus"),
        ([{"op": "no_such_op", "arguments": {}}], "no_such_op"),
        ([{"op": "link_element", "arguments": {"view": "api-gateway", "element": "x"}}], "link_element"),
        ([{"arguments": {}}], "op"),
        (["add_node"], "op"),
        ([{"op": "add_node", "arguments": "x"}], "arguments"),
    ],
)
def test_invalid_operations_are_refused_and_nothing_is_stored(root, operations, message):
    with pytest.raises(ProposalError, match=message):
        propose(root, operations=operations)
    assert as_(root, "ada").proposals.list("shop") == []


def test_limits(root):
    many = [{"op": "rename_diagram", "arguments": {"title": f"T{n}"}} for n in range(proposals.MAX_OPERATIONS + 1)]
    with pytest.raises(ProposalError, match="at most"):
        propose(root, operations=many)
    with pytest.raises(ProposalError, match="rationale"):
        propose(root, rationale="x" * (proposals.MAX_RATIONALE + 1))
    big = [{"op": "add_node", "arguments": {"name": "N" * 300, "kind": "service", "id": f"n{i}"}} for i in range(45)]
    with pytest.raises(ProposalError, match="too large"):
        propose(root, operations=big)


def test_the_pending_cap(root):
    for _ in range(proposals.MAX_PENDING):
        propose(root, operations=[{"op": "rename_diagram", "arguments": {"title": "T"}}])
    with pytest.raises(ProposalError, match="pending"):
        propose(root)
    as_(root, "ed").proposals.discard("shop", "p1")
    assert propose(root).id == f"p{proposals.MAX_PENDING + 1}"


# ---- read ------------------------------------------------------------------------------------------------------


def test_viewers_list_and_read_newest_first_with_a_status_filter(root):
    propose(root)
    propose(root)
    as_(root, "ed").proposals.discard("shop", "p1")
    vic = as_(root, "vic").proposals
    assert [p.id for p in vic.list("shop")] == ["p2", "p1"]
    assert [p.id for p in vic.list("shop", "pending")] == ["p2"]
    assert vic.get("shop", "p1").status == "discarded"
    with pytest.raises(ProposalError, match="Unknown proposal 'p9'"):
        vic.get("shop", "p9")
    with pytest.raises(ProposalError, match="status"):
        vic.list("shop", "bogus")
    with pytest.raises(ProjectNotFound):
        as_(root, "out").proposals.list("shop")
    with pytest.raises(ProjectNotFound):
        as_(root, "out").proposals.get("shop", "p1")


# ---- apply and discard -----------------------------------------------------------------------------------------


def test_applying_replays_the_proposal_as_one_version_and_adopts_the_new_view(root):
    propose(root)
    ada = as_(root, "ada")
    versions = len(ada.diagrams.versions("orders"))
    applied = ada.proposals.apply("shop", "p1")
    assert applied.status == "applied" and applied.decided_by == "ada" and applied.decided_at
    assert applied.version == versions + 1
    assert "cache" in ada.diagrams.get("orders").nodes
    assert any(e.name == "Cache" for e in ada.projects.get("shop").elements)  # views become elements (ADR-0164)
    with pytest.raises(ProposalError, match="already applied"):
        ada.proposals.apply("shop", "p1")
    with pytest.raises(ProposalError, match="already applied"):
        ada.proposals.discard("shop", "p1")


def test_a_stale_proposal_is_marked_and_never_applied(root):
    propose(root)
    ada = as_(root, "ada")
    ada.diagrams.apply_operation("orders", "rename_diagram", {"title": "Neu"}, "x")
    with pytest.raises(ProposalConflict):
        ada.proposals.apply("shop", "p1")
    assert ada.proposals.get("shop", "p1").status == "stale"
    assert "cache" not in ada.diagrams.get("orders").nodes
    ada.proposals.discard("shop", "p1")  # a stale proposal can still be discarded
    assert ada.proposals.get("shop", "p1").status == "discarded"


def test_a_removed_diagram_makes_the_proposal_stale(root):
    propose(root)
    ada = as_(root, "ada")
    ada.diagrams.delete("orders")
    with pytest.raises(ProposalConflict):
        ada.proposals.apply("shop", "p1")
    assert ada.proposals.get("shop", "p1").status == "stale"


def test_discarding_records_who_and_when(root):
    propose(root)
    discarded = as_(root, "ed").proposals.discard("shop", "p1")
    assert (discarded.status, discarded.decided_by) == ("discarded", "ed") and discarded.decided_at
    assert "cache" not in as_(root, "ada").diagrams.get("orders").nodes


def test_only_editors_decide(root):
    propose(root)
    vic = as_(root, "vic").proposals
    with pytest.raises(Forbidden):
        vic.apply("shop", "p1")
    with pytest.raises(Forbidden):
        vic.discard("shop", "p1")
    with pytest.raises(ProjectNotFound):
        as_(root, "out").proposals.apply("shop", "p1")
    assert as_(root, "ada").proposals.get("shop", "p1").status == "pending"


# ---- retention -------------------------------------------------------------------------------------------------


def age(store, days: int, key: str = "shop") -> None:
    body, revision = store.read_document(proposals.DOCUMENT, key)
    then = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    for item in body["proposals"]:
        if item["status"] != "pending":
            item["decided_at"] = then
    store.commit_document(proposals.DOCUMENT, key, body, revision)


def test_old_decided_proposals_are_dropped_when_listing_but_pending_ones_stay(root, store):
    propose(root)
    propose(root)
    as_(root, "ed").proposals.discard("shop", "p1")
    age(store, proposals.DEFAULT_RETENTION_DAYS + 1)
    assert [p.id for p in as_(root, "vic").proposals.list("shop")] == ["p2"]
    assert propose(root).id == "p3"


def test_only_the_newest_decided_proposals_are_kept(root):
    for n in range(1, proposals.KEEP_DECIDED + 6):
        propose(root, operations=[{"op": "rename_diagram", "arguments": {"title": "T"}}])
        as_(root, "ed").proposals.discard("shop", f"p{n}")
    kept = as_(root, "vic").proposals.list("shop")
    assert len(kept) == proposals.KEEP_DECIDED
    assert kept[0].id == f"p{proposals.KEEP_DECIDED + 5}"


def test_a_conflicting_concurrent_write_is_retried(root, store, monkeypatch):
    service = as_(root, "ed").proposals
    real = store.read_document
    calls = {"n": 0}

    def racing(kind, key):
        result = real(kind, key)
        if kind == proposals.DOCUMENT and calls["n"] == 0:
            calls["n"] += 1
            propose(root, "ada")  # somebody else writes between our read and our write
        return result

    propose(root, "ada")
    monkeypatch.setattr(store, "read_document", racing)
    created = service.create("shop", "orders", ADD_CACHE, "x")
    assert created.id == "p3"
    assert {p.id for p in as_(root, "ada").proposals.list("shop")} == {"p1", "p2", "p3"}


def test_conflict_is_given_up_after_the_attempts(root, store, monkeypatch):
    def always(*args: Any, **kwargs: Any) -> None:
        raise ConflictError("busy")

    propose(root)
    monkeypatch.setattr(store, "commit_document", always)
    with pytest.raises(ConflictError):
        propose(root)


# ---- deciding at the same time, interrupted applications, recreated projects (review of #239) ------------------------


def test_discarding_while_the_proposal_is_being_applied_is_refused_and_the_application_stands(root, monkeypatch):
    propose(root)
    real = proposals_service.apply_proposal
    seen: dict[str, Any] = {}

    def apply_while_somebody_discards(*args: Any, **kwargs: Any):
        with pytest.raises(ProposalError, match="being applied"):
            as_(root, "ed").proposals.discard("shop", "p1")
        seen["status"] = as_(root, "ada").proposals.get("shop", "p1").status
        return real(*args, **kwargs)

    monkeypatch.setattr(proposals_service, "apply_proposal", apply_while_somebody_discards)
    applied = as_(root, "ada").proposals.apply("shop", "p1")
    assert seen["status"] == "applying" and applied.status == "applied"
    assert "cache" in as_(root, "ada").diagrams.get("orders").nodes


def test_a_second_apply_during_an_application_is_refused(root, monkeypatch):
    propose(root)
    real = proposals_service.apply_proposal

    def apply_twice(*args: Any, **kwargs: Any):
        with pytest.raises(ProposalError, match="being applied"):
            as_(root, "ed").proposals.apply("shop", "p1")
        return real(*args, **kwargs)

    monkeypatch.setattr(proposals_service, "apply_proposal", apply_twice)
    assert as_(root, "ada").proposals.apply("shop", "p1").status == "applied"


def test_a_failure_before_anything_was_written_releases_the_proposal(root, monkeypatch):
    propose(root)

    def broken(*args: Any, **kwargs: Any):
        raise StoreError("the store is down")

    monkeypatch.setattr(proposals_service, "apply_proposal", broken)
    with pytest.raises(StoreError, match="down"):
        as_(root, "ada").proposals.apply("shop", "p1")
    assert as_(root, "ada").proposals.get("shop", "p1").status == "pending"
    assert "cache" not in as_(root, "ada").diagrams.get("orders").nodes


def test_an_interrupted_application_is_recognised_by_its_result_when_tried_again(root, monkeypatch):
    propose(root)
    real = proposals_service.apply_proposal

    def written_then_lost(*args: Any, **kwargs: Any):
        real(*args, **kwargs)  # the diagram is written ...
        raise RuntimeError("... and the process dies before the proposal is marked")

    monkeypatch.setattr(proposals_service, "apply_proposal", written_then_lost)
    with pytest.raises(RuntimeError):
        as_(root, "ada").proposals.apply("shop", "p1")
    monkeypatch.setattr(proposals_service, "apply_proposal", real)
    again = as_(root, "ada").proposals.apply("shop", "p1")  # the base no longer matches, the result does
    assert again.status == "applied" and again.version == 2
    assert "cache" in as_(root, "ada").diagrams.get("orders").nodes


def claim(store, status: str = "applying", age_seconds: int = 0) -> None:
    body, revision = store.read_document(proposals.DOCUMENT, "shop")
    body["proposals"][0]["status"] = status
    body["proposals"][0]["decided_at"] = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat(
        timespec="seconds"
    )
    store.commit_document(proposals.DOCUMENT, "shop", body, revision)


def test_a_fresh_claim_blocks_and_an_old_one_is_taken_over(root, store):
    propose(root)
    claim(store)
    with pytest.raises(ProposalError, match="being applied"):
        as_(root, "ada").proposals.apply("shop", "p1")
    with pytest.raises(ProposalError, match="being applied"):
        as_(root, "ada").proposals.discard("shop", "p1")
    claim(store, age_seconds=proposals.CLAIM_TTL_SECONDS + 5)  # the process that claimed it is gone
    assert as_(root, "ada").proposals.apply("shop", "p1").status == "applied"


def test_a_claimed_proposal_counts_as_pending_for_the_cap_and_is_never_swept(root, store):
    propose(root)
    claim(store, age_seconds=10 * 24 * 3600)
    assert [p.id for p in as_(root, "vic").proposals.list("shop")] == ["p1"]
    for _ in range(proposals.MAX_PENDING - 1):
        propose(root, operations=[{"op": "rename_diagram", "arguments": {"title": "T"}}])
    with pytest.raises(ProposalError, match="pending"):
        propose(root)


def test_a_project_created_again_under_the_same_id_does_not_inherit_old_proposals(root):
    propose(root)
    ada = as_(root, "ada")
    ada.diagrams.delete("orders")
    ada.projects.delete("shop")
    ada.projects.create("Shop", "shop")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    assert ada.proposals.list("shop") == []
    with pytest.raises(ProposalError, match="Unknown proposal 'p1'"):
        ada.proposals.get("shop", "p1")
    assert propose(root, "ada").id == "p1"  # a fresh counter, not the old project's p2
