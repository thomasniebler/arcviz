"""Applying and discarding project proposals over HTTP (spec: architecture-objects-over-mcp.md, ADR-0187)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz.server import create_app
from arcviz.services import ServiceRoot
from arcviz.workspaces import Identity

ITEMS = [
    {"key": "a", "op": "add_driver", "arguments": {"title": "Antwortzeit"}},
    {"key": "b", "op": "add_scenario", "arguments": {"title": "Lastspitze", "driver": "@a"}},
    {"key": "c", "op": "add_driver", "arguments": {"title": "Kosten"}},
]


@pytest.fixture
def client(store):
    c = TestClient(create_app(store=store))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    admin = ServiceRoot(store).for_actor(Identity("admin", "Admin", None))  # type: ignore[arg-type]
    admin.proposals.create_project("shop", ITEMS, "Test", client="mcp")
    return c


def test_the_list_and_the_detail_show_items(client):
    listed = client.get("/api/projects/shop/proposals").json()["proposals"]
    assert [(p["id"], p["kind"], len(p["items"])) for p in listed] == [("p1", "project", 3)]
    one = client.get("/api/projects/shop/proposals/p1").json()["proposal"]
    assert [i["key"] for i in one["items"]] == ["a", "b", "c"] and one["items"][1]["arguments"]["driver"] == "@a"


def test_apply_a_selection_then_the_rest(client):
    first = client.post("/api/projects/shop/proposals/p1/apply", json={"accept": ["c"]})
    assert first.status_code == 200
    body = first.json()["proposal"]
    assert body["status"] == "pending" and [i["state"] for i in body["items"]] == ["pending", "pending", "applied"]
    assert [d["title"] for d in client.get("/api/projects/shop/drivers").json()["drivers"]] == ["Kosten"]
    rest = client.post("/api/projects/shop/proposals/p1/apply")  # no body: every pending item
    assert rest.status_code == 200 and rest.json()["proposal"]["status"] == "applied"
    assert client.get("/api/projects/shop/scenarios").json()["scenarios"][0]["driver"] == "d2"


def test_a_dependency_without_its_driver_is_a_400(client):
    r = client.post("/api/projects/shop/proposals/p1/apply", json={"accept": ["b"]})
    assert r.status_code == 400 and "Item 'b' needs 'a'" in r.json()["detail"]
    assert client.get("/api/projects/shop/proposals/p1").json()["proposal"]["status"] == "pending"


def test_discard_items(client):
    r = client.post("/api/projects/shop/proposals/p1/discard", json={"keys": ["a"]})
    states = [i["state"] for i in r.json()["proposal"]["items"]]
    assert r.status_code == 200 and states == ["discarded", "discarded", "pending"]  # b needed a
    assert client.post("/api/projects/shop/proposals/p1/discard", json={"keys": ["zz"]}).status_code == 400
    done = client.post("/api/projects/shop/proposals/p1/discard")
    assert done.json()["proposal"]["status"] == "discarded"
    assert client.get("/api/projects/shop/drivers").json()["drivers"] == []


def test_a_decided_proposal_keeps_no_arguments(client):
    client.post("/api/projects/shop/proposals/p1/apply")
    item = client.get("/api/projects/shop/proposals/p1").json()["proposal"]["items"][0]
    assert item["arguments"] == {} and item["state"] == "applied" and item["created"] == "d1"
