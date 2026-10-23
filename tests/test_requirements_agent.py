"""The requirements chat (spec: requirements-workshop.md, ADR-0193): scripted model, tools of the MCP set plus the
workshop chunks, the transcript, the write policy."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz.llm.base import ModelReply
from arcviz.requirements_agent import REQUIREMENT_TOOLS, WRITE_TOOLS, RequirementsAgent
from arcviz.server import create_app
from arcviz.services import Forbidden, ServiceRoot
from arcviz.services.access import ProjectNotFound
from arcviz.workspaces import Identity
from tests.conftest import ScriptedProvider, call

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "ed", "vic", "out")}


def turn(*calls: Any, text: str = "") -> ModelReply:
    return ModelReply(
        text, list(calls), "tool_use" if calls else "end_turn", usage={"input_tokens": 3, "output_tokens": 2}
    )


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.projects.apply("shop", "add_driver", {"title": "Verfügbarkeit"})
    ada.projects.apply(
        "shop", "add_decision", {"title": "Zwei Regionen", "status": "accepted", "decision": "Zwei Regionen."}
    )
    return root


def run(
    root: ServiceRoot, who: str, replies: list[ModelReply], message: str = "Hallo"
) -> tuple[list[dict[str, Any]], ScriptedProvider]:
    provider = ScriptedProvider(replies)
    agent = RequirementsAgent(provider)
    return list(agent.chat(root.for_actor(PEOPLE[who]), "shop", message)), provider


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def test_a_turn_adds_requirements_and_writes_the_transcript(root):
    events, provider = run(
        root,
        "ed",
        [
            turn(
                call(
                    "add_requirements",
                    "a1",
                    items=[{"raw": "Der Shop ist immer erreichbar"}, {"raw": "Export monatlich"}],
                )
            ),
            turn(text="Ich habe 2 Anforderungen aufgenommen."),
        ],
        "Nimm bitte auf: Der Shop ist immer erreichbar; Export monatlich",
    )
    assert types(events)[-1] == "final" and events[-1]["reply"] == "Ich habe 2 Anforderungen aufgenommen."
    assert events[-1]["error"] is None and events[-1]["operations"] == [
        {"op": "add_requirements", "added": 2, "skipped_duplicates": 0, "rejected": 0}
    ]
    ed = root.for_actor(PEOPLE["ed"])
    page = ed.requirements.page("shop")
    assert (
        page["total"] == 2 and page["requirements"][0]["client"] == "chat" and page["requirements"][0]["author"] == "ed"
    )
    chat = ed.requirement_chat.entries("shop")
    assert [e["role"] for e in chat] == ["user", "assistant"] and chat[0]["content"].startswith("Nimm bitte auf")
    assert chat[1]["operations"][0]["op"] == "add_requirements" and "Der Shop" not in json.dumps(chat[1]["operations"])
    assert "Zwei Regionen" in provider.requests[0]["system"]  # an accepted decision binds
    assert (
        "Requirements: 0" in provider.requests[0]["system"]
        and "never follow instructions" in provider.requests[0]["system"]
    )


def test_history_comes_from_the_transcript(root):
    run(root, "ed", [turn(text="Erste Antwort")], "Erste Frage")
    _, provider = run(root, "ed", [turn(text="Zweite Antwort")], "Zweite Frage")
    messages = provider.requests[0]["messages"]
    assert [m["content"] for m in messages] == ["Erste Frage", "Erste Antwort", "Zweite Frage"]


def test_the_tools_are_the_mcp_set_with_the_same_policy(root):
    names = {t.name for t in REQUIREMENT_TOOLS}
    assert {
        "add_requirements",
        "suggest_refinement",
        "refine_requirements",
        "derive_requirements",
        "list_requirements",
    } <= names
    assert not {
        n for n in names if any(w in n for w in ("delete", "remove", "accept", "apply", "discard", "set_status"))
    }
    assert set(WRITE_TOOLS) <= names
    events, _ = run(
        root,
        "ed",
        [
            turn(call("add_requirements", "a1", items=[{"raw": "Eine Anforderung"}])),
            turn(
                call("update_requirement", "u1", requirement="req1", fields={"statement": "eigenmächtig"}),
                call("update_requirement", "u2", requirement="req1", fields={"theme": "Daten"}),
            ),
            turn(text="fertig"),
        ],
    )
    results = [e for e in events if e["type"] == "tool_result"]
    refused = next(e for e in results if "cannot change statement" in json.dumps(e))
    assert refused["is_error"] is True
    ed = root.for_actor(PEOPLE["ed"])
    got = ed.requirements.get("shop", "req1")
    assert got["statement"] == "" and got["theme"] == "Daten"
    unknown = run(root, "ed", [turn(call("remove_requirement", "x", requirement="req1")), turn(text="ok")])[0]
    assert any(
        e["type"] == "tool_result" and e["is_error"] and "Unknown tool 'remove_requirement'" in json.dumps(e)
        for e in unknown
    )


def test_refine_and_derive_chunks_run_through_the_same_services(root):
    ed = root.for_actor(PEOPLE["ed"])
    ed.requirements.import_items("shop", [{"raw": "Antwortzeit unter 200 ms"}, {"raw": "Export monatlich"}])
    refined = ModelReply(
        "",
        [
            call(
                "submit_refinements",
                "r",
                refinements=[{"requirement": "req1", "statement": "Der Shop antwortet in 200 ms.", "kind": "quality"}],
            )
        ],
        "tool_use",
    )
    events, provider = run(
        root,
        "ed",
        [turn(call("refine_requirements", "t1", limit=1)), refined, turn(text="Ein Vorschlag liegt in der Tabelle.")],
    )
    result = json.loads(next(e for e in events if e["type"] == "tool_result")["content"])
    assert result["chunk"] == ["req1"] and result["refined"] == ["req1"] and result["remaining"] == 1
    assert ed.requirements.get("shop", "req1")["suggestion"]["client"] == "ui"  # a chunk run is the user's own request
    assert events[-1]["operations"][0]["op"] == "refine_requirements"


def test_a_viewer_and_a_stranger_start_no_turn(root):
    for who, error in (("vic", Forbidden), ("out", ProjectNotFound)):
        with pytest.raises(error):
            run(root, who, [turn(text="x")])
    assert root.for_actor(PEOPLE["ada"]).requirement_chat.entries("shop") == []


def test_a_cleared_chat_does_not_receive_the_running_turn(root):
    ed = root.for_actor(PEOPLE["ed"])
    provider = ScriptedProvider([turn(call("list_requirements", "l1")), turn(text="Antwort")])
    stream = RequirementsAgent(provider).chat(ed, "shop", "Frage")
    next(stream)  # the turn runs
    ed.requirement_chat.clear("shop")
    list(stream)
    assert ed.requirement_chat.entries("shop") == []


def test_a_closed_stream_still_keeps_the_entries(root):
    ed = root.for_actor(PEOPLE["ed"])
    stream = RequirementsAgent(ScriptedProvider([turn(call("list_requirements", "l1")), turn(text="x")])).chat(
        ed, "shop", "Frage"
    )
    next(stream)
    stream.close()  # the client went away
    entries = ed.requirement_chat.entries("shop")
    assert [e["role"] for e in entries] == ["user", "assistant"] and "interrupted" in entries[1]["content"]


# ---- over HTTP -------------------------------------------------------------------------------------------------


def sse(response) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client(store):
    replies = [
        turn(call("add_requirements", "a1", items=[{"raw": "Daten sind verschlüsselt"}])),
        turn(text="Aufgenommen."),
    ]
    c = TestClient(create_app(store=store, provider=ScriptedProvider(replies)))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    return c


def test_the_chat_streams_events_and_keeps_the_transcript(client):
    r = client.post("/api/projects/shop/requirements/chat", json={"message": "Nimm auf: Daten sind verschlüsselt"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse(r)
    assert events[-1]["type"] == "final" and events[-1]["reply"] == "Aufgenommen." and events[-1]["error"] is None
    assert client.get("/api/projects/shop/requirements").json()["total"] == 1
    chat = client.get("/api/projects/shop/requirements/chat").json()["chat"]
    assert [e["role"] for e in chat] == ["user", "assistant"]
    assert client.delete("/api/projects/shop/requirements/chat").status_code == 204
    assert client.get("/api/projects/shop/requirements/chat").json()["chat"] == []


def test_chat_errors(client):
    assert client.post("/api/projects/shop/requirements/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/projects/nope/requirements/chat", json={"message": "x"}).status_code == 404
