"""Live updates and presence: the in-process event bus, the presence registry and the event loop of one stream.

Spec: docs/architecture/specs/collaboration.md, ADR-0095..0098. This module imports nothing from the package but `envconfig`: the
services publish events through `EventSink` and bind streams to callers (`services/live.py`); `server.py` turns the
events of `run_stream` into server-sent events. Publishing is thread-safe (the services run in worker threads), the
streams live on the event loop and use a worker thread only for their periodic checks.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

from .envconfig import Env

logger = logging.getLogger(__name__)

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
QUEUE_SIZE = 256  # pending events per stream; beyond that the stream re-reads the state anyway
PRESENCE_INTERVAL = 0.25  # seconds between two presence snapshots of one stream (trailing edge)
LINGER_SECONDS = 5.0  # an ended stream stays in presence this long, so a reconnect does not flicker
MIN_PRESENCE_TTL = 15.0  # a stream not seen for max(3 x poll, this) is dropped from presence
MAX_SELECTION = 200  # characters of a selected element id
SHARED_CAPACITY = 4096  # shared reads kept (least recently used ones go first)
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_MAX_SECONDS = 900.0


@dataclass(frozen=True)
class LiveConfig:
    poll_seconds: float = DEFAULT_POLL_SECONDS  # revision poll and access re-check of every open stream
    max_seconds: float = DEFAULT_MAX_SECONDS  # lifetime of one stream; the client reconnects

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LiveConfig:
        env = Env(environ)
        config = cls(
            poll_seconds=env.number("ARCVIZ_LIVE_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=0.1, unit="seconds"),
            max_seconds=env.number("ARCVIZ_LIVE_MAX_SECONDS", DEFAULT_MAX_SECONDS, minimum=0.1, unit="seconds"),
        )
        env.check()
        return config

    @property
    def presence_ttl(self) -> float:
        return max(3 * self.poll_seconds, MIN_PRESENCE_TTL)


class EventSink(Protocol):
    """Where the services announce their writes (the hub; nothing in the system context and in tests)."""

    def publish(self, event: dict[str, Any]) -> None: ...


class PresenceChannel(Protocol):
    """Where the replicas of one deployment share presence (Cosmos DB, ADR-0104): one item per project and replica,
    expiring after `ttl` seconds unless written again. Implemented by the store (`presence_channel()`)."""

    def write_presence(self, project: str, replica: str, body: dict[str, Any], ttl: float) -> None: ...

    def read_presence(self, project: str) -> list[dict[str, Any]]:
        """The items of every replica for `project` that have not expired (`body["expires"]`, wall clock)."""
        ...

    def delete_presence(self, project: str, replica: str) -> None: ...


class SharedReadTimeout(Exception):  # noqa: N818 - says what happened
    """A shared read did not finish within the wait (a hanging store); the re-check fails, the stream reconnects."""


class StreamLimit(Exception):  # noqa: N818 - the HTTP answer is the clearest name
    """No stream for this caller now: `scope` "user" (429) or "process" (503, also while shutting down)."""

    def __init__(self, message: str, scope: str):
        super().__init__(message)
        self.scope = scope


@dataclass(frozen=True)
class Watch:
    """What one stream watches, as last checked: the diagram (None for a project stream), its project and workspace,
    the caller's role, and the revisions of the diagram and the project."""

    diagram: str | None
    project: str
    workspace: str | None
    role: str | None
    revision: str | None
    project_revision: str | None


def new_session() -> str:
    return secrets.token_urlsafe(12)


