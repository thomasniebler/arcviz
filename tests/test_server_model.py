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
    # model first: the template nodes of the three diagrams are project elements too
    before = {e["id"] for e in shop.get("/api/projects/shop/model").json()["elements"]}
    r = shop.post("/api/projects/shop/elements", json={"name": "Order Service", "description": "Bestellungen"})
    assert r.status_code == 201
    assert r.json()["element"] == {
        "id": "order-service",
        "name": "Order Service",
        "kind": "component",
        "description": "Bestellungen",
        "parent": None,
    }
    assert {e["id"] for e in r.json()["model"]["elements"]} == before | {"order-service"}
    shop.post("/api/projects/shop/elements", json={"name": "Shop System", "kind": "system", "id": "shop-system"})
    r = shop.patch("/api/projects/shop/elements/order-service", json={"description": "Neu", "parent": "shop-system"})
    assert r.status_code == 200 and r.json()["element"]["parent"] == "shop-system"
    assert r.json()["element"]["description"] == "Neu"
    r = shop.patch("/api/projects/shop/elements/order-service", json={"parent": None})
    assert r.json()["element"]["parent"] is None
    model = shop.get("/api/projects/shop/model").json()
    assert model["project"] == "shop" and len(model["elements"]) == len(before) + 2
    r = shop.delete("/api/projects/shop/elements/shop-system")
    assert r.status_code == 200 and r.json()["element"]["element"] == "shop-system"
    assert {e["id"] for e in r.json()["model"]["elements"]} == before | {"order-service"}


def test_config_describes_the_model(client):
    config = client.get("/api/config").json()
    kinds = config["project_model"]["element_kinds"]
    # model first: ELEMENT_KINDS gained interface, use_case and package (the nodes of class/use case/component views)
    m18 = ["actor", "system", "subsystem", "component", "node", "data"]
    assert kinds == [*m18, "interface", "use_case", "package"]
    assert set(config["diagram_types"]["sequence"]["linkable"]["lifeline"]) == set(m18)
    assert config["diagram_types"]["activity"]["linkable"]["data_store_node"] == ["data"]
    assert "opaque_action" not in config["diagram_types"]["activity"]["linkable"]
    assert config["diagram_types"]["deployment"]["linkable"]["database"] == ["data", "component"]


def test_the_elements_alone_come_from_the_project_document(shop):
    shop.post("/api/projects/shop/elements", json={"name": "Order Service", "description": "x"})
    r = shop.get("/api/projects/shop/elements")
    assert r.status_code == 200
    body = r.json()
    assert sorted(body) == ["elements", "project", "relations"] and body["project"] == "shop"
    assert body["relations"] == []  # M25 (spec: model-relations.md)
    # model first: the template nodes are elements as well; the added one comes as stored, without usages
    assert body["elements"][-1] == {
        "id": "order-service",
        "name": "Order Service",
        "kind": "component",
        "description": "x",
        "parent": None,
    }
    assert all("usages" not in e for e in body["elements"])
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
    # model first: a linked node becomes the element, so it carries the element's id from now on
    assert service not in nodes(shop, "platform")
    assert nodes(shop, "platform")["order-service"]["ref"] == "order-service"
    assert nodes(shop, "platform")["order-service"]["name"] == "Order Service"
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

    # model first: a lifeline always stands for an element, so an unlink cannot take effect; it must not pretend to
    # (no version that changes nothing) and the lifeline keeps its element
    count = len(versions(shop, "checkout"))
    shop.post("/api/diagrams/checkout/unlink", json={"view": lifeline["id"]})
    assert lifelines(shop, "checkout")[0]["ref"] == "kunde"
    assert len(versions(shop, "checkout")) == count

    # model first: every lifeline already is an element, so there is nothing left to promote
    other = next(lf for lf in lifelines(shop, "checkout") if not lf["actor"])
    r = shop.post("/api/diagrams/checkout/promote", json={"view": other["id"], "description": "Webshop"})
    assert r.status_code == 400 and "already represents" in r.json()["detail"], r.text


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
    before = shop.get("/api/projects/shop/model").json()["elements"]
    r = shop.post("/api/diagrams/platform/promote", json={"view": service, "kind": "actor"})
    # model first: the template nodes are elements too, so "nothing created" is "the model is unchanged"
    assert r.status_code == 400 and shop.get("/api/projects/shop/model").json()["elements"] == before


