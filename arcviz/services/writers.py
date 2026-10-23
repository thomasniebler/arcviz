"""Settling the pending locator tokens of writers that died (#189, ADR-0133; spec:
docs/architecture/specs/follow-ups.md).

A placement lists its target project in the diagram's locator entry with a pending token before it writes the
membership (`locations.add_location`, the M22 invariant) and clears the token afterwards. A writer that crashes in
between leaves its token, and a pending token keeps a create of that id failing closed and the project listed for good.
A token may only be cleared once its writer can provably write nothing more, without comparing clocks of replicas:

- Every token names its writer incarnation (`locations.Writer.boot`, new on every process start).
- Each process keeps a keyed document of kind `writer` per incarnation (`Heartbeat`): created at start-up with state
  `alive`, renewed every `heartbeat` seconds by a write conditional on its own last revision (so the revision moves),
  set to `stopped` on a clean shutdown with no placement in flight.
- Another process (`Reaper`, run by every heartbeat; `settle_pending` for the operator) settles the tokens of an
  incarnation that is `stopped` or `dead`, and declares one `dead` only after it saw the *same* revision for a whole
  lease (`lease` = 10 heartbeats) on its *own* monotonic clock, with a write conditional on exactly that revision. That
  write is the fence: the dead writer's next renewal fails, it marks itself fenced and issues no tokens any more (and
  `/readyz` fails, so it is restarted). A writer that could not renew for half a lease stops issuing tokens by itself.
- Settling removes the tokens, then drops a project from the entry only if it does not hold the diagram (read again,
  `remove_location`); a failed read settles nothing (fail closed). A settled incarnation's document is dropped only a
  lease after a pass found nothing left of it, so a placement paused before the fence still finds it known. Tokens from before M24 name no incarnation and are
  settled only by `settle_pending(everything=True)` (`arcviz-migrate --settle-pending --all`) in a maintenance window,
  which refuses when any heartbeat moves during its observation.

A reaper judges others only while its own heartbeat is fresh and has watched without a gap (after an outage or a
pause every writer gets a whole lease again); it runs on its own thread. A fenced process fails /healthz and /readyz.

Residual risk (documented in ADR-0133): a membership write that lands more than half a lease after the writer's last
check inside it (`Writer.check`), while it was fenced and its target dropped, stays unlisted until its settle re-lists.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..envconfig import Env
from ..ports import ConflictError, DocumentRepositoryPort, ProjectRepositoryPort, StoreError, is_missing
from .locations import (
    LOCATION,
    WRITER,
    Writer,
    _update,
    boot_of,
    new_incarnation,
    remove_location,
    settle_after_failure,
    writer_of,
)

DEFAULT_HEARTBEAT = 30.0  # seconds between two renewals of a writer's document
LEASE_BEATS = 10  # a writer is declared dead after its document stood still for this many heartbeats
STOP_WAIT = 5.0  # seconds a clean shutdown waits for placements in flight before it claims to have stopped
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriterConfig:
    heartbeat: float = DEFAULT_HEARTBEAT

    @property
    def lease(self) -> float:
        return LEASE_BEATS * self.heartbeat

    @classmethod
    def from_env(cls, environ: Any = None) -> WriterConfig:
        env = Env(environ)
        config = cls(
            heartbeat=env.number("ARCVIZ_WRITER_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT, minimum=1.0, unit="seconds")
        )
        env.check()
        return config


@dataclass
class ReapReport:
    stopped: list[str] = field(default_factory=list)  # incarnations that stopped cleanly, settled
    dead: list[str] = field(default_factory=list)  # incarnations declared dead now (or before), settled
    alive: list[str] = field(default_factory=list)  # incarnations whose heartbeat moved (or not long enough seen)
    refused: list[str] = field(default_factory=list)  # settle_pending(everything): live writers that stopped it
    settled: int = 0  # pending tokens cleared
    legacy: int = 0  # tokens from before M24 (no incarnation) or of unknown incarnations, left or (everything) cleared


def _state(body: dict[str, Any]) -> str:
    state = body.get("state")
    return state if state in ("alive", "stopped", "dead") else "alive"


def settle_tokens(
    documents: DocumentRepositoryPort, projects: ProjectRepositoryPort, settles: Callable[[str], bool]
) -> tuple[list[str], bool]:
    """Clear every pending token for which `settles(token)` is true, in every locator entry (one listing), then drop
    each of those projects from its entry if it does not hold the diagram (read again; failures keep it). Returns (the
    tokens cleared, complete): not complete when the entries could not be listed or one could not be written (nothing
    is assumed about it)."""
    cleared: list[str] = []
    complete = True
    try:
        entries = documents.list_documents(LOCATION)
    except StoreError:
        logger.warning("Locator entries could not be listed; pending tokens are settled later", exc_info=True)
        return [], False
    for diagram_id, body, _ in entries:
        raw = body.get("pending")
        if not isinstance(raw, dict):
            continue
        targets = {p for p, tokens in raw.items() if isinstance(tokens, list) and any(settles(t) for t in tokens)}
        if not targets:
            continue
        removed: list[str] = []

        def drop(entry: Any, targets: set[str] = targets, removed: list[str] = removed) -> Any:
            if entry is None:
                return None
            gone = [t for p, ts in entry.pending.items() if p in targets for t in ts if settles(t)]
            if not gone:
                return None
            removed[:] = gone
            return entry.projects, {p: [t for t in ts if t not in gone] for p, ts in entry.pending.items()}

        try:
            _update(documents, diagram_id, drop)
        except StoreError:
            logger.warning("Pending tokens of diagram '%s' could not be settled now", diagram_id, exc_info=True)
            complete = False
            continue
        cleared += removed
        for project_id in sorted(targets):
            remove_location(documents, projects, diagram_id, project_id)  # only if it does not hold the diagram
    return cleared, complete


def _forget(documents: DocumentRepositoryPort, boot: str) -> None:
    """Drop a settled incarnation's document (conditional; it may be gone or changed already)."""
    try:
        _, revision = documents.read_document(WRITER, boot)
        documents.delete_document(WRITER, boot, revision)
    except StoreError:
        logger.debug("Writer document '%s' stays for the next pass", boot, exc_info=True)


