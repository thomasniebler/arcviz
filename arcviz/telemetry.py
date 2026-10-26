"""Spans and metrics of the app's own work (#187, ADR-0134; spec: docs/architecture/specs/follow-ups.md).

The instrumentation API the request middleware, the LLM tool loop and the agents call. Everything is a no-op until
`observability.start_telemetry` called `enable()` (ARCVIZ_TELEMETRY, the optional `telemetry` extra): no OpenTelemetry
import, no allocation beyond a shared no-op object. No package imports, so `llm/` may use it.

Privacy (ADR-0112, ADR-0134): spans and metric points carry only the attributes in `SPAN_ATTRIBUTES` /
`METRIC_ATTRIBUTES` (anything else is dropped here, and the span scrubber drops it again at the end), and only values
that cannot hold content: route templates (never a raw path), fixed names (tool names the turn offered, else
`unknown`; provider and model from the configuration), outcomes and counts. Never ids, paths, user ids or text. An
exception is recorded by its type only.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

TRACER = "arcviz"
METER = "arcviz"
# Attributes the app's own spans may carry (the request spans have observability.SPAN_ATTRIBUTES).
SPAN_ATTRIBUTES = frozenset(
    {
        "gen_ai.system",  # the provider name (configuration)
        "gen_ai.request.model",  # the configured model
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "arcviz.llm.round",
        "arcviz.llm.outcome",  # ok | error
        "arcviz.tool.name",  # a tool the turn (or the MCP endpoint) offered, else "unknown"
        "arcviz.tool.outcome",  # ok | error
        "arcviz.store.operation",  # write_through | commit_turn | append_chat
        "arcviz.store.outcome",  # the write's outcome (written, rejected, deleted, forbidden, busy, ok, skipped, error)
        "arcviz.steward.outcome",  # a steward run's outcome (spec: project-steward.md): fixed names, never content
    }
)
METRIC_ATTRIBUTES = frozenset(
    {
        "http.route",  # the route template (observability.route_of), never the raw path
        "http.request.method",  # a standard method, else _OTHER
        "http.response.status_code",
        "gen_ai.system",
        "gen_ai.request.model",
        "gen_ai.token.type",  # input | output
        "arcviz.llm.kind",  # turn | call
        "arcviz.llm.outcome",
        "arcviz.tool.name",  # an MCP tool of mcp_server.TOOL_NAMES, else "unknown"
        "arcviz.tool.outcome",  # ok | error
        "db.operation.name",  # a Cosmos DB request kind of STORE_OPERATIONS (#201)
        "arcviz.store.document",  # the kind of document a request addressed, one of STORE_DOCUMENTS (#201)
        "arcviz.steward.outcome",  # unchanged | clean | proposed | blocked | disabled | capped
        "arcviz.steward.verdict",  # what happened to a model candidate: supported | unsupported | unclear | proposed
    }
)
# The values of the request unit attributes (#201, ADR-0154): fixed names, never an id, a partition key or a query.
STORE_OPERATIONS = frozenset({"read", "create", "upsert", "replace", "delete", "patch", "batch", "query"})
STORE_DOCUMENTS = frozenset(
    {
        "diagram",  # a diagram's partition: latest, versions, transcript
        "project",  # a project item and its transcript
        "presence",  # the presence relay (ADR-0104)
        # keyed documents (store.base.DOCUMENT_KINDS; tests/test_request_units.py checks the list is complete)
        "workspace",
        "user",
        "placement",
        "listing",
        "rename",
        "location",
        "writer",
        "proposal",
        "token",
        "requirements",
        "reqchat",
        "steward",
        "any",  # a query across partitions
        "other",  # anything else (never the partition key itself)
    }
)
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"})
TOKEN_TYPES = {"input_tokens": "input", "output_tokens": "output"}
_PRIMITIVES = (str, bool, int, float)

_enabled = False
_lock = threading.Lock()
_instruments: dict[str, Any] = {}
_instruments_of: Any = None  # the meter provider the instruments belong to
_stream_sources: weakref.WeakSet[Any] = weakref.WeakSet()  # objects with `streams() -> int` (live hubs)


def enable() -> None:
    """Called once telemetry export is configured (process-wide)."""
    global _enabled
    _enabled = True


def enabled() -> bool:
    return _enabled


def span_attributes(values: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if k in SPAN_ATTRIBUTES and isinstance(v, _PRIMITIVES)}


def metric_attributes(values: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if k in METRIC_ATTRIBUTES and isinstance(v, _PRIMITIVES)}


def store_document(partition: str | None) -> str:
    """The document kind of a Cosmos DB partition key as a metric may show it: `<kind>:<key>` gives the kind (when it
    is a known one), a bare key is a diagram's partition, None a query across partitions (#201)."""
    if partition is None:
        return "any"
    if ":" not in partition:
        return "diagram"
    kind = partition.split(":", 1)[0]
    return kind if kind in STORE_DOCUMENTS else "other"