class Subscription:
    """One open stream: its caller, session and watch, a queue on the event loop that opened it, and its selection."""

    def __init__(self, user: str, name: str, session: str, watch: Watch, loop: asyncio.AbstractEventLoop, now: float):
        self.user, self.name, self.session = user, name, session
        self.watch = watch
        self.watch_version = 0  # bumped by every update of `watch`: a re-check based on an older one is dropped (#176)
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(QUEUE_SIZE)
        self.selection: str | None = None
        self.seen = now
        self.ended: float | None = None
        self.overflow = False  # events were dropped: the stream re-checks at once
        self.close_reason: str | None = None  # set (from any thread) to end the stream; never lost to a full queue
        self.checking = False  # a re-check is running: the TTL does not drop the stream meanwhile (for one more TTL)
        self.checking_since = float("-inf")
        self.last_poke = float("-inf")  # when this stream's selection last poked the others
        self.poke_scheduled = False  # a trailing poke for a throttled selection is pending
        self.closed = asyncio.Event()  # set on the loop by `close`: a running re-check is abandoned at once

    @property
    def key(self) -> tuple[str, str]:
        return self.user, self.session

    def matches(self, event: Mapping[str, Any]) -> bool:
        if "diagram" in event:
            return event["diagram"] is not None and event["diagram"] == self.watch.diagram
        if "project" in event:
            return event["project"] == self.watch.project
        if "workspace" in event:
            return event["workspace"] == self.watch.workspace
        return True  # an access event without a target: every stream re-checks

    def deliver(self, item: dict[str, Any]) -> None:
        """On the subscription's loop."""
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.overflow = True

    def close(self, reason: str) -> None:
        """End the stream (any thread): the flag survives a full queue, the wake-up only speeds it up."""
        self.close_reason = self.close_reason or reason
        _call(self, self.deliver, {"type": "_wake"})
        _call(self, self.closed.set)

    async def next(self, timeout: float) -> list[dict[str, Any]]:
        """Everything queued, waiting up to `timeout` seconds for the first item; [] when nothing came."""
        items: list[dict[str, Any]] = []
        if timeout > 0:
            try:
                items.append(await asyncio.wait_for(self.queue.get(), timeout))
            except TimeoutError:
                return []
        while not self.queue.empty():
            items.append(self.queue.get_nowait())
        return items


_PRESENCE = {"type": "_presence"}


