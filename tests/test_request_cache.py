"""One project read per request (#166; spec: docs/architecture/specs/scale-and-robustness.md, ADR-0121).

Guards of ordinary HTTP requests cache the project documents their viewer checks read; checks for more than viewer
and `fresh` checks read again, writes of the request drop the cached copy. Chat turns and live streams never cache.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz.server import create_app
from arcviz.services import ServiceRoot
from arcviz.services.access import DiagramNotFound
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity
from tests.conftest import ScriptedProvider

ADA, VIC = (Identity(n, n.capitalize(), f"{n}@example.org") for n in ("ada", "vic"))


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in (ADA, VIC):
        root.users.seen(identity)
    ada = root.for_actor(ADA)
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "vic", "viewer")
    ada.diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop")
    return root


def count_project_reads(store, monkeypatch) -> list[str]:
    reads: list[str] = []
    original = store.read_project
    monkeypatch.setattr(store, "read_project", lambda pid: reads.append(pid) or original(pid))
    return reads


def test_a_request_scoped_guard_reads_the_project_once(root, store, monkeypatch):
    svc = root.for_actor(VIC, per_request=True)
    reads = count_project_reads(store, monkeypatch)
    svc.diagrams.state("orders")
    svc.diagrams.access("orders")
    svc.diagrams.versions("orders")
    svc.diagrams.get("orders")
    svc.projects.view("shop")
    assert reads == ["shop"]


def test_without_the_request_cache_every_check_reads_the_project(root, store, monkeypatch):
    svc = root.for_actor(VIC)
    reads = count_project_reads(store, monkeypatch)
    svc.diagrams.state("orders")
    svc.diagrams.get("orders")
    assert len(reads) >= 2


def test_checks_above_viewer_read_the_project_again(root, store, monkeypatch):
    svc = root.for_actor(ADA, per_request=True)
    svc.diagrams.get("orders")
    reads = count_project_reads(store, monkeypatch)
    svc.diagrams.apply_operation("orders", "rename_diagram", {"title": "Neu"}, "x")
    assert reads == ["shop"]


def test_a_write_of_the_request_is_visible_to_its_later_reads(root):
    svc = root.for_actor(ADA, per_request=True)
    assert svc.projects.view("shop").project.title == "Shop"
    svc.projects.apply("shop", "rename_project", {"title": "Laden"})
    view = svc.projects.view("shop")
    assert view.project.title == "Laden" and view.revision == root.for_actor(ADA).projects.view("shop").revision


def test_a_revoke_in_another_request_is_seen_by_the_next_request(root):
    vic = root.for_actor(VIC, per_request=True)
    vic.diagrams.get("orders")
    root.for_actor(ADA).projects.revoke("shop", "vic")
    with pytest.raises(DiagramNotFound):
        root.for_actor(VIC, per_request=True).diagrams.get("orders")
    with pytest.raises(DiagramNotFound):
        vic.diagrams.require("orders", "editor")  # above viewer: read again, also within the request


def test_a_fresh_check_reads_again(root, store, monkeypatch):
    svc = root.for_actor(VIC, per_request=True)
    svc.diagrams.get("orders")
    root.for_actor(ADA).projects.revoke("shop", "vic")
    with pytest.raises(DiagramNotFound):
        svc.guard.diagram("orders", fresh=True)


def test_ordinary_routes_cache_chat_and_live_routes_do_not(store, monkeypatch):
    """The HTTP wiring: `services_of` builds request-scoped guards, the chat and live routes guards without the cache."""
    from arcviz import server

    built: list[bool] = []
    original = ServiceRoot.for_actor

    def recording(self, actor, per_request=False):
        built.append(per_request)
        return original(self, actor, per_request=per_request)

    monkeypatch.setattr(ServiceRoot, "for_actor", recording)
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    built.clear()
    client.get("/api/diagrams/demo")
    assert built[-1] is True  # the route's (the first request also builds the sign-in's, without the cache)
    built.clear()
    client.post("/api/diagrams/demo/chat", json={"message": "hallo"})
    assert built == [False]
    assert server.turn_services_of is not server.services_of


def test_a_placement_reads_the_project_again_for_its_editor_check(root, store):
    """Review of #166: check_target needs editor, so it never decides on the request's cached copy of the project."""
    from arcviz.services import Forbidden

    ada = root.for_actor(ADA)
    ada.projects.share("shop", "vic", "editor")
    vic = root.for_actor(VIC, per_request=True)
    vic.projects.view("shop")  # cached with vic as editor
    ada.projects.share("shop", "vic", "viewer")  # lowered by another request
    with pytest.raises(Forbidden):
        vic.diagrams.create("Neu", "neu", demo_diagram("neu", "Neu"), "shop")
    assert not store.exists("neu")
