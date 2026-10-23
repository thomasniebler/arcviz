"""Structured logs, request context, turn records and telemetry (#83; spec: operations.md §4, ADR-0111..0114)."""

from __future__ import annotations

import io
import json
import logging
import sys
import types

import pytest
from fastapi.testclient import TestClient

from arcviz import observability, server
from arcviz.envconfig import ConfigError
from arcviz.llm.base import ModelReply, ToolResult, ToolSpec
from arcviz.observability import LogConfig, configure_logging, pseudonym, redact_path
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider, call
from tests.test_optional_sdks import hide


@pytest.fixture(autouse=True)
def reset_logging():
    yield
    observability.reset_logging()


KEY = "pseudonym-key-for-tests-0123456789"


def json_logs(level: str = "DEBUG") -> io.StringIO:
    stream = io.StringIO()
    configure_logging(LogConfig(format="json", level=level, pseudonym_key=KEY), stream=stream)
    return stream


def admin() -> str:
    return pseudonym("admin", KEY)  # off mode acts as the admin


def records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def app_client(tmp_path, monkeypatch, provider=None, **env: str) -> tuple[TestClient, io.StringIO]:
    """An app in JSON mode whose records go to the returned stream."""
    env = {"ARCVIZ_LOG_FORMAT": "json", "ARCVIZ_LOG_PSEUDONYM_KEY": KEY, **env}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=provider or ScriptedProvider([]))
    stream = json_logs()
    return TestClient(app), stream


# ---- configuration ------------------------------------------------------------------------------------------------


def test_defaults_keep_text_logs_and_no_telemetry():
    assert LogConfig.from_env({}) == LogConfig(format="text", level="INFO", telemetry="off")


def test_configuration_from_the_environment():
    config = LogConfig.from_env({"ARCVIZ_LOG_FORMAT": "JSON", "ARCVIZ_LOG_LEVEL": "debug"})
    assert config == LogConfig(format="json", level="DEBUG", telemetry="off")


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"ARCVIZ_LOG_FORMAT": "xml"}, "ARCVIZ_LOG_FORMAT must be one of text, json, got 'xml'."),
        ({"ARCVIZ_LOG_LEVEL": "loud"}, "ARCVIZ_LOG_LEVEL must be one of debug, info, warning, error, got 'loud'."),
        ({"ARCVIZ_TELEMETRY": "yes"}, "ARCVIZ_TELEMETRY must be one of off, otlp, azure-monitor, got 'yes'."),
        (
            {"ARCVIZ_TELEMETRY": "azure-monitor"},
            "ARCVIZ_TELEMETRY=azure-monitor needs APPLICATIONINSIGHTS_CONNECTION_STRING.",
        ),
    ],
)
def test_configuration_problems(environ, message):
    with pytest.raises(ConfigError) as caught:
        LogConfig.from_env(environ)
    assert message in caught.value.problems


@pytest.mark.parametrize("mode", ["otlp", "azure-monitor"])
def test_telemetry_without_the_extra_is_a_start_up_problem(monkeypatch, mode):
    hide(monkeypatch, "opentelemetry", "azure.monitor")
    environ = {"ARCVIZ_TELEMETRY": mode, "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=x"}
    with pytest.raises(ConfigError) as caught:
        LogConfig.from_env(environ)
    assert caught.value.problems == (f'ARCVIZ_TELEMETRY={mode} needs pip install "arcviz-agent[telemetry]".',)


def test_a_malformed_log_variable_stops_the_start_with_the_others(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "xml")
    monkeypatch.setenv("ARCVIZ_MAX_ROUNDS", "x")
    with pytest.raises(ConfigError) as caught:
        server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
    assert len(caught.value.problems) == 2


# ---- the JSON format ----------------------------------------------------------------------------------------------