def _fence(documents: DocumentRepositoryPort, boot: str, body: dict[str, Any], revision: str) -> bool:
    """Declare an incarnation dead, conditional on the revision that stood still; False if it moved meanwhile."""
    try:
        documents.commit_document(WRITER, boot, {**body, "state": "dead"}, revision)
    except ConflictError:
        return False
    return True


class Reaper:
    """Watches the other writers' documents and settles the tokens of those that stopped or died (see the module
    docstring). `observe()` is one pass; `clock` is this process's monotonic clock, the only clock involved."""

    def __init__(
        self,
        documents: DocumentRepositoryPort,
        projects: ProjectRepositoryPort,
        lease: float,
        clock: Callable[[], float] = time.monotonic,
        own: str | None = None,
    ) -> None:
        self.documents, self.projects, self.lease, self.clock, self.own = documents, projects, lease, clock, own
        self.seen: dict[str, tuple[str, float]] = {}  # incarnation -> (revision, when first seen with it)
        self.settled: dict[str, float] = {}  # stopped/dead incarnation -> when its tokens were first all settled
        self.last: float | None = None  # when this reaper last watched while healthy

    def observe(self, healthy: bool = True) -> ReapReport:
        """One pass. `healthy`: this process's own heartbeat is fresh. A reaper judges others only while it is healthy
        itself and has watched without a gap: after its own outage or pause (no healthy pass for half a lease) every
        writer gets a whole lease again, so a store outage that stopped every heartbeat never fences them all."""
        report = ReapReport()
        now = self.clock()
        if not healthy or self.last is None or now - self.last > self.lease / 2:
            self.seen.clear()
        if not healthy:
            self.last = None
            return report
        try:
            writers = self.documents.list_documents(WRITER)
        except StoreError:
            logger.warning("Writer documents could not be listed; nothing settled now", exc_info=True)
            self.seen.clear()
            self.last = None
            return report
        self.last = now
        current = {boot for boot, _, _ in writers}
        for known in (self.seen, self.settled):
            for boot in list(known):
                if boot not in current:
                    del known[boot]
        first_pass: list[str] = []  # stopped/dead incarnations this process has not settled yet
        final_pass: list[str] = []  # settled a lease ago: one more pass, then forgotten if nothing was left
        for boot, body, revision in writers:
            if boot == self.own:
                continue
            state = _state(body)
            if state == "alive":
                previous = self.seen.get(boot)
                if previous is None or previous[0] != revision:
                    self.seen[boot] = (revision, now)
                    report.alive.append(boot)
                    continue
                if now - previous[1] < self.lease or not _fence(self.documents, boot, body, revision):
                    report.alive.append(boot)
                    continue
                logger.warning("Writer %s stood still for a lease and is declared dead; its tokens are settled", boot)
                state = "dead"
            self.seen.pop(boot, None)
            (report.stopped if state == "stopped" else report.dead).append(boot)
            settled_at = self.settled.get(boot)
            if settled_at is None:
                first_pass.append(boot)
            elif now - settled_at >= self.lease:
                # kept for a lease: a placement of that writer paused after taking its token (before the fence) may
                # still publish it, and must find a known incarnation, not an unknown one
                final_pass.append(boot)
        due = set(first_pass) | set(final_pass)
        if not due:
            return report
        cleared, complete = settle_tokens(self.documents, self.projects, lambda t: boot_of(t) in due)
        report.settled = len(cleared)
        if not complete:
            return report  # retried by the next pass
        late = {boot_of(t) for t in cleared}
        for boot in first_pass:
            self.settled[boot] = now
        for boot in final_pass:
            if boot in late:
                self.settled[boot] = now  # a late token turned up: another lease
            else:
                self.settled.pop(boot, None)
                _forget(self.documents, boot)
        return report


