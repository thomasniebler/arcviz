"""TokenService: personal access tokens (spec: docs/architecture/specs/agent-authentication.md, ADR-0173).

One keyed document `token` per user holds the hashes of the user's tokens, written conditionally on its revision like
every keyed document. A caller manages only their own tokens; `authenticate` is the middleware's lookup (no actor): every
failure answers None, so nothing tells a guesser why a token failed.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from pydantic import ValidationError

from ..model import now_iso
from ..operations import OperationError
from ..ports import MUTATION_ATTEMPTS, ConflictError, DocumentRepositoryPort, StoreError, is_definitely_missing
from ..tokens import (
    DEFAULT_DAYS,
    DOCUMENT,
    MAX_DAYS,
    MAX_LABEL,
    MAX_TOKENS,
    SCOPES,
    NewToken,
    TokenBox,
    TokenOwner,
    TokenRecord,
    digest,
    generate,
    parse,
)
from ..workspaces import Identity, user_key

T = TypeVar("T")


class TokenError(OperationError):
    """Invalid input for a token (HTTP 400); nothing was written."""


@dataclass(frozen=True)
class AgentCredential:
    """Who an agent's credential speaks for and what it may do: `read` (queries) or `write` (also code links and
    proposals); `kind` says where it came from (`pat`, `oidc`, `shared`: the admin secret of token mode, `open`: off mode)."""

    identity: Identity
    scope: str
    kind: str


@dataclass(frozen=True)
class CreatedToken:
    token: str  # the whole token: shown once, never stored
    record: TokenRecord


class TokenService:
    def __init__(self, documents: DocumentRepositoryPort, actor: Identity | None = None):
        self._docs = documents
        self._actor = actor

    # ---- the caller's own tokens ---------------------------------------------------------------------------

    def list(self) -> list[TokenRecord]:
        """The caller's tokens, oldest first; the expired ones are left out."""
        actor = self._need_actor()
        box = self._load(user_key(actor.id))[0]
        now = datetime.now(UTC)
        return [t for t in (box.tokens if box else []) if not _expired(t, now)]

    def create(self, label: str, scope: str = "read", expires_in_days: int = DEFAULT_DAYS) -> CreatedToken:
        """A new token for the caller; the secret is in the answer only. TokenError for invalid input or too many."""
        actor = self._need_actor()
        label = " ".join(label.split())
        if not label or len(label) > MAX_LABEL or not label.isprintable():
            raise TokenError(f"A token needs a one-line label of at most {MAX_LABEL} characters.")
        if scope not in SCOPES:
            raise TokenError(f"Unknown scope '{scope}'. Available: {', '.join(SCOPES)}.")
        if not 1 <= expires_in_days <= MAX_DAYS:
            raise TokenError(f"A token lives 1 to {MAX_DAYS} days (got {expires_in_days} days).")
        new: NewToken = generate(actor.id)
        now = datetime.now(UTC)
        record = TokenRecord(
            id=new.id,
            label=label,
            scope=scope,
            hash=new.hash,
            created_at=now_iso(),
            expires_at=(now + timedelta(days=expires_in_days)).isoformat(timespec="seconds"),
        )
        owner = TokenOwner(id=actor.id, name=actor.name, email=actor.email, email_trusted=actor.email_trusted)

        def add(box: TokenBox | None) -> TokenBox:
            box = box or TokenBox(user=owner)
            box.user = owner
            now = datetime.now(UTC)
            box.tokens = [
                t for t in box.tokens if not _expired(t, now)
            ]  # the ones the caller no longer sees do not count
            if len(box.tokens) >= MAX_TOKENS:
                raise TokenError(f"You have {MAX_TOKENS} tokens already; revoke one first.")
            box.tokens.append(record)
            return box

        self._update(new.user_key, add)
        return CreatedToken(new.value, record)

    def revoke(self, token_id: str) -> None:
        actor = self._need_actor()

        def drop(box: TokenBox | None) -> TokenBox | None:
            if box is None or all(t.id != token_id for t in box.tokens):
                raise TokenError(f"Unknown token '{token_id}'.")
            box.tokens = [t for t in box.tokens if t.id != token_id]
            return box

        self._update(user_key(actor.id), drop)

    # ---- the middleware's lookup -------------------------------------------------------------------------------

    def authenticate(self, token: str) -> AgentCredential | None:
        """The credential of a valid, unexpired token; None for anything else (malformed, unknown, wrong secret,
        expired, a damaged or unreadable document)."""
        parsed = parse(token)
        if parsed is None:
            return None
        key, token_id, secret = parsed
        try:
            box = self._load(key)[0]
        except (StoreError, ConflictError):
            return None
        if box is None:
            return None
        wanted = digest(secret)
        now = datetime.now(UTC)
        found = next((t for t in box.tokens if t.id == token_id), None)
        # compare even when the id is unknown, so the time does not say whether it exists
        stored = found.hash if found is not None else "0" * 64
        if not hmac.compare_digest(stored, wanted) or found is None or _expired(found, now):
            return None
        identity = Identity.of(box.user.id, box.user.name, box.user.email, box.user.email_trusted)
        if identity is None or user_key(identity.id) != key:
            return None
        return AgentCredential(identity, found.scope, "pat")

    # ---- internals -----------------------------------------------------------------------------------------------

    def _need_actor(self) -> Identity:
        if self._actor is None:
            raise TokenError("Tokens belong to a signed-in user.")
        return self._actor

    def _load(self, key: str) -> tuple[TokenBox | None, str | None]:
        """The box and its revision; (None, None) while there is none. A damaged document is a StoreError, never an
        empty box: writing over it would revoke every token silently."""
        try:
            body, revision = self._docs.read_document(DOCUMENT, key)
        except ConflictError:
            raise
        except StoreError as exc:
            if is_definitely_missing(exc):
                return None, None
            raise
        try:
            return TokenBox.model_validate(body), revision
        except ValidationError as exc:
            raise StoreError("The tokens of this user are damaged and cannot be read.") from exc

    def _update(self, key: str, change: Callable[[TokenBox | None], TokenBox | None]) -> None:
        """`change` on the user's box, written conditionally on the revision read (retried on a concurrent write); the
        write also drops the expired tokens."""
        for attempt in range(MUTATION_ATTEMPTS):
            box, revision = self._load(key)
            updated = change(box)
            if updated is None:
                return
            now = datetime.now(UTC)
            updated.tokens = [t for t in updated.tokens if not _expired(t, now)]
            body = updated.model_dump(mode="json")
            try:
                if revision is None:
                    self._docs.create_document(DOCUMENT, key, body)
                else:
                    self._docs.commit_document(DOCUMENT, key, body, revision)
                return
            except ConflictError:
                if attempt == MUTATION_ATTEMPTS - 1:
                    raise
            except StoreError:
                if revision is None and attempt < MUTATION_ATTEMPTS - 1:
                    continue  # created concurrently
                raise
        raise ConflictError("The tokens kept changing; try again.")


def _expired(record: TokenRecord, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(record.expires_at).astimezone(UTC) <= now
    except ValueError:
        return True  # an unreadable expiry never grants access
