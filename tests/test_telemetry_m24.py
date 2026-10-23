"""Telemetry follow-ups of M9 (#187, ADR-0134): keyless Azure Monitor export, privacy-safe metrics, spans inside chat
turns. Everything is optional (the `telemetry` extra) and off without ARCVIZ_TELEMETRY; spans and metrics carry only
allow-listed attributes: route templates, fixed names and counts, never ids, raw paths, user ids or content."""

from __future__ import annotations

import logging
import sys
import types

import pytest
from fastapi.testclient import TestClient

from arcviz import observability, server, telemetry
from arcviz.envconfig import ConfigError
from arcviz.llm.base import ModelReply
from arcviz.observability import LogConfig
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider, call

SECRET = "Geheimprojekt-Zeta"  # in a diagram id, a node name and the chat message: must never be exported


@pytest.fixture
def fresh_metrics(monkeypatch):
    """The process-wide meter provider slot back afterwards (like `fresh_tracing` for tracing)."""
    from opentelemetry import metrics
    from opentelemetry.util._once import Once

    monkeypatch.setattr(metrics._internal, "_METER_PROVIDER_SET_ONCE", Once())
    monkeypatch.setattr(metrics._internal, "_METER_PROVIDER", None)
    yield


@pytest.fixture
def otlp(tmp_path, monkeypatch, fresh_tracing, fresh_metrics):
    """Telemetry on (OTLP) with in-memory exporters; returns (spans exporter, metric reader)."""
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    monkeypatch.setattr(observability, "_telemetry_started", None)
    monkeypatch.setattr(telemetry, "_enabled", False)
    monkeypatch.setattr(observability, "_span_exporter", lambda: spans)
    monkeypatch.setattr(observability, "_span_processor", SimpleSpanProcessor)
    monkeypatch.setattr(observability, "_metric_reader", lambda: reader)
    monkeypatch.setattr(observability, "_log_exporter", InMemoryLogRecordExporter)
    monkeypatch.setenv("ARCVIZ_TELEMETRY", "otlp")
    app_logger = logging.getLogger("arcviz")
    before = list(app_logger.handlers)
    yield spans, reader
    for handler in [h for h in app_logger.handlers if h not in before]:
        app_logger.removeHandler(handler)


def metric_points(reader) -> dict[str, list]:
    data = reader.get_metrics_data()
    out: dict[str, list] = {}
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out.setdefault(metric.name, []).extend(metric.data.data_points)
    return out


def chat_app(tmp_path, replies):
    provider = ScriptedProvider(replies)
    provider.model = "claude-test"
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=provider)
    client = TestClient(app)
    diagram = client.post("/api/diagrams", json={"title": SECRET, "id": SECRET.lower(), "template": "demo"})
    return client, diagram.json()["diagram"]["id"]


def turn_replies():
    return [
        ModelReply(
            text="",
            tool_calls=[
                call("add_node", "c1", name=SECRET, kind="service", parent="prod-deployment"),
                call("delete_everything_" + SECRET, "c2"),  # a tool the model made up: its name is never exported
            ],
            stop_reason="tool_use",
            usage={"input_tokens": 120, "output_tokens": 30},
        ),
        ModelReply(text=f"Fertig mit {SECRET}.", tool_calls=[], stop_reason="end_turn", usage={"input_tokens": 200}),
    ]


def everything_exported(spans, reader) -> str:
    texts = []
    for span in spans.get_finished_spans():
        texts += [span.name, str(dict(span.attributes or {})), str(span.status.description)]
        texts += [str(dict(e.attributes or {})) for e in span.events]
    for points in metric_points(reader).values():
        texts += [str(dict(p.attributes)) for p in points]
    return "\n".join(texts)


# ---- spans inside a chat turn --------------------------------------------------------------------------------------