class Heartbeat:
    """This process's writer document for one store: `register()` at start-up, `beat()` every `heartbeat` seconds
    (plus retrying abandoned tokens), a reaper pass every `heartbeat` seconds on a thread of its own (a slow scan of the
    locator entries never delays a beat), `close()` at shutdown. `tick()` runs one beat and one pass (tests)."""

    def __init__(
        self,
        documents: DocumentRepositoryPort,
        projects: ProjectRepositoryPort,
        config: WriterConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        writer: Writer | None = None,
    ) -> None:
        self.documents, self.projects, self.clock = documents, projects, clock
        self.config = config or WriterConfig()
        self.writer = writer or writer_of(documents)
        self.reaper = Reaper(documents, projects, self.config.lease, clock, own=self.writer.boot)
        self.revision: str | None = None
        self.beats = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _body(self, state: str) -> dict[str, Any]:
        return {"boot": self.writer.boot, "state": state, "beats": self.beats}

    def register(self) -> None:
        """Create this incarnation's document. Until it exists the writer's tokens are not tied to a heartbeat (they
        are settled only by the operator command); a failure is logged and retried by the next beat."""
        try:
            self.revision = self.documents.create_document(WRITER, self.writer.boot, self._body("alive"))
        except StoreError:
            # an earlier create may have been written although its answer was lost: adopt the document if it exists
            # (its boot id is this process's own), or stop placing if somebody fenced it meanwhile
            try:
                body, revision = self.documents.read_document(WRITER, self.writer.boot)
            except StoreError:
                logger.warning("The writer heartbeat could not be registered now; retried", exc_info=True)
                return
            if _state(body) != "alive":
                self.writer.fenced = True
                logger.error("This process's writer document was fenced before it registered; restart it")
                return
            self.revision = revision
        self.writer.lease = self.config.lease
        self.writer.renewed()

    def beat(self) -> None:
        """Renew the document, conditional on its last revision. A conflict means another process declared this one
        dead (or stopped): the writer is fenced for good."""
        if self.revision is None:
            self.register()
            return
        if self.writer.fenced:
            return
        self.beats += 1
        try:
            self.revision = self.documents.commit_document(WRITER, self.writer.boot, self._body("alive"), self.revision)
        except ConflictError:
            self.writer.fenced = True
            logger.error(
                "Another replica declared this process dead (its heartbeat stood still for a lease); it places no "
                "diagrams any more and reports unhealthy until it is restarted"
            )
            return
        except StoreError as exc:
            if is_missing(exc):  # deleted: only after it was settled as dead or stopped
                self.writer.fenced = True
            logger.warning("The writer heartbeat could not be renewed now", exc_info=True)
            return
        self.writer.renewed()

    def retry_abandoned(self) -> None:
        for token, (diagram_id, project_id) in list(self.writer.abandoned.items()):
            settle_after_failure(self.documents, self.projects, diagram_id, project_id, token, writer=self.writer)

    def _beat_and_retry(self) -> None:
        self.beat()
        self.retry_abandoned()

    def reap(self) -> ReapReport:
        """One reaper pass; it judges others only while this writer's own heartbeat is fresh."""
        healthy = self.revision is not None and not self.writer.fenced and self.writer.fresh()
        return self.reaper.observe(healthy=healthy)

    def tick(self) -> None:
        self.beat()
        self.retry_abandoned()
        self.reap()

    def start(self) -> None:
        """Register and start the beat and reaper threads. A writer closed by an earlier lifespan is fenced; this one
        is a new incarnation (a new boot id), so a second lifespan in the same process can place diagrams again."""
        if self.writer.fenced:
            self.writer = new_incarnation(self.documents, self.writer.clock)
            self.reaper = Reaper(self.documents, self.projects, self.config.lease, self.clock, own=self.writer.boot)
            self.revision, self.beats = None, 0
        self.register()
        self._stop.clear()

        def every_beat(name: str, work: Callable[[], Any]) -> threading.Thread:
            def run() -> None:
                while not self._stop.wait(self.config.heartbeat):
                    try:
                        work()
                    except Exception:  # never let the thread die: the next round tries again
                        logger.exception("The writer %s failed; retried", name)

            return threading.Thread(target=run, name=f"arcviz-writer-{name}", daemon=True)

        self._threads = [
            every_beat("heartbeat", self._beat_and_retry),
            every_beat("reaper", self.reap),
        ]
        for thread in self._threads:
            thread.start()

    def close(self, wait: float = STOP_WAIT) -> None:
        """Stop issuing tokens, wait up to `wait` seconds for placements in flight, then mark the document `stopped`
        (its leftover tokens are settled by the others at once). With placements still in flight it stays `alive`:
        the lease decides. This incarnation stays fenced; a later `start()` begins a new one."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = []
        self.writer.fenced = True
        deadline = time.monotonic() + wait
        while self.writer.in_flight() and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.writer.in_flight() or self.revision is None:
            return
        try:
            self.revision = self.documents.commit_document(
                WRITER, self.writer.boot, self._body("stopped"), self.revision
            )
        except StoreError:
            logger.warning("The writer could not record its clean stop; the lease decides", exc_info=True)

    def ready(self) -> str | None:
        """None while this process may place diagrams, else why not (for /readyz and /healthz: a fenced process is
        restarted by the orchestrator's liveness probe and comes back as a new incarnation)."""
        if self.writer.fenced and not self._stop.is_set():
            return "this process was declared dead by another replica; restart it"
        return None


def settle_pending(
    documents: DocumentRepositoryPort,
    projects: ProjectRepositoryPort,
    wait: float,
    lease: float,
    everything: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ReapReport:
    """The operator command (`arcviz-migrate --settle-pending`): watch every writer document for `wait` seconds (this
    process's clock), then settle the tokens of incarnations that stopped, were declared dead, or stood still the
    whole time when `wait` is at least a lease. With `everything` (a maintenance window: the operator stopped every
    replica) it refuses, settling nothing, if any heartbeat moved or a new writer appeared; otherwise it fences every
    writer that stood still and also settles tokens from before M24 and of unknown incarnations."""
    report = ReapReport()
    first = {boot: (body, revision) for boot, body, revision in documents.list_documents(WRITER)}
    started = clock()
    sleep(wait)
    waited = clock() - started
    second = {boot: (body, revision) for boot, body, revision in documents.list_documents(WRITER)}
    moving = [
        boot
        for boot, (body, revision) in second.items()
        if _state(body) == "alive" and (boot not in first or first[boot][1] != revision)
    ]
    if everything and moving:
        report.refused = sorted(moving)
        return report
    settled: set[str] = set()
    for boot, (body, revision) in sorted(second.items()):
        state = _state(body)
        if state == "alive":
            if boot in moving or (waited < lease and not everything) or not _fence(documents, boot, body, revision):
                report.alive.append(boot)
                continue
            state = "dead"
        (report.stopped if state == "stopped" else report.dead).append(boot)
        settled.add(boot)
    known = set(second)

    def unknown(token: str) -> bool:
        boot = boot_of(token)
        return boot is None or boot not in known

    def settles(token: str) -> bool:
        return boot_of(token) in settled or (everything and unknown(token))

    report.legacy = sum(
        1
        for _, body, _ in documents.list_documents(LOCATION)
        for tokens in (body.get("pending") or {}).values()
        if isinstance(tokens, list)
        for token in tokens
        if isinstance(token, str) and unknown(token)
    )
    cleared, _ = settle_tokens(documents, projects, settles)
    report.settled = len(cleared)
    return report  # the writer documents stay: the reapers drop them a lease after their last token was settled
