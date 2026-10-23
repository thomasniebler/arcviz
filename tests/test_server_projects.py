"""Project API (spec: docs/architecture/specs/projects.md, #132)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz.model import Diagram
from arcviz.server import create_app
from arcviz.services import ProjectService
from arcviz.workspaces import personal_project_id, personal_workspace_id
from tests.conftest import ScriptedProvider


@pytest.fixture
def client(store):
    return TestClient(create_app(store=store, provider=ScriptedProvider([])))


def _diagram(client, title, **extra):
    r = client.post("/api/diagrams", json={"title": title, "template": "empty", **extra})
    assert r.status_code == 201, r.text
    return r.json()["diagram"]["id"]


def test_existing_diagrams_land_in_the_default_project_at_start(store):
    store.create("Alt", "alt", Diagram(id="alt", title="Alt"))  # stored before projects existed
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))  # the start-up repair adopts it
    summaries = [p for p in client.get("/api/projects").json() if p["id"] != personal_project_id("admin")]
    assert summaries == [
        {
            "id": "default",
            "title": "Standardprojekt",
            "description": "",
            "diagram_count": 1,
            "updated_at": summaries[0]["updated_at"],
            "workspace": personal_workspace_id("admin"),
            "role": "owner",  # the admin identity owns the default project
            "owner": {"id": "admin", "name": "Administrator", "email": None},
            "shared": False,
        }
    ]
    view = client.get("/api/projects/default").json()
    assert view["tree"] == {"diagrams": ["alt"], "sections": []}
    assert [(d["id"], d["title"], d["section"]) for d in view["diagrams"]] == [("alt", "Alt", None)]


def test_project_crud(client):
    r = client.post("/api/projects", json={"title": "Webshop", "description": "Alles rund um den Shop"})
    assert r.status_code == 201
    view = r.json()
    assert view["project"]["id"] == "webshop" and view["project"]["description"] == "Alles rund um den Shop"
    assert view["tree"] == {"diagrams": [], "sections": []} and view["diagrams"] == []
    again = client.post("/api/projects", json={"title": "Webshop"})  # a taken id gets a free variant (ADR-0089)
    assert again.status_code == 201 and again.json()["project"]["id"] == "webshop-2"
    assert client.post("/api/projects", json={"title": "X", "id": "Bad Id"}).status_code == 400
    assert client.post("/api/projects", json={"title": "  "}).status_code == 400

    r = client.patch("/api/projects/webshop", json={"title": "Shop", "description": ""})
    assert r.status_code == 200 and r.json()["project"]["title"] == "Shop" and r.json()["project"]["description"] == ""
    assert client.get("/api/projects/nope").status_code == 404
    assert client.patch("/api/projects/nope", json={"title": "x"}).status_code == 404

    assert client.delete("/api/projects/webshop").status_code == 204
    assert client.get("/api/projects/webshop").status_code == 404
    assert client.delete("/api/projects/webshop").status_code == 404


def test_create_diagram_in_a_project_and_section(client):
    client.post("/api/projects", json={"title": "Shop"})
    client.post("/api/projects/shop/sections", json={"title": "Backend"})
    diagram_id = _diagram(client, "Orders", project="shop", section="backend")
    view = client.get("/api/projects/shop").json()
    assert view["tree"]["sections"][0]["diagrams"] == [diagram_id]
    assert client.get("/api/projects/shop/diagrams").json()[0]["section"] == "backend"
    # the diagram routes are unchanged
    assert client.get(f"/api/diagrams/{diagram_id}").status_code == 200


def test_create_without_project_goes_to_the_callers_default_project(client):
    diagram_id = _diagram(client, "Lonely")
    home = personal_project_id("admin")  # off mode: everybody is the admin identity
    assert client.get(f"/api/projects/{home}").json()["tree"]["diagrams"] == [diagram_id]


@pytest.mark.parametrize(
    ("extra", "status"), [({"project": "nope"}, 404), ({"project": "shop", "section": "nope"}, 400)]
)
def test_unknown_target_is_refused_before_anything_is_created(client, extra, status):
    client.post("/api/projects", json={"title": "Shop"})
    r = client.post("/api/diagrams", json={"title": "Orders", "template": "empty", **extra})
    assert r.status_code == status
    assert client.get("/api/diagrams").json() == []


def test_import_into_a_project(client):
    client.post("/api/projects", json={"title": "Shop"})
    source = _diagram(client, "Orders")
    document = client.get(f"/api/diagrams/{source}/export").json()
    r = client.post("/api/diagrams/import", json={"document": document, "project": "shop"})
    assert r.status_code == 201
    imported = r.json()["diagram"]["id"]
    assert client.get("/api/projects/shop").json()["tree"]["diagrams"] == [imported]
    r = client.post("/api/diagrams/import", json={"document": document, "project": "nope"})
    assert r.status_code == 404


def test_deleting_a_diagram_removes_its_membership(client):
    client.post("/api/projects", json={"title": "Shop"})
    diagram_id = _diagram(client, "Orders", project="shop")
    assert client.delete("/api/projects/shop").status_code == 409  # not empty
    assert client.delete(f"/api/diagrams/{diagram_id}").status_code == 204
    assert client.get("/api/projects/shop").json()["tree"]["diagrams"] == []
    assert client.delete("/api/projects/shop").status_code == 204


def test_sections_create_rename_move_delete(client):
    client.post("/api/projects", json={"title": "Shop"})
    r = client.post("/api/projects/shop/sections", json={"title": "Backend"})
    assert r.status_code == 201 and r.json()["section"]["id"] == "backend"
    client.post("/api/projects/shop/sections", json={"title": "Services", "parent": "backend"})
    client.post("/api/projects/shop/sections", json={"title": "Frontend", "position": 0})
    diagram_id = _diagram(client, "Orders", project="shop", section="services")

    r = client.patch("/api/projects/shop/sections/services", json={"title": "Dienste"})
    assert r.status_code == 200
    r = client.patch("/api/projects/shop/sections/services", json={"parent": None, "position": 0})
    assert [s["id"] for s in r.json()["tree"]["sections"]] == ["services", "frontend", "backend"]
    assert r.json()["tree"]["sections"][0]["title"] == "Dienste"
    assert client.patch("/api/projects/shop/sections/backend", json={"parent": "backend"}).status_code == 400
    assert client.patch("/api/projects/shop/sections/nope", json={"title": "x"}).status_code == 400

    r = client.delete("/api/projects/shop/sections/services")
    assert r.status_code == 200 and r.json()["tree"]["diagrams"] == [diagram_id]  # contents moved up
    assert client.delete("/api/projects/shop/sections/services").status_code == 400
    assert client.post("/api/projects/nope/sections", json={"title": "x"}).status_code == 404


def test_generic_ops_route_refuses_internal_operations(client):
    client.post("/api/projects", json={"title": "Shop"})
    r = client.post("/api/projects/shop/ops", json={"op": "add_section", "args": {"title": "Docs"}})
    assert r.status_code == 200 and r.json()["result"]["result"]["id"] == "docs"
    assert r.json()["tree"]["sections"][0]["id"] == "docs"
    r = client.post("/api/projects/shop/ops", json={"op": "add_diagram", "args": {"diagram": "x"}})
    assert r.status_code == 400 and "internal" in r.json()["detail"]
    r = client.post("/api/projects/shop/ops", json={"op": "rename_project", "args": {"title": 1}})
    assert r.status_code == 400


def test_move_diagram_within_and_across_projects(client):
    client.post("/api/projects", json={"title": "Shop"})
    client.post("/api/projects", json={"title": "Archiv"})
    client.post("/api/projects/archiv/sections", json={"title": "Alt"})
    a = _diagram(client, "A", project="shop")
    b = _diagram(client, "B", project="shop")
    r = client.post(f"/api/projects/shop/diagrams/{b}/move", json={"position": 0})
    assert r.status_code == 200 and r.json()["tree"]["diagrams"] == [b, a]

    r = client.post(f"/api/projects/shop/diagrams/{a}/move", json={"project": "archiv", "section": "alt"})
    assert r.status_code == 200 and r.json()["project"]["id"] == "archiv"
    assert r.json()["tree"]["sections"][0]["diagrams"] == [a]
    assert client.get("/api/projects/shop").json()["tree"]["diagrams"] == [b]

    assert client.post(f"/api/projects/shop/diagrams/{a}/move", json={}).status_code == 404  # not in shop any more
    assert client.post(f"/api/projects/archiv/diagrams/{a}/move", json={"project": "nope"}).status_code == 404
    assert client.post(f"/api/projects/archiv/diagrams/{a}/move", json={"section": "nope"}).status_code == 400
    assert client.post(f"/api/projects/archiv/diagrams/{a}/move", json={"position": -1}).status_code == 400


def test_project_routes_need_authentication_when_it_is_on(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_AUTH", "token")
    monkeypatch.setenv("ARCVIZ_AUTH_TOKEN", "s3cret-token-of-sufficient-length")
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/projects", json={"title": "x"}).status_code == 401
    headers = {"Authorization": "Bearer s3cret-token-of-sufficient-length"}
    assert client.get("/api/projects", headers=headers).status_code == 200


def test_project_writes_are_rate_limited_like_other_writes(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_RATE_LIMIT_OPS", "1")
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    assert client.post("/api/projects", json={"title": "A"}).status_code == 201
    assert client.post("/api/projects", json={"title": "B"}).status_code == 429
    assert client.get("/api/projects").status_code == 200  # reads are not limited


def test_project_export_and_import(client):
    client.post("/api/projects", json={"title": "Shop"})
    client.post("/api/projects/shop/sections", json={"title": "Backend"})
    _diagram(client, "Orders", project="shop", section="backend")
    r = client.get("/api/projects/shop/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="shop.arcviz-project.json"'
    bundle = r.json()
    assert bundle["format"] == "arcviz-project"
    r = client.post("/api/projects/import", json={"bundle": bundle})
    assert r.status_code == 201
    view = r.json()
    assert view["project"]["id"] == "shop-2" and view["tree"]["sections"][0]["diagrams"] == ["orders-2"]
    assert client.get("/api/projects/nope/export").status_code == 404
    bundle["format"] = "nope"
    r = client.post("/api/projects/import", json={"bundle": bundle})
    assert r.status_code == 400 and r.json()["detail"].startswith("Invalid bundle: ")


def test_project_import_gets_the_import_body_limit(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_MAX_BODY_BYTES", "2000")
    monkeypatch.setenv("ARCVIZ_MAX_IMPORT_BYTES", "100000")
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    padding = "x" * 5000
    r = client.post("/api/projects/import", json={"bundle": {"format": "arcviz-project", "pad": padding}})
    assert r.status_code == 400  # parsed (and refused), not 413
    r = client.post("/api/projects", json={"title": "A", "description": padding})
    assert r.status_code == 413


def test_a_failed_placement_leaves_no_diagram_behind(store, client, monkeypatch):
    from arcviz.services import ProjectNotFound, ProjectService

    client.post("/api/projects", json={"title": "Shop"})

    def vanished(*args, **kwargs):
        raise ProjectNotFound("Project 'shop' not found.")

    monkeypatch.setattr(ProjectService, "place", vanished)  # the services are built per request
    r = client.post("/api/diagrams", json={"title": "Orders", "template": "empty", "project": "shop"})
    assert r.status_code == 404
    assert client.get("/api/diagrams").json() == []


@pytest.mark.parametrize("bad", ["", "a/b", "Upper"])
def test_bundle_section_ids_must_be_slugs(client, bad):
    client.post("/api/projects", json={"title": "Shop"})
    client.post("/api/projects/shop/sections", json={"title": "Backend"})
    _diagram(client, "Orders", project="shop", section="backend")
    bundle = client.get("/api/projects/shop/export").json()
    bundle["project"]["sections"][0]["id"] = bad
    bundle["project"]["diagrams"][0]["section"] = bad
    r = client.post("/api/projects/import", json={"bundle": bundle})
    assert r.status_code == 400 and "invalid section id" in r.json()["detail"]


# ---- project chat (spec: docs/architecture/specs/project-chat.md, #135) --------------------------------


def _shop(store, replies):
    """A project `shop` with the demo diagram, and a client whose model replays `replies`."""
    from arcviz.templates import demo_diagram

    client = TestClient(create_app(store=store, provider=ScriptedProvider(replies)))
    assert client.post("/api/projects", json={"title": "Shop"}).status_code == 201
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    ProjectService(store, store).adopt("demo", "shop")  # an admin places it (arcviz-migrate --adopt)
    return client


def _sse(client, path, body):
    import json

    with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def _add_payment():
    from arcviz.llm.base import ModelReply
    from tests.conftest import call

    return [
        ModelReply(
            "",
            [call("add_node", "c1", name="payment", id="payment", kind="service", parent="prod-deployment")],
            "tool_use",
        ),
        ModelReply("Payment ergänzt.", [], "end_turn"),
    ]


def test_project_chat_streams_and_keeps_its_own_transcript(store):
    client = _shop(store, _add_payment())
    events = _sse(client, "/api/projects/shop/chat", {"message": "Payment ergänzen", "active": "demo"})
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "text", "done", "final"]
    assert events[1]["diagram_id"] == "demo" and "payment" in events[1]["diagram"]["nodes"]
    final = events[-1]
    assert final["active"] == "demo" and final["diagrams"][0]["version"] == 2
    chat = client.get("/api/projects/shop/chat").json()["chat"]
    assert [e["role"] for e in chat] == ["user", "assistant"] and chat[1]["diagrams"][0]["id"] == "demo"
    assert client.get("/api/diagrams/demo").json()["chat"] == []  # the diagram's transcript is untouched
    assert client.delete("/api/projects/shop/chat").status_code == 204
    assert client.get("/api/projects/shop/chat").json()["chat"] == []


def test_project_chat_errors(store, monkeypatch):
    client = _shop(store, [])
    assert client.post("/api/projects/nope/chat", json={"message": "x"}).status_code == 404
    assert client.get("/api/projects/nope/chat").status_code == 404
    assert client.delete("/api/projects/nope/chat").status_code == 404
    assert client.post("/api/projects/shop/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/projects/shop/chat", json={"message": "x" * 20_001}).status_code == 413
    broken = TestClient(create_app(store=store))
    monkeypatch.setenv("ARCVIZ_PROVIDER", "nonsense")  # after start-up, which would refuse it (ADR-0107)
    r = broken.post("/api/projects/shop/chat", json={"message": "hi"})
    assert r.status_code == 503


def test_project_chat_compact(store, monkeypatch):
    from arcviz.llm.base import ModelReply

    monkeypatch.setenv("ARCVIZ_CHAT_KEEP", "2")

    replies = [ModelReply(f"a{i}", [], "end_turn") for i in range(3)] + [ModelReply("S", [], "end_turn")]
    client = _shop(store, replies)
    for i in range(3):
        _sse(client, "/api/projects/shop/chat", {"message": f"u{i}"})
    r = client.post("/api/projects/shop/chat/compact")
    assert r.status_code == 200 and r.json()["compacted"] is True
    assert r.json()["chat"][0]["role"] == "summary" and r.json()["chat"][0]["content"] == "S"
    assert client.post("/api/projects/nope/chat/compact").status_code == 404


def test_project_proposal_apply_and_discard_note_the_project_transcript(store):
    client = _shop(store, _add_payment() + _add_payment())
    events = _sse(client, "/api/projects/shop/chat", {"message": "Payment?", "active": "demo", "mode": "propose"})
    proposal = events[-1]["proposal"]
    assert proposal["diagram_id"] == "demo" and "payment" not in store.load("demo").nodes
    body = {"diagram": "demo", **{k: proposal[k] for k in ("operations", "base", "result")}}
    r = client.post("/api/projects/shop/proposal/apply", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["version"]["version"] == 2 and "payment" in r.json()["diagram"]["nodes"]
    assert r.json()["changed"] == ["payment"]
    assert client.post("/api/projects/shop/proposal/apply", json=body).status_code == 409  # stale base now
    chat = client.get("/api/projects/shop/chat").json()["chat"]
    assert chat[-1]["proposal_outcome"] == "applied"
    assert client.get("/api/diagrams/demo").json()["chat"] == []

    _sse(client, "/api/projects/shop/chat", {"message": "Noch einmal?", "active": "demo", "mode": "propose"})
    r = client.post("/api/projects/shop/proposal/discard", json={"diagram": "demo"})
    assert r.status_code == 200 and r.json()["chat"][-1]["proposal_outcome"] == "discarded"


def test_project_proposal_routes_check_the_membership(store):
    client = _shop(store, [])
    store.create("Fremd", "fremd", Diagram(id="fremd", title="Fremd"))
    body = {"diagram": "fremd", "operations": [], "base": "x", "result": "y"}
    assert client.post("/api/projects/shop/proposal/apply", json=body).status_code == 404
    assert client.post("/api/projects/nope/proposal/apply", json=body).status_code == 404
    assert client.post("/api/projects/shop/proposal/discard", json={"diagram": "fremd"}).status_code == 404
    assert client.post("/api/projects/shop/proposal/discard", json={}).status_code == 200


# ---- project review (spec: docs/architecture/specs/project-review.md, #136) ----------------------------


def test_project_checks_and_review(store):
    from arcviz.llm.base import ModelReply
    from tests.conftest import call

    review = ModelReply(
        "",
        [
            call(
                "submit_project_review",
                "r1",
                summary="Gut strukturiert.",
                findings=[
                    {
                        "category": "risk",
                        "severity": "high",
                        "message": "TLS fehlt.",
                        "diagram": "demo",
                        "elements": ["service-a"],
                    }
                ],
            )
        ],
        "tool_use",
    )
    client = _shop(store, [review])
    assert client.post("/api/projects/shop/sections", json={"title": "Leer"}).status_code == 201
    before = (store.list_versions("demo"), store.load_project_chat("shop"), store.read_project("shop")[1])

    checks = client.get("/api/projects/shop/checks").json()
    assert checks["project"] == "shop" and [r["id"] for r in checks["rules"]][0] == "empty-section"
    assert any(f["rule"] == "empty-section" and f["section"] == "leer" for f in checks["findings"])
    assert any(f["diagram"] == "demo" and f["id"].startswith("demo:") for f in checks["findings"])
    assert checks["diagrams"]["demo"]["title"] == "Demo"

    result = client.post("/api/projects/shop/review").json()
    assert result["llm"]["ok"] is True and result["summary"] == "Gut strukturiert."
    llm = [f for f in result["findings"] if f["source"] == "llm"]
    assert llm == [{**llm[0], "diagram": "demo", "elements": ["service-a"]}]
    assert (store.list_versions("demo"), store.load_project_chat("shop"), store.read_project("shop")[1]) == before

    assert client.get("/api/projects/nope/checks").status_code == 404
    assert client.post("/api/projects/nope/review").status_code == 404


def test_project_review_without_provider_is_503(store, monkeypatch):
    client = TestClient(create_app(store=store))
    monkeypatch.setenv("ARCVIZ_PROVIDER", "nonsense")  # after start-up, which would refuse it (ADR-0107)
    client.post("/api/projects", json={"title": "Shop"})
    assert client.post("/api/projects/shop/review").status_code == 503
    assert client.get("/api/projects/shop/checks").status_code == 200  # the rules need no model
