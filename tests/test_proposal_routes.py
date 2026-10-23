"""The HTTP side of stored proposals (spec docs/architecture/specs/agent-access.md, ADR-0172)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz.server import create_app
from arcviz.services import ServiceRoot
from arcviz.workspaces import Identity

ADMIN = Identity("admin", "Administrator")
OPS = [{"op": "add_node", "arguments": {"name": "Cache", "kind": "service", "id": "cache"}}]


@pytest.fixture
def client(store):
    c = TestClient(create_app(store=store))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    assert c.post("/api/diagrams", json={"title": "Orders", "id": "orders", "project": "shop"}).status_code == 201
    return c


def propose(store, operations=OPS):
    root = ServiceRoot(store)
    return root.for_actor(ADMIN).proposals.create("shop", "orders", operations, "Weil")


def test_list_get_apply(client, store):
    assert client.get("/api/projects/shop/proposals").json() == {"project": "shop", "proposals": []}
    propose(store)
    listed = client.get("/api/projects/shop/proposals").json()["proposals"]
    assert [(p["id"], p["status"], p["author"], p["diagram"]) for p in listed] == [("p1", "pending", "admin", "orders")]
    assert listed[0]["diff"]["nodes"]["added"][0]["id"] == "cache"
    assert client.get("/api/projects/shop/proposals?status=applied").json()["proposals"] == []
    assert client.get("/api/projects/shop/proposals?status=bogus").status_code == 400
    one = client.get("/api/projects/shop/proposals/p1").json()["proposal"]
    assert one["rationale"] == "Weil"
    applied = client.post("/api/projects/shop/proposals/p1/apply")
    assert applied.status_code == 200
    assert applied.json()["proposal"]["status"] == "applied" and applied.json()["proposal"]["version"] == 2
    assert "cache" in client.get("/api/diagrams/orders").json()["diagram"]["nodes"]
    assert client.post("/api/projects/shop/proposals/p1/apply").status_code == 400  # already applied


def test_a_stale_proposal_answers_409_and_can_be_discarded(client, store):
    propose(store)
    assert (
        client.post("/api/diagrams/orders/ops", json={"op": "rename_diagram", "args": {"title": "Neu"}}).status_code
        == 200
    )
    r = client.post("/api/projects/shop/proposals/p1/apply")
    assert r.status_code == 409
    assert client.get("/api/projects/shop/proposals/p1").json()["proposal"]["status"] == "stale"
    d = client.post("/api/projects/shop/proposals/p1/discard")
    assert d.status_code == 200 and d.json()["proposal"]["status"] == "discarded"


def test_unknown_things(client):
    assert client.get("/api/projects/shop/proposals/p9").status_code == 400
    assert client.post("/api/projects/shop/proposals/p9/apply").status_code == 400
    assert client.get("/api/projects/nope/proposals").status_code == 404
    assert client.post("/api/projects/nope/proposals/p1/discard").status_code == 404


def test_the_retention_is_configurable(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_PROPOSAL_RETENTION_DAYS", "zero")
    with pytest.raises(ValueError, match="ARCVIZ_PROPOSAL_RETENTION_DAYS"):
        create_app(store=store)
