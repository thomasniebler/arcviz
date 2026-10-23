"""The diagram→project locator (spec: docs/architecture/specs/scale-and-robustness.md, ADR-0120, #181).

A diagram document does not know its project (ADR-0051); the membership lives in the project document. Finding it
used to read every project. A keyed document of kind `location` per diagram (key: the diagram id) lists the projects
that may hold it instead: point reads. It is a hint, never a grant: every project it lists is read and must hold the
diagram (the newest membership wins, like the repair), and the caller's role there is checked by the guard afterwards.

**Invariant**: the entry lists every project that holds a membership of the id (a superset). A writer lists a project
*before* it adds the membership, with a `pending` token of its own (`add_location`), and clears its token once the
membership is written, or once a re-read shows whether its write happened (`settle_location`). `remove_location`
drops a project only when no writer has it pending and it does not hold the id (read again). No clock is involved, so
skew between replicas cannot break it. So a delete removes the id from every project the entry lists, and a
create of an id first clears every project the entry lists (`ProjectService.vacate`): a stale membership left by a
crashed delete never shows a new diagram of the same id in somebody else's project.

**Tokens of writers that died** (#189, ADR-0133): a token names the writer incarnation that issued it
(`<boot>.<random>`, `Writer`, one per process and store). A crash leaves its tokens behind; `services/writers.py`
settles them once that incarnation provably wrote nothing more: it stopped cleanly, or another process saw its
heartbeat document unchanged for a whole lease on its own monotonic clock and fenced it with a conditional write.
Tokens from before M24 name no incarnation and are only settled by `arcviz-migrate --settle-pending --all`.

**Failed reads are never "absent"**: only the store's definite "not found" counts as a missing project; any other
failure (a throttle, a timeout) is raised, so a decision that relies on a project not holding the id is never taken on
a read that failed.

A missing or stale entry (data from before M22, an older replica) falls back to the scan only for a diagram that
exists. Scans are single-flight per id and process, and a scan's holder ids are reused for `SCAN_COOLDOWN` (the
projects are read again, so roles are always current); a scan that finds holders heals the entry, one that finds none
(an orphan in no project) records `scanned_at`, and within `SCAN_TTL` the orphan answers "not found" without another
scan (placing it clears that). A failed write records nothing. The repair (`repair_locations`, at the end of
`ProjectService.reconcile`: every start, `arcviz-migrate`) backfills.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from ..ports import (
    MUTATION_ATTEMPTS,
    ConflictError,
    DocumentRepositoryPort,
    ProjectRepositoryPort,
    StoreError,
    check_id,
    is_missing,
)
from ..projects import Membership, Project

LOCATION = "location"
WRITER = "writer"  # keyed documents: one per writer incarnation, kept alive by its heartbeat (#189, ADR-0133)
_BOOT = re.compile(r"[0-9a-f]{16}")
SCAN_TTL = timedelta(minutes=10)  # an orphan (exists, in no project) is scanned again at most this often (a cost bound)
SCAN_COOLDOWN = 5.0  # seconds a process reuses the holder ids a scan found (single flight plus this cooldown)
logger = logging.getLogger(__name__)


class Writer:
    """This process's incarnation as a writer of pending tokens for one store (#189, ADR-0133). `boot` is new on every
    start. `fenced`: declared stopped or dead (by its own shutdown or by another process), so it issues no tokens any
    more. Once a heartbeat registered it (`lease` set), it also issues none while its last successful renewal is older
    than half a lease on its own clock: another process may be about to declare it dead. `open` holds the tokens of
    placements in flight, `abandoned` those of finished placements whose settle failed (the heartbeat retries them)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.boot = secrets.token_hex(8)
        self.clock = clock
        self.lease: float | None = None  # set when a heartbeat registers this writer
        self.renewed_at = clock()
        self.fenced = False
        self.open: set[str] = set()
        self.abandoned: dict[str, tuple[str, str]] = {}  # token -> (diagram, project)
        self._lock = threading.Lock()

    def check(self) -> None:
        """Raise StoreError unless this writer may write now: not fenced, and (once registered) renewed within half a
        lease on its own clock. Called when a token is issued and again right before the membership write (#189)."""
        if self.fenced:
            raise StoreError(
                "This process was declared stopped or dead (another replica fenced it, or it is shutting down) "
                "and places no diagrams any more; restart it."
            )
        if not self.fresh():
            raise StoreError("This process could not renew its writer heartbeat; placing diagrams waits until it can.")

    def fresh(self) -> bool:
        """Whether this writer renewed recently enough to place diagrams (always, before a heartbeat registered it)."""
        return self.lease is None or self.clock() - self.renewed_at <= self.lease / 2

    def token(self) -> str:
        with self._lock:
            self.check()
            token = f"{self.boot}.{secrets.token_hex(6)}"
            self.open.add(token)
            return token

    def renewed(self) -> None:
        with self._lock:
            self.renewed_at = self.clock()

    def done(self, token: str) -> None:
        with self._lock:
            self.open.discard(token)
            self.abandoned.pop(token, None)

    def abandon(self, token: str, diagram_id: str, project_id: str) -> None:
        """The placement ended, but its token could not be settled: the heartbeat retries it."""
        with self._lock:
            self.open.discard(token)
            self.abandoned[token] = (diagram_id, project_id)

    def in_flight(self) -> int:
        with self._lock:
            return len(self.open)


