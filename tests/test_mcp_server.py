"""The MCP endpoint end to end (spec docs/architecture/specs/agent-access.md and agent-authentication.md, ADR-0170, ADR-0173):
the real app with ARCVIZ_MCP=1 and JSON-RPC over HTTP, per authentication mode."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz.server import create_app
from arcviz.services import ServiceRoot
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity
from tests.conftest import ScriptedProvider

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
ADA = Identity("ada", "Ada", "ada@example.org")
VIC = Identity("vic", "Vic", "vic@example.org")
SHARED = "shared-secret-0123456789abcdef"
ENV = (
    "ARCVIZ_AUTH",
    "ARCVIZ_AUTH_TOKEN",
    "ARCVIZ_AUTH_PROXY_PRESET",
    "ARCVIZ_AUTH_PROXY_HEADER",
    "ARCVIZ_MCP",
    "ARCVIZ_MCP_PATH",
    "ARCVIZ_PUBLIC_URL",
    "ARCVIZ_MCP_OAUTH_ISSUER",
    "ARCVIZ_MCP_OAUTH_AUDIENCE",
    "ARCVIZ_ADMIN_USER",
)


def make_app(store, monkeypatch, **env: str):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in {"ARCVIZ_MCP": "1", **env}.items():
        monkeypatch.setenv(name, value)
    return create_app(store=store, provider=ScriptedProvider([]))


def rpc(
    client: TestClient,
    method: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    id: int = 1,
):
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", headers={**HEADERS, **(headers or {})}, json=body)


def call(client: TestClient, tool: str, arguments: dict[str, Any] | None = None, headers: dict[str, str] | None = None):
    response = rpc(client, "tools/call", {"name": tool, "arguments": arguments or {}}, headers)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return result["isError"], (json.loads(text) if not result["isError"] else text)


def shop_listed(projects: dict[str, Any]) -> bool:
    """Whether `shop` is among the listed projects (everybody also has their own default project)."""
    return "shop" in [p["id"] for p in projects["projects"]]


def seed(store) -> None:
    root = ServiceRoot(store)
    root.users.seen(ADA)
    root.users.seen(VIC)
    ada = root.for_actor(ADA)
    ada.projects.create("Shop", "shop")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    ada.models.add_element("shop", "Orders", "component", "Owns orders", element_id="orders")
    ada.models.code_ref_operation("shop", "add_code_ref", {"element": "orders", "path": "src/orders"})


# ---- off mode: the protocol --------------------------------------------------------------------------------------


@pytest.fixture
def client(store, monkeypatch):
    seed_user = Identity("admin", "Administrator")
    root = ServiceRoot(store)
    root.users.seen(seed_user)
    admin = root.for_actor(seed_user)
    admin.projects.create("Shop", "shop")
    admin.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    admin.models.add_element("shop", "Orders", "component", "Owns orders", element_id="orders")
    admin.models.code_ref_operation("shop", "add_code_ref", {"element": "orders", "path": "src/orders"})
    with TestClient(make_app(store, monkeypatch)) as c:
        yield c


def test_the_handshake_and_the_tool_list(client):
    r = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "arcviz" and "get_component" in result["instructions"]
    tools = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert set(tools) == {
        "list_projects",
        "get_architecture_context",
        "list_components",
        "get_component",
        "find_component_by_path",
        "list_findings",
        "list_diagrams",
        "get_diagram",
        "list_operations",
        "list_proposals",
        "get_proposal",
        "add_code_ref",
        "remove_code_ref",
        "propose_change",
        "list_stakeholders",
        "list_drivers",
        "get_driver",
        "list_scenarios",
        "get_scenario",
        "list_decisions",
        "get_decision",
        "list_requirements",
        "get_requirement",
        "get_traceability",
        "add_requirements",
        "update_requirement",
        "suggest_refinement",
        "add_stakeholder",
        "update_stakeholder",
        "propose_architecture_items",
    }
    assert tools["get_component"]["inputSchema"]["required"] == ["project", "component"]
    assert "write token" in tools["propose_change"]["description"]
    assert not {n for n in tools if any(w in n for w in ("apply", "discard", "accept", "delete", "remove_req"))}
    assert "write token" in tools["propose_architecture_items"]["description"]


def test_a_notification_is_accepted(client):
    r = client.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202


def test_tools_answer_with_json_and_refusals_are_tool_errors(client):
    failed, projects = call(client, "list_projects")
    assert not failed and shop_listed(projects)
    failed, component = call(client, "get_component", {"project": "shop", "component": "orders"})
    assert not failed and component["code_links"][0]["path"] == "src/orders"
    failed, owners = call(client, "find_component_by_path", {"project": "shop", "path": "src/orders/api.py"})
    assert not failed and owners["owners"][0]["component"]["id"] == "orders"
    failed, message = call(client, "get_component", {"project": "shop", "component": "nope"})
    assert failed and "Unknown element 'nope'" in message


def test_the_whole_loop_link_propose_see_it_applied(client):
    failed, out = call(
        client, "add_code_ref", {"project": "shop", "component": "orders", "path": "src/orders/db", "symbol": "Repo"}
    )
    assert not failed and len(out["code_links"]) == 2
    ops = [{"op": "add_node", "arguments": {"name": "Cache", "kind": "service", "id": "cache"}}]
    failed, proposed = call(
        client, "propose_change", {"project": "shop", "diagram": "orders", "operations": ops, "rationale": "Hot reads"}
    )
    assert not failed and proposed["proposal"]["status"] == "pending"
    assert client.get("/api/projects/shop/proposals").json()["proposals"][0]["rationale"] == "Hot reads"
    assert "cache" not in client.get("/api/diagrams/orders").json()["diagram"]["nodes"]
    assert client.post("/api/projects/shop/proposals/p1/apply").status_code == 200  # a person, over the UI's route
    failed, seen = call(client, "get_proposal", {"project": "shop", "proposal": "p1"})
    assert not failed and seen["proposal"]["status"] == "applied"


def test_requirements_to_proposed_architecture_to_a_person_applying_it(client):
    failed, added = call(
        client,
        "add_requirements",
        {"project": "shop", "items": [{"raw": "Die Bestellung ist in 200 ms bestätigt", "external_id": "R-1"}]},
    )
    assert not failed and added["added"] == ["req1"]
    failed, again = call(
        client, "add_requirements", {"project": "shop", "items": [{"raw": "Die Bestellung ist in 200 ms bestätigt"}]}
    )
    assert not failed and again["added"] == [] and len(again["skipped_duplicates"]) == 1
    failed, suggested = call(
        client,
        "suggest_refinement",
        {
            "project": "shop",
            "suggestions": [
                {"requirement": "req1", "statement": "Bestellungen werden in 200 ms bestätigt.", "kind": "quality"}
            ],
        },
    )
    assert not failed and suggested["stored"] == ["req1"]
    # nothing is in force until a person accepts: the original and an empty statement are still there
    failed, got = call(client, "get_requirement", {"project": "shop", "requirement": "req1"})
    assert (
        not failed and got["requirement"]["statement"] == "" and got["requirement"]["suggestion"]["kind"] == "quality"
    )
    rev = got["requirement"]["suggestion"]["rev"]
    failed, message = call(
        client, "update_requirement", {"project": "shop", "requirement": "req1", "fields": {"statement": "x"}}
    )
    assert failed and "cannot change statement" in message
    items = [
        {
            "key": "d1",
            "op": "add_driver",
            "arguments": {"title": "Antwortzeit", "importance": "high"},
            "derived_from": ["req1"],
        },
        {
            "key": "q1",
            "op": "add_scenario",
            "arguments": {"title": "Lastspitze", "driver": "@d1"},
            "derived_from": ["req1"],
        },
    ]
    failed, proposed = call(
        client, "propose_architecture_items", {"project": "shop", "items": items, "rationale": "aus req1"}
    )
    assert not failed and proposed["proposal"]["kind"] == "project" and proposed["proposal"]["id"] == "p1"
    assert client.get("/api/projects/shop/drivers").json()["drivers"] == []  # the agent changed nothing binding
    accepted = client.post("/api/projects/shop/requirements/accept", json={"items": [{"id": "req1", "expected": rev}]})
    assert accepted.json()["accepted"] == ["req1"]  # a person, over the UI's route
    applied = client.post("/api/projects/shop/proposals/p1/apply", json={"accept": ["d1"]})
    assert applied.status_code == 200 and applied.json()["proposal"]["status"] == "pending"
    failed, seen = call(client, "get_proposal", {"project": "shop", "proposal": "p1"})
    assert [(i["key"], i["state"], i["created"]) for i in seen["proposal"]["items"]] == [
        ("d1", "applied", "d1"),
        ("q1", "pending", None),
    ]
    failed, drivers = call(client, "list_drivers", {"project": "shop"})
    assert drivers["drivers"][0]["requirements"] == ["req1"]
    failed, trace = call(client, "get_traceability", {"project": "shop", "requirement": "req1"})
    assert trace["coverage"] == "driver" and trace["trace"]["drivers"] == ["d1"]
    failed, context = call(client, "get_architecture_context", {"project": "shop"})
    assert context["requirements"]["total"] == 1 and context["requirements"]["steps"]["derived"] == 1


def test_invalid_arguments_are_a_protocol_error_or_a_tool_error(client):
    r = rpc(client, "tools/call", {"name": "get_component", "arguments": {"project": "shop"}})
    assert r.status_code == 200
    body = r.json()
    assert "error" in body or body["result"]["isError"] is True
    r = rpc(client, "tools/call", {"name": "no_such_tool", "arguments": {}})
    body = r.json()
    assert "error" in body or body["result"]["isError"] is True


def test_only_post_is_served(client):
    for method in ("GET", "DELETE", "PUT"):
        r = client.request(method, "/mcp", headers=HEADERS)
        assert r.status_code == 405 and r.headers["allow"] == "POST"
    assert client.post("/mcp/", headers=HEADERS, json={}).status_code >= 300  # exactly the configured path


def test_another_content_type_is_refused(client):
    r = client.post("/mcp", headers={"Accept": HEADERS["Accept"], "Content-Type": "text/plain"}, content="{}")
    assert r.status_code == 400


def test_the_endpoint_is_off_unless_asked_for(store, monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(store=store, provider=ScriptedProvider([]))) as c:
        assert c.post("/mcp", headers=HEADERS, json={}).status_code in (404, 405)
        assert c.get("/api/config").json()["mcp"] == {"enabled": False, "path": "/mcp", "oauth": False}


def test_the_config_names_the_endpoint(store, monkeypatch):
    with TestClient(make_app(store, monkeypatch, ARCVIZ_MCP_PATH="/agents/mcp")) as c:
        assert c.get("/api/config").json()["mcp"] == {"enabled": True, "path": "/agents/mcp", "oauth": False}
        assert (
            c.post("/agents/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code
            == 200
        )
        assert c.post("/mcp", headers=HEADERS, json={}).status_code in (404, 405)


# ---- credentials, per mode -------------------------------------------------------------------------------------------


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def personal_token(store, who: Identity, scope: str) -> str:
    return ServiceRoot(store).for_actor(who).tokens.create("test", scope).token


def test_proxy_mode_a_token_acts_as_its_owner_and_headers_are_ignored(store, monkeypatch):
    seed(store)
    ada_read = personal_token(store, ADA, "read")
    vic_write = personal_token(store, VIC, "write")
    with TestClient(make_app(store, monkeypatch, ARCVIZ_AUTH="proxy", ARCVIZ_AUTH_PROXY_PRESET="oauth2-proxy")) as c:
        assert rpc(c, "tools/list").status_code == 401
        assert (
            rpc(c, "tools/list", headers={"X-Forwarded-User": "ada"}).status_code == 401
        )  # a forged header is no credential
        failed, listed = call(c, "list_projects", headers=bearer(ada_read))
        assert not failed and shop_listed(listed)
        failed, theirs = call(c, "list_projects", headers=bearer(vic_write))
        assert not failed and not shop_listed(theirs)  # vic sees nothing of ada's
        failed, message = call(
            c, "add_code_ref", {"project": "shop", "component": "orders", "path": "x"}, bearer(ada_read)
        )
        assert failed and "read-only" in message
        failed, message = call(c, "get_component", {"project": "shop", "component": "orders"}, bearer(vic_write))
        assert failed and "not found" in message
        assert c.get("/api/projects", headers=bearer(ada_read)).status_code == 401  # REST refuses the token
        assert c.get("/api/projects", headers={"X-Forwarded-User": "ada"}).status_code == 200  # as before


def test_token_mode_shared_secret_and_personal_token(store, monkeypatch):
    seed(store)
    pat = personal_token(store, ADA, "write")
    with TestClient(make_app(store, monkeypatch, ARCVIZ_AUTH="token", ARCVIZ_AUTH_TOKEN=SHARED)) as c:
        assert rpc(c, "tools/list").status_code == 401
        assert not shop_listed(call(c, "list_projects", headers=bearer(SHARED))[1])  # the admin owns nothing here
        assert shop_listed(call(c, "list_projects", headers=bearer(pat))[1])
        r = rpc(c, "tools/list", headers=bearer("nope"))
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")


def test_a_revoked_token_stops_working_at_once(store, monkeypatch):
    seed(store)
    created = ServiceRoot(store).for_actor(ADA).tokens.create("laptop", "read")
    with TestClient(make_app(store, monkeypatch, ARCVIZ_AUTH="proxy", ARCVIZ_AUTH_PROXY_PRESET="oauth2-proxy")) as c:
        assert rpc(c, "tools/list", headers=bearer(created.token)).status_code == 200
        ServiceRoot(store).for_actor(ADA).tokens.revoke(created.record.id)
        assert rpc(c, "tools/list", headers=bearer(created.token)).status_code == 401


def test_rate_limit_class_for_agents(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_RATE_LIMIT_AGENT", "2")
    with TestClient(make_app(store, monkeypatch)) as c:
        codes = [rpc(c, "tools/list").status_code for _ in range(4)]
        assert codes[:2] == [200, 200] and 429 in codes[2:]


def test_missing_sdk_or_malformed_settings_stop_the_start(store, monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARCVIZ_MCP", "1")
    monkeypatch.setenv("ARCVIZ_MCP_PATH", "nope")
    with pytest.raises(ValueError, match="ARCVIZ_MCP_PATH"):
        create_app(store=store, provider=ScriptedProvider([]))


# ---- OIDC bearer tokens through the real app -----------------------------------------------------------------------


def test_an_oidc_token_acts_as_its_user_and_the_challenge_names_the_metadata(store, monkeypatch):
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer, audience = "https://login.example.org/tenant/v2.0", "api://arcviz"
    jwks = {"keys": [{**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": "k1", "use": "sig"}]}

    def fetch(url: str) -> dict[str, Any]:
        if url.endswith("openid-configuration"):
            return {"issuer": issuer, "jwks_uri": "https://login.example.org/tenant/keys"}
        return jwks

    monkeypatch.setattr("arcviz.oidc.fetch_json", fetch)
    seed(store)
    claims = {"iss": issuer, "aud": audience, "exp": int(time.time()) + 600, "oid": "ada", "name": "Ada"}
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
    env = {
        "ARCVIZ_AUTH": "proxy",
        "ARCVIZ_AUTH_PROXY_PRESET": "oauth2-proxy",
        "ARCVIZ_MCP_OAUTH_ISSUER": issuer,
        "ARCVIZ_MCP_OAUTH_AUDIENCE": audience,
        "ARCVIZ_PUBLIC_URL": "https://arc.example.org",
    }
    with TestClient(make_app(store, monkeypatch, **env)) as c:
        failed, listed = call(c, "list_projects", headers=bearer(token))
        assert not failed and shop_listed(listed)  # `oid` ada is the same user as the browser's: same projects
        denied = rpc(c, "tools/list")
        assert denied.status_code == 401
        assert (
            'resource_metadata="https://arc.example.org/.well-known/oauth-protected-resource/mcp"'
            in denied.headers["www-authenticate"]
        )
        meta = c.get("/.well-known/oauth-protected-resource/mcp")
        assert meta.status_code == 200 and meta.json()["authorization_servers"] == [issuer]
        assert meta.json()["resource"] == "https://arc.example.org/mcp"
        assert c.get("/api/config", headers={"X-Forwarded-User": "ada"}).json()["mcp"]["oauth"] is True
        bad = jwt.encode({**claims, "aud": "api://other"}, key, algorithm="RS256", headers={"kid": "k1"})
        assert rpc(c, "tools/list", headers=bearer(bad)).status_code == 401


def test_a_missing_sdk_stops_the_start_with_the_install_hint(store, monkeypatch):
    import sys

    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARCVIZ_MCP", "1")
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    with pytest.raises(ValueError, match=r"arcviz-agent\[mcp\]"):
        create_app(store=store, provider=ScriptedProvider([]))


def test_oidc_without_pyjwt_stops_the_start_with_the_install_hint(store, monkeypatch):
    import sys

    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARCVIZ_MCP", "1")
    monkeypatch.setenv("ARCVIZ_MCP_OAUTH_ISSUER", "https://login.example.org/t/v2.0")
    monkeypatch.setenv("ARCVIZ_MCP_OAUTH_AUDIENCE", "api://x")
    monkeypatch.setitem(sys.modules, "jwt", None)
    with pytest.raises(ValueError, match="PyJWT"):
        create_app(store=store, provider=ScriptedProvider([]))
