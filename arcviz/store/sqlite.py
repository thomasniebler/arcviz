"""SQLite-backed repository: one file, WAL mode, one connection per thread.

Writers are serialised with ``BEGIN IMMEDIATE`` transactions, which makes version and seq allocation
and the conditional write of latest (revision check, update, version insert) atomic across threads and
processes on one host.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..model import Diagram
from ..projects import Project

if TYPE_CHECKING:
    from ..gitmirror import GitMirror
from .base import (
    LEGACY_REVISION,
    ConflictError,
    DiagramInfo,
    DiagramRepository,
    StoreError,
    VersionInfo,
    check_document,
    check_id,
    finalize_info,
    hide_staged,
    missing_document,
    new_token,
    strip_seq,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS diagrams (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL, latest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
    diagram_id TEXT NOT NULL, version INTEGER NOT NULL, meta TEXT NOT NULL, snapshot TEXT NOT NULL,
    PRIMARY KEY (diagram_id, version)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chat (
    diagram_id TEXT NOT NULL, seq INTEGER NOT NULL, entry TEXT NOT NULL,
    PRIMARY KEY (diagram_id, seq)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, revision TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    kind TEXT NOT NULL, key TEXT NOT NULL, revision TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (kind, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS project_chat (
    project_id TEXT NOT NULL, seq INTEGER NOT NULL, entry TEXT NOT NULL,
    PRIMARY KEY (project_id, seq)) WITHOUT ROWID;
"""


# Columns added after the first release, created on start-up for existing databases.
_ADDED_COLUMNS = {"chat_generation": "chat_generation TEXT", "revision": "revision TEXT"}


def _close_all(connections: list[sqlite3.Connection], lock: threading.Lock) -> None:
    """Close every connection a store opened (its `close()`, or the store being collected)."""
    with lock:
        opened, connections[:] = list(connections), []
    for conn in opened:
        conn.close()


def _release(conn: sqlite3.Connection, connections: list[sqlite3.Connection], lock: threading.Lock) -> None:
    """Close the connection of a thread that ended (its thread-local holder was collected)."""
    with lock:
        if not any(c is conn for c in connections):
            return  # closed already by the store's close()
        connections[:] = [c for c in connections if c is not conn]
    conn.close()