def boot_of(token: str) -> str | None:
    """The writer incarnation that issued a token; None for a token from before M24 (it names none)."""
    boot, sep, _ = token.partition(".")
    return boot if sep and _BOOT.fullmatch(boot) else None


_WRITERS: weakref.WeakKeyDictionary[Any, Writer] = weakref.WeakKeyDictionary()
_WRITERS_LOCK = threading.Lock()


def writer_of(documents: DocumentRepositoryPort) -> Writer:
    """This process's writer for the store (created on first use)."""
    with _WRITERS_LOCK:
        writer = _WRITERS.get(documents)
        if writer is None:
            writer = _WRITERS[documents] = Writer()
        return writer


def new_incarnation(documents: DocumentRepositoryPort, clock: Callable[[], float] = time.monotonic) -> Writer:
    """A new writer incarnation for the store (a new lifespan after the previous one closed and fenced its writer)."""
    with _WRITERS_LOCK:
        writer = _WRITERS[documents] = Writer(clock=clock)
        return writer


@contextmanager
def writing_as(documents: DocumentRepositoryPort, writer: Writer) -> Iterator[Writer]:
    """Issue and settle tokens for `documents` as `writer` (tests simulate several processes on one store)."""
    with _WRITERS_LOCK:
        previous = _WRITERS.get(documents)
        _WRITERS[documents] = writer
    try:
        yield writer
    finally:
        with _WRITERS_LOCK:
            if previous is None:
                _WRITERS.pop(documents, None)
            else:
                _WRITERS[documents] = previous


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(offset: timedelta = timedelta(0)) -> str:
    return (_now() + offset).isoformat(timespec="microseconds")


class Located(NamedTuple):
    """Where a diagram is: the project (as just read), its membership there and the project's revision."""

    project: Project
    member: Membership
    revision: str


@dataclass
class Entry:
    """A diagram's locator entry as read."""

    projects: list[str]  # every project that may hold a membership of the id (a superset)
    pending: dict[str, list[str]] = field(default_factory=dict)  # project -> tokens of writers about to add it
    scanned_at: str | None = None  # when a scan last found the diagram in no project (an orphan; a cost bound only)
    revision: str = ""


def _body(
    entry_projects: list[str], pending: dict[str, list[str]], diagram_id: str, scanned_at: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "diagram": diagram_id,
        "projects": entry_projects,
        "pending": {p: tokens for p, tokens in pending.items() if tokens and p in entry_projects},
    }
    if scanned_at is not None:
        body["scanned_at"] = scanned_at
    return body


def read_location(documents: DocumentRepositoryPort, diagram_id: str) -> Entry | None:
    """The diagram's entry, None when it has none. Any other read failure is raised."""
    try:
        body, revision = documents.read_document(LOCATION, diagram_id)
    except StoreError as exc:
        if is_missing(exc):
            return None
        raise
    names = body.get("projects")
    projects = [p for p in names if isinstance(p, str) and p] if isinstance(names, list) else []
    raw = body.get("pending")
    pending = (
        {p: [t for t in v if isinstance(t, str)] for p, v in raw.items() if isinstance(v, list)}
        if isinstance(raw, dict)
        else {}
    )
    scanned = body.get("scanned_at")
    return Entry(projects, pending, scanned if isinstance(scanned, str) else None, revision)


Change = Callable[[Entry | None], tuple[list[str], dict[str, list[str]]] | None]