def test_a_record_is_one_json_object_with_fixed_and_structured_fields():
    stream = json_logs()
    logging.getLogger("arcviz.test").info("hello %s", "world", extra=observability.fields(event="x", n=3, ok=True))
    (record,) = records(stream)
    assert record["message"] == "hello world" and record["level"] == "INFO" and record["logger"] == "arcviz.test"
    assert record["time"].endswith("Z") and "T" in record["time"]
    assert record["event"] == "x" and record["n"] == 3 and record["ok"] is True
    assert "request_id" not in record and "user" not in record  # outside a request


def test_structured_fields_never_override_the_fixed_ones_and_must_be_scalars():
    stream = json_logs()
    logging.getLogger("arcviz.test").warning(
        "m", extra=observability.fields(time="forged", level="DEBUG", user="forged", nested={"a": 1}, items=[1])
    )
    (record,) = records(stream)
    assert record["message"] == "m" and record["level"] == "WARNING" and record["time"] != "forged"
    assert "user" not in record  # a context field, outside a request
    assert record["nested"] == "{'a': 1}" and record["items"] == "[1]"  # stringified, never structures


def test_exceptions_carry_type_and_traceback():
    stream = json_logs()
    try:
        raise KeyError("boom")
    except KeyError:
        logging.getLogger("arcviz.test").exception("failed")
    (record,) = records(stream)
    assert record["exc_type"] == "KeyError" and record["error_code"] and "exc" not in record  # no message


def test_the_level_applies_to_the_apps_loggers():
    stream = json_logs(level="WARNING")
    logging.getLogger("arcviz.test").info("hidden")
    logging.getLogger("arcviz.test").warning("shown")
    assert [r["message"] for r in records(stream)] == ["shown"]


def test_configuring_twice_replaces_the_handler():
    first = json_logs()
    second = json_logs()
    logging.getLogger("arcviz.test").info("once")
    assert first.getvalue() == "" and len(records(second)) == 1


def test_text_mode_leaves_an_existing_root_handler_alone():
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging(LogConfig(format="text"))  # pytest's capture handler is installed
    assert root.handlers == before


