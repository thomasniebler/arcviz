"""Structured logs, the request context of every log record, and the optional OpenTelemetry / Azure Monitor export.

Spec: docs/architecture/specs/operations.md §4 (ADR-0111..0114). `LogConfig.from_env` reads `ARCVIZ_LOG_*` and
`ARCVIZ_TELEMETRY`; `configure_logging` installs the handler (JSON or text); `RequestContextMiddleware` (outermost)
gives every request an id, takes the W3C trace id, writes one `request` record per request in JSON mode, and lets
`ContextFilter` add `request_id`, `trace_id` and the pseudonymous `user` to every record written meanwhile.
`start_telemetry` / `instrument` wire the `telemetry` extra, imported lazily. Privacy (ADR-0112): records carry route
templates and the user directory key, never raw paths, e-mail addresses, secrets or content.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import IO, Any

from starlette.requests import ClientDisconnect

from . import telemetry
from .envconfig import Env, installed

LOG_FORMATS = ("text", "json")
LOG_LEVELS = ("debug", "info", "warning", "error")
TELEMETRY_MODES = ("off", "otlp", "azure-monitor")
TELEMETRY_AUTHS = ("connection-string", "entra")  # how the Azure Monitor exporter authenticates (#187)
APP_LOGGER = "arcviz"
PROBES = frozenset({"/healthz", "/readyz"})
REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_TRACEPARENT = re.compile(r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}")
MIN_PSEUDONYM_KEY = 16  # characters of ARCVIZ_LOG_PSEUDONYM_KEY
MAX_FRAMES = 20  # innermost frames of a logged traceback
CONTEXT_FIELDS = ("request_id", "client_request_id", "trace_id", "user")
EXCEPTION_FIELDS = ("exc_type", "error_code", "exc_frames")
FIXED_FIELDS = ("time", "level", "logger", "message", *CONTEXT_FIELDS, *EXCEPTION_FIELDS, "exc")
# The only attributes a span keeps (ADR-0112): no client address, user agent, host or raw path.
SPAN_ATTRIBUTES = frozenset(
    {
        "http.method",
        "http.request.method",
        "http.route",
        "http.status_code",
        "http.response.status_code",
        "http.scheme",
        "url.scheme",
        "http.flavor",
        "network.protocol.version",
        "http.target",  # replaced by the redacted path
        "url.path",  # likewise
    }
)
# Every span: the request spans' attributes plus those of the app's own spans (telemetry.SPAN_ATTRIBUTES, #187).
ALLOWED_SPAN_ATTRIBUTES = SPAN_ATTRIBUTES | telemetry.SPAN_ATTRIBUTES
# Request spans of the probes are not recorded; anchored so that an id containing "healthz" still is.
EXCLUDED_URLS = r"^https?://[^/]+/healthz(\?.*)?$,^https?://[^/]+/readyz(\?.*)?$"
# The modules each telemetry mode needs (the `telemetry` extra).
TELEMETRY_MODULES = {
    "otlp": (
        "opentelemetry.sdk",
        "opentelemetry.exporter.otlp.proto.http",
        "opentelemetry.instrumentation.fastapi",
        "opentelemetry.instrumentation.logging",
    ),
    "azure-monitor": ("azure.monitor.opentelemetry", "opentelemetry.instrumentation.fastapi"),
}
# Client instrumentations of the Azure Monitor distro stay off: their spans carry URLs with document ids (ADR-0112).
AZURE_MONITOR_DISABLED = ("azure_sdk", "django", "fastapi", "flask", "psycopg2", "requests", "urllib", "urllib3")

logger = logging.getLogger(__name__)
_telemetry_started: str | None = None  # the mode whose providers this process installed (they are process-wide)


@dataclass(frozen=True)
class LogConfig:
    format: str = "text"  # text | json
    level: str = "INFO"
    telemetry: str = "off"  # off | otlp | azure-monitor
    telemetry_auth: str = "connection-string"  # azure-monitor: the connection string's key, or Entra ID (entra)
    pseudonym_key: str | None = field(default=None, repr=False)  # HMAC key of the `user` field; None: no field
    exception_details: bool = False  # tracebacks with messages (may contain user content); off: type and frames

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LogConfig:
        env = Env(environ)
        config = cls(
            format=env.choice("ARCVIZ_LOG_FORMAT", LOG_FORMATS, "text"),
            level=env.choice("ARCVIZ_LOG_LEVEL", LOG_LEVELS, "info").upper(),
            telemetry=env.choice("ARCVIZ_TELEMETRY", TELEMETRY_MODES, "off"),
            telemetry_auth=env.choice("ARCVIZ_TELEMETRY_AUTH", TELEMETRY_AUTHS, "connection-string"),
            pseudonym_key=env.raw("ARCVIZ_LOG_PSEUDONYM_KEY") or None,
            exception_details=env.flag("ARCVIZ_LOG_EXCEPTION_DETAILS"),
        )
        if config.pseudonym_key is not None and len(config.pseudonym_key) < MIN_PSEUDONYM_KEY:
            env.problem(f"ARCVIZ_LOG_PSEUDONYM_KEY must be at least {MIN_PSEUDONYM_KEY} characters long.")  # no value
        if config.telemetry != "off":
            if not all(installed(module) for module in TELEMETRY_MODULES[config.telemetry]):
                env.problem(f'ARCVIZ_TELEMETRY={config.telemetry} needs pip install "arcviz-agent[telemetry]".')
            elif config.telemetry == "azure-monitor" and not env.raw("APPLICATIONINSIGHTS_CONNECTION_STRING"):
                # also with ARCVIZ_TELEMETRY_AUTH=entra: there it names the resource and endpoint, its key grants nothing
                env.problem("ARCVIZ_TELEMETRY=azure-monitor needs APPLICATIONINSIGHTS_CONNECTION_STRING.")
            elif config.telemetry_auth == "entra" and not installed("azure.identity"):
                env.problem('ARCVIZ_TELEMETRY_AUTH=entra needs azure-identity: pip install "arcviz-agent[telemetry]".')
        if config.telemetry_auth == "entra" and config.telemetry != "azure-monitor":
            env.problem("ARCVIZ_TELEMETRY_AUTH=entra applies to ARCVIZ_TELEMETRY=azure-monitor only.")
        env.check()
        return config


_settings = LogConfig()  # the configuration `configure_logging` installed (process-wide, like the handlers)


def pseudonym(user_id: str, key: str) -> str:
    """The `user` of log records: a keyed HMAC of the id, so it cannot be reversed by guessing ids (ADR-0112)."""
    return "u-" + hmac.new(key.encode("utf-8"), user_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def fields(**values: Any) -> dict[str, Any]:
    """`extra=` for a structured record: the values become record attributes (OpenTelemetry exports them as
    attributes), `fields` names them for `JsonFormatter`."""
    return {**values, "fields": tuple(values)}


# ---- request context ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestContext:
    request_id: str  # generated by the server for every request (ADR-0113)
    trace_id: str | None
    scope: MutableMapping[str, Any]  # the ASGI scope: the identity is read from its state when a record is written
    client_request_id: str | None = None  # a safe X-Request-ID the client or the ingress sent

    @property
    def user(self) -> str | None:
        """The caller's pseudonym (`pseudonym`), never the raw id; None without ARCVIZ_LOG_PSEUDONYM_KEY."""
        key = _settings.pseudonym_key
        identity = (self.scope.get("state") or {}).get("arcviz_identity")
        user_id = getattr(identity, "id", None)
        return pseudonym(user_id, key) if key and isinstance(user_id, str) else None