def tool_name(name: str, offered: Collection[str]) -> str:
    """A tool name the model used, as a span may show it: only names the turn offered (a model can make up any
    string, which could carry content)."""
    return name if name in offered else "unknown"


# ---- spans ---------------------------------------------------------------------------------------------------------


class Span:
    """A started span (or the shared no-op one while telemetry is off)."""

    def __init__(self, span: Any = None) -> None:
        self._span = span

    def set(self, key: str, value: Any) -> None:
        if self._span is not None and key in SPAN_ATTRIBUTES and isinstance(value, _PRIMITIVES):
            self._span.set_attribute(key, value)

    def failed(self, error: BaseException | str) -> None:
        """Error status with the exception's type only (never its message)."""
        if self._span is None:
            return
        from opentelemetry.trace import Status, StatusCode

        name = error if isinstance(error, str) else type(error).__name__
        self._span.set_status(Status(StatusCode.ERROR, name))

    def end(self) -> None:
        if self._span is not None:
            self._span.end()


NOOP = Span()


def context() -> Any:
    """The current trace context (to parent spans started later on another thread or across a generator's yields)."""
    if not _enabled:
        return None
    from opentelemetry import context as otel_context

    return otel_context.get_current()


def start(name: str, attributes: Mapping[str, Any] | None = None, parent: Any = None) -> Span:
    """A span that is *not* made current, ended by `Span.end()`: for work that spans yields of a generator (a streamed
    model call), where attaching a context would be detached on another thread."""
    if not _enabled:
        return NOOP
    from opentelemetry import trace

    tracer = trace.get_tracer(TRACER)
    return Span(tracer.start_span(name, context=parent, attributes=span_attributes(attributes or {})))


@contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
    """A span made current for a synchronous block (no yield inside); an exception sets the error status (its type)."""
    if not _enabled:
        yield NOOP
        return
    from opentelemetry import trace

    tracer = trace.get_tracer(TRACER)
    with tracer.start_as_current_span(
        name, attributes=span_attributes(attributes or {}), record_exception=False, set_status_on_exception=False
    ) as current:
        handle = Span(current)
        try:
            yield handle
        except BaseException as exc:
            handle.failed(exc)
            raise


# ---- metrics -------------------------------------------------------------------------------------------------------


