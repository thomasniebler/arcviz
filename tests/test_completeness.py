"""Completeness declarations and hidden relations per diagram (spec: model-relations.md, ADR-0137).

Open world by default: a diagram says nothing about what it leaves out unless it declares itself complete for some
relation kinds (`Diagram.complete`, `set_completeness`); a relation it intentionally leaves out is marked hidden in
that diagram (`Diagram.hidden_relations`, `set_relation_hidden`, internal).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import agent, exchange, operations
from arcviz.model import Diagram, Edge, Node, fingerprint, kinds_for
from arcviz.operations import OperationError
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from arcviz.templates import create_from_template


def platform() -> Diagram:
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {
        "web": Node(id="web", name="Web", kind="service", ref="web"),
        "orders": Node(id="orders", name="Orders", kind="service", ref="orders"),
    }
    d.edges = [Edge(id="e1", source="web", target="orders")]
    return d


def test_unset_declarations_are_omitted_so_fingerprints_stay():
    d = platform()
    before = fingerprint(d)
    dumped = d.model_dump()
    assert "complete" not in dumped and "hidden_relations" not in dumped
    operations.apply(d, "set_completeness", {"kinds": ["communicates"]})
    assert d.model_dump()["complete"] == ["communicates"]
    assert fingerprint(d) != before
    operations.apply(d, "set_completeness", {"kinds": []})
    assert "complete" not in d.model_dump() and fingerprint(d) == before


def test_set_completeness_expands_all_and_checks_the_kinds():
    d = platform()
    result = operations.apply(d, "set_completeness", {"kinds": ["all"]})
    assert result["result"]["complete"] == ["communicates", "data_flow", "deployed_on"]
    operations.apply(d, "set_completeness", {"kinds": ["deployed_on", "communicates", "communicates"]})
    assert d.complete == ["communicates", "deployed_on"]
    with pytest.raises(OperationError, match="contains"):
        operations.apply(d, "set_completeness", {"kinds": ["contains"]})
    s = create_from_template("checkout", "checkout", "Checkout")
    operations.apply(s, "set_completeness", {"kinds": ["all"]})
    assert s.complete == ["communicates"]
    with pytest.raises(OperationError, match="deployed_on"):
        operations.apply(s, "set_completeness", {"kinds": ["deployed_on"]})
    a = create_from_template("order-process", "order", "Bestellprozess")
    with pytest.raises(OperationError, match="not available"):
        operations.apply(a, "set_completeness", {"kinds": ["all"]})


def test_hidden_markers():
    d = platform()
    operations.apply(d, "set_relation_hidden", {"relation": "r2", "hidden": True})
    operations.apply(d, "set_relation_hidden", {"relation": "r1", "hidden": True})
    operations.apply(d, "set_relation_hidden", {"relation": "r1", "hidden": True})
    assert d.hidden_relations == ["r1", "r2"]
    operations.apply(d, "set_relation_hidden", {"relation": "r2", "hidden": False})
    assert d.hidden_relations == ["r1"]
    with pytest.raises(OperationError, match="Invalid relation id"):
        operations.apply(d, "set_relation_hidden", {"relation": "Not A Slug", "hidden": True})
    operations.apply(d, "clear_relation", {"relation": "r1"})
    assert d.hidden_relations == [] and "hidden_relations" not in d.model_dump()
    assert "set_relation_hidden" in operations.INTERNAL_OPERATIONS
    assert "set_completeness" not in operations.INTERNAL_OPERATIONS


def test_the_agent_has_a_completeness_tool_for_types_that_show_relations():
    for diagram_type, offered in (("deployment", True), ("sequence", True), ("activity", False)):
        names = {t.name for t in agent.build_tools(kinds_for(diagram_type), diagram_type)}
        assert ("set_completeness" in names) is offered


def test_documents_carry_and_check_the_declarations():
    d = platform()
    operations.apply(d, "set_completeness", {"kinds": ["communicates"]})
    operations.apply(d, "set_relation_hidden", {"relation": "r1", "hidden": True})
    document = exchange.export_document(d)
    parsed = exchange.parse_document(document)
    assert parsed.complete == ["communicates"] and parsed.hidden_relations == ["r1"]
    s = create_from_template("checkout", "checkout", "Checkout")
    document = exchange.export_document(s)
    document["diagram"]["complete"] = ["deployed_on"]
    document["diagram"]["hidden_relations"] = ["Bad Id"]
    with pytest.raises(exchange.ExchangeError) as info:
        exchange.parse_document(document)
    text = "\n".join(info.value.errors)
    assert "deployed_on" in text and "Bad Id" in text


# ---- the service and REST ------------------------------------------------------------------------------------


@pytest.fixture
def world(store):
    projects = ProjectService(store, store)
    diagrams = DiagramService(store, projects)
    models = ModelService(projects, diagrams)
    projects.create("Shop", "shop")
    diagrams.create("Plattform", "platform", platform(), "shop")
    models.add_element("shop", "Web", "component", element_id="web")
    models.add_element("shop", "Orders", "component", element_id="orders")
    models.add_relation("shop", "communicates", "web", "orders")
    return projects, diagrams, models


def test_service_hides_known_relations_only(world):
    _, diagrams, models = world
    models.hide_relation("platform", "r1", True)
    assert diagrams.get("platform").hidden_relations == ["r1"]
    entry = models.model("shop").to_dict()["diagrams"][0]
    assert entry == {"id": "platform", "title": "Plattform", "type": "deployment", "complete": [], "hidden": ["r1"]}
    with pytest.raises(Exception, match="Unknown relation 'r9'"):
        models.hide_relation("platform", "r9", True)
    models.hide_relation("platform", "r1", False)
    assert diagrams.get("platform").hidden_relations == []


def test_removing_a_relation_clears_its_hidden_markers(world):
    _, diagrams, models = world
    models.hide_relation("platform", "r1", True)
    removal = models.remove_relation("shop", "r1")
    assert removal.unlinked == ["platform"] and diagrams.get("platform").hidden_relations == []


def test_imports_drop_hidden_markers_moves_carry_them_and_both_keep_completeness(world):
    projects, diagrams, models = world
    models.hide_relation("platform", "r1", True)
    diagrams.apply_operation("platform", "set_completeness", {"kinds": ["all"]}, "vollständig")
    copy = diagrams.get(diagrams.import_new(diagrams.get("platform"), "Kopie", project="shop"))
    assert copy.hidden_relations == [] and copy.complete == ["communicates", "data_flow", "deployed_on"]
    projects.create("Other", "other")
    diagrams.move("platform", "other")
    moved = diagrams.get("platform")
    assert moved.hidden_relations == ["r1"] and moved.complete != []  # r1 came along with its ends (#153)
    assert [r.id for r in projects.get("other").relations] == ["r1"]


def test_rest(world, store):
    client = TestClient(create_app(store=store))
    r = client.post("/api/diagrams/platform/hidden-relations", json={"relation": "r1", "hidden": True})
    assert r.status_code == 200, r.text
    assert r.json()["diagram"]["hidden_relations"] == ["r1"]
    r = client.post("/api/diagrams/platform/ops", json={"op": "set_completeness", "args": {"kinds": ["all"]}})
    assert r.status_code == 200 and r.json()["diagram"]["complete"] == ["communicates", "data_flow", "deployed_on"]
    r = client.post(
        "/api/diagrams/platform/ops", json={"op": "set_relation_hidden", "args": {"relation": "r1", "hidden": False}}
    )
    assert r.status_code == 400 and "hidden-relations" in r.json()["detail"]
    config = client.get("/api/config").json()["diagram_types"]
    assert config["sequence"]["completeness"] == ["communicates"] and config["activity"]["completeness"] == []
    elements = client.get("/api/projects/shop/elements").json()
    assert [r["id"] for r in elements["relations"]] == ["r1"]


def test_imported_declarations_are_canonical():
    """Codex review on #210: an imported document's lists are stored sorted and unique, like the operations keep them."""
    d = platform()
    document = exchange.export_document(d)
    document["diagram"]["complete"] = ["data_flow", "communicates", "communicates"]
    document["diagram"]["hidden_relations"] = ["r2", "r1", "r2"]
    parsed = exchange.parse_document(document)
    assert parsed.complete == ["communicates", "data_flow"] and parsed.hidden_relations == ["r1", "r2"]
    same = exchange.export_document(d)
    same["diagram"]["complete"] = ["communicates", "data_flow"]
    same["diagram"]["hidden_relations"] = ["r1", "r2"]
    assert fingerprint(exchange.parse_document(same)) == fingerprint(parsed)
