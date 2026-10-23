"""Linked elements and relations come along when a diagram moves to another project (#153, ADR-0161).

Planner (`projects.carry_model`): map to the same element (same id, kind and name), copy under the same id when it is
free, under a new id when another element holds it, drop (ADR-0079) what does not fit; relations whose ends come along
are mapped or copied with ids from the target's `relation_seq`. Service (`DiagramService.move`): the project write, the
diagram's links adjusted before the membership moves and relinked after it, and the report in the answer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import projects
from arcviz.model import Diagram, Edge, Node
from arcviz.project_model import MAX_ELEMENTS, Element, Relation
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService

# ---- the planner -------------------------------------------------------------------------------------------------


def source_model() -> tuple[list[Element], list[Relation]]:
    elements = [
        Element(id="shop", name="Shop", kind="system"),
        Element(id="orders", name="Orders", kind="component", parent="shop", description="Bestellungen"),
        Element(id="db", name="DB", kind="data"),
        Element(id="web", name="Web", kind="component"),
        Element(id="unused", name="Unbenutzt", kind="component"),
    ]
    relations = [
        Relation(id="r1", kind="communicates", source="web", target="orders", properties={"protocol": "HTTPS"}),
        Relation(id="r2", kind="data_flow", source="orders", target="db"),
        Relation(id="r3", kind="communicates", source="web", target="unused"),
    ]
    return elements, relations


def target(*elements: Element, relations: list[Relation] | None = None, seq: int = 0) -> projects.Project:
    project = projects.new_project("Ziel", "target")
    project.elements = list(elements)
    project.relations = list(relations or [])
    project.relation_seq = seq
    return project


def carry(project: projects.Project, element_ids, relation_ids=(), limit=None) -> projects.CarriedModel:
    elements, relations = source_model()
    return projects.carry_model(project, elements, relations, set(element_ids), set(relation_ids), limit=limit)


def test_free_ids_are_copied_with_their_parent_chain():
    project = target()
    carried = carry(project, {"orders", "db"})
    assert carried.elements == {"shop": "shop", "orders": "orders", "db": "db"}  # the parent came along
    copied = {e.id: e for e in project.elements}
    assert copied["orders"].parent == "shop" and copied["orders"].description == "Bestellungen"
    assert [c["id"] for c in carried.report()["elements"]["copied"]] == ["shop", "db", "orders"]  # parents first
    assert project.integrity_errors() == []


def test_the_same_element_is_mapped_not_copied():
    project = target(Element(id="orders", name="ORDERS", kind="component"))
    carried = carry(project, {"orders"})
    assert carried.elements["orders"] == "orders"
    assert [e.id for e in project.elements] == ["orders", "shop"]  # the parent was copied, the element mapped
    assert carried.report()["elements"]["mapped"] == [{"id": "orders", "as": "orders"}]
    assert next(e for e in project.elements if e.id == "orders").parent is None  # the target's element stays as it is


def test_an_id_held_by_another_element_is_copied_under_a_new_id():
    project = target(Element(id="orders", name="Fremd", kind="component"), Element(id="db", name="Lager", kind="data"))
    carried = carry(project, {"orders", "db"})
    assert carried.elements["orders"] == "orders-2" and carried.elements["db"] == "db-2"
    report = carried.report()["elements"]
    assert {"id": "orders", "as": "orders-2"} in report["copied"]
    assert next(e for e in project.elements if e.id == "orders").name == "Fremd"  # untouched


def test_a_name_held_by_another_element_is_dropped():
    project = target(Element(id="bestand", name="db", kind="data"))  # names are unique per project
    carried = carry(project, {"db"})
    assert "db" not in carried.elements
    (dropped,) = carried.report()["elements"]["dropped"]
    assert dropped["id"] == "db" and "already called" in dropped["reason"]
    assert [e.id for e in project.elements] == ["bestand"]


def test_unknown_refs_are_dropped():
    carried = carry(target(), {"ghost"})
    assert carried.report()["elements"]["dropped"] == [{"id": "ghost", "reason": "not in the source project"}]


def test_relations_get_ids_from_the_relation_seq_and_are_never_reused():
    project = target(seq=7)  # r1..r7 were used and removed: never generated again
    carried = carry(project, {"web", "orders", "db"}, {"r1"})
    assert carried.relations == {"r1": "r8", "r2": "r9"}  # r2: both ends copied; r3: its end stays behind
    assert project.relation_seq == 9
    assert [(r.id, r.kind, r.source, r.target) for r in project.relations] == [
        ("r8", "communicates", "web", "orders"),
        ("r9", "data_flow", "orders", "db"),
    ]
    assert project.relations[0].properties == {"protocol": "HTTPS"}


def test_an_existing_relation_between_the_mapped_ends_is_mapped():
    web, orders = Element(id="web", name="Web", kind="component"), Element(id="orders", name="Orders", kind="component")
    existing = Relation(id="r4", kind="communicates", source="orders", target="web")  # communicates: either way
    project = target(web, orders, relations=[existing], seq=4)
    carried = carry(project, {"web", "orders"}, {"r1"})
    assert carried.relations == {"r1": "r4"}
    assert carried.report()["relations"]["mapped"] == [{"id": "r1", "as": "r4"}]
    assert len(project.relations) == 1


def test_a_referenced_relation_brings_its_ends():
    carried = carry(target(), set(), {"r2"})
    assert carried.relations == {"r2": "r1"} and set(carried.elements) == {"shop", "orders", "db"}


def test_relations_between_elements_already_there_are_not_copied_unless_referenced():
    web, orders = Element(id="web", name="Web", kind="component"), Element(id="orders", name="Orders", kind="component")
    project = target(web, orders)
    carried = carry(project, {"web", "orders"})  # no relation referenced: the target's model is not changed
    assert carried.relations == {} and project.relations == []


def test_what_does_not_fit_is_dropped():
    filler = [Element(id=f"e{i}", name=f"E{i}", kind="component") for i in range(MAX_ELEMENTS - 1)]
    project = target(*filler)
    carried = carry(project, {"web", "db"})
    assert len(project.elements) == MAX_ELEMENTS
    kept = [source for source in ("db", "web") if source in carried.elements]
    assert len(kept) == 1
    (dropped,) = carried.report()["elements"]["dropped"]
    assert "at most" in dropped["reason"]


def test_a_size_limit_drops_what_would_not_fit():
    project = target()
    db = Element(id="db", name="DB", kind="data")
    limit = projects.document_size(project) + len(db.model_dump_json()) + 20  # room for db only
    carried = carry(project, {"web", "db"}, limit=limit)
    assert list(carried.elements) == ["db"]  # in source order: db first, then web did not fit
    assert carried.report()["elements"]["dropped"] == [{"id": "web", "reason": projects.TOO_LARGE}]
    assert [e.id for e in project.elements] == ["db"] and projects.document_size(project) <= limit


def test_a_project_already_over_the_limit_takes_nothing():
    project = target(Element(id="big", name="Big", kind="component", description="x" * 300))
    carried = carry(project, {"db"}, limit=projects.document_size(project) - 1)
    assert carried.elements == {} and [e.id for e in project.elements] == ["big"]


def test_a_child_whose_parent_was_dropped_is_dropped_too():
    project = target(Element(id="x", name="Shop", kind="actor"))  # the name of the parent is taken
    carried = carry(project, {"orders"})
    assert carried.elements == {}
    reasons = {d["id"]: d["reason"] for d in carried.report()["elements"]["dropped"]}
    assert "already called" in reasons["shop"] and reasons["orders"] == "its parent 'shop' could not be carried"


# ---- the move ----------------------------------------------------------------------------------------------------


def platform() -> Diagram:
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {
        "web": Node(id="web", name="Web", kind="service", ref="web"),
        "orders": Node(id="orders", name="Orders", kind="service", ref="orders"),
        "db": Node(id="db", name="DB", kind="database", ref="db"),
        "free": Node(id="free", name="Frei", kind="service"),
    }
    d.edges = [
        Edge(id="e1", source="web", target="orders", kind="connection", properties={"protocol": "HTTPS"}),
        Edge(id="e2", source="orders", target="db", kind="connection"),
    ]
    return d


@pytest.fixture
def world(store):
    project_service = ProjectService(store, store)
    diagrams = DiagramService(store, project_service)
    models = ModelService(project_service, diagrams)
    project_service.create("Shop", "shop")
    project_service.create("Andere", "other")
    diagrams.create("Plattform", "platform", platform(), "shop")
    models.add_element("shop", "Shop", "system", element_id="shop")
    for name, kind, element_id in (
        ("Web", "component", "web"),
        ("Orders", "component", "orders"),
        ("DB", "data", "db"),
    ):
        models.add_element("shop", name, kind, element_id=element_id)
    models.move_element("shop", "orders", "shop")
    models.add_relation("shop", "communicates", "web", "orders", {"protocol": "HTTPS"})
    models.add_relation("shop", "data_flow", "orders", "db")
    models.link_relation("platform", "e1", "r1")
    models.link_relation("platform", "e2", "r2")
    return project_service, diagrams, models


def refs(diagrams: DiagramService) -> tuple[dict[str, str | None], dict[str, str | None]]:
    d = diagrams.get("platform")
    return {n.id: n.ref for n in d.nodes.values()}, {e.id: e.ref for e in d.edges}


def test_a_move_into_an_empty_project_keeps_every_link(world):
    project_service, diagrams, _ = world
    before = len(diagrams.versions("platform"))
    placed, _, carried = diagrams.move("platform", "other")
    assert placed == "other"
    assert refs(diagrams) == ({"web": "web", "orders": "orders", "db": "db", "free": None}, {"e1": "r1", "e2": "r2"})
    other = project_service.get("other")
    assert sorted(e.id for e in other.elements) == ["db", "orders", "shop", "web"]  # the parent chain came along
    assert [(r.id, r.source, r.target) for r in other.relations] == [("r1", "web", "orders"), ("r2", "orders", "db")]
    assert len(diagrams.versions("platform")) == before  # nothing in the diagram had to change
    assert [c["id"] for c in carried["elements"]["copied"]] == ["shop", "web", "db", "orders"]  # parents first
    assert carried["elements"]["dropped"] == [] and carried["relations"]["dropped"] == []
    assert sorted(e.id for e in project_service.get("shop").elements) == ["db", "orders", "shop", "web"]  # source kept


def test_a_move_relinks_what_got_new_ids(world):
    project_service, diagrams, models = world
    models.add_element("other", "Fremd", "component", element_id="orders")
    models.add_element("other", "Alt", "component")
    models.add_element("other", "Neu", "component")
    models.add_relation("other", "communicates", "alt", "neu")  # r1 is taken in the target
    _, _, carried = diagrams.move("platform", "other")
    nodes, edges = refs(diagrams)
    assert nodes == {"web": "web", "orders": "orders-2", "db": "db", "free": None}
    assert edges == {"e1": "r2", "e2": "r3"}
    other = project_service.get("other")
    assert next(e for e in other.elements if e.id == "orders").name == "Fremd"
    assert next(e for e in other.elements if e.id == "orders-2").name == "Orders"
    assert {"id": "orders", "as": "orders-2"} in carried["elements"]["copied"]
    assert carried["relations"]["copied"] == [{"id": "r1", "as": "r2"}, {"id": "r2", "as": "r3"}]
    labels = [v.label for v in diagrams.versions("platform")[-2:]]
    assert labels == ["Verknüpfungen angepasst (anderes Projekt)", "Verknüpfungen übernommen (anderes Projekt)"]
    assert models.model("other").to_dict()  # the target model is consistent


def test_a_move_maps_to_the_same_elements(world):
    project_service, diagrams, models = world
    models.add_element("other", "web", "component", element_id="web")  # the same element (name case-insensitive)
    _, _, carried = diagrams.move("platform", "other")
    assert refs(diagrams)[0]["web"] == "web"
    assert carried["elements"]["mapped"] == [{"id": "web", "as": "web"}]
    assert diagrams.get("platform").nodes["web"].name == "web"  # the view shows the target element's name
    assert sum(1 for e in project_service.get("other").elements if e.id == "web") == 1


def test_what_does_not_fit_loses_its_links_and_is_reported(world, monkeypatch):
    project_service, diagrams, models = world
    monkeypatch.setattr(projects, "MAX_ELEMENTS", 3)
    _, _, carried = diagrams.move("platform", "other")
    nodes, edges = refs(diagrams)
    assert nodes == {"web": "web", "orders": None, "db": "db", "free": None}  # shop, web, db fit (parents first)
    assert edges == {"e1": None, "e2": None}  # both relations touch orders
    assert [d["id"] for d in carried["elements"]["dropped"]] == ["orders"]
    assert sorted(d["id"] for d in carried["relations"]["dropped"]) == ["r1", "r2"]
    assert len(project_service.get("other").elements) == 3


def test_a_move_within_the_project_reports_nothing(world):
    _, diagrams, _ = world
    _, _, carried = diagrams.move("platform", "shop")
    assert carried is None
    assert refs(diagrams)[0]["orders"] == "orders"


def test_the_move_answer_lists_what_was_carried(store):
    client = TestClient(create_app(store=store))
    for title, project_id in (("Shop", "shop"), ("Andere", "other")):
        assert client.post("/api/projects", json={"title": title, "id": project_id}).status_code == 201
    client.post(
        "/api/diagrams", json={"title": "Plattform", "id": "platform", "template": "aks-database", "project": "shop"}
    )
    client.post("/api/projects/shop/elements", json={"name": "Order Service"})
    service = next(
        i for i, n in client.get("/api/diagrams/platform").json()["diagram"]["nodes"].items() if n["kind"] == "service"
    )
    assert (
        client.post("/api/diagrams/platform/link", json={"view": service, "element": "order-service"}).status_code
        == 200
    )
    r = client.post("/api/projects/shop/diagrams/platform/move", json={"project": "other"})
    assert r.status_code == 200
    assert r.json()["carried"]["elements"]["copied"] == [{"id": "order-service", "as": "order-service"}]
    assert client.get("/api/diagrams/platform").json()["diagram"]["nodes"][service]["ref"] == "order-service"
    within = client.post("/api/projects/other/diagrams/platform/move", json={"position": 0})
    assert within.status_code == 200 and "carried" not in within.json()


def test_hidden_relations_follow_their_new_ids(world):
    _, diagrams, models = world
    models.add_element("other", "Alt", "component")
    models.add_element("other", "Neu", "component")
    models.add_relation("other", "communicates", "alt", "neu")  # r1 is taken in the target
    models.unlink_relation("platform", "e2")
    models.hide_relation("platform", "r2", True)
    _, _, carried = diagrams.move("platform", "other")
    assert diagrams.get("platform").hidden_relations == ["r3"]
    assert {"id": "r2", "as": "r3"} in carried["relations"]["copied"]


def test_a_relation_keeping_its_id_is_linked_again_when_an_end_view_is_relinked(world):
    _, diagrams, models = world
    models.add_element(
        "other", "Fremd", "component", element_id="orders"
    )  # orders moves to a new id, r1/r2 keep theirs
    _, _, carried = diagrams.move("platform", "other")
    nodes, edges = refs(diagrams)
    assert nodes["orders"] == "orders-2" and edges == {"e1": "r1", "e2": "r2"}
    assert carried["relations"]["copied"] == [{"id": "r1", "as": "r1"}, {"id": "r2", "as": "r2"}]


# ---- M26 bounds and size guard (#212, ADR-0153) -------------------------------------------------------------------


def test_an_element_over_the_bounds_is_dropped_not_refused():
    long_name = Element(id="long", name="L" * (projects.MAX_ELEMENT_NAME + 1), kind="component")  # stored before M26
    long_text = Element(
        id="text", name="Text", kind="component", description="d" * (projects.MAX_ELEMENT_DESCRIPTION + 1)
    )
    project = target()
    carried = projects.carry_model(project, [long_name, long_text], [], {"long", "text"}, set())
    reasons = {d["id"]: d["reason"] for d in carried.report()["elements"]["dropped"]}
    assert set(reasons) == {"long", "text"} and all("too long" in r for r in reasons.values())
    assert project.elements == []


def test_the_size_guard_drops_what_does_not_fit_per_element(world, monkeypatch):
    project_service, diagrams, _ = world
    budget = projects.document_size(project_service.get("other")) + 200  # room for about one element
    monkeypatch.setattr(projects, "MAX_PROJECT_BYTES", budget)
    _, _, carried = diagrams.move("platform", "other")  # never refused: what does not fit is dropped
    kept = carried["elements"]["copied"]
    assert 1 <= len(kept) < 4
    assert {d["reason"] for d in carried["elements"]["dropped"]} == {projects.TOO_LARGE}
    assert projects.document_size(project_service.get("other")) <= projects.MAX_MEMBERSHIP_BYTES
    nodes, _ = refs(diagrams)
    assert {ref for ref in nodes.values() if ref} == {k["as"] for k in kept} & {"web", "orders", "db"}


# ---- review of M28: kept links re-checked, failures lose nothing, the cost of the size guard ----------------------


def test_a_kept_link_whose_element_vanished_before_the_move_is_dropped(world, monkeypatch):
    project_service, diagrams, models = world
    original = ProjectService.place

    def place_after_a_delete(self, diagram_id, *args, **kwargs):  # another editor removes a copy before the membership
        if not getattr(self, "_raced", False):
            self._raced = True
            project_service.apply("other", "remove_element", {"element": "db"}, internal=True)
        return original(self, diagram_id, *args, **kwargs)

    monkeypatch.setattr(ProjectService, "place", place_after_a_delete)
    _, _, carried = diagrams.move("platform", "other")
    nodes, edges = refs(diagrams)
    assert nodes["db"] is None and edges["e2"] is None  # never a ref a later element "db" could re-attach to
    assert nodes["orders"] == "orders" and edges["e1"] == "r1"
    assert "db" in [d["id"] for d in carried["elements"]["dropped"]]
    assert "r2" in [d["id"] for d in carried["relations"]["dropped"]]


def test_sync_clears_dangling_element_refs(world):
    project_service, diagrams, models = world
    project_service.apply("shop", "remove_element", {"element": "db"}, internal=True)  # around ModelService: dangling
    assert refs(diagrams)[0]["db"] == "db"
    report = models.sync("shop")
    assert report.changed == ["platform"] and refs(diagrams)[0]["db"] is None


def test_a_failed_placement_loses_nothing(world, monkeypatch):
    project_service, diagrams, models = world
    models.add_element("other", "Fremd", "component", element_id="orders")  # orders would be relinked to orders-2
    before_refs = refs(diagrams)
    before_model = project_service.get("other").model_dump()

    def gone(self, *args, **kwargs):
        raise projects.ProjectError("Unknown section 'backend' (removed meanwhile).")

    monkeypatch.setattr(ProjectService, "place", gone)
    with pytest.raises(projects.ProjectError, match="removed meanwhile"):
        diagrams.move("platform", "other")
    assert refs(diagrams) == before_refs  # the links are back
    after = project_service.get("other").model_dump()
    assert after["elements"] == before_model["elements"] and after.get("relations") == before_model.get("relations")
    assert project_service.holder("platform").project.id == "shop"


def test_the_size_guard_measures_incrementally(monkeypatch):
    calls = []
    real = projects.document_size
    monkeypatch.setattr(projects, "document_size", lambda p: calls.append(1) or real(p))
    elements = [Element(id=f"e{i}", name=f"E{i}", kind="component") for i in range(50)]
    project = target()
    projects.carry_model(project, elements, [], {e.id for e in elements}, set(), limit=10_000_000)
    assert len(project.elements) == 50 and len(calls) <= 2  # one measure before, one after; none per element


# ---- second review: compensation only when nothing moved, and never at the cost of user data ---------------------


def test_a_placement_that_fails_after_its_membership_counts_as_done(world, monkeypatch):
    project_service, diagrams, models = world
    models.add_element("other", "Fremd", "component", element_id="orders")  # orders -> orders-2
    original = ProjectService.place

    def fails_after_writing(self, diagram_id, *args, **kwargs):
        original(self, diagram_id, *args, **kwargs)
        raise ConnectionError("the source membership could not be removed")  # e.g. _remove_member, settle_location

    monkeypatch.setattr(ProjectService, "place", fails_after_writing)
    with pytest.raises(ConnectionError):  # the failure is still reported
        diagrams.move("platform", "other")
    assert project_service.get("other").member("platform") is not None  # but the move is done
    nodes, edges = refs(diagrams)
    assert nodes["orders"] == "orders-2" and edges == {"e1": "r1", "e2": "r2"}  # relinked against the target
    assert any(e.id == "orders-2" for e in project_service.get("other").elements)  # never uncarried


def test_uncarry_leaves_a_copy_that_changed_or_is_used(world, monkeypatch):
    project_service, diagrams, models = world
    original = ProjectService.place

    def busy_target(self, diagram_id, *args, **kwargs):
        if diagram_id != "platform":
            return original(self, diagram_id, *args, **kwargs)
        # meanwhile another editor renames one copy and links a second diagram to another
        models.update_element("other", "web", name="Web neu")
        other = Diagram(id="other-view", title="Andere Sicht")
        other.nodes = {"x": Node(id="x", name="DB", kind="database")}
        diagrams.create("Andere Sicht", "other-view", other, "other")
        models.link("other-view", "x", "db")
        raise projects.ProjectError("Unknown section (removed meanwhile).")

    monkeypatch.setattr(ProjectService, "place", busy_target)
    with pytest.raises(projects.ProjectError):
        diagrams.move("platform", "other")
    left = {e.id for e in project_service.get("other").elements}
    assert {"web", "db"} <= left  # changed or used: left in the target
    assert "orders" not in left and "shop" not in left  # untouched copies only are taken back


def test_restored_links_follow_the_source_as_it_is_now(world, monkeypatch):
    project_service, diagrams, models = world
    models.add_element("other", "Fremd", "component", element_id="orders")  # so the move unlinks orders first

    def gone(self, *args, **kwargs):
        models.remove_element("shop", "orders")  # the source element was removed while the move ran
        raise projects.ProjectError("Unknown section (removed meanwhile).")

    monkeypatch.setattr(ProjectService, "place", gone)
    with pytest.raises(projects.ProjectError):
        diagrams.move("platform", "other")
    assert refs(diagrams)[0]["orders"] is None  # never relinked to an element that no longer exists


def test_carry_maps_stored_names_with_control_characters():
    project = target(Element(id="web", name="W\neb", kind="component"))  # stored before #103
    carried = projects.carry_model(project, [Element(id="web", name="W eb", kind="component")], [], {"web"}, set())
    assert carried.mapped_elements == [("web", "web")] and len(project.elements) == 1


def test_a_normalised_name_counts_as_taken():
    project = target(Element(id="a", name="Order\nService", kind="component"))  # stored before #103
    with pytest.raises(projects.ProjectError, match="already called"):
        projects.apply(project, "add_element", {"name": "Order Service"})
