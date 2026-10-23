"""Persistence ports: the contract between the application services and the store.

Spec: docs/architecture/specs/application-services.md, ADR-0050. Services and other business logic
depend on this module, never on `arcviz.store`; every repository of the store implements the protocols
here structurally. Revisions are opaque: callers get one from a read and hand it back to a conditional
write unchanged.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

from .live import PresenceChannel
from .model import Diagram
from .projects import Project
from .workspaces import Identity

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ID_SUFFIX_LENGTH = 10  # random base32 characters of a new diagram or project id (ADR-0131)
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"  # base32, lowercase: valid in ID_RE
MUTATION_ATTEMPTS = 5  # attempts of a read-modify-write before a conflict is given up as ConflictError
T = TypeVar("T")
Revision = str  # opaque token of a stored state; compared by the store only
Expected = Revision | tuple[Revision, ...]  # the revision(s) an `If-Match` write accepts (any of them, RFC 9110)


class StoreError(Exception):
    pass


class ConflictError(StoreError):
    """The latest state is not at the expected revision any more, or the diagram is gone; nothing was
    written. Spec: docs/architecture/specs/concurrent-mutations.md."""


class VersionGone(ConflictError):
    """A version that existed was pruned by the retention policy meanwhile (HTTP 409: reload, ADR-0130)."""


class RevisionMismatch(ConflictError):
    """A write conditional on a revision the caller named (HTTP `If-Match`) found another one, or lost the race to
    a concurrent write; nothing was written. `revision` is the latest revision when known (spec: collaboration.md,
    ADR-0094)."""

    def __init__(self, message: str, revision: Revision | None = None):
        super().__init__(message)
        self.revision = revision


def is_missing(exc: StoreError) -> bool:
    """The store's answer for a document, project or diagram that does not exist ("… not found."); any other failure
    (a throttle, a timeout, a conflict) is not "absent"."""
    return not isinstance(exc, ConflictError) and str(exc).endswith("not found.")


def is_definitely_missing(exc: BaseException) -> bool:
    """Whether a failed read is the store's "not found" for sure: the innermost StoreError of the cause chain says so (a
    service may re-raise a timeout as its own not-found error, which never counts)."""
    root = exc
    while isinstance(root.__cause__, StoreError):
        root = root.__cause__
    return isinstance(root, StoreError) and is_missing(root)


class VersionInfo(BaseModel):
    version: int
    label: str
    created_at: str
    source: str  # "initial" | "auto" | "manual" | "restore" | "undo" | "redo"
    restored_from: int | None = None
    node_count: int = 0
    edge_count: int = 0
    fingerprint: str = ""  # hash of the model content, used to tell real changes from duplicate snapshots
    # Who caused the version (spec: workspaces-and-sharing.md, ADR-0086): user id and the display name of that
    # moment. Omitted from dumps while unknown, so versions written before keep their JSON.
    author: str | None = None
    author_name: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unknown_author(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in ("author", "author_name"):
            if key in data and data[key] is None:
                del data[key]
        return data


DEFAULT_VERSIONS_PAGE = 50  # versions in a diagram response (ARCVIZ_VERSIONS_PAGE, #114)
MAX_VERSIONS_PAGE = 500  # the largest page of GET /api/diagrams/{id}/versions


def page_of(versions: list[VersionInfo], limit: int, before: int | None = None) -> list[VersionInfo]:
    """The newest `limit` versions (older than `before`, if given), ascending."""
    older = [v for v in versions if before is None or v.version < before]
    return older[-limit:] if limit > 0 else []


@dataclass(frozen=True)
class History:
    """One listing of a diagram's version metadata with the undo and redo targets derived from it (#114)."""

    versions: list[VersionInfo]
    undo_target: int | None
    redo_target: int | None


class DiagramInfo(BaseModel):
    id: str
    title: str
    updated_at: str
    version_count: int


@dataclass
class Change(Generic[T]):
    """What an `update()` change function wants written: `diagram` as the new latest state, recorded as a
    version with `label`/`source` (no version when `label` is None), and a `result` for the caller."""

    diagram: Diagram
    result: T
    label: str | None = None
    source: str = "auto"
    restored_from: int | None = None
    details: str | None = None
    author: Identity | None = None  # who caused the version (ADR-0086)


@dataclass
class Updated(Generic[T]):
    """The outcome of `update()`: the written change, the stored version (if any) and the new revision."""

    diagram: Diagram
    result: T
    version: VersionInfo | None
    revision: Revision


