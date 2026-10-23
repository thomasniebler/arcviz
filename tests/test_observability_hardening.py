"""Hardening of logs and telemetry after review (#83; spec: operations.md §4, ADR-0112, ADR-0113).

Exceptions without messages, one 500 with a request id, span attributes by allow-list, keyed pseudonyms, exact probe
exclusion, the server's own request id, and the shared `installed` helper.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from arcviz import envconfig, observability, server
from arcviz.envconfig import ConfigError, installed
from arcviz.observability import LogConfig, configure_logging
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider

KEY = "pseudonym-key-for-tests-0123456789"
SECRET_EMAIL = "carol.secret@example.org"


@pytest.fixture(autouse=True)
def reset_logging():
    yield
    observability.reset_logging()


def json_logs(**config) -> io.StringIO:
    stream = io.StringIO()
    configure_logging(LogConfig(format="json", level="DEBUG", **{"pseudonym_key": KEY, **config}), stream=stream)
    return stream


def records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def client_of(tmp_path, monkeypatch, **env) -> tuple[TestClient, io.StringIO]:
    for name, value in {"ARCVIZ_LOG_FORMAT": "json", "ARCVIZ_LOG_PSEUDONYM_KEY": KEY, **env}.items():
        monkeypatch.setenv(name, value)
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
    stream = json_logs()
    return TestClient(app), stream


def expected_pseudonym(user_id: str, key: str = KEY) -> str:
    return "u-" + hmac.new(key.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]


# ---- 1. exceptions without messages ---------------------------------------------------------------------------------


def raise_with_secret() -> None:
    raise LookupError(f"no user {SECRET_EMAIL}")


def test_json_records_carry_type_code_and_frames_but_never_the_message():
    stream = json_logs()
    try:
        raise_with_secret()
    except LookupError:
        logging.getLogger("arcviz.test").exception("failed")
    (record,) = records(stream)
    assert SECRET_EMAIL not in json.dumps(record)
    assert record["exc_type"] == "LookupError" and "exc" not in record
    assert "raise_with_secret" in record["exc_frames"] and "test_observability_hardening.py:" in record["exc_frames"]
    assert len(record["error_code"]) == 12


def test_the_error_code_is_stable_for_the_same_failure_site():
    stream = json_logs()
    for _ in range(2):
        try:
            raise_with_secret()
        except LookupError:
            logging.getLogger("arcviz.test").exception("failed")
    first, second = records(stream)
    assert first["error_code"] == second["error_code"]


def test_exception_details_only_with_the_opt_in_flag():
    stream = json_logs(exception_details=True)
    try:
        raise_with_secret()
    except LookupError:
        logging.getLogger("arcviz.test").exception("failed")
    (record,) = records(stream)
    assert SECRET_EMAIL in record["exc"]
    assert LogConfig.from_env({"ARCVIZ_LOG_EXCEPTION_DETAILS": "1"}).exception_details is True
    assert LogConfig.from_env({}).exception_details is False
    with pytest.raises(ConfigError, match="ARCVIZ_LOG_EXCEPTION_DETAILS"):
        LogConfig.from_env({"ARCVIZ_LOG_EXCEPTION_DETAILS": "maybe"})


def test_uvicorn_error_records_are_scrubbed_too():
    stream = json_logs()
    try:
        raise_with_secret()
    except LookupError:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=True)
    (record,) = records(stream)
    assert SECRET_EMAIL not in json.dumps(record) and record["exc_type"] == "LookupError"


# ---- 2. one 500 with a request id -----------------------------------------------------------------------------------


def test_an_unhandled_error_answers_500_with_the_request_id_and_one_record(tmp_path, monkeypatch, caplog):
    client, stream = client_of(tmp_path, monkeypatch)

    def broken() -> None:
        raise_with_secret()

    client.app.add_api_route("/api/broken", broken)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/broken")
    assert response.status_code == 500 and response.headers["x-request-id"]
    assert response.json() == {"detail": "Internal Server Error", "request_id": response.headers["x-request-id"]}
    errors = [r for r in records(stream) if r.get("exc_type")]
    assert len(errors) == 1 and errors[0]["request_id"] == response.headers["x-request-id"]
    assert errors[0]["event"] == "request" and errors[0]["status"] == 500
    assert not [r for r in caplog.records if r.name.startswith("uvicorn")]  # no second, context-less traceback
    assert SECRET_EMAIL not in stream.getvalue() + response.text


def test_text_mode_logs_the_error_once_too(tmp_path, monkeypatch, caplog):
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))

    def broken() -> None:
        raise RuntimeError("defect")

    app.add_api_route("/api/broken", broken)
    with caplog.at_level(logging.ERROR):
        response = TestClient(app).get("/api/broken")
    assert response.status_code == 500
    assert len([r for r in caplog.records if r.exc_info]) == 1


# ---- 3. span attributes by allow-list -------------------------------------------------------------------------------


def test_spans_carry_only_allowed_attributes(tmp_path, monkeypatch, fresh_tracing):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(observability, "_telemetry_started", None)
    monkeypatch.setattr(observability, "_span_exporter", lambda: exporter)
    monkeypatch.setattr(observability, "_span_processor", SimpleSpanProcessor)
    monkeypatch.setenv("ARCVIZ_TELEMETRY", "otlp")
    app_logger = logging.getLogger("arcviz")
    before = list(app_logger.handlers)
    try:
        app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
        client = TestClient(app)
        client.get("/api/config", headers={"User-Agent": "Secret-Agent/1.0", "Host": "tenant.secret.example"})
        client.get("/api/diagrams/healthz-x")  # an id that merely contains a probe name
        client.get("/healthz")
    finally:
        for handler in [h for h in app_logger.handlers if h not in before]:
            app_logger.removeHandler(handler)
    servers = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    routes = {s.attributes.get("http.route") for s in servers}
    assert routes == {"/api/config", "/api/diagrams/{diagram_id}"}  # 6: /healthz excluded, healthz-x is not
    for span in exporter.get_finished_spans():
        attributes = dict(span.attributes or {})
        assert set(attributes) <= observability.SPAN_ATTRIBUTES, attributes
        assert "Secret-Agent" not in str(attributes) and "tenant.secret" not in str(attributes)
        for event in span.events:
            assert set(event.attributes or {}) <= {"exception.type"}


def test_the_probe_exclusion_is_anchored():
    assert observability.EXCLUDED_URLS.split(",") and all("$" in p for p in observability.EXCLUDED_URLS.split(","))


# ---- 4. keyed pseudonyms --------------------------------------------------------------------------------------------


def test_the_user_field_is_a_keyed_hmac(tmp_path, monkeypatch):
    client, stream = client_of(tmp_path, monkeypatch)
    client.get("/api/config")
    (record,) = [r for r in records(stream) if r.get("event") == "request"]
    assert record["user"] == expected_pseudonym("admin")
    assert record["user"] != "u-" + hashlib.sha256(b"admin").hexdigest()[:32]  # not guessable without the key


def test_without_a_key_the_user_field_is_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "json")
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
    stream = io.StringIO()
    configure_logging(LogConfig(format="json"), stream=stream)
    TestClient(app).get("/api/config")
    (record,) = [r for r in records(stream) if r.get("event") == "request"]
    assert "user" not in record


def test_the_key_is_validated_and_never_shown():
    with pytest.raises(ConfigError, match="ARCVIZ_LOG_PSEUDONYM_KEY must be at least 16 characters"):
        LogConfig.from_env({"ARCVIZ_LOG_PSEUDONYM_KEY": "short"})
    config = LogConfig.from_env({"ARCVIZ_LOG_PSEUDONYM_KEY": KEY})
    assert config.pseudonym_key == KEY and KEY not in repr(config)


# ---- 7. the server's own request id ---------------------------------------------------------------------------------


def test_the_server_always_generates_its_request_id_and_logs_the_clients_separately(tmp_path, monkeypatch):
    client, stream = client_of(tmp_path, monkeypatch)
    response = client.get("/api/config", headers={"X-Request-ID": "ingress-42.a:b_c"})
    assert response.headers["x-request-id"] != "ingress-42.a:b_c" and len(response.headers["x-request-id"]) == 32
    assert response.headers.get_list("x-request-id") == [response.headers["x-request-id"]]
    client.get("/api/config", headers={"X-Request-ID": "has space<script>"})
    first, second = [r for r in records(stream) if r.get("event") == "request"]
    assert first["client_request_id"] == "ingress-42.a:b_c" and first["request_id"] == response.headers["x-request-id"]
    assert "client_request_id" not in second  # not a safe token: dropped


def test_an_existing_response_header_is_replaced_not_duplicated(tmp_path, monkeypatch):
    client, _ = client_of(tmp_path, monkeypatch)

    def echo():
        from fastapi.responses import JSONResponse

        return JSONResponse({}, headers={"X-Request-ID": "set-by-the-route"})

    client.app.add_api_route("/api/echo", echo)
    response = client.get("/api/echo")
    assert len(response.headers.get_list("x-request-id")) == 1
    assert response.headers["x-request-id"] != "set-by-the-route"


# ---- 8. one `installed` helper --------------------------------------------------------------------------------------


def test_installed_handles_missing_parents_and_stand_ins(monkeypatch):
    import sys
    import types

    assert installed("json") is True
    assert installed("no_such_package_xyz.sub") is False
    stand_in = types.ModuleType("arcviz_stand_in")
    monkeypatch.setitem(sys.modules, "arcviz_stand_in", stand_in)
    assert installed("arcviz_stand_in") is True  # a module without a spec
    assert not hasattr(observability, "_installed") and envconfig.installed is installed


def test_span_events_keep_only_the_exception_type():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(observability.event_scrubber())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("test").start_as_current_span("s") as span:
        span.record_exception(LookupError(f"no user {SECRET_EMAIL}"))
    (finished,) = exporter.get_finished_spans()
    assert [dict(e.attributes) for e in finished.events] == [{"exception.type": "LookupError"}]
