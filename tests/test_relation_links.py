"""Deployment edges and sequence messages link to model relations (spec: model-relations.md, ADR-0139).

Diagram side: `Edge.ref`, `Message.ref`, the operations `link_relation`, `unlink_relation`, `sync_relation`,
`clear_relation`, links cleared when a connection's ends change; service side: `ModelService` linking, propagation of
relation changes, removal, `sync`, the model answer and the REST routes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import operations, project_model
from arcviz.model import Diagram, Edge, Node, fingerprint
from arcviz.operations import OperationError
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from arcviz.templates import create_from_template


def platform() -> Diagram:
    """web -> orders (connection), orders -> db (connection), orders deploys onto k8s; all four linked."""
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {
        "k8s": Node(id="k8s", name="Cluster", kind="cluster", ref="cluster"),
        "web": Node(id="web", name="Web", kind="service", ref="web"),
        "orders": Node(id="orders", name="Orders", kind="service", ref="orders"),
        "db": Node(id="db", name="DB", kind="database", ref="db"),
        "free": Node(id="free", name="Frei", kind="service"),
    }
    d.edges = [
        Edge(id="e1", source="web", target="orders", kind="connection", properties={"protocol": "HTTPS"}),
        Edge(id="e2", source="orders", target="db", kind="connection"),
        Edge(id="e3", source="orders", target="k8s", kind="deploys"),
        Edge(id="e4", source="web", target="free", kind="connection"),
        Edge(id="e5", source="orders", target="web", kind="dependency"),
    ]
    return d


def checkout() -> Diagram:
    d = create_from_template("checkout", "checkout", "Checkout")
    lifelines = d.interaction.lifelines
    lifelines[0].ref, lifelines[1].ref = "web", "orders"
    return d


def link(d: Diagram, view: str, relation: str, kind: str, source: str, target: str) -> dict:
    args = {"id": view, "relation": relation, "kind": kind, "source": source, "target": target}
    return operations.apply(d, "link_relation", args)


# ---- the reference fields ----------------------------------------------------------------------------------


def test_unset_refs_are_omitted_so_fingerprints_stay():
    d = platform()
    before = fingerprint(d)
    assert all("ref" not in e for e in d.model_dump()["edges"])
    s = checkout()
    assert '"ref"' not in s.model_dump_json().replace('"ref":"web"', "").replace('"ref":"orders"', "")
    link(d, "e1", "r1", "communicates", "web", "orders")
    assert d.model_dump()["edges"][0]["ref"] == "r1"
    assert fingerprint(d) != before
    assert Diagram.model_validate(d.model_dump(mode="json")).edges[0].ref == "r1"


def test_which_connections_may_show_which_relations():
    kinds = project_model.relation_kinds_for
    assert kinds("deployment", "connection") == ("communicates", "data_flow")
    assert kinds("deployment", "dependency") == ("communicates",)
    assert kinds("deployment", "deploys") == ("deployed_on",)
    assert kinds("sequence", project_model.MESSAGE) == ("communicates",)
    assert kinds("activity", "object_flow") == ()
    assert project_model.shown_relation_kinds("deployment") == ("communicates", "deployed_on", "data_flow")
    assert project_model.shown_relation_kinds("sequence") == ("communicates",)
    assert project_model.shown_relation_kinds("activity") == ()


# ---- link_relation ---------------------------------------------------------------------------------------------


def test_link_an_edge_whose_ends_stand_for_the_relations_ends():
    d = platform()
    result = link(d, "e1", "r1", "communicates", "web", "orders")
    assert result["result"]["touched"] == ["e1"] and d.edges[0].ref == "r1"
    link(d, "e5", "r1", "communicates", "web", "orders")  # communicates: either orientation
    link(d, "e3", "r2", "deployed_on", "orders", "cluster")
    assert d.edges[2].ref == "r2"


@pytest.mark.parametrize(
    ("view", "kind", "source", "target", "message"),
    [
        ("e3", "communicates", "orders", "cluster", "cannot show"),  # a deploys edge shows deployed_on only
        ("e1", "deployed_on", "web", "orders", "cannot show"),
        ("e1", "communicates", "web", "db", "stand for"),  # ends do not match
        ("e4", "communicates", "web", "orders", "stand for"),  # an unlinked end
        ("e2", "data_flow", "db", "orders", "stand for"),  # data_flow keeps its orientation
        ("e9", "communicates", "web", "orders", "Unknown edge"),
    ],
)
def test_link_refuses_connections_that_do_not_fit(view, kind, source, target, message):
    d = platform()
    before = d.model_dump()
    with pytest.raises(OperationError, match=message):
        link(d, view, "r1", kind, source, target)
    assert d.model_dump() == before


def test_data_flow_in_its_orientation():
    d = platform()
    link(d, "e2", "r3", "data_flow", "orders", "db")
    assert d.edges[1].ref == "r3"


def test_link_a_message():
    d = checkout()
    message = next(m for m in operations.sequence_ops.messages(d.interaction) if m.sender and m.receiver)
    sender = next(lf for lf in d.interaction.lifelines if lf.id == message.sender)
    receiver = next(lf for lf in d.interaction.lifelines if lf.id == message.receiver)
    sender.ref, receiver.ref = "web", "orders"
    link(d, message.id, "r1", "communicates", "orders", "web")
    assert message.ref == "r1"
    dumped = [f for f in d.model_dump()["interaction"]["fragments"] if f.get("id") == message.id]
    assert dumped == [] or dumped[0]["ref"] == "r1"
    with pytest.raises(OperationError, match="cannot show"):
        link(d, message.id, "r2", "deployed_on", "web", "orders")


def test_activity_diagrams_have_no_relation_operations():
    d = create_from_template("order-process", "order", "Bestellprozess")
    with pytest.raises(OperationError, match="not available"):
        link(d, "x", "r1", "data_flow", "a", "b")


def test_unlink_relation():
    d = platform()
    link(d, "e1", "r1", "communicates", "web", "orders")
    result = operations.apply(d, "unlink_relation", {"id": "e1"})
    assert result["result"]["relation"] == "r1" and d.edges[0].ref is None
    with pytest.raises(OperationError, match="does not show"):
        operations.apply(d, "unlink_relation", {"id": "e1"})


def test_the_internal_operations_are_refused_in_batches():
    d = platform()
    for op in ("link_relation", "sync_relation", "clear_relation"):
        assert op in operations.INTERNAL_OPERATIONS
        with pytest.raises(OperationError, match="cannot be part of a batch"):
            operations.apply(d, "apply_batch", {"operations": [{"op": op, "args": {}}]})
    assert "unlink_relation" not in operations.INTERNAL_OPERATIONS


# ---- links follow their ends ------------------------------------------------------------------------------


def test_unlinking_or_relinking_a_node_clears_the_relation_links_touching_it():
    d = platform()
    link(d, "e1", "r1", "communicates", "web", "orders")
    link(d, "e2", "r3", "data_flow", "orders", "db")
    result = operations.apply(d, "unlink_element", {"id": "web"})
    assert result["result"]["unlinked_relations"] == ["e1"]
    assert d.edges[0].ref is None and d.edges[1].ref == "r3"
    args = {"id": "orders", "element": "other", "name": "Other", "element_kind": "component"}
    operations.apply(d, "link_element", args)
    assert d.edges[1].ref is None
    # relinking to the same element keeps the links
    link(d, "e3", "r2", "deployed_on", "other", "cluster")
    operations.apply(d, "link_element", args)
    assert d.edges[2].ref == "r2"
    operations.apply(d, "clear_element", {"element": "cluster"})
    assert d.edges[2].ref is None


def test_changing_a_linked_messages_ends_clears_its_link():
    d = checkout()
    d.interaction.executions = []
    lifelines = d.interaction.lifelines
    message = next(m for m in operations.sequence_ops.messages(d.interaction) if m.sender and m.receiver)
    for lf in lifelines:
        lf.ref = {message.sender: "web", message.receiver: "orders"}.get(lf.id, lf.ref)
    link(d, message.id, "r1", "communicates", "web", "orders")
    other = next(lf.id for lf in lifelines if lf.id not in (message.sender, message.receiver))
    result = operations.apply(d, "update_message", {"id": message.id, "receiver": other})
    assert message.ref is None and result["result"]["unlinked_relation"] == "r1"


def test_a_linked_edge_may_not_change_to_a_kind_that_cannot_show_its_relation():
    d = platform()
    link(d, "e1", "r1", "communicates", "web", "orders")
    with pytest.raises(OperationError, match="unlink_relation"):
        operations.apply(d, "update_edge", {"id": "e1", "kind": "deploys"})
    operations.apply(d, "update_edge", {"id": "e1", "label": "REST"})
    link(d, "e5", "r1", "communicates", "web", "orders")
    operations.apply(d, "update_edge", {"id": "e5", "kind": "connection"})  # still shows communicates
    assert d.edges[4].ref == "r1"


def test_sync_relation_clears_links_that_no_longer_fit():
    d = platform()
    link(d, "e1", "r1", "communicates", "web", "orders")
    link(d, "e5", "r1", "communicates", "web", "orders")
    unchanged = operations.apply(
        d, "sync_relation", {"relation": "r1", "kind": "communicates", "source": "orders", "target": "web"}
    )
    assert unchanged["result"]["touched"] == []
    result = operations.apply(
        d, "sync_relation", {"relation": "r1", "kind": "communicates", "source": "web", "target": "db"}
    )
    assert result["result"]["touched"] == ["e1", "e5"] and d.edges[0].ref is None


def test_clear_relation():
    d = platform()
    link(d, "e1", "r1", "communicates", "web", "orders")
    assert operations.apply(d, "clear_relation", {"relation": "r1"})["result"]["touched"] == ["e1"]
    assert d.edges[0].ref is None


# ---- the service ------------------------------------------------------------------------------------------


@pytest.fixture
def world(store):
    projects = ProjectService(store, store)
    diagrams = DiagramService(store, projects)
    models = ModelService(projects, diagrams)
    projects.create("Shop", "shop")
    diagrams.create("Plattform", "platform", platform(), "shop")
    for name, kind, element_id in (
        ("Web", "component", "web"),
        ("Orders", "component", "orders"),
        ("DB", "data", "db"),
        ("Cluster", "node", "cluster"),
    ):
        models.add_element("shop", name, kind, element_id=element_id)
    models.add_relation("shop", "communicates", "web", "orders", {"protocol": "HTTPS"})
    models.add_relation("shop", "deployed_on", "orders", "cluster")
    return projects, diagrams, models


def test_service_link_and_unlink(world):
    _, diagrams, models = world
    updated = models.link_relation("platform", "e1", "r1")
    assert updated.diagram.edges[0].ref == "r1"
    assert diagrams.versions("platform")[-1].label.startswith("Mit Beziehung")
    model = models.model("shop").to_dict()
    r1 = model["relations"][0]
    assert [(u["diagram"], u["view"]) for u in r1["usages"]] == [("platform", "e1")]
    assert [(u["diagram"], u["view"]) for u in r1["shown"]] == [("platform", "e5")]  # unlinked, could be linked
    models.unlink_relation("platform", "e1")
    assert diagrams.get("platform").edges[0].ref is None
    with pytest.raises(Exception, match="Unknown relation 'r9'"):
        models.link_relation("platform", "e1", "r9")


def test_changing_a_relations_ends_unlinks_views_that_no_longer_fit(world):
    _, diagrams, models = world
    models.link_relation("platform", "e1", "r1")
    change = models.update_relation("shop", "r1", properties={"port": "443"})
    assert change.unlinked == [] and diagrams.get("platform").edges[0].ref == "r1"  # properties reach no view
    change = models.update_relation("shop", "r1", target="db")
    assert change.unlinked == ["platform"]
    assert diagrams.get("platform").edges[0].ref is None


def test_removing_a_relation_or_an_element_clears_the_links_first(world):
    _, diagrams, models = world
    models.link_relation("platform", "e1", "r1")
    models.link_relation("platform", "e3", "r2")
    removal = models.remove_relation("shop", "r1")
    assert removal.unlinked == ["platform"] and diagrams.get("platform").edges[0].ref is None
    removal = models.remove_element("shop", "cluster")
    assert diagrams.get("platform").edges[2].ref is None
    assert models.relations("shop") == []


def test_sync_clears_links_that_drifted(world):
    projects, diagrams, models = world
    models.link_relation("platform", "e1", "r1")
    # a relation changed behind its links' back (a crash between the writes), and a link to a relation that is gone
    projects.apply("shop", "update_relation", {"relation": "r1", "target": "db"}, internal=True)
    args = {"id": "e2", "relation": "ghost", "kind": "communicates", "source": "orders", "target": "db"}
    diagrams.apply_operation("platform", "link_relation", args, "x")
    report = models.sync("shop")
    assert report.changed == ["platform"]
    edges = diagrams.get("platform").edges
    assert edges[0].ref is None and edges[1].ref is None
    assert models.sync("shop").changed == []  # idempotent


def test_a_diagram_moved_to_another_project_keeps_its_relation_links(world):
    """Since #153 (ADR-0161) the relation comes along with its ends; tests/test_carry_model.py covers the rest."""
    projects, diagrams, models = world
    models.link_relation("platform", "e1", "r1")
    projects.create("Other", "other")
    diagrams.move("platform", "other")
    assert [e.ref for e in diagrams.get("platform").edges][0] == "r1"
    assert ("r1", "web", "orders") in [(r.id, r.source, r.target) for r in projects.get("other").relations]


