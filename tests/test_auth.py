"""Authentication (issue #82; spec: docs/architecture/specs/authentication.md, ADR-0047)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from arcviz import auth
from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from arcviz.workspaces import personal_workspace_id
from tests.conftest import ScriptedProvider

TOKEN = "s3cret-token-0123456789abcdef"
HTML = {"accept": "text/html,application/xhtml+xml"}


def make_client(store, monkeypatch, provider=None, tmp_docs=None, **env: str) -> TestClient:
    for name in (
        "ARCVIZ_AUTH",
        "ARCVIZ_AUTH_TOKEN",
        "ARCVIZ_AUTH_PROXY_HEADER",
        "ARCVIZ_AUTH_PROXY_PRESET",
        "ARCVIZ_AUTH_PROXY_NAME_HEADER",
        "ARCVIZ_AUTH_PROXY_EMAIL_HEADER",
        "ARCVIZ_AUTH_DOCS",
        "ARCVIZ_ADMIN_USER",
        "ARCVIZ_ADMIN_NAME",
        "ARCVIZ_ADMIN_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app(store=store, provider=provider or ScriptedProvider([]), docs_dir=tmp_docs)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def token_client(store, demo, monkeypatch):
    return make_client(store, monkeypatch, ARCVIZ_AUTH="token", ARCVIZ_AUTH_TOKEN=TOKEN)


def login(client: TestClient, token: str = TOKEN, next_: str = "/") -> object:
    return client.post("/login", data={"token": token, "next": next_})


# ---- configuration -------------------------------------------------------------------


def test_authentication_is_off_by_default(store, demo, monkeypatch):
    client = make_client(store, monkeypatch)
    assert client.get("/api/diagrams/demo").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/config").json()["auth"] == {"mode": "off", "user": None}
    assert client.get("/login?next=/x").headers["location"] == "/x"  # nothing to log in to


@pytest.mark.parametrize(
    "env, message",
    [
        ({"ARCVIZ_AUTH": "magic"}, "ARCVIZ_AUTH must be one of off, token, proxy"),
        ({"ARCVIZ_AUTH": "token"}, "ARCVIZ_AUTH_TOKEN"),
        ({"ARCVIZ_AUTH": "token", "ARCVIZ_AUTH_TOKEN": "short"}, "at least 16 characters"),
        ({"ARCVIZ_AUTH": "proxy"}, "ARCVIZ_AUTH_PROXY_HEADER"),
        ({"ARCVIZ_AUTH": "off", "ARCVIZ_AUTH_DOCS": "open"}, "ARCVIZ_AUTH_DOCS"),
        ({"ARCVIZ_COOKIE_SECURE": "maybe"}, "ARCVIZ_COOKIE_SECURE"),
    ],
)
def test_invalid_configuration_stops_the_start(store, monkeypatch, env, message):
    with pytest.raises(ValueError, match=message):
        make_client(store, monkeypatch, **env)


def test_the_token_never_leaks_into_config_or_errors(token_client):
    r = token_client.get("/api/config", headers={"authorization": f"Bearer {TOKEN}"})
    assert r.json()["auth"] == {"mode": "token", "user": None} and TOKEN not in r.text
    assert TOKEN not in token_client.get("/api/config").text
    assert TOKEN not in repr(auth.AuthConfig.from_env({"ARCVIZ_AUTH": "token", "ARCVIZ_AUTH_TOKEN": TOKEN}))


# ---- token mode ----------------------------------------------------------------------


def test_api_without_credentials_is_401(token_client, store):
    r = token_client.get("/api/diagrams/demo")
    assert r.status_code == 401 and r.json() == {"detail": "Authentication required."}
    assert r.headers["www-authenticate"] == 'Bearer realm="arcviz"'
    r = token_client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "x"}})
    assert r.status_code == 401 and store.load("demo").title == "Demo"


def test_a_browser_navigation_is_redirected_to_the_login_page(token_client):
    r = token_client.get("/?d=demo", headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/%3Fd%3Ddemo"
    r = token_client.get("/static/app.js")  # assets without an HTML accept header: 401, no redirect
    assert r.status_code == 401
    page = token_client.get("/login?next=/%3Fd%3Ddemo")
    assert page.status_code == 200 and 'type="password"' in page.text and 'name="token"' in page.text
    assert 'value="/?d=demo"' in page.text
    assert page.text.count("<script") == 1 and "location.hash" in page.text  # carries a permalink fragment into `next`


def test_bearer_token_passes_and_a_wrong_one_does_not(token_client):
    assert token_client.get("/api/diagrams/demo", headers={"authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert token_client.get("/api/diagrams/demo", headers={"authorization": "Bearer nope"}).status_code == 401
    assert token_client.get("/api/diagrams/demo", headers={"authorization": f"Basic {TOKEN}"}).status_code == 401


def test_probes_stay_public(token_client):
    assert token_client.get("/healthz").status_code == 200
    assert token_client.get("/readyz").status_code in (200, 503)


def test_login_sets_a_strict_http_only_session_cookie_and_redirects(token_client):
    r = login(token_client, next_="/?d=demo")
    assert r.status_code == 303 and r.headers["location"] == "/?d=demo"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=") and TOKEN not in cookie
    for attribute in ("HttpOnly", "SameSite=strict", "Path=/", f"Max-Age={auth.SESSION_MAX_AGE}"):
        assert attribute.lower() in cookie.lower()
    assert "secure" not in cookie.lower()  # plain HTTP test client, ARCVIZ_COOKIE_SECURE=auto
    assert token_client.get("/api/diagrams/demo").status_code == 200  # the client keeps the cookie
    assert token_client.get("/", headers=HTML).status_code == 200


def test_login_returns_to_a_permalink_fragment(token_client):
    r = login(token_client, next_="/#/p/webshop/d/zahlungen")
    assert r.status_code == 303 and r.headers["location"] == "/#/p/webshop/d/zahlungen"
    page = token_client.get("/login?next=/%23/p/webshop/d/zahlungen")
    assert 'value="/#/p/webshop/d/zahlungen"' in page.text


def test_login_accepts_json_too(token_client):
    r = token_client.post("/login", json={"token": TOKEN, "next": "/"})
    assert r.status_code == 303 and token_client.get("/api/diagrams/demo").status_code == 200


def test_a_wrong_token_shows_the_page_again_with_401(token_client):
    r = login(token_client, token="wrong-token-0123456789")
    assert r.status_code == 401 and "Ungültiges Token" in r.text and "set-cookie" not in r.headers
    assert token_client.get("/api/diagrams/demo").status_code == 401


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:x"])
def test_login_redirects_only_to_local_paths(token_client, target):
    r = login(token_client, next_=target)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_cookie_secure_follows_the_configuration(store, demo, monkeypatch):
    client = make_client(store, monkeypatch, ARCVIZ_AUTH="token", ARCVIZ_AUTH_TOKEN=TOKEN, ARCVIZ_COOKIE_SECURE="true")
    assert "secure" in login(client).headers["set-cookie"].lower()


def test_expired_future_and_forged_sessions_are_refused():
    config = auth.AuthConfig(mode="token", token=TOKEN)
    now = int(time.time())
    assert auth.valid_session(config, auth.session_value(TOKEN, now), now)
    assert auth.valid_session(config, auth.session_value(TOKEN, now - auth.SESSION_MAX_AGE + 5), now)
    assert not auth.valid_session(config, auth.session_value(TOKEN, now - auth.SESSION_MAX_AGE - 5), now)
    assert not auth.valid_session(config, auth.session_value(TOKEN, now + 3600), now)
    assert not auth.valid_session(config, auth.session_value("another-token-0123456789", now), now)
    assert not auth.valid_session(config, f"{now}.deadbeef", now)
    assert not auth.valid_session(config, "garbage", now)


def test_logout_deletes_the_cookie(token_client):
    login(token_client)
    r = token_client.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert f"{auth.SESSION_COOKIE}=" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
    token_client.cookies.clear()
    assert token_client.get("/api/diagrams/demo").status_code == 401


def test_the_chat_stream_works_with_the_session_cookie(store, demo, monkeypatch):
    provider = ScriptedProvider([ModelReply(text="Hallo.", tool_calls=[], stop_reason="end_turn")])
    client = make_client(store, monkeypatch, provider, ARCVIZ_AUTH="token", ARCVIZ_AUTH_TOKEN=TOKEN)
    assert client.post("/api/diagrams/demo/chat", json={"message": "hi"}).status_code == 401
    login(client)
    with client.stream("POST", "/api/diagrams/demo/chat", json={"message": "hi"}) as r:
        body = "".join(r.iter_text())
    assert r.status_code == 200 and '"type": "final"' in body


def test_docs_are_protected_by_default_and_public_on_request(store, demo, monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<h1>Docs</h1>", encoding="utf-8")
    client = make_client(store, monkeypatch, tmp_docs=tmp_path, ARCVIZ_AUTH="token", ARCVIZ_AUTH_TOKEN=TOKEN)
    assert client.get("/docs/", headers=HTML).status_code == 303
    login(client)
    assert client.get("/docs/").status_code == 200
    client = make_client(
        store, monkeypatch, tmp_docs=tmp_path, ARCVIZ_AUTH="token", ARCVIZ_AUTH_TOKEN=TOKEN, ARCVIZ_AUTH_DOCS="public"
    )
    assert client.get("/docs/").status_code == 200 and client.get("/docs").status_code in (200, 307)
    assert client.get("/api/diagrams/demo").status_code == 401  # only the docs are public


# ---- proxy mode ----------------------------------------------------------------------


@pytest.fixture
def proxy_client(store, demo, monkeypatch):
    return make_client(
        store,
        monkeypatch,
        ARCVIZ_AUTH="proxy",
        ARCVIZ_AUTH_PROXY_HEADER="X-MS-CLIENT-PRINCIPAL-NAME",
        ARCVIZ_ADMIN_USER="ada@example.org",  # owns the default workspace with the existing data
    )


def test_proxy_mode_trusts_the_configured_header_only(proxy_client):
    user = {"x-ms-client-principal-name": "ada@example.org"}
    assert proxy_client.get("/api/diagrams/demo").status_code == 401
    assert proxy_client.get("/api/diagrams/demo", headers={"x-ms-client-principal-name": "  "}).status_code == 401
    assert proxy_client.get("/api/diagrams/demo", headers={"authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert proxy_client.get("/api/diagrams/demo", headers=user).status_code == 200
    assert proxy_client.get("/api/config", headers=user).json()["auth"] == {"mode": "proxy", "user": "ada@example.org"}
    assert proxy_client.get("/login?next=/x", headers=user).headers["location"] == "/x"


def test_proxy_identity_keys_the_rate_limiter(store, demo, monkeypatch):
    client = make_client(
        store,
        monkeypatch,
        ARCVIZ_AUTH="proxy",
        ARCVIZ_AUTH_PROXY_HEADER="X-User",
        ARCVIZ_RATE_LIMIT_OPS="1",
        ARCVIZ_ADMIN_USER="ada",
    )
    body = {"op": "rename_diagram", "args": {"title": "t"}}
    assert client.post("/api/diagrams/demo/ops", json=body, headers={"x-user": "ada"}).status_code == 200
    assert client.post("/api/diagrams/demo/ops", json=body, headers={"x-user": "ada"}).status_code == 429
    assert client.post("/api/diagrams/demo/ops", json=body, headers={"x-user": "bob"}).status_code != 429


def test_unauthenticated_requests_do_not_use_the_rate_budget(token_client, store, demo, monkeypatch):
    client = make_client(store, monkeypatch, ARCVIZ_AUTH="token", ARCVIZ_AUTH_TOKEN=TOKEN, ARCVIZ_RATE_LIMIT_OPS="1")
    body = {"op": "rename_diagram", "args": {"title": "t"}}
    for _ in range(3):
        assert client.post("/api/diagrams/demo/ops", json=body).status_code == 401
    headers = {"authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/diagrams/demo/ops", json=body, headers=headers).status_code == 200


def test_only_a_proxy_identity_keys_the_rate_limiter():
    """In token mode everyone is the same admin identity: the limiter must key on the client address instead."""
    import asyncio

    seen: list[dict] = []

    async def app(scope, receive, send):
        seen.append(dict(scope.get("state") or {}))

    def run(config, headers):
        scope = {"type": "http", "method": "GET", "path": "/api/x", "headers": headers, "query_string": b""}
        asyncio.run(auth.AuthMiddleware(app, config)(scope, None, None))

    run(auth.AuthConfig(mode="token", token=TOKEN), [(b"authorization", f"Bearer {TOKEN}".encode())])
    run(auth.AuthConfig(mode="proxy", proxy_header="X-User"), [(b"x-user", b"ada")])
    assert [s.get("arcviz_user") for s in seen] == [None, "ada"]
    assert [s["arcviz_identity"].id for s in seen] == ["admin", "ada"]


# ---- identities (issue #158; spec: docs/architecture/specs/workspaces-and-sharing.md, ADR-0080) -------------


def _identity(env: dict[str, str], headers: dict[str, str]):
    config = auth.AuthConfig.from_env({"ARCVIZ_AUTH": "proxy", **env})
    return auth.identify(config, {k.lower(): v for k, v in headers.items()})


def _principal(**claims: str) -> str:
    import base64
    import json

    payload = {"auth_typ": "aad", "claims": [{"typ": k, "val": v} for k, v in claims.items()]}
    return base64.b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_easyauth_preset_reads_id_name_and_email():
    env = {"ARCVIZ_AUTH_PROXY_PRESET": "easyauth"}
    headers = {
        "X-MS-CLIENT-PRINCIPAL-ID": "8d1f-oid",
        "X-MS-CLIENT-PRINCIPAL-NAME": "ada@contoso.com",
        "X-MS-CLIENT-PRINCIPAL": _principal(name="Ada Lovelace", preferred_username="ada@contoso.com"),
    }
    assert _identity(env, headers) == auth.Identity("8d1f-oid", "Ada Lovelace", "ada@contoso.com")
    # without the principal: the name header, which is also the address
    plain = _identity(env, {"X-MS-CLIENT-PRINCIPAL-ID": "8d1f-oid", "X-MS-CLIENT-PRINCIPAL-NAME": "ada@contoso.com"})
    assert plain == auth.Identity("8d1f-oid", "ada@contoso.com", "ada@contoso.com")
    # a broken principal is ignored, the id header alone is enough
    broken = _identity(env, {"X-MS-CLIENT-PRINCIPAL-ID": "8d1f-oid", "X-MS-CLIENT-PRINCIPAL": "%%%"})
    assert broken == auth.Identity("8d1f-oid", "8d1f-oid", None)
    assert _identity(env, {"X-MS-CLIENT-PRINCIPAL-NAME": "ada@contoso.com"}) is None  # the id is required
    # Easy Auth has no e-mail header: an address from claims or the name is display data, never an identifier
    assert _identity(env, headers).email_trusted is False and plain.email_trusted is False


def test_oauth2_proxy_preset_and_overrides():
    headers = {
        "X-Forwarded-User": "ada",
        "X-Forwarded-Email": "ada@example.org",
        "X-Forwarded-Preferred-Username": "Ada",
    }
    assert _identity({"ARCVIZ_AUTH_PROXY_PRESET": "oauth2-proxy"}, headers) == auth.Identity(
        "ada", "Ada", "ada@example.org"
    )
    assert _identity({"ARCVIZ_AUTH_PROXY_PRESET": "oauth2-proxy"}, headers).email_trusted is True  # its own header
    custom = {
        "ARCVIZ_AUTH_PROXY_PRESET": "oauth2-proxy",
        "ARCVIZ_AUTH_PROXY_HEADER": "X-Forwarded-Email",
        "ARCVIZ_AUTH_PROXY_NAME_HEADER": "X-Display",
    }
    assert _identity(custom, {**headers, "X-Display": "Ada L."}) == auth.Identity(
        "ada@example.org", "Ada L.", "ada@example.org"
    )


def test_identity_values_are_validated_and_decoded():
    env = {"ARCVIZ_AUTH_PROXY_HEADER": "X-User", "ARCVIZ_AUTH_PROXY_NAME_HEADER": "X-Name"}
    utf8_as_latin1 = "Jürgen Müller".encode().decode("latin-1")  # how ASGI hands over UTF-8 header bytes
    assert _identity(env, {"X-User": "jm", "X-Name": utf8_as_latin1}).name == "Jürgen Müller"
    assert _identity(env, {"X-User": "x" * 257}) is None
    assert _identity(env, {"X-User": "ada\x00"}) is None
    assert _identity(env, {"X-User": "  ada  ", "X-Name": "  Ada   Lovelace "}) == auth.Identity("ada", "Ada Lovelace")
    no_mail = {**env, "ARCVIZ_AUTH_PROXY_EMAIL_HEADER": "X-Mail"}
    assert _identity(no_mail, {"X-User": "ada", "X-Mail": "not-an-address"}).email is None


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"ARCVIZ_AUTH": "proxy", "ARCVIZ_AUTH_PROXY_PRESET": "nginx"}, "ARCVIZ_AUTH_PROXY_PRESET"),
        ({"ARCVIZ_AUTH": "proxy"}, "ARCVIZ_AUTH_PROXY_PRESET"),
        ({"ARCVIZ_AUTH": "off", "ARCVIZ_ADMIN_USER": "x" * 300}, "ARCVIZ_ADMIN_USER"),
    ],
)
def test_identity_configuration_errors(env, message):
    with pytest.raises(ValueError, match=message):
        auth.AuthConfig.from_env(env)


def test_admin_identity_per_mode():
    assert auth.AuthConfig.from_env({}).admin == auth.Identity("admin", "Administrator")
    custom = {"ARCVIZ_ADMIN_USER": "ops", "ARCVIZ_ADMIN_NAME": "Ops Team", "ARCVIZ_ADMIN_EMAIL": "ops@example.org"}
    assert auth.AuthConfig.from_env(custom).admin == auth.Identity("ops", "Ops Team", "ops@example.org")
    proxy = {"ARCVIZ_AUTH": "proxy", "ARCVIZ_AUTH_PROXY_PRESET": "easyauth"}
    assert auth.AuthConfig.from_env(proxy).admin is None  # unknown until configured (ADR-0087)
    assert auth.AuthConfig.from_env({**proxy, "ARCVIZ_ADMIN_USER": "8d1f-oid"}).admin.id == "8d1f-oid"


def test_proxy_mode_refuses_missing_identities_without_a_redirect(proxy_client):
    navigation = proxy_client.get("/", headers=HTML)
    assert navigation.status_code == 401 and "authenticating proxy" in navigation.json()["detail"]
    assert proxy_client.get("/api/me").status_code == 401
    assert proxy_client.get("/healthz").status_code == 200


def test_me_reports_the_caller(store, demo, monkeypatch):
    off = make_client(store, monkeypatch)
    assert off.get("/api/me").json() == {
        "id": "admin",
        "name": "Administrator",
        "email": None,
        "admin": True,
        "auth_mode": "off",
        "workspace": personal_workspace_id("admin"),
    }
    proxy = make_client(
        store, monkeypatch, ARCVIZ_AUTH="proxy", ARCVIZ_AUTH_PROXY_PRESET="oauth2-proxy", ARCVIZ_ADMIN_USER="ada"
    )
    bob = {"x-forwarded-user": "bob", "x-forwarded-email": "bob@example.org"}
    assert proxy.get("/api/me", headers=bob).json() == {
        "id": "bob",
        "name": "bob",
        "email": "bob@example.org",
        "admin": False,
        "auth_mode": "proxy",
        "workspace": personal_workspace_id("bob"),
    }
    assert proxy.get("/api/me", headers={"x-forwarded-user": "ada"}).json()["admin"] is True


def test_requests_fill_the_user_directory(store, demo, monkeypatch):
    from arcviz.services.users import UserService

    proxy = make_client(store, monkeypatch, ARCVIZ_AUTH="proxy", ARCVIZ_AUTH_PROXY_PRESET="oauth2-proxy")
    headers = {
        "x-forwarded-user": "bob",
        "x-forwarded-email": "bob@example.org",
        "x-forwarded-preferred-username": "Bob",
    }
    proxy.get("/api/me", headers=headers)
    users = UserService(store)
    record = users.get("bob")
    assert record is not None and (record.name, record.email) == ("Bob", "bob@example.org")
    assert record.first_seen == record.last_seen
    proxy.get("/api/me", headers={**headers, "x-forwarded-preferred-username": "Robert"})
    assert users.get("bob").name == "Robert"  # a changed name is refreshed
    assert users.find("BOB@example.org").id == "bob" and users.find("nobody@example.org") is None
    assert users.find("bob").id == "bob" and users.find("bo") is None  # exact matches only


def test_an_e_mail_is_trusted_only_as_the_exact_value_of_the_e_mail_header():
    env = {"ARCVIZ_AUTH_PROXY_PRESET": "easyauth", "ARCVIZ_AUTH_PROXY_EMAIL_HEADER": "X-Verified-Email"}
    base = {"X-MS-CLIENT-PRINCIPAL-ID": "oid", "X-MS-CLIENT-PRINCIPAL-NAME": "victim@contoso.com"}
    # the header carries no address: the fallback (a user-choosable name) is display data, never trusted
    fallback = _identity(env, {**base, "X-Verified-Email": "not-an-address"})
    assert fallback.email == "victim@contoso.com" and fallback.email_trusted is False
    verified = _identity(env, {**base, "X-Verified-Email": "me@contoso.com"})
    assert verified.email == "me@contoso.com" and verified.email_trusted is True
