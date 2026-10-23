"""RequirementChatService: the transcript of the requirements chat (spec: requirements-workshop.md, ADR-0193).

One keyed document `reqchat` per project holds the entries of the requirements chat, separate from the project chat's
transcript (whose compaction folds the whole transcript and cannot work per scope). It keeps the newest `MAX_ENTRIES`
entries, each cut to `MAX_CONTENT` characters, so the document stays small; it carries the project's incarnation and a
`generation` that a clear replaces, so a turn that was running while the chat was cleared (or the project deleted and
created again) writes nothing. Viewers read, editors write.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError

from ..model import now_iso
from ..ports import MUTATION_ATTEMPTS, ConflictError, DocumentRepositoryPort, StoreError, is_definitely_missing
from ..projects import incarnation_of
from .projects import ProjectService

T = TypeVar("T")

DOCUMENT = "reqchat"
MAX_ENTRIES = 60
MAX_CONTENT = 8000


class ChatBox(BaseModel):
    incarnation: str = ""
    generation: str = ""
    entries: list[dict[str, Any]] = Field(default_factory=list)


class RequirementChatService:
    def __init__(self, documents: DocumentRepositoryPort, projects: ProjectService):
        self._docs = documents
        self._projects = projects

    def entries(self, project_id: str) -> list[dict[str, Any]]:
        """The transcript, oldest first (viewer)."""
        self._projects.require(project_id, "viewer")
        return list(self._load(project_id)[0].entries)

    def generation(self, project_id: str) -> str:
        """Identifies this incarnation of the transcript; "" before the first entry (viewer)."""
        self._projects.require(project_id, "viewer")
        return self._load(project_id)[0].generation

    def append(self, project_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> bool:
        """Add entries (editor). False, and nothing written, when the transcript was cleared since `generation` was read."""
        self._projects.require(project_id, "editor")
        fresh = [self._entry(e) for e in entries]

        def add(box: ChatBox) -> bool:
            if generation is not None and box.generation != generation:
                return False
            box.generation = box.generation or secrets.token_hex(8)
            box.entries = [*box.entries, *fresh][-MAX_ENTRIES:]
            return True

        return self._update(project_id, add)

    def clear(self, project_id: str) -> None:
        """Empty the transcript (editor); a turn running now no longer writes into it."""
        self._projects.require(project_id, "editor")

        def wipe(box: ChatBox) -> None:
            box.entries = []
            box.generation = secrets.token_hex(8)

        self._update(project_id, wipe)

    # ---- internals -------------------------------------------------------------------------------------------

    @staticmethod
    def _entry(entry: dict[str, Any]) -> dict[str, Any]:
        content = str(entry.get("content") or "")
        cut = content if len(content) <= MAX_CONTENT else content[: MAX_CONTENT - 1] + "…"
        return {**entry, "content": cut, "at": entry.get("at") or now_iso()}

    def _load(self, project_id: str) -> tuple[ChatBox, str | None]:
        incarnation = incarnation_of(self._projects.get(project_id))
        try:
            body, revision = self._docs.read_document(DOCUMENT, project_id)
        except ConflictError:
            raise
        except StoreError as exc:
            if is_definitely_missing(exc):
                return ChatBox(incarnation=incarnation), None
            raise
        try:
            box = ChatBox.model_validate(body)
        except ValidationError as exc:
            raise StoreError(f"The requirements chat of project '{project_id}' is damaged and cannot be read.") from exc
        if box.incarnation != incarnation:  # the project was deleted and created again: that chat is gone
            return ChatBox(incarnation=incarnation), revision
        return box, revision

    def _update(self, project_id: str, change: Callable[[ChatBox], T]) -> T:
        for attempt in range(MUTATION_ATTEMPTS):
            box, revision = self._load(project_id)
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
                    continue
                raise
            return result
        raise ConflictError(f"The requirements chat of project '{project_id}' kept changing; try again.")