_SUFFIX_RE = re.compile(rf"(?=[a-z2-7]*[2-7])[a-z2-7]{{{ID_SUFFIX_LENGTH}}}")  # base32, at least one digit
_TRAILING_SUFFIX_RE = re.compile(rf"(?:^|-)({_SUFFIX_RE.pattern})$")


def id_suffix() -> str:
    """A fresh random suffix (10 base32 characters, ~49.8 bits) for a new id (ADR-0131). It always holds a digit, so
    `id_base` can tell it from a word of ten letters (#200, ADR-0150): drawn again in the 12.5 % of cases without one."""
    while True:
        suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(ID_SUFFIX_LENGTH))
        if _SUFFIX_RE.fullmatch(suffix):
            return suffix


def id_base(requested: str) -> str:
    """`requested` without a trailing generated suffix (`-` + 10 base32 characters holding a digit, ADR-0150): the
    base of an id that came from an export or that a caller copied, so that a new id replaces its suffix instead of
    adding a second one (#200). Ten letters without a digit stay (`order-processing`); so does the digit-free suffix
    of an id issued before M26, once (the new suffix has a digit)."""
    match = _TRAILING_SUFFIX_RE.search(requested)
    return requested[: match.start()] if match else requested


def new_ids(base: str) -> Iterator[str]:
    """The ids a create tries for a new diagram or project, in order (ADR-0131): `<base>-<random suffix>`, a fresh
    suffix for every attempt; `base` is cut so that the id fits `ID_RE`. A taken candidate (practically never) just
    moves on to the next one, so the id a caller gets never depends on whether somebody else holds `<base>` or any
    other id: nothing about other tenants is observable through a create (#167, superseding ADR-0089). A generated
    suffix at the end of `base` is replaced, not extended (`id_base`, #200)."""
    prefix = id_base(base)[: 64 - ID_SUFFIX_LENGTH - 1].rstrip("-_")
    check_id(prefix or "x")
    while True:
        yield f"{prefix}-{id_suffix()}" if prefix else id_suffix()


CREATE_ATTEMPTS = 8  # fresh ids a create tries before it gives up (a store that keeps failing)


def id_attempts(base: str, what: str) -> Iterator[str]:
    """The bounded create loop of the diagram and the project service (#195): at most `CREATE_ATTEMPTS` candidates of
    `new_ids(base)`, then StoreError naming `what` ("diagram", "project"). `new_ids` is looked up on every call, so it
    stays the one seam that tests and the e2e server pin."""
    for attempt, candidate in enumerate(new_ids(base)):
        if attempt >= CREATE_ATTEMPTS:
            break
        yield candidate
    raise StoreError(f"No free id for a new {what} after {CREATE_ATTEMPTS} attempts; try again.")


def check_id(diagram_id: str) -> str:
    if not ID_RE.match(diagram_id):
        raise StoreError(f"Invalid diagram id '{diagram_id}' (use lowercase letters, digits, '-' or '_').")
    return diagram_id


