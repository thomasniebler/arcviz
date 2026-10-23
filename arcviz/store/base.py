"""Repository contract and the versioning logic shared by every backend.

Spec: docs/architecture/specs/persistence.md. Backends implement the *primitives* (abstract methods);
the *derived operations* are implemented here once and are what the application calls.
"""

from __future__ import annotations

import logging
import secrets
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from ..history import HistoryConfig, history_of, prunable, prune_due, redo_target_of, undo_target_of
from ..live import PresenceChannel
from ..model import Diagram, fingerprint, now_iso, slugify
from ..ports import (
    ID_RE,
    MUTATION_ATTEMPTS,
    Change,
    ConflictError,
    DiagramInfo,
    Expected,
    History,
    RevisionMismatch,
    StoreError,
    Updated,
    VersionGone,
    VersionInfo,
    check_id,
)

if TYPE_CHECKING:
    from ..gitmirror import GitMirror
    from ..projects import Project
    from ..workspaces import Identity

__all__ = [  # the port types are re-exported: `from arcviz.store import ...` keeps working
    "DOCUMENT_KINDS",
    "ID_RE",
    "LEGACY_REVISION",
    "MUTATION_ATTEMPTS",
    "Change",
    "ConflictError",
    "DiagramInfo",
    "DiagramRepository",
    "RevisionMismatch",
    "StoreError",
    "Updated",
    "VersionInfo",
    "check_id",
    "fingerprint",
]

DOCUMENT_KINDS = (
    "workspace",
    "user",
    "placement",
    "listing",
    "rename",
    "location",
    "writer",
    "proposal",
    "token",
    "requirements",
    "reqchat",
)  # kinds of keyed documents (ADR-0082, ADR-0088, ADR-0120, ADR-0133, ADR-0172, ADR-0173, ADR-0188, ADR-0193)
PING_ID = "readiness-probe"  # read by ping(); whether it exists does not matter
LEGACY_REVISION = "0"  # the revision of a latest state stored before revisions existed
T = TypeVar("T")
logger = logging.getLogger(__name__)


def check_document(kind: str, key: str) -> str:
    """Validate a keyed document's kind and key (backends build paths and partition keys from them)."""
    if kind not in DOCUMENT_KINDS:
        raise StoreError(f"Unknown document kind '{kind}'.")
    return check_id(key)


def missing_document(kind: str, key: str) -> StoreError:
    return StoreError(f"{kind.capitalize()} '{key}' not found.")


def finalize_info(info: VersionInfo, number: int) -> VersionInfo:
    """Called by backends once the version number is allocated: sets it and the default label."""
    return info.model_copy(update={"version": number, "label": info.label or f"Version {number}"})