_current: ContextVar[RequestContext | None] = ContextVar("arcviz_request", default=None)


def current() -> RequestContext | None:
    return _current.get()


@contextlib.contextmanager
def request_context(
    request_id: str, trace_id: str | None, scope: MutableMapping[str, Any], client_request_id: str | None = None
) -> Iterator[RequestContext]:
    context = RequestContext(request_id, trace_id, scope, client_request_id)
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def new_request_id() -> str:
    return uuid.uuid4().hex


def client_request_id_from(value: str | None) -> str | None:
    """An incoming X-Request-ID when it is a safe token (letters, digits, `._:-`, at most 128), else None."""
    return value if value and _REQUEST_ID.fullmatch(value) else None


def trace_id_from(traceparent: str | None) -> str | None:
    """The trace id of a valid W3C `traceparent` header: version not `ff`, trace id and parent id not all zero (an
    invalid header starts a new trace in the instrumentation, so its trace id would not correlate)."""
    match = _TRACEPARENT.fullmatch((traceparent or "").strip())
    if match is None or match.group(1) == "ff" or match.group(2) == "0" * 32 or match.group(3) == "0" * 16:
        return None
    return match.group(2)


def redact_path(path: str) -> str:
    """A request path without ids: `/api/<collection>/{id}/<sub>/{id}…`; other paths by their prefix."""
    if path in PROBES:
        return path
    parts = path.split("/")
    if len(parts) > 2 and parts[1] == "api":
        return "/".join("{id}" if index >= 3 and index % 2 == 1 else part for index, part in enumerate(parts))
    for prefix in ("/static", "/docs"):
        if path == prefix or path.startswith(prefix + "/"):
            return prefix
    return "-"