def test_renaming_a_linked_view_element_renames_the_element(shop):
    """Model first: the refusal of ADR-0071 is gone; the view element is the element, so a rename renames it."""
    shop.post("/api/projects/shop/elements", json={"name": "Order Service"})
    service = next(i for i, n in nodes(shop, "platform").items() if n["kind"] == "service")
    shop.post("/api/diagrams/platform/link", json={"view": service, "element": "order-service"})
    r = shop.post(
        "/api/diagrams/platform/ops", json={"op": "update_node", "args": {"id": "order-service", "name": "X"}}
    )
    assert r.status_code == 200, r.text
    assert nodes(shop, "platform")["order-service"]["name"] == "X"
    names = {e["id"]: e["name"] for e in shop.get("/api/projects/shop/model").json()["elements"]}
    assert names["order-service"] == "X"


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
    # model first: the linked deployment node became the element and carries its id; behaviour parts keep theirs
    shop.views = {"platform": "order-service", "order": partition, "checkout": lifeline}
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
    # model first: every view resolves the name from the model, so nothing is propagated and no diagram is written
    assert r.json()["propagated"] == [] and r.json()["failed"] == []
    for diagram_id, view in linked.views.items():
        assert view_name(linked, diagram_id, view) == "Bestellservice"
        assert len(versions(linked, diagram_id)) == counts[diagram_id]
    project = linked.get("/api/projects/shop").json()["project"]
    assert project["sections"][0]["title"] == "Bestellservice"
    usages = next(e for e in r.json()["model"]["elements"] if e["id"] == "order-service")["usages"]
    assert sorted(u["diagram"] for u in usages) == ["checkout", "order", "platform"]
    assert {u["name"] for u in usages} == {"Bestellservice"}


def test_a_description_changes_no_diagram(linked):
    counts = {d: len(versions(linked, d)) for d in linked.views}
    r = linked.patch("/api/projects/shop/elements/order-service", json={"description": "Nimmt Bestellungen an"})
    assert r.json()["propagated"] == [] and r.json()["element"]["description"] == "Nimmt Bestellungen an"
    assert {d: len(versions(linked, d)) for d in linked.views} == counts


def test_an_undo_leaves_no_drift_for_sync_to_repair(linked):
    """Model first: drift is impossible by construction. The rename wrote no diagram version, so an undo in platform
    takes back the link (the node shows its former element again); the other views keep the model's name and the
    sync finds nothing to write."""
    linked.patch("/api/projects/shop/elements/order-service", json={"name": "Bestellservice"})
    assert linked.post("/api/diagrams/platform/undo").status_code == 200
    assert (
        "order-service" not in nodes(linked, "platform") and nodes(linked, "platform")["frontend"]["ref"] == "frontend"
    )
    assert {d: view_name(linked, d, linked.views[d]) for d in ("order", "checkout")} == {
        "order": "Bestellservice",
        "checkout": "Bestellservice",
    }
    counts = {d: len(versions(linked, d)) for d in linked.views}
    r = linked.post("/api/projects/shop/model/sync")
    assert r.status_code == 200 and r.json()["changed"] == [] and r.json()["failed"] == []
    assert {d: len(versions(linked, d)) for d in linked.views} == counts


def test_a_kind_change_must_suit_every_view(linked):
    r = linked.patch("/api/projects/shop/elements/order-service", json={"kind": "data"})
    assert r.status_code == 400 and "order" in r.json()["detail"] and "Unlink" in r.json()["detail"]
    r = linked.patch("/api/projects/shop/elements/order-service", json={"kind": "system"})
    assert r.status_code == 200 and r.json()["element"]["kind"] == "system"


def test_deleting_an_element_that_behaviour_parts_stand_for_is_refused(linked):
    """Model first (ADR-0213): a partition or lifeline always stands for an element, so the element cannot go while
    one stands for it; once they are gone it can, and the structure view stops showing it."""
    r = linked.delete("/api/projects/shop/elements/order-service")
    assert r.status_code == 400 and "order" in r.json()["detail"] and "checkout" in r.json()["detail"]
    assert any(e["id"] == "order-service" for e in linked.get("/api/projects/shop/model").json()["elements"])
    linked.post("/api/diagrams/order/ops", json={"op": "remove_node", "args": {"id": linked.views["order"]}})
    lifeline = linked.views["checkout"]
    linked.post("/api/diagrams/checkout/ops", json={"op": "remove_lifeline", "args": {"id": lifeline}})
    r = linked.delete("/api/projects/shop/elements/order-service")
    assert r.status_code == 200, r.text
    assert "order-service" not in nodes(linked, "platform")
    assert "element" not in linked.get("/api/projects/shop").json()["project"]["sections"][0]


