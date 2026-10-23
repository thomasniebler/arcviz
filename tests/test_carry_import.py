"""A single diagram document carries the model it refers to, and its import brings it along (#153, ADR-0161).

Export: an optional `model` key with the referenced elements, their ancestors and the relations between referenced
elements (no key for a diagram without links, so such exports are unchanged). Import: the model is validated like a
bundle's, then the planner of the move maps, copies or drops; a document without `model` drops its links (ADR-0079).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import exchange
from arcviz.model import Diagram, Edge, Node
from arcviz.server import create_app


@pytest.fixture
def client(store):
    client = TestClient(create_app(store=store))
    for title, project_id in (("Shop", "shop"), ("Andere", "other")):
        assert client.post("/api/projects", json={"title": title, "id": project_id}).status_code == 201
    diagram = Diagram(id="platform", title="Plattform")
    diagram.nodes = {
        "web": Node(id="web", name="Web", kind="service"),
        "orders": Node(id="orders", name="Orders", kind="service"),
        "db": Node(id="db", name="DB", kind="database"),
    }
    diagram.edges = [
        Edge(id="e1", source="web", target="orders", kind="connection"),
        Edge(id="e2", source="orders", target="db", kind="connection"),
    ]
    r = client.post("/api/diagrams/import", json={"document": exchange.export_document(diagram), "project": "shop"})
    assert r.status_code == 201
    client.diagram = r.json()["diagram"]["id"]
    for name, kind, element_id, parent in (
        ("Shop", "system", "shop", None),
        ("Web", "component", "web", None),
        ("Orders", "component", "orders", "shop"),
        ("DB", "data", "db", None),
        ("Fremd", "component", "fremd", None),
    ):
        body = {"name": name, "kind": kind, "id": element_id, "parent": parent}
        assert client.post("/api/projects/shop/elements", json=body).status_code == 201
    for view in ("web", "orders", "db"):
        r = client.post(f"/api/diagrams/{client.diagram}/link", json={"view": view, "element": view})
        assert r.status_code == 200, r.text
    for kind, source, target in (
        ("communicates", "web", "orders"),
        ("data_flow", "orders", "db"),
        ("communicates", "web", "fremd"),
    ):
        body = {"kind": kind, "source": source, "target": target}
        assert client.post("/api/projects/shop/relations", json=body).status_code == 201, (kind, source, target)
    for edge, relation in (("e1", "r1"), ("e2", "r2")):
        r = client.post(f"/api/diagrams/{client.diagram}/link-relation", json={"view": edge, "relation": relation})
        assert r.status_code == 200, r.text
    return client


def export(client) -> dict:
    return client.get(f"/api/diagrams/{client.diagram}/export").json()


def imported(client, document: dict, project: str) -> dict:
    r = client.post("/api/diagrams/import", json={"document": document, "project": project})
    assert r.status_code == 201, r.text
    return r.json()


def test_the_export_carries_the_referenced_model(client):
    model = export(client)["model"]
    assert [e["id"] for e in model["elements"]] == ["shop", "web", "orders", "db"]  # the parent of orders too
    assert [r["id"] for r in model["relations"]] == ["r1", "r2"]  # r3 ends at an element the diagram does not show
    assert export(client)["schema_version"] == exchange.DEPLOYMENT_SCHEMA_VERSION  # an optional key, no new version


def test_a_diagram_without_links_exports_unchanged():
    assert "model" not in exchange.export_document(Diagram(id="d", title="D"))
    document = exchange.export_document(Diagram(id="d", title="D"), ([], []))
    assert "model" not in document


def test_an_import_into_another_project_keeps_the_links(client):
    answer = imported(client, export(client), "other")
    nodes = answer["diagram"]["nodes"]
    assert {i: n.get("ref") for i, n in nodes.items()} == {"web": "web", "orders": "orders", "db": "db"}
    assert [e.get("ref") for e in answer["diagram"]["edges"]] == ["r1", "r2"]
    assert [c["id"] for c in answer["carried"]["elements"]["copied"]] == ["shop", "web", "db", "orders"]
    model = client.get("/api/projects/other/model").json()
    assert sorted(e["id"] for e in model["elements"]) == ["db", "orders", "shop", "web"]
    assert [r["id"] for r in model["relations"]] == ["r1", "r2"]


def test_an_import_into_the_same_project_maps_everything(client):
    answer = imported(client, export(client), "shop")
    assert {n.get("ref") for n in answer["diagram"]["nodes"].values()} == {"web", "orders", "db"}
    assert [m["id"] for m in answer["carried"]["elements"]["mapped"]] == ["shop", "web", "db", "orders"]
    assert answer["carried"]["relations"]["mapped"] == [{"id": "r1", "as": "r1"}, {"id": "r2", "as": "r2"}]
    assert len(client.get("/api/projects/shop/model").json()["elements"]) == 5  # nothing copied


def test_an_import_relinks_what_got_new_ids(client):
    client.post("/api/projects/other/elements", json={"name": "Alt", "id": "orders"})
    answer = imported(client, export(client), "other")
    assert answer["diagram"]["nodes"]["orders"]["ref"] == "orders-2"
    assert answer["diagram"]["nodes"]["orders"]["name"] == "Orders"
    assert {"id": "orders", "as": "orders-2"} in answer["carried"]["elements"]["copied"]
    assert [e.get("ref") for e in answer["diagram"]["edges"]] == ["r1", "r2"]


def test_a_document_without_a_model_drops_its_links_and_says_so(client):
    document = export(client)
    del document["model"]
    answer = imported(client, document, "other")
    assert all("ref" not in n for n in answer["diagram"]["nodes"].values())
    assert all("ref" not in e for e in answer["diagram"]["edges"])
    dropped = answer["carried"]["elements"]["dropped"]
    assert sorted(d["id"] for d in dropped) == ["db", "orders", "web"]
    assert {d["reason"] for d in dropped} == {"the document carries no model"}
    assert [d["id"] for d in answer["carried"]["relations"]["dropped"]] == ["r1", "r2"]
    assert client.get("/api/projects/other/model").json()["elements"] == []


def test_a_document_without_links_reports_nothing(client):
    answer = imported(client, exchange.export_document(Diagram(id="plain", title="Plain")), "other")
    assert "carried" not in answer


@pytest.mark.parametrize(
    ("model", "message"),
    [
        ("nope", "model must be an object"),
        ({"elements": "x"}, "model.elements must be a list"),
        ({"elements": [{"id": "a"}]}, "model.elements.0.name"),
        ({"elements": [{"id": "Bad Id", "name": "A"}]}, "invalid element id"),
        ({"elements": [{"id": "a", "name": "A"}, {"id": "b", "name": "a"}]}, "same name"),
        ({"elements": [{"id": "a", "name": "A", "parent": "zz"}]}, "unknown parent"),
        (
            {
                "elements": [{"id": "a", "name": "A"}],
                "relations": [{"id": "r1", "kind": "communicates", "source": "a", "target": "q"}],
            },
            "unknown target",
        ),
    ],
)
def test_the_embedded_model_is_validated(model, message):
    document = exchange.export_document(Diagram(id="d", title="D"))
    document["model"] = model
    with pytest.raises(exchange.ExchangeError, match=message):
        exchange.parse_document_model(document)


def test_an_invalid_model_refuses_the_import(client):
    document = export(client)
    document["model"]["elements"][0]["kind"] = "planet"
    r = client.post("/api/diagrams/import", json={"document": document, "project": "other"})
    assert r.status_code == 400 and "model: element 'shop' has unknown kind 'planet'" in r.json()["detail"]


def test_bundles_keep_their_documents_as_they_were(client):
    bundle = client.get("/api/projects/shop/export").json()
    assert all("model" not in d for d in bundle["diagrams"])  # the bundle carries the whole model once


def test_an_embedded_model_is_shortened_like_a_bundle(client):
    document = export(client)
    document["model"]["elements"][1]["name"] = "W" * 150  # web, exported by a release before M26's bounds
    r = client.post("/api/diagrams/import", json={"document": document, "project": "other"})
    assert r.status_code == 201, r.text
    answer = r.json()
    assert answer["warnings"] == ["1 element name longer than 100 characters was shortened."]
    assert answer["diagram"]["nodes"]["web"]["ref"] == "web"
    assert answer["diagram"]["nodes"]["web"]["name"] == "W" * 99 + "…"  # the view shows the element's name


def test_a_failed_import_removes_what_it_copied(client, monkeypatch):
    from arcviz.ports import StoreError
    from arcviz.services import DiagramService

    def broken(self, *args, **kwargs):
        raise StoreError("The store is not available.")

    monkeypatch.setattr(DiagramService, "import_new", broken)
    r = client.post("/api/diagrams/import", json={"document": export(client), "project": "other"})
    assert r.status_code >= 400
    assert client.get("/api/projects/other/model").json()["elements"] == []  # the copies are gone again


def test_a_version_export_embeds_only_elements_that_still_match_its_views(client):
    versions = client.get(f"/api/diagrams/{client.diagram}/versions").json()
    linked = max(v["version"] for v in versions)  # every view linked
    client.patch("/api/projects/shop/elements/web", json={"name": "Web-Frontend"})  # renamed since that version
    client.patch("/api/projects/shop/elements/db", json={"kind": "component"})  # a database view can still show it
    document = client.get(f"/api/diagrams/{client.diagram}/export", params={"version": linked}).json()
    ids = [e["id"] for e in document["model"]["elements"]]
    assert "web" not in ids  # its name in that version is not the element's any more: perhaps not the same element
    assert "orders" in ids and "shop" in ids
    assert all("web" not in (r["source"], r["target"]) for r in document["model"]["relations"])
    current = export(client)["model"]["elements"]
    assert "web" in [e["id"] for e in current]  # the current state matches (the rename reached the view)


def test_a_copy_removed_before_the_create_is_unlinked_even_when_prelinked(client, monkeypatch):
    from arcviz.services import DiagramService

    client.post("/api/projects/other/elements", json={"name": "Lager", "id": "db"})  # db is copied as db-2: prelinked
    original = DiagramService.import_new

    def remove_first(self, *args, **kwargs):
        client.delete("/api/projects/other/elements/db-2")  # another editor removes the copy before the create
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DiagramService, "import_new", remove_first)
    answer = imported(client, export(client), "other")
    assert "ref" not in answer["diagram"]["nodes"]["db"]
    assert "db" in [d["id"] for d in answer["carried"]["elements"]["dropped"]]


def test_a_blank_title_on_import_gets_a_default_and_a_warning(client):
    document = exchange.export_document(Diagram(id="blank", title="  "))
    answer = imported(client, document, "other")
    assert answer["diagram"]["title"] == "Unbenanntes Diagramm"
    assert any("Unbenanntes Diagramm" in w for w in answer["warnings"])


def test_properties_are_never_normalised():
    d = Diagram(id="d", title="D")
    d.nodes["a"] = Node(id="a", name="a", kind="service", properties={"name": "x\ny", "label": "p\tq"})
    parsed = exchange.parse_document(exchange.export_document(d))
    assert parsed.nodes["a"].properties == {"name": "x\ny", "label": "p\tq"}  # user data, not labels


def test_a_node_called_properties_is_still_normalised():
    d = Diagram(id="d", title="D")
    d.nodes["properties"] = Node(id="properties", name="a\nb", kind="service")
    assert exchange.parse_document(exchange.export_document(d)).nodes["properties"].name == "a b"
