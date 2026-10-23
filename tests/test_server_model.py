"""HTTP API and service of the shared project model (spec: docs/architecture/specs/shared-project-model.md)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService
from arcviz.store import FileStore


@pytest.fixture
def client(store):
    return TestClient(create_app(store=store))


@pytest.fixture
def shop(client):
    """A project with a deployment, an activity and a sequence diagram."""
    assert client.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    client.post("/api/projects/shop/sections", json={"title": "Bestellung", "id": "bestellung"})
    for title, diagram_id, template in (
        ("Plattform", "platform", "aks-database"),
        ("Bestellprozess", "order", "order-process"),
        ("Checkout", "checkout", "checkout"),
    ):
        r = client.post(
            "/api/diagrams", json={"title": title, "id": diagram_id, "template": template, "project": "shop"}
        )
        assert r.status_code == 201
    return client


def nodes(client, diagram_id: str) -> dict:
    return client.get(f"/api/diagrams/{diagram_id}").json()["diagram"]["nodes"]


def lifelines(client, diagram_id: str) -> list[dict]:
    return client.get(f"/api/diagrams/{diagram_id}").json()["diagram"]["interaction"]["lifelines"]


def test_elements_crud(shop):
    r = shop.post("/api/projects/shop/elements", json={"name": "Order Service", "description": "Bestellungen"})
    assert r.status_code == 201
    assert r.json()["element"] == {
        "id": "order-service",
        "name": "Order Service",
        "kind": "component",
        "description": "Bestellungen",
        "parent": None,
    }
    assert [e["id"] for e in r.json()["model"]["elements"]] == ["order-service"]
    shop.post("/api/projects/shop/elements", json={"name": "Shop", "kind": "system", "id": "shop-system"})
    r = shop.patch("/api/projects/shop/elements/order-service", json={"description": "Neu", "parent": "shop-system"})
    assert r.status_code == 200 and r.json()["element"]["parent"] == "shop-system"
    assert r.json()["element"]["description"] == "Neu"
    r = shop.patch("/api/projects/shop/elements/order-service", json={"parent": None})
    assert r.json()["element"]["parent"] is None
    model = shop.get("/api/projects/shop/model").json()
    assert model["project"] == "shop" and len(model["elements"]) == 2
    r = shop.delete("/api/projects/shop/elements/shop-system")
    assert r.status_code == 200 and r.json()["element"]["element"] == "shop-system"
    assert [e["id"] for e in r.json()["model"]["elements"]] == ["order-service"]


def test_config_describes_the_model(client):
    config = client.get("/api/config").json()
    kinds = config["project_model"]["element_kinds"]
    assert kinds == ["actor", "system", "subsystem", "component", "node", "data"]
    assert set(config["diagram_types"]["sequence"]["linkable"]["lifeline"]) == set(kinds)
    assert config["diagram_types"]["activity"]["linkable"]["data_store_node"] == ["data"]
    assert "opaque_action" not in config["diagram_types"]["activity"]["linkable"]
    assert config["diagram_types"]["deployment"]["linkable"]["database"] == ["data", "component"]


def test_the_elements_alone_come_from_the_project_document(shop):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service", "description": "x"})
    r = shop.get("/api/projects/shop/elements")
    assert r.status_code == 200
    assert r.json() == {
        "project": "shop",
        "elements": [
            {"id": "order-service", "name": "Order Service", "kind": "component", "description": "x", "parent": None}
        ],
        "relations": [],  # M25 (spec: model-relations.md)
    }
    assert shop.get("/api/projects/nope/elements").status_code == 404


def test_element_errors(shop):
    assert shop.post("/api/projects/nope/elements", json={"name": "X"}).status_code == 404
    assert shop.post("/api/projects/shop/elements", json={"name": "X", "kind": "robot"}).status_code == 400
    assert shop.patch("/api/projects/shop/elements/ghost", json={"name": "X"}).status_code == 400
    assert shop.delete("/api/projects/shop/elements/ghost").status_code == 400
    assert shop.get("/api/projects/nope/model").status_code == 404
    # the model is not changed through the generic project operations
    r = shop.post("/api/projects/shop/ops", json={"op": "add_element", "args": {"name": "X"}})
    assert r.status_code == 400 and "/elements" in r.json()["detail"]


def test_link_unlink_and_promote_across_the_three_diagram_types(shop):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    service = next(i for i, n in nodes(shop, "platform").items() if n["kind"] == "service")
    r = shop.post("/api/diagrams/platform/link", json={"view": service, "element": "Order Service"})
    assert r.status_code == 200, r.text
    assert nodes(shop, "platform")[service]["ref"] == "order-service"
    assert nodes(shop, "platform")[service]["name"] == "Order Service"
    assert r.json()["result"]["touched"] == [service]
    assert r.json()["versions"][-1]["label"].startswith("Mit Element")

    shop.post("/api/projects/shop/elements", json={"name": "Kunde", "kind": "actor"})
    partition = next(i for i, n in nodes(shop, "order").items() if n["kind"] == "activity_partition")
    assert shop.post("/api/diagrams/order/link", json={"view": partition, "element": "kunde"}).status_code == 200
    lifeline = next(lf for lf in lifelines(shop, "checkout") if lf["actor"])
    assert (
        shop.post("/api/diagrams/checkout/link", json={"view": lifeline["id"], "element": "kunde"}).status_code == 200
    )

    model = shop.get("/api/projects/shop/model").json()
    kunde = next(e for e in model["elements"] if e["id"] == "kunde")
    assert sorted(u["diagram"] for u in kunde["usages"]) == ["checkout", "order"]
    assert {u["type"] for u in kunde["usages"]} == {"activity", "sequence"}

    r = shop.post("/api/diagrams/checkout/unlink", json={"view": lifeline["id"]})
    assert r.status_code == 200 and "ref" not in lifelines(shop, "checkout")[0]

    other = next(lf for lf in lifelines(shop, "checkout") if not lf["actor"])
    r = shop.post("/api/diagrams/checkout/promote", json={"view": other["id"], "description": "Webshop"})
    assert r.status_code == 201, r.text
    assert r.json()["element"]["name"] == other["name"] and r.json()["element"]["kind"] == "component"
    assert next(lf for lf in lifelines(shop, "checkout") if lf["id"] == other["id"])["ref"] == r.json()["element"]["id"]


def test_link_errors(shop):
    shop.post("/api/projects/shop/elements", json={"name": "DB", "kind": "data"})
    action = next(i for i, n in nodes(shop, "order").items() if n["kind"] == "opaque_action")
    assert shop.post("/api/diagrams/order/link", json={"view": action, "element": "db"}).status_code == 400
    assert shop.post("/api/diagrams/order/link", json={"view": "ghost", "element": "db"}).status_code == 400
    assert shop.post("/api/diagrams/order/link", json={"view": action, "element": "ghost"}).status_code == 400
    assert shop.post("/api/diagrams/nope/link", json={"view": "x", "element": "db"}).status_code == 404
    assert shop.post("/api/diagrams/order/unlink", json={"view": action}).status_code == 400
    assert shop.post("/api/diagrams/order/promote", json={"view": action}).status_code == 400
    service = next(i for i, n in nodes(shop, "platform").items() if n["kind"] == "service")
    r = shop.post("/api/diagrams/platform/promote", json={"view": service, "kind": "actor"})
    assert r.status_code == 400 and shop.get("/api/projects/shop/model").json()["elements"][0]["id"] == "db"


def test_a_linked_view_element_is_renamed_through_the_model_only(shop):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    service = next(i for i, n in nodes(shop, "platform").items() if n["kind"] == "service")
    shop.post("/api/diagrams/platform/link", json={"view": service, "element": "order-service"})
    r = shop.post("/api/diagrams/platform/ops", json={"op": "update_node", "args": {"id": service, "name": "X"}})
    assert r.status_code == 400 and "update_element" in r.json()["detail"]


def test_sections_link_to_elements(shop):
    shop.post("/api/projects/shop/elements", json={"name": "Checkout", "kind": "subsystem"})
    r = shop.patch("/api/projects/shop/sections/bestellung", json={"element": "checkout"})
    assert r.status_code == 200
    section = r.json()["project"]["sections"][0]
    assert section == {"id": "bestellung", "title": "Checkout", "parent": None, "element": "checkout"}
    assert shop.patch("/api/projects/shop/sections/bestellung", json={"title": "Kasse"}).status_code == 400
    r = shop.patch("/api/projects/shop/sections/bestellung", json={"element": None, "title": "Kasse"})
    assert r.json()["project"]["sections"][0] == {"id": "bestellung", "title": "Kasse", "parent": None}
    assert shop.get("/api/projects/shop/model").json()["elements"][0]["sections"] == []


def test_a_diagram_outside_every_project_cannot_link(tmp_path):
    store = FileStore(tmp_path)
    diagrams = DiagramService(store)  # no project service: the diagram is placed nowhere
    from arcviz.templates import create_from_template

    diagrams.create("Plattform", "platform", create_from_template("aks-database", "platform", "Plattform"))
    projects = ProjectService(store, store)
    projects.create("Shop", "shop")
    models = ModelService(projects, DiagramService(store, projects))
    models.add_element("shop", "Order Service")
    from arcviz.projects import ProjectError

    with pytest.raises(ProjectError, match="in no project"):
        models.link("platform", "frontend", "order-service")


# ---- propagation, deletion and the sync repair (ADR-0071, ADR-0072) ------------------------------------------


@pytest.fixture
def linked(shop):
    """'Order Service' shown in all three diagrams and standing for the section."""
    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    service = next(i for i, n in nodes(shop, "platform").items() if n["kind"] == "service")
    partition = next(i for i, n in nodes(shop, "order").items() if n["kind"] == "activity_partition")
    lifeline = lifelines(shop, "checkout")[1]["id"]
    for diagram_id, view in (("platform", service), ("order", partition), ("checkout", lifeline)):
        r = shop.post(f"/api/diagrams/{diagram_id}/link", json={"view": view, "element": "order-service"})
        assert r.status_code == 200, r.text
    shop.patch("/api/projects/shop/sections/bestellung", json={"element": "order-service"})
    shop.views = {"platform": service, "order": partition, "checkout": lifeline}
    return shop


def view_name(client, diagram_id: str, view: str) -> str:
    diagram = client.get(f"/api/diagrams/{diagram_id}").json()["diagram"]
    if diagram["interaction"] is not None:
        return next(lf["name"] for lf in diagram["interaction"]["lifelines"] if lf["id"] == view)
    return diagram["nodes"][view]["name"]


def versions(client, diagram_id: str) -> list[dict]:
    return client.get(f"/api/diagrams/{diagram_id}/versions").json()


def test_a_rename_reaches_every_view_and_the_section(linked):
    counts = {d: len(versions(linked, d)) for d in linked.views}
    r = linked.patch("/api/projects/shop/elements/order-service", json={"name": "Bestellservice"})
    assert r.status_code == 200
    assert sorted(r.json()["propagated"]) == ["checkout", "order", "platform"] and r.json()["failed"] == []
    for diagram_id, view in linked.views.items():
        assert view_name(linked, diagram_id, view) == "Bestellservice"
        history = versions(linked, diagram_id)
        assert len(history) == counts[diagram_id] + 1
        assert history[-1]["label"] == "Element „Bestellservice“ umbenannt"
    project = linked.get("/api/projects/shop").json()["project"]
    assert project["sections"][0]["title"] == "Bestellservice"
    usages = r.json()["model"]["elements"][0]["usages"]
    assert {u["name"] for u in usages} == {"Bestellservice"}


def test_a_description_changes_no_diagram(linked):
    counts = {d: len(versions(linked, d)) for d in linked.views}
    r = linked.patch("/api/projects/shop/elements/order-service", json={"description": "Nimmt Bestellungen an"})
    assert r.json()["propagated"] == [] and r.json()["element"]["description"] == "Nimmt Bestellungen an"
    assert {d: len(versions(linked, d)) for d in linked.views} == counts


def test_sync_repairs_drift_and_writes_nothing_when_consistent(linked):
    linked.patch("/api/projects/shop/elements/order-service", json={"name": "Bestellservice"})
    assert linked.post("/api/diagrams/platform/undo").status_code == 200  # the old name is back: drift
    assert view_name(linked, "platform", linked.views["platform"]) == "Order Service"
    counts = {d: len(versions(linked, d)) for d in linked.views}
    r = linked.post("/api/projects/shop/model/sync")
    assert r.status_code == 200 and r.json()["changed"] == ["platform"] and r.json()["failed"] == []
    assert view_name(linked, "platform", linked.views["platform"]) == "Bestellservice"
    assert versions(linked, "platform")[-1]["label"] == "Modell abgeglichen"
    again = linked.post("/api/projects/shop/model/sync").json()
    assert again["changed"] == []
    assert len(versions(linked, "platform")) == counts["platform"] + 1
    assert {d: len(versions(linked, d)) for d in ("order", "checkout")} == {d: counts[d] for d in ("order", "checkout")}


def test_a_kind_change_must_suit_every_view(linked):
    r = linked.patch("/api/projects/shop/elements/order-service", json={"kind": "data"})
    assert r.status_code == 400 and "order" in r.json()["detail"] and "Unlink" in r.json()["detail"]
    r = linked.patch("/api/projects/shop/elements/order-service", json={"kind": "system"})
    assert r.status_code == 200 and r.json()["element"]["kind"] == "system"


def test_deleting_an_element_unlinks_its_views_and_keeps_them(linked):
    r = linked.delete("/api/projects/shop/elements/order-service")
    assert r.status_code == 200 and sorted(r.json()["unlinked"]) == ["checkout", "order", "platform"]
    for diagram_id, view in linked.views.items():
        assert view_name(linked, diagram_id, view) == "Order Service"  # the view element stays
        assert versions(linked, diagram_id)[-1]["label"] == "Element „Order Service“ entfernt"
    assert "ref" not in nodes(linked, "platform")[linked.views["platform"]]
    assert all("ref" not in lf for lf in lifelines(linked, "checkout"))
    assert linked.get("/api/projects/shop/model").json()["elements"] == []
    assert "element" not in linked.get("/api/projects/shop").json()["project"]["sections"][0]


def test_propagation_reports_diagrams_it_could_not_write(store):
    from arcviz.ports import ConflictError
    from arcviz.templates import create_from_template

    projects = ProjectService(store, store)
    diagrams = DiagramService(store, projects)
    models = ModelService(projects, diagrams)
    projects.create("Shop", "shop")
    for diagram_id in ("a", "b"):
        diagrams.create(diagram_id, diagram_id, create_from_template("aks-database", diagram_id, diagram_id), "shop")
        service = next(n.id for n in diagrams.get(diagram_id).nodes.values() if n.kind == "service")
    models.add_element("shop", "Order Service")
    for diagram_id in ("a", "b"):
        models.link(diagram_id, service, "order-service")

    real = diagrams.apply_if_changed

    def busy_b(diagram_id, steps, label):
        if diagram_id == "b":
            raise ConflictError("busy")
        return real(diagram_id, steps, label)

    diagrams.apply_if_changed = busy_b  # type: ignore[method-assign]
    change = models.update_element("shop", "order-service", name="Bestellservice")
    assert change.propagated == ["a"] and change.failed == ["b"]
    assert models.model("shop").elements[0].name == "Bestellservice"  # the model is authoritative
    with pytest.raises(ConflictError):
        models.remove_element("shop", "order-service")
    assert models.model("shop").elements[0].id == "order-service"  # kept: b still links it
    diagrams.apply_if_changed = real  # type: ignore[method-assign]
    assert models.sync("shop").changed == ["b"]
    diagrams.delete("a")  # a diagram deleted meanwhile is skipped
    assert models.remove_element("shop", "order-service").unlinked == ["b"]


# ---- review of #154: one write per request, one version per diagram ---------------------------------------------


def test_a_failing_part_of_an_element_patch_changes_nothing(linked):
    linked.post("/api/projects/shop/elements", json={"name": "Shop", "kind": "system"})
    linked.patch("/api/projects/shop/elements/order-service", json={"parent": "shop"})
    counts = {d: len(versions(linked, d)) for d in linked.views}
    # a rename plus a move into its own part: the move fails, so the rename must not happen either
    r = linked.patch("/api/projects/shop/elements/shop", json={"name": "Webshop", "parent": "order-service"})
    assert r.status_code == 400
    names = {e["id"]: e["name"] for e in linked.get("/api/projects/shop/model").json()["elements"]}
    assert names["shop"] == "Shop"
    r = linked.patch("/api/projects/shop/elements/order-service", json={"name": "Bestellservice", "parent": "ghost"})
    assert r.status_code == 400
    assert {d: len(versions(linked, d)) for d in linked.views} == counts
    assert view_name(linked, "platform", linked.views["platform"]) == "Order Service"


def test_a_failing_part_of_a_section_patch_changes_nothing(shop):
    shop.post("/api/projects/shop/elements", json={"name": "Checkout", "kind": "subsystem"})
    r = shop.patch("/api/projects/shop/sections/bestellung", json={"element": "checkout", "title": "Kasse"})
    assert r.status_code == 400
    section = shop.get("/api/projects/shop").json()["project"]["sections"][0]
    assert section == {"id": "bestellung", "title": "Bestellung", "parent": None}


def test_an_unchanged_name_reaches_no_diagram(linked):
    linked.patch("/api/projects/shop/elements/order-service", json={"name": "Bestellservice"})
    linked.post("/api/diagrams/platform/undo")  # drift in platform
    counts = {d: len(versions(linked, d)) for d in linked.views}
    r = linked.patch("/api/projects/shop/elements/order-service", json={"name": "Bestellservice", "description": "x"})
    assert r.status_code == 200 and r.json()["propagated"] == []
    assert {d: len(versions(linked, d)) for d in linked.views} == counts


def test_sync_writes_one_version_per_diagram(linked):
    linked.post("/api/projects/shop/elements", json={"name": "Datenbank", "kind": "data"})
    database = next(i for i, n in nodes(linked, "platform").items() if n["kind"] == "database")
    linked.post("/api/diagrams/platform/link", json={"view": database, "element": "datenbank"})
    linked.patch("/api/projects/shop/elements/order-service", json={"name": "Bestellservice"})
    linked.patch("/api/projects/shop/elements/datenbank", json={"name": "Orders DB"})
    linked.post("/api/diagrams/platform/undo")
    linked.post("/api/diagrams/platform/undo")  # both names drifted in platform
    before = len(versions(linked, "platform"))
    r = linked.post("/api/projects/shop/model/sync")
    assert r.json()["changed"] == ["platform"]
    assert len(versions(linked, "platform")) == before + 1
    assert view_name(linked, "platform", linked.views["platform"]) == "Bestellservice"
    assert view_name(linked, "platform", database) == "Orders DB"


def test_moving_a_diagram_to_another_project_relinks_its_views(linked):
    """Review of #154: element ids are only unique within a project, so a moved view must never keep a ref that names
    another element there; since #153 (ADR-0161) the element comes along under a new id and the view is relinked."""
    linked.post("/api/projects", json={"title": "Andere", "id": "other"})
    linked.post("/api/projects/other/elements", json={"name": "Fremd", "id": "order-service"})  # same id, other thing
    within = linked.post("/api/projects/shop/diagrams/order/move", json={"section": "bestellung"})
    assert within.status_code == 200
    assert nodes(linked, "order")[linked.views["order"]]["ref"] == "order-service"  # same project: kept
    r = linked.post("/api/projects/shop/diagrams/platform/move", json={"project": "other"})
    assert r.status_code == 200
    node = nodes(linked, "platform")[linked.views["platform"]]
    assert node["ref"] == "order-service-2" and node["name"] == "Order Service"
    assert r.json()["carried"]["elements"]["copied"] == [{"id": "order-service", "as": "order-service-2"}]
    assert [v["label"] for v in versions(linked, "platform")[-2:]] == [
        "Verknüpfungen angepasst (anderes Projekt)",
        "Verknüpfungen übernommen (anderes Projekt)",
    ]
    elements = {e["id"]: e for e in linked.get("/api/projects/other/model").json()["elements"]}
    assert elements["order-service"]["name"] == "Fremd" and elements["order-service"]["usages"] == []
    assert linked.post("/api/projects/shop/diagrams/checkout/move", json={"project": "nope"}).status_code == 404
    assert any(lf.get("ref") for lf in lifelines(linked, "checkout"))  # unknown target: nothing changed


def test_importing_a_single_diagram_keeps_its_links_only_with_its_model(linked):
    """Since #153 (ADR-0161) the export carries the model; a document without it drops its links (ADR-0079)."""
    document = linked.get("/api/diagrams/platform/export").json()
    assert document["diagram"]["nodes"][linked.views["platform"]]["ref"] == "order-service"
    r = linked.post("/api/diagrams/import", json={"document": document, "project": "shop"})
    assert r.status_code == 201
    assert r.json()["diagram"]["nodes"][linked.views["platform"]]["ref"] == "order-service"  # the same element
    del document["model"]
    r = linked.post("/api/diagrams/import", json={"document": document, "project": "shop"})
    assert r.status_code == 201
    assert all("ref" not in n for n in r.json()["diagram"]["nodes"].values())
