"""Request size limits and in-process rate limiting.

Spec: docs/architecture/specs/request-limits.md, ADR-0048. `LimitConfig.from_env()` is the only place
that reads the limit configuration; `LimitsMiddleware` enforces body sizes and rate limits for every HTTP
request, the server checks the content-dependent limits (chat message length, proposal size).
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

from .envconfig import Env

DEFAULT_MAX_BODY_BYTES = 1_048_576  # 1 MiB of JSON: far above any request the UI sends
DEFAULT_MAX_IMPORT_BYTES = 10_485_760  # 10 MiB for an exported diagram document
DEFAULT_MAX_CHAT_CHARS = 20_000
MAX_PROPOSAL_OPERATIONS = 500
DEFAULT_MAX_STREAMS = 1000  # open live streams per process (spec: collaboration.md, ADR-0098)
DEFAULT_MAX_STREAMS_PER_USER = 20
PRESENCE_MAX_BODY = 4096  # bytes of a presence update (a session id and an element id)
MAX_BUCKETS = 10_000
IMPORT_PATHS = ("/api/diagrams/import", "/api/projects/import")  # get the larger import body limit
RATE_CLASSES = ("chat", "review", "ops", "share", "agent")
# per client and minute; 0 = unlimited. Sharing is limited by default: share and revoke would otherwise let anybody
# probe which user ids and e-mail addresses exist (spec: personal-workspaces.md, ADR-0101).
RATE_DEFAULTS = {"chat": 0, "review": 0, "ops": 0, "share": 30, "agent": 0}
RATE_VARIABLES = {
    "chat": "ARCVIZ_RATE_LIMIT_CHAT",
    "review": "ARCVIZ_RATE_LIMIT_REVIEW",
    "ops": "ARCVIZ_RATE_LIMIT_OPS",
    "share": "ARCVIZ_RATE_LIMIT_SHARE",
    "agent": "ARCVIZ_RATE_LIMIT_AGENT",
}
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


@dataclass(frozen=True)
class LimitConfig:
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES  # 0 = unlimited (also for the two below)
    max_import_bytes: int = DEFAULT_MAX_IMPORT_BYTES
    max_chat_chars: int = DEFAULT_MAX_CHAT_CHARS
    rates: Mapping[str, int] = field(default_factory=lambda: dict(RATE_DEFAULTS))  # per minute
    max_streams: int = DEFAULT_MAX_STREAMS  # 0 = unlimited (also below)
    max_streams_per_user: int = DEFAULT_MAX_STREAMS_PER_USER

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LimitConfig:
        env = Env(environ)
        config = cls(
            max_body_bytes=env.integer("ARCVIZ_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES),
            max_import_bytes=env.integer("ARCVIZ_MAX_IMPORT_BYTES", DEFAULT_MAX_IMPORT_BYTES),
            max_chat_chars=env.integer("ARCVIZ_MAX_CHAT_CHARS", DEFAULT_MAX_CHAT_CHARS),
            rates={name: env.integer(variable, RATE_DEFAULTS[name]) for name, variable in RATE_VARIABLES.items()},
            max_streams=env.integer("ARCVIZ_LIVE_MAX_STREAMS", DEFAULT_MAX_STREAMS),
            max_streams_per_user=env.integer("ARCVIZ_LIVE_MAX_STREAMS_PER_USER", DEFAULT_MAX_STREAMS_PER_USER),
        )
        env.check()  # ConfigError (a ValueError) naming every malformed variable
        return config

    def body_limit(self, path: str) -> int:
        if is_live_path(path) and self.max_body_bytes:
            return min(self.max_body_bytes, PRESENCE_MAX_BODY)
        return self.max_import_bytes if path in IMPORT_PATHS else self.max_body_bytes


LIVE_PATHS = re.compile(r"^/api/(diagrams/[^/]+/(live|presence)|projects/[^/]+/live)$")


def is_live_path(path: str) -> bool:
    """A live stream or presence update (spec: collaboration.md): throttled by the hub, not by the rate classes."""
    return LIVE_PATHS.match(path) is not None


_SHARE_PATH = re.compile(r"^/api/projects/[^/]+/access(/.*)?$")


def rate_class(method: str, path: str) -> str | None:
    """The rate-limit class of a request: `chat`/`review` (model calls), `ops` (other API writes), or None."""
    if method not in _WRITE_METHODS or not path.startswith("/api/") or is_live_path(path):
        return None
    if method == "POST" and (path.endswith("/chat") or path.endswith("/chat/compact")):
        return "chat"
    if method == "POST" and (
        path.endswith(("/review", "/analysis", "/requirements/refine", "/requirements/derive"))
    ):  # model calls (decision-support.md, requirements-workshop.md)
        return "review"
    if _SHARE_PATH.match(path):
        return "share"
    return "ops"


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token buckets per (class, client): capacity N, refilled with N tokens per minute. Thread-safe."""

    def __init__(
        self, rates: Mapping[str, int], clock: Callable[[], float] = time.monotonic, max_buckets: int = MAX_BUCKETS
    ):
        self.rates = dict(rates)
        self.clock = clock
        self.max_buckets = max_buckets
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._buckets)

    def check(self, name: str, client: str) -> float | None:
        """Take a token; None if allowed, else the seconds until the next token is available."""
        rate = self.rates.get(name, 0)
        if rate <= 0:
            return None
        now = self.clock()
        with self._lock:
            bucket = self._buckets.get((name, client))
            if bucket is None:
                self._evict(now)
                bucket = self._buckets[(name, client)] = _Bucket(float(rate), now)
            bucket.tokens = min(float(rate), bucket.tokens + (now - bucket.updated) * rate / 60.0)
            bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None
            return (1.0 - bucket.tokens) * 60.0 / rate

    def _evict(self, now: float) -> None:
        """Keep at most `max_buckets`: drop buckets that are full again (idle clients), oldest first."""
        if len(self._buckets) < self.max_buckets:
            return
        by_age = sorted(self._buckets.items(), key=lambda item: item[1].updated)
        for key, bucket in by_age:
            rate = self.rates.get(key[0], 0)
            if rate <= 0 or bucket.tokens + (now - bucket.updated) * rate / 60.0 >= rate:
                del self._buckets[key]
            if len(self._buckets) < self.max_buckets:
                return
        del self._buckets[by_age[0][0]]  # all busy: drop the least recently used one