def hide_staged(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop entries older than the newest summary (a staged compaction that has not finished deleting)."""
    newest = max((e["seq"] for e in entries if e.get("role") == "summary"), default=None)
    if newest is None:
        return entries
    return [e for e in entries if e["seq"] >= newest]


def new_token() -> str:
    """A fresh opaque token (chat generations, latest revisions): random, never reused."""
    return secrets.token_hex(8)


def strip_seq(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of `entries` without a `seq` key (backends assign their own)."""
    return [{k: v for k, v in e.items() if k != "seq"} for e in entries]


class DiagramRepository(ABC):
    """Persistence for diagrams, their version history and chat transcripts.

    Chat entries are plain dicts owned by the agent; the repository adds `seq`, a positive integer
    that strictly increases per diagram and is never reused after `compact_chat`. `clear_chat` starts
    numbering again at 1, so a transcript also has a *generation* (`chat_generation`), an opaque token
    that `clear_chat` and `import_chat` replace and that deleting the diagram discards.
    """

    name: str = "abstract"

    def __init__(self, mirror: GitMirror | None = None):
        self.mirror = mirror  # optional Git mirror; receives every snapshot and deletion
        # The retention policy (ADR-0130); None or keep 0: every version is kept. Set by `create_store`.
        self.retention: HistoryConfig | None = None

    # ---- primitives: diagrams ------------------------------------------------

    @abstractmethod
    def list_diagrams(self) -> list[DiagramInfo]:
        """All diagrams, sorted by id."""

    @abstractmethod
    def diagram_infos(self, diagram_ids: list[str]) -> list[DiagramInfo]:
        """The `DiagramInfo` of every given diagram that exists, sorted by id (unknown ids are left out): a caller's
        diagram list without listing the store (#181)."""

    @abstractmethod
    def exists(self, diagram_id: str) -> bool: ...

    @abstractmethod
    def read_latest(self, diagram_id: str) -> tuple[Diagram, str]:
        """Current state and its revision (LEGACY_REVISION if stored without one); StoreError if unknown."""

    @abstractmethod
    def read_revision(self, diagram_id: str) -> str:
        """The revision `read_latest` would return, without reading the diagram itself (live polling, #174);
        StoreError("Diagram '<id>' not found.") if unknown."""

    @abstractmethod
    def create_latest(self, diagram: Diagram, info: VersionInfo) -> tuple[str, VersionInfo]:
        """Write the initial state and version `info` (numbered 1) only if the diagram does not exist yet,
        atomically. Returns the revision and the stored version.

        Raises StoreError("... already exists.") otherwise. Two concurrent creators of the same id must
        never both succeed.
        """

    @abstractmethod
    def commit_latest(
        self, diagram: Diagram, expected: str | None, info: VersionInfo | None
    ) -> tuple[str, VersionInfo | None]:
        """Atomically: if `diagram.id` exists and its revision is `expected` (any revision when None), write
        `diagram` as latest with a new revision and, with `info`, append it as the next version.

        Returns the new revision and the stored version (None without `info`). Raises ConflictError and
        writes nothing when the revision differs or the diagram does not exist (never creates it).
        """

    @abstractmethod
    def write_latest(self, diagram: Diagram) -> str:
        """Unconditional create-or-replace of the current state with a new revision (migration only)."""

    @abstractmethod
    def purge(self, diagram_id: str, expected: str | None = None) -> None:
        """Remove latest, all versions and the chat; StoreError if the diagram does not exist. With `expected` only if
        the latest revision is `expected`, atomically (ConflictError otherwise, nothing removed; #175, ADR-0123)."""

    # ---- primitives: versions ------------------------------------------------

    @abstractmethod
    def append_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> VersionInfo:
        """Allocate the next version number atomically, store meta + snapshot, return `info` with it set.

        `info.version` is ignored on input. Two concurrent callers must never receive the same number.
        """

    @abstractmethod
    def list_versions(self, diagram_id: str) -> list[VersionInfo]:
        """Ascending by version; [] for unknown diagrams."""

    @abstractmethod
    def load_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]:
        """StoreError if the version does not exist."""

    @abstractmethod
    def put_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> None:
        """Store a version under exactly `info.version` (migration only, `store.migrate`): a history with gaps from the
        retention policy keeps its numbers, so `restored_from` stays valid. Replaces a version with that number."""

    @abstractmethod
    def delete_versions(self, diagram_id: str, versions: list[int]) -> None:
        """Remove these versions (the retention policy's prune, ADR-0130); a version that is already gone is fine.
        Never called for the latest version, so version numbers are never reused."""

    # ---- primitives: chat ----------------------------------------------------

    @abstractmethod
    def load_chat(self, diagram_id: str) -> list[dict[str, Any]]:
        """Ascending by seq; [] if there is no transcript."""

    @abstractmethod
    def append_chat(self, diagram_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> None:
        """Assign consecutive `seq` values (highest stored + 1, ...) and store the entries.

        StoreError if the diagram does not exist (a deleted diagram's transcript is never recreated). With
        `generation`, only while the transcript has that generation, checked atomically with the write; ConflictError
        and nothing written otherwise (cleared, imported, or deleted and created again; ADR-0127)."""

    @abstractmethod
    def chat_generation(self, diagram_id: str) -> str:
        """The transcript's current generation; "" for an unknown diagram (nothing is created then).

        Stable across `append_chat` and `compact_chat`; `clear_chat`, `import_chat` and deleting the
        diagram replace it, so it is never the same for two transcripts that reuse seq numbers. A caller
        that wants to compact reads it *before* `load_chat` and passes it to `compact_chat`.
        """

    @abstractmethod
    def compact_chat(
        self, diagram_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        """Atomically replace all entries with seq <= upto_seq by `summary` (stored with seq = upto_seq).

        Conditional: when no entry with seq == upto_seq exists any more (the transcript was compacted past
        it while the summary was being produced), or `generation` is given and is no longer the
        transcript's generation (it was cleared, imported, or the diagram deleted and created again),
        nothing is written and False is returned.
        """

    @abstractmethod
    def clear_chat(self, diagram_id: str, generation: str | None = None) -> None:
        """Remove every entry and start a new generation; with `generation` only while the transcript has it
        (ConflictError otherwise, ADR-0127)."""

    @abstractmethod
    def import_chat(self, diagram_id: str, entries: list[dict[str, Any]]) -> None:
        """Replace the transcript with `entries` verbatim, keeping their `seq` values (migration/export).

        Starts a new generation."""

    # ---- primitives: projects (spec: docs/architecture/specs/projects.md) ------

    @abstractmethod
    def list_projects(self) -> list[tuple[Project, str]]:
        """Every project with its revision, sorted by id."""

    @abstractmethod
    def read_project(self, project_id: str) -> tuple[Project, str]:
        """The project and its revision; StoreError if unknown."""

    @abstractmethod
    def create_project(self, project: Project) -> str:
        """Create-if-absent, atomically; StoreError("... already exists.") otherwise. Returns the revision."""

    @abstractmethod
    def commit_project(self, project: Project, expected: str) -> str:
        """If the project exists at revision `expected`, write it with a new revision (returned); otherwise
        ConflictError and nothing written (never creates)."""

    @abstractmethod
    def delete_project(self, project_id: str, expected: str) -> None:
        """Delete the project (and its transcript) if it exists at revision `expected`; ConflictError otherwise.

        A project created later under the same id starts with an empty transcript."""

    # ---- primitives: project transcripts (spec: docs/architecture/specs/project-chat.md) --------

    @abstractmethod
    def load_project_chat(self, project_id: str) -> list[dict[str, Any]]:
        """The project's transcript, ascending by seq; [] if there is none (or no such project)."""

    @abstractmethod
    def append_project_chat(
        self, project_id: str, entries: list[dict[str, Any]], generation: str | None = None
    ) -> None:
        """Like `append_chat`, for the project's transcript; StoreError if the project does not exist."""

    @abstractmethod
    def project_chat_generation(self, project_id: str) -> str:
        """Like `chat_generation`; "" for an unknown project. Deleting the project discards it."""

    @abstractmethod
    def compact_project_chat(
        self, project_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        """Like `compact_chat`, for the project's transcript."""

    @abstractmethod
    def clear_project_chat(self, project_id: str, generation: str | None = None) -> None:
        """Remove every entry and start a new generation; StoreError if the project does not exist. With
        `generation` only while the transcript has it (ConflictError otherwise, ADR-0127)."""

    # ---- primitives: keyed documents (spec: docs/architecture/specs/workspaces-and-sharing.md) ------
    # Small documents of the kinds in DOCUMENT_KINDS (workspaces, the user directory), keyed by an id-like key,
    # written conditionally on a revision like projects (ADR-0082).

    @abstractmethod
    def list_documents(self, kind: str) -> list[tuple[str, dict[str, Any], str]]:
        """Every document of `kind` as (key, document, revision), sorted by key."""

    @abstractmethod
    def read_document(self, kind: str, key: str) -> tuple[dict[str, Any], str]:
        """The document and its revision; StoreError("<Kind> '<key>' not found.") if unknown."""

    @abstractmethod
    def create_document(self, kind: str, key: str, body: dict[str, Any]) -> str:
        """Create-if-absent, atomically; StoreError("... already exists.") otherwise. Returns the revision."""

    @abstractmethod
    def commit_document(self, kind: str, key: str, body: dict[str, Any], expected: str) -> str:
        """If the document exists at revision `expected`, replace it (new revision returned); otherwise
        ConflictError and nothing written (never creates)."""

    @abstractmethod
    def delete_document(self, kind: str, key: str, expected: str) -> None:
        """Delete the document if it exists at revision `expected`; ConflictError otherwise."""

    # ---- readiness -------------------------------------------------------------

    def presence_channel(self) -> PresenceChannel | None:
        """Where the replicas share presence (ADR-0104): None here, the file and sqlite stores serve one instance and
        keep presence in memory; the Cosmos store overrides it."""
        return None

    def close(self) -> None:
        """Release what the backend holds open (the SQLite store's connections); a no-op for the others."""
        return None

    def ping(self) -> None:
        """Cheap reachability probe for readiness checks; raises if the backend cannot serve requests.

        The default is one existence lookup; backends whose `exists` cannot fail override it.
        """
        self.exists(PING_ID)

    def git_status(self) -> dict[str, Any]:
        """Status of the attached Git mirror, or `{"enabled": False}` without one."""
        return self.mirror.status() if self.mirror is not None else {"enabled": False}

    def start_mirror(self) -> None:
        """Start-up: let the Git mirror push what a previous process left unpushed (#113); nothing without one."""
        if self.mirror is not None:
            self.mirror.start()

    def drain_mirror(self, timeout: float | None = None) -> bool:
        """Shutdown: let the Git mirror finish its pending push, at most `timeout` seconds (default: the mirror's
        `DRAIN_SECONDS`, #113); True without a mirror."""
        if self.mirror is None:
            return True
        return self.mirror.close() if timeout is None else self.mirror.close(timeout)

    # ---- derived operations --------------------------------------------------

    def create(
        self,
        title: str,
        diagram_id: str | None = None,
        diagram: Diagram | None = None,
        author: Identity | None = None,
    ) -> Diagram:
        diagram_id = check_id(diagram_id or slugify(title))
        if diagram is None:
            diagram = Diagram(id=diagram_id, title=title)
        else:
            diagram = diagram.model_copy(deep=True, update={"id": diagram_id, "title": title or diagram.title})
        self._check_integrity(diagram)
        _, info = self.create_latest(diagram, self._version_info(diagram, "Initial state", "initial", author=author))
        self._mirror(diagram, info, None)
        return diagram

    def load(self, diagram_id: str) -> Diagram:
        return self.load_latest(diagram_id)

    def load_latest(self, diagram_id: str) -> Diagram:
        return self.read_latest(check_id(diagram_id))[0]

    def load_with_revision(self, diagram_id: str) -> tuple[Diagram, str]:
        """The current state and the revision a conditional write of a change to it must expect."""
        return self.read_latest(check_id(diagram_id))

    @staticmethod
    def _check_integrity(diagram: Diagram) -> None:
        errors = diagram.integrity_errors()
        if errors:
            raise StoreError("Refusing to persist inconsistent diagram: " + "; ".join(errors))

    def save_latest(self, diagram: Diagram, expected: str | None = None) -> str:
        """Write the current state without a version, conditional on `expected` (see `commit_latest`).

        Returns the new revision. Without `expected` the write is unconditional but still never creates
        a diagram that does not exist."""
        self._check_integrity(diagram)
        revision, _ = self.commit_latest(diagram, expected, None)
        return revision

    def commit(
        self,
        diagram: Diagram,
        expected: str | None,
        *,
        label: str,
        source: str = "auto",
        restored_from: int | None = None,
        details: str | None = None,
        author: Identity | None = None,
    ) -> tuple[str, VersionInfo]:
        """Write `diagram` as latest and as a new version in one atomic step, conditional on `expected`.

        `details` (e.g. the applied operations) only goes into the Git mirror's commit body; `author` is recorded
        on the version (ADR-0086)."""
        self._check_integrity(diagram)
        info = self._version_info(diagram, label, source, restored_from, author)
        revision, stored = self.commit_latest(diagram, expected, info)
        assert stored is not None
        self._mirror(diagram, stored, details)
        self._retain(diagram.id, stored.version)
        return revision, stored

    def update(
        self,
        diagram_id: str,
        change: Callable[[Diagram], Change[T] | None],
        attempts: int = MUTATION_ATTEMPTS,
        expected: Expected | None = None,
    ) -> Updated[T] | None:
        """Read-modify-write with a conditional write, retried on conflicts (spec: concurrent-mutations.md).

        `change` gets the freshly read state (a private copy) and returns what to write, or None to write
        nothing. It runs again on every attempt; exceptions it raises propagate with nothing written.
        Raises StoreError if the diagram does not exist (any more) and ConflictError after `attempts`.

        With `expected` (HTTP `If-Match`, spec: collaboration.md, ADR-0094) there is exactly one attempt: a latest
        revision other than `expected`, or a write in between, raises `RevisionMismatch` and nothing is written.
        """
        if expected is not None:
            attempts = 1
        for _ in range(attempts):
            diagram, revision = self.load_with_revision(diagram_id)
            accepted = (expected,) if isinstance(expected, str) else expected
            if accepted is not None and revision not in accepted:
                raise RevisionMismatch(
                    f"Diagram '{diagram_id}' has changed since revision {', '.join(accepted)}.", revision
                )
            outcome = change(diagram)
            if outcome is None:
                return None
            try:
                if outcome.label is None:
                    return Updated(outcome.diagram, outcome.result, None, self.save_latest(outcome.diagram, revision))
                new_revision, info = self.commit(
                    outcome.diagram,
                    revision,
                    label=outcome.label,
                    source=outcome.source,
                    restored_from=outcome.restored_from,
                    details=outcome.details,
                    author=outcome.author,
                )
            except ConflictError:
                if expected is not None:
                    raise RevisionMismatch(
                        f"Diagram '{diagram_id}' was changed by another request while this one was being applied."
                    ) from None
                continue
            return Updated(outcome.diagram, outcome.result, info, new_revision)
        raise ConflictError(
            f"Diagram '{diagram_id}' was changed by other requests while this one was being applied; try again."
        )

    def delete(self, diagram_id: str, expected: str | None = None) -> None:
        """Remove the diagram from the store and from the Git mirror, if one is attached; with `expected` only at that
        revision (ConflictError otherwise, see `purge`)."""
        self.purge(diagram_id, expected)
        if self.mirror is not None:
            self.mirror.remove(diagram_id)

    @staticmethod
    def _version_info(
        diagram: Diagram,
        label: str,
        source: str,
        restored_from: int | None = None,
        author: Identity | None = None,
    ) -> VersionInfo:
        return VersionInfo(
            version=0,
            label=label.strip()[:120],
            created_at=now_iso(),
            source=source,
            restored_from=restored_from,
            node_count=len(diagram.nodes),
            edge_count=len(diagram.edges),
            fingerprint=fingerprint(diagram),
            author=author.id if author is not None else None,
            author_name=author.name if author is not None else None,
        )

    def _mirror(self, diagram: Diagram, stored: VersionInfo, details: str | None) -> None:
        if self.mirror is not None:
            body = (details or "").strip()
            trailer = f"Arcviz version {stored.version} ({stored.source})"
            if stored.author is not None:
                trailer += f"\nArcviz author: {stored.author_name or stored.author} ({stored.author})"
            self.mirror.record(diagram, subject=stored.label, body=f"{body}\n\n{trailer}" if body else trailer)

    def snapshot(
        self,
        diagram: Diagram,
        label: str,
        source: str = "manual",
        restored_from: int | None = None,
        details: str | None = None,
    ) -> VersionInfo:
        """Append `diagram` as a version without touching latest (tests and tools; the application records
        versions with `commit`/`update`, which keep latest and the newest version consistent).

        `details` (e.g. the applied operations) only goes into the Git mirror's commit body; the stored
        version keeps the label."""
        stored = self.append_version(diagram.id, diagram, self._version_info(diagram, label, source, restored_from))
        self._mirror(diagram, stored, details)
        self._retain(diagram.id, stored.version)
        return stored

    def _retain(self, diagram_id: str, latest: int) -> None:
        """After version `latest` was added: prune what the retention policy lets go (ADR-0130), but only when
        `prune_due` says so from the number (no listing on every commit). Best effort: a failure is logged and never
        fails the write that added the version."""
        if not prune_due(latest, self.retention):
            return
        assert self.retention is not None
        try:
            doomed = prunable(self.list_versions(diagram_id), self.retention)
            if doomed:
                self.delete_versions(diagram_id, doomed)
        except Exception as exc:  # the next write tries again
            logger.warning("Pruning the versions of a diagram failed (%s)", type(exc).__name__)

    def load_kept_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]:
        """`load_version`, but a version the retention policy pruned (a gap between the first and the latest version)
        is `VersionGone`, a 409 "reload" for the caller, not "not found" (ADR-0130)."""
        try:
            return self.load_version(diagram_id, version)
        except StoreError:
            versions = self.list_versions(diagram_id)
            if versions and versions[0].version < version < versions[-1].version:
                raise VersionGone(
                    f"Version {version} of diagram '{diagram_id}' no longer exists (old versions are pruned); "
                    "reload the version list."
                ) from None
            raise

    def history(self, diagram_id: str) -> History:
        """The version metadata with the undo and redo targets, from one listing (#114, ADR-0129)."""
        return history_of(self.list_versions(diagram_id))

    def snapshot_latest(
        self,
        diagram_id: str,
        label: str,
        source: str = "manual",
        author: Identity | None = None,
        expected: Expected | None = None,
    ) -> VersionInfo:
        """Record the current state as a version (a manual snapshot), atomically with latest."""
        updated = self.update(
            diagram_id,
            lambda current: Change(current, None, label=label, source=source, author=author),
            expected=expected,
        )
        assert updated is not None and updated.version is not None
        return updated.version

    def restore(
        self,
        diagram_id: str,
        version: int,
        source: str = "restore",
        label: str | None = None,
        author: Identity | None = None,
        expected: Expected | None = None,
    ) -> tuple[Diagram, VersionInfo]:
        """Make `version` the current state. Recorded as a new snapshot so history stays linear.

        `source` is "restore", or "undo"/"redo" when called by `undo()`/`redo()`.
        """
        info, _ = self.load_kept_version(diagram_id, version)  # StoreError for an unknown version, before anything

        def change(current: Diagram) -> Change[None]:
            _, restored = self.load_kept_version(diagram_id, version)
            restored.updated_at = now_iso()
            text = label or f"Restored version {version} ({info.label})"
            return Change(restored, None, label=text, source=source, restored_from=version, author=author)

        updated = self.update(diagram_id, change, expected=expected)
        assert updated is not None and updated.version is not None
        return updated.diagram, updated.version

    def undo(
        self, diagram_id: str, author: Identity | None = None, expected: Expected | None = None
    ) -> tuple[Diagram, VersionInfo] | None:
        """Restore `undo_target` as a version with source "undo"; None if there is nothing to undo."""
        return self._step(diagram_id, self.undo_target, "undo", author, expected)

    def redo(
        self, diagram_id: str, author: Identity | None = None, expected: Expected | None = None
    ) -> tuple[Diagram, VersionInfo] | None:
        """Restore `redo_target` as a version with source "redo"; None if there is nothing to redo."""
        return self._step(diagram_id, self.redo_target, "redo", author, expected)

    def _step(
        self,
        diagram_id: str,
        target_of: Callable[[str], int | None],
        source: str,
        author: Identity | None = None,
        expected: Expected | None = None,
    ) -> tuple[Diagram, VersionInfo] | None:
        """Undo/redo: the target is computed from the history on every attempt, so after a concurrent
        change it is the one that undoes (redoes) the newest state."""

        def change(current: Diagram) -> Change[None] | None:
            target = target_of(diagram_id)
            if target is None:
                return None
            info, restored = self.load_kept_version(diagram_id, target)
            restored.updated_at = now_iso()
            label = f"{source.capitalize()}: restored version {target} ({info.label})"
            return Change(restored, None, label=label, source=source, restored_from=target, author=author)

        updated = self.update(diagram_id, change, expected=expected)
        if updated is None:
            return None
        assert updated.version is not None
        return updated.diagram, updated.version

    def redo_target(self, diagram_id: str) -> int | None:
        """Version to restore for a "redo": the state the most recent un-redone undo replaced.

        Replays the history as a stack: an undo pushes the version current before it, a redo pops,
        manual snapshots and versions that did not change the content (same fingerprint) are
        transparent, and any other version (a real change or a restore chosen by the user) clears the
        stack. Pushed versions are resolved through `restored_from` so that `undo_target`, which
        follows one level of it, walks back from the original version.
        """
        return redo_target_of(self.list_versions(diagram_id))

    def undo_target(self, diagram_id: str) -> int | None:
        """Version to restore for an "undo": the most recent *different* state before the current one.

        Manual snapshots duplicate the state they were taken from and are skipped. After a restore (or
        undo/redo) the walk continues from the restored version, so repeated undos keep going backwards
        in history. Versions with the same content on top of a restore (a manual snapshot, a no-op
        operation) do not hide it: the cursor is found by walking back over the run of versions with
        the latest fingerprint and following the first `restored_from` in that run.
        """
        return undo_target_of(self.list_versions(diagram_id))
