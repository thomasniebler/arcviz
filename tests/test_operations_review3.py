"""Third review of M9: secrets in the Git remote, exception texts in log calls, a hanging token refresh, aborted
responses that uvicorn must not log again (#83, #156; spec: operations.md §4, §5, ADR-0112, ADR-0115)."""

from __future__ import annotations

import ast
import asyncio
import io
import json
import logging
import pathlib
import threading
import time
import types

import httpx
import pytest
from fastapi.responses import StreamingResponse

from arcviz import observability, server
from arcviz.gitmirror import GitMirror, GitMirrorConfig, GitMirrorError, redacted
from arcviz.llm import entra
from arcviz.llm.entra import DEFAULT_SCOPE, BearerTokenProvider, TokenRefreshError
from arcviz.observability import LogConfig, configure_logging
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider
from tests.test_entra import Clock, FakeCredential

KEY = "pseudonym-key-for-tests-0123456789"
SENTINEL = "SENTINEL-carol@example.org-Nightingale"
TOKEN = "ghp_SECRETtoken0123456789"
REMOTE = f"https://deploy:{TOKEN}@git.example.org/org/diagrams.git?private_token={TOKEN}"


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


# ---- 1. the Git remote never shows its credentials ---------------------------------------------------------------


def test_redacted_removes_userinfo_and_query_from_urls():
    assert redacted(REMOTE) == "https://***@git.example.org/org/diagrams.git?***"
    text = f"fatal: unable to access '{REMOTE}': 403"
    assert TOKEN not in redacted(text) and "git.example.org/org/diagrams.git" in redacted(text)
    assert redacted("origin") == "origin" and redacted("git@github.com:org/repo.git") == "git@github.com:org/repo.git"


def test_a_token_in_the_git_remote_never_reaches_logs_errors_or_the_status(tmp_path, monkeypatch):
    stream = json_logs()
    mirror = GitMirror(GitMirrorConfig(repo_dir=tmp_path / "git", push=True, remote=REMOTE))  # not configured
    assert mirror.last_error and TOKEN not in mirror.last_error
    monkeypatch.setattr(
        mirror,
        "_git",
        lambda *a, **k: types.SimpleNamespace(returncode=128, stderr=f"fatal: unable to access '{REMOTE}'", stdout=""),
    )
    mirror._push()
    assert mirror.last_error and "git.example.org" in mirror.last_error
    status = mirror.status()

    def failing_git(*args, **kwargs):
        raise GitMirrorError(f"git push -q -u {REMOTE} HEAD failed: fatal: unable to access '{REMOTE}'")

    monkeypatch.setattr(mirror, "_git", failing_git)
    monkeypatch.setattr(mirror, "_write_files", lambda diagram: None)
    from arcviz.model import Diagram

    mirror.record(Diagram(id="d1", title="t"), "subject")
    assert TOKEN not in json.dumps(status) + (mirror.last_error or "") + stream.getvalue()
    assert status["remote"] == redacted(REMOTE)


# ---- 2. no exception object as a log argument --------------------------------------------------------------------

LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _type_name_only(node: ast.AST, name: str) -> bool:
    """`type(<name>).__name__`: the only way an exception may appear in a log call's arguments."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "__name__"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "type"
        and [ast.unparse(a) for a in node.value.args] == [name]
    )


def test_no_log_call_formats_a_caught_exception():
    """In `except … as exc:` a log call may name `exc` only as `type(exc).__name__` (its message may carry user
    content or secrets); `exc_info=` is scrubbed by the formatter."""
    offenders = []
    for path in sorted(pathlib.Path("arcviz").rglob("*.py")):
        for handler in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(handler, ast.ExceptHandler) or not handler.name:
                continue
            for call in ast.walk(handler):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in LOG_METHODS
                ):
                    continue
                arguments = [*call.args, *(k.value for k in call.keywords if k.arg != "exc_info")]
                for argument in arguments:
                    allowed = {id(n.value.args[0]) for n in ast.walk(argument) if _type_name_only(n, handler.name)}
                    named = [n for n in ast.walk(argument) if isinstance(n, ast.Name) and n.id == handler.name]
                    offenders += [f"{path}:{call.lineno}" for n in named if id(n) not in allowed]
    assert not offenders, offenders


def test_the_cosmos_ttl_warning_logs_the_type_only():
    from arcviz.store import cosmos

    class Proxy:
        id = "diagrams"

        def read(self):
            return {"id": "diagrams", "partitionKey": {"paths": ["/diagram_id"], "kind": "Hash"}}

    class Database:
        def replace_container(self, *args, **kwargs):
            raise RuntimeError(SENTINEL)

    stream = json_logs()
    assert cosmos.ensure_ttl(Database(), Proxy()) == "off"
    (record,) = records(stream)
    assert "RuntimeError" in record["message"] and SENTINEL not in stream.getvalue()


# ---- 3, 4. a hanging credential has a deadline; waiters get a fresh exception -----------------------------------


class Hanging(FakeCredential):
    def __init__(self, clock, release: threading.Event):
        super().__init__(clock)
        self.release = release

    def get_token(self, *scopes, **kwargs):
        self.scopes.append(scopes)
        self.release.wait(5)
        return entra_token("late", self.clock)


def entra_token(value, clock):
    from tests.test_entra import AccessToken

    return AccessToken(value, int(clock.now) + 3600)


def test_a_hanging_credential_is_abandoned_at_the_deadline_and_counts_as_a_failure():
    clock = Clock()
    release = threading.Event()
    credential = Hanging(clock, release)
    tokens = BearerTokenProvider(credential, DEFAULT_SCOPE, clock=clock, refresh_timeout=0.2)
    errors: list[BaseException] = []

    def call():
        try:
            tokens()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(4)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    elapsed = time.perf_counter() - started
    try:
        assert elapsed < 1.0 and len(errors) == 4  # nobody waits past the deadline
        assert len(credential.scopes) == 1
        assert any(isinstance(e, TimeoutError) for e in errors)  # the leader's own deadline
        assert all(isinstance(e, TokenRefreshError | TimeoutError) for e in errors)
        with pytest.raises(TokenRefreshError):  # the slot is free and the attempt is in the backoff: fail fast
            tokens()
        assert len(credential.scopes) == 1
        clock.now += entra.REFRESH_BACKOFF + 1
        tokens.credential = FakeCredential(clock)
        assert tokens() == "t1"  # the next attempt after the backoff, not blocked by the abandoned one
    finally:
        release.set()
    time.sleep(0.05)
    assert tokens() == "t1"  # the late result of the abandoned call is ignored


def test_waiters_get_a_fresh_exception_caused_by_the_failure():
    clock = Clock()

    class SlowBroken(FakeCredential):
        def get_token(self, *scopes, **kwargs):
            self.scopes.append(scopes)
            time.sleep(0.15)
            raise RuntimeError(SENTINEL)

    tokens = BearerTokenProvider(SlowBroken(clock), DEFAULT_SCOPE, clock=clock)
    errors: list[BaseException] = []

    def call():
        try:
            tokens()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    shared = [e for e in errors if isinstance(e, TokenRefreshError)]
    (original,) = [e for e in errors if not isinstance(e, TokenRefreshError)]
    assert len(shared) == 2 and shared[0] is not shared[1] and all(e.__cause__ is original for e in shared)
    assert all(SENTINEL not in str(e) and "RuntimeError" in str(e) for e in shared)
    with pytest.raises(TokenRefreshError) as backoff:
        tokens()
    assert backoff.value.__cause__ is original and backoff.value not in shared


# ---- 5. the order of completed, defect and disconnect ------------------------------------------------------------


def run_middleware(app, receive=None, send=None) -> list[dict]:
    sent: list[dict] = []

    async def default_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def default_send(message):
        sent.append(message)

    middleware = observability.RequestContextMiddleware(app, log_requests=True)
    scope = {"type": "http", "method": "GET", "path": "/x", "headers": []}
    asyncio.run(middleware(scope, receive or default_receive, send or default_send))
    return sent


def request_record(stream: io.StringIO) -> dict:
    (record,) = [r for r in records(stream) if r.get("event") == "request"]
    return record


def test_a_defect_after_a_complete_response_is_an_error_even_with_a_disconnect():
    stream = json_logs()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})
        await receive()  # http.disconnect below
        raise LookupError(SENTINEL)

    async def receive():
        return {"type": "http.disconnect"}

    run_middleware(app, receive)
    record = request_record(stream)
    assert record["level"] == "ERROR" and record["exc_type"] == "LookupError" and record["status"] == 200
    assert "aborted" not in record and SENTINEL not in stream.getvalue()


def test_a_defect_after_a_disconnect_is_an_error_not_a_gone_client():
    stream = json_logs()

    async def app(scope, receive, send):
        await receive()
        raise LookupError(SENTINEL)

    async def receive():
        return {"type": "http.disconnect"}

    run_middleware(app, receive)
    record = request_record(stream)
    assert record["level"] == "ERROR" and record["exc_type"] == "LookupError" and record["status"] == 500


def test_a_runtime_error_from_send_is_a_defect_answered_with_a_500():
    stream = json_logs()
    sent: list[dict] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message):
        if message.get("status") == 200:
            raise RuntimeError("Unexpected ASGI message")
        sent.append(message)

    run_middleware(app, send=send)
    record = request_record(stream)
    assert record["level"] == "ERROR" and record["exc_type"] == "RuntimeError" and record["status"] == 500
    assert sent[0]["status"] == 500 and json.loads(sent[1]["body"])["request_id"] == record["request_id"]


def test_an_os_error_from_send_is_a_gone_client():
    stream = json_logs()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x", "more_body": True})

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("broken pipe")

    run_middleware(app, send=send)
    record = request_record(stream)
    assert record["level"] == "INFO" and record["aborted"] is True and record["status"] == 200


def test_a_stream_failing_after_its_start_is_not_re_raised_and_not_completed():
    stream = json_logs()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"data: one\n\n", "more_body": True})
        raise RuntimeError(SENTINEL)

    sent = run_middleware(app)  # does not raise into ServerErrorMiddleware or uvicorn
    assert sent[-1].get("more_body") is True  # never completed: the server closes the connection
    record = request_record(stream)
    assert record["level"] == "ERROR" and record["aborted"] is True and record["exc_type"] == "RuntimeError"


def test_under_uvicorn_a_broken_stream_drops_the_connection_and_is_logged_once(tmp_path, monkeypatch):
    import uvicorn

    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "json")
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=ScriptedProvider([]))

    def failing_stream():
        def body():
            yield b"data: one\n\n"
            raise RuntimeError(SENTINEL)

        return StreamingResponse(body(), media_type="text/event-stream")

    app.add_api_route("/api/failing-stream", failing_stream)
    stream = json_logs()
    uvicorn_records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            uvicorn_records.append(record)

    collect = Collect()
    logging.getLogger("uvicorn.error").addHandler(collect)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="off", access_log=False)
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not uvicorn_server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
        with httpx.Client(trust_env=False, timeout=5) as client:
            with pytest.raises(httpx.TransportError):  # a broken stream, not a clean end
                with client.stream("GET", f"http://127.0.0.1:{port}/api/failing-stream") as response:
                    assert response.status_code == 200
                    b"".join(response.iter_bytes())
    finally:
        uvicorn_server.should_exit = True
        thread.join(10)
        logging.getLogger("uvicorn.error").removeHandler(collect)
    assert not [r for r in uvicorn_records if r.exc_info], "uvicorn logged the exception a second time"
    record = request_record(stream)
    assert record["aborted"] is True and record["level"] == "ERROR" and SENTINEL not in stream.getvalue()
