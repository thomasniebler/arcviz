"""Personal access tokens: the format, the stored shape and the bounds (spec: docs/architecture/specs/agent-authentication.md,
ADR-0173).

A token is `avp_<user key>_<id>_<secret>`: the user's 32-hex key, 8 random hex of identification and 40 random hex (160
bits) of secret. Only the SHA-256 of the secret is stored, in the keyed document `token` of the user (one read finds
the user's tokens, also with Cosmos DB). Reading and writing, and who may do what, are `services/tokens.py`.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from .workspaces import user_key

DOCUMENT = "token"  # kind of the keyed document, one per user (key = `workspaces.user_key(user id)`)
PREFIX = "avp"
SCOPES = ("read", "write")
MAX_TOKENS = 20  # per user
MAX_LABEL = 64
DEFAULT_DAYS = 90
MAX_DAYS = 365
_TOKEN_RE = re.compile(r"^avp_([0-9a-f]{32})_([0-9a-f]{8})_([0-9a-f]{40})$")


@dataclass(frozen=True)
class NewToken:
    value: str  # the whole token, shown once
    user_key: str
    id: str
    secret: str
    hash: str


def generate(user_id: str) -> NewToken:
    key = user_key(user_id)
    token_id, secret = secrets.token_hex(4), secrets.token_hex(20)
    return NewToken(f"{PREFIX}_{key[2:]}_{token_id}_{secret}", key, token_id, secret, digest(secret))


def parse(text: str) -> tuple[str, str, str] | None:
    """(user key, id, secret) of a token, or None when `text` is not shaped like one."""
    match = _TOKEN_RE.match(text)
    return (f"u-{match.group(1)}", match.group(2), match.group(3)) if match else None


def looks_like_token(text: str) -> bool:
    return _TOKEN_RE.match(text) is not None


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


class TokenRecord(BaseModel):
    id: str
    label: str
    scope: str
    hash: str
    created_at: str
    expires_at: str

    def public(self) -> dict[str, Any]:
        """What the API shows: everything but the hash."""
        return self.model_dump(mode="json", exclude={"hash"})


class TokenOwner(BaseModel):
    """The identity the tokens act as, as it was when the box was first written (refreshed with every creation)."""

    id: str
    name: str
    email: str | None = None
    email_trusted: bool = False


class TokenBox(BaseModel):
    user: TokenOwner
    tokens: list[TokenRecord] = Field(default_factory=list)
