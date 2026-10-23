"""Authors of versions and transcript entries (issue #162; spec: docs/architecture/specs/workspaces-and-sharing.md,
ADR-0086)."""

from __future__ import annotations

import pytest

from arcviz.agent import DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.project_agent import ProjectAgent
from arcviz.services import ServiceRoot
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity, personal_workspace_id
from tests.conftest import ScriptedProvider, call, proxy_users

ADA = Identity("ada", "Ada Lovelace", "ada@example.org")
ED = Identity("ed", "Ed Editor", "ed@example.org")


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in (ADA, ED):
        root.users.seen(identity)
    ada = root.for_actor(ADA)
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    ada.diagrams.create("Flow", "flow", demo_diagram("flow", "Flow"), "shop")
    return root


def authors(store, diagram_id: str) -> list[tuple[str, str | None]]:
    return [(v.source, v.author_name) for v in store.list_versions(diagram_id)]


def test_every_kind_of_version_records_its_author(root, store):
    ed = root.for_actor(ED).diagrams
    ed.apply_operation("orders", "rename_diagram", {"title": "Bestellungen"}, "Umbenannt")
    ed.snapshot("orders", "Stand")
    ed.undo("orders")
    ed.redo("orders")
    ed.restore("orders", 1)
    assert authors(store, "orders") == [
        ("initial", "Ada Lovelace"),
        ("auto", "Ed Editor"),
        ("manual", "Ed Editor"),
        ("undo", "Ed Editor"),
        ("redo", "Ed Editor"),
        ("restore", "Ed Editor"),
    ]
    assert store.list_versions("orders")[1].author == "ed"


def test_propagated_model_changes_record_who_renamed(root, store):
    ada = root.for_actor(ADA)
    element = ada.models.add_element("shop", "Bezahlung")
    node = next(n.id for n in store.load("flow").nodes.values() if n.kind == "service")
    ada.models.link("flow", node, element.id)
    root.for_actor(ED).models.update_element("shop", element.id, name="Zahlung")
    assert store.list_versions("flow")[-1].author_name == "Ed Editor"
    assert store.list_versions("flow")[-2].author_name == "Ada Lovelace"  # the link


def test_a_chat_turn_records_its_author_on_the_version_and_the_transcript(root, store):
    replies = [
        ModelReply(text="", tool_calls=[call("rename_diagram", "c1", title="Neu")], stop_reason="tool_use"),
        ModelReply(text="Erledigt.", tool_calls=[], stop_reason="end_turn"),
    ]
    ada = root.for_actor(ADA)
    base = DiagramAgent(ScriptedProvider(replies), ada.diagrams, background=False)
    ed = root.for_actor(ED)
    list(base.bound(ed.diagrams, ed.models).chat("orders", "Benenne um"))
    assert store.list_versions("orders")[-1].author_name == "Ed Editor"
    assert [(e["role"], e.get("author"), e.get("author_name")) for e in store.load_chat("orders")] == [
        ("user", "ed", "Ed Editor"),
        ("assistant", "ed", "Ed Editor"),
    ]


def test_project_chat_entries_and_proposal_notes_record_their_author(root, store):
    replies = [ModelReply(text="Zwei Diagramme.", tool_calls=[], stop_reason="end_turn")]
    ada = root.for_actor(ADA)
    base = ProjectAgent(ScriptedProvider(replies), ada.diagrams, ada.projects, ada.models, background=False)
    ed = root.for_actor(ED)
    list(base.bound(ed.diagrams, ed.models, ed.projects).chat("shop", "Was gibt es?"))
    ed.projects.append_chat("shop", [{"role": "assistant", "content": "verworfen", "author": "someone"}])
    entries = store.load_project_chat("shop")
    assert [e.get("author") for e in entries] == ["ed", "ed", "someone"]  # an existing author is kept


def test_system_writes_and_summaries_have_no_author(root, store):
    from arcviz.services import DiagramService, ProjectService

    system = DiagramService(store, ProjectService(store, store))
    system.apply_operation("orders", "rename_diagram", {"title": "Sys"}, "System")
    system.append_chat("orders", [{"role": "user", "content": "x"}])
    assert store.list_versions("orders")[-1].author is None
    assert "author" not in store.load_chat("orders")[0]
    ed = root.for_actor(ED).diagrams
    generation = ed.chat_generation("orders")
    ed.append_chat("orders", [{"role": "assistant", "content": "y"}])
    ed.compact_chat("orders", 2, {"role": "summary", "content": "s"}, generation)
    assert "author" not in store.load_chat("orders")[0]


def test_the_api_shows_the_authors(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    users("ada", "POST", "/api/projects", json={"title": "Shop"})
    users("ada", "POST", "/api/diagrams", json={"title": "Orders", "template": "demo", "project": "shop"})
    detail = users("ada", "POST", "/api/diagrams/orders/ops", json={"op": "rename_diagram", "args": {"title": "O"}})
    versions = detail.json()["versions"]
    assert [(v["author"], v["author_name"]) for v in versions] == [("ada", "Ada"), ("ada", "Ada")]
    assert detail.json()["access"] == {"role": "owner", "project": "shop", "workspace": personal_workspace_id("ada")}