def test_a_chat_turn_has_spans_for_llm_calls_tools_and_store_writes(tmp_path, otlp):
    spans, reader = otlp
    client, diagram_id = chat_app(tmp_path, turn_replies())
    response = client.post(f"/api/diagrams/{diagram_id}/chat", json={"message": f"Baue {SECRET}"})
    assert response.status_code == 200
    finished = spans.get_finished_spans()
    by_name: dict[str, list] = {}
    for span in finished:
        by_name.setdefault(span.name, []).append(span)
    assert len(by_name["arcviz.llm.call"]) == 2
    assert [dict(s.attributes) for s in by_name["arcviz.llm.call"]][0] == {
        "gen_ai.system": "scripted",
        "gen_ai.request.model": "claude-test",
        "arcviz.llm.round": 1,
        "arcviz.llm.outcome": "ok",
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 30,
    }
    tools = by_name["arcviz.tool"]
    assert [s.attributes["arcviz.tool.name"] for s in tools] == ["add_node", "unknown"]
    assert [s.attributes["arcviz.tool.outcome"] for s in tools] == ["ok", "error"]
    writes: dict[str, list] = {}
    for span in by_name["arcviz.store.write"]:
        writes.setdefault(span.attributes["arcviz.store.operation"], []).append(span)
    assert set(writes) == {"write_through", "commit_turn", "append_chat"}
    assert [s.attributes["arcviz.store.outcome"] for s in writes["write_through"]] == ["written", "rejected"]
    assert [s.parent.span_id for s in writes["write_through"]] == [t.context.span_id for t in tools]  # inside the tools
    assert writes["commit_turn"][0].attributes["arcviz.store.outcome"] == "ok"
    request = next(s for s in finished if s.kind.name == "SERVER" and s.attributes["http.route"].endswith("/chat"))
    assert tools[0].parent.span_id == request.context.span_id
    assert by_name["arcviz.llm.call"][0].parent.span_id == request.context.span_id
    allowed = observability.SPAN_ATTRIBUTES | telemetry.SPAN_ATTRIBUTES
    for span in finished:
        assert set(span.attributes or {}) <= allowed, (span.name, dict(span.attributes))
    assert SECRET.lower() not in everything_exported(spans, reader).lower()


def test_a_project_turn_records_its_store_writes_too(tmp_path, otlp):
    spans, _ = otlp
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("create_diagram", "p1", title=SECRET, type="activity")],
                stop_reason="tool_use",
            ),
            ModelReply(text="ok", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    client = TestClient(server.create_app(store=FileStore(tmp_path / "data"), provider=provider))
    project = client.post("/api/projects", json={"title": "Shop"}).json()["project"]["id"]
    assert client.post(f"/api/projects/{project}/chat", json={"message": "neu"}).status_code == 200
    names = [s.name for s in spans.get_finished_spans()]
    assert "arcviz.tool" in names and "arcviz.store.write" in names
    tools = [s for s in spans.get_finished_spans() if s.name == "arcviz.tool"]
    assert tools[0].attributes["arcviz.tool.name"] == "create_diagram"


def test_a_failing_llm_call_records_the_error_type_only(tmp_path, otlp):
    spans, _ = otlp

    class Failing(ScriptedProvider):
        def _complete(self, system, messages, tools):
            raise RuntimeError(f"upstream said {SECRET}")

    app = server.create_app(store=FileStore(tmp_path / "data"), provider=Failing([]))
    client = TestClient(app)
    diagram_id = client.post("/api/diagrams", json={"title": "x", "template": "demo"}).json()["diagram"]["id"]
    client.post(f"/api/diagrams/{diagram_id}/chat", json={"message": "hallo"})
    (llm,) = [s for s in spans.get_finished_spans() if s.name == "arcviz.llm.call"]
    assert llm.attributes["arcviz.llm.outcome"] == "error"
    assert llm.status.status_code.name == "ERROR" and llm.status.description == "RuntimeError"
    assert all(SECRET not in str(dict(e.attributes or {})) for e in llm.events)


def test_without_telemetry_nothing_is_traced_or_measured(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "_enabled", False)
    monkeypatch.delenv("ARCVIZ_TELEMETRY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("OpenTelemetry touched while telemetry is off")

    from opentelemetry import metrics, trace

    monkeypatch.setattr(trace, "get_tracer", forbidden)
    monkeypatch.setattr(metrics, "get_meter", forbidden)
    client, diagram_id = chat_app(tmp_path, turn_replies())
    assert client.post(f"/api/diagrams/{diagram_id}/chat", json={"message": "x"}).status_code == 200
    with telemetry.span("arcviz.tool", {"arcviz.tool.name": "add_node"}) as span:
        span.set("arcviz.tool.outcome", "ok")


# ---- metrics ---------------------------------------------------------------------------------------------------


def test_metrics_per_route_template_llm_and_live_streams_without_raw_targets(tmp_path, otlp):
    spans, reader = otlp
    client, diagram_id = chat_app(tmp_path, turn_replies())
    client.get(f"/api/diagrams/{diagram_id}?focus=top-{SECRET}")
    client.get(f"/api/nothing/{SECRET}")  # unmatched: no template
    client.request("PROPFIND", "/api/config")  # an unusual method is not a dimension of its own
    client.get("/healthz")  # probes are not measured
    client.post(f"/api/diagrams/{diagram_id}/chat", json={"message": f"Baue {SECRET}"})
    points = metric_points(reader)
    requests = points["arcviz.http.server.request.duration"]
    routes = {p.attributes["http.route"] for p in requests}
    assert "/api/diagrams/{diagram_id}" in routes and "/api/diagrams/{diagram_id}/chat" in routes
    assert "-" in routes and "/healthz" not in routes
    assert {p.attributes["http.request.method"] for p in requests} >= {"GET", "POST", "_OTHER"}
    for point in requests:
        assert set(point.attributes) == {"http.route", "http.request.method", "http.response.status_code"}
    tokens = {
        (p.attributes["gen_ai.token.type"], p.attributes["arcviz.llm.kind"]): p.value
        for p in points["arcviz.llm.tokens"]
    }
    assert tokens == {("input", "turn"): 320, ("output", "turn"): 30}
    (duration,) = points["arcviz.llm.duration"]
    assert dict(duration.attributes) == {
        "gen_ai.system": "scripted",
        "gen_ai.request.model": "claude-test",
        "arcviz.llm.kind": "turn",
        "arcviz.llm.outcome": "done",
    }
    assert duration.count == 1
    (streams,) = points["arcviz.live.streams"]
    assert streams.value == 0 and dict(streams.attributes) == {}
    for name, series in points.items():
        assert name.startswith("arcviz."), name
        for point in series:
            assert set(point.attributes) <= telemetry.METRIC_ATTRIBUTES, (name, dict(point.attributes))
    assert SECRET.lower() not in everything_exported(spans, reader).lower()


def test_the_live_stream_gauge_counts_open_streams(tmp_path, otlp):
    _, reader = otlp
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))
    app.state.hub.streams = lambda: 3  # type: ignore[method-assign]
    (streams,) = metric_points(reader)["arcviz.live.streams"]
    assert streams.value == 3