def _update(documents: DocumentRepositoryPort, diagram_id: str, change: Change) -> None:
    """Conditional read-modify-write of the entry, retried: `change` gets the entry (None: none) and returns the new
    (projects, pending), None for nothing to write; no projects deletes the entry. ConflictError after
    MUTATION_ATTEMPTS; any other store failure is raised."""
    for _ in range(MUTATION_ATTEMPTS):
        entry = read_location(documents, diagram_id)
        outcome = change(entry)
        if outcome is None or (entry is None and not outcome[0]):
            return
        projects, pending = outcome
        try:
            if entry is None:
                documents.create_document(LOCATION, diagram_id, _body(projects, pending, diagram_id))
            elif projects:
                documents.commit_document(LOCATION, diagram_id, _body(projects, pending, diagram_id), entry.revision)
            else:
                documents.delete_document(LOCATION, diagram_id, entry.revision)
            return
        except ConflictError:
            continue
        except StoreError as exc:
            if entry is None and "already exists" in str(exc):
                continue  # created concurrently
            raise
    raise ConflictError(f"The location of diagram '{diagram_id}' kept changing; try again.")


def add_location(documents: DocumentRepositoryPort, diagram_id: str, project_id: str) -> str:
    """List `project_id` in the entry *before* its membership is added (the invariant), marked pending with a fresh
    token of this writer; returns the token for `settle_location`. Always writes (the revision changes, so a concurrent
    `remove_location` re-checks). Clears an orphan's `scanned_at`. ConflictError after MUTATION_ATTEMPTS. The token
    names this process's writer incarnation; a fenced writer raises StoreError before anything is written."""
    writer = writer_of(documents)
    token = writer.token()

    def add(entry: Entry | None) -> tuple[list[str], dict[str, list[str]]]:
        projects = [p for p in (entry.projects if entry else []) if p != project_id]
        pending = dict(entry.pending if entry else {})
        pending[project_id] = [*pending.get(project_id, []), token]
        return [*projects, project_id], pending

    try:
        _update(documents, diagram_id, add)
    except BaseException:
        writer.abandon(token, diagram_id, project_id)  # it may have been written: the heartbeat settles it
        raise
    return token


def settle_location(
    documents: DocumentRepositoryPort,
    diagram_id: str,
    project_id: str,
    token: str,
    relist: bool = False,
    writer: Writer | None = None,
) -> None:
    """Clear this writer's pending token of `project_id`: after its membership was written (`relist`: and list the
    project again should it have been dropped meanwhile, which only happens after this writer was declared dead while
    it was paused), or after its write failed and a re-read showed whether it happened (`settle_after_failure`). Best
    effort: a token that cannot be cleared now is retried by the writer's heartbeat (`Writer.abandoned`)."""
    writer = writer or writer_of(documents)

    def settle(entry: Entry | None) -> tuple[list[str], dict[str, list[str]]] | None:
        if entry is None:
            return ([project_id], {}) if relist else None
        held = token in entry.pending.get(project_id, [])
        missing = relist and project_id not in entry.projects
        if not held and not missing:
            return None
        rest = [t for t in entry.pending.get(project_id, []) if t != token]
        projects = [*entry.projects, project_id] if missing else entry.projects
        return projects, {**entry.pending, project_id: rest}

    try:
        _update(documents, diagram_id, settle)
    except StoreError:
        writer.abandon(token, diagram_id, project_id)
        logger.debug("The pending mark of '%s' in '%s' stays for now (retried)", diagram_id, project_id, exc_info=True)
        return
    writer.done(token)


def settle_after_failure(
    documents: DocumentRepositoryPort,
    projects: ProjectRepositoryPort,
    diagram_id: str,
    project_id: str,
    token: str,
    writer: Writer | None = None,
) -> None:
    """A placement that raised: its token is cleared once a re-read of the project shows whether the membership was
    written (if it was, the project stays listed); a re-read that fails leaves it to the writer's heartbeat."""
    writer = writer or writer_of(documents)
    try:
        held = read_holder(projects, project_id, diagram_id) is not None
    except Exception:
        writer.abandon(token, diagram_id, project_id)
        return
    settle_location(documents, diagram_id, project_id, token, relist=held, writer=writer)


