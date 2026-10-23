from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterator
from typing import Any

import pytest
from hypothesis import settings

from arcviz import ports
from arcviz.llm.base import ChatMessage, LLMProvider, ModelReply, TextDelta, ToolCall, ToolResult, ToolSpec
from arcviz.model import BUILTIN_KINDS, use_kinds
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.templates import demo_diagram

# Property tests (tests/test_properties.py, ADR-0021): random locally, derandomised in CI so a red run is
# reproducible and never a lucky seed. HYPOTHESIS_PROFILE overrides the choice.
settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile("ci", max_examples=100, deadline=None, derandomize=True, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev"))


class ScriptedProvider(LLMProvider):
    """Replays a fixed list of model replies; records everything it was sent.

    With `stream=True` each reply's text is first emitted word by word as `TextDelta`s, like a
    streaming provider does.
    """

    name = "scripted"
    model = "scripted-1"

    def __init__(self, replies: list[ModelReply], stream: bool = False):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.stream = stream

    def _history_to_native(self, history: list[ChatMessage]) -> list[Any]:
        return [{"role": m.role, "content": m.content} for m in history]

    def _user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def _complete(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> ModelReply:
        self.requests.append({"system": system, "messages": list(messages), "tools": [t.name for t in tools]})
        if not self.replies:
            return ModelReply(text="(no more scripted replies)", tool_calls=[], stop_reason="end_turn")
        return self.replies.pop(0)

    def _stream(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> Iterator[TextDelta | ModelReply]:
        reply = self._complete(system, messages, tools)
        if self.stream:
            for word in re.findall(r"\S+\s*|\s+", reply.text):
                yield TextDelta(word)
        yield reply

    def _assistant_message(self, reply: ModelReply) -> Any:
        return {"role": "assistant", "content": reply.text, "tool_calls": [c.__dict__ for c in reply.tool_calls]}

    def _tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        return [{"role": "tool", "id": r.tool_call_id, "content": r.content, "is_error": r.is_error} for r in results]


def call(tool: str, call_id: str = "c1", **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id, name=tool, arguments=arguments)


def pytest_configure(config: pytest.Config) -> None:
    """A leaked resource fails the run (#182): an unclosed SQLite connection (or file) raises ResourceWarning when it
    is collected, which pytest reports as an unraisable exception. Configured here, not in pyproject.toml."""
    config.addinivalue_line("filterwarnings", "error::ResourceWarning")
    config.addinivalue_line("filterwarnings", "error::pytest.PytestUnraisableExceptionWarning")


_LOOPS: list[asyncio.AbstractEventLoop] = []


def new_loop() -> asyncio.AbstractEventLoop:
    """An event loop for a test that hands one to a live stream without running it; closed after the test."""
    loop = asyncio.new_event_loop()
    _LOOPS.append(loop)
    return loop


@pytest.fixture(autouse=True)
def close_loops() -> Iterator[None]:
    yield
    while _LOOPS:
        _LOOPS.pop().close()


@pytest.fixture(params=["file", "sqlite"])
def store(request, tmp_path):
    """Every agent/server test runs against the file and the sqlite backend."""
    if request.param == "sqlite":
        repo = SqliteStore(tmp_path / "data" / "arcviz.sqlite3")
        yield repo
        repo.close()
        return
    yield FileStore(tmp_path / "data")


@pytest.fixture
def demo(store):
    return store.create("Demo", "demo", demo_diagram("demo", "Demo"))


def readable_ids(base: str) -> Iterator[str]:
    """Test ids for new diagrams and projects: `<base>`, then `<base>-2`, `<base>-3`, ... while taken. Production draws
    `<base>-<random suffix>` (`ports.new_ids`, ADR-0131); the suite pins readable ids so its tests can name what they
    created. Tests of the id scheme itself are marked `real_ids` and get the production generator."""
    yield base
    n = 2
    while True:
        yield f"{base}-{n}"
        n += 1


PRODUCTION_IDS = ports.new_ids


@pytest.fixture(autouse=True, scope="session")
def pinned_ids() -> Iterator[None]:
    """Readable ids for the whole session (also for module-scoped fixtures, which run before function fixtures)."""
    ports.new_ids = readable_ids
    yield
    ports.new_ids = PRODUCTION_IDS


@pytest.fixture(autouse=True)
def real_ids_when_marked(request, monkeypatch):
    if request.node.get_closest_marker("real_ids") is not None:
        monkeypatch.setattr(ports, "new_ids", PRODUCTION_IDS)


@pytest.fixture(autouse=True)
def telemetry_off(monkeypatch):
    """The app's own spans and metrics stay off unless a test turns telemetry on (#187): `start_telemetry` enables
    them process-wide."""
    from arcviz import observability, telemetry

    def in_memory_metrics():  # tests never export over the network (OTLP's default is localhost:4318)
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        return InMemoryMetricReader()

    def in_memory_logs():
        from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

        return InMemoryLogRecordExporter()

    monkeypatch.setattr(telemetry, "_enabled", False)
    monkeypatch.setattr(observability, "_metric_reader", in_memory_metrics)
    monkeypatch.setattr(observability, "_log_exporter", in_memory_logs)


@pytest.fixture(autouse=True)
def builtin_kinds():
    """The kind catalogue is process-wide; every test starts and ends with the built-in kinds."""
    use_kinds(BUILTIN_KINDS)
    yield
    use_kinds(BUILTIN_KINDS)


AUTH_VARIABLES = (
    "ARCVIZ_AUTH",
    "ARCVIZ_AUTH_TOKEN",
    "ARCVIZ_AUTH_PROXY_HEADER",
    "ARCVIZ_AUTH_PROXY_PRESET",
    "ARCVIZ_AUTH_PROXY_NAME_HEADER",
    "ARCVIZ_AUTH_PROXY_EMAIL_HEADER",
    "ARCVIZ_AUTH_DOCS",
    "ARCVIZ_ADMIN_USER",
    "ARCVIZ_ADMIN_NAME",
    "ARCVIZ_ADMIN_EMAIL",
)


@pytest.fixture(autouse=True)
def no_auth_from_the_environment(monkeypatch):
    """Tests decide the authentication themselves; a developer's shell must not leak into them."""
    for name in AUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class Users:
    """A test client of an app in proxy mode (oauth2-proxy preset) that sends requests as named users."""

    def __init__(self, client):
        self.client = client

    @staticmethod
    def headers(user: str) -> dict[str, str]:
        return {
            "x-forwarded-user": user,
            "x-forwarded-email": f"{user}@example.org",
            "x-forwarded-preferred-username": user.capitalize(),
        }

    def __call__(self, user: str, method: str, path: str, **kwargs: Any):
        return self.client.request(method, path, headers=self.headers(user), **kwargs)


def proxy_users(store, monkeypatch, provider=None, admin: str | None = "admin") -> Users:
    """The app in proxy mode; `admin` (default "admin") owns the default workspace with existing data."""
    from fastapi.testclient import TestClient

    from arcviz.server import create_app

    monkeypatch.setenv("ARCVIZ_AUTH", "proxy")
    monkeypatch.setenv("ARCVIZ_AUTH_PROXY_PRESET", "oauth2-proxy")
    if admin is not None:
        monkeypatch.setenv("ARCVIZ_ADMIN_USER", admin)
    app = create_app(store=store, provider=provider or ScriptedProvider([]))
    return Users(TestClient(app, follow_redirects=False))


@pytest.fixture
def fresh_tracing(monkeypatch):
    """A test that installs OpenTelemetry tracing gets the process-wide tracer provider slot back afterwards."""
    from opentelemetry import trace
    from opentelemetry.util._once import Once

    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", Once())
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