def test_text_mode_adds_a_handler_with_the_request_id_when_there_is_none(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    stream = io.StringIO()
    configure_logging(LogConfig(format="text"), stream=stream)
    with observability.request_context("req-1", None, {"type": "http"}):
        logging.getLogger("arcviz.test").warning("inside")
    logging.getLogger("arcviz.test").warning("outside")
    lines = stream.getvalue().splitlines()
    assert lines[0].endswith("WARNING arcviz.test [req-1]: inside")
    assert lines[1].endswith("WARNING arcviz.test: outside")


# ---- request context ----------------------------------------------------------------------------------------------


def test_every_response_has_a_request_id_and_records_carry_it(tmp_path, monkeypatch):
    client, stream = app_client(tmp_path, monkeypatch)
    response = client.get("/api/config")
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    (record,) = [r for r in records(stream) if r.get("event") == "request"]
    assert record["request_id"] == request_id and record["route"] == "/api/config" and record["status"] == 200
    assert record["method"] == "GET" and isinstance(record["duration_ms"], int)
    assert record["user"] == admin()


def test_a_safe_client_request_id_is_logged_apart_and_an_unsafe_one_dropped(tmp_path, monkeypatch):
    client, stream = app_client(tmp_path, monkeypatch)
    rid = client.get("/healthz", headers={"X-Request-ID": "ingress-42.a:b_c"}).headers["x-request-id"]
    assert rid != "ingress-42.a:b_c" and len(rid) == 32  # the server's own id (ADR-0113)
    for bad in ["has space", "x" * 129, "semi;colon", "<script>"]:
        rid = client.get("/healthz", headers={"X-Request-ID": bad}).headers["x-request-id"]
        assert rid != bad and len(rid) == 32
    clients = [r.get("client_request_id") for r in records(stream) if r.get("event") == "request"]
    assert clients == ["ingress-42.a:b_c", None, None, None, None]


def test_the_trace_id_comes_from_a_valid_traceparent(tmp_path, monkeypatch):
    client, stream = app_client(tmp_path, monkeypatch)
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    client.get("/api/config", headers={"traceparent": f"00-{trace}-00f067aa0ba902b7-01"})
    client.get("/api/config", headers={"traceparent": "00-00000000000000000000000000000000-00f067aa0ba902b7-01"})
    client.get("/api/config", headers={"traceparent": "garbage"})
    client.get("/api/config", headers={"traceparent": f"00-{trace}-0000000000000000-01"})  # zero parent id
    client.get("/api/config", headers={"traceparent": f"ff-{trace}-00f067aa0ba902b7-01"})  # forbidden version
    traces = [r.get("trace_id") for r in records(stream) if r.get("event") == "request"]
    assert traces == [trace, None, None, None, None]


def test_requests_are_logged_by_route_template_never_by_path(tmp_path, monkeypatch):
    client, stream = app_client(tmp_path, monkeypatch)
    client.post("/api/diagrams", json={"title": "Secret Merger Plan", "id": "secret-merger-plan"})
    client.get("/api/diagrams/secret-merger-plan?focus=x")
    client.get("/static/app.js")
    client.get("/nowhere/secret-merger-plan")
    routes = [r["route"] for r in records(stream) if r.get("event") == "request"]
    assert routes == ["/api/diagrams", "/api/diagrams/{diagram_id}", "/static", "-"]
    assert "secret-merger-plan" not in stream.getvalue()


def test_probes_are_logged_at_debug_and_server_errors_at_warning(tmp_path, monkeypatch):
    client, stream = app_client(tmp_path, monkeypatch)
    client.get("/healthz")
    client.get("/readyz")
    levels = {r["route"]: r["level"] for r in records(stream) if r.get("event") == "request"}
    assert levels == {"/healthz": "DEBUG", "/readyz": "DEBUG"}


def test_an_unhandled_error_is_logged_with_the_request_id(tmp_path, monkeypatch):
    client, stream = app_client(tmp_path, monkeypatch)

    def broken() -> None:
        raise RuntimeError("defect")

    client.app.add_api_route("/api/broken", broken)
    assert client.get("/api/broken").status_code == 500  # answered by the middleware, not re-raised
    (record,) = [r for r in records(stream) if r.get("event") == "request" and r["status"] == 500]
    assert record["level"] == "ERROR" and record["exc_type"] == "RuntimeError" and record["request_id"]
    assert record["route"] == "/api/broken"


def test_text_mode_writes_no_request_records(tmp_path, monkeypatch):
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
    stream = io.StringIO()
    configure_logging(LogConfig(format="json"), stream=stream)
    observability.reset_logging()
    configure_logging(LogConfig(format="text"))
    TestClient(app).get("/api/config")
    assert stream.getvalue() == ""


def test_records_written_during_a_request_carry_its_context(tmp_path, monkeypatch):
    client, stream = app_client(tmp_path, monkeypatch)
    seen = {}

    def config_spy(*args, **kwargs):
        logging.getLogger("arcviz.services.test").warning("inside the route")
        seen["called"] = True
        return {"mode": "x"}

    monkeypatch.setattr(server.ServiceRoot, "git_status", config_spy)
    response = client.get("/api/config", headers={"X-Request-ID": "abc-123"})
    assert seen
    (inside,) = [r for r in records(stream) if r["message"] == "inside the route"]
    assert inside["request_id"] == response.headers["x-request-id"] and inside["user"] == admin()
    assert inside["client_request_id"] == "abc-123"


# ---- model turns ---------------------------------------------------------------------------------------------------

TOOLS = [ToolSpec("noop", "does nothing", {"type": "object", "properties": {}})]


def ok(_call):
    return ToolResult(_call.id, "fine")


def drain(events):
    return list(events)


def turn_records(stream):
    return [r for r in records(stream) if r.get("event") == "llm_turn"]


def test_a_completed_turn_writes_one_record_with_usage():
    stream = json_logs()
    provider = ScriptedProvider(
        [
            ModelReply(
                "", [call("noop", "c1"), call("noop", "c2")], "tool_use", usage={"input_tokens": 10, "output_tokens": 2}
            ),
            ModelReply("Secret reply text", [], "end_turn", usage={"input_tokens": 20, "output_tokens": 5}),
        ]
    )
    drain(provider.run_turn("system", [], "Secret user message", TOOLS, ok))
    (record,) = turn_records(stream)
    assert record["provider"] == "scripted" and record["model"] == "scripted-1"
    assert record["outcome"] == "done" and record["rounds"] == 2 and record["tool_calls"] == 2
    assert record["input_tokens"] == 30 and record["output_tokens"] == 7 and record["level"] == "INFO"
    assert isinstance(record["duration_ms"], int) and "error_kind" not in record
    assert "Secret" not in stream.getvalue()


@pytest.mark.parametrize(
    ("replies", "kwargs", "kind", "error_type"),
    [
        ([ModelReply("", [], "refusal")], {}, "refusal", None),
        ([ModelReply("cut", [], "max_tokens")], {}, "max_tokens", None),
        ([ModelReply("", [call("noop")], "tool_use")] * 2, {"max_rounds": 2}, "max_rounds", None),
        (
            [ModelReply("", [call("noop")], "tool_use", usage={"input_tokens": 50, "output_tokens": 50})] * 2,
            {"token_budget": 10},
            "budget",
            None,
        ),
    ],
)
def test_failed_turns_name_the_kind_never_the_message(replies, kwargs, kind, error_type):
    stream = json_logs()
    drain(ScriptedProvider(list(replies)).run_turn("s", [], "u", TOOLS, ok, **kwargs))
    (record,) = turn_records(stream)
    assert record["outcome"] == "error" and record["error_kind"] == kind and record["level"] == "WARNING"
    assert record.get("error_type") == error_type


def test_a_provider_exception_records_its_type_only():
    stream = json_logs()

    class Failing(ScriptedProvider):
        def _complete(self, system, messages, tools):
            raise ConnectionError("https://user:secret@example.org refused")

    drain(Failing([]).run_turn("s", [], "u", TOOLS, ok))
    (record,) = turn_records(stream)
    assert record["error_kind"] == "exception" and record["error_type"] == "ConnectionError"
    assert "secret" not in stream.getvalue()


def test_a_closed_stream_writes_an_aborted_record():
    stream = json_logs()
    events = ScriptedProvider([ModelReply("hi", [call("noop")], "tool_use")]).run_turn("s", [], "u", TOOLS, ok)
    next(events)
    events.close()  # the client disconnected
    (record,) = turn_records(stream)
    assert record["outcome"] == "aborted"


def test_single_calls_write_llm_call_records():
    stream = json_logs()
    provider = ScriptedProvider(
        [
            ModelReply("summary", [], "end_turn", usage={"input_tokens": 3, "output_tokens": 1}),
            ModelReply(
                "", [call("submit_review", findings=[])], "tool_use", usage={"input_tokens": 4, "output_tokens": 2}
            ),
            ModelReply("", [], "refusal"),
        ]
    )
    provider.complete_text("s", "u")
    provider.complete_tool("s", "u", ToolSpec("submit_review", "d", {"type": "object"}))
    with pytest.raises(RuntimeError):
        provider.complete_text("s", "u")
    calls = [r for r in records(stream) if r.get("event") == "llm_call"]
    assert [(c["purpose"], c["outcome"]) for c in calls] == [
        ("text", "done"),
        ("submit_review", "done"),
        ("text", "error"),
    ]
    assert calls[0]["input_tokens"] == 3 and calls[1]["output_tokens"] == 2


# ---- path redaction -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "redacted"),
    [
        ("/api/diagrams/secret-plan/chat", "/api/diagrams/{id}/chat"),
        ("/api/projects/p1/access/ada@example.org", "/api/projects/{id}/access/{id}"),
        ("/api/diagrams/d/versions/3/restore", "/api/diagrams/{id}/versions/{id}/restore"),
        ("/api/config", "/api/config"),
        ("/static/app.js", "/static"),
        ("/docs/guide/chat/", "/docs"),
        ("/healthz", "/healthz"),
        ("/anything/else", "-"),
    ],
)
def test_redact_path(path, redacted):
    assert redact_path(path) == redacted


