"""Suggesting signed-in users in the share dialog (spec: docs/architecture/specs/personal-workspaces.md, ADR-0165)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz.server import create_app
from arcviz.services import UserService
from arcviz.services import users as users_module
from arcviz.workspaces import Identity

PEOPLE = [
    Identity("ada", "Ada Lovelace", "ada@example.org", email_trusted=True),
    Identity("alan", "Alan Turing", "turing@example.org", email_trusted=True),
    Identity("grace", "Grace Hopper", "grace@navy.example", email_trusted=False),  # a claim: never searched
    Identity("bad", "Mallory", "ada@lovelace.example", email_trusted=False),
]


@pytest.fixture
def directory(store) -> UserService:
    service = UserService(store)
    for person in PEOPLE:
        service.seen(person)
    return service


def ids(records) -> list[str]:
    return [r.id for r in records]


def test_two_characters_or_more(directory):
    assert directory.suggest("") == [] and directory.suggest("a") == []
    assert ids(directory.suggest("al")) == ["alan", "bad"]  # a prefix match first ("Alan"), then "Mallory"


def test_name_id_and_trusted_email_are_searched_untrusted_email_never(directory):
    assert ids(directory.suggest("TURING")) == ["alan"]
    assert ids(directory.suggest("example.org")) == ["ada", "alan"]
    assert ids(directory.suggest("navy")) == []  # an untrusted address is display data only (ADR-0091)
    assert ids(directory.suggest("lovelace")) == ["ada"]  # Mallory's claimed address does not match


def test_the_caller_and_the_limit(directory):
    assert ids(directory.suggest("a", exclude="ada")) == []
    assert "ada" not in ids(directory.suggest("ada", exclude="ada"))
    assert len(directory.suggest("a", limit=1)) == 0  # still two characters
    assert len(directory.suggest("ex", limit=1)) == 1


def test_the_listing_is_cached_per_process(directory, store, monkeypatch):
    directory.suggest("al")
    calls = []
    original = store.list_documents
    monkeypatch.setattr(store, "list_documents", lambda kind: calls.append(kind) or original(kind))
    directory.suggest("ad")
    directory.suggest("gr")
    assert calls == []  # within DIRECTORY_TTL: no store listing per keystroke
    monkeypatch.setattr(users_module, "DIRECTORY_TTL", 0.0)
    directory.suggest("gr")
    assert calls == ["user"]


def test_the_route_hides_untrusted_addresses_and_the_caller(store, directory):
    client = TestClient(create_app(store=store))
    client.get("/api/me")  # the admin signs in
    users = client.get("/api/users", params={"q": "a"}).json()["users"]
    assert users == []
    users = client.get("/api/users", params={"q": "gr"}).json()["users"]
    assert users == [{"id": "grace", "name": "Grace Hopper", "email": None}]
    users = client.get("/api/users", params={"q": "admin"}).json()["users"]
    assert all(u["id"] != "admin" for u in users)