def remove_location(
    documents: DocumentRepositoryPort,
    projects: ProjectRepositoryPort,
    diagram_id: str,
    project_id: str,
    strict: bool = False,
) -> None:
    """Drop `project_id` from the entry when no writer has it pending and it does not hold the id (read again); the
    entry goes with its last project. Extra names are harmless, so this is best effort unless `strict` (then store
    failures are raised)."""

    def remove(entry: Entry | None) -> tuple[list[str], dict[str, list[str]]] | None:
        if entry is None or project_id not in entry.projects or entry.pending.get(project_id):
            return None
        if read_holder(projects, project_id, diagram_id) is not None:
            return None
        return [p for p in entry.projects if p != project_id], entry.pending

    try:
        _update(documents, diagram_id, remove)
    except StoreError:
        if strict:
            raise
        logger.debug("The location of '%s' keeps '%s' (left to the repair)", diagram_id, project_id, exc_info=True)


def read_holder(projects: ProjectRepositoryPort, project_id: str, diagram_id: str) -> Located | None:
    """The project if it exists and holds the diagram; None only when it does not hold it or the store says it does
    not exist. Every other read failure is raised: never taken as "not a holder"."""
    try:
        project, revision = projects.read_project(project_id)
    except ConflictError:
        raise
    except StoreError as exc:
        if is_missing(exc):
            return None
        raise
    member = project.member(diagram_id)
    return Located(project, member, revision) if member is not None else None


def newest(found: list[Located]) -> Located | None:
    """The membership the system keeps when several projects hold a diagram: the newest `added_at` (ties: the lowest
    project id), the rule of `reconcile` (ADR-0052)."""
    if not found:
        return None
    return max(sorted(found, key=lambda h: h.project.id), key=lambda h: h.member.added_at)


def holders(projects: ProjectRepositoryPort, diagram_id: str) -> list[Located]:
    """Every project holding a membership of the diagram, by reading every project: the system context's answer and
    the fallback of `locate_membership`."""
    found = []
    for project, revision in projects.list_projects():
        member = project.member(diagram_id)
        if member is not None:
            found.append(Located(project, member, revision))
    return found


def membership(projects: ProjectRepositoryPort, diagram_id: str) -> Located | None:
    """The membership of the diagram that counts (the newest, should a repair be pending), by a scan; or None."""
    return newest(holders(projects, diagram_id))


def listed_holders(projects: ProjectRepositoryPort, diagram_id: str, entry: Entry | None) -> list[Located]:
    """The projects the entry lists that hold the diagram (point reads; failed reads are raised)."""
    if entry is None:
        return []
    return [h for p in entry.projects if (h := read_holder(projects, p, diagram_id)) is not None]