def route_of(scope: Mapping[str, Any]) -> str:
    """The route template that served the request (`/api/diagrams/{diagram_id}`), else the redacted path."""
    template = getattr(scope.get("route"), "path", None)
    if isinstance(template, str) and not (template.startswith("/static") or template.startswith("/docs")):
        return template
    path = redact_path(scope.get("path", ""))
    return path if not path.startswith("/api/") else "-"


Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


CLIENT_GONE = 499  # logged status of a request whose client went away before a response started (nginx's code)


class RequestContextMiddleware:
    """Request id, trace id and the request record (JSON mode) for every HTTP request; outermost middleware.

    An unhandled exception is logged here once, scrubbed, with the request context, and never re-raised, so neither
    Starlette's error middleware nor uvicorn logs it again: before the response started it is answered with a 500
    that carries the request id; after it started the record has the status sent and `aborted`, and the middleware
    returns without completing the body, so uvicorn closes the connection (a truncated stream must not look complete;
    uvicorn notes "ASGI callable returned without completing response." without a traceback). After a complete
    response it is logged and nothing more is sent. Only the disconnect itself (an `http.disconnect` followed by
    starlette's `ClientDisconnect`, an `OSError` from `receive` or `send`) is a client gone: INFO with `aborted` and the
    status sent or 499. Any other exception is the app's defect (ERROR), also when the client went away meanwhile."""

    def __init__(self, app: ASGIApp, log_requests: bool = False):
        self.app = app
        self.log_requests = log_requests

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope.get("headers", [])}
        request_id = new_request_id()
        header = (b"x-request-id", request_id.encode("latin-1"))
        status: list[int] = []
        completed: list[bool] = []
        gone: list[bool] = []  # the client went away: not the app's error
        disconnects: list[BaseException] = []  # the exceptions that were the disconnect itself
        started = time.perf_counter()

        async def receive_tracked() -> Message:
            try:
                message = await receive()
            except OSError as exc:
                gone.append(True)
                disconnects.append(exc)
                raise
            if message.get("type") == "http.disconnect":
                gone.append(True)
            return message

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                message["headers"] = [*kept, header]
            try:
                await send(message)
            except OSError as exc:  # uvicorn's ClientDisconnected is one; a RuntimeError is the app's defect
                gone.append(True)
                disconnects.append(exc)
                raise
            if message["type"] == "http.response.start":
                status.append(int(message["status"]))
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                completed.append(True)

        def is_disconnect(exc: BaseException) -> bool:
            return any(exc is d for d in disconnects) or (bool(gone) and isinstance(exc, ClientDisconnect))

        context = (request_id, trace_id_from(headers.get("traceparent")), scope)
        with request_context(*context, client_request_id_from(headers.get(REQUEST_ID_HEADER))):
            try:
                await self.app(scope, receive_tracked, send_with_id)
            except Exception as exc:
                if completed:  # the response was complete: nothing is gone; a defect is logged, nothing more sent
                    self._log(scope, status[0], started, None if is_disconnect(exc) else exc)
                elif is_disconnect(exc):  # the client went away while the app read or wrote: nothing to answer
                    self._log(scope, status[0] if status else CLIENT_GONE, started, None, aborted=True)
                elif status:  # started, not complete: return without completing, uvicorn closes the connection
                    self._log(scope, status[0], started, exc, aborted=True)
                else:
                    self._log(scope, 500, started, exc)
                    await self._fail(send_with_id, request_id)
                return
            finally:  # request metrics by route template (#187); probes are not measured
                self._measure(scope, status[0] if status else (CLIENT_GONE if gone else 500), started)
            if gone and not completed:
                self._log(scope, status[0] if status else CLIENT_GONE, started, None, aborted=True)
            elif self.log_requests:
                self._log(scope, status[0] if status else 500, started, None)

    @staticmethod
    def _measure(scope: Scope, status: int, started: float) -> None:
        route = route_of(scope)
        if route not in PROBES:
            telemetry.record_request(route, scope.get("method", ""), status, time.perf_counter() - started)

    @staticmethod
    async def _fail(send: Send, request_id: str) -> None:
        """The 500 of an unhandled exception before the response started."""
        body = json.dumps({"detail": "Internal Server Error", "request_id": request_id}).encode()
        with contextlib.suppress(Exception):  # the client may be gone
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _log(scope: Scope, status: int, started: float, exc: BaseException | None, aborted: bool = False) -> None:
        route = route_of(scope)
        level = logging.INFO
        if route in PROBES:
            level = logging.DEBUG  # probes run every few seconds
        if exc is not None:
            level = logging.ERROR
        elif status >= 500 and status != CLIENT_GONE:
            level = logging.WARNING
        duration = int((time.perf_counter() - started) * 1000)
        method = scope.get("method", "")
        values: dict[str, Any] = {"event": "request", "method": method, "route": route, "status": status}
        if aborted:
            values["aborted"] = True
        logging.getLogger(f"{APP_LOGGER}.request").log(
            level,
            "%s %s %d in %d ms%s",
            method,
            route,
            status,
            duration,
            " (aborted)" if aborted else "",
            exc_info=exc,
            extra=fields(**values, duration_ms=duration),
        )


