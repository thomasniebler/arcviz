"""UserService: the user directory (spec: docs/architecture/specs/workspaces-and-sharing.md, ADR-0080, ADR-0091;
suggestions while sharing: personal-workspaces.md, ADR-0165).

Users are keyed documents (ADR-0082) written conditionally on their revision; the directory is display data plus
the trusted e-mail address that may identify a user when a project is shared with them (ADR-0091).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from pydantic import ValidationError

from ..model import now_iso
from ..operations import OperationError
from ..ports import MUTATION_ATTEMPTS, ConflictError, DocumentRepositoryPort, StoreError
from ..workspaces import Identity, UserRecord, user_key

USER = "user"
logger = logging.getLogger(__name__)

SUGGEST_MIN_CHARS = 2  # a shorter text suggests nobody: a single letter would list most of the directory
SUGGEST_LIMIT = 10
DIRECTORY_TTL = 60.0  # seconds a process reuses its listing of the directory for suggestions (one store listing, not
# one per keystroke; with Cosmos DB a listing is a cross-partition query)


class UserError(OperationError):
    """A reference to a user that does not identify exactly one (HTTP 400); nothing was written."""


@dataclass
class Person:
    """A user as the API shows it: the directory's name and e-mail, or just the id for an unknown user."""

    id: str
    name: str
    email: str | None = None

    @classmethod
    def of(cls, user_id: str, records: dict[str, UserRecord]) -> Person:
        """The user from the directory records (`UserService.records`), or just the id when unknown."""
        record = records.get(user_id)
        return cls(user_id, record.name, record.email) if record else cls(user_id, user_id)


class UserService:
    """The user directory: one record per identity seen, refreshed at most once a day per process."""

    def __init__(self, documents: DocumentRepositoryPort):
        self._docs = documents
        self._recent: dict[str, tuple[str, str | None, bool, str]] = {}  # id -> (name, e-mail, trusted, day) written
        self._lock = threading.Lock()
        self._directory: tuple[float, list[UserRecord]] | None = None  # (monotonic time, records) for suggestions

    def seen(self, identity: Identity) -> None:
        """Record that `identity` made a request: creates its record, or refreshes name, e-mail and `last_seen`
        when they changed or a new day began. Never raises (it runs for every request; display data): failures are
        logged, and only a write that happened is remembered."""
        today = now_iso()[:10]
        stamp = (identity.name, identity.email, identity.email_trusted, today)
        with self._lock:
            if self._recent.get(identity.id) == stamp:
                return
        try:
            written = self._write(identity, today)
        except Exception:
            logger.warning("Could not update the user directory for '%s'", identity.id, exc_info=True)
            return
        if written:
            with self._lock:
                self._recent[identity.id] = stamp

    def _write(self, identity: Identity, today: str) -> bool:
        """Create or refresh the record; True when it is current afterwards. A malformed record (written by hand or
        by an older version) is replaced: it is display data."""
        key = user_key(identity.id)
        fresh = UserRecord(
            id=identity.id,
            name=identity.name,
            email=identity.email,
            email_trusted=identity.email_trusted,
            first_seen=today,
            last_seen=today,
        )
        for _ in range(MUTATION_ATTEMPTS):
            try:
                body, revision = self._docs.read_document(USER, key)
            except ConflictError:
                raise
            except StoreError:
                try:
                    self._docs.create_document(USER, key, fresh.model_dump(mode="json"))
                    return True
                except ConflictError:
                    raise
                except StoreError:
                    continue  # created concurrently
            try:
                record = UserRecord.model_validate(body)
            except ValidationError:
                logger.warning("Replacing the malformed directory record of '%s'", identity.id)
                record = fresh
            else:
                current = (identity.name, identity.email, identity.email_trusted, today)
                if (record.name, record.email, record.email_trusted, record.last_seen) == current:
                    return True
            updated = record.model_copy(
                update={
                    "name": identity.name,
                    "email": identity.email,
                    "email_trusted": identity.email_trusted,
                    "last_seen": today,
                }
            )
            try:
                self._docs.commit_document(USER, key, updated.model_dump(mode="json"), revision)
                return True
            except ConflictError:
                continue
        return False

    def get(self, user_id: str) -> UserRecord | None:
        try:
            body, _ = self._docs.read_document(USER, user_key(user_id))
        except ConflictError:
            raise
        except StoreError:
            return None
        try:
            return UserRecord.model_validate(body)
        except ValidationError:
            return None  # a malformed record is repaired by the next `seen`

    def find(self, ref: str) -> UserRecord | None:
        """A known user by exact id, else by e-mail address (case-insensitive, only addresses from the proxy's e-mail
        header, UserError when several users have it); None if nobody matches. There is deliberately no search
        (ADR-0085)."""
        ref = (ref or "").strip()
        if not ref:
            return None
        found = self.get(ref)
        if found is not None:
            return found
        if "@" not in ref:
            return None
        wanted = ref.casefold()
        matches = []
        for _, body, _ in self._docs.list_documents(USER):
            try:
                record = UserRecord.model_validate(body)
            except ValidationError:
                continue
            # only an address from the proxy's e-mail header identifies someone: a name or preferred_username claim
            # can be chosen by the user, so anybody could claim somebody else's address (ADR-0085)
            if record.email_trusted and record.email and record.email.casefold() == wanted:
                matches.append(record)
        if len(matches) > 1:
            raise UserError(f"Several users signed in with the e-mail address '{ref}'; use their user id instead.")
        return matches[0] if matches else None

    def _listing(self) -> list[UserRecord]:
        """Every valid directory record, listed at most once per `DIRECTORY_TTL` per process."""
        now = time.monotonic()
        with self._lock:
            if self._directory is not None and now - self._directory[0] < DIRECTORY_TTL:
                return self._directory[1]
        records: list[UserRecord] = []
        for _, body, _ in self._docs.list_documents(USER):
            try:
                records.append(UserRecord.model_validate(body))
            except ValidationError:
                continue
        with self._lock:
            self._directory = (now, records)
        return records

    def suggest(self, query: str, exclude: str | None = None, limit: int = SUGGEST_LIMIT) -> list[UserRecord]:
        """Users who signed in before whose name, id or trusted e-mail address contains `query` (case-insensitive;
        at least `SUGGEST_MIN_CHARS` characters, else none), without `exclude` (the caller), at most `limit`: those
        starting with the text first, then by name (ADR-0165). Untrusted addresses are never searched: they are
        display data anybody can claim (ADR-0091)."""
        wanted = (query or "").strip().casefold()
        if len(wanted) < SUGGEST_MIN_CHARS:
            return []

        def keys(record: UserRecord) -> list[str]:
            fields = [record.name, record.id] + ([record.email] if record.email_trusted and record.email else [])
            return [f.casefold() for f in fields]

        found = [r for r in self._listing() if r.id != exclude and any(wanted in k for k in keys(r))]
        found.sort(key=lambda r: (not any(k.startswith(wanted) for k in keys(r)), r.name.casefold(), r.id))
        return found[:limit]

    def records(self, user_ids: list[str]) -> dict[str, UserRecord]:
        """The directory records of `user_ids` that exist."""
        out: dict[str, UserRecord] = {}
        for user_id in dict.fromkeys(user_ids):
            record = self.get(user_id)
            if record is not None:
                out[user_id] = record
        return out