def client_key(scope: Scope) -> str:
    """The authenticated user (set by the auth middleware) or the client address."""
    user = (scope.get("state") or {}).get("arcviz_user")
    if user:
        return f"user:{user}"
    client = scope.get("client")
    return f"addr:{client[0] if client else 'unknown'}"


async def send_json(send: Send, status: int, detail: str, headers: Mapping[str, str] | None = None) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw += [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


class LimitsMiddleware:
    """Rate limits per request class, then the body size limit; the body is buffered and replayed."""

    def __init__(
        self, app: ASGIApp, config: LimitConfig, limiter: RateLimiter | None = None, agent_path: str | None = None
    ):
        self.app = app
        self.config = config
        self.limiter = limiter or RateLimiter(config.rates)
        self.agent_path = agent_path  # the MCP endpoint, if there is one: its own rate class

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        name = "agent" if method == "POST" and path == self.agent_path else rate_class(method, path)
        if name is not None:
            wait = self.limiter.check(name, client_key(scope))
            if wait is not None:
                seconds = max(1, math.ceil(wait))
                await send_json(
                    send, 429, f"Too many {name} requests; try again in {seconds} s.", {"Retry-After": str(seconds)}
                )
                return
        limit = self.config.body_limit(path)
        if method not in _BODY_METHODS or limit <= 0:
            await self.app(scope, receive, send)
            return
        too_large = f"Request body too large (max {limit} bytes)."
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and length.isdigit() and int(length) > limit:
            await send_json(send, 413, too_large)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the client went away
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await send_json(send, 413, too_large)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # after the body: disconnect detection as usual

        await self.app(scope, replay, send)
