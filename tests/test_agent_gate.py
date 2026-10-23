"""How `/mcp` authenticates, per mode and credential (spec docs/architecture/specs/agent-authentication.md, ADR-0173):
the middleware in front of a stub app that reports what it was given."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import auth
from arcviz.agentauth import AgentAuthConfig, AgentGate
from arcviz.oidc import InvalidToken, OidcConfig
from arcviz.services import AgentCredential
from arcviz.workspaces import Identity

ADMIN = Identity("admin", "Administrator")
ADA = Identity("ada", "Ada", "ada@example.org")
PAT = "avp_" + "a" * 32 + "_" + "b" * 8 + "_" + "c" * 40
JWT_LIKE = "aaa.bbb.ccc"
SHARED = "shared-secret-0123456789abcdef"
ISSUER = "https://login.example.org/tenant/v2.0"


class FakeOidc:
    config = OidcConfig(ISSUER, "api://arcviz", scopes=("mcp.use",))

    def validate(self, token: str) -> Identity:
        if token == JWT_LIKE:
            return ADA
        raise InvalidToken


def lookup(token: str) -> AgentCredential | None:
    return AgentCredential(ADA, "read", "pat") if token == PAT else None


async def stub(scope: Any, receive: Any, send: Any) -> None:
    state = scope.get("state", {})
    identity = state.get("arcviz_identity")
    body = json.dumps(
        {
            "path": scope["path"],
            "identity": identity.id if identity else None,
            "scope": state.get("arcviz_agent_scope"),
            "kind": state.get("arcviz_agent_kind"),
            "user": state.get("arcviz_user"),
        }
    ).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


def client(mode: str, oidc: bool = False, public_url: str | None = None, path: str = "/mcp") -> TestClient:
    config = auth.AuthConfig(
        mode=mode,
        token=SHARED if mode == "token" else None,
        proxy_header="X-User" if mode == "proxy" else None,
        proxy_name_header="X-Name" if mode == "proxy" else None,
        admin=ADMIN if mode != "proxy" else None,
    )
    gate = AgentGate(
        AgentAuthConfig(enabled=True, path=path, oidc=FakeOidc.config if oidc else None, public_url=public_url),
        tokens=lookup,
        oidc=FakeOidc() if oidc else None,
        admin=config.admin,
        auth=config,
    )
    return TestClient(auth.AuthMiddleware(stub, config, gate), follow_redirects=False)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def post(c: TestClient, headers: dict[str, str] | None = None, path: str = "/mcp"):
    return c.post(path, headers=headers or {}, json={})


# ---- off ---------------------------------------------------------------------------------------------------------


def test_off_mode_needs_no_credential_and_acts_as_the_admin_with_write():
    r = post(client("off"))
    assert r.status_code == 200
    assert (r.json()["identity"], r.json()["scope"], r.json()["kind"]) == ("admin", "write", "open")


def test_off_mode_still_honours_a_token_and_refuses_a_bad_one():
    c = client("off", oidc=True)
    assert post(c, bearer(PAT)).json()["identity"] == "ada"
    assert post(c, bearer(JWT_LIKE)).json()["kind"] == "oidc"
    assert post(c, bearer("avp_" + "0" * 32 + "_" + "0" * 8 + "_" + "0" * 40)).status_code == 401


# ---- token mode --------------------------------------------------------------------------------------------------


def test_token_mode_accepts_the_shared_secret_as_admin_and_a_pat_as_its_owner():
    c = client("token")
    assert post(c).status_code == 401
    shared = post(c, bearer(SHARED)).json()
    assert (shared["identity"], shared["scope"], shared["kind"]) == ("admin", "write", "shared")
    pat = post(c, bearer(PAT)).json()
    assert (pat["identity"], pat["scope"], pat["kind"], pat["user"]) == ("ada", "read", "pat", "ada")
    assert post(c, bearer("wrong")).status_code == 401


def test_token_mode_ignores_the_session_cookie_on_the_mcp_path():
    c = client("token")
    cookie = auth.session_value(SHARED, int(__import__("time").time()))
    assert post(c, {"Cookie": f"{auth.SESSION_COOKIE}={cookie}"}).status_code == 401
    assert (
        c.get("/api/x", headers={"Cookie": f"{auth.SESSION_COOKIE}={cookie}"}).status_code == 200
    )  # elsewhere it counts


# ---- proxy mode --------------------------------------------------------------------------------------------------


def test_proxy_mode_ignores_forged_identity_headers_on_the_mcp_path():
    c = client("proxy")
    assert post(c, {"X-User": "admin"}).status_code == 401
    assert post(c, {"X-User": "admin", **bearer(PAT)}).json()["identity"] == "ada"  # the credential, not the header
    assert c.get("/api/x", headers={"X-User": "admin"}).json()["identity"] == "admin"  # elsewhere the proxy decides


def test_proxy_mode_accepts_a_pat_and_an_oidc_token():
    c = client("proxy", oidc=True)
    assert post(c, bearer(PAT)).json()["kind"] == "pat"
    r = post(c, bearer(JWT_LIKE)).json()
    assert (r["identity"], r["scope"], r["kind"], r["user"]) == ("ada", "write", "oidc", "ada")


def test_the_shared_secret_means_nothing_in_proxy_mode():
    assert post(client("proxy"), bearer(SHARED)).status_code == 401


# ---- the answer to a missing or bad credential ---------------------------------------------------------------------


def test_the_challenge_without_oidc():
    r = post(client("token"))
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Bearer realm="arcviz"'
    assert r.json() == {"detail": "Authentication required."}


def test_the_challenge_with_oidc_names_the_metadata_and_a_failed_token():
    c = client("proxy", oidc=True)
    r = post(c)
    assert r.headers["www-authenticate"] == (
        'Bearer realm="arcviz", resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"'
    )
    bad = post(c, bearer("x.y.z"))
    assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]
    assert "x.y.z" not in bad.text


def test_the_metadata_url_follows_a_configured_public_url_and_forwarded_headers():
    r = post(client("proxy", oidc=True, public_url="https://arc.example.org"))
    assert (
        'resource_metadata="https://arc.example.org/.well-known/oauth-protected-resource/mcp"'
        in r.headers["www-authenticate"]
    )
    r = post(client("proxy", oidc=True), {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "arc.example.org"})
    assert 'resource_metadata="https://arc.example.org/.well-known/' in r.headers["www-authenticate"]
    r = post(client("proxy", oidc=True), {"X-Forwarded-Host": 'x"; evil="1'})
    assert "evil" not in r.headers["www-authenticate"]


# ---- the metadata ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"])
def test_the_metadata_is_public_whatever_the_mode(path):
    for mode in ("off", "token", "proxy"):
        r = client(mode, oidc=True, public_url="https://arc.example.org").get(path)
        assert r.status_code == 200
        assert r.json() == {
            "resource": "https://arc.example.org/mcp",
            "authorization_servers": [ISSUER],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp.use"],
        }


def test_there_is_no_public_metadata_without_oidc():
    assert client("proxy").get("/.well-known/oauth-protected-resource").status_code == 401  # an ordinary path then


# ---- elsewhere ---------------------------------------------------------------------------------------------------


def test_credentials_of_agents_do_not_work_on_other_paths():
    for mode in ("token", "proxy"):
        c = client(mode, oidc=True)
        assert c.get("/api/diagrams", headers=bearer(PAT)).status_code == 401
        assert c.get("/api/diagrams", headers=bearer(JWT_LIKE)).status_code == 401
    off = client("off")
    assert off.get("/api/diagrams", headers=bearer(PAT)).json()["identity"] == "admin"  # off: the admin, not the owner


def test_the_mcp_path_is_configurable_and_exact():
    c = client("token", path="/agents/mcp")
    assert post(c, path="/agents/mcp").status_code == 401
    assert c.post("/mcp", json={}).status_code == 401  # elsewhere: the ordinary rules, no credential either
    assert c.post("/agents/mcp/", json={}).status_code == 401
    assert post(c, bearer(SHARED), path="/agents/mcp").json()["path"] == "/agents/mcp"


# ---- configuration -----------------------------------------------------------------------------------------------


def config(**env: str) -> AgentAuthConfig:
    return AgentAuthConfig.from_env(env)


def test_the_endpoint_is_off_by_default():
    c = config()
    assert (c.enabled, c.path, c.oidc, c.public_url) == (False, "/mcp", None, None)


def test_a_full_configuration():
    c = config(
        ARCVIZ_MCP="1",
        ARCVIZ_MCP_PATH="/agents/mcp",
        ARCVIZ_PUBLIC_URL="https://arc.example.org/",
        ARCVIZ_MCP_OAUTH_ISSUER=ISSUER,
        ARCVIZ_MCP_OAUTH_AUDIENCE="api://arcviz",
        ARCVIZ_MCP_OAUTH_REQUIRED_SCOPE="mcp.use",
        ARCVIZ_MCP_OAUTH_SCOPES="mcp.use, offline_access",
    )
    assert c.enabled and c.path == "/agents/mcp" and c.public_url == "https://arc.example.org"
    assert c.oidc == OidcConfig(ISSUER, "api://arcviz", "mcp.use", ("mcp.use", "offline_access"))


@pytest.mark.parametrize(
    ("env", "variable"),
    [
        ({"ARCVIZ_MCP_PATH": "mcp"}, "ARCVIZ_MCP_PATH"),
        ({"ARCVIZ_MCP_PATH": "/mcp/"}, "ARCVIZ_MCP_PATH"),
        ({"ARCVIZ_MCP_PATH": "/api/mcp"}, "ARCVIZ_MCP_PATH"),
        ({"ARCVIZ_MCP_PATH": "/docs"}, "ARCVIZ_MCP_PATH"),
        ({"ARCVIZ_MCP_PATH": "/.well-known/x"}, "ARCVIZ_MCP_PATH"),
        ({"ARCVIZ_MCP_PATH": "/a b"}, "ARCVIZ_MCP_PATH"),
        ({"ARCVIZ_PUBLIC_URL": "arc.example.org"}, "ARCVIZ_PUBLIC_URL"),
        ({"ARCVIZ_PUBLIC_URL": "https://arc.example.org/path"}, "ARCVIZ_PUBLIC_URL"),
        ({"ARCVIZ_MCP": "maybe"}, "ARCVIZ_MCP"),
        ({"ARCVIZ_MCP": "1", "ARCVIZ_MCP_OAUTH_ISSUER": ISSUER}, "ARCVIZ_MCP_OAUTH_AUDIENCE"),
        ({"ARCVIZ_MCP": "1", "ARCVIZ_MCP_OAUTH_AUDIENCE": "x"}, "ARCVIZ_MCP_OAUTH_ISSUER"),
        ({"ARCVIZ_MCP_OAUTH_ISSUER": ISSUER, "ARCVIZ_MCP_OAUTH_AUDIENCE": "x"}, "ARCVIZ_MCP=1"),
        (
            {"ARCVIZ_MCP": "1", "ARCVIZ_MCP_OAUTH_ISSUER": "http://evil.example.org", "ARCVIZ_MCP_OAUTH_AUDIENCE": "x"},
            "https://",
        ),
    ],
)
def test_a_malformed_configuration_names_the_variable(env, variable):
    with pytest.raises(ValueError, match=variable):
        config(**env)


def test_a_local_issuer_may_use_http():
    c = config(ARCVIZ_MCP="1", ARCVIZ_MCP_OAUTH_ISSUER="http://localhost:8080/realms/x", ARCVIZ_MCP_OAUTH_AUDIENCE="a")
    assert c.oidc is not None
