"""Model first: diagrams are views of the project model (spec: docs/architecture/specs/model-first.md, ADR-0213..0220)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import views
from arcviz.agent_tools import AgentTools
from arcviz.model import Diagram, Edge, Node
from arcviz.operations import OperationError, apply
from arcviz.projects import Project
from arcviz.server import create_app
from arcviz.workspaces import Identity


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as client:
        yield client


def new(client: TestClient, title: str, type: str = "deployment") -> str:
    response = client.post("/api/diagrams", json={"title": title, "type": type})
    assert response.status_code == 201, response.text
    return response.json()["diagram"]["id"]


def op(client: TestClient, diagram: str, operation: str, **args: Any) -> dict[str, Any]:
    response = client.post(f"/api/diagrams/{diagram}/ops", json={"op": operation, "args": args})
    assert response.status_code == 200, response.text
    return response.json()


def get(client: TestClient, diagram: str) -> Diagram:
    return Diagram.model_validate(client.get(f"/api/diagrams/{diagram}").json()["diagram"])


def model(client: TestClient, project: str = "default") -> dict[str, Any]:
    project_id = client.get("/api/projects").json()[0]["id"] if project == "default" else project
    return client.get(f"/api/projects/{project_id}").json()["project"]


# ---- through the app -----------------------------------------------------------------------------------------------


def test_a_node_is_a_model_element_and_the_view_stores_only_ids(client, store):
    a = new(client, "A")
    op(client, a, "add_node", name="Orders API", kind="service")
    elements = {e["id"]: e for e in model(client)["elements"]}
    assert elements["orders-api"]["name"] == "Orders API"
    assert elements["orders-api"]["notation"] == {"deployment": "service"}
    stored, _ = store.backend.read_latest(a)
    assert stored.shows == ["orders-api"] and stored.nodes == {} and stored.edges == []


def test_a_rename_in_one_view_shows_in_every_view(client):
    a, b = new(client, "A"), new(client, "B")
    op(client, a, "add_node", name="Orders API", kind="service")
    op(client, b, "add_node", name="Orders API", kind="service")  # the same element (reuse by name)
    op(client, b, "update_node", id="orders-api", name="Order Service")
    assert [n.name for n in get(client, a).nodes.values()] == ["Order Service"]
    assert len(model(client)["elements"]) == 1


def test_a_connection_appears_in_every_view_that_shows_both_ends(client):
    a, b = new(client, "A"), new(client, "B")
    op(client, a, "add_node", name="API", kind="service")
    op(client, a, "add_node", name="DB", kind="database")
    op(client, a, "add_edge", source="API", target="DB", label="SQL")
    op(client, b, "show_element", element="API")
    answer = op(client, b, "show_element", element="db")
    assert [(e["source"], e["target"], e["label"]) for e in answer["diagram"]["edges"]] == [("api", "db", "SQL")]


def test_removing_in_a_view_hides_by_default(client):
    a, b = new(client, "A"), new(client, "B")
    for d in (a, b):
        op(client, d, "add_node", name="API", kind="service")
        op(client, d, "add_node", name="DB", kind="database")
    op(client, a, "add_edge", source="API", target="DB")
    op(client, b, "remove_edge", source="API", target="DB")
    assert get(client, b).edges == []
    assert len(get(client, a).edges) == 1
    op(client, b, "remove_node", id="db")
    assert list(get(client, b).nodes) == ["api"]
    assert {e["id"] for e in model(client)["elements"]} == {"api", "db"}
    assert list(get(client, a).nodes) == ["api", "db"]


def test_removing_with_scope_model_deletes_everywhere(client):
    a, b = new(client, "A"), new(client, "B")
    for d in (a, b):
        op(client, d, "add_node", name="API", kind="service")
        op(client, d, "add_node", name="DB", kind="database")
    op(client, a, "add_edge", source="API", target="DB")
    op(client, b, "remove_node", id="db", scope="model")
    assert {e["id"] for e in model(client)["elements"]} == {"api"}
    assert list(get(client, a).nodes) == ["api"] and get(client, a).edges == []


def test_a_hidden_connection_comes_back_when_unhidden_by_drawing_it_again(client):
    a = new(client, "A")
    op(client, a, "add_node", name="API", kind="service")
    op(client, a, "add_node", name="DB", kind="database")
    op(client, a, "add_edge", source="API", target="DB", label="SQL")
    op(client, a, "remove_edge", source="API", target="DB")
    assert get(client, a).edges == []
    op(client, a, "add_edge", source="API", target="DB", label="SQL")
    assert len(get(client, a).edges) == 1
    assert len(model(client).get("connections", [])) == 1


def test_a_container_hidden_in_a_view_keeps_its_parts_in_the_model(client):
    a = new(client, "A")
    op(client, a, "add_node", name="K8s", kind="cluster")
    op(client, a, "add_node", name="API", kind="service", parent="k8s")
    op(client, a, "remove_node", id="k8s")
    assert get(client, a).nodes["api"].parent is None
    elements = {e["id"]: e for e in model(client)["elements"]}
    assert elements["api"]["parent"] == "k8s"


def test_lifelines_are_elements_named_by_the_model(client):
    d, s = new(client, "Deploy"), new(client, "Flow", "sequence")
    op(client, d, "add_node", name="Orders API", kind="service")
    op(client, s, "add_lifeline", name="Orders API")
    op(client, s, "add_lifeline", name="Client", actor=True)
    lifelines = get(client, s).interaction.lifelines
    assert [(lf.name, lf.ref) for lf in lifelines] == [("Orders API", "orders-api"), ("Client", "client")]
    op(client, d, "update_node", id="orders-api", name="Order Service")
    assert get(client, s).interaction.lifelines[0].name == "Order Service"


def test_a_model_change_changes_the_revision_of_every_view_showing_it(client):
    a, b = new(client, "A"), new(client, "B")
    op(client, a, "add_node", name="API", kind="service")
    op(client, b, "add_node", name="API", kind="service")
    before = client.get(f"/api/diagrams/{b}").headers["etag"]
    op(client, a, "update_node", id="api", name="Gateway")
    assert client.get(f"/api/diagrams/{b}").headers["etag"] != before


# ---- pure ------------------------------------------------------------------------------------------------------


def _project() -> Project:
    return Project(id="p", title="P")


def test_resolve_passes_a_diagram_outside_a_project_on():
    diagram = Diagram(id="d", title="D", nodes={"a": Node(id="a", name="A")})
    assert views.resolve(diagram, None) is diagram


def test_decompose_converts_a_full_document_without_showing_more():
    project = _project()
    first = Diagram(
        id="a",
        title="A",
        nodes={"x": Node(id="x", name="X", kind="service"), "y": Node(id="y", name="Y", kind="service")},
        edges=[Edge(id="e1", source="x", target="y")],
    )
    views.decompose(first, None, project)
    legacy = Diagram(
        id="b",
        title="B",
        nodes={"x": Node(id="x", name="X", kind="service"), "y": Node(id="y", name="Y", kind="service")},
    )
    result = views.decompose(legacy, legacy, project)
    assert result.stored.shows == ["x", "y"]
    assert views.resolve(result.stored, project).edges == []  # it did not draw e1 before: hidden there
    assert result.stored.hidden_connections == ["e1"]


def test_a_new_node_reuses_an_element_of_the_same_name():
    project = _project()
    views.decompose(
        Diagram(id="a", title="A", nodes={"api": Node(id="api", name="API", kind="service")}), None, project
    )
    view = views.resolve(Diagram(id="b", title="B", shows=[]), project)
    node = apply(view, "add_node", {"name": "api", "kind": "service"})["result"]
    assert (node["id"], node["name"]) == ("api", "API")


def test_show_element_refuses_an_unknown_element():
    view = views.resolve(Diagram(id="b", title="B", shows=[]), _project())
    with pytest.raises(OperationError, match="no element"):
        apply(view, "show_element", {"element": "nothing"})


def test_scope_must_be_view_or_model():
    diagram = Diagram(id="d", title="D", nodes={"a": Node(id="a", name="A")})
    with pytest.raises(OperationError, match="scope"):
        apply(diagram, "remove_node", {"id": "a", "scope": "everywhere"})


# ---- agents on the MCP path (ADR-0219) ------------------------------------------------------------------------------

ADA = Identity(id="ada", name="Ada")


@pytest.fixture
def ada(store):
    from arcviz.services import ServiceRoot
    from arcviz.store import create_store

    root = ServiceRoot(create_store(backend=store))
    root.users.seen(ADA)
    services = root.for_actor(ADA)
    services.projects.create("Shop", "shop")
    services.diagrams.create("Orders", "orders", Diagram(id="orders", title="Orders"), "shop")
    return services


def test_agents_propose_model_changes_by_default(ada):
    tools = AgentTools(ada, "write")
    answer = tools.create_component("shop", "Billing", "component")
    assert answer["mode"] == "propose"
    assert ada.models.elements("shop") == []


def test_agents_write_the_model_when_the_owner_allows_it(ada):
    ada.projects.set_agent_writes("shop", "direct")
    tools = AgentTools(ada, "write")
    assert tools.create_component("shop", "Billing", "component")["mode"] == "direct"
    diagram_id = ada.projects.get("shop").diagrams[0].diagram
    tools.show_in_diagram("shop", diagram_id, "billing")
    assert list(ada.diagrams.get(diagram_id).nodes) == ["billing"]
    tools.hide_in_diagram("shop", diagram_id, "billing")
    assert ada.diagrams.get(diagram_id).nodes == {}
    assert [e.id for e in ada.models.elements("shop")] == ["billing"]  # hiding never deletes


def test_only_known_modes(ada):
    from arcviz.projects import ProjectError

    with pytest.raises(ProjectError):
        ada.projects.set_agent_writes("shop", "sometimes")


# ---- the migration (ADR-0220) ---------------------------------------------------------------------------------------


def test_the_migration_converts_full_documents_and_merges_by_name(store):
    from arcviz.services import ServiceRoot

    raw = ServiceRoot(store.backend)  # written the way v0.6 did: full documents
    services = raw.for_actor(ADA)
    raw.users.seen(ADA)
    services.projects.create("Shop", "shop")
    for title in ("One", "Two"):
        services.diagrams.create(
            title,
            title.lower(),
            Diagram(
                id=title.lower(),
                title=title,
                nodes={
                    "api": Node(id="api", name="API", kind="service"),
                    "db": Node(id="db", name="DB", kind="database"),
                },
                edges=[Edge(id="e1", source="api", target="db", label="SQL")] if title == "One" else [],
            ),
            "shop",
        )
    root = ServiceRoot(store)
    report = root.migrate_model()
    assert len(report.converted) == 2 and not report.failed
    assert root.migrate_model().converted == []  # idempotent
    project = root.for_actor(ADA).projects.get("shop")
    assert sorted(e.name for e in project.elements) == ["API", "DB"]
    diagrams = {
        d.title: root.for_actor(ADA).diagrams.get(d.id)
        for d in [store.backend.read_latest(m.diagram)[0] for m in project.diagrams]
    }
    assert len(diagrams["One"].edges) == 1 and diagrams["Two"].edges == []  # Two never drew it: hidden there


def test_a_container_hidden_and_shown_again_holds_its_parts_again(client):
    a = new(client, "A")
    op(client, a, "add_node", name="K8s", kind="cluster")
    op(client, a, "add_node", name="API", kind="service", parent="k8s")
    op(client, a, "remove_node", id="k8s")  # hidden here: the part sits at the top while its container is not shown
    op(client, a, "show_element", element="k8s")
    assert get(client, a).nodes["api"].parent == "k8s"


def test_a_delete_with_the_current_etag_is_accepted(client):
    a = new(client, "A")
    op(client, a, "add_node", name="API", kind="service")
    etag = client.get(f"/api/diagrams/{a}").headers["etag"]
    assert client.delete(f"/api/diagrams/{a}", headers={"If-Match": etag}).status_code == 204