class DiagramRepositoryPort(Protocol):
    """Diagrams, their linear version history and their chat transcripts, as the services use them.

    Semantics (versions, undo/redo targets, transcript seq and generation) are those of the store
    contract, `tests/store_contract.py`.
    """

    name: str

    def git_status(self) -> dict[str, Any]: ...
    def start_mirror(self) -> None: ...
    def drain_mirror(self, timeout: float | None = None) -> bool: ...
    def ping(self) -> None: ...

    # diagrams
    def list_diagrams(self) -> list[DiagramInfo]: ...
    def diagram_infos(self, diagram_ids: list[str]) -> list[DiagramInfo]: ...
    def exists(self, diagram_id: str) -> bool: ...
    def create(
        self,
        title: str,
        diagram_id: str | None = None,
        diagram: Diagram | None = None,
        author: Identity | None = None,
    ) -> Diagram: ...
    def load(self, diagram_id: str) -> Diagram: ...
    def load_with_revision(self, diagram_id: str) -> tuple[Diagram, Revision]: ...
    def read_revision(self, diagram_id: str) -> Revision: ...
    def save_latest(self, diagram: Diagram, expected: Revision | None = None) -> Revision: ...
    def commit(
        self,
        diagram: Diagram,
        expected: Revision | None,
        *,
        label: str,
        source: str = "auto",
        restored_from: int | None = None,
        details: str | None = None,
        author: Identity | None = None,
    ) -> tuple[Revision, VersionInfo]: ...
    def update(
        self,
        diagram_id: str,
        change: Callable[[Diagram], Change[T] | None],
        attempts: int = MUTATION_ATTEMPTS,
        expected: Expected | None = None,
    ) -> Updated[T] | None: ...
    def delete(self, diagram_id: str, expected: Revision | None = None) -> None: ...

    # versions
    def list_versions(self, diagram_id: str) -> list[VersionInfo]: ...
    def load_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]: ...
    def snapshot_latest(
        self,
        diagram_id: str,
        label: str,
        source: str = "manual",
        author: Identity | None = None,
        expected: Expected | None = None,
    ) -> VersionInfo: ...
    def restore(
        self,
        diagram_id: str,
        version: int,
        source: str = "restore",
        label: str | None = None,
        author: Identity | None = None,
        expected: Expected | None = None,
    ) -> tuple[Diagram, VersionInfo]: ...
    def undo(
        self, diagram_id: str, author: Identity | None = None, expected: Expected | None = None
    ) -> tuple[Diagram, VersionInfo] | None: ...
    def redo(
        self, diagram_id: str, author: Identity | None = None, expected: Expected | None = None
    ) -> tuple[Diagram, VersionInfo] | None: ...
    def undo_target(self, diagram_id: str) -> int | None: ...
    def load_kept_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]: ...
    def history(self, diagram_id: str) -> History: ...
    def redo_target(self, diagram_id: str) -> int | None: ...

    # chat
    def load_chat(self, diagram_id: str) -> list[dict[str, Any]]: ...
    def append_chat(self, diagram_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> None: ...
    def chat_generation(self, diagram_id: str) -> str: ...
    def compact_chat(
        self, diagram_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool: ...
    def clear_chat(self, diagram_id: str, generation: str | None = None) -> None: ...


class ProjectRepositoryPort(Protocol):
    """Project documents (spec: docs/architecture/specs/projects.md), written conditionally on a revision."""

    def list_projects(self) -> list[tuple[Project, Revision]]:
        """Every project with its revision, sorted by id."""
        ...

    def read_project(self, project_id: str) -> tuple[Project, Revision]:
        """StoreError if the project does not exist."""
        ...

    def create_project(self, project: Project) -> Revision:
        """StoreError("... already exists.") when the id is taken (or invalid); never overwrites."""
        ...

    def commit_project(self, project: Project, expected: Revision) -> Revision:
        """Write `project` with a new revision if it exists at revision `expected`; ConflictError otherwise."""
        ...

    def delete_project(self, project_id: str, expected: Revision) -> None:
        """Delete the project (and its transcript) if it exists at revision `expected`; ConflictError otherwise."""
        ...

    # the project transcript (spec: docs/architecture/specs/project-chat.md): the diagram transcript's semantics
    def load_project_chat(self, project_id: str) -> list[dict[str, Any]]: ...
    def append_project_chat(
        self, project_id: str, entries: list[dict[str, Any]], generation: str | None = None
    ) -> None: ...
    def project_chat_generation(self, project_id: str) -> str: ...
    def compact_project_chat(
        self, project_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool: ...
    def clear_project_chat(self, project_id: str, generation: str | None = None) -> None: ...


class DocumentRepositoryPort(Protocol):
    """Keyed documents of the kinds `workspace` and `user` (spec: docs/architecture/specs/workspaces-and-sharing.md,
    ADR-0082), written conditionally on a revision. Keys follow `ID_RE`."""

    def list_documents(self, kind: str) -> list[tuple[str, dict[str, Any], str]]:
        """Every document of `kind` as (key, document, revision), sorted by key."""
        ...

    def read_document(self, kind: str, key: str) -> tuple[dict[str, Any], str]:
        """StoreError if the document does not exist."""
        ...

    def create_document(self, kind: str, key: str, body: dict[str, Any]) -> Revision:
        """StoreError("... already exists.") when the key is taken; never overwrites."""
        ...

    def commit_document(self, kind: str, key: str, body: dict[str, Any], expected: Revision) -> Revision:
        """Replace the document if it exists at revision `expected`; ConflictError otherwise."""
        ...

    def delete_document(self, kind: str, key: str, expected: Revision) -> None:
        """Delete the document if it exists at revision `expected`; ConflictError otherwise."""
        ...


class RepositoryPort(DiagramRepositoryPort, ProjectRepositoryPort, DocumentRepositoryPort, Protocol):
    """One store serving every port, as `create_store()` returns it (what the composition roots wire)."""

    def presence_channel(self) -> PresenceChannel | None:
        """Where the replicas share presence; None for single-instance stores (presence stays in memory)."""
        ...
