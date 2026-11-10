"""Structural views become project elements when they are created (spec: docs/architecture/specs/elements-by-default.md,
ADR-0164). Every model reply is scripted (ScriptedProvider)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import operations, proposal
from arcviz.agent import DiagramAgent
from arcviz.model import fingerprint
from arcviz.project_agent import ProjectAgent
from arcviz.project_model import Element, adoptable, adoption_target, loose_key
from arcviz.project_review import ProjectSnapshot, run_project_checks
from arcviz.projects import new_project
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from tests.conftest import ScriptedProvider, call
from tests.test_concurrency import done, tool_round

# ---- which views, which element ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("diagram_type", "kind", "expected"),
    [
        ("deployment", "service", True),
        ("deployment", "database", True),
        ("deployment", "external", True),
        ("deployment", "node", True),
        ("deployment", "cluster", True),
        ("deployment", "vnet", False),
        ("deployment", "subnet", False),
        ("deployment", "region", False),
        ("deployment", "group", False),
        ("deployment", "deployment", False),
        ("sequence", "lifeline", True),
        ("activity", "activity_partition", True),
        ("activity", "data_store_node", True),
        ("activity", "opaque_action", False),
        ("activity", "decision_node", False),
    ],
)
def test_adoptable_views(diagram_type, kind, expected):
    assert adoptable(diagram_type, kind) is expected


def test_matching_prefers_the_exact_name_then_a_unique_loose_one():
    elements = [Element(id="a", name="Order Service"), Element(id="b", name="order-service-x")]
    assert adoption_target(elements, "order service").id == "a"
    assert adoption_target(elements, "OrderService").id == "a"
    assert adoption_target(elements, "Billing") is None
    twins = [Element(id="a", name="Auth-Service"), Element(id="b", name="Auth Service!")]
    assert adoption_target(twins, "AuthService") is None  # ambiguous: a new element
    assert loose_key("Auth-Service 2") == "authservice2"


# ---- REST -------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    c = TestClient(create_app(store=store))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    for title, diagram_id in (("Plattform", "platform"), ("Betrieb", "ops")):
        r = c.post("/api/diagrams", json={"title": title, "id": diagram_id, "template": "empty", "project": "shop"})
        assert r.status_code == 201
    return c


def add_node(client, diagram_id: str, **args: Any) -> dict[str, Any]:
    r = client.post(f"/api/diagrams/{diagram_id}/ops", json={"op": "add_node", "args": args})
    assert r.status_code == 200, r.text
    return r.json()


def elements(client) -> list[dict[str, Any]]:
    return client.get("/api/projects/shop/elements").json()["elements"]


def test_a_node_added_through_ops_becomes_a_project_element_in_the_same_version(client):
    answer = add_node(client, "platform", name="Order Service", kind="service")
    # model first (ADR-0213): the repository makes the node an element; no adoption report any more
    assert "adoption" not in answer
    assert answer["diagram"]["nodes"]["order-service"]["ref"] == "order-service"
    assert [e["id"] for e in elements(client)] == ["order-service"]
    versions = client.get("/api/diagrams/platform/versions").json()
    assert len(versions) == 2  # the initial state and one version holding node and link


def test_another_diagram_reuses_the_element_of_the_same_name(client):
    add_node(client, "platform", name="Order Service", kind="service")
    # model first: add_node reuses the element of the same name (case-insensitive), the node is that element
    answer = add_node(client, "ops", name="order service", kind="service")
    assert list(answer["diagram"]["nodes"]) == ["order-service"]
    assert answer["diagram"]["nodes"]["order-service"]["name"] == "Order Service"  # the view takes the element's name
    assert len(elements(client)) == 1


def test_grouping_containers_are_elements_too(client):
    # model first (ADR-0213): every node of a structure view is an element, grouping containers included
    answer = add_node(client, "platform", name="prod", kind="vnet")
    assert "adoption" not in answer and answer["diagram"]["nodes"]["prod"]["ref"] == "prod"
    assert [(e["id"], e["notation"]) for e in elements(client)] == [("prod", {"deployment": "vnet"})]


def test_a_node_merges_with_the_element_of_its_name_whatever_its_kind(client):
    # model first (spec deviation 5): names are unique per project, so the node is the existing element
    client.post("/api/projects/shop/elements", json={"name": "Ledger", "kind": "actor"})
    answer = add_node(client, "platform", name="Ledger", kind="database")
    assert answer["diagram"]["nodes"]["ledger"]["ref"] == "ledger"
    assert [e["id"] for e in elements(client)] == ["ledger"]


def test_a_failing_operation_creates_no_element(client):
    r = client.post("/api/diagrams/platform/ops", json={"op": "add_node", "args": {"name": "X", "kind": "robot"}})
    assert r.status_code == 400 and elements(client) == []


def test_a_batch_with_two_nodes_of_one_name_is_refused(client):
    steps = [
        {"op": "add_node", "args": {"name": "Cache", "kind": "service", "id": "cache-a"}},
        {"op": "add_node", "args": {"name": "Cache", "kind": "service", "id": "cache-b"}},
    ]
    before = [e["name"] for e in elements(client)]
    r = client.post("/api/diagrams/platform/ops", json={"op": "apply_batch", "args": {"operations": steps}})
    # model first: every node is an element, element names are unique per project, and an element is shown once per
    # diagram, so the second node of that name is refused and nothing is written
    assert r.status_code == 400 and "shown once" in r.json()["detail"]
    assert [e["name"] for e in elements(client)] == before


# ---- existing diagrams -------------------------------------------------------------------------------------------


def test_a_diagram_from_a_template_shows_elements_at_once(client):
    # model first (ADR-0213): a template's nodes become elements when the diagram is created, nothing is left to adopt
    r = client.post("/api/diagrams", json={"title": "AKS", "id": "aks", "template": "aks-database", "project": "shop"})
    assert r.status_code == 201
    nodes = client.get("/api/diagrams/aks").json()["diagram"]["nodes"]
    assert nodes and set(nodes) <= {e["id"] for e in elements(client)}
    assert client.get("/api/projects/shop/adoption").json()["diagrams"] == []
    assert [v["label"] for v in client.get("/api/diagrams/aks/versions").json()] == ["Initial state"]


# ---- the assistants -----------------------------------------------------------------------------------------------


@pytest.fixture
def services(store, demo):
    projects = ProjectService(store, store)
    projects.create("Shop", "shop")
    projects.place("demo", "shop")
    diagrams = DiagramService(store, projects)
    return diagrams, projects, ModelService(projects, diagrams)


def results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(e["content"]) for e in events if e["type"] == "tool_result"]


def test_the_diagram_chat_makes_nodes_elements_and_tells_the_model(services):
    diagrams, projects, models = services
    provider = ScriptedProvider(
        [
            tool_round(call("add_node", "c1", name="Payment", kind="service", parent="prod-deployment")),
            done("Neu im Projektmodell: Payment"),
        ]
    )
    events = list(DiagramAgent(provider, diagrams, background=False, models=models).chat("demo", "Füge Payment hinzu"))
    (result,) = results(events)
    assert "adoption" not in result  # model first: the repository makes the node an element, no adoption report
    assert diagrams.get("demo").nodes["payment"].ref == "payment"
    assert set(diagrams.get("demo").nodes) == {e.id for e in projects.get("shop").elements}  # every node is one
    assert len([v for v in diagrams.versions("demo")]) == 2  # one version for the turn
    assert "Reuse before you create" in provider.requests[1]["system"]


def test_preview_mode_adopts_nothing(services):
    diagrams, projects, models = services
    provider = ScriptedProvider(
        [tool_round(call("add_node", "c1", name="Payment", kind="service", parent="prod-deployment")), done()]
    )
    agent = DiagramAgent(provider, diagrams, background=False, models=models)
    events = list(agent.chat("demo", "Füge Payment hinzu", propose=True))
    assert "adoption" not in results(events)[0]
    assert projects.get("shop").elements == []
    final = events[-1]
    assert final["proposal"] is not None


def test_a_confirmed_proposal_adopts(services):
    diagrams, projects, models = services
    stored = diagrams.get("demo")
    preview = stored.model_copy(deep=True)
    ops = [{"op": "add_node", "arguments": {"name": "Payment", "kind": "service", "parent": "prod-deployment"}}]
    operations.apply(preview, "add_node", ops[0]["arguments"])
    diagram, _, _ = proposal.apply_proposal(
        diagrams, "demo", ops, fingerprint(stored), fingerprint(preview), models=models
    )
    assert diagram.nodes["payment"].ref == "payment"
    # model first: every node of the demo diagram is an element now, the new one included
    assert set(diagram.nodes) == {e.id for e in projects.get("shop").elements}


def test_the_project_chat_adopts_lifelines_and_reuses_elements(services):
    diagrams, projects, models = services
    models.add_element("shop", "Kasse", "component")
    provider = ScriptedProvider(
        [
            tool_round(call("create_diagram", "c1", title="Bezahlen", type="sequence")),
            tool_round(call("add_lifeline", "c2", name="kasse"), call("add_lifeline", "c3", name="Bank")),
            done(),
        ]
    )
    agent = ProjectAgent(provider, diagrams, projects, models, background=False)
    events = list(agent.chat("shop", "Lege einen Bezahlablauf an", active="demo"))
    # model first: lifelines always stand for elements (no adoption report); "kasse" reuses Kasse by name
    assert not [r for r in results(events) if "adoption" in r]
    assert {e.id for e in projects.get("shop").elements} == {"kasse", "bank"}
    lifelines = diagrams.get("bezahlen").interaction.lifelines
    assert [(lf.ref, lf.name) for lf in lifelines] == [("kasse", "Kasse"), ("bank", "Bank")]


# ---- near-duplicate names ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "similar"),
    [
        ("Order Service", "OrderService", True),
        ("Order Service", "Order Svc", True),
        ("Order DB", "Order Datenbank", True),
        ("Paymnet Gateway", "Payment Gateway", True),
        ("Order Service", "Order DB", False),
        ("service-a", "service-b", False),
        ("Node 1", "Node 2", False),
        ("Auth", "Auth Service", False),
    ],
)
def test_similar_element_names(a, b, similar):
    project = new_project("Shop", "shop")
    project.elements = [Element(id="a", name=a), Element(id="b", name=b)]
    findings = [f for f in run_project_checks(ProjectSnapshot(project, [])) if f.rule == "similar-element-names"]
    assert bool(findings) is similar
    if similar:
        assert f"„{a}“" in findings[0].message and findings[0].severity == "low"
