"""Starter templates (spec docs/architecture/specs/starter-templates.md)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange, mermaid, render, sequence
from arcviz.model import kinds_for
from arcviz.server import create_app
from arcviz.templates import TEMPLATES

INDEX_HTML = Path(__file__).resolve().parents[1] / "arcviz" / "static" / "index.html"


def test_new_templates_are_registered():
    assert set(TEMPLATES) == {
        "empty",
        "demo",
        "hub-spoke",
        "aks-database",
        "event-driven",
        "order-process",
        "order-lifecycle",
        "webshop-domain",
        "webshop-use-cases",
        "webshop-components",
        "checkout",
    }


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_every_template_is_valid_and_renders(template):
    d = TEMPLATES[template]("t", "Title")
    assert d.id == "t" and d.title == "Title"
    assert d.integrity_errors() == []
    assert {n.kind for n in d.nodes.values()} <= kinds_for(d.type).all_kinds
    assert {e.kind for e in d.edges} <= kinds_for(d.type).edge_kinds
    assert exchange._rule_errors(d) == []
    rendering = render.payload(d)
    if rendering["renderer"] == "mermaid":
        assert mermaid.render(d).removeprefix(mermaid.SWIMLANE_FRONT_MATTER).startswith(f"flowchart {d.direction}")
    if template != "empty" and d.interaction is not None:
        assert len(sequence.element_ids(d.interaction)) >= 15
    elif template != "empty":
        assert len(d.nodes) >= 8 and len(d.edges) >= 5


def test_hub_spoke_content():
    d = TEMPLATES["hub-spoke"]("hs", "Hub and spoke")
    assert d.ancestors("vpn-gateway") == ["gateway-subnet", "hub-vnet", "westeurope"]
    assert d.nodes["hub-vnet"].properties["cidr"] == "10.0.0.0/16"
    peerings = [e for e in d.edges if e.label == "peering"]
    assert {(e.source, e.target) for e in peerings} == {("hub-vnet", "spoke-app-vnet"), ("hub-vnet", "spoke-data-vnet")}
    assert all(e.direction == "both" for e in peerings)
    sql = next(e for e in d.edges if e.target == "sql-db")
    assert sql.properties == {"protocol": "tds", "port": "1433"}


def test_aks_database_content():
    d = TEMPLATES["aks-database"]("aks", "AKS")
    assert d.ancestors("orders-api") == ["user-pool", "aks-cluster", "aks-subnet", "aks-vnet", "prod"]
    db = next(e for e in d.edges if e.source == "orders-api" and e.target == "postgres")
    assert db.properties == {"protocol": "postgres", "port": "5432"}
    assert any(e.kind == "deploys" and e.source == "acr" for e in d.edges)


def test_event_driven_content():
    d = TEMPLATES["event-driven"]("ev", "Events")
    subscribers = {e.target for e in d.edges if e.source == "order-events" and e.label == "subscribe"}
    assert subscribers == {"billing-handler", "shipping-handler", "notification-service"}
    assert any(e.source == "order-service" and e.target == "order-events" and e.label == "publish" for e in d.edges)


def test_every_template_is_selectable_in_the_ui():
    # The new-diagram dialog lists the templates of the chosen type with the labels from navigator.js.
    source = (INDEX_HTML.parent / "navigator.js").read_text(encoding="utf-8")
    block = source[source.index("const TEMPLATE_LABELS") : source.index("};", source.index("const TEMPLATE_LABELS"))]
    labelled = set(re.findall(r'^\s*"?([\w-]+)"?:', block, re.M))
    assert set(TEMPLATES) <= labelled


def test_every_template_can_be_created_through_the_api(store):
    client = TestClient(create_app(store=store))
    assert sorted(client.get("/api/config").json()["templates"]) == sorted(TEMPLATES)
    for template in TEMPLATES:
        r = client.post("/api/diagrams", json={"title": f"From {template}", "template": template})
        assert r.status_code == 201, r.text
        assert r.json()["diagram"]["title"] == f"From {template}"
