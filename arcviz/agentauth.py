"""How agents authenticate on the MCP endpoint (spec: docs/architecture/specs/agent-authentication.md, ADR-0173).

`AgentAuthConfig.from_env` is the one place that reads the MCP and agent-credential variables; `AgentGate` is what
`auth.AuthMiddleware` hands the MCP path to. On that path the identity comes only from a credential the gate checks
itself: a personal access token, an OIDC access token, or in token mode the shared secret; in off mode no credential is
needed. Proxy identity headers and the session cookie are never honoured there. Elsewhere in the app these credentials
mean nothing, so REST keeps its one rule per mode.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from . import tokens
from .auth import AuthConfig, token_matches
from .envconfig import Env
from .limits import ASGIApp, Receive, Scope, Send, send_json
from .oidc import InvalidToken, OidcConfig, protected_resource_metadata
from .services.tokens import AgentCredential
from .workspaces import Identity

DEFAULT_PATH = "/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource"
REALM = 'Bearer realm="arcviz"'
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+){0,3}$")
_RESERVED = ("/api", "/docs", "/.well-known", "/static", "/login", "/logout", "/healthz", "/readyz")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^\[[0-9A-Fa-f:.]+\](:[0-9]{1,5})?$")
_ORIGIN_RE = re.compile(r"^https?://[^/?#\s]+$")


class Validator(Protocol):
    def validate(self, token: str) -> Identity: ...


@dataclass(frozen=True)
class AgentAuthConfig:
    enabled: bool = False
    path: str = DEFAULT_PATH
    oidc: OidcConfig | None = None
    public_url: str | None = None  # the origin clients reach the app at; else derived from each request

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AgentAuthConfig:
        env = Env(environ)
        enabled = env.flag("ARCVIZ_MCP")
        path = env.text("ARCVIZ_MCP_PATH", DEFAULT_PATH)
        if not _PATH_RE.match(path) or any(path == r or path.startswith(r + "/") for r in _RESERVED):
            env.problem(
                f"ARCVIZ_MCP_PATH must be an absolute path such as /mcp (no trailing slash, not under {', '.join(_RESERVED[:4])}…), got {path!r}."
            )
            path = DEFAULT_PATH
        public = env.text("ARCVIZ_PUBLIC_URL").rstrip("/") or None
        if public is not None and not _ORIGIN_RE.match(public):
            env.problem(f"ARCVIZ_PUBLIC_URL must be an origin such as https://arcviz.example.org, got {public!r}.")
            public = None
        issuer, audience = env.text("ARCVIZ_MCP_OAUTH_ISSUER"), env.text("ARCVIZ_MCP_OAUTH_AUDIENCE")
        oidc: OidcConfig | None = None
        if issuer or audience:
            if not issuer or not audience:
                env.problem("ARCVIZ_MCP_OAUTH_ISSUER and ARCVIZ_MCP_OAUTH_AUDIENCE must be set together.")
            elif not enabled:
                env.problem("ARCVIZ_MCP_OAUTH_ISSUER needs ARCVIZ_MCP=1.")
            elif not (
                issuer.startswith("https://") or re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/|$)", issuer)
            ):
                env.problem(f"ARCVIZ_MCP_OAUTH_ISSUER must be an https:// URL, got {issuer!r}.")
            else:
                scopes = tuple(s.strip() for s in env.text("ARCVIZ_MCP_OAUTH_SCOPES").split(",") if s.strip())
                oidc = OidcConfig(issuer, audience, env.text("ARCVIZ_MCP_OAUTH_REQUIRED_SCOPE") or None, scopes)
        env.check()
        return cls(enabled, path, oidc, public)


def oauth_missing(config: AgentAuthConfig) -> str | None:
    """A sentence saying how to install what OIDC validation needs when it is configured and missing, else None."""
    if config.oidc is None:
        return None
    try:
        import jwt  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: F401
    except ImportError:
        return "ARCVIZ_MCP_OAUTH_ISSUER needs PyJWT with cryptography: pip install 'arcviz-agent[mcp]' (the Docker image has it)."
    return None


def _bearer(headers: Mapping[str, str]) -> str:
    scheme, _, credentials = headers.get("authorization", "").partition(" ")
    return credentials.strip() if scheme.lower() == "bearer" else ""


class AgentGate:
    def __init__(
        self,
        config: AgentAuthConfig,
        tokens: Callable[[str], AgentCredential | None],
        oidc: Validator | None,
        admin: Identity | None,
        auth: AuthConfig,
    ):
        self.config = config
        self._tokens = tokens
        self._oidc = oidc
        self._admin = admin
        self._auth = auth
        self._metadata_paths = (METADATA_PATH, f"{METADATA_PATH}{config.path}") if config.oidc is not None else ()

    def owns(self, path: str) -> bool:
        return path == self.config.path or path in self._metadata_paths

    async def handle(self, scope: Scope, receive: Receive, send: Send, app: ASGIApp) -> None:
        path = scope["path"]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        if path != self.config.path:  # the metadata: public
            await self._metadata(scope, headers, send)
            return
        bearer = _bearer(headers)
        credential = await self._credential(bearer)
        if credential is None:
            await send_json(send, 401, "Authentication required.", self._challenge(scope, headers, failed=bool(bearer)))
            return
        state: MutableMapping[str, Any] = scope.setdefault("state", {})
        state["arcviz_identity"] = credential.identity
        state["arcviz_agent_scope"] = credential.scope
        state["arcviz_agent_kind"] = credential.kind
        if credential.kind in ("pat", "oidc"):  # a real user; the shared secret and open mode act as one admin identity
            state["arcviz_user"] = credential.identity.id
        await app(scope, receive, send)

    async def _credential(self, bearer: str) -> AgentCredential | None:
        if bearer and tokens.looks_like_token(bearer):
            return await run_in_threadpool(self._tokens, bearer)
        if bearer and self._oidc is not None and bearer.count(".") == 2:
            try:
                return AgentCredential(await run_in_threadpool(self._oidc.validate, bearer), "write", "oidc")
            except InvalidToken:
                return None
        if self._auth.mode == "token":
            if bearer and token_matches(self._auth, bearer) and self._admin is not None:
                return AgentCredential(self._admin, "write", "shared")
            return None
        if self._auth.mode == "off" and not bearer and self._admin is not None:
            return AgentCredential(self._admin, "write", "open")
        return None  # proxy mode: headers are not credentials here; off mode: a presented credential must be valid

    def _base(self, scope: Scope, headers: Mapping[str, str]) -> str | None:
        if self.config.public_url:
            return self.config.public_url
        proto = headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() or scope.get("scheme", "http")
        host = headers.get("x-forwarded-host", "").split(",")[0].strip() or headers.get("host", "")
        if proto not in ("http", "https") or not _HOST_RE.match(host):
            return None
        return f"{proto}://{host}"

    def _challenge(self, scope: Scope, headers: Mapping[str, str], failed: bool) -> dict[str, str]:
        parts = [REALM]
        base = self._base(scope, headers) if self.config.oidc is not None else None
        if base is not None:
            parts.append(f'resource_metadata="{base}{METADATA_PATH}{self.config.path}"')
        if failed:
            parts.append('error="invalid_token"')
        return {"WWW-Authenticate": ", ".join(parts)}

    async def _metadata(self, scope: Scope, headers: Mapping[str, str], send: Send) -> None:
        assert self.config.oidc is not None
        base = self._base(scope, headers)
        if base is None:
            await send_json(send, 400, "Cannot tell the address of this server; set ARCVIZ_PUBLIC_URL.")
            return
        body = protected_resource_metadata(base, self.config.path, self.config.oidc)
        raw = json.dumps(body).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": raw})
