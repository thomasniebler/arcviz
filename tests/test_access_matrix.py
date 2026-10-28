"""Every endpoint × role (issues #161, #178; spec: docs/architecture/specs/personal-workspaces.md, ADR-0084, ADR-0101).

One world, copied fresh for every request: ada owns project "shop" (in her personal workspace) and shared it with ed
(editor) and vic (viewer); zed has signed in and owns a project of his own ("zeds"), but nothing is shared with him;
out is an outsider. Project "shop" holds diagram "orders" (section "backend", element "svc"). A caller who may not
see a resource gets 404, one whose role is too low 403, anyone else something that is neither (validation errors,
conflicts and the like are fine: the other tests check the behaviour). `test_every_route_is_classified` keeps the
table complete.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange
from arcviz.model import fingerprint
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectExchangeService, ProjectService, ServiceRoot
from arcviz.store.file import FileStore
from arcviz.templates import demo_diagram
from arcviz.workspaces import Identity
from tests.conftest import ScriptedProvider, Users

PEOPLE = ("ada", "ed", "vic", "zed", "out")
LADDER = {"out": 0, "zed": 0, "vic": 1, "ed": 2, "ada": 3}  # nobody < viewer < editor < project owner
NEED_RANK = {"viewer": 1, "editor": 2, "owner": 3}
ROLES = ("out", "zed", "vic", "ed", "ada")


def person(name: str) -> Identity:
    return Identity(name, name.capitalize(), f"{name}@example.org")


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> tuple[Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("matrix") / "data"
    store = FileStore(root)
    services = ServiceRoot(store)
    for name in PEOPLE:
        services.users.seen(person(name))
    ada = services.for_actor(person("ada")).projects
    ada.create("Shop", "shop")
    ada.share("shop", "ed", "editor")
    ada.share("shop", "vic", "viewer")
    zed = services.for_actor(person("zed"))
    zed.projects.create("Zeds", "zeds")  # zed has data of his own, nothing shared
    zed.diagrams.create("Zeds Diagramm", "zd", demo_diagram("zd", "Zeds Diagramm"), "zeds")
    projects = ProjectService(store, store)
    diagrams = DiagramService(store, projects)
    projects.apply("shop", "add_section", {"title": "Backend", "id": "backend"})
    diagrams.create("Orders", "orders", demo_diagram("orders", "Orders"), "shop", "backend")
    models = ModelService(projects, diagrams)
    models.add_element("shop", "Service", "component", element_id="svc")
    models.add_element("shop", "Store", "data", element_id="data")
    models.add_relation("shop", "communicates", "svc", "data", relation_id="rel")
    services.for_actor(person("ada")).requirements.apply("shop", "add_requirement", {"raw": "Eine erste Anforderung"})
    projects.apply("shop", "add_driver", {"title": "Last"})  # d1
    for op, args in (("add_stakeholder", {"name": "Betrieb"}), ("add_scenario", {"title": "Ausfall"})):
        projects.apply("shop", op, args)  # s1, q1
    projects.apply("shop", "add_decision", {"title": "Zweite Region"})  # adr1
    nodes = [n for n in diagrams.get("orders").nodes.values() if n.kind == "service"]
    models.link("orders", nodes[0].id, "svc")
    agent = services.for_actor(person("ed")).proposals  # two stored proposals of an agent: p1, p2
    for _ in range(2):
        agent.create("shop", "orders", [{"op": "rename_diagram", "arguments": {"title": "Neu"}}], "Test")
    diagram = diagrams.get("orders")
    facts = {
        "linked": nodes[0].id,
        "free": nodes[1].id,
        "fingerprint": fingerprint(diagram),
        "document": exchange.export_document(diagram),
        "bundle": ProjectExchangeService(diagrams, projects).export("shop"),
    }
    return root, facts


def endpoints(f: dict[str, Any]) -> list[tuple[str, str, dict[str, Any] | None, str]]:
    """(method, path, JSON body, need): need is auth, viewer, editor or owner (of the project)."""
    proposal = {"operations": [], "base": f["fingerprint"], "result": f["fingerprint"]}
    return [
        # anyone authenticated; lists are filtered
        ("GET", "/api/config", None, "auth"),
        ("GET", "/api/me", None, "auth"),
        ("GET", "/api/users?q=ad", None, "auth"),
        ("GET", "/api/workspace", None, "auth"),
        ("GET", "/api/projects", None, "auth"),
        ("GET", "/api/diagrams", None, "auth"),
        # anyone: their own tokens (spec: agent-authentication.md); an unknown id is a 400
        ("GET", "/api/tokens", None, "auth"),
        ("POST", "/api/tokens", {"label": "Laptop"}, "auth"),
        ("DELETE", "/api/tokens/zz", None, "auth"),
        # anyone: into the caller's own workspace
        ("POST", "/api/projects", {"title": "Zwei"}, "auth"),
        ("POST", "/api/projects/import", {"bundle": f["bundle"]}, "auth"),
        # project viewers
        ("GET", "/api/projects/shop", None, "viewer"),
        ("GET", "/api/projects/shop/export", None, "viewer"),
        ("GET", "/api/projects/shop/diagrams", None, "viewer"),
        ("GET", "/api/projects/shop/model", None, "viewer"),
        ("GET", "/api/projects/shop/elements", None, "viewer"),
        ("GET", "/api/projects/shop/elements/svc/code-refs", None, "viewer"),
        ("GET", "/api/projects/shop/code-owners?path=src", None, "viewer"),
        ("GET", "/api/projects/shop/proposals", None, "viewer"),
        ("GET", "/api/projects/shop/proposals/p1", None, "viewer"),
        ("GET", "/api/projects/shop/drivers", None, "viewer"),
        ("GET", "/api/projects/shop/requirements", None, "viewer"),
        ("GET", "/api/projects/shop/requirements/req1", None, "viewer"),
        ("GET", "/api/projects/shop/traceability", None, "viewer"),
        ("GET", "/api/projects/shop/requirements/chat", None, "viewer"),
        ("GET", "/api/projects/shop/stakeholders", None, "viewer"),
        ("GET", "/api/projects/shop/scenarios", None, "viewer"),
        ("GET", "/api/projects/shop/decisions", None, "viewer"),
        ("GET", "/api/projects/shop/decisions/adr1/markdown", None, "viewer"),
        ("POST", "/api/projects/shop/scenarios/q1/analysis", None, "viewer"),
        ("POST", "/api/projects/shop/scenarios/qualities", {"title": "x"}, "viewer"),
        ("POST", "/api/projects/shop/decisions/testability", {"title": "x"}, "viewer"),
        ("GET", "/api/projects/shop/adoption", None, "viewer"),
        ("GET", "/api/projects/shop/checks", None, "viewer"),
        ("GET", "/api/projects/shop/steward", None, "viewer"),
        ("GET", "/project/shop/arc42", None, "viewer"),
        ("GET", "/project/shop/arc42/05", None, "viewer"),
        ("GET", "/api/projects/shop/arc42", None, "viewer"),
        ("GET", "/api/projects/shop/arc42.md", None, "viewer"),
        ("GET", "/api/projects/shop/arc42.zip", None, "viewer"),
        ("GET", "/api/projects/shop/arc42.docx", None, "viewer"),
        ("GET", "/api/projects/shop/arc42/figures/orders.svg", None, "viewer"),
        ("POST", "/api/projects/shop/review", None, "viewer"),
        ("GET", "/api/projects/shop/chat", None, "viewer"),
        ("GET", "/api/projects/shop/access", None, "viewer"),
        ("GET", "/api/diagrams/orders", None, "viewer"),
        ("GET", "/api/diagrams/orders/mermaid", None, "viewer"),
        ("GET", "/api/diagrams/orders/render", None, "viewer"),
        ("GET", "/api/diagrams/orders/export", None, "viewer"),
        ("GET", "/api/diagrams/orders/checks", None, "viewer"),
        ("POST", "/api/diagrams/orders/review", None, "viewer"),
        ("GET", "/api/diagrams/orders/versions", None, "viewer"),
        ("GET", "/api/diagrams/orders/versions/1", None, "viewer"),
        ("GET", "/api/diagrams/orders/compare?base=1", None, "viewer"),
        # live updates and presence (spec: collaboration.md); streams end after ARCVIZ_LIVE_MAX_SECONDS here
        ("GET", "/api/diagrams/orders/live", None, "viewer"),
        ("GET", "/api/projects/shop/live", None, "viewer"),
        ("POST", "/api/diagrams/orders/presence", {"session": "matrix-session", "selection": None}, "viewer"),
        # project editors
        ("PATCH", "/api/projects/shop", {"title": "Shop 2"}, "editor"),
        ("POST", "/api/projects/shop/steward/run", None, "editor"),
        ("POST", "/api/projects/shop/ops", {"op": "add_section", "args": {"title": "Neu"}}, "editor"),
        ("PUT", "/api/projects/shop/vision", {"statement": "Schnell und sicher"}, "editor"),
        ("POST", "/api/projects/shop/sections", {"title": "Frontend"}, "editor"),
        ("PATCH", "/api/projects/shop/sections/backend", {"title": "Backend 2"}, "editor"),
        ("DELETE", "/api/projects/shop/sections/backend", None, "editor"),
        ("POST", "/api/projects/shop/elements", {"name": "Neu"}, "editor"),
        ("PATCH", "/api/projects/shop/elements/svc", {"description": "x"}, "editor"),
        ("DELETE", "/api/projects/shop/elements/svc", None, "editor"),
        ("POST", "/api/projects/shop/elements/svc/code-refs", {"path": "src/svc"}, "editor"),
        ("DELETE", "/api/projects/shop/elements/svc/code-refs?path=src/svc", None, "editor"),
        ("POST", "/api/projects/shop/proposals/p1/apply", None, "editor"),
        ("POST", "/api/projects/shop/proposals/p2/discard", None, "editor"),
        ("POST", "/api/projects/shop/model/sync", None, "editor"),
        ("POST", "/api/projects/shop/adoption", None, "editor"),
        ("POST", "/api/projects/shop/relations", {"kind": "data_flow", "source": "svc", "target": "data"}, "editor"),
        ("PATCH", "/api/projects/shop/relations/rel", {"description": "x"}, "editor"),
        ("DELETE", "/api/projects/shop/relations/rel", None, "editor"),
        ("POST", "/api/projects/shop/drivers", {"title": "Kosten"}, "editor"),
        ("POST", "/api/projects/shop/requirements", {"raw": "Eine weitere Anforderung"}, "editor"),
        ("PATCH", "/api/projects/shop/requirements/req1", {"theme": "Daten"}, "editor"),
        ("DELETE", "/api/projects/shop/requirements/req1", None, "editor"),
        ("POST", "/api/projects/shop/requirements/accept", {"items": [{"id": "req1", "expected": "x"}]}, "editor"),
        ("POST", "/api/projects/shop/requirements/import", {"text": "Eine Zeile"}, "editor"),
        ("POST", "/api/projects/shop/requirements/refine", {}, "editor"),
        ("POST", "/api/projects/shop/requirements/derive", {}, "editor"),
        ("POST", "/api/projects/shop/requirements/chat", {"message": "Hallo"}, "editor"),
        ("DELETE", "/api/projects/shop/requirements/chat", None, "editor"),
        ("POST", "/api/projects/shop/requirements/status", {"ids": ["req1"], "status": "rejected"}, "editor"),
        ("POST", "/api/projects/shop/requirements/delete", {"ids": ["req1"]}, "editor"),
        ("POST", "/api/projects/shop/requirements/req1/reject-suggestion", None, "editor"),
        ("PATCH", "/api/projects/shop/requirements/req1/issues/0", {"resolved": True}, "editor"),
        ("PATCH", "/api/projects/shop/drivers/d1", {"risk": "high"}, "editor"),
        ("DELETE", "/api/projects/shop/drivers/d1", None, "editor"),
        ("POST", "/api/projects/shop/stakeholders", {"name": "Neu"}, "editor"),
        ("PATCH", "/api/projects/shop/stakeholders/s1", {"role": "Ops"}, "editor"),
        ("DELETE", "/api/projects/shop/stakeholders/s1", None, "editor"),
        ("POST", "/api/projects/shop/scenarios", {"title": "Neu"}, "editor"),
        ("PATCH", "/api/projects/shop/scenarios/q1", {"kind": "growth"}, "editor"),
        ("DELETE", "/api/projects/shop/scenarios/q1", None, "editor"),
        ("POST", "/api/projects/shop/decisions", {"title": "Neu"}, "editor"),
        ("PATCH", "/api/projects/shop/decisions/adr1", {"context": "x"}, "editor"),
        ("DELETE", "/api/projects/shop/decisions/adr1", None, "editor"),
        ("POST", "/api/projects/shop/chat", {"message": "Hallo"}, "editor"),
        ("DELETE", "/api/projects/shop/chat", None, "editor"),
        ("POST", "/api/projects/shop/chat/compact", None, "editor"),
        ("POST", "/api/projects/shop/proposal/apply", {"diagram": "orders", **proposal}, "editor"),
        ("POST", "/api/projects/shop/proposal/discard", {"diagram": "orders"}, "editor"),
        ("POST", "/api/projects/shop/diagrams/orders/move", {"position": 0}, "editor"),
        ("POST", "/api/diagrams", {"title": "Neu", "template": "empty", "project": "shop"}, "editor"),
        ("POST", "/api/diagrams/import", {"document": f["document"], "project": "shop"}, "editor"),
        ("POST", "/api/diagrams/orders/ops", {"op": "rename_diagram", "args": {"title": "O"}}, "editor"),
        ("POST", "/api/diagrams/orders/versions", {"label": "Stand"}, "editor"),
        ("POST", "/api/diagrams/orders/versions/1/restore", None, "editor"),
        ("POST", "/api/diagrams/orders/undo", None, "editor"),
        ("POST", "/api/diagrams/orders/redo", None, "editor"),
        ("POST", "/api/diagrams/orders/chat", {"message": "Hallo"}, "editor"),
        ("DELETE", "/api/diagrams/orders/chat", None, "editor"),
        ("POST", "/api/diagrams/orders/chat/compact", None, "editor"),
        ("POST", "/api/diagrams/orders/proposal/apply", proposal, "editor"),
        ("POST", "/api/diagrams/orders/proposal/discard", None, "editor"),
        ("POST", "/api/diagrams/orders/link", {"view": f["free"], "element": "svc"}, "editor"),
        ("POST", "/api/diagrams/orders/unlink", {"view": f["linked"]}, "editor"),
        ("POST", "/api/diagrams/orders/promote", {"view": f["free"]}, "editor"),
        ("POST", "/api/diagrams/orders/link-relation", {"view": "e1", "relation": "rel"}, "editor"),
        ("POST", "/api/diagrams/orders/unlink-relation", {"view": "e1"}, "editor"),
        ("POST", "/api/diagrams/orders/hidden-relations", {"relation": "rel", "hidden": True}, "editor"),
        # project owners
        ("PUT", "/api/projects/shop/steward", {"enabled": True}, "owner"),
        ("DELETE", "/api/projects/shop", None, "owner"),
        ("POST", "/api/projects/shop/access", {"user": "zed", "role": "viewer"}, "owner"),
        ("DELETE", "/api/projects/shop/access/vic", None, "owner"),
        ("DELETE", "/api/diagrams/orders", None, "owner"),
    ]


PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/login"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("GET", "/"),
    ("GET", "/openapi.json"),
    ("GET", "/api/docs"),
    ("GET", "/api/redoc"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/docs"),
    ("GET", "/docs/{path:path}"),
}


def expected(need: str, role: str) -> str:
    """ "ok", "403" or "404"."""
    rank = LADDER[role]
    if need == "auth":
        return "ok"
    if rank == 0:
        return "404"
    return "ok" if rank >= NEED_RANK[need] else "403"


@pytest.fixture(scope="module")
def app_client(template, tmp_path_factory):
    """One app in proxy mode over a data directory that every case resets to the template (fast: no app per case)."""
    data = tmp_path_factory.mktemp("matrix-app") / "data"
    shutil.copytree(template[0], data)
    with pytest.MonkeyPatch.context() as env:
        env.setenv("ARCVIZ_AUTH", "proxy")
        env.setenv("ARCVIZ_AUTH_PROXY_PRESET", "oauth2-proxy")
        env.setenv("ARCVIZ_ADMIN_USER", "admin")
        env.setenv("ARCVIZ_LIVE_MAX_SECONDS", "0.2")
        env.setenv("ARCVIZ_LIVE_POLL_SECONDS", "0.1")
        app = create_app(store=FileStore(data), provider=ScriptedProvider([]))
    return data, Users(TestClient(app, follow_redirects=False))


def _client(template, app_client) -> Users:
    data, users = app_client
    shutil.rmtree(data)
    shutil.copytree(template[0], data)
    return users


def _cases():
    # the table needs the template's facts; the ids are fixed, so build a stand-in to name the cases
    stand_in = {"linked": "a", "free": "b", "fingerprint": "", "document": {}, "bundle": {}}
    return [(i, role) for i, _ in enumerate(endpoints(stand_in)) for role in ROLES]


@pytest.mark.parametrize(("index", "role"), _cases(), ids=lambda v: str(v))
def test_endpoint_by_role(template, app_client, index, role):
    method, path, body, need = endpoints(template[1])[index]
    users = _client(template, app_client)
    response = users(role, method, path, json=body)
    want = expected(need, role)
    got = response.status_code
    where = f"{method} {path} as {role} (needs {need}): {got} {response.text[:200]}"
    if want == "ok":
        assert got not in (401, 403, 404), where
        assert got < 500, where
    else:
        assert got == int(want), where


def test_every_route_is_classified(template, app_client):
    users = _client(template, app_client)
    table = {(m, p.split("?")[0]) for m, p, _, _ in endpoints(template[1])}
    routes: set[tuple[str, str]] = set()
    for route in users.client.app.routes:
        for method in sorted(getattr(route, "methods", None) or []):
            if method != "HEAD":
                routes.add((method, route.path))
    concrete = {
        "project_id": "shop",
        "diagram_id": "orders",
        "section_id": "backend",
        "section": "05",
        "element_id": "svc",
        "relation_id": "rel",
        "driver_id": "d1",
        "decision_id": "adr1",
        "version": "1",
        "token_id": "zz",
        "requirement_id": "req1",
        "index": "0",
    }

    def fill(path: str) -> str:
        for plural, item in (("stakeholders", "s1"), ("scenarios", "q1"), ("decisions", "adr1")):
            path = path.replace(f"/{plural}/{{item_id}}", f"/{plural}/{item}")
        path = path.replace("{scenario_id}", "q1")
        path = path.replace("/proposals/{proposal_id}/discard", "/proposals/p2/discard").replace("{proposal_id}", "p1")
        for name, value in concrete.items():
            path = path.replace("{" + name + "}", value)
        return path.replace("{user_id:path}", "vic")

    covered = set(table)
    missing = sorted(
        (m, p) for m, p in routes if (m, p) not in PUBLIC and (m, fill(p)) not in covered and p != "/static"
    )
    assert not missing, f"routes without an entry in the access matrix: {missing}"


def test_proxy_requests_without_an_identity_are_refused(template, app_client):
    users = _client(template, app_client)
    for method, path, body, _ in endpoints(template[1]):
        response = users.client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path}"


def test_nobody_reaches_another_users_project(template, app_client):
    """Tenant isolation: ada, who owns everything in "shop", gets 404 for every route of zed's project and diagram."""
    users = _client(template, app_client)
    checked = 0
    for method, path, body, _ in endpoints(template[1]):
        theirs = path.replace("/api/projects/shop", "/api/projects/zeds").replace(
            "/api/diagrams/orders", "/api/diagrams/zd"
        )
        payload = {**body, "project": "zeds"} if isinstance(body, dict) and body.get("project") == "shop" else body
        if theirs == path and payload == body:
            continue  # not about one project or diagram (lists, creates in the own workspace)
        response = users("ada", method, theirs, json=payload)
        assert response.status_code == 404, f"{method} {theirs}: {response.status_code} {response.text[:200]}"
        checked += 1
    assert checked >= 50  # every project and diagram route
