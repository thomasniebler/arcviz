"""The steward's bookkeeping document of a project (spec: docs/architecture/specs/project-steward.md, ADR-0204).

`StewardStore` reads and conditionally writes the keyed document `steward` (opt-in switch, what the last run saw, run counts,
dismissed findings, the lease). `StewardService` is the face the routes use: it guards the project before it reads or
changes the document. The check itself is `arcviz/steward.py`.
"""

from __future__ import annotations

import contextlib
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import ValidationError

from ..ports import MUTATION_ATTEMPTS, ConflictError, DocumentRepositoryPort, StoreError, is_definitely_missing
from ..projects import incarnation_of
from ..steward_model import DOCUMENT, LastRun, Lease, StewardBox, StewardConfig
from .projects import ProjectService

T = TypeVar("T")


class StewardStore:
    def __init__(self, documents: DocumentRepositoryPort, projects: ProjectService):
        self._docs = documents
        self._projects = projects

    def load(self, project_id: str) -> tuple[StewardBox, str | None]:
        """The project's box and its revision (None while it does not exist). An unreadable document is an error, never
        an empty box: writing over it would lose what people dismissed."""
        incarnation = incarnation_of(self._projects.get(project_id))
        try:
            body, revision = self._docs.read_document(DOCUMENT, project_id)
        except ConflictError:
            raise
        except StoreError as exc:
            if is_definitely_missing(exc):
                return StewardBox(incarnation=incarnation), None
            raise
        try:
            box = StewardBox.model_validate(body)
        except ValidationError as exc:
            raise StoreError(f"The steward document of project '{project_id}' is damaged and cannot be read.") from exc
        if box.incarnation != incarnation:  # the project was deleted and created again: this belongs to the old one
            return StewardBox(incarnation=incarnation), revision
        return box, revision

    def update(self, project_id: str, change: Callable[[StewardBox], T]) -> T:
        """`change` on the project's box, written conditionally on the revision read (retried on a concurrent write)."""
        for attempt in range(MUTATION_ATTEMPTS):
            box, revision = self.load(project_id)
            result = change(box)
            body = box.model_dump(mode="json")
            try:
                if revision is None:
                    self._docs.create_document(DOCUMENT, project_id, body)
                else:
                    self._docs.commit_document(DOCUMENT, project_id, body, revision)
            except ConflictError:
                if attempt == MUTATION_ATTEMPTS - 1:
                    raise
                continue
            except StoreError:
                if revision is None and attempt < MUTATION_ATTEMPTS - 1:
                    continue  # created concurrently: read it and change it
                raise
            return result
        raise ConflictError(f"The steward document of project '{project_id}' kept changing; try again.")

    # ---- what the services and the worker need ----------------------------------------------------------------

    def dismiss(self, project_id: str, findings: list[str]) -> None:
        """Findings a person discarded: they are not proposed again (`DismissalSink`)."""
        self.update(project_id, lambda box: box.dismiss(findings))

    def enabled(self, project_id: str) -> bool:
        try:
            return self.load(project_id)[0].enabled
        except StoreError:
            return False

    def enabled_projects(self) -> list[str]:
        """The ids of the projects that opted in (one small listing of the keyed documents)."""
        out = []
        for key, body, _revision in self._docs.list_documents(DOCUMENT):
            if isinstance(body, dict) and body.get("enabled"):
                out.append(key)
        return out

    def acquire(self, project_id: str, seconds: float) -> str | None:
        """Take the lease of the project for `seconds` (an expired one is taken over); the holder token, or None while
        another process holds it. Wall-clock based: replicas share it, a little skew only shortens or stretches a lease."""
        holder = secrets.token_hex(8)
        now = time.time()

        def take(box: StewardBox) -> bool:
            if box.lease is not None and box.lease.until > now:
                return False
            box.lease = Lease(holder=holder, until=now + seconds)
            return True

        try:
            return holder if self.update(project_id, take) else None
        except (StoreError, ConflictError):
            return None

    def release(self, project_id: str, holder: str) -> None:
        def free(box: StewardBox) -> None:
            if box.lease is not None and box.lease.holder == holder:
                box.lease = None

        with contextlib.suppress(StoreError, ConflictError):  # the lease expires on its own
            self.update(project_id, free)

    def record(self, project_id: str, fingerprint: str, outcome: Any, now: datetime) -> None:
        """What a run saw and how it ended; counts the run."""

        def done(box: StewardBox) -> None:
            box.last_fingerprint = fingerprint
            box.last_run = LastRun(
                at=now.astimezone(UTC).isoformat(timespec="seconds"),
                outcome=outcome.outcome,
                items=outcome.items,
                verified=outcome.verified,
                dropped=outcome.dropped,
                tokens=outcome.tokens,
            )
            box.count_run(now)

        self.update(project_id, done)


class StewardService:
    """The routes' face: every method guards the project first."""

    def __init__(self, store: StewardStore, projects: ProjectService, config: StewardConfig):
        self._store = store
        self._projects = projects
        self.config = config

    def status(self, project_id: str) -> dict[str, Any]:
        """The steward's state for the project (viewer)."""
        self._projects.require(project_id, "viewer")
        box, _revision = self._store.load(project_id)
        now = datetime.now(UTC)
        return {
            "project": project_id,
            "available": self.config.enabled,
            "enabled": box.enabled,
            "verifier": "configured" if self.config.verifier_configured else "missing",
            "last_run": box.last_run.model_dump() if box.last_run else None,
            "runs_today": box.runs_today(now),
            "max_runs_per_day": self.config.max_runs_per_day,
        }

    def set_enabled(self, project_id: str, enabled: bool) -> dict[str, Any]:
        """Opt the project in or out (owner)."""
        self._projects.require(project_id, "owner")

        def change(box: StewardBox) -> None:
            box.enabled = enabled

        self._store.update(project_id, change)
        return self.status(project_id)

    def require_run(self, project_id: str) -> None:
        """A manual run needs an editor."""
        self._projects.require(project_id, "editor")