def test_a_single_import_drops_relation_links(world):
    _, diagrams, models = world
    models.link_relation("platform", "e1", "r1")
    imported = diagrams.import_new(diagrams.get("platform"), "Kopie", project="shop")
    assert all(e.ref is None for e in diagrams.get(imported).edges)


# ---- REST ----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(world, store):
    return TestClient(create_app(store=store))


def test_rest_link_and_unlink(client):
    r = client.post("/api/diagrams/platform/link-relation", json={"view": "e1", "relation": "r1"})
    assert r.status_code == 200, r.text
    assert r.json()["diagram"]["edges"][0]["ref"] == "r1"
    assert client.post("/api/diagrams/platform/link-relation", json={"view": "e4", "relation": "r1"}).status_code == 400
    r = client.post("/api/diagrams/platform/unlink-relation", json={"view": "e1"})
    assert r.status_code == 200 and "ref" not in r.json()["diagram"]["edges"][0]
    r = client.post("/api/diagrams/platform/ops", json={"op": "link_relation", "args": {}})
    assert r.status_code == 400 and "link-relation" in r.json()["detail"]


def test_config_lists_which_connections_show_which_relations(client):
    types = client.get("/api/config").json()["diagram_types"]
    assert types["deployment"]["relations"]["deploys"] == ["deployed_on"]
    assert types["sequence"]["relations"] == {"message": ["communicates"]}
    assert types["activity"]["relations"] == {}


def test_diagram_documents_carry_relation_links():
    from arcviz import exchange

    d = platform()
    link(d, "e1", "r1", "communicates", "web", "orders")
    document = exchange.export_document(d)
    assert document["diagram"]["edges"][0]["ref"] == "r1"
    assert exchange.parse_document(document).edges[0].ref == "r1"
