"""Personal access tokens (spec docs/architecture/specs/agent-authentication.md, ADR-0173)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from arcviz import tokens
from arcviz.ports import ConflictError
from arcviz.services import ServiceRoot
from arcviz.services.tokens import TokenError
from arcviz.workspaces import Identity, user_key

ADA = Identity("ada", "Ada Lovelace", "ada@example.org", True)
ED = Identity("ed", "Ed")


@pytest.fixture
def root(store):
    return ServiceRoot(store)


def mine(root: ServiceRoot, who: Identity = ADA):
    return root.for_actor(who).tokens


# ---- the format ------------------------------------------------------------------------------------------------


def test_a_generated_token_has_the_documented_shape_and_parses_back():
    token = tokens.generate("ada")
    assert re.fullmatch(r"avp_[0-9a-f]{32}_[0-9a-f]{8}_[0-9a-f]{40}", token.value)
    assert tokens.parse(token.value) == (token.user_key, token.id, token.secret)
    assert token.user_key == user_key("ada")
    assert tokens.digest(token.secret) == token.hash and len(token.hash) == 64
    assert tokens.generate("ada").value != token.value


@pytest.mark.parametrize(
    "text",
    [
        "",
        "avp_",
        "ghp_" + "a" * 36,
        "avp_" + "a" * 32 + "_" + "b" * 8 + "_" + "c" * 39,  # secret too short
        "avp_" + "A" * 32 + "_" + "b" * 8 + "_" + "c" * 40,  # upper case
        "avp_" + "a" * 32 + "_" + "b" * 8 + "_" + "c" * 40 + "x",
        "x.y.z",
    ],
)
def test_other_strings_are_no_tokens(text):
    assert tokens.parse(text) is None
    assert tokens.looks_like_token(text) is False


def test_looks_like_token():
    assert tokens.looks_like_token(tokens.generate("ada").value)


# ---- create, list, revoke --------------------------------------------------------------------------------------


def test_create_returns_the_secret_once_and_stores_only_its_hash(root, store):
    created = mine(root).create("laptop", "write", 30)
    assert created.token.startswith("avp_")
    [record] = mine(root).list()
    assert (record.id, record.label, record.scope) == (created.record.id, "laptop", "write")
    body, _ = store.read_document(tokens.DOCUMENT, user_key("ada"))
    assert created.token not in str(body) and tokens.parse(created.token)[2] not in str(body)
    assert body["tokens"][0]["hash"] == tokens.digest(tokens.parse(created.token)[2])
    assert "hash" not in record.public()
    expires = datetime.fromisoformat(record.expires_at)
    assert timedelta(days=29) < expires - datetime.now(UTC) <= timedelta(days=30, minutes=1)


def test_defaults(root):
    record = mine(root).create("x").record
    assert record.scope == "read"
    assert timedelta(days=89) < datetime.fromisoformat(record.expires_at) - datetime.now(UTC) <= timedelta(days=90)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("", "read", 30), "label"),
        (("  ", "read", 30), "label"),
        (("x" * 65, "read", 30), "label"),
        (("x", "admin", 30), "scope"),
        (("x", "read", 0), "days"),
        (("x", "read", 366), "days"),
        (("bad\x00label", "read", 30), "label"),
    ],
)
def test_invalid_input_is_refused(root, args, message):
    with pytest.raises(TokenError, match=message):
        mine(root).create(*args)
    assert mine(root).list() == []


def test_at_most_max_tokens_per_user(root):
    for n in range(tokens.MAX_TOKENS):
        mine(root).create(f"t{n}")
    with pytest.raises(TokenError, match=str(tokens.MAX_TOKENS)):
        mine(root).create("one more")
    first = mine(root).list()[0]
    mine(root).revoke(first.id)
    mine(root).create("now it fits")


def test_everybody_sees_only_their_own_tokens(root):
    mine(root).create("ada's")
    assert mine(root, ED).list() == []
    created = mine(root, ED).create("ed's")
    with pytest.raises(TokenError, match="Unknown token"):
        mine(root).revoke(created.record.id)
    assert [t.label for t in mine(root).list()] == ["ada's"]


def test_revoke(root):
    a, b = mine(root).create("a"), mine(root).create("b")
    mine(root).revoke(a.record.id)
    assert [t.label for t in mine(root).list()] == ["b"]
    assert root.authenticate_token(a.token) is None
    assert root.authenticate_token(b.token) is not None
    with pytest.raises(TokenError, match="Unknown token"):
        mine(root).revoke(a.record.id)


def test_a_concurrent_write_is_retried(root, store, monkeypatch):
    real, calls = store.commit_document, {"n": 0}

    def racing(kind, key, body, expected):
        if calls["n"] == 0:
            calls["n"] += 1
            raise ConflictError("lost the race")
        return real(kind, key, body, expected)

    mine(root).create("first")
    monkeypatch.setattr(store, "commit_document", racing)
    mine(root).create("second")
    assert [t.label for t in mine(root).list()] == ["first", "second"]


# ---- authenticate ----------------------------------------------------------------------------------------------


def test_a_token_authenticates_as_its_owner_with_its_scope(root):
    created = mine(root).create("laptop", "write")
    credential = root.authenticate_token(created.token)
    assert credential is not None
    assert (credential.identity.id, credential.identity.name, credential.scope, credential.kind) == (
        "ada",
        "Ada Lovelace",
        "write",
        "pat",
    )
    assert credential.identity.email == "ada@example.org" and credential.identity.email_trusted is True


def test_every_failure_looks_the_same(root):
    created = mine(root).create("laptop")
    user, token_id, secret = tokens.parse(created.token)
    wrong_secret = f"avp_{user}_{token_id}_{'0' * 40}"
    wrong_id = f"avp_{user}_{'0' * 8}_{secret}"
    wrong_user = f"avp_{'0' * 32}_{token_id}_{secret}"
    for bad in (wrong_secret, wrong_id, wrong_user, "avp_nonsense", "", "Bearer x"):
        assert root.authenticate_token(bad) is None


def test_an_expired_token_does_not_authenticate_and_is_dropped_on_the_next_write(root, store):
    created = mine(root).create("old", "read", 1)
    body, revision = store.read_document(tokens.DOCUMENT, user_key("ada"))
    body["tokens"][0]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.commit_document(tokens.DOCUMENT, user_key("ada"), body, revision)
    assert root.authenticate_token(created.token) is None
    mine(root).create("new")
    assert [t.label for t in mine(root).list()] == ["new"]


def test_deleting_the_document_revokes_everything(root, store):
    created = mine(root).create("laptop")
    body, revision = store.read_document(tokens.DOCUMENT, user_key("ada"))
    store.delete_document(tokens.DOCUMENT, user_key("ada"), revision)
    assert root.authenticate_token(created.token) is None


def test_a_damaged_document_authenticates_nobody_and_is_not_overwritten(root, store):
    created = mine(root).create("laptop")
    body, revision = store.read_document(tokens.DOCUMENT, user_key("ada"))
    body["tokens"] = "damaged"
    store.commit_document(tokens.DOCUMENT, user_key("ada"), body, revision)
    assert root.authenticate_token(created.token) is None
    with pytest.raises(Exception, match="damaged"):
        mine(root).create("x")


def test_expired_tokens_do_not_count_against_the_cap(root, store):
    for n in range(tokens.MAX_TOKENS):
        mine(root).create(f"t{n}", "read", 1)
    body, revision = store.read_document(tokens.DOCUMENT, user_key("ada"))
    for item in body["tokens"]:
        item["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.commit_document(tokens.DOCUMENT, user_key("ada"), body, revision)
    assert mine(root).list() == []  # the dialog shows none ...
    mine(root).create("fresh")  # ... so creating one must work
    assert [t.label for t in mine(root).list()] == ["fresh"]
