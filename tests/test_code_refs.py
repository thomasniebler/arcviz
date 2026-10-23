"""Code links on project elements (spec docs/architecture/specs/agent-access.md, ADR-0171)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange, projects
from arcviz.agent import DiagramAgent
from arcviz.project_agent import ProjectAgent
from arcviz.project_model import MAX_CODE_REFS, CodeRef, Element, owner_of_path
from arcviz.projects import ProjectError
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from tests.conftest import ScriptedProvider, call
from tests.test_concurrency import done, tool_round


def project() -> projects.Project:
    p = projects.new_project("Shop", "shop")
    projects.apply(p, "add_element", {"name": "API", "kind": "component"})
    projects.apply(p, "add_element", {"name": "Orders", "kind": "component"})
    return p


def link(p: projects.Project, element: str, path: str, **args: Any) -> dict[str, Any]:
    return projects.apply(p, "add_code_ref", {"element": element, "path": path, **args})["result"]


# ---- the type --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "normal"),
    [
        ("src/orders/api.py", "src/orders/api.py"),
        ("./src/orders/", "src/orders"),
        ("src//orders", "src/orders"),
        ("  src/a  ", "src/a"),
        (".", "."),
        ("./", "."),
    ],
)
def test_paths_are_normalised(raw, normal):
    assert CodeRef(path=raw).path == normal


@pytest.mark.parametrize(
    "raw", ["", "  ", "/etc/passwd", "../x", "a/../b", "a\\b", "a\x00b", "C:/x", "x" * 201, "a/\nb"]
)
def test_unsafe_or_empty_paths_are_refused(raw):
    with pytest.raises(ValueError):
        CodeRef(path=raw)


def test_other_fields_are_bounded():
    with pytest.raises(ValueError):
        CodeRef(path="a", repo="r" * 65)
    with pytest.raises(ValueError):
        CodeRef(path="a", symbol="s" * 65)
    with pytest.raises(ValueError):
        CodeRef(path="a", note="n" * 201)
    with pytest.raises(ValueError):
        CodeRef(path="a", repo="bad repo")


def test_an_element_without_links_dumps_as_before():
    assert "code_refs" not in Element(id="a", name="A").model_dump()
    assert "code_refs" not in project().model_dump(mode="json")["elements"][0]
    assert Element.model_validate({"id": "a", "name": "A"}).code_refs == []


# ---- operations ------------------------------------------------------------------------------------------------


def test_add_and_remove_a_link():
    p = project()
    result = link(p, "api", "src/api", symbol="OrdersApi", note="entry point")
    assert result["element"] == "api"
    assert result["code_refs"] == [{"path": "src/api", "repo": "", "symbol": "OrdersApi", "note": "entry point"}]
    assert p.find_element("API").code_refs[0].symbol == "OrdersApi"  # by name too
    out = projects.apply(p, "remove_code_ref", {"element": "api", "path": "./src/api/", "symbol": "OrdersApi"})
    assert out["result"]["code_refs"] == []
    assert "code_refs" not in p.model_dump(mode="json")["elements"][0]


def test_adding_the_same_link_again_updates_its_note():
    p = project()
    link(p, "api", "src/api", note="a")
    again = link(p, "api", "src/api", note="b")
    assert [r["note"] for r in again["code_refs"]] == ["b"]
    link(p, "api", "src/api", repo="backend")  # another repo is another link
    assert len(p.find_element("api").code_refs) == 2


def test_an_element_holds_at_most_max_links():
    p = project()
    for n in range(MAX_CODE_REFS):
        link(p, "api", f"src/m{n}")
    with pytest.raises(ProjectError, match=f"at most {MAX_CODE_REFS}"):
        link(p, "api", "src/more")
    assert len(p.find_element("api").code_refs) == MAX_CODE_REFS


@pytest.mark.parametrize(
    ("op", "args", "message"),
    [
        ("add_code_ref", {"element": "nope", "path": "a"}, "Unknown element 'nope'"),
        ("add_code_ref", {"element": "api", "path": "../a"}, "path"),
        ("add_code_ref", {"element": "api", "path": ""}, "path"),
        ("remove_code_ref", {"element": "api", "path": "a"}, "no code link"),
        ("remove_code_ref", {"element": "nope", "path": "a"}, "Unknown element"),
    ],
)
def test_bad_input_is_refused_and_changes_nothing(op, args, message):
    p = project()
    before = p.model_dump(mode="json")
    with pytest.raises(ProjectError, match=message):
        projects.apply(p, op, args)
    assert p.model_dump(mode="json") == before


def test_removing_an_unknown_link_lists_the_existing_ones():
    p = project()
    link(p, "api", "src/api")
    with pytest.raises(ProjectError, match="src/api"):
        projects.apply(p, "remove_code_ref", {"element": "api", "path": "src/other"})


def test_updates_and_removal_of_the_element():
    p = project()
    link(p, "api", "src/api")
    projects.apply(p, "update_element", {"element": "api", "name": "Public API", "description": "Entry"})
    assert p.find_element("public-api") is None or p.find_element("api").code_refs  # links stay on the element
    assert p.find_element("api").code_refs[0].path == "src/api"
    projects.apply(p, "remove_element", {"element": "api"})
    assert owner_of_path(p.elements, "src/api/x.py") == []


def test_code_ref_operations_are_public():
    assert {"add_code_ref", "remove_code_ref"} <= set(projects.PUBLIC_OPERATIONS)
    assert set(projects.CODE_REF_OPERATIONS) == {"add_code_ref", "remove_code_ref"}


# ---- path lookup -----------------------------------------------------------------------------------------------


def lookup(p: projects.Project, path: str, repo: str | None = None) -> list[tuple[str, str]]:
    return [(e.id, r.path) for e, r in owner_of_path(p.elements, path, repo)]


def test_the_longest_matching_link_owns_a_path():
    p = project()
    link(p, "api", "src")
    link(p, "orders", "src/orders")
    assert lookup(p, "src/orders/db.py") == [("orders", "src/orders")]
    assert lookup(p, "src/orders") == [("orders", "src/orders")]
    assert lookup(p, "src/web/x.py") == [("api", "src")]
    assert lookup(p, "srcx/y") == []  # a prefix of characters is not a parent directory
    assert lookup(p, "./src/orders/") == [("orders", "src/orders")]


def test_ties_keep_every_owner_and_the_root_link_owns_everything():
    p = project()
    link(p, "api", "src/shared")
    link(p, "orders", "src/shared")
    assert sorted(lookup(p, "src/shared/util.py")) == [("api", "src/shared"), ("orders", "src/shared")]
    link(p, "orders", ".")
    assert lookup(p, "docs/readme.md") == [("orders", ".")]


def test_repo_labels_narrow_the_lookup():
    p = project()
    link(p, "api", "src", repo="backend")
    link(p, "orders", "src", repo="worker")
    link(p, "orders", "lib")  # no repo: any repository
    assert lookup(p, "src/a.py", "backend") == [("api", "src")]
    assert lookup(p, "src/a.py", "worker") == [("orders", "src")]
    assert sorted(lookup(p, "src/a.py")) == [("api", "src"), ("orders", "src")]
    assert lookup(p, "lib/x", "backend") == [("orders", "lib")]


@pytest.mark.parametrize("path", ["", "/abs", "../x", "a/../b"])
def test_an_invalid_query_path_matches_nothing(path):
    p = project()
    link(p, "api", ".")
    assert lookup(p, path) == []


# ---- bundles ---------------------------------------------------------------------------------------------------


def test_bundles_keep_their_version_without_links_and_use_the_newest_with_them():
    p = project()
    assert exchange.export_project(p, [])["schema_version"] == 2
    link(p, "api", "src/api", repo="backend")
    bundle = exchange.export_project(p, [])
    assert bundle["schema_version"] == exchange.CODE_REFS_PROJECT_SCHEMA_VERSION == 6
    assert exchange.PROJECT_SCHEMA_VERSION >= 6  # a newer one (7: requirements) is only written for what needs it
    parsed = exchange.parse_project(json.loads(json.dumps(bundle)))
    assert parsed.elements[0].code_refs[0].repo == "backend"


def test_a_bundle_with_a_broken_link_is_refused():
    p = project()
    link(p, "api", "src/api")
    bundle = json.loads(json.dumps(exchange.export_project(p, [])))
    bundle["project"]["elements"][0]["code_refs"][0]["path"] = "../escape"
    with pytest.raises(exchange.ExchangeError, match="path"):
        exchange.parse_project(bundle)


# ---- HTTP ------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    c = TestClient(create_app(store=store))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    assert c.post("/api/projects/shop/elements", json={"name": "API"}).status_code == 201
    assert c.post("/api/projects/shop/elements", json={"name": "Orders"}).status_code == 201
    return c


def test_code_ref_routes(client):
    url = "/api/projects/shop/elements/api/code-refs"
    r = client.post(url, json={"path": "src/api", "symbol": "App"})
    assert r.status_code == 201
    assert r.json()["code_refs"] == [{"path": "src/api", "repo": "", "symbol": "App", "note": ""}]
    assert client.get(url).json()["code_refs"][0]["path"] == "src/api"
    elements = client.get("/api/projects/shop/elements").json()["elements"]
    assert [e["code_refs"] for e in elements if e["id"] == "api"][0][0]["path"] == "src/api"
    r = client.delete(url, params={"path": "src/api", "symbol": "App"})
    assert r.status_code == 200 and r.json()["code_refs"] == []
    assert client.delete(url, params={"path": "src/api"}).status_code == 400
    assert client.post(url, json={"path": "../x"}).status_code == 400
    assert client.post(url, json={"path": "a", "bogus": 1}).status_code == 422
    assert client.post("/api/projects/shop/elements/nope/code-refs", json={"path": "a"}).status_code == 400
    assert client.get("/api/projects/nope/elements/api/code-refs").status_code == 404


def test_the_owner_of_a_path(client):
    client.post("/api/projects/shop/elements/api/code-refs", json={"path": "src"})
    client.post("/api/projects/shop/elements/orders/code-refs", json={"path": "src/orders"})
    r = client.get("/api/projects/shop/code-owners", params={"path": "src/orders/x.py"})
    assert r.status_code == 200
    assert [(o["element"]["id"], o["code_ref"]["path"]) for o in r.json()["owners"]] == [("orders", "src/orders")]
    assert client.get("/api/projects/shop/code-owners", params={"path": "../x"}).json()["owners"] == []
    assert client.get("/api/projects/shop/code-owners").status_code == 422


def test_import_keeps_the_links(client):
    client.post("/api/projects/shop/elements/api/code-refs", json={"path": "src/api"})
    bundle = client.get("/api/projects/shop/export").json()
    r = client.post("/api/projects/import", json={"bundle": bundle})
    assert r.status_code == 201
    imported = r.json()["project"]["id"]
    elements = client.get(f"/api/projects/{imported}/elements").json()["elements"]
    assert [e["code_refs"][0]["path"] for e in elements if e.get("code_refs")] == ["src/api"]


# ---- the assistants --------------------------------------------------------------------------------------------


@pytest.fixture
def services(store, demo):
    project_service = ProjectService(store, store)
    project_service.create("Shop", "shop")
    project_service.place("demo", "shop")
    diagrams = DiagramService(store, project_service)
    models = ModelService(project_service, diagrams)
    models.add_element("shop", "API", "component")
    return diagrams, project_service, models


def results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(e["content"]) for e in events if e["type"] == "tool_result"]


def test_the_chat_links_and_unlinks_code(services):
    diagrams, project_service, models = services
    provider = ScriptedProvider(
        [
            tool_round(call("add_code_ref", "c1", element="api", path="src/api", symbol="App")),
            tool_round(call("list_elements", "c2")),
            tool_round(call("remove_code_ref", "c3", element="api", path="src/api", symbol="App")),
            done(),
        ]
    )
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Verknüpfe API"))
    added, listed, removed = results(events)
    assert added["code_refs"][0]["path"] == "src/api"
    assert listed["elements"][0]["code_refs"][0]["symbol"] == "App"
    assert removed["code_refs"] == []
    assert events[-1]["error"] is None


def test_preview_mode_refuses_code_links(services):
    diagrams, project_service, models = services
    provider = ScriptedProvider([tool_round(call("add_code_ref", "c1", element="api", path="src/api")), done()])
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "x", propose=True))
    [refused] = results(events)
    assert refused["ok"] is False and "preview mode" in refused["error"]
    assert project_service.get("shop").elements[0].code_refs == []


def test_the_project_chat_links_code(services):
    diagrams, project_service, models = services
    provider = ScriptedProvider([tool_round(call("add_code_ref", "c1", element="api", path="src/api")), done()])
    agent = ProjectAgent(provider, diagrams, project_service, background=False, models=models)
    list(agent.chat("shop", "Verknüpfe API mit src/api"))
    assert project_service.get("shop").elements[0].code_refs[0].path == "src/api"


def test_a_code_link_error_reaches_the_model(services):
    diagrams, project_service, models = services
    provider = ScriptedProvider([tool_round(call("add_code_ref", "c1", element="api", path="../x")), done()])
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "x"))
    [failed] = results(events)
    assert failed["ok"] is False and "path" in failed["error"]
