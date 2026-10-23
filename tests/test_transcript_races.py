"""Transcripts and placements bound to their incarnation (#143, spec: docs/architecture/specs/hardening.md, ADR-0127).

Store-level behaviour is in tests/store_contract.py; these tests interleave a turn or a request with a clear, a
re-created project or a move.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import operations as ops
from arcviz import projects
from arcviz.model import Diagram
from arcviz.server import create_app
from arcviz.services import DiagramService
from arcviz.services.access import ProjectNotFound
from arcviz.services.projects import ProjectService
from arcviz.store import fingerprint
from tests.conftest import ScriptedProvider
from tests.test_concurrency import HookedProvider, add, done, run, tool_round


def test_a_chat_cleared_during_a_turn_does_not_receive_the_turns_entry(store, demo):
    provider = HookedProvider([tool_round(add("one", "c1")), done()], hooks={1: lambda: store.clear_chat("demo")})
    events = run(store, provider)
    assert events[-1]["type"] == "final" and "one" in store.load("demo").nodes  # the change itself is kept
    assert store.load_chat("demo") == []  # the cleared transcript stays empty


def test_a_diagram_is_never_placed_into_a_project_created_again_meanwhile(store):
    project_service = ProjectService(store, store)
    diagrams = DiagramService(store, project_service)
    shop = project_service.create("Shop", "shop")
    project_service.delete("shop")
    again = project_service.create("Shop", "shop")  # same id, another incarnation (even within the same second)
    assert again.id == "shop" and projects.incarnation_of(again) != projects.incarnation_of(shop)
    with pytest.raises(ProjectNotFound):
        diagrams.import_new(
            Diagram(id="late", title="Late"), "Late", None, "shop", incarnation=projects.incarnation_of(shop)
        )
    assert not store.exists("late")
    assert store.read_project("shop")[0].diagrams == []
    created = diagrams.import_new(
        Diagram(id="ok", title="Ok"),
        "Ok",
        None,
        "shop",
        incarnation=projects.incarnation_of(store.read_project("shop")[0]),
    )
    assert [m.diagram for m in store.read_project("shop")[0].diagrams] == [created]


def test_a_confirmed_project_proposal_notes_nothing_in_a_project_the_diagram_left(store, monkeypatch):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    assert client.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code in (200, 201)
    assert client.post("/api/projects", json={"title": "Other", "id": "other"}).status_code in (200, 201)
    created = client.post("/api/diagrams", json={"title": "Orders", "id": "orders", "project": "shop"})
    assert created.status_code == 201
    preview = store.load("orders")
    base = fingerprint(preview)
    ops.apply(preview, "add_node", {"name": "cache", "kind": "service"})
    body = {
        "diagram": "orders",
        "operations": [{"op": "add_node", "arguments": {"name": "cache", "kind": "service"}}],
        "base": base,
        "result": fingerprint(preview),
    }
    real_holds = ProjectService.holds

    def moved_meanwhile(self, project_id, diagram_id):
        system = ProjectService(store, store)
        DiagramService(store, system).move(diagram_id, "other")  # another request moves it during the replay
        monkeypatch.setattr(ProjectService, "holds", real_holds)
        return real_holds(self, project_id, diagram_id)

    monkeypatch.setattr(ProjectService, "holds", moved_meanwhile)
    response = client.post("/api/projects/shop/proposal/apply", json=body)
    assert response.status_code == 200 and "cache" in store.load("orders").nodes
    assert not [e for e in store.load_project_chat("shop") if e.get("proposal_outcome")]


def test_a_confirmed_project_proposal_is_noted_while_the_diagram_stays(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    client.post("/api/diagrams", json={"title": "Orders", "id": "orders", "project": "shop"})
    preview = store.load("orders")
    base = fingerprint(preview)
    ops.apply(preview, "add_node", {"name": "cache", "kind": "service"})
    body = {
        "diagram": "orders",
        "operations": [{"op": "add_node", "arguments": {"name": "cache", "kind": "service"}}],
        "base": base,
        "result": fingerprint(preview),
    }
    assert client.post("/api/projects/shop/proposal/apply", json=body).status_code == 200
    assert [e["proposal_outcome"] for e in store.load_project_chat("shop")] == ["applied"]


def test_new_projects_carry_a_fresh_incarnation_and_old_ones_fall_back_to_created_at():
    a, b = projects.new_project("A", "a"), projects.new_project("A", "a")
    assert a.incarnation and b.incarnation and a.incarnation != b.incarnation
    legacy = projects.Project.model_validate({"id": "a", "title": "A", "created_at": "2026-01-01T00:00:00+00:00"})
    assert projects.incarnation_of(legacy) == "2026-01-01T00:00:00+00:00"
    assert "incarnation" not in legacy.model_dump()  # data written before M23 keeps its JSON


def test_a_confirmed_project_proposal_whose_project_vanished_is_applied_without_a_note(store, monkeypatch):
    """Codex review on #194: the project deleted (after the diagram moved away) during the replay is not a 500."""
    from arcviz.services.access import ProjectNotFound as Gone

    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    client.post("/api/projects", json={"title": "Shop", "id": "shop"})
    client.post("/api/diagrams", json={"title": "Orders", "id": "orders", "project": "shop"})
    preview = store.load("orders")
    base = fingerprint(preview)
    ops.apply(preview, "add_node", {"name": "cache", "kind": "service"})
    body = {
        "diagram": "orders",
        "operations": [{"op": "add_node", "arguments": {"name": "cache", "kind": "service"}}],
        "base": base,
        "result": fingerprint(preview),
    }

    def gone(self, project_id, diagram_id):
        raise Gone(f"Project '{project_id}' not found.")

    monkeypatch.setattr(ProjectService, "holds", gone)
    response = client.post("/api/projects/shop/proposal/apply", json=body)
    assert response.status_code == 200 and "cache" in store.load("orders").nodes
    assert not [e for e in store.load_project_chat("shop") if e.get("proposal_outcome")]
