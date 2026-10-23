"""OIDC bearer tokens for the MCP endpoint (spec: docs/architecture/specs/agent-authentication.md, ADR-0173).

Arcviz is an OAuth *protected resource* (RFC 9728): it validates access tokens of an existing issuer (Entra ID, Keycloak,
Google, …) and never issues any. `OidcValidator.validate` is blocking (key fetches), so callers run it in a worker thread;
every failure is one `InvalidToken` that says nothing about why. Needs PyJWT with the `crypto` extra (extra `oauth`).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .workspaces import Identity

ALGORITHMS = ("RS256", "ES256")  # never HS* (key confusion) and never none
LEEWAY_SECONDS = 60
KEYS_TTL_SECONDS = 3600.0
REFRESH_INTERVAL_SECONDS = 60.0  # an unknown key id refreshes the key set at most this often
FETCH_TIMEOUT_SECONDS = 5.0
MAX_TOKEN_CHARS = 4096
MAX_DOCUMENT_BYTES = 1024 * 1024


class InvalidToken(Exception):
    """The token is not acceptable. The reason is deliberately not part of the message."""


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    required_scope: str | None = None
    scopes: tuple[str, ...] = ()  # advertised in the metadata


def fetch_json(url: str) -> dict[str, Any]:
    """GET a JSON document (discovery or key set); OSError / ValueError when it cannot be had."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310  # https checked at start-up
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
        raw = response.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("document too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


class OidcValidator:
    def __init__(
        self,
        config: OidcConfig,
        fetch: Callable[[str], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._fetch = fetch or fetch_json
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}  # kid -> PyJWK
        self._loaded: float | None = None  # when the key set was fetched

    def validate(self, token: str) -> Identity:
        """The identity the access token speaks for, or InvalidToken."""
        import jwt  # lazy: PyJWT is an optional dependency

        if not token or len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
            raise InvalidToken
        try:
            header = jwt.get_unverified_header(token)
            algorithm, kid = header.get("alg"), header.get("kid")
            if algorithm not in ALGORITHMS or not isinstance(kid, str):
                raise InvalidToken
            key = self._key(kid)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[algorithm],
                audience=self.config.audience,
                issuer=self.config.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud"]},
            )
        except InvalidToken:
            raise
        except Exception as exc:  # every PyJWT error, a malformed header, an unreachable issuer
            raise InvalidToken from exc
        if self.config.required_scope and self.config.required_scope not in _scopes(claims):
            raise InvalidToken
        subject = claims.get("oid") or claims.get("sub")
        email = next(
            (c for c in (claims.get("email"), claims.get("preferred_username")) if isinstance(c, str) and "@" in c),
            None,
        )
        identity = Identity.of(
            subject if isinstance(subject, str) else "",
            claims.get("name") if isinstance(claims.get("name"), str) else None,
            email,
            False,
        )
        if identity is None:
            raise InvalidToken
        return identity

    def _key(self, kid: str) -> Any:
        with self._lock:
            now = self._clock()
            fresh = self._loaded is not None and now - self._loaded < KEYS_TTL_SECONDS
            if fresh and kid in self._keys:
                return self._keys[kid]
            if self._loaded is not None and now - self._loaded < REFRESH_INTERVAL_SECONDS and kid not in self._keys:
                raise InvalidToken  # just refreshed: an unknown id is not a reason to fetch again
            self._load(now)
            if kid not in self._keys:
                raise InvalidToken
            return self._keys[kid]

    def _load(self, now: float) -> None:
        import jwt

        try:
            discovery = self._fetch(f"{self.config.issuer.rstrip('/')}/.well-known/openid-configuration")
            if (
                discovery.get("issuer") != self.config.issuer
            ):  # a document for another issuer says nothing about this one
                raise InvalidToken
            uri = discovery.get("jwks_uri")
            if not isinstance(uri, str) or not uri.startswith(("https://", "http://localhost", "http://127.0.0.1")):
                raise InvalidToken
            document = self._fetch(uri)
            keys: dict[str, Any] = {}
            for entry in document.get("keys", []):
                if isinstance(entry, dict) and isinstance(entry.get("kid"), str) and entry.get("use", "sig") == "sig":
                    try:
                        keys[entry["kid"]] = jwt.PyJWK(entry)
                    except Exception:  # a key of a type this build cannot use is skipped
                        continue
        except InvalidToken:
            raise
        except Exception as exc:
            raise InvalidToken from exc
        self._keys, self._loaded = keys, now


def _scopes(claims: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for name in ("scp", "scope"):
        value = claims.get(name)
        if isinstance(value, str):
            out.update(value.split())
        elif isinstance(value, list):
            out.update(v for v in value if isinstance(v, str))
    return out


def protected_resource_metadata(base_url: str, path: str, config: OidcConfig) -> dict[str, Any]:
    """The RFC 9728 document of the MCP endpoint at `path`."""
    document: dict[str, Any] = {
        "resource": f"{base_url.rstrip('/')}{path}",
        "authorization_servers": [config.issuer],
        "bearer_methods_supported": ["header"],
    }
    if config.scopes:
        document["scopes_supported"] = list(config.scopes)
    return document