class _ScanGate:
    """Single flight per diagram id and process, plus a cooldown: concurrent callers share one scan, and for
    SCAN_COOLDOWN seconds later callers reuse the holder ids it found (and read those projects again)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[str, threading.Lock] = {}
        self._results: dict[str, tuple[float, list[str]]] = {}

    def recent(self, diagram_id: str) -> list[str] | None:
        with self._lock:
            found = self._results.get(diagram_id)
            if found is None or time.monotonic() - found[0] > SCAN_COOLDOWN:
                self._results.pop(diagram_id, None)
                return None
            return found[1]

    def scan(self, diagram_id: str, run: Callable[[], list[str]]) -> list[str]:
        with self._lock:
            flight = self._flights.setdefault(diagram_id, threading.Lock())
        with flight:  # a concurrent caller waits here for the scan in flight
            done = self.recent(diagram_id)
            if done is not None:
                return done
            ids = run()
            with self._lock:
                self._results[diagram_id] = (time.monotonic(), ids)
                self._flights.pop(diagram_id, None)
                if len(self._results) > 1024:
                    oldest = min(self._results, key=lambda k: self._results[k][0])
                    self._results.pop(oldest, None)
            return ids


_GATES: weakref.WeakKeyDictionary[Any, _ScanGate] = weakref.WeakKeyDictionary()
_GATES_LOCK = threading.Lock()


def _gate(documents: DocumentRepositoryPort) -> _ScanGate:
    with _GATES_LOCK:
        gate = _GATES.get(documents)
        if gate is None:
            gate = _GATES[documents] = _ScanGate()
        return gate


def locate_membership(
    documents: DocumentRepositoryPort,
    projects: ProjectRepositoryPort,
    diagram_id: str,
    exists: Callable[[str], bool],
) -> Located | None:
    """The project holding the diagram and its membership (the newest), or None (no such diagram, or in no project).

    Point reads of the entry and of the projects it lists. Only when none of them holds the diagram and the diagram
    exists (`exists`), the projects are scanned: single flight per id and process, the holder ids reused for
    SCAN_COOLDOWN (their projects read again). A scan that finds holders heals the entry (conditional on the revision
    read), one that finds none records `scanned_at` (an orphan), and within SCAN_TTL an orphan answers None without
    another scan (the listed projects are always read first, so the stamp never hides a membership there). Unknown ids
    never cause a scan."""
    try:
        check_id(diagram_id)
    except StoreError:
        return None  # no diagram can have it
    entry = read_location(documents, diagram_id)
    found = newest(listed_holders(projects, diagram_id, entry))
    if found is not None:
        return found
    if entry is not None and entry.scanned_at is not None and entry.scanned_at > _stamp(-SCAN_TTL):
        return None  # no listed project holds it, and a scan a moment ago found none either (placing it clears this)
    if not exists(diagram_id):
        return None

    def run() -> list[str]:
        held = holders(projects, diagram_id)
        _record(documents, diagram_id, entry, held)
        return sorted({h.project.id for h in held})

    ids = _gate(documents).scan(diagram_id, run)
    return newest([h for p in ids if (h := read_holder(projects, p, diagram_id)) is not None])


def _record(documents: DocumentRepositoryPort, diagram_id: str, entry: Entry | None, held: list[Located]) -> None:
    """After a scan, one conditional write on the revision read before it (a writer since then wins): the holders
    found (healed), or for an orphan its `scanned_at`. A failure writes nothing."""
    pending = entry.pending if entry is not None else {}
    current = entry.projects if entry is not None else []
    if held:
        body = _body(sorted({h.project.id for h in held} | set(current)), pending, diagram_id)
    else:  # an orphan; the projects listed stay (they are read first, so the stamp never hides a membership there)
        body = _body(current, pending, diagram_id, _stamp())
    try:
        if entry is None:
            documents.create_document(LOCATION, diagram_id, body)
        else:
            documents.commit_document(LOCATION, diagram_id, body, entry.revision)
    except StoreError:
        logger.debug("The location of diagram '%s' was not recorded (changed meanwhile)", diagram_id, exc_info=True)


def repair_locations(
    documents: DocumentRepositoryPort, projects: ProjectRepositoryPort, exists: Callable[[str], bool]
) -> tuple[int, list[str]]:
    """System context (start-up, `arcviz-migrate`): add every project holding a diagram to its entry (the invariant;
    extra names are kept, a writer between the scan and the write may have listed one) and drop the entries of diagrams
    that no longer exist, that no project holds and that carry no pending token. The repair only ever adds names:
    pending tokens are cleared by their writers, or by `services/writers.py` once their writer provably died (a snapshot
    alone cannot tell a crashed writer from one in flight). Every write is conditional on the revision listed (a conflict means a
    writer maintains it: left alone); a failing item is logged and skipped. Returns (entries written or dropped,
    skipped diagram ids)."""
    held: dict[str, set[str]] = {}
    for project, _ in projects.list_projects():
        for member in project.diagrams:
            held.setdefault(member.diagram, set()).add(project.id)
    fixed, skipped = 0, []
    entries = {}
    for key, _, revision in documents.list_documents(LOCATION):
        entries[key] = revision
    for diagram_id in sorted(set(held) | set(entries)):
        try:
            entry = read_location(documents, diagram_id) if diagram_id in entries else None
            ids = held.get(diagram_id, set())
            if not ids:
                pending = entry is not None and any(entry.pending.values())
                if entry is not None and not pending and not exists(diagram_id):
                    documents.delete_document(LOCATION, diagram_id, entry.revision)
                    fixed += 1
                continue  # placed nowhere: an orphan the membership repair decides about, or a writer in flight
            current = entry.projects if entry is not None else []
            if ids <= set(current):
                continue
            body = _body(
                [*current, *sorted(ids - set(current))], entry.pending if entry is not None else {}, diagram_id
            )
            if entry is None:
                documents.create_document(LOCATION, diagram_id, body)
            else:
                documents.commit_document(LOCATION, diagram_id, body, entry.revision)
            fixed += 1
        except ConflictError:
            continue  # a writer maintains it
        except StoreError:
            logger.warning("The location of diagram '%s' could not be repaired now", diagram_id, exc_info=True)
            skipped.append(diagram_id)
    return fixed, skipped