# ---- telemetry ---------------------------------------------------------------------------------------------------


def test_otlp_telemetry_exports_request_spans_without_ids(tmp_path, monkeypatch, fresh_tracing):
    monkeypatch.setattr(observability, "_telemetry_started", None)
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    log_exporter = InMemoryLogRecordExporter()
    monkeypatch.setattr(observability, "_span_exporter", lambda: exporter)
    monkeypatch.setattr(observability, "_span_processor", SimpleSpanProcessor)
    monkeypatch.setattr(observability, "_log_exporter", lambda: log_exporter)
    monkeypatch.setenv("ARCVIZ_TELEMETRY", "otlp")
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "json")  # request records
    monkeypatch.setenv("ARCVIZ_LOG_PSEUDONYM_KEY", KEY)
    app_logger = logging.getLogger("arcviz")
    before = list(app_logger.handlers)
    try:
        app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
        client = TestClient(app)
        client.post("/api/diagrams", json={"title": "Secret Plan", "id": "secret-plan"})
        client.get("/api/diagrams/secret-plan?focus=top-secret", headers={"X-Request-ID": "otlp-req-1"})
        client.get("/healthz")
        added = [h for h in app_logger.handlers if h not in before]
        assert added and all(any(isinstance(f, observability.ContextFilter) for f in h.filters) for h in added)
        for handler in added:
            handler.flush()
    finally:
        for handler in [h for h in app_logger.handlers if h not in before]:
            app_logger.removeHandler(handler)
    exported = [item.log_record for item in log_exporter.get_finished_logs()]
    request = [r for r in exported if (r.attributes or {}).get("client_request_id") == "otlp-req-1"]
    assert request and request[0].attributes["user"] == admin()  # exported records correlate too
    assert len(request[0].attributes["request_id"]) == 32
    spans = exporter.get_finished_spans()
    servers = [s for s in spans if s.kind.name == "SERVER"]
    assert {s.attributes.get("http.route") for s in servers} == {"/api/diagrams", "/api/diagrams/{diagram_id}"}
    for span in spans:
        for value in span.attributes.values():
            assert "secret-plan" not in str(value) and "top-secret" not in str(value), span.attributes