def test_metric_attributes_are_filtered_by_the_allow_list(otlp, tmp_path):
    _, reader = otlp
    server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))  # starts telemetry
    assert telemetry.metric_attributes({"http.route": "/x", "user": "ada", "url.full": "/api/diagrams/a"}) == {
        "http.route": "/x"
    }
    assert telemetry.span_attributes({"arcviz.tool.name": "add_node", "diagram": "a", "x": object()}) == {
        "arcviz.tool.name": "add_node"
    }
    telemetry.record_llm("call", "scripted", "m", "done", 0.5, {"input_tokens": 3, "cache_read_input_tokens": 9})
    points = metric_points(reader)
    assert all(set(p.attributes) <= telemetry.METRIC_ATTRIBUTES for ps in points.values() for p in ps)
    assert {p.attributes["gen_ai.token.type"] for p in points["arcviz.llm.tokens"]} == {"input"}


# ---- Entra ID for the Azure Monitor exporter ---------------------------------------------------------------------


def fake_azure(monkeypatch) -> tuple[list[dict], list]:
    calls: list[dict] = []
    credentials: list = []
    distro = types.ModuleType("azure.monitor.opentelemetry")
    distro.configure_azure_monitor = lambda **kwargs: calls.append(kwargs)
    identity = types.ModuleType("azure.identity")

    class DefaultAzureCredential:
        def __init__(self, **kwargs):
            credentials.append(self)

    identity.DefaultAzureCredential = DefaultAzureCredential
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", distro)
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setattr(observability, "_telemetry_started", None)
    monkeypatch.setattr(telemetry, "_enabled", False)
    return calls, credentials


def test_azure_monitor_with_entra_passes_a_credential_and_no_key_is_needed(monkeypatch, fresh_metrics):
    calls, credentials = fake_azure(monkeypatch)
    env = {
        "ARCVIZ_TELEMETRY": "azure-monitor",
        "ARCVIZ_TELEMETRY_AUTH": "entra",
        # endpoint and the resource's id only; with local authentication disabled it grants nothing by itself
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://westeurope-5.in.applicationinsights.azure.com/",
    }
    config = LogConfig.from_env(env)
    assert config.telemetry_auth == "entra"
    try:
        observability.start_telemetry(config)
    finally:
        observability.reset_logging()
    assert len(calls) == 1 and calls[0]["credential"] is credentials[0]


def test_azure_monitor_with_the_connection_string_passes_no_credential(monkeypatch, fresh_metrics):
    calls, credentials = fake_azure(monkeypatch)
    config = LogConfig.from_env(
        {"ARCVIZ_TELEMETRY": "azure-monitor", "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=x"}
    )
    try:
        observability.start_telemetry(config)
    finally:
        observability.reset_logging()
    assert "credential" not in calls[0] and credentials == []


@pytest.mark.parametrize(
    "env, problem",
    [
        ({"ARCVIZ_TELEMETRY": "otlp", "ARCVIZ_TELEMETRY_AUTH": "entra"}, "ARCVIZ_TELEMETRY_AUTH=entra"),
        (
            {"ARCVIZ_TELEMETRY": "azure-monitor", "ARCVIZ_TELEMETRY_AUTH": "entra"},
            "APPLICATIONINSIGHTS_CONNECTION_STRING",
        ),
        ({"ARCVIZ_TELEMETRY_AUTH": "password"}, "ARCVIZ_TELEMETRY_AUTH"),
    ],
)
def test_telemetry_auth_is_checked_at_start_up(env, problem):
    with pytest.raises(ConfigError, match=problem):
        LogConfig.from_env(env)