# ---- formatting -----------------------------------------------------------------------------------------------------


class ContextFilter(logging.Filter):
    """Adds the request context to every record (empty outside a request); never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current()
        request_id = context.request_id if context else ""
        record.request_id = request_id
        record.client_request_id = (context.client_request_id or "") if context else ""
        record.trace_id = ((context.trace_id or _span_trace_id()) if context else "") or ""
        record.user = (context.user or "") if context else ""
        record.context = f" [{request_id}]" if request_id else ""
        return True


ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def exception_summary(exc_info: ExcInfo) -> dict[str, str]:
    """An exception without its message (which may carry user content): the type, the innermost frames as
    `file:line:function`, and a stable code over type and frames without line numbers (ADR-0112)."""
    exc_type, _, tb = exc_info
    frames = traceback.extract_tb(tb)[-MAX_FRAMES:]
    names = [f"{_short(frame.filename)}:{frame.lineno}:{frame.name}" for frame in frames]
    site = ";".join([exc_type.__name__, *(f"{_short(frame.filename)}:{frame.name}" for frame in frames)])
    return {
        "exc_type": exc_type.__name__,
        "error_code": hashlib.sha256(site.encode("utf-8")).hexdigest()[:12],
        "exc_frames": " | ".join(names),
    }


def _short(filename: str) -> str:
    """The last two path components (`arcviz/server.py`), so no home or venv paths."""
    parts = filename.replace(os.sep, "/").split("/")
    return "/".join(parts[-2:])


def scrubbed(record: logging.LogRecord) -> logging.LogRecord:
    """The record an export handler may send: a copy whose exception is replaced by `exception_summary`, unless
    ARCVIZ_LOG_EXCEPTION_DETAILS. The original stays untouched for the other handlers (local stderr keeps the
    traceback in text mode)."""
    if not (record.exc_info and record.exc_info[0] is not None) or _settings.exception_details:
        return record
    summary = exception_summary(record.exc_info)
    copy_ = copy.copy(record)
    for name, value in summary.items():
        setattr(copy_, name, value)
    copy_.fields = (*(getattr(record, "fields", ()) or ()), *summary)
    copy_.exc_info = None
    copy_.exc_text = None
    return copy_


def _span_trace_id() -> str | None:
    """With telemetry on, the trace the instrumentation started for a request without `traceparent`."""
    if _telemetry_started is None:
        return None
    from opentelemetry import trace

    span_context = trace.get_current_span().get_span_context()
    return format(span_context.trace_id, "032x") if span_context.is_valid else None


def protect_export_handlers() -> None:
    """Every handler on the `arcviz` logger (the telemetry exporters) gets the request context and sends scrubbed
    copies of records with exceptions. Idempotent."""
    for handler in logging.getLogger(APP_LOGGER).handlers:
        if getattr(handler, "_arcviz_protected", False):
            continue
        handler.addFilter(ContextFilter())
        emit = handler.emit

        def emit_scrubbed(record: logging.LogRecord, emit: Callable[[logging.LogRecord], None] = emit) -> None:
            emit(scrubbed(record))

        handler.emit = emit_scrubbed  # type: ignore[method-assign]
        handler._arcviz_protected = True  # type: ignore[attr-defined]


class JsonFormatter(logging.Formatter):
    """One JSON object per line: the fixed fields, then the record's structured `fields` (ADR-0111)."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, "")
            if value:
                out[name] = value
        for name in getattr(record, "fields", ()) or ():
            if name in FIXED_FIELDS or name in out:
                continue
            value = getattr(record, name, None)
            out[name] = value if value is None or isinstance(value, str | int | float | bool) else str(value)
        if record.exc_info and record.exc_info[0] is not None:
            out.update(exception_summary(record.exc_info))
            if _settings.exception_details:  # opt-in: the message may contain user content
                out["exc"] = self.formatException(record.exc_info)
        else:  # a scrubbed copy (`scrubbed`) carries the summary as attributes
            for name in EXCEPTION_FIELDS:
                value = getattr(record, name, None)
                if isinstance(value, str):
                    out[name] = value
        return json.dumps(out, ensure_ascii=False, default=str)


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s%(context)s: %(message)s"