def _meter_instruments() -> dict[str, Any]:
    """The instruments of the current meter provider (created again when a new provider was installed)."""
    global _instruments_of
    from opentelemetry import metrics

    provider = metrics.get_meter_provider()
    with _lock:
        if _instruments_of is provider and _instruments:
            return _instruments
        meter = metrics.get_meter(METER)
        _instruments.clear()
        _instruments["request"] = meter.create_histogram(
            "arcviz.http.server.request.duration", unit="s", description="Duration of HTTP requests by route template"
        )
        _instruments["llm_duration"] = meter.create_histogram(
            "arcviz.llm.duration", unit="s", description="Duration of chat turns and single model calls"
        )
        _instruments["llm_tokens"] = meter.create_counter(
            "arcviz.llm.tokens", unit="{token}", description="Model tokens used, by type"
        )
        _instruments["mcp_calls"] = meter.create_histogram(
            "arcviz.mcp.calls.duration", unit="s", description="Duration of MCP tool calls by tool and outcome"
        )
        _instruments["request_units"] = meter.create_histogram(
            "arcviz.store.request_units",
            unit="{request_unit}",
            description="Cosmos DB request charge per request, by operation and document kind",
        )
        _instruments["steward_runs"] = meter.create_counter(
            "arcviz.steward.runs", unit="{run}", description="Steward runs, by outcome"
        )
        _instruments["steward_items"] = meter.create_counter(
            "arcviz.steward.items", unit="{item}", description="Steward candidates and items, by verdict"
        )
        meter.create_observable_up_down_counter(
            "arcviz.live.streams", callbacks=[_observe_streams], unit="{stream}", description="Open live streams"
        )
        _instruments_of = provider
        return _instruments


def _observe_streams(_: Any) -> Iterator[Any]:
    from opentelemetry.metrics import Observation

    yield Observation(sum(source.streams() for source in list(_stream_sources)))


def observe_live_streams(source: Any) -> None:
    """Count `source.streams()` (a live hub) in the `arcviz.live.streams` gauge; held weakly."""
    _stream_sources.add(source)
    if _enabled:
        _meter_instruments()


def record_request(route: str, method: str, status: int, seconds: float) -> None:
    if not _enabled:
        return
    attributes = {
        "http.route": route,
        "http.request.method": method if method in METHODS else "_OTHER",
        "http.response.status_code": int(status),
    }
    _meter_instruments()["request"].record(seconds, metric_attributes(attributes))


def record_llm(
    kind: str, system: str, model: str | None, outcome: str, seconds: float, usage: Mapping[str, Any]
) -> None:
    """One chat turn (`kind` turn) or single model call (`call`): its duration and tokens by type."""
    if not _enabled:
        return
    instruments = _meter_instruments()
    base = {"gen_ai.system": system, "gen_ai.request.model": model or "unknown", "arcviz.llm.kind": kind}
    instruments["llm_duration"].record(seconds, metric_attributes({**base, "arcviz.llm.outcome": outcome}))
    for key, token_type in TOKEN_TYPES.items():
        count = int(usage.get(key) or 0)
        if count:
            instruments["llm_tokens"].add(count, metric_attributes({**base, "gen_ai.token.type": token_type}))


def record_steward_run(outcome: str) -> None:
    """One steward run and how it ended (a fixed outcome name)."""
    if _enabled:
        _meter_instruments()["steward_runs"].add(1, metric_attributes({"arcviz.steward.outcome": outcome}))


def record_steward_items(verdict: str, count: int = 1) -> None:
    """`count` candidates (or proposed items) with a verdict (a fixed name)."""
    if _enabled and count:
        _meter_instruments()["steward_items"].add(count, metric_attributes({"arcviz.steward.verdict": verdict}))


def record_mcp_call(tool: str, outcome: str, seconds: float) -> None:
    """One MCP tool call (a tool name of the endpoint, never an argument): its duration by tool and outcome."""
    if not _enabled:
        return
    attributes = {"arcviz.tool.name": tool, "arcviz.tool.outcome": outcome}
    _meter_instruments()["mcp_calls"].record(seconds, metric_attributes(attributes))


def record_request_units(operation: str, document: str, charge: float) -> None:
    """One Cosmos DB request's charge (#201); unknown operation or document names become "other"."""
    if not _enabled:
        return
    attributes = {
        "db.operation.name": operation if operation in STORE_OPERATIONS else "other",
        "arcviz.store.document": document if document in STORE_DOCUMENTS else "other",
    }
    _meter_instruments()["request_units"].record(float(charge), metric_attributes(attributes))


def reset() -> None:
    """Forget the instruments (tests that install a new meter provider get new ones anyway)."""
    global _instruments_of
    with _lock:
        _instruments.clear()
        _instruments_of = None
