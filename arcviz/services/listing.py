"""The listing of a user: the ids of their own projects and of the projects shared with them (spec:
docs/architecture/specs/personal-workspaces.md, *Listings*).

A keyed document of kind `listing` per user (key `user_key(user)`), so a caller's project list costs one point read
plus one per listed project instead of a scan of every project of every tenant. It is a hint, never a grant: every
id is re-validated on read (the project must exist and the caller must have a role on it), so a stale or extra id is
harmless. Writers add an id before the write that makes it visible; the migration repairs missing entries at every
start.

Stale entries (a revoked share, a deleted project) are pruned (#183, ADR-0124) without racing a re-share or re-create:
every add stamps its entry (`added`), so it always changes the listing revision; a prune is a write conditional on the
revision read *before* the projects were read, and it removes only entries stamped longer than `LISTING_GRACE` ago (an
add before that read may belong to a share whose project write is still in flight).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..ports import MUTATION_ATTEMPTS, ConflictError, DocumentRepositoryPort, StoreError, is_missing
from ..workspaces import user_key

LISTING = "listing"
OWN, SHARED = "own", "shared"
LISTING_GRACE = timedelta(hours=1)  # an entry younger than this is never pruned (its share may be in flight)
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


@dataclass
class Listing:
    """A user's listing as read, with the revision a prune is conditional on."""

    user: str
    own: list[str]
    shared: list[str]
    added: dict[str, str] = field(default_factory=dict)  # "<field>:<project id>" -> when it was (last) added
    revision: str | None = None  # None: the user has no listing document yet

    def ids(self) -> list[str]:
        return list(dict.fromkeys([*self.own, *self.shared]))


def read_listing(documents: DocumentRepositoryPort, user_id: str) -> tuple[list[str], list[str]]:
    """(own project ids, shared project ids) of the user; empty when they have no listing yet. Any other failure is
    raised (StoreError): never an empty project list because a read was throttled."""
    listing = read_listing_document(documents, user_id)
    return listing.own, listing.shared


def read_listing_document(documents: DocumentRepositoryPort, user_id: str) -> Listing:
    """The user's listing with its stamps and revision (an empty one without revision when there is none)."""
    try:
        body, revision = documents.read_document(LISTING, user_key(user_id))
    except StoreError as exc:
        if is_missing(exc):
            return Listing(user_id, [], [])
        raise
    return parse_listing(user_id, body, revision)


def parse_listing(user_id: str, body: dict[str, Any], revision: str) -> Listing:
    added = body.get("added")
    stamps = {k: v for k, v in added.items() if isinstance(v, str)} if isinstance(added, dict) else {}
    return Listing(user_id, _ids(body.get(OWN)), _ids(body.get(SHARED)), stamps, revision)


def prune_listing(documents: DocumentRepositoryPort, listing: Listing, stale: Iterable[str]) -> int:
    """Remove the entries of the `stale` project ids that were stamped longer than LISTING_GRACE ago from `listing`,
    with one write conditional on the revision it was read at; 0 when nothing qualifies or the listing changed
    meanwhile (a share or create since the read wins). A stale entry without a stamp (written before M22, or by an
    older replica during a rolling upgrade, which may have added it for a share still in flight) is stamped in the
    same write instead, so it goes only after the grace period too. Best effort: never raises. Returns the entries
    removed."""
    if listing.revision is None:
        return 0
    cutoff = (_now() - LISTING_GRACE).isoformat(timespec="seconds")
    lists = {OWN: list(listing.own), SHARED: list(listing.shared)}
    added = dict(listing.added)
    removed = stamped = 0
    for project_id in set(stale):
        for name in (OWN, SHARED):
            key = f"{name}:{project_id}"
            if project_id not in lists[name]:
                continue
            if key not in added:
                added[key] = _stamp()
                stamped += 1
            elif added[key] < cutoff:
                lists[name] = [p for p in lists[name] if p != project_id]
                del added[key]
                removed += 1
    if not (removed or stamped):
        return 0
    try:
        documents.commit_document(
            LISTING, user_key(listing.user), _document(listing.user, lists, added), listing.revision
        )
    except Exception:
        logger.debug("The listing of '%s' was not pruned (changed meanwhile)", listing.user, exc_info=True)
        return 0
    return removed


def _document(user_id: str, lists: dict[str, list[str]], added: dict[str, str]) -> dict[str, Any]:
    return {"user": user_id, **lists, "added": added}


def _ids(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def update_listing(
    documents: DocumentRepositoryPort,
    user_id: str,
    add: Iterable[tuple[str, str]] = (),
    remove: Iterable[tuple[str, str]] = (),
) -> bool:
    """Add and remove `(OWN | SHARED, project id)` entries with a conditional read-modify-write (created when
    missing); True when it wrote. An add always writes, also for a listed id: it stamps the entry (ADR-0124).
    ConflictError after MUTATION_ATTEMPTS."""
    add, remove = list(add), list(remove)
    key = user_key(user_id)
    for _ in range(MUTATION_ATTEMPTS):
        listing = read_listing_document(documents, user_id)
        revision = listing.revision
        lists = {OWN: list(listing.own), SHARED: list(listing.shared)}
        added = dict(listing.added)
        before = {k: list(v) for k, v in lists.items()}
        for name, project_id in remove:
            lists[name] = [p for p in lists[name] if p != project_id]
            added.pop(f"{name}:{project_id}", None)
        stamp = _stamp()
        for name, project_id in add:
            if project_id not in lists[name]:
                lists[name].append(project_id)
            added[f"{name}:{project_id}"] = stamp  # also when listed: an add always changes the revision (ADR-0124)
        if lists == before and not add:
            return False
        document = _document(user_id, lists, added)
        try:
            if revision is None:
                documents.create_document(LISTING, key, document)
            else:
                documents.commit_document(LISTING, key, document, revision)
            return True
        except ConflictError:
            continue
        except StoreError as exc:
            if revision is None and "already exists" in str(exc):
                continue  # created concurrently
            raise
    raise ConflictError(f"The listing of '{user_id}' kept changing; try again.")


def update_quietly(documents: DocumentRepositoryPort, user_id: str, **changes: Iterable[tuple[str, str]]) -> None:
    """`update_listing` after a write that already happened: a failure is logged, the start-up repair fixes it."""
    try:
        update_listing(documents, user_id, **changes)
    except Exception:
        logger.warning(
            "The project listing of '%s' could not be updated; the next start repairs it", user_id, exc_info=True
        )
