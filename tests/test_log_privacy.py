"""No secret, e-mail address or diagram content reaches a log record (#83; spec: operations.md §4, ADR-0112).

A whole session in token mode and one in proxy mode, with JSON logs of the app's loggers at DEBUG: create a diagram
with a distinctive title, nodes and a note, chat with the scripted model, share by e-mail and revoke. Then no line
contains any of the strings below, every line is JSON, and the records that should exist do.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from arcviz import observability, server
from arcviz.llm.base import ModelReply
from arcviz.observability import LogConfig, configure_logging, pseudonym
from arcviz.store.file import FileStore
from tests.conftest import ScriptedProvider, call

TOKEN = "tok-SECRET-4f1d2c3b4a596877"
API_KEY = "sk-ant-SECRET-api-key-0042"
TITLE = "Project Nightingale"
DIAGRAM_ID = "nightingale-plan"
NODE = "Nightingale Ledger"
NOTE = "confidential note about the merger"
MESSAGE = "Please add the Nightingale vault"
REPLY = "Added the Nightingale vault next to the ledger"
ADA = ("ada-7c1f", "Ada Lovelace", "ada.lovelace@example.org")
BOB = ("bob-9e2d", "Bob Builder", "bob.builder@example.org")
KEY = "log-pseudonym-SECRET-key-0123456789"
FORBIDDEN = [KEY, TOKEN, API_KEY, TITLE, "Nightingale", DIAGRAM_ID, NOTE, MESSAGE, REPLY, *ADA, *BOB, "example.org"]


@pytest.fixture(autouse=True)
def reset_logging():
    yield
    observability.reset_logging()


def scripted() -> ScriptedProvider:
    return ScriptedProvider(
        [
            ModelReply(
                "",
                [call("add_node", "c1", name="Nightingale Vault", kind="database")],
                "tool_use",
                usage={"input_tokens": 120, "output_tokens": 30},
            ),
            ModelReply(REPLY, [], "end_turn", usage={"input_tokens": 150, "output_tokens": 20}),
        ]
    )


def session(client: TestClient, headers: dict[str, str]) -> None:
    created = client.post("/api/diagrams", json={"title": TITLE, "id": DIAGRAM_ID}, headers=headers)
    assert created.status_code == 201, created.text
    for args in ({"name": NODE, "kind": "service", "description": NOTE}, {"name": "Nightingale Gateway"}):
        assert client.post(f"/api/diagrams/{DIAGRAM_ID}/ops", json={"op": "add_node", "args": args}, headers=headers)
    with client.stream("POST", f"/api/diagrams/{DIAGRAM_ID}/chat", json={"message": MESSAGE}, headers=headers) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert REPLY in body  # the turn ran
    assert client.get(f"/api/diagrams/{DIAGRAM_ID}", headers=headers).status_code == 200
    assert client.get(f"/api/diagrams/{DIAGRAM_ID}/nope", headers=headers).status_code == 404

    def defect() -> None:  # a defect whose message carries content: only its type and frames are logged
        raise RuntimeError(f"cannot render {TITLE} for {ADA[2]}")

    if not any(getattr(route, "path", "") == "/api/defect" for route in client.app.routes):
        client.app.add_api_route("/api/defect", defect)
    assert client.get("/api/defect", headers=headers).status_code == 500


def check(stream: io.StringIO) -> list[dict]:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]  # every line is one JSON object
    for line in lines:
        for secret in FORBIDDEN:
            assert secret not in line, f"{secret!r} leaked into a log record: {line}"
    events = {r.get("event") for r in records}
    assert {"request", "llm_turn"} <= events, events
    return records


def test_token_mode_session_logs_nothing_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_AUTH", "token")
    monkeypatch.setenv("ARCVIZ_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "json")
    monkeypatch.setenv("ARCVIZ_LOG_PSEUDONYM_KEY", KEY)
    monkeypatch.setenv("ARCVIZ_LOG_LEVEL", "debug")
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=scripted())
    stream = io.StringIO()
    configure_logging(LogConfig(format="json", level="DEBUG", pseudonym_key=KEY), stream=stream)
    client = TestClient(app)
    assert client.get("/api/config").status_code == 401  # logged too
    assert client.post("/login", data={"token": "wrong-" + TOKEN}).status_code == 401
    session(client, {"Authorization": f"Bearer {TOKEN}"})
    records = check(stream)
    assert {r["status"] for r in records if r.get("event") == "request"} >= {200, 401, 404}


def test_proxy_mode_session_with_sharing_logs_nothing_personal(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_AUTH", "proxy")
    monkeypatch.setenv("ARCVIZ_AUTH_PROXY_PRESET", "oauth2-proxy")
    monkeypatch.setenv("ARCVIZ_ADMIN_USER", "admin")
    monkeypatch.setenv("ARCVIZ_LOG_FORMAT", "json")
    monkeypatch.setenv("ARCVIZ_LOG_PSEUDONYM_KEY", KEY)
    monkeypatch.setenv("ARCVIZ_LOG_LEVEL", "debug")
    app = server.create_app(store=FileStore(tmp_path / "data"), provider=scripted())
    stream = io.StringIO()
    configure_logging(LogConfig(format="json", level="DEBUG", pseudonym_key=KEY), stream=stream)
    client = TestClient(app)

    def headers(person: tuple[str, str, str]) -> dict[str, str]:
        user, name, email = person
        return {"x-forwarded-user": user, "x-forwarded-preferred-username": name, "x-forwarded-email": email}

    assert client.get("/api/me", headers=headers(BOB)).status_code == 200  # Bob signs in once
    session(client, headers(ADA))
    project = client.get(f"/api/diagrams/{DIAGRAM_ID}", headers=headers(ADA)).json()["access"]["project"]
    shared = client.post(
        f"/api/projects/{project}/access", json={"user": BOB[2], "role": "viewer"}, headers=headers(ADA)
    )
    assert shared.status_code == 200, shared.text
    assert client.delete(f"/api/projects/{project}/access/{BOB[0]}", headers=headers(ADA)).status_code == 200
    assert client.get("/api/me").status_code == 401  # no identity
    records = check(stream)
    users = {r.get("user") for r in records if r.get("event") == "request"}
    assert users == {pseudonym(ADA[0], KEY), pseudonym(BOB[0], KEY), None}  # keyed pseudonyms, never the ids
