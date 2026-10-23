"""The token routes (spec docs/architecture/specs/agent-authentication.md, ADR-0173)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import tokens
from arcviz.server import create_app


@pytest.fixture
def client(store):
    return TestClient(create_app(store=store))


def test_create_list_revoke(client):
    assert client.get("/api/tokens").json()["tokens"] == []
    r = client.post("/api/tokens", json={"label": "Laptop", "scope": "write", "expires_in_days": 7})
    assert r.status_code == 201
    secret = r.json()["token"]
    assert tokens.looks_like_token(secret)
    record = r.json()["record"]
    assert (record["label"], record["scope"]) == ("Laptop", "write") and "hash" not in record
    listed = client.get("/api/tokens").json()
    assert [t["id"] for t in listed["tokens"]] == [record["id"]]
    assert secret not in client.get("/api/tokens").text  # the value is shown once
    assert listed["scopes"] == ["read", "write"] and listed["max_tokens"] == tokens.MAX_TOKENS
    assert client.delete(f"/api/tokens/{record['id']}").json() == {"revoked": record["id"]}
    assert client.get("/api/tokens").json()["tokens"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"label": ""},
        {"label": "x", "scope": "root"},
        {"label": "x", "expires_in_days": 0},
        {"label": "x", "expires_in_days": 999},
    ],
)
def test_invalid_requests_answer_400(client, body):
    assert client.post("/api/tokens", json=body).status_code == 400


def test_unknown_fields_and_ids(client):
    assert client.post("/api/tokens", json={"label": "x", "admin": True}).status_code == 422
    assert client.delete("/api/tokens/nope").status_code == 400