def configure_logging(config: LogConfig, stream: IO[str] | None = None) -> None:
    """Install the app's handler on the root logger (idempotent: replaces only a handler installed here before).

    JSON: always (uvicorn's records too, when `main` hands logging over). Text: only when the root logger has no
    handler yet, so an embedding server or pytest keeps its own."""
    global _settings
    _settings = config
    root = logging.getLogger()
    for own in [h for h in root.handlers if getattr(h, "_arcviz", False)]:
        root.removeHandler(own)
    handler: logging.Handler | None = None
    if config.format == "json":
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(JsonFormatter())
        logging.getLogger("uvicorn").setLevel(logging.INFO)  # without uvicorn's log config they would be dropped
    elif not root.handlers:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))
    if handler is not None:
        handler.addFilter(ContextFilter())
        handler._arcviz = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    logging.getLogger(APP_LOGGER).setLevel(config.level)


def reset_logging() -> None:
    """Remove the app's handler, level and settings (tests)."""
    global _settings
    _settings = LogConfig()
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_arcviz", False)]:
        root.removeHandler(handler)
    logging.getLogger(APP_LOGGER).setLevel(logging.NOTSET)


# ---- telemetry (the `telemetry` extra, ADR-0114) --------------------------------------------------------------------


def start_telemetry(config: LogConfig) -> None:
    """Install the process-wide OpenTelemetry providers once (a second app in the process reuses them)."""
    global _telemetry_started
    if config.telemetry == "off" or _telemetry_started == config.telemetry:
        return
    if config.telemetry == "azure-monitor":
        from azure.monitor.opentelemetry import configure_azure_monitor

        options: dict[str, Any] = {}
        if config.telemetry_auth == "entra":  # keyless (#187): managed identity, workload identity, Azure CLI, …
            from azure.identity import DefaultAzureCredential

            options["credential"] = DefaultAzureCredential()  # AZURE_CLIENT_ID picks a user-assigned identity
        configure_azure_monitor(  # reads APPLICATIONINSIGHTS_CONNECTION_STRING; installs the meter provider too
            logger_name=APP_LOGGER,
            instrumentation_options={name: {"enabled": False} for name in AZURE_MONITOR_DISABLED},
            span_processors=[event_scrubber()],
            **options,
        )
    else:
        _start_otlp()
    # The export handlers sit on the `arcviz` logger and see a record before the root handler does: give them the
    # request context too, so exported records correlate like the local ones, and never an exception message.
    protect_export_handlers()
    telemetry.enable()  # the app's own spans and metrics (#187)
    _telemetry_started = config.telemetry
    logger.info("Telemetry export: %s", config.telemetry)


