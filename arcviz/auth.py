"""Optional authentication: a shared token (bearer or session cookie) or a trusted authenticating proxy.

Spec: docs/architecture/specs/authentication.md (ADR-0047) and, for identities, workspaces-and-sharing.md
(ADR-0080). `AuthConfig.from_env()` is the only place that reads the authentication configuration (also the admin
identity); `AuthMiddleware` enforces it for every HTTP request and stores the caller's `Identity` in the request
scope; the login/logout routes in `server.py` use the helpers below.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from . import i18n
from .i18n import _
from .limits import ASGIApp, Receive, Scope, Send, send_json
from .workspaces import Identity

if TYPE_CHECKING:
    from .agentauth import AgentGate

MODES = ("off", "token", "proxy")
SESSION_COOKIE = "arcviz_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # seconds a browser session stays valid
MIN_TOKEN_LENGTH = 16
PUBLIC_PATHS = frozenset({"/healthz", "/readyz", "/login", "/logout"})
REALM = 'Bearer realm="arcviz"'
DEFAULT_ADMIN_ID = "admin"  # the admin identity of off/token mode unless ARCVIZ_ADMIN_USER says otherwise
DEFAULT_ADMIN_NAME = "Administrator"
EASYAUTH_PRINCIPAL = "x-ms-client-principal"  # base64 JSON with the claims (Azure Easy Auth)
PROXY_MISSING = "Authentication required: no identity from the authenticating proxy."
# Header names per proxy (spec: workspaces-and-sharing.md): (id, name, e-mail)
PROXY_PRESETS: dict[str, tuple[str, str | None, str | None]] = {
    "easyauth": ("X-MS-CLIENT-PRINCIPAL-ID", "X-MS-CLIENT-PRINCIPAL-NAME", None),
    "oauth2-proxy": ("X-Forwarded-User", "X-Forwarded-Preferred-Username", "X-Forwarded-Email"),
}


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "off"
    token: str | None = field(default=None, repr=False)
    proxy_header: str | None = None  # the header with the user id
    docs_public: bool = False
    cookie_secure: bool | None = None  # None: Secure when the request came over HTTPS
    proxy_name_header: str | None = None
    proxy_email_header: str | None = None
    proxy_preset: str | None = None
    # The admin identity: every request of off/token mode, the owner of the default workspace (ADR-0087).
    # None in proxy mode without ARCVIZ_ADMIN_USER.
    admin: Identity | None = Identity(DEFAULT_ADMIN_ID, DEFAULT_ADMIN_NAME)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthConfig:
        environ = os.environ if environ is None else environ
        mode = environ.get("ARCVIZ_AUTH", "").strip().lower() or "off"
        if mode not in MODES:
            raise ValueError(f"ARCVIZ_AUTH must be one of {', '.join(MODES)}, got {mode!r}.")
        docs = environ.get("ARCVIZ_AUTH_DOCS", "").strip().lower() or "protected"
        if docs not in ("protected", "public"):
            raise ValueError(f"ARCVIZ_AUTH_DOCS must be protected or public, got {docs!r}.")
        secure_raw = environ.get("ARCVIZ_COOKIE_SECURE", "").strip().lower() or "auto"
        secure = {"auto": None, "true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}
        if secure_raw not in secure:
            raise ValueError(f"ARCVIZ_COOKIE_SECURE must be auto, true or false, got {secure_raw!r}.")
        token = environ.get("ARCVIZ_AUTH_TOKEN", "").strip() or None
        preset_name = environ.get("ARCVIZ_AUTH_PROXY_PRESET", "").strip().lower() or None
        if preset_name is not None and preset_name not in PROXY_PRESETS:
            raise ValueError(
                f"ARCVIZ_AUTH_PROXY_PRESET must be one of {', '.join(PROXY_PRESETS)}, got {preset_name!r}."
            )
        preset: tuple[str | None, str | None, str | None] = (
            PROXY_PRESETS[preset_name] if preset_name else (None, None, None)
        )
        header = environ.get("ARCVIZ_AUTH_PROXY_HEADER", "").strip() or preset[0]
        name_header = environ.get("ARCVIZ_AUTH_PROXY_NAME_HEADER", "").strip() or preset[1]
        email_header = environ.get("ARCVIZ_AUTH_PROXY_EMAIL_HEADER", "").strip() or preset[2]
        if mode == "token" and token is None:
            raise ValueError("ARCVIZ_AUTH=token needs ARCVIZ_AUTH_TOKEN (generate one, e.g. `openssl rand -hex 32`).")
        if mode == "token" and token is not None and len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"ARCVIZ_AUTH_TOKEN must be at least {MIN_TOKEN_LENGTH} characters long.")
        if mode == "proxy" and header is None:
            raise ValueError(
                "ARCVIZ_AUTH=proxy needs ARCVIZ_AUTH_PROXY_HEADER (the header with the user id) "
                "or ARCVIZ_AUTH_PROXY_PRESET (easyauth, oauth2-proxy)."
            )
        admin_raw = environ.get("ARCVIZ_ADMIN_USER", "").strip()
        if mode != "proxy":
            admin_raw = admin_raw or DEFAULT_ADMIN_ID
        admin: Identity | None = None
        if admin_raw:
            admin = Identity.of(
                admin_raw,
                environ.get("ARCVIZ_ADMIN_NAME", "").strip() or (DEFAULT_ADMIN_NAME if mode != "proxy" else None),
                environ.get("ARCVIZ_ADMIN_EMAIL", "").strip() or None,
                email_trusted=True,  # set by the operator
            )
            if admin is None:
                raise ValueError("ARCVIZ_ADMIN_USER must be at most 256 printable characters.")
        proxy = mode == "proxy"
        return cls(
            mode=mode,
            token=token if mode == "token" else None,
            proxy_header=header if proxy else None,
            docs_public=docs == "public",
            cookie_secure=secure[secure_raw],
            proxy_name_header=name_header if proxy else None,
            proxy_email_header=email_header if proxy else None,
            proxy_preset=preset_name if proxy else None,
            admin=admin,
        )


def _mac(token: str, issued: int) -> str:
    return hmac.new(token.encode(), f"arcviz-session:{issued}".encode(), hashlib.sha256).hexdigest()


def session_value(token: str, issued: int) -> str:
    """The session cookie's value: the issue time and a MAC keyed with the token (not the token itself)."""
    return f"{issued}.{_mac(token, issued)}"


