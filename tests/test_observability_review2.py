"""Second review of the logs and telemetry (#83; spec: operations.md §4, ADR-0112).

Aborted streams, records that are not mutated, exception texts kept out of messages, span status and attributes at
the end, no instrumentation metrics, client disconnects.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import types

import pytest
from fastapi.testclient import TestClient

from arcviz import observability, server
from arcviz.observability import LogConfig, configure_logging
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider

KEY = "pseudonym-key-for-tests-0123456789"
SENTINEL = "SENTINEL-carol@example.org-Nightingale"


@pytest.fixture(autouse=True)
def reset_logging():
    yield
    observability.reset_logging()


def json_logs() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(LogConfig(format="json", level="DEBUG", pseudonym_key=KEY), stream=stream)
    return stream


def records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def client_of(tmp_path, monkeypatch) -> tuple[TestClient, io.StringIO]:
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "json")
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
    return TestClient(app), json_logs()


# ---- 3. an aborted stream is not made to look complete ---------------------------------------------------------


def test_nothing_is_sent_after_a_complete_response():
    sent: list[dict] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        raise RuntimeError("after the end")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = observability.RequestContextMiddleware(app, log_requests=True)
    asyncio.run(middleware({"type": "http", "method": "GET", "path": "/x", "headers": []}, receive, send))
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]


# ---- 9. a client gone while the request is read ----------------------------------------------------------------


def test_a_client_disconnect_from_receive_is_logged_as_gone_not_as_an_error():
    stream = json_logs()

    async def app(scope, receive, send):
        await receive()  # the client is gone: starlette raises ClientDisconnect for the body
        from starlette.requests import ClientDisconnect

        raise ClientDisconnect()

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise AssertionError("nothing may be sent to a client that is gone")

    middleware = observability.RequestContextMiddleware(app, log_requests=True)
    asyncio.run(middleware({"type": "http", "method": "POST", "path": "/api/x", "headers": []}, receive, send))
    (record,) = [r for r in records(stream) if r.get("event") == "request"]
    assert record["level"] == "INFO" and record["aborted"] is True and record["status"] == 499
    assert "exc_type" not in record


def test_an_os_error_from_receive_counts_as_gone():
    stream = json_logs()

    async def app(scope, receive, send):
        await receive()

    async def receive():
        raise OSError("connection reset")

    async def send(message):
        raise AssertionError("nothing may be sent")

    middleware = observability.RequestContextMiddleware(app, log_requests=True)
    asyncio.run(middleware({"type": "http", "method": "POST", "path": "/api/x", "headers": []}, receive, send))
    (record,) = [r for r in records(stream) if r.get("event") == "request"]
    assert record["level"] == "INFO" and record["aborted"] is True


# ---- 4. scrubbing never changes the record other handlers see --------------------------------------------------


def test_the_export_scrubbing_leaves_the_text_traceback_intact():
    exported: list[logging.LogRecord] = []

    class Export(logging.Handler):
        def emit(self, record):
            exported.append(record)

    app_logger = logging.getLogger("arcviz")
    export = Export()
    app_logger.addHandler(export)
    root = logging.getLogger()
    text = io.StringIO()
    local = logging.StreamHandler(text)
    root.addHandler(local)
    try:
        observability.protect_export_handlers()  # what start_telemetry does for the export handlers
        try:
            raise LookupError(SENTINEL)
        except LookupError:
            logging.getLogger("arcviz.test").exception("failed")
    finally:
        app_logger.removeHandler(export)
        root.removeHandler(local)
    assert SENTINEL in text.getvalue() and "Traceback" in text.getvalue()  # local stderr keeps everything
    (record,) = exported
    assert record.exc_info is None and record.exc_type == "LookupError" and SENTINEL not in record.getMessage()


# ---- 5. exception texts never interpolated into messages -------------------------------------------------------


def test_compaction_failures_log_the_type_not_the_message(tmp_path):
    stream = json_logs()
    from arcviz.agent import DiagramAgent
    from arcviz.services import DiagramService

    class Broken:
        def chat(self, diagram_id):
            raise RuntimeError(SENTINEL)

    agent = DiagramAgent(ScriptedProvider([]), DiagramService(FileStore(tmp_path / "d")), compact_after=1)
    agent.transcripts = Broken()
    agent._maybe_compact("d1")
    agent.compact = lambda diagram_id: (_ for _ in ()).throw(RuntimeError(SENTINEL))  # type: ignore[method-assign]
    agent._compact_safely("d1")
    lines = records(stream)
    assert len(lines) == 2 and all(r["exc_type"] == "RuntimeError" for r in lines)
    assert SENTINEL not in stream.getvalue()


def test_git_mirror_failures_log_the_type_not_the_git_output(tmp_path, monkeypatch):
    from arcviz.gitmirror import GitMirror, GitMirrorConfig, GitMirrorError
    from arcviz.model import Diagram

    stream = json_logs()
    mirror = GitMirror(GitMirrorConfig(repo_dir=tmp_path / "git"))

    def failing_git(*args, **kwargs):
        raise GitMirrorError(f"git add failed: {SENTINEL}")

    monkeypatch.setattr(mirror, "_ensure_repo", lambda: None)
    monkeypatch.setattr(mirror, "_git", failing_git)
    monkeypatch.setattr(mirror, "_write_files", lambda diagram: None)
    monkeypatch.setattr(mirror, "_diagram_dir", lambda diagram_id: tmp_path)
    mirror.record(Diagram(id="d1", title="t"), "subject")
    mirror.remove("d1")
    monkeypatch.setattr(mirror, "_git", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr=SENTINEL, stdout=""))
    mirror._push()
    assert SENTINEL in (mirror.last_error or "")  # still reported in GET /api/config
    assert len([r for r in records(stream) if r["level"] == "WARNING"]) == 3 and SENTINEL not in stream.getvalue()


def test_cosmos_retries_log_the_type_not_the_message(monkeypatch):
    from arcviz.store import cosmos

    stream = json_logs()
    monkeypatch.setattr(cosmos.time, "sleep", lambda seconds: None)
    cosmos._backoff(1, "read", RuntimeError(SENTINEL))
    (record,) = records(stream)
    assert "RuntimeError" in record["message"] and SENTINEL not in stream.getvalue()


def test_start_up_migration_problems_log_types_only(tmp_path, monkeypatch):
    from arcviz.services import PersonalMigration

    report = PersonalMigration()
    report.repair_error = f"RuntimeError: {SENTINEL}"
    report.failed["p1"] = f"ValueError: {SENTINEL}"
    monkeypatch.setattr(server.ServiceRoot, "migrate", lambda self, admin: report)
    stream = io.StringIO()
    root = logging.getLogger()
    handler = logging.StreamHandler(stream)
    root.addHandler(handler)
    try:
        server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
    finally:
        root.removeHandler(handler)
    text = stream.getvalue()
    assert "RuntimeError" in text and "ValueError" in text and SENTINEL not in text


# ---- 6, 8. span status and attributes are scrubbed at the end --------------------------------------------------


def test_span_status_descriptions_and_late_attributes_are_scrubbed():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Status, StatusCode

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(observability.event_scrubber())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("test").start_as_current_span("s") as span:
        span.set_attribute("http.route", "/api/x")
        span.set_attribute("http.response.header.x_secret", SENTINEL)  # added after the hook ran
        span.set_status(Status(StatusCode.ERROR, f"LookupError: {SENTINEL}"))
    (finished,) = exporter.get_finished_spans()
    assert dict(finished.attributes) == {"http.route": "/api/x"}
    assert finished.status.status_code == StatusCode.ERROR and finished.status.description == "LookupError"


# ---- 7. no instrumentation metrics with raw attributes ---------------------------------------------------------


def test_the_request_instrumentation_records_no_metrics(tmp_path, monkeypatch, fresh_tracing):
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.util._once import Once

    reader = InMemoryMetricReader()
    monkeypatch.setattr(metrics._internal, "_METER_PROVIDER_SET_ONCE", Once())
    monkeypatch.setattr(metrics._internal, "_METER_PROVIDER", None)
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))  # like the Azure Monitor distro does
    monkeypatch.setattr(observability, "_telemetry_started", None)
    monkeypatch.setattr(observability, "_span_exporter", lambda: InMemorySpanExporter())
    monkeypatch.setattr(observability, "_span_processor", SimpleSpanProcessor)
    monkeypatch.setenv("ARCVIZ_TELEMETRY", "otlp")
    app_logger = logging.getLogger("arcviz")
    before = list(app_logger.handlers)
    try:
        app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
        TestClient(app).get("/api/diagrams/secret-plan", headers={"Host": "tenant.secret.example"})
    finally:
        for handler in [h for h in app_logger.handlers if h not in before]:
            app_logger.removeHandler(handler)
    data = reader.get_metrics_data()
    names = [m.name for rm in (data.resource_metrics if data else []) for sm in rm.scope_metrics for m in sm.metrics]
    assert not [name for name in names if name.startswith("http.server")], names
