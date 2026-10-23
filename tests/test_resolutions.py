"""Resolutions of correspondence findings (spec: model-relations.md, *Resolution flow*; ADR-0138).

Every correspondence finding names what a person can do; the review applies none of it. The REST tests apply a
resolution the way the project review panel does (the request of `relations.resolutionRequest`) and check that the
finding is gone afterwards.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import projects
from arcviz.model import Diagram, Edge, Node
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from tests.test_correspondence import checks, link_relation, relate
from tests.test_project_review import link, model_snapshot, op, rule


def actions(finding) -> list[str]:
    return [r.action for r in finding.resolutions]


def test_property_conflict_offers_every_value_and_a_proposal_per_view():
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "communicates", "web", "api", protocol="HTTPS")
    edge = dep.edges[0].id
    op(dep, "update_edge", id=edge, properties={"protocol": "gRPC"})
    link_relation(dep, edge, r, "communicates", "web", "api")
    (finding,) = rule(checks(snap), "relation-property-conflict")
    models = [x for x in finding.resolutions if x.action == "update_relation"]
    assert sorted(x.args["properties"]["protocol"] for x in models) == ["gRPC"]  # the model's own value needs no change
    (proposal,) = [x for x in finding.resolutions if x.action == "propose"]
    assert proposal.diagram == "dep" and edge in proposal.prompt and "HTTPS" in proposal.prompt
    assert finding.edges == [edge] and finding.elements == []  # deployment edges are highlighted as edges


def test_gap_resolutions():
    snap, dep, seq, _ = model_snapshot()
    r = relate(snap, "data_flow", "api", "db")
    (not_shown,) = rule(checks(snap), "relation-not-shown")
    assert actions(not_shown) == ["propose", "remove_relation"] and not_shown.resolutions[0].diagram == "dep"
    op(dep, "set_completeness", kinds=["all"])
    (missing,) = rule(checks(snap), "complete-view-missing-relation")
    assert actions(missing) == ["propose", "hide", "remove_relation"]
    assert missing.resolutions[1].relation == r and missing.resolutions[1].diagram == "dep"
    relate(snap, "communicates", "web", "api")
    by_diagram = {f.diagram: f for f in rule(checks(snap), "connection-without-relation")}
    link_fix = by_diagram["dep"].resolutions[0]
    assert link_fix.action == "link_relation" and link_fix.view == dep.edges[0].id and link_fix.relation == "r2"
    op(dep, "add_node", name="Kunde", kind="external", id="kunde")
    link(dep, "kunde", "kunde", "Kunde", "actor")
    op(dep, "add_edge", source="kunde", target="web")
    created = [f for f in rule(checks(snap), "connection-without-relation") if "Kunde" in f.message]
    (create,) = [x for x in created[0].resolutions if x.action == "create_relation"]
    assert create.args == {"kind": "communicates", "source": "kunde", "target": "web"}


def test_contradiction_and_drift_resolutions():
    snap, dep, *_ = model_snapshot()
    for name in ("Host A", "Host B"):
        projects.apply(snap.project, "add_element", {"name": name, "kind": "node"})
    r = relate(snap, "deployed_on", "api", "host-a")
    op(dep, "add_node", name="Host B", kind="node", id="host-b")
    link(dep, "host-b", "host-b", "Host B", "node")
    op(dep, "move_node", id="api", parent="host-b")
    (several,) = rule(checks(snap), "deployed-on-several-nodes")
    assert [(x.action, x.relation or x.diagram) for x in several.resolutions] == [
        ("remove_relation", r),
        ("propose", "dep"),
    ]
    projects.apply(snap.project, "add_element", {"name": "Shop", "kind": "system"})
    projects.apply(snap.project, "add_element", {"name": "Backoffice", "kind": "subsystem"})
    projects.apply(snap.project, "move_element", {"element": "web", "parent": "shop"})
    op(dep, "add_node", name="Backoffice", kind="group", id="bo")
    link(dep, "bo", "backoffice", "Backoffice", "subsystem")
    op(dep, "move_node", id="web", parent="bo")
    (containment,) = rule(checks(snap), "containment-conflict")
    assert containment.resolutions[0].action == "move_element"
    assert containment.resolutions[0].model_element == "web" and containment.resolutions[0].args == {
        "parent": "backoffice"
    }
    assert containment.resolutions[1].action == "propose"
    r2 = relate(snap, "communicates", "web", "api")
    link_relation(dep, dep.edges[0].id, r2, "communicates", "web", "api")
    projects.apply(snap.project, "remove_relation", {"relation": r2})
    (drift,) = rule(checks(snap), "invalid-relation-link")
    assert actions(drift) == ["sync"]


def test_m18_rules_have_no_resolutions_and_the_review_applies_nothing():
    snap, *_ = model_snapshot()
    before = snap.project.model_dump()
    findings = checks(snap)
    assert all(f.resolutions == [] for f in findings if f.rule in ("unused-element", "message-without-connection"))
    assert snap.project.model_dump() == before


# ---- applied through REST, as the panel does ---------------------------------------------------------------


def request(resolution: dict, project: str) -> tuple[str, str, dict | None]:
    """The request the review panel sends for a resolution (mirrors `relations.resolutionRequest`)."""
    action = resolution["action"]
    if action == "hide":
        return (
            "POST",
            f"/api/diagrams/{resolution['diagram']}/hidden-relations",
            {"relation": resolution["relation"], "hidden": True},
        )
    if action == "link_relation":
        return (
            "POST",
            f"/api/diagrams/{resolution['diagram']}/link-relation",
            {"view": resolution["view"], "relation": resolution["relation"]},
        )
    if action == "update_relation":
        return "PATCH", f"/api/projects/{project}/relations/{resolution['relation']}", resolution["args"]
    if action == "remove_relation":
        return "DELETE", f"/api/projects/{project}/relations/{resolution['relation']}", None
    if action == "create_relation":
        return "POST", f"/api/projects/{project}/relations", resolution["args"]
    if action == "move_element":
        return "PATCH", f"/api/projects/{project}/elements/{resolution['model_element']}", resolution["args"]
    assert action == "sync"
    return "POST", f"/api/projects/{project}/model/sync", None


@pytest.fixture
def client(store):
    project_service = ProjectService(store, store)
    diagrams = DiagramService(store, project_service)
    models = ModelService(project_service, diagrams)
    project_service.create("Shop", "shop")
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {
        "web": Node(id="web", name="Web", kind="service"),
        "api": Node(id="api", name="API", kind="service"),
        "db": Node(id="db", name="DB", kind="database"),
    }
    d.edges = [Edge(id="e1", source="web", target="api", properties={"protocol": "gRPC"})]
    diagrams.create("Plattform", "platform", d, "shop")
    for name, kind, element_id in (("Web", "component", "web"), ("API", "component", "api"), ("DB", "data", "db")):
        models.add_element("shop", name, kind, element_id=element_id)
        models.link("platform", element_id, element_id)
    models.add_relation("shop", "communicates", "web", "api", {"protocol": "HTTPS"})
    models.link_relation("platform", "e1", "r1")
    models.add_relation("shop", "data_flow", "api", "db")
    return TestClient(create_app(store=store))


def findings(client, name: str) -> list[dict]:
    return [f for f in client.get("/api/projects/shop/checks").json()["findings"] if f["rule"] == name]


def apply(client, resolution: dict) -> None:
    method, path, body = request(resolution, "shop")
    r = client.request(method, path, json=body)
    assert r.status_code in (200, 201), r.text


def test_a_property_conflict_resolved_in_the_model(client):
    (finding,) = findings(client, "relation-property-conflict")
    assert finding["relation"] == "r1" and finding["edges"] == ["e1"]
    fix = next(x for x in finding["resolutions"] if x["action"] == "update_relation")
    apply(client, fix)
    assert findings(client, "relation-property-conflict") == []
    assert client.get("/api/projects/shop/model").json()["relations"][0]["properties"] == {"protocol": "gRPC"}


def test_a_complete_diagram_resolved_by_hiding(client):
    client.post("/api/diagrams/platform/ops", json={"op": "set_completeness", "args": {"kinds": ["all"]}})
    (finding,) = findings(client, "complete-view-missing-relation")
    apply(client, next(x for x in finding["resolutions"] if x["action"] == "hide"))
    assert findings(client, "complete-view-missing-relation") == []


def test_a_connection_resolved_by_linking_and_a_relation_by_removing(client):
    client.post("/api/diagrams/platform/unlink-relation", json={"view": "e1"})
    (finding,) = findings(client, "connection-without-relation")
    apply(client, finding["resolutions"][0])
    assert findings(client, "connection-without-relation") == []
    (gap,) = findings(client, "relation-not-shown")
    apply(client, next(x for x in gap["resolutions"] if x["action"] == "remove_relation"))
    assert findings(client, "relation-not-shown") == []
