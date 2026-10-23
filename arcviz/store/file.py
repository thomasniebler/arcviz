"""File-backed repository (the default).

Layout under the data root::

    diagrams/<diagram_id>/latest.json          current state, rewritten after every change
    diagrams/<diagram_id>/versions/0001.json   immutable snapshots {"meta": VersionInfo, "diagram": Diagram}
    diagrams/<diagram_id>/versions/0001.meta.json   the version's metadata alone, read by list_versions (#114)
    diagrams/<diagram_id>/chat.json            transcript, every entry carries "seq"
    diagrams/<diagram_id>/chat-generation      the transcript's generation token (plain text)
    projects/<project_id>.json                 a project document with its "revision" (projects.md)
    project-chats/<project_id>/chat.json       the project transcript, and its "generation" (project-chat.md)
    workspaces/<id>.json, users/<key>.json     keyed documents {"document": ..., "revision": ...} (ADR-0082)

`latest.json` carries the state's revision under the top-level key "revision". Version numbers are
claimed with exclusive file creation (O_EXCL), so several processes sharing one data directory cannot
collide on them. Conditional writes of latest, chat writes and deletion hold a lock per diagram that is
shared by every FileStore of the same directory in this process, but not across processes: the
directory must have a single writing process.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
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

log = logging.getLogger(__name__)

_TOMBSTONE = ".purging"  # written first and removed last by purge
_PURGE_STARTED = "started"  # the tombstone's content until latest.json is gone
_LATEST_REMOVED = "latest removed"  # from then on: what a crashed purge leaves is debris


def _tombstone(directory: Path) -> str | None:
    """The content of a purge's tombstone in `directory`, None without one."""
    try:
        return (directory / _TOMBSTONE).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError):
        return None


_RACY_NS = 2_000_000_000  # read_revision caches only a latest.json older than this (2 s)
_REVISIONS_CACHED = 4096  # read_revision's cache holds at most this many diagrams


def _clock_ns() -> int:
    """The wall clock read_revision compares modification times with (a seam for the tests)."""
    return time.time_ns()


_CLAIM_PROBE = 64  # bytes: a latest.json this small may be a whitespace-only claim and is read to tell (#168)