def test_a_rename_needs_no_diagram_write_and_a_removal_waits_for_busy_diagrams(store):
    """Model first: a rename is one project write that every view resolves (a busy diagram is no obstacle any more);
    the removal still unlinks the views first and keeps the element while a diagram keeps changing (ADR-0072)."""
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
    names = lambda: {d: diagrams.get(d).nodes["order-service"].name for d in ("a", "b")}  # noqa: E731

    real = diagrams.apply_if_changed

    def busy_b(diagram_id, steps, label):
        if diagram_id == "b":
            raise ConflictError("busy")
        return real(diagram_id, steps, label)

    diagrams.apply_if_changed = busy_b  # type: ignore[method-assign]
    change = models.update_element("shop", "order-service", name="Bestellservice")
    # model first: nothing to propagate, both views show the new name although b is busy
    assert change.propagated == [] and change.failed == []
    assert names() == {"a": "Bestellservice", "b": "Bestellservice"}
    # model first: removing it writes no structure view either (they hold only its id), so a busy one cannot stop it
    diagrams.apply_if_changed = real  # type: ignore[method-assign]
    assert models.sync("shop").changed == []  # no drift to repair
    models.remove_element("shop", "order-service")
    assert "order-service" not in diagrams.get("a").nodes and "order-service" not in diagrams.get("b").nodes


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


def test_moving_a_diagram_to_another_project_relinks_its_views(linked):
    """Review of #154: element ids are only unique within a project, so a moved view must never keep a ref that names
    another element there; since #153 (ADR-0161) the element comes along under a new id and the view is relinked."""
    linked.post("/api/projects", json={"title": "Andere", "id": "other"})
    linked.post("/api/projects/other/elements", json={"name": "Fremd", "id": "order-service"})  # same id, other thing
    within = linked.post("/api/projects/shop/diagrams/order/move", json={"section": "bestellung"})
    assert within.status_code == 200
    assert nodes(linked, "order")[linked.views["order"]]["ref"] == "order-service"  # same project: kept
    r = linked.post("/api/projects/shop/diagrams/platform/move", json={"project": "other"})
    assert r.status_code == 200, r.text
    # model first: the node is the carried element, so it carries the new id; every element of the view comes along
    assert linked.views["platform"] not in nodes(linked, "platform")
    node = nodes(linked, "platform")["order-service-2"]
    assert node["ref"] == "order-service-2" and node["name"] == "Order Service"
    assert {"id": "order-service", "as": "order-service-2"} in r.json()["carried"]["elements"]["copied"]
    assert [v["label"] for v in versions(linked, "platform")[-2:]] == [
        "Verknüpfungen angepasst (anderes Projekt)",
        "Verknüpfungen übernommen (anderes Projekt)",
    ]
    elements = {e["id"]: e for e in linked.get("/api/projects/other/model").json()["elements"]}
    assert elements["order-service"]["name"] == "Fremd" and elements["order-service"]["usages"] == []
    assert linked.post("/api/projects/shop/diagrams/checkout/move", json={"project": "nope"}).status_code == 404
    assert any(lf.get("ref") for lf in lifelines(linked, "checkout"))  # unknown target: nothing changed


def test_importing_a_single_diagram_keeps_its_links_with_or_without_its_model(linked):
    """Since #153 (ADR-0161) the export carries the model. Model first: a document without it no longer drops its
    links (ADR-0079): every node becomes an element of the project, merged with the one of the same name."""
    document = linked.get("/api/diagrams/platform/export").json()
    assert document["diagram"]["nodes"][linked.views["platform"]]["ref"] == "order-service"
    r = linked.post("/api/diagrams/import", json={"document": document, "project": "shop"})
    assert r.status_code == 201, r.text
    assert r.json()["diagram"]["nodes"][linked.views["platform"]]["ref"] == "order-service"  # the same element
    del document["model"]
    r = linked.post("/api/diagrams/import", json={"document": document, "project": "shop"})
    assert r.status_code == 201, r.text
    elements = {e["id"] for e in linked.get("/api/projects/shop/model").json()["elements"]}
    assert all(n["ref"] == i and i in elements for i, n in r.json()["diagram"]["nodes"].items())
    assert r.json()["diagram"]["nodes"]["order-service"]["name"] == "Order Service"  # the same element