def valid_session(config: AuthConfig, value: str, now: int | None = None) -> bool:
    if config.token is None:
        return False
    issued_raw, _, mac = value.partition(".")
    if not issued_raw.isdigit():
        return False
    issued = int(issued_raw)
    now = int(time.time()) if now is None else now
    if not (now - SESSION_MAX_AGE <= issued <= now + 60):  # a minute of clock skew
        return False
    return hmac.compare_digest(mac, _mac(config.token, issued))


def token_matches(config: AuthConfig, candidate: str) -> bool:
    return config.token is not None and hmac.compare_digest(candidate.encode(), config.token.encode())


def safe_next(target: Any) -> str:
    """`target` if it is a local path, else "/": the login never redirects to another site."""
    if not isinstance(target, str) or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _cookies(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep:
            out[name] = value
    return out


def _header(headers: Mapping[str, str], name: str | None) -> str:
    """A header value as text: ASGI hands over Latin-1, proxies send UTF-8 (e.g. names with umlauts)."""
    if not name:
        return ""
    raw = headers.get(name.lower(), "")
    try:
        return raw.encode("latin-1").decode("utf-8").strip()
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw.strip()


def _principal_claims(headers: Mapping[str, str]) -> dict[str, str]:
    """The claims of Easy Auth's `X-MS-CLIENT-PRINCIPAL` (base64 JSON); {} when absent or not decodable."""
    raw = headers.get(EASYAUTH_PRINCIPAL, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
    except (binascii.Error, ValueError):
        return {}
    claims = data.get("claims") if isinstance(data, dict) else None
    out: dict[str, str] = {}
    for claim in claims if isinstance(claims, list) else []:
        if isinstance(claim, dict) and isinstance(claim.get("typ"), str) and isinstance(claim.get("val"), str):
            out.setdefault(claim["typ"], claim["val"])
    return out


def proxy_identity(config: AuthConfig, headers: Mapping[str, str]) -> Identity | None:
    """The caller as the authenticating proxy describes it (spec: workspaces-and-sharing.md), or None."""
    name = _header(headers, config.proxy_name_header)
    header_email = email = _header(headers, config.proxy_email_header)
    if config.proxy_preset == "easyauth":
        claims = _principal_claims(headers)
        name_header = name
        name = claims.get("name") or name_header
        email = email or claims.get("email") or claims.get("preferred_username") or ""
        if "@" not in email and "@" in name_header:
            email = name_header
    # Only the exact value of the configured e-mail header identifies someone (ADR-0091); an address from a claim or
    # the name header (which the user can choose) is display data, also when it replaced a header value without "@".
    trusted = bool(header_email) and email == header_email
    return Identity.of(_header(headers, config.proxy_header), name or None, email or None, trusted)


def identify(config: AuthConfig, headers: Mapping[str, str]) -> Identity | None:
    """The caller of a request, or None if it is not authenticated: the proxy's user in proxy mode, the admin
    identity in token mode (with a valid token or session) and in off mode."""
    if config.mode == "proxy":
        return proxy_identity(config, headers)
    if config.mode == "token":
        scheme, _, credentials = headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and token_matches(config, credentials.strip()):
            return config.admin
        session = _cookies(headers.get("cookie", "")).get(SESSION_COOKIE)
        if session and valid_session(config, session):
            return config.admin
        return None
    return config.admin


def login_page(next_path: str, error: str | None = None) -> str:
    """The login form of token mode (in the request language, no script, everything inline)."""
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    title = html.escape(_("Arcviz – Anmelden"))
    label = html.escape(_("Zugangstoken"))
    button = html.escape(_("Anmelden"))
    return f"""<!doctype html>
<html lang="{i18n.language()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; background: #f3f5f9; display: grid; place-items: center; min-height: 100vh; margin: 0; }}
form {{ background: #fff; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,.08); width: min(22rem, 90vw); }}
h1 {{ font-size: 1.3rem; margin: 0 0 1rem; color: #2b5fc7; }}
label {{ display: block; margin-bottom: .4rem; }}
input[type=password] {{ width: 100%; box-sizing: border-box; padding: .5rem; font-size: 1rem; }}
button {{ margin-top: 1rem; padding: .5rem 1rem; font-size: 1rem; background: #2b5fc7; color: #fff; border: 0; border-radius: 4px; }}
.error {{ color: #b00020; }}
</style>
</head>
<body>
<form method="post" action="/login">
<h1>Arcviz</h1>
{message}
<label for="token">{label}</label>
<input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
<input type="hidden" name="next" value="{html.escape(next_path)}">
<button type="submit">{button}</button>
</form>
</body>
</html>
"""


class AuthMiddleware:
    """Refuses unauthenticated requests (401, or a redirect to the login page for browser navigations)."""

    def __init__(self, app: ASGIApp, config: AuthConfig, agents: AgentGate | None = None):
        self.app = app
        self.config = config
        self.agents = agents  # the MCP endpoint and its credentials (spec: agent-authentication.md)

    def _public(self, path: str) -> bool:
        return path in PUBLIC_PATHS or (self.config.docs_public and (path == "/docs" or path.startswith("/docs/")))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.agents is not None and self.agents.owns(scope["path"]):
            await self.agents.handle(scope, receive, send, self.app)
            return
        if self.config.mode == "off":
            if self.config.admin is not None:
                scope.setdefault("state", {})["arcviz_identity"] = self.config.admin
            await self.app(scope, receive, send)
            return
        if self._public(scope["path"]):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        user = identify(self.config, headers)
        if user is not None:
            state = scope.setdefault("state", {})
            state["arcviz_identity"] = user
            if self.config.mode == "proxy":  # a real user; in token mode everyone is the same admin identity
                state["arcviz_user"] = user.id
            await self.app(scope, receive, send)
            return
        if self.config.mode == "proxy":  # nothing to log in to here: a redirect to /login would come straight back
            await send_json(send, 401, PROXY_MISSING)
            return
        path = scope["path"]
        navigation = scope["method"] in ("GET", "HEAD") and "text/html" in headers.get("accept", "")
        if navigation and not path.startswith("/api/"):
            query = scope.get("query_string", b"").decode("latin-1")
            target = quote(path + (f"?{query}" if query else ""), safe="/")
            await send(
                {
                    "type": "http.response.start",
                    "status": 303,
                    "headers": [(b"location", f"/login?next={target}".encode("latin-1")), (b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        extra = {"WWW-Authenticate": REALM} if self.config.mode == "token" else None
        await send_json(send, 401, "Authentication required.", extra)