def test_azure_monitor_telemetry_is_configured_once_for_the_apps_loggers(tmp_path, monkeypatch):
    monkeypatch.setattr(observability, "_telemetry_started", None)
    calls: list[dict] = []
    fake = types.ModuleType("azure.monitor.opentelemetry")
    fake.configure_azure_monitor = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", fake)
    instrumented: list = []
    monkeypatch.setattr(observability, "_instrument", lambda app: instrumented.append(app))
    monkeypatch.setenv("ARCVIZ_TELEMETRY", "azure-monitor")
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=00000000-0000-0000-0000-000000000000"
    )
    first = server.create_app(store=FileStore(tmp_path / "a"), provider=ScriptedProvider([]))
    second = server.create_app(store=FileStore(tmp_path / "b"), provider=ScriptedProvider([]))
    assert len(calls) == 1  # providers are process-wide
    assert calls[0]["logger_name"] == "arcviz"
    options = calls[0]["instrumentation_options"]
    assert options["fastapi"] == {"enabled": False}  # instrumented explicitly per app
    assert all(options[name] == {"enabled": False} for name in ("requests", "urllib", "urllib3", "azure_sdk"))
    assert instrumented == [first, second]  # every app gets its request spans


def test_main_hands_logging_to_the_app_in_json_mode(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "json")
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: seen.update(kwargs))
    server.main()
    assert seen["log_config"] is None and seen["access_log"] is False


def test_main_keeps_uvicorns_logging_in_text_mode(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: seen.update(kwargs))
    server.main()
    assert "log_config" not in seen and "access_log" not in seen
