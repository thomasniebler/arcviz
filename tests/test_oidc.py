"""OIDC bearer tokens for the MCP endpoint (spec docs/architecture/specs/agent-authentication.md, ADR-0173): the validator
runs against a locally generated key and a fake issuer, no network."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from arcviz.oidc import InvalidToken, OidcConfig, OidcValidator, protected_resource_metadata

ISSUER = "https://login.example.org/tenant/v2.0"
AUDIENCE = "api://arcviz"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())


def jwk(key: Any, kid: str) -> dict[str, Any]:
    data = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
        if isinstance(key, rsa.RSAPrivateKey)
        else jwt.algorithms.ECAlgorithm.to_jwk(key.public_key())
    )
    return {**data, "kid": kid, "use": "sig"}


class Issuer:
    """A fake issuer: serves the discovery document and the key set, counts the fetches."""

    def __init__(self, keys: dict[str, Any] | None = None):
        self.keys = keys if keys is not None else {"k1": KEY, "ec1": EC_KEY}
        self.fetched: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.fetched.append(url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return {"issuer": ISSUER, "jwks_uri": "https://login.example.org/tenant/keys"}
        if url == "https://login.example.org/tenant/keys":
            return {"keys": [jwk(k, kid) for kid, k in self.keys.items()]}
        raise OSError(f"unexpected fetch {url}")


def make_token(claims: dict[str, Any] | None = None, key: Any = KEY, kid: str = "k1", alg: str = "RS256") -> str:
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 600,
        "iat": now,
        "oid": "oid-ada",
        "name": "Ada",
        "scp": "mcp.use",
    }
    return jwt.encode({**base, **(claims or {})}, key, algorithm=alg, headers={"kid": kid})


def validator(issuer: Issuer | None = None, **config: Any) -> OidcValidator:
    return OidcValidator(OidcConfig(ISSUER, AUDIENCE, **config), fetch=issuer or Issuer())


# ---- accepted --------------------------------------------------------------------------------------------------


def test_a_good_token_gives_the_identity_from_the_oid_claim():
    identity = validator().validate(make_token({"preferred_username": "ada@example.org"}))
    assert (identity.id, identity.name, identity.email, identity.email_trusted) == (
        "oid-ada",
        "Ada",
        "ada@example.org",
        False,
    )


def test_sub_is_the_fallback_and_an_email_claim_wins_over_the_username():
    token = make_token({"oid": None, "sub": "sub-1", "email": "a@x.org", "preferred_username": "b@x.org"})
    token = jwt.encode(
        {k: v for k, v in jwt.decode(token, options={"verify_signature": False}).items() if v is not None},
        KEY,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    identity = validator().validate(token)
    assert (identity.id, identity.email, identity.email_trusted) == ("sub-1", "a@x.org", False)


def test_an_ec_key_is_accepted():
    assert validator().validate(make_token(key=EC_KEY, kid="ec1", alg="ES256")).id == "oid-ada"


def test_the_audience_may_be_a_list():
    assert validator().validate(make_token({"aud": ["other", AUDIENCE]})).id == "oid-ada"


def test_a_name_that_is_missing_falls_back_to_the_id():
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60, "oid": "o1"}, KEY, "RS256", {"kid": "k1"}
    )
    assert validator().validate(token).name == "o1"


# ---- refused ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example.org/"},
        {"aud": "api://other"},
        {"exp": int(time.time()) - 120},
        {"nbf": int(time.time()) + 3600},
        {"aud": []},
    ],
)
def test_wrong_claims_are_refused(claims):
    with pytest.raises(InvalidToken):
        validator().validate(make_token(claims))


@pytest.mark.parametrize("missing", ["exp", "iss", "aud"])
def test_required_claims_must_be_there(missing):
    claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60, "oid": "o1"}
    del claims[missing]
    with pytest.raises(InvalidToken):
        validator().validate(jwt.encode(claims, KEY, "RS256", {"kid": "k1"}))


def test_a_token_signed_by_another_key_is_refused():
    with pytest.raises(InvalidToken):
        validator().validate(make_token(key=OTHER_KEY))


def test_alg_none_and_hmac_confusion_are_refused():
    none = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": "k1", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    )
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60, "oid": "o"}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(InvalidToken):
        validator().validate(f"{none}.{body}.")
    hmac_token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60, "oid": "o"}, "secret" * 8, "HS256", {"kid": "k1"}
    )
    with pytest.raises(InvalidToken):
        validator().validate(hmac_token)


@pytest.mark.parametrize("text", ["", "abc", "a.b.c", "a.b", "....", "x" * 5000])
def test_garbage_is_refused(text):
    with pytest.raises(InvalidToken):
        validator().validate(text)


def test_an_unknown_kid_is_refused_after_one_refresh():
    issuer = Issuer()
    v = validator(issuer)
    with pytest.raises(InvalidToken):
        v.validate(make_token(kid="nope"))
    keys_fetches = [u for u in issuer.fetched if u.endswith("/keys")]
    assert len(keys_fetches) <= 2


def test_the_required_scope():
    v = validator(required_scope="mcp.use")
    assert v.validate(make_token({"scp": "a mcp.use b"})).id == "oid-ada"
    assert v.validate(make_token({"scp": None, "scope": "mcp.use"})).id == "oid-ada"
    with pytest.raises(InvalidToken):
        v.validate(make_token({"scp": "other"}))
    with pytest.raises(InvalidToken):
        v.validate(make_token({"scp": None}))


def test_an_empty_identifier_is_refused():
    with pytest.raises(InvalidToken):
        validator().validate(make_token({"oid": "", "sub": ""}))


# ---- keys and discovery ----------------------------------------------------------------------------------------


def test_the_key_set_is_cached_and_a_rotated_key_is_found():
    issuer = Issuer({"k1": KEY})
    clock = {"now": 1000.0}
    v = OidcValidator(OidcConfig(ISSUER, AUDIENCE), fetch=issuer, clock=lambda: clock["now"])
    v.validate(make_token())
    v.validate(make_token())
    assert sum(u.endswith("/keys") for u in issuer.fetched) == 1
    issuer.keys["k2"] = OTHER_KEY  # the issuer rotates
    token = make_token(key=OTHER_KEY, kid="k2")
    with pytest.raises(InvalidToken):  # refreshed at most once a minute
        v.validate(token)
    clock["now"] += 61
    assert v.validate(token).id == "oid-ada"


def test_a_discovery_document_for_another_issuer_is_refused():
    def fetch(url: str) -> dict[str, Any]:
        if url.endswith("openid-configuration"):
            return {"issuer": "https://evil.example.org", "jwks_uri": "https://evil.example.org/keys"}
        raise AssertionError("must not fetch keys of a foreign issuer")

    with pytest.raises(InvalidToken):
        OidcValidator(OidcConfig(ISSUER, AUDIENCE), fetch=fetch).validate(make_token())


def test_an_unreachable_issuer_refuses_instead_of_raising():
    def down(url: str) -> dict[str, Any]:
        raise OSError("down")

    with pytest.raises(InvalidToken):
        OidcValidator(OidcConfig(ISSUER, AUDIENCE), fetch=down).validate(make_token())


# ---- metadata --------------------------------------------------------------------------------------------------


def test_protected_resource_metadata():
    config = OidcConfig(ISSUER, AUDIENCE, scopes=("mcp.use", "offline_access"))
    assert protected_resource_metadata("https://arc.example.org", "/mcp", config) == {
        "resource": "https://arc.example.org/mcp",
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp.use", "offline_access"],
    }
    assert "scopes_supported" not in protected_resource_metadata(
        "https://arc.example.org/", "/mcp", OidcConfig(ISSUER, AUDIENCE)
    )