class LiveHub:
    """The event bus and presence registry of one process (ADR-0095, ADR-0097)."""

    def __init__(
        self,
        config: LiveConfig | None = None,
        max_streams: int = 0,
        max_streams_per_user: int = 0,
        clock: Callable[[], float] = time.monotonic,
        shared_capacity: int = SHARED_CAPACITY,
        wall: Callable[[], float] = time.time,
        replica: str | None = None,
    ):
        self.config = config or LiveConfig()
        self.wall = wall  # wall clock: presence items of other replicas expire by it (ADR-0104)
        self.replica = replica or secrets.token_hex(8)  # this process among the replicas sharing presence
        self.max_streams = max_streams  # 0: unlimited
        self.max_streams_per_user = max_streams_per_user
        self.clock = clock
        self.closing = False
        self._lock = threading.RLock()  # re-entrant: close() runs in a signal handler on the loop thread
        self._subs: dict[tuple[str, str], Subscription] = {}
        self._left: dict[tuple[str, str], Subscription] = {}  # ended streams, lingering in presence
        self._by_project: dict[str, set[tuple[str, str]]] = {}  # project -> keys of its streams (open or lingering)
        self._pruned = float("-inf")
        self._listeners: list[Callable[[dict[str, Any]], None]] = []  # in-process observers of the bus (the steward)
        # Reads shared by the re-checks of all streams (one read per change, fanned out): key -> _Shared, bounded
        self._shared: OrderedDict[tuple[str, str], _Shared] = OrderedDict()
        self._generations: dict[tuple[str, str], int] = {}  # only for slots in `_shared` (evicted together)
        self._epoch = 0  # bumped by an access event without a target: every shared read is stale
        self.shared_capacity = shared_capacity
        self.shared_wait = self.config.presence_ttl  # longest wait for a shared read in flight; older ones are stuck
        self._chatting: dict[int, dict[str, Any]] = {}
        self._chat_ids = 0
        # Presence shared across replicas (ADR-0104); None: in memory only (file, sqlite, tests)
        self.presence_channel: PresenceChannel | None = None
        self._relay_autostart = False
        self._relay: threading.Thread | None = None
        self._relay_wake = threading.Event()
        self._relay_stop = threading.Event()
        self._relay_lock = threading.Lock()  # one relay cycle at a time
        self._relay_failing = False  # a failure is logged once per streak
        self._written: dict[str, tuple[dict[str, Any], float]] = {}  # project -> (body written, when)
        self._remote: dict[str, dict[str, list[dict[str, Any]]]] = {}  # project -> other replicas' entries

    # ---- the bus -----------------------------------------------------------------------------------

    def publish(self, event: dict[str, Any]) -> None:
        """Hand `event` to every matching stream; thread-safe, never blocks, never raises. The shared reads the event
        makes stale are invalidated first, so the streams' re-checks read the new state (once)."""
        try:
            with self._lock:
                self._invalidate(event)
                targets = [s for s in self._subs.values() if s.matches(event)]
                listeners = list(self._listeners)
            for sub in targets:
                _call(sub, sub.deliver, dict(event))
            for listener in listeners:
                try:
                    listener(dict(event))
                except Exception:  # an observer never fails a write
                    logger.exception("A live event listener failed")
        except Exception:  # a hint lost is never worth failing a write
            logger.exception("Publishing a live event failed")

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        """Call `listener(event)` for every published event, on the publishing thread: it must be quick and never block."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _poke(self, watches: list[Watch], relay: bool = True) -> None:
        """Tell the streams of the projects of `watches` (a diagram's viewers are in its project) that presence
        changed; with a shared channel the relay writes the change for the other replicas soon (`relay`)."""
        if relay and self.presence_channel is not None:
            self._relay_wake.set()
        with self._lock:
            keys = {k for w in watches for k in self._by_project.get(w.project, ())}
            targets = [self._subs[k] for k in keys if k in self._subs]
        for sub in targets:
            _call(sub, sub.deliver, dict(_PRESENCE))

    # ---- reads shared by the re-checks (spec: collaboration.md) --------------------------------------------

    @property
    def shared_capacity(self) -> int:
        return self._shared_capacity

    @shared_capacity.setter
    def shared_capacity(self, value: int) -> None:
        if value < 1:
            raise ValueError(f"shared_capacity must be at least 1, got {value}.")
        self._shared_capacity = value

    def shared(self, kind: str, key: str, load: Callable[[], Any]) -> Any:
        """`load()` once for every re-check of (`kind`, `key`) until an event invalidates it or half a poll interval
        passed (changes of other replicas); concurrent callers wait for the one read, at most `shared_wait` seconds
        (then SharedReadTimeout, and the stuck read is replaced for the next caller). Failures are not cached."""
        slot = (kind, key)
        with self._lock:
            entry = self._shared.get(slot)
            if entry is None or entry.stuck(time.monotonic(), self.shared_wait):
                entry = self._shared[slot] = _Shared()
            self._shared.move_to_end(slot)
            entry.users += 1  # never evicted while in use: a second caller would read again
            self._evict()
        try:
            if not entry.lock.acquire(timeout=self.shared_wait):
                with self._lock:
                    if self._shared.get(slot) is entry:
                        del self._shared[slot]  # the read in flight hangs: the next caller starts afresh
                raise SharedReadTimeout(f"Reading {kind} '{key}' takes longer than {self.shared_wait:g} s.")
            try:
                now = self.clock()
                with self._lock:
                    generation = (self._epoch, self._generations.get(slot, 0))
                if entry.valid(generation, now, self.config.poll_seconds / 2):
                    return entry.value
                entry.loading_since = time.monotonic()
                try:
                    value = load()
                finally:
                    entry.loading_since = None
                entry.store(value, generation, now)
                return value
            finally:
                entry.lock.release()
        finally:
            with self._lock:
                entry.users -= 1

    def _evict(self) -> None:
        """Under the lock: drop the least recently used shared reads beyond the capacity, never one in use (the
        capacity may be exceeded while all are); their generations go with them (a new entry reads afresh)."""
        excess = len(self._shared) - self.shared_capacity
        if excess <= 0:
            return
        for slot in [s for s, e in self._shared.items() if e.users == 0][:excess]:
            del self._shared[slot]
            self._generations.pop(slot, None)

    def _invalidate(self, event: Mapping[str, Any]) -> None:
        """Under the lock: the shared reads an event makes stale (a transcript event changes none of them; moves and
        deletions also publish the projects' events)."""
        if event.get("type") == "chat":
            return
        slots = [(k, event[k]) for k in ("diagram", "project", "workspace") if event.get(k)]
        if not slots:
            self._epoch += 1  # an access change without a target: every read, also one in flight, is stale
        for slot in slots:
            if slot in self._shared:  # nothing cached or in flight: nothing to invalidate (keeps this bounded)
                self._generations[slot] = self._generations.get(slot, 0) + 1

    def _reindex(self, key: tuple[str, str], before: str | None, after: str | None) -> None:
        """Under the lock: move a stream's key from project `before` to `after` in the presence index."""
        if before is not None and before in self._by_project:
            self._by_project[before].discard(key)
            if not self._by_project[before]:
                del self._by_project[before]
        if after is not None:
            self._by_project.setdefault(after, set()).add(key)

    # ---- streams -------------------------------------------------------------------------------------

    def subscribe(
        self, user: str, name: str, session: str | None, watch: Watch, loop: asyncio.AbstractEventLoop
    ) -> Subscription:
        """A new stream; one of the same caller and session ends (`replaced`). StreamLimit at the caps."""
        session = session if session and SESSION_RE.match(session) else new_session()
        now = self.clock()
        with self._lock:
            if self.closing:
                raise StreamLimit("The server is shutting down.", "process")
            stale = self._prune(now, force=True)
            key = (user, session)
            old = self._subs.get(key)
            others = len(self._subs) - (old is not None)
            if self.max_streams and others >= self.max_streams:
                raise StreamLimit(f"Too many live streams on this server (max {self.max_streams}).", "process")
            mine = sum(1 for k in self._subs if k[0] == user and k != key)
            if self.max_streams_per_user and mine >= self.max_streams_per_user:
                raise StreamLimit(f"Too many live streams for one user (max {self.max_streams_per_user}).", "user")
            previous = old or self._left.pop(key, None)
            if previous is not None:
                self._reindex(key, previous.watch.project, None)
            sub = Subscription(user, name, session, watch, loop, now)
            self._subs[key] = sub
            self._reindex(key, None, watch.project)
        for gone in stale:
            gone.close("stale")
        if old is not None:
            old.ended = now
            old.close("replaced")
        self._ensure_relay()
        self._poke([watch])
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        now = self.clock()
        with self._lock:
            if self._subs.get(sub.key) is sub:
                del self._subs[sub.key]
                sub.ended = now
                self._left[sub.key] = sub
        self._poke([sub.watch])

    def update_watch(self, sub: Subscription, watch: Watch, based_on: int | None = None) -> bool:
        """After a re-check: the stream is alive and may watch another project (a moved diagram). With `based_on`
        (the `watch_version` the re-check started from) the update is refused when the watch changed meanwhile, so a
        stale re-check never re-points a stream back to a project it left (ADR-0105); returns whether it applied."""
        with self._lock:
            if based_on is not None and sub.watch_version != based_on:
                return False
            before = sub.watch
            sub.watch = watch
            sub.watch_version += 1
            sub.seen = self.clock()
            if self._subs.get(sub.key) is sub and before.project != watch.project:
                self._reindex(sub.key, before.project, watch.project)
        if (before.project, before.diagram) != (watch.project, watch.diagram):
            self._poke([before, watch])
        return True

    def touch(self, sub: Subscription) -> None:
        """The stream's loop is alive (every wake-up), so the TTL never drops a slow but running stream."""
        sub.seen = self.clock()

    def streams(self) -> int:
        with self._lock:
            return len(self._subs)

    def close(self) -> None:
        """Shutdown: refuse new streams and end every open one at once (ADR-0098)."""
        with self._lock:
            self.closing = True
            subs = list(self._subs.values())
        for sub in subs:
            sub.close("shutdown")
        self._stop_relay()

    def open(self) -> None:
        """Accept streams again (a new lifespan of the app after a shutdown)."""
        with self._lock:
            self.closing = False
            self._relay_stop.clear()

    def _prune(self, now: float, force: bool = False) -> list[Subscription]:
        """Under the lock, at most once a second unless `force`: drop streams whose loop has not woken up for the TTL
        (a task that died or hangs; returned, so the caller ends them) and lingering ones that are done."""
        if not force and now - self._pruned < 1.0:
            return []
        self._pruned = now
        ttl = self.config.presence_ttl
        stale = []
        for key, sub in list(self._subs.items()):
            busy = sub.checking and now - sub.checking_since <= ttl  # a slow re-check is no dead stream, a hung one is
            if now - sub.seen > ttl and not busy:
                del self._subs[key]
                self._reindex(key, sub.watch.project, None)
                stale.append(sub)
        for key, sub in list(self._left.items()):
            if sub.ended is None or now - sub.ended > LINGER_SECONDS:
                del self._left[key]
                self._reindex(key, sub.watch.project, None)
        return stale

    # ---- presence ------------------------------------------------------------------------------------

    def select(self, user: str, session: str, diagram: str, selection: str | None) -> bool:
        """Record the element the caller's stream `session` on `diagram` has selected; False if there is no such
        stream. The latest value always wins; streams throttle what they send."""
        if selection is not None and (not isinstance(selection, str) or len(selection) > MAX_SELECTION):
            raise ValueError(f"selection must be an element id of at most {MAX_SELECTION} characters, or null.")
        with self._lock:
            sub = self._subs.get((user, session))
            if sub is None or sub.watch.diagram != diagram:
                return False
            changed = sub.selection != selection
            sub.selection = selection
        if changed:
            self._throttled_poke(sub)
        return True

    def find(self, user: str, session: str) -> Subscription | None:
        """The caller's open stream with this session, if any."""
        with self._lock:
            return self._subs.get((user, session))

    def _throttled_poke(self, sub: Subscription) -> None:
        """At most one presence poke per PRESENCE_INTERVAL for a stream's selections; a faster change is delivered by
        one trailing poke, so the latest selection always arrives."""
        now = self.clock()
        with self._lock:
            if sub.poke_scheduled:
                return
            wait = sub.last_poke + PRESENCE_INTERVAL - now
            if wait > 0:
                sub.poke_scheduled = True
            else:
                sub.last_poke = now
        if wait <= 0:
            self._poke([sub.watch])
            return

        def trailing() -> None:
            with self._lock:
                sub.poke_scheduled = False
                sub.last_poke = self.clock()
            self._poke([sub.watch])

        _call(sub, sub.loop.call_later, wait, trailing)

    @contextmanager
    def chatting(self, user: str, name: str, diagram: str | None, project: str | None) -> Iterator[None]:
        """`user` is in a chat turn on `diagram` / `project` until the block ends."""
        with self._lock:
            self._chat_ids += 1
            token = self._chat_ids
            self._chatting[token] = {"user": user, "name": name, "diagram": diagram, "project": project}
        self._ensure_relay()
        watch = Watch(diagram, project or "", None, None, None, None)
        self._poke([watch])
        try:
            yield
        finally:
            with self._lock:
                self._chatting.pop(token, None)
            self._poke([watch])

    def presence(self, sub: Subscription) -> dict[str, Any]:
        """The presence snapshot for one stream: viewers of its diagram (with selections), viewers of its project,
        and who is chatting there."""
        now = self.clock()
        diagram, project = sub.watch.diagram, sub.watch.project
        with self._lock:
            stale = self._prune(now)
            keys = list(self._by_project.get(project, ()))
            found = (self._subs.get(k) or self._left.get(k) for k in keys)
            entries = [s for s in found if s is not None]
            chatting = list(self._chatting.values())
            remote = self._remote.get(project, {})
        for gone in stale:
            gone.close("stale")
        local = [
            {"session": s.session, "user": s.user, "name": s.name, "diagram": s.watch.diagram, "selection": s.selection}
            for s in entries
        ]
        mine = {(v["user"], v["session"]) for v in local}
        # entries of the other replicas for this project (ADR-0104): a session seen here too is shown once, from here
        others = [v for v in remote.get("viewers", []) if (v["user"], v["session"]) not in mine]
        everyone = local + others
        viewers = sorted(
            (dict(v) for v in everyone if diagram is not None and v["diagram"] == diagram),
            key=lambda v: (v["user"], v["session"]),
        )
        project_viewers = sorted(
            ({k: v[k] for k in ("session", "user", "name", "diagram")} for v in everyone),
            key=lambda v: (v["user"], v["session"]),
        )
        chats = sorted(
            (
                dict(c)
                for c in [*chatting, *remote.get("chatting", [])]
                if (diagram is not None and c["diagram"] == diagram) or (c["project"] and c["project"] == project)
            ),
            key=lambda c: (c["user"], c["diagram"] or "", c["project"] or ""),
        )
        return {"type": "presence", "viewers": viewers, "project_viewers": project_viewers, "chatting": chats}

    # ---- presence across replicas (Cosmos DB, ADR-0104) --------------------------------------------------

    @property
    def heartbeat_seconds(self) -> float:
        """How often an unchanged presence item is written again: a third of its TTL."""
        return self.config.presence_ttl / 3

    def share_presence(self, channel: PresenceChannel | None, start: bool = True) -> None:
        """Share presence with the other replicas through `channel` (None: in memory only). `start`: a relay thread
        runs `sync_presence` once per poll interval and soon after every change (from the first stream on); tests
        call `sync_presence` themselves."""
        self.presence_channel = channel
        self._relay_autostart = start and channel is not None

    def remote_projects(self) -> set[str]:
        """The projects whose entries of other replicas are held (those this replica has open streams for)."""
        with self._lock:
            return set(self._remote)

    def _ensure_relay(self) -> None:
        if not self._relay_autostart or self.closing:
            return
        with self._lock:
            if self._relay is not None and self._relay.is_alive():
                return
            self._relay_stop.clear()
            self._relay = threading.Thread(target=self._run_relay, name="arcviz-presence-relay", daemon=True)
            self._relay.start()

    def _run_relay(self) -> None:
        """The relay thread: one cycle per poll interval, or soon after a change (at most four a second)."""
        while not self._relay_stop.is_set():
            self._relay_wake.wait(self.config.poll_seconds)
            self._relay_wake.clear()
            if self._relay_stop.is_set():
                break
            self.sync_presence()
            self._relay_stop.wait(PRESENCE_INTERVAL)
        self._retire_presence()

    def _stop_relay(self) -> None:
        """Shutdown: the relay thread removes this replica's items as it ends; without one, remove them now."""
        self._relay_stop.set()
        self._relay_wake.set()
        thread = self._relay
        if thread is None or not thread.is_alive():
            self._retire_presence()

    def _retire_presence(self) -> None:
        channel = self.presence_channel
        if channel is None:
            return
        with self._relay_lock:
            for project in list(self._written):
                try:
                    channel.delete_presence(project, self.replica)
                except Exception:
                    logger.warning("Could not remove the presence of this replica in '%s'", project, exc_info=True)
                self._written.pop(project, None)

    def _local_bodies(self) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Under the lock: what this replica shows per project (open and lingering streams, chat turns)."""
        bodies: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for project, keys in self._by_project.items():
            found = (self._subs.get(k) or self._left.get(k) for k in keys)
            viewers = [
                {
                    "session": s.session,
                    "user": s.user,
                    "name": s.name,
                    "diagram": s.watch.diagram,
                    "selection": s.selection,
                }
                for s in found
                if s is not None
            ]
            if viewers:
                bodies[project] = {"viewers": sorted(viewers, key=lambda v: (v["user"], v["session"])), "chatting": []}
        for chat in self._chatting.values():
            if chat["project"]:
                bodies.setdefault(chat["project"], {"viewers": [], "chatting": []})["chatting"].append(dict(chat))
        for body in bodies.values():
            body["chatting"].sort(key=lambda c: (c["user"], c["diagram"] or "", c["project"] or ""))
        return bodies

    def sync_presence(self) -> None:
        """One relay cycle: write this replica's item of every project it has entries for when they changed or the
        heartbeat is due (delete it when nobody is left), then read the other replicas' items of the projects it has
        open streams for, one shared read per project and half poll interval (`shared`), and poke those streams
        when they changed. Never raises: a failing channel only costs the cross-replica part."""
        channel = self.presence_channel
        if channel is None:
            return
        with self._relay_lock:
            now, wall, ttl = self.clock(), self.wall(), self.config.presence_ttl
            with self._lock:
                stale = self._prune(now, force=True)
                bodies = self._local_bodies()
                watched = {p for p, keys in self._by_project.items() if any(k in self._subs for k in keys)}
            for gone in stale:
                gone.close("stale")
            failed = False
            for project in sorted(set(bodies) | set(self._written)):
                body = bodies.get(project)
                try:
                    if body is None:
                        channel.delete_presence(project, self.replica)
                        del self._written[project]
                        continue
                    last = self._written.get(project)
                    if last is None or last[0] != body or now - last[1] >= self.heartbeat_seconds:
                        item = {"replica": self.replica, "expires": wall + ttl, **body}
                        channel.write_presence(project, self.replica, item, ttl)
                        self._written[project] = (body, now)
                except Exception:
                    failed = True
                    self._relay_failed(f"writing the presence of '{project}'")
            changed: list[str] = []
            for project in sorted(watched):
                try:
                    items = self.shared("presence", project, partial(channel.read_presence, project))
                except Exception:
                    failed = True
                    self._relay_failed(f"reading the presence of '{project}'")
                    continue
                remote = self._remote_entries(items, wall, project)
                with self._lock:
                    if self._remote.get(project) != remote:
                        self._remote[project] = remote
                        changed.append(project)
            with self._lock:
                for project in [p for p in self._remote if p not in watched]:
                    del self._remote[project]
            if not failed:
                self._relay_failing = False
        if changed:
            self._poke([Watch(None, p, None, None, None, None) for p in changed], relay=False)

    def _relay_failed(self, what: str) -> None:
        if not self._relay_failing:
            logger.warning("Presence across replicas: %s failed; local presence is unaffected", what, exc_info=True)
        self._relay_failing = True

    def _remote_entries(
        self, items: list[dict[str, Any]], wall: float, project: str
    ) -> dict[str, list[dict[str, Any]]]:
        """The viewers and chat turns of the other replicas' items that have not expired, in the shape of the local
        entries (anything else in an item is ignored)."""
        viewers: list[dict[str, Any]] = []
        chatting: list[dict[str, Any]] = []
        for item in items:
            if item.get("replica") == self.replica or not _number(item.get("expires")) >= wall:
                continue
            for v in item.get("viewers") or []:
                entry = _clean(v, ("session", "user", "name"), ("diagram", "selection"))
                if entry is not None:
                    viewers.append(entry)
            for c in item.get("chatting") or []:
                entry = _clean(c, ("user", "name"), ("diagram", "project"))
                if entry is not None and entry["project"] == project:
                    chatting.append(entry)
        viewers.sort(key=lambda v: (v["user"], v["session"]))
        chatting.sort(key=lambda c: (c["user"], c["diagram"] or "", c["project"] or ""))
        return {"viewers": viewers, "chatting": chatting}


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else float("-inf")


def _clean(raw: Any, required: tuple[str, ...], optional: tuple[str, ...]) -> dict[str, Any] | None:
    """An entry of another replica with exactly the expected string fields (None when malformed)."""
    if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) for k in required):
        return None
    if not all(raw.get(k) is None or isinstance(raw.get(k), str) for k in optional):
        return None
    return {k: raw.get(k) for k in (*required, *optional)}


class _Shared:
    """One shared read: its value, the generation it was read at and when, and whether a read is in flight."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.value: Any = None
        self.generation: tuple[int, int] = (-1, -1)  # (epoch, generation of its key)
        self.read_at = float("-inf")
        self.users = 0  # callers holding the entry (under the hub's lock)
        self.loading_since: float | None = None  # monotonic start of the read in flight

    def stuck(self, now: float, wait: float) -> bool:
        started = self.loading_since
        return started is not None and now - started > wait

    def valid(self, generation: tuple[int, int], now: float, max_age: float) -> bool:
        return self.generation == generation and now - self.read_at <= max_age

    def store(self, value: Any, generation: tuple[int, int], now: float) -> None:
        self.value, self.generation, self.read_at = value, generation, now


def _call(sub: Subscription, fn: Callable[..., Any], *args: Any) -> None:
    """Run `fn` on the subscription's loop (from any thread); a closed loop means the stream is gone."""
    with suppress(RuntimeError):
        sub.loop.call_soon_threadsafe(fn, *args)


Recheck = Callable[[Watch], "Watch | None"]


async def run_stream(
    hub: LiveHub, sub: Subscription, recheck: Recheck, run_sync: Callable[..., Any] | None = None
) -> AsyncIterator[dict[str, Any] | None]:
    """The events of one stream (None: a keep-alive), ending with `gone` or `bye`; unsubscribes when it ends.

    `recheck(watch)` re-checks the caller's access and reads the revisions (a blocking call, run in a worker thread
    by `run_sync`, default `asyncio.to_thread`); None means the diagram or project is gone for the caller.
    """
    run = run_sync or asyncio.to_thread
    loop = asyncio.get_running_loop()
    config = hub.config
    started = loop.time()
    deadline = started + config.max_seconds
    watch = sub.watch
    try:
        yield {
            "type": "hello",
            "session": sub.session,
            "diagram": watch.diagram,
            "project": watch.project,
            "revision": watch.revision,
            "project_revision": watch.project_revision,
            "role": watch.role,
            "poll_seconds": config.poll_seconds,
        }
        last_presence = hub.presence(sub)
        yield last_presence
        last_presence_at = loop.time()
        presence_pending = False
        unattributed: str | None = None  # a revision the poll reported before its bus event arrived
        carried: list[dict[str, Any]] = []  # bus events of a re-check that was dropped (#176): the next one sees them
        next_check = started + config.poll_seconds
        while True:
            now = loop.time()
            if now >= deadline:
                # an event that came in meanwhile (a delete above all) still gets its re-check (#218): the lifetime ends
                # quiet waiting, never the report of what happened
                pending = carried + [i for i in await sub.next(0) if not str(i.get("type", "")).startswith("_")]
                if pending and sub.close_reason is None:
                    try:
                        final = await _bounded(run(recheck, sub.watch), sub, hub.shared_wait)
                    except Exception:
                        final = sub.watch  # cannot tell: the reconnect finds out (a 404 for a deleted diagram)
                    if final is None:
                        yield {"type": "gone"}
                        return
                yield {"type": "bye", "reason": "expired"}
                return
            wake = min(deadline, next_check)
            if presence_pending:
                wake = min(wake, last_presence_at + PRESENCE_INTERVAL)
            items = await sub.next(max(0.0, wake - now))
            hub.touch(sub)
            if sub.close_reason is not None:
                yield {"type": "bye", "reason": sub.close_reason}
                return
            if any(i.get("type") == "_presence" for i in items):
                presence_pending = True
            bus = carried + [i for i in items if not str(i.get("type", "")).startswith("_")]
            carried = []
            now = loop.time()
            sent = False
            if bus or sub.overflow or now >= next_check:
                eventful = bool(bus) or sub.overflow  # something happened: checked in full, past the lifetime too
                sub.overflow = False
                polled = now >= next_check
                base, current = sub.watch_version, sub.watch  # the newest watch: a selection may have re-pointed it
                sub.checking, sub.checking_since = True, hub.clock()  # the TTL keeps the stream meanwhile (ADR-0098)
                # raced against close and bounded: a hanging store never pins a stream or the shutdown. An event is
                # checked in full (`shared_wait`) even past the lifetime, so a delete is reported as `gone` (#218); the
                # lifetime only cuts a poll.
                budget = hub.shared_wait if eventful else min(deadline - loop.time(), hub.shared_wait)
                try:
                    checked = await _bounded(run(recheck, current), sub, budget)
                except _Closed:
                    yield {"type": "bye", "reason": sub.close_reason or "shutdown"}
                    return
                except TimeoutError:
                    yield {"type": "bye", "reason": "expired" if loop.time() >= deadline else "error"}
                    return
                except Exception:
                    logger.exception("A live stream could not re-check its watch; it ends and the client reconnects")
                    yield {"type": "bye", "reason": "error"}
                    return
                finally:
                    sub.checking = False
                    hub.touch(sub)
                next_check = loop.time() + config.poll_seconds
                if checked is None:
                    yield {"type": "gone"}
                    return
                if not hub.update_watch(sub, checked, based_on=base):
                    # the watch changed while this re-check ran (#176): drop its result, check the newer watch at once
                    carried, next_check = bus, loop.time()
                    continue
                for event in _changes(watch, checked, bus):
                    yield event
                    sent = True
                    if event["type"] == "changed":
                        unattributed = event["revision"] if event["author"] is None else None
                late = _late_author(unattributed, checked, bus)
                if late is not None:  # the poll was quicker than the bus: name the author now
                    unattributed = None
                    yield late
                    sent = True
                watch = checked
                if polled:
                    presence_pending = True  # TTLs and lingering entries change without a poke
            if presence_pending and loop.time() >= last_presence_at + PRESENCE_INTERVAL:
                presence_pending = False
                snapshot = hub.presence(sub)
                if snapshot != last_presence:
                    last_presence = snapshot
                    last_presence_at = loop.time()
                    yield snapshot
                    sent = True
            if not sent and not items:
                yield None  # keep-alive after a quiet poll
    finally:
        hub.unsubscribe(sub)


class _Closed(Exception):
    """The stream was closed while it waited for a re-check."""


async def _bounded(work: Any, sub: Subscription, timeout: float) -> Any:
    """The result of `work`, unless the stream is closed first (_Closed) or `timeout` passes (TimeoutError); the
    abandoned work is cancelled (a worker thread finishes on its own, its result is dropped)."""
    task = asyncio.ensure_future(work)
    closed = asyncio.ensure_future(sub.closed.wait())
    try:
        done, _ = await asyncio.wait({task, closed}, timeout=max(0.0, timeout), return_when=asyncio.FIRST_COMPLETED)
    finally:
        closed.cancel()
    if task in done:
        return task.result()
    task.cancel()
    if closed in done or sub.close_reason is not None:
        raise _Closed()
    raise TimeoutError()


def _late_author(revision: str | None, after: Watch, bus: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The author of a change the poll reported without one, once its bus event arrives (same revision)."""
    if revision is None or after.revision != revision:
        return None
    cause = next((e for e in reversed(bus) if e.get("type") == "changed" and e.get("author")), None)
    if cause is None:
        return None
    return {
        "type": "changed",
        "diagram": after.diagram,
        "revision": revision,
        "author": cause.get("author"),
        "author_name": cause.get("author_name"),
        "origin": cause.get("origin"),
    }


def _changes(before: Watch, after: Watch, bus: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """What a stream sends after a successful re-check: the differences of the two watches, attributed to the bus
    events that announced them (unknown when only the poll saw them: another replica wrote)."""
    if after.revision != before.revision and after.diagram is not None:
        cause: dict[str, Any] = next((e for e in reversed(bus) if e.get("type") == "changed"), {})
        yield {
            "type": "changed",
            "diagram": after.diagram,
            "revision": after.revision,
            "author": cause.get("author"),
            "author_name": cause.get("author_name"),
            "origin": cause.get("origin"),
        }
    if (after.project, after.project_revision) != (before.project, before.project_revision):
        yield {"type": "project", "project": after.project, "revision": after.project_revision}
    if after.role != before.role:
        yield {"type": "access", "role": after.role}
    # a diagram's transcript, or (without `diagram`) the project's: the diagram UI ignores the latter
    if any(e.get("type") == "chat" and e.get("diagram") for e in bus):
        yield {"type": "chat", "diagram": after.diagram, "project": after.project}
    if any(e.get("type") == "chat" and not e.get("diagram") for e in bus):
        yield {"type": "chat", "diagram": None, "project": after.project}