def _claim(path: Path) -> bool:
    """Claim a new diagram (`latest.json`), project or keyed document file exclusively (`O_EXCL`); False when it exists.
    An **empty** (or whitespace-only) file is a claim whose writer died before writing (a crash between the claim and the write): the caller holds the per-key
    lock, and the file store has one writing process per data directory, so nobody is writing it any more; it is
    reclaimed instead of blocking the id forever. Readers treat empty files as absent."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            if not path.read_text(encoding="utf-8").strip():
                return True  # a dead claim (empty or whitespace only, as readers treat it): ours now
        except FileNotFoundError:
            return _claim(path)  # deleted meanwhile
        except (OSError, UnicodeDecodeError):
            pass
        return False
    os.close(fd)
    return True


def _meta_path(snapshot: Path) -> Path:
    """The metadata sidecar of a version snapshot (`0001.json` -> `0001.meta.json`)."""
    return snapshot.with_name(f"{snapshot.stem}.meta.json")


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())  # the content is on disk before the rename makes it the file
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        finally:
            raise
    _fsync_dir(path.parent)  # and the rename itself: after a power loss the file is the old or the new one, never empty


def _atomic_write_text(path: Path, text: str) -> None:
    """`_atomic_write` for a plain-text file (the transcript generation, a purge's tombstone): synced, then renamed,
    then the directory synced (#117)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def _version_number(path: Path) -> int | None:
    """The number of a version snapshot file (`0001.json`, `10000.json`); None for sidecars and temporary files."""
    return int(path.stem) if path.suffix == ".json" and path.stem.isdigit() else None


def _fsync_dir(directory: Path) -> None:
    """Make a rename in `directory` durable (POSIX); where directories cannot be opened (Windows) it is skipped."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _parse_version(path: Path, text: str) -> tuple[VersionInfo, Diagram]:
    """A version snapshot's content; StoreError for a cut-off or otherwise invalid file (#117)."""
    try:
        data = json.loads(text)
        return VersionInfo.model_validate(data["meta"]), Diagram.model_validate(data["diagram"])
    except (ValueError, KeyError, TypeError) as exc:  # JSONDecodeError and pydantic's ValidationError are ValueErrors
        raise StoreError(f"{type(exc).__name__} in {path.name}") from exc


@dataclass(frozen=True)
class _Owner:
    """Where a transcript lives and how to tell whether its owner (a diagram or a project) exists."""

    lock: str
    chat: Path
    generation: Path
    exists: Callable[[], bool]
    writable: Callable[[], bool]
    missing: str


_REGISTRY_LOCK = threading.Lock()
_DIAGRAM_LOCKS: dict[tuple[str, str], threading.RLock] = {}


class FileStore(DiagramRepository):
    name = "file"

    def __init__(self, root: Path | str, mirror: GitMirror | None = None):
        super().__init__(mirror)
        self.root = Path(root)
        (self.root / "diagrams").mkdir(parents=True, exist_ok=True)
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self._key = str(self.root.resolve())
        # read_revision's cache: id -> (stat, revision), least recently used first, at most _REVISIONS_CACHED entries
        self._revisions: OrderedDict[str, tuple[tuple[int, int, int], str]] = OrderedDict()
        self._revisions_lock = threading.Lock()

    @contextmanager
    def _locked(self, diagram_id: str) -> Iterator[None]:
        """The process-wide lock of one diagram of this data directory (see the module docstring)."""
        with _REGISTRY_LOCK:
            lock = _DIAGRAM_LOCKS.setdefault((self._key, diagram_id), threading.RLock())
        with lock:
            yield

    # ---- paths -----------------------------------------------------------

    def _dir(self, diagram_id: str) -> Path:
        return self.root / "diagrams" / check_id(diagram_id)

    def _latest_path(self, diagram_id: str) -> Path:
        return self._dir(diagram_id) / "latest.json"

    def _versions_dir(self, diagram_id: str) -> Path:
        return self._dir(diagram_id) / "versions"

    def _chat_path(self, diagram_id: str) -> Path:
        return self._dir(diagram_id) / "chat.json"

    def _generation_path(self, diagram_id: str) -> Path:
        return self._dir(diagram_id) / "chat-generation"

    # ---- diagrams -------------------------------------------------------

    def exists(self, diagram_id: str) -> bool:
        """An empty (or whitespace-only) latest.json is a claim, not a diagram (#168): absent, like every reader sees it."""
        try:
            path = self._latest_path(diagram_id)
        except StoreError:
            return False
        try:
            size = path.stat().st_size
            if size == 0:
                return False
            return size > _CLAIM_PROBE or bool(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, NotADirectoryError):
            return False
        except UnicodeDecodeError:
            return True  # content, just not a claim (readers report what is wrong with it)
        except OSError as exc:  # permissions, I/O: never "absent", which could let a create claim a live id
            raise StoreError(f"Diagram '{diagram_id}' could not be checked: {exc}") from exc

    def ping(self) -> None:
        diagrams = self.root / "diagrams"
        if not (diagrams.is_dir() and os.access(diagrams, os.W_OK | os.X_OK)):
            raise StoreError(f"Data directory '{diagrams}' is not a writable directory.")

    def list_diagrams(self) -> list[DiagramInfo]:
        out = (self._info(entry) for entry in sorted((self.root / "diagrams").iterdir()))
        return [info for info in out if info is not None]

    def diagram_infos(self, diagram_ids: list[str]) -> list[DiagramInfo]:
        out = (self._info(self._dir(diagram_id)) for diagram_id in sorted(set(diagram_ids)))
        return [info for info in out if info is not None]

    def _info(self, directory: Path) -> DiagramInfo | None:
        try:
            text = (directory / "latest.json").read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return None  # no diagram (any more)
        if not text.strip():
            return None  # being created right now
        data = json.loads(text)
        return DiagramInfo(
            id=data["id"],
            title=data.get("title", data["id"]),
            updated_at=data.get("updated_at", ""),
            version_count=len(self._version_files(data["id"])),
        )

    def _read(self, diagram_id: str) -> tuple[Diagram, str] | None:
        path = self._latest_path(diagram_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if not text.strip():  # claimed by a concurrent create_latest that has not written yet
            return None
        data = json.loads(text)
        revision = str(data.pop("revision", LEGACY_REVISION))
        return Diagram.model_validate(data), revision

    def read_revision(self, diagram_id: str) -> str:
        """`stat()` of latest.json; the file is parsed (not validated) only when it changed since the last call: every
        write replaces it with a new file (new inode, mtime), so (inode, mtime, size) identifies the state (#174). A file
        younger than `_RACY_NS` is never cached: a rewrite within the same timestamp tick could reuse inode and size."""
        path = self._latest_path(diagram_id)
        try:
            st = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            raise StoreError(f"Diagram '{diagram_id}' not found.") from None
        key = (st.st_ino, st.st_mtime_ns, st.st_size)
        with self._revisions_lock:
            cached = self._revisions.get(diagram_id)
            if cached is not None and cached[0] == key:
                self._revisions.move_to_end(diagram_id)
                return cached[1]
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise StoreError(f"Diagram '{diagram_id}' not found.") from None
        if not text.strip():  # a claim, not a diagram
            raise StoreError(f"Diagram '{diagram_id}' not found.")
        revision = str(json.loads(text).get("revision", LEGACY_REVISION))
        if _clock_ns() - st.st_mtime_ns > _RACY_NS:  # a file written just now could be rewritten within the same
            with self._revisions_lock:  # timestamp tick with an equal key: never cached
                self._revisions[diagram_id] = (key, revision)
                self._revisions.move_to_end(diagram_id)
                while len(self._revisions) > _REVISIONS_CACHED:
                    self._revisions.popitem(last=False)
        return revision

    def read_latest(self, diagram_id: str) -> tuple[Diagram, str]:
        found = self._read(diagram_id)
        if found is None:
            raise StoreError(f"Diagram '{diagram_id}' not found.")
        return found

    def _write(self, diagram: Diagram) -> str:
        revision = new_token()
        _atomic_write(self._latest_path(diagram.id), {**diagram.model_dump(mode="json"), "revision": revision})
        return revision

    def create_latest(self, diagram: Diagram, info: VersionInfo) -> tuple[str, VersionInfo]:
        path = self._latest_path(diagram.id)
        with self._locked(diagram.id):
            path.parent.mkdir(parents=True, exist_ok=True)
            had_latest = path.exists()
            purging = not had_latest and _tombstone(path.parent) == _LATEST_REMOVED  # a purge stopped past latest
            if not _claim(path):  # an empty claim of a create that died is reclaimed (#168)
                raise StoreError(f"Diagram '{diagram.id}' already exists.")
            if not purging:
                try:  # never over a diagram with history whose latest.json was lost (raises, keeps everything)
                    self._check_reclaimable(diagram.id)
                except StoreError:
                    if not had_latest:
                        path.unlink(missing_ok=True)  # our own claim: the directory stays as it was
                    raise
            try:
                self._set_leftovers_aside(diagram.id)  # of a create that died: the new diagram inherits nothing
                stored = self.append_version(
                    diagram.id, diagram, info
                )  # readers see no diagram until latest is written
                return self._write(diagram), stored  # replaces the empty placeholder atomically
            except BaseException:
                path.unlink(missing_ok=True)  # give the id back instead of leaving an unreadable placeholder
                raise

    def _check_reclaimable(self, diagram_id: str) -> None:
        """A directory without a usable latest.json (empty, or none at all) and without a purge tombstone is reclaimed
        only when it can be what a dead create leaves: at most version 1 and no transcript. Anything more is a diagram
        whose latest state was lost (a crash of the file system): it is refused and left for an operator, who restores
        latest from its newest version (#168)."""
        versions = self._version_files(diagram_id)
        transcript = self._chat_path(diagram_id).exists() or self._generation_path(diagram_id).exists()
        if len(versions) > 1 or transcript:
            raise StoreError(
                f"Diagram '{diagram_id}' has no usable latest state but {len(versions)} versions"
                f"{' and a transcript' if transcript else ''}; it is not reclaimed (restore latest.json from the newest "
                "version file)."
            )

    def _set_leftovers_aside(self, diagram_id: str) -> None:
        """Move what a dead create (its version 1) or an interrupted purge (marked by its tombstone) left in the
        diagram's directory to `reclaimed/<id>-<token>/`; never deleted, an operator may still want it."""
        leftovers = [p for p in self._dir(diagram_id).iterdir() if p.name != "latest.json"]
        if not leftovers:
            return
        aside = self.root / "reclaimed" / f"{diagram_id}-{new_token()}"
        aside.mkdir(parents=True)
        for path in leftovers:
            os.replace(path, aside / path.name)

    def commit_latest(
        self, diagram: Diagram, expected: str | None, info: VersionInfo | None
    ) -> tuple[str, VersionInfo | None]:
        with self._locked(diagram.id):
            current = self._read(diagram.id)
            if current is None:
                raise ConflictError(f"Diagram '{diagram.id}' does not exist (any more).")
            if expected is not None and current[1] != expected:
                raise ConflictError(f"Diagram '{diagram.id}' was changed by another request.")
            revision = self._write(diagram)
            stored = self.append_version(diagram.id, diagram, info) if info is not None else None
            return revision, stored

    def write_latest(self, diagram: Diagram) -> str:
        with self._locked(diagram.id):
            return self._write(diagram)

    def purge(self, diagram_id: str, expected: str | None = None) -> None:
        directory = self._dir(diagram_id)
        with self._revisions_lock:
            self._revisions.pop(diagram_id, None)
        with self._locked(diagram_id):
            if not directory.exists():
                raise StoreError(f"Diagram '{diagram_id}' not found.")
            if expected is not None:
                current = self._read(diagram_id)
                if current is None:
                    raise StoreError(f"Diagram '{diagram_id}' not found.")
                if current[1] != expected:
                    raise ConflictError(f"Diagram '{diagram_id}' was changed by another request.")
            tombstone = directory / _TOMBSTONE
            try:
                _atomic_write_text(tombstone, _PURGE_STARTED)  # first
                try:
                    self._latest_path(diagram_id).unlink(missing_ok=True)  # then latest: gone for readers
                except BaseException:
                    tombstone.unlink(missing_ok=True)  # latest is still there: nothing is debris
                    raise
                _atomic_write_text(tombstone, _LATEST_REMOVED)  # only now is what is left debris
                self._remove_debris(directory, tombstone)
            except OSError as exc:  # never a bare OSError (a 500); the tombstone marks what is left as debris (#117)
                raise StoreError(
                    f"Diagram '{diagram_id}' could not be deleted completely: {type(exc).__name__}."
                ) from exc

    @staticmethod
    def _remove_debris(directory: Path, tombstone: Path, passes: int = 3) -> None:
        """Remove everything in a purged diagram's directory, the tombstone last. Writes that do not hold the diagram's
        lock (a version sidecar written by a listing) may rename a temporary file away or create one meanwhile (#117):
        a file already gone is fine, and a directory that is not empty yet is tried again in the next pass."""
        for attempt in range(passes):
            for path in sorted(directory.rglob("*"), reverse=True):
                if path == tombstone:
                    continue
                if path.is_dir():
                    try:
                        path.rmdir()
                    except FileNotFoundError:
                        pass  # removed meanwhile: nothing left to do
                    except OSError:
                        if attempt == passes - 1:
                            raise  # still not empty after the last pass
                        # not empty yet (a write created a file meanwhile): the next pass removes it
                else:
                    path.unlink(missing_ok=True)
            try:
                tombstone.unlink()  # last
                directory.rmdir()
                return
            except OSError:
                if not tombstone.exists():  # what is left stays marked as debris (the next pass, or the next create)
                    with suppress(OSError):
                        _atomic_write_text(tombstone, _LATEST_REMOVED)
                if attempt == passes - 1:
                    raise

    # ---- versions -------------------------------------------------------

    def _version_files(self, diagram_id: str) -> list[Path]:
        directory = self._versions_dir(diagram_id)
        if not directory.exists():
            return []
        numbered = ((n, p) for p in directory.glob("*.json") if (n := _version_number(p)) is not None)
        return [p for _, p in sorted(numbered)]  # by number: 10000.json follows 9999.json (#117)

    def append_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> VersionInfo:
        directory = self._versions_dir(diagram_id)
        directory.mkdir(parents=True, exist_ok=True)
        # after the highest stored number (never a gap a prune left: numbers are never reused, ADR-0130)
        number = max((int(p.stem) for p in self._version_files(diagram_id)), default=0) + 1  # sorted: the last one
        while True:  # claim the next free number by creating its file exclusively
            path = directory / f"{number:04d}.json"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                number += 1
                continue
            os.close(fd)
            break
        info = finalize_info(info, number)
        payload = {"meta": info.model_dump(), "diagram": diagram.model_dump(mode="json")}
        _atomic_write(path, payload)  # replaces the reserved (empty) file atomically
        _atomic_write(_meta_path(path), info.model_dump())  # after the snapshot: a sidecar means a complete version
        return info

    def list_versions(self, diagram_id: str) -> list[VersionInfo]:
        """From the metadata sidecars; a version written before M23 has none yet: its snapshot is read once and the
        sidecar written (#114). A sidecar is only a cache: a damaged one (cut off, invalid, not UTF-8) is rebuilt from
        the snapshot the same way, and a version whose snapshot is damaged too is skipped (#117)."""
        out = []
        for path in self._version_files(diagram_id):
            meta = _meta_path(path)
            try:
                out.append(VersionInfo.model_validate(json.loads(meta.read_text(encoding="utf-8"))))
                continue
            except FileNotFoundError:
                pass
            except ValueError:  # JSONDecodeError, ValidationError and UnicodeDecodeError are ValueErrors
                log.warning("Rebuilding a damaged version metadata file from its snapshot (#117)")
            try:
                info = self._read_version_meta(path)
            except StoreError:
                log.warning("Skipped a damaged version file while listing versions (#117)")
                continue  # load_version reports it; the rest of the history stays usable
            if info is None:
                continue  # reserved by a concurrent snapshot that has not finished writing yet
            out.append(info)
            with suppress(OSError):  # only a cache: the next listing tries again
                _atomic_write(meta, info.model_dump())
        return out

    @staticmethod
    def _read_version_meta(path: Path) -> VersionInfo | None:
        """The metadata out of a snapshot file (the slow path; None while the file is still reserved)."""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:  # pruned meanwhile
            return None
        except UnicodeDecodeError as exc:  # damaged: the listing skips it like any other damaged file (#117)
            raise StoreError(f"UnicodeDecodeError in {path.name}") from exc
        if not text.strip():
            return None
        return _parse_version(path, text)[0]

    def put_version(self, diagram_id: str, diagram: Diagram, info: VersionInfo) -> None:
        directory = self._versions_dir(diagram_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{info.version:04d}.json"
        _atomic_write(path, {"meta": info.model_dump(), "diagram": diagram.model_dump(mode="json")})
        _atomic_write(_meta_path(path), info.model_dump())

    def delete_versions(self, diagram_id: str, versions: list[int]) -> None:
        directory = self._versions_dir(diagram_id)
        for number in versions:
            path = directory / f"{number:04d}.json"
            for victim in (path, _meta_path(path)):  # the snapshot first: a listing goes by the snapshot files
                with suppress(FileNotFoundError):
                    victim.unlink()

    def load_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]:
        path = self._versions_dir(diagram_id) / f"{version:04d}.json"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise StoreError(f"Version {version} of diagram '{diagram_id}' not found.") from None
        except UnicodeDecodeError as exc:
            raise StoreError(f"Version {version} of diagram '{diagram_id}' is damaged (not UTF-8).") from exc
        if not text.strip():  # a claim whose writer died (or is still writing): no version yet (#117)
            raise StoreError(f"Version {version} of diagram '{diagram_id}' is incomplete (an empty file).")
        try:
            return _parse_version(path, text)
        except StoreError as exc:
            raise StoreError(f"Version {version} of diagram '{diagram_id}' is damaged ({exc}).") from exc

    # ---- chat transcripts -----------------------------------------------
    # Diagram and project transcripts share one implementation, parametrised by their owner (ADR-0057).

    def _diagram_owner(self, diagram_id: str) -> _Owner:
        return _Owner(
            lock=diagram_id,
            chat=self._chat_path(diagram_id),
            generation=self._generation_path(diagram_id),
            exists=lambda: self.exists(diagram_id),
            writable=lambda: self._dir(diagram_id).exists(),  # never recreate the directory of a deleted diagram
            missing=f"Diagram '{diagram_id}' not found.",
        )

    def _project_owner(self, project_id: str) -> _Owner:
        directory = self._project_chat_dir(project_id)
        return _Owner(
            lock=f"project:{project_id}",
            chat=directory / "chat.json",
            generation=directory / "generation",
            exists=lambda: self._read_project(project_id) is not None,
            writable=lambda: self._read_project(project_id) is not None,
            missing=f"Project '{project_id}' not found.",
        )

    def _project_chat_dir(self, project_id: str) -> Path:
        return self.root / "project-chats" / check_id(project_id)

    @staticmethod
    def _t_load(owner: _Owner) -> list[dict[str, Any]]:
        if not owner.chat.exists():
            return []
        entries: list[dict[str, Any]] = json.loads(owner.chat.read_text(encoding="utf-8"))
        if entries and any("seq" not in e for e in entries):  # transcript written before seq existed
            for i, e in enumerate(entries, start=1):
                e["seq"] = i
        return hide_staged(entries)

    def _t_require(self, owner: _Owner) -> None:
        if not owner.exists():
            raise StoreError(owner.missing)

    def _t_check_generation(self, owner: _Owner, generation: str | None) -> None:
        """ConflictError unless the transcript has `generation` (no check for None; ADR-0127). Under the lock."""
        if generation is not None and self._t_stored_generation(owner) != generation:
            raise ConflictError(f"{owner.missing.removesuffix(' not found.')}: the transcript changed (generation).")

    def _t_append(self, owner: _Owner, entries: list[dict[str, Any]], generation: str | None = None) -> None:
        with self._locked(owner.lock):
            self._t_require(owner)
            self._t_check_generation(owner, generation)
            history = self._t_load(owner)
            seq = history[-1]["seq"] if history else 0
            for e in strip_seq(entries):
                seq += 1
                e["seq"] = seq
                history.append(e)
            _atomic_write(owner.chat, history)

    @staticmethod
    def _t_stored_generation(owner: _Owner) -> str | None:
        return owner.generation.read_text(encoding="utf-8").strip() if owner.generation.exists() else None

    @staticmethod
    def _t_new_generation(owner: _Owner) -> None:
        if owner.writable():
            owner.generation.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(owner.generation, new_token())

    def _t_generation(self, owner: _Owner) -> str:
        with self._locked(owner.lock):
            if not owner.exists():
                return ""
            stored = self._t_stored_generation(owner)
            if stored is None:  # a transcript from before generations existed (or a new one)
                self._t_new_generation(owner)
                stored = self._t_stored_generation(owner)
            return stored or ""

    def _t_compact(self, owner: _Owner, upto_seq: int, summary: dict[str, Any], generation: str | None) -> bool:
        with self._locked(owner.lock):
            if generation is not None and self._t_stored_generation(owner) != generation:
                return False
            entries = self._t_load(owner)
            if not any(e["seq"] == upto_seq for e in entries):
                return False
            tail = [e for e in entries if e["seq"] > upto_seq]
            entry = {**strip_seq([summary])[0], "seq": upto_seq}
            _atomic_write(owner.chat, [entry, *tail])
            return True

    def _t_clear(self, owner: _Owner, generation: str | None = None) -> None:
        with self._locked(owner.lock):
            self._t_require(owner)
            self._t_check_generation(owner, generation)
            self._t_new_generation(owner)
            _atomic_write(owner.chat, [])

    def load_chat(self, diagram_id: str) -> list[dict[str, Any]]:
        return self._t_load(self._diagram_owner(diagram_id))

    def append_chat(self, diagram_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> None:
        self._t_append(self._diagram_owner(diagram_id), entries, generation)

    def chat_generation(self, diagram_id: str) -> str:
        return self._t_generation(self._diagram_owner(diagram_id))

    def compact_chat(
        self, diagram_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        return self._t_compact(self._diagram_owner(diagram_id), upto_seq, summary, generation)

    def clear_chat(self, diagram_id: str, generation: str | None = None) -> None:
        self._t_clear(self._diagram_owner(diagram_id), generation)

    def import_chat(self, diagram_id: str, entries: list[dict[str, Any]]) -> None:
        owner = self._diagram_owner(diagram_id)
        with self._locked(owner.lock):
            self._t_require(owner)
            self._t_new_generation(owner)
            _atomic_write(owner.chat, sorted(entries, key=lambda e: e["seq"]))

    def load_project_chat(self, project_id: str) -> list[dict[str, Any]]:
        return self._t_load(self._project_owner(project_id))

    def append_project_chat(
        self, project_id: str, entries: list[dict[str, Any]], generation: str | None = None
    ) -> None:
        self._t_append(self._project_owner(project_id), entries, generation)

    def project_chat_generation(self, project_id: str) -> str:
        return self._t_generation(self._project_owner(project_id))

    def compact_project_chat(
        self, project_id: str, upto_seq: int, summary: dict[str, Any], generation: str | None = None
    ) -> bool:
        return self._t_compact(self._project_owner(project_id), upto_seq, summary, generation)

    def clear_project_chat(self, project_id: str, generation: str | None = None) -> None:
        self._t_clear(self._project_owner(project_id), generation)

    def _remove_project_chat(self, project_id: str) -> None:
        directory = self._project_chat_dir(project_id)
        if directory.exists():
            shutil.rmtree(directory)

    # ---- projects ---------------------------------------------------------

    def _project_path(self, project_id: str) -> Path:
        return self.root / "projects" / f"{check_id(project_id)}.json"

    def _read_project(self, project_id: str) -> tuple[Project, str] | None:
        try:
            text = self._project_path(project_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if not text.strip():  # claimed by a concurrent create_project that has not written yet
            return None
        data = json.loads(text)
        revision = str(data.pop("revision", LEGACY_REVISION))
        return Project.model_validate(data), revision

    def _write_project(self, project: Project) -> str:
        revision = new_token()
        _atomic_write(self._project_path(project.id), {**project.model_dump(mode="json"), "revision": revision})
        return revision

    def list_projects(self) -> list[tuple[Project, str]]:
        out = []
        for path in sorted((self.root / "projects").glob("*.json")):
            if path.name.startswith("."):
                continue  # a temporary file of a write in progress
            found = self._read_project(path.stem)
            if found is not None:
                out.append(found)
        return out

    def read_project(self, project_id: str) -> tuple[Project, str]:
        found = self._read_project(project_id)
        if found is None:
            raise StoreError(f"Project '{project_id}' not found.")
        return found

    def create_project(self, project: Project) -> str:
        path = self._project_path(project.id)
        with self._locked(f"project:{project.id}"):
            if not _claim(path):
                raise StoreError(f"Project '{project.id}' already exists.")
            try:
                self._remove_project_chat(project.id)  # a leftover of a deleted project is never inherited
                return self._write_project(project)
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    def commit_project(self, project: Project, expected: str) -> str:
        with self._locked(f"project:{project.id}"):
            current = self._read_project(project.id)
            if current is None:
                raise ConflictError(f"Project '{project.id}' does not exist (any more).")
            if current[1] != expected:
                raise ConflictError(f"Project '{project.id}' was changed by another request.")
            return self._write_project(project)

    def delete_project(self, project_id: str, expected: str) -> None:
        with self._locked(f"project:{project_id}"):
            current = self._read_project(project_id)
            if current is None:
                raise ConflictError(f"Project '{project_id}' does not exist (any more).")
            if current[1] != expected:
                raise ConflictError(f"Project '{project_id}' was changed by another request.")
            self._project_path(project_id).unlink()
            self._remove_project_chat(project_id)

    # ---- keyed documents (workspaces, users) ---------------------------------

    def _document_path(self, kind: str, key: str) -> Path:
        return self.root / f"{kind}s" / f"{check_document(kind, key)}.json"

    def _read_document(self, kind: str, key: str) -> tuple[dict[str, Any], str] | None:
        try:
            text = self._document_path(kind, key).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if not text.strip():  # claimed by a concurrent create that has not written yet
            return None
        data = json.loads(text)
        return data["document"], str(data["revision"])

    def _write_document(self, kind: str, key: str, body: dict[str, Any]) -> str:
        revision = new_token()
        _atomic_write(self._document_path(kind, key), {"document": body, "revision": revision})
        return revision

    def list_documents(self, kind: str) -> list[tuple[str, dict[str, Any], str]]:
        check_document(kind, "probe")
        out = []
        for path in sorted((self.root / f"{kind}s").glob("*.json")):
            if path.name.startswith("."):
                continue  # a temporary file of a write in progress
            found = self._read_document(kind, path.stem)
            if found is not None:
                out.append((path.stem, *found))
        return out

    def read_document(self, kind: str, key: str) -> tuple[dict[str, Any], str]:
        found = self._read_document(kind, key)
        if found is None:
            raise missing_document(kind, key)
        return found

    def create_document(self, kind: str, key: str, body: dict[str, Any]) -> str:
        path = self._document_path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked(f"{kind}:{key}"):
            if not _claim(path):
                raise StoreError(f"{kind.capitalize()} '{key}' already exists.")
            try:
                return self._write_document(kind, key, body)
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    def commit_document(self, kind: str, key: str, body: dict[str, Any], expected: str) -> str:
        with self._locked(f"{kind}:{key}"):
            current = self._read_document(kind, key)
            if current is None:
                raise ConflictError(f"{kind.capitalize()} '{key}' does not exist (any more).")
            if current[1] != expected:
                raise ConflictError(f"{kind.capitalize()} '{key}' was changed by another request.")
            return self._write_document(kind, key, body)

    def delete_document(self, kind: str, key: str, expected: str) -> None:
        with self._locked(f"{kind}:{key}"):
            current = self._read_document(kind, key)
            if current is None:
                raise ConflictError(f"{kind.capitalize()} '{key}' does not exist (any more).")
            if current[1] != expected:
                raise ConflictError(f"{kind.capitalize()} '{key}' was changed by another request.")
            self._document_path(kind, key).unlink()