class _Holder:
    """The thread-local slot of one thread's connection: when the thread ends, its locals are dropped and a finalizer
    on the holder closes the connection (worker threads of a long-running server come and go)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class SqliteStore(DiagramRepository):
    name = "sqlite"

    def __init__(self, path: Path | str, mirror: GitMirror | None = None):
        super().__init__(mirror)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # every connection opened (one per thread), so that `close()` and the collection of the store close them all
        # instead of leaving them to the garbage collector (ResourceWarning, #182)
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self._finalizer = weakref.finalize(self, _close_all, self._connections, self._connections_lock)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(diagrams)")}
            for column, ddl in _ADDED_COLUMNS.items():
                if column not in columns:  # a database created by an older version
                    conn.execute(f"ALTER TABLE diagrams ADD COLUMN {ddl}")
            project_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
            if "chat_generation" not in project_columns:  # a database created before project chats (M17)
                conn.execute("ALTER TABLE projects ADD COLUMN chat_generation TEXT")

    def _connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            # autocommit (explicit BEGIN below); still one connection per thread, `close()` may run on another thread
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            with self._connections_lock:
                self._connections.append(conn)
            holder = _Holder(conn)
            weakref.finalize(holder, _release, conn, self._connections, self._connections_lock)
            self._local.holder = holder
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close every connection of this store (each thread opens a new one on its next use)."""
        _close_all(self._connections, self._connections_lock)
        self._local = threading.local()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        yield self._connection()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:  # also on KeyboardInterrupt & co: never leave the thread's connection in a transaction
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # ---- diagrams -------------------------------------------------------

    def list_diagrams(self) -> list[DiagramInfo]:
        rows = (
            self._connection()
            .execute(
                "SELECT d.id, d.title, d.updated_at, (SELECT COUNT(*) FROM versions v WHERE v.diagram_id = d.id)"
                " FROM diagrams d ORDER BY d.id"
            )
            .fetchall()
        )
        return [DiagramInfo(id=r[0], title=r[1], updated_at=r[2], version_count=r[3]) for r in rows]

    def diagram_infos(self, diagram_ids: list[str]) -> list[DiagramInfo]:
        wanted = sorted(set(diagram_ids))
        out: list[DiagramInfo] = []
        for start in range(0, len(wanted), 500):  # below SQLite's limit of host parameters
            chunk = wanted[start : start + 500]
            rows = (
                self._connection()
                .execute(
                    "SELECT d.id, d.title, d.updated_at, (SELECT COUNT(*) FROM versions v WHERE v.diagram_id = d.id)"
                    f" FROM diagrams d WHERE d.id IN ({', '.join('?' * len(chunk))}) ORDER BY d.id",
                    chunk,
                )
                .fetchall()
            )
            out += [DiagramInfo(id=r[0], title=r[1], updated_at=r[2], version_count=r[3]) for r in rows]
        return out

    def exists(self, diagram_id: str) -> bool:
        return self._connection().execute("SELECT 1 FROM diagrams WHERE id = ?", (diagram_id,)).fetchone() is not None

    def read_latest(self, diagram_id: str) -> tuple[Diagram, str]:
        row = self._connection().execute("SELECT latest, revision FROM diagrams WHERE id = ?", (diagram_id,)).fetchone()
        if row is None:
            raise StoreError(f"Diagram '{diagram_id}' not found.")
        return Diagram.model_validate_json(row[0]), row[1] or LEGACY_REVISION

    def read_revision(self, diagram_id: str) -> str:
        row = self._connection().execute("SELECT revision FROM diagrams WHERE id = ?", (diagram_id,)).fetchone()
        if row is None:
            raise StoreError(f"Diagram '{diagram_id}' not found.")
        return str(row[0] or LEGACY_REVISION)

    def create_latest(self, diagram: Diagram, info: VersionInfo) -> tuple[str, VersionInfo]:
        revision = new_token()
        try:
            with self._write() as conn:
                conn.execute(
                    "INSERT INTO diagrams (id, title, updated_at, latest, revision) VALUES (?, ?, ?, ?, ?)",
                    (diagram.id, diagram.title, diagram.updated_at, _dumps(diagram.model_dump(mode="json")), revision),
                )
                stored = self._insert_version(conn, diagram.id, diagram, info)
        except sqlite3.IntegrityError:
            raise StoreError(f"Diagram '{diagram.id}' already exists.") from None
        return revision, stored

    def commit_latest(
        self, diagram: Diagram, expected: str | None, info: VersionInfo | None
    ) -> tuple[str, VersionInfo | None]:
        revision = new_token()
        with self._write() as conn:
            row = conn.execute("SELECT revision FROM diagrams WHERE id = ?", (diagram.id,)).fetchone()
            if row is None:
                raise ConflictError(f"Diagram '{diagram.id}' does not exist (any more).")
            if expected is not None and (row[0] or LEGACY_REVISION) != expected:
                raise ConflictError(f"Diagram '{diagram.id}' was changed by another request.")
            conn.execute(
                "UPDATE diagrams SET title = ?, updated_at = ?, latest = ?, revision = ? WHERE id = ?",
                (diagram.title, diagram.updated_at, _dumps(diagram.model_dump(mode="json")), revision, diagram.id),
            )
            stored = self._insert_version(conn, diagram.id, diagram, info) if info is not None else None
        return revision, stored

    def write_latest(self, diagram: Diagram) -> str:
        revision = new_token()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO diagrams (id, title, updated_at, latest, revision) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET title = excluded.title, updated_at = excluded.updated_at,"
                " latest = excluded.latest, revision = excluded.revision",
                (diagram.id, diagram.title, diagram.updated_at, _dumps(diagram.model_dump(mode="json")), revision),
            )
        return revision

    def purge(self, diagram_id: str, expected: str | None = None) -> None:
        with self._write() as conn:
            if expected is not None:
                row = conn.execute("SELECT revision FROM diagrams WHERE id = ?", (diagram_id,)).fetchone()
                if row is None:
                    raise StoreError(f"Diagram '{diagram_id}' not found.")
                if (row[0] or LEGACY_REVISION) != expected:
                    raise ConflictError(f"Diagram '{diagram_id}' was changed by another request.")
            deleted = conn.execute("DELETE FROM diagrams WHERE id = ?", (diagram_id,)).rowcount
            if not deleted:
                raise StoreError(f"Diagram '{diagram_id}' not found.")
            conn.execute("DELETE FROM versions WHERE diagram_id = ?", (diagram_id,))
            conn.execute("DELETE FROM chat WHERE diagram_id = ?", (diagram_id,))

    # ---- versions -------------------------------------------------------

    @staticmethod
    def _insert_version(conn: sqlite3.Connection, diagram_id: str, diagram: Diagram, info: VersionInfo) -> VersionInfo:
        (current,) = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM versions WHERE diagram_id = ?", (diagram_id,)
        ).fetchone()
        info = finalize_info(info, current + 1)
        conn.execute(
            "INSERT INTO versions (diagram_id, version, meta, snapshot) VALUES (?, ?, ?, ?)",
            (diagram_id, info.version, _dumps(info.model_dump()), _dumps(diagram.model_dump(mode="json"))),
        )
        return info

    def append_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> VersionInfo:
        with self._write() as conn:
            return self._insert_version(conn, diagram_id, diagram, info)

    def list_versions(self, diagram_id: str) -> list[VersionInfo]:
        rows = (
            self._connection()
            .execute("SELECT meta FROM versions WHERE diagram_id = ? ORDER BY version", (diagram_id,))
            .fetchall()
        )
        return [VersionInfo.model_validate_json(r[0]) for r in rows]

    def put_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO versions (diagram_id, version, meta, snapshot) VALUES (?, ?, ?, ?)",
                (diagram_id, info.version, _dumps(info.model_dump()), _dumps(diagram.model_dump(mode="json"))),
            )

    def delete_versions(self, diagram_id: str, versions: list[int]) -> None:
        if not versions:
            return
        with self._write() as conn:
            conn.executemany(
                "DELETE FROM versions WHERE diagram_id = ? AND version = ?", [(diagram_id, n) for n in versions]
            )

    def load_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]:
        row = (
            self._connection()
            .execute("SELECT meta, snapshot FROM versions WHERE diagram_id = ? AND version = ?", (diagram_id, version))
            .fetchone()
        )
        if row is None:
            raise StoreError(f"Version {version} of diagram '{diagram_id}' not found.")
        return VersionInfo.model_validate_json(row[0]), Diagram.model_validate_json(row[1])

    # ---- chat transcript -----------------------------------------------

    def load_chat(self, diagram_id: str) -> list[dict[str, Any]]:
        rows = (
            self._connection()
            .execute("SELECT seq, entry FROM chat WHERE diagram_id = ? ORDER BY seq", (diagram_id,))
            .fetchall()
        )
        return hide_staged([{**json.loads(entry), "seq": seq} for seq, entry in rows])

    @staticmethod
    def _require(conn: sqlite3.Connection, diagram_id: str) -> None:
        if conn.execute("SELECT 1 FROM diagrams WHERE id = ?", (diagram_id,)).fetchone() is None:
            raise StoreError(f"Diagram '{diagram_id}' not found.")

    @staticmethod
    def _check_generation(conn: sqlite3.Connection, table: str, owner_id: str, generation: str | None) -> None:
        """ConflictError unless the transcript has `generation` (no check for None; ADR-0127); in the transaction."""
        if generation is None:
            return
        row = conn.execute(f"SELECT chat_generation FROM {table} WHERE id = ?", (owner_id,)).fetchone()
        if row is None or row[0] != generation:
            raise ConflictError(f"The transcript of '{owner_id}' changed (generation).")

    def append_chat(self, diagram_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> None:
        with self._write() as conn:
            self._require(conn, diagram_id)
            self._check_generation(conn, "diagrams", diagram_id, generation)
            (seq,) = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM chat WHERE diagram_id = ?", (diagram_id,)
            ).fetchone()
            for e in strip_seq(entries):
                seq += 1
                conn.execute("INSERT INTO chat (diagram_id, seq, entry) VALUES (?, ?, ?)", (diagram_id, seq, _dumps(e)))

    def chat_generation(self, diagram_id: str) -> str:
        row = self._connection().execute("SELECT chat_generation FROM diagrams WHERE id = ?", (diagram_id,)).fetchone()
        if row is None:
            return ""
        if row[0] is None:  # a transcript from before generations existed
            with self._write() as conn:
                conn.execute(
                    "UPDATE diagrams SET chat_generation = ? WHERE id = ? AND chat_generation IS NULL",
                    (new_token(), diagram_id),
                )
            return self.chat_generation(diagram_id)
        return str(row[0])

    def compact_chat(
        self, diagram_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        with self._write() as conn:
            if generation is not None:
                row = conn.execute("SELECT chat_generation FROM diagrams WHERE id = ?", (diagram_id,)).fetchone()
                if row is None or row[0] != generation:
                    return False
            present = conn.execute(
                "SELECT 1 FROM chat WHERE diagram_id = ? AND seq = ?", (diagram_id, upto_seq)
            ).fetchone()
            if present is None:
                return False
            conn.execute("DELETE FROM chat WHERE diagram_id = ? AND seq <= ?", (diagram_id, upto_seq))
            conn.execute(
                "INSERT INTO chat (diagram_id, seq, entry) VALUES (?, ?, ?)",
                (diagram_id, upto_seq, _dumps(strip_seq([summary])[0])),
            )
            return True

    def clear_chat(self, diagram_id: str, generation: str | None = None) -> None:
        with self._write() as conn:
            self._require(conn, diagram_id)
            self._check_generation(conn, "diagrams", diagram_id, generation)
            conn.execute("UPDATE diagrams SET chat_generation = ? WHERE id = ?", (new_token(), diagram_id))
            conn.execute("DELETE FROM chat WHERE diagram_id = ?", (diagram_id,))

    def import_chat(self, diagram_id: str, entries: list[dict[str, Any]]) -> None:
        with self._write() as conn:
            self._require(conn, diagram_id)
            conn.execute("UPDATE diagrams SET chat_generation = ? WHERE id = ?", (new_token(), diagram_id))
            conn.execute("DELETE FROM chat WHERE diagram_id = ?", (diagram_id,))
            for e in entries:
                conn.execute(
                    "INSERT INTO chat (diagram_id, seq, entry) VALUES (?, ?, ?)",
                    (diagram_id, e["seq"], _dumps(strip_seq([e])[0])),
                )

    # ---- projects ---------------------------------------------------------

    def list_projects(self) -> list[tuple[Project, str]]:
        rows = self._connection().execute("SELECT body, revision FROM projects ORDER BY id").fetchall()
        return [(Project.model_validate_json(body), revision) for body, revision in rows]

    def read_project(self, project_id: str) -> tuple[Project, str]:
        row = self._connection().execute("SELECT body, revision FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise StoreError(f"Project '{project_id}' not found.")
        return Project.model_validate_json(row[0]), row[1]

    def create_project(self, project: Project) -> str:
        check_id(project.id)
        revision = new_token()
        try:
            with self._write() as conn:
                conn.execute(
                    "INSERT INTO projects (id, revision, body) VALUES (?, ?, ?)",
                    (project.id, revision, _dumps(project.model_dump(mode="json"))),
                )
                conn.execute("DELETE FROM project_chat WHERE project_id = ?", (project.id,))  # never inherited
        except sqlite3.IntegrityError:
            raise StoreError(f"Project '{project.id}' already exists.") from None
        return revision

    def commit_project(self, project: Project, expected: str) -> str:
        revision = new_token()
        with self._write() as conn:
            updated = conn.execute(
                "UPDATE projects SET revision = ?, body = ? WHERE id = ? AND revision = ?",
                (revision, _dumps(project.model_dump(mode="json")), project.id, expected),
            ).rowcount
            if not updated:
                raise ConflictError(f"Project '{project.id}' was changed by another request or does not exist.")
        return revision

    def delete_project(self, project_id: str, expected: str) -> None:
        with self._write() as conn:
            deleted = conn.execute(
                "DELETE FROM projects WHERE id = ? AND revision = ?", (project_id, expected)
            ).rowcount
            if not deleted:
                raise ConflictError(f"Project '{project_id}' was changed by another request or does not exist.")
            conn.execute("DELETE FROM project_chat WHERE project_id = ?", (project_id,))

    # ---- project transcripts (spec: docs/architecture/specs/project-chat.md) -----------

    def load_project_chat(self, project_id: str) -> list[dict[str, Any]]:
        rows = (
            self._connection()
            .execute("SELECT seq, entry FROM project_chat WHERE project_id = ? ORDER BY seq", (project_id,))
            .fetchall()
        )
        return hide_staged([{**json.loads(entry), "seq": seq} for seq, entry in rows])

    @staticmethod
    def _require_project(conn: sqlite3.Connection, project_id: str) -> None:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise StoreError(f"Project '{project_id}' not found.")

    def append_project_chat(
        self, project_id: str, entries: list[dict[str, Any]], generation: str | None = None
    ) -> None:
        with self._write() as conn:
            self._require_project(conn, project_id)
            self._check_generation(conn, "projects", project_id, generation)
            (seq,) = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM project_chat WHERE project_id = ?", (project_id,)
            ).fetchone()
            for e in strip_seq(entries):
                seq += 1
                conn.execute(
                    "INSERT INTO project_chat (project_id, seq, entry) VALUES (?, ?, ?)", (project_id, seq, _dumps(e))
                )

    def project_chat_generation(self, project_id: str) -> str:
        row = self._connection().execute("SELECT chat_generation FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return ""
        if row[0] is None:  # the first reader of a project's transcript creates its generation
            with self._write() as conn:
                conn.execute(
                    "UPDATE projects SET chat_generation = ? WHERE id = ? AND chat_generation IS NULL",
                    (new_token(), project_id),
                )
            return self.project_chat_generation(project_id)
        return str(row[0])

    def compact_project_chat(
        self, project_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        with self._write() as conn:
            if generation is not None:
                row = conn.execute("SELECT chat_generation FROM projects WHERE id = ?", (project_id,)).fetchone()
                if row is None or row[0] != generation:
                    return False
            present = conn.execute(
                "SELECT 1 FROM project_chat WHERE project_id = ? AND seq = ?", (project_id, upto_seq)
            ).fetchone()
            if present is None:
                return False
            conn.execute("DELETE FROM project_chat WHERE project_id = ? AND seq <= ?", (project_id, upto_seq))
            conn.execute(
                "INSERT INTO project_chat (project_id, seq, entry) VALUES (?, ?, ?)",
                (project_id, upto_seq, _dumps(strip_seq([summary])[0])),
            )
            return True

    def clear_project_chat(self, project_id: str, generation: str | None = None) -> None:
        with self._write() as conn:
            self._require_project(conn, project_id)
            self._check_generation(conn, "projects", project_id, generation)
            conn.execute("UPDATE projects SET chat_generation = ? WHERE id = ?", (new_token(), project_id))
            conn.execute("DELETE FROM project_chat WHERE project_id = ?", (project_id,))

    # ---- keyed documents (workspaces, users) ---------------------------------

    def list_documents(self, kind: str) -> list[tuple[str, dict[str, Any], str]]:
        check_document(kind, "probe")
        rows = (
            self._connection()
            .execute("SELECT key, body, revision FROM documents WHERE kind = ? ORDER BY key", (kind,))
            .fetchall()
        )
        return [(key, json.loads(body), revision) for key, body, revision in rows]

    def read_document(self, kind: str, key: str) -> tuple[dict[str, Any], str]:
        check_document(kind, key)
        row = (
            self._connection()
            .execute("SELECT body, revision FROM documents WHERE kind = ? AND key = ?", (kind, key))
            .fetchone()
        )
        if row is None:
            raise missing_document(kind, key)
        return json.loads(row[0]), row[1]

    def create_document(self, kind: str, key: str, body: dict[str, Any]) -> str:
        check_document(kind, key)
        revision = new_token()
        try:
            with self._write() as conn:
                conn.execute(
                    "INSERT INTO documents (kind, key, revision, body) VALUES (?, ?, ?, ?)",
                    (kind, key, revision, _dumps(body)),
                )
        except sqlite3.IntegrityError:
            raise StoreError(f"{kind.capitalize()} '{key}' already exists.") from None
        return revision

    def commit_document(self, kind: str, key: str, body: dict[str, Any], expected: str) -> str:
        check_document(kind, key)
        revision = new_token()
        with self._write() as conn:
            updated = conn.execute(
                "UPDATE documents SET revision = ?, body = ? WHERE kind = ? AND key = ? AND revision = ?",
                (revision, _dumps(body), kind, key, expected),
            ).rowcount
            if not updated:
                raise ConflictError(f"{kind.capitalize()} '{key}' was changed by another request or does not exist.")
        return revision

    def delete_document(self, kind: str, key: str, expected: str) -> None:
        check_document(kind, key)
        with self._write() as conn:
            deleted = conn.execute(
                "DELETE FROM documents WHERE kind = ? AND key = ? AND revision = ?", (kind, key, expected)
            ).rowcount
            if not deleted:
                raise ConflictError(f"{kind.capitalize()} '{key}' was changed by another request or does not exist.")