def _start_otlp() -> None:
    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.instrumentation.logging.handler import LoggingHandler  # the SDK's own is deprecated
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    resource = Resource.create()  # OTEL_SERVICE_NAME, OTEL_RESOURCE_ATTRIBUTES
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(event_scrubber())  # before the exporter's processor: runs first
    tracer_provider.add_span_processor(_span_processor(_span_exporter()))
    trace.set_tracer_provider(tracer_provider)
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider

    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[_metric_reader()]))
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(_log_exporter()))
    set_logger_provider(logger_provider)
    logging.getLogger(APP_LOGGER).addHandler(LoggingHandler(logger_provider=logger_provider))


def _span_exporter() -> Any:  # OTEL_EXPORTER_OTLP_* configure the endpoint and headers
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter()


def _span_processor(exporter: Any) -> Any:
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    return BatchSpanProcessor(exporter)


def _metric_reader() -> Any:  # the app's own metrics (#187); OTEL_EXPORTER_OTLP_* as for spans
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    return PeriodicExportingMetricReader(OTLPMetricExporter())


def _log_exporter() -> Any:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

    return OTLPLogExporter()


def instrument(app: Any, config: LogConfig) -> None:
    """Request spans for this app (the FastAPI instrumentation, probes excluded, ids redacted)."""
    if config.telemetry != "off":
        _instrument(app)


def _instrument(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.metrics import NoOpMeterProvider

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=EXCLUDED_URLS,
        server_request_hook=_redact_span,
        exclude_spans=["receive", "send"],
        # no request metrics: their attributes (raw target, host) cannot be restricted like span attributes, and
        # Application Insights derives request statistics from the spans (ADR-0112)
        meter_provider=NoOpMeterProvider(),
    )


def _redact_span(span: Any, scope: Mapping[str, Any]) -> None:
    """Keep only `SPAN_ATTRIBUTES` on a server span and give it the redacted path (ADR-0112).

    The hook runs right after the span started, when the instrumentation has set the request attributes; the SDK
    offers no public way to remove an attribute, so the span's attribute mapping is edited directly (pinned SDK,
    covered by tests/test_observability_hardening.py)."""
    if span is None or not span.is_recording():
        return
    attributes = getattr(span, "_attributes", None)
    if attributes is not None:
        for name in [name for name in attributes if name not in SPAN_ATTRIBUTES]:
            del attributes[name]
    redacted = redact_path(scope.get("path", ""))
    span.set_attribute("http.target", redacted)
    span.set_attribute("url.path", redacted)


def event_scrubber() -> Any:
    """A span processor (added first, so it runs before the exporter's): when a span ends, its attributes are cut
    down to `ALLOWED_SPAN_ATTRIBUTES` again (attributes added after the request hook, such as captured headers), its events
    keep only `exception.type`, and its status description only the exception type. Built on demand because the SDK
    is the optional `telemetry` extra; the ended span is read-only through the public API."""
    from opentelemetry.attributes import BoundedAttributes
    from opentelemetry.sdk.trace import Event, SpanProcessor
    from opentelemetry.trace import Status

    class EventScrubber(SpanProcessor):
        def on_end(self, span: Any) -> None:
            attributes = getattr(span, "_attributes", None)
            if attributes:
                kept = {name: value for name, value in attributes.items() if name in ALLOWED_SPAN_ATTRIBUTES}
                span._attributes = BoundedAttributes(attributes=kept, immutable=True)
            events = getattr(span, "_events", None)
            if events:
                span._events = tuple(
                    Event(
                        event.name,
                        {"exception.type": event.attributes["exception.type"]}
                        if event.attributes and "exception.type" in event.attributes
                        else {},
                        event.timestamp,
                    )
                    for event in events
                )
            status = getattr(span, "_status", None)
            if status is not None and status.description:
                span._status = Status(status.status_code, _status_type(status.description))

    return EventScrubber()


def _status_type(description: str) -> str | None:
    """`LookupError: …` → `LookupError`; anything that does not start with a type name → None."""
    head = description.split(":", 1)[0].strip()
    return head if re.fullmatch(r"[A-Za-z_][\w.]*", head) else None
