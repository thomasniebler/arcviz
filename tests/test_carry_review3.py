"""Third review of M28 (#153, #103): compensation reads fresh state, never a request's cached copy; a definite answer
from the target decides; a finished move leaves no source membership; the import keeps a caller's title; activity
guards in `properties` are normalised on import."""

from __future__ import annotations

import pytest

from arcviz import exchange, projects
from arcviz.model import Diagram, Edge, Node
from arcviz.services import ServiceRoot
from arcviz.services.access import ProjectNotFound
from arcviz.workspaces import Identity

ADA = Identity("ada", "Ada", "ada@example.org")


def platform() -> Diagram:
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {
        "web": Node(id="web", name="Web", kind="service", ref="web"),
        "orders": Node(id="orders", name="Orders", kind="service", ref="orders"),
    }
    d.edges = [Edge(id="e1", source="web", target="orders", kind="connection")]
    return d


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    root.users.seen(ADA)
    ada = root.for_actor(ADA)
    ada.projects.create("Shop", "shop")
    ada.projects.create("Andere", "other")
    ada.diagrams.create("Plattform", "platform", platform(), "shop")
    ada.models.add_element("shop", "Web", element_id="web")
    ada.models.add_element("shop", "Orders", element_id="orders")
    ada.models.add_element("other", "Fremd", element_id="orders")  # orders is copied as orders-2 and relinked
    return root


def request(root):
    """The services of one ordinary HTTP request: its guard caches the projects it reads (#166)."""
    return root.for_actor(ADA, per_request=True)


def refs(root) -> dict[str, str | None]:
    return {n.id: n.ref for n in root.for_actor(ADA).diagrams.get("platform").nodes.values()}


def land_then_raise(store, monkeypatch, project_id: str, when):
    """The next commit of `project_id` matching `when(project)` lands, then raises (an ambiguous write)."""
    original = store.commit_project
    state = {"done": False}

    def commit(project, revision):
        result = original(project, revision)
        if not state["done"] and project.id == project_id and when(project):
            state["done"] = True
            raise ConnectionError("the answer was lost")
        return result

    monkeypatch.setattr(store, "commit_project", commit)


def test_a_membership_that_landed_is_seen_through_a_cached_request(root, store, monkeypatch):
    land_then_raise(store, monkeypatch, "other", lambda p: p.member("platform") is not None)
    with pytest.raises(ConnectionError):
        request(root).diagrams.move("platform", "other")
    ada = root.for_actor(ADA)
    assert ada.projects.get("other").member("platform") is not None
    assert refs(root) == {"web": "web", "orders": "orders-2"}  # finished against the target, never restored
    assert {e.id for e in ada.projects.get("other").elements} >= {"web", "orders-2"}  # never uncarried
    assert ada.projects.get("shop").member("platform") is None  # the source membership is gone too (item 5)


def test_restored_links_read_the_source_fresh(root, monkeypatch):
    from arcviz.services import ProjectService

    original = ProjectService.place

    def remove_then_fail(self, diagram_id, *args, **kwargs):
        if diagram_id == "platform":
            root.for_actor(ADA).models.remove_element("shop", "orders")  # another request, after this one read shop
            raise projects.ProjectError("Unknown section (removed meanwhile).")
        return original(self, diagram_id, *args, **kwargs)

    monkeypatch.setattr(ProjectService, "place", remove_then_fail)
    with pytest.raises(projects.ProjectError):
        request(root).diagrams.move("platform", "other")
    assert refs(root)["orders"] is None  # the cached source still had "orders": never relinked to it


def test_uncarry_reads_the_target_fresh(root, monkeypatch):
    from arcviz.services import ProjectService

    original = ProjectService.place

    def link_then_fail(self, diagram_id, *args, **kwargs):
        if diagram_id == "platform":
            other = root.for_actor(ADA)  # another request links a diagram in the target to the copy of "web"
            view = Diagram(id="view", title="Sicht")
            view.nodes = {"x": Node(id="x", name="Web", kind="service")}
            other.diagrams.create("Sicht", "view", view, "other")
            other.models.link("view", "x", "web")
            raise projects.ProjectError("Unknown section (removed meanwhile).")
        return original(self, diagram_id, *args, **kwargs)

    monkeypatch.setattr(ProjectService, "place", link_then_fail)
    with pytest.raises(projects.ProjectError):
        request(root).diagrams.move("platform", "other")
    assert any(e.id == "web" for e in root.for_actor(ADA).projects.get("other").elements)  # used: left in place


def test_a_target_that_is_definitely_gone_restores_the_links(root, monkeypatch):
    from arcviz.services import ProjectService

    original_get = ProjectService.get
    failed = {"place": False}

    def fail(self, diagram_id, *args, **kwargs):
        failed["place"] = True
        raise projects.ProjectError("Unknown section (removed meanwhile).")

    def gone_after_the_failure(self, project_id):  # the re-read gets a definite answer: the target is gone
        if project_id == "other" and failed["place"]:
            raise ProjectNotFound("Project 'other' not found.")
        return original_get(self, project_id)

    monkeypatch.setattr(ProjectService, "place", fail)
    monkeypatch.setattr(ProjectService, "get", gone_after_the_failure)
    with pytest.raises(projects.ProjectError):
        request(root).diagrams.move("platform", "other")
    assert refs(root) == {"web": "web", "orders": "orders"}  # certainly not moved: the links are back


def test_the_callers_title_wins_over_an_empty_document_title(root):
    ada = root.for_actor(ADA)
    created = ada.diagrams.import_new(Diagram(id="blank", title=""), "Mein Titel", project="shop")
    assert ada.diagrams.get(created).title == "Mein Titel"
    untitled = ada.diagrams.import_new(Diagram(id="blank", title=" "), "", project="shop")
    assert ada.diagrams.get(untitled).title == "Unbenanntes Diagramm"


def test_activity_guards_in_properties_are_normalised_on_import():
    d = Diagram(id="a", title="A", type="activity")
    d.nodes = {
        "start": Node(id="start", name="start", kind="initial_node"),
        "act": Node(id="act", name="act", kind="opaque_action"),
    }
    d.edges = [Edge(id="e1", source="start", target="act", kind="control_flow", properties={"guard": "x\ny"})]
    parsed = exchange.parse_document(exchange.export_document(d))
    assert parsed.edges[0].properties["guard"] == "x y"


# ---- Codex review of 8be3eb8 --------------------------------------------------------------------------------------


def test_a_kept_relation_link_that_does_not_fit_is_dropped(root):
    """An older version's edge web -> orders still names r1, which the model meanwhile connects web -> db; an empty
    target copies r1 under its id, so the link would be kept: it must be checked like a relinked one."""
    from arcviz.project_model import Element, Relation

    d = platform()
    d.nodes["db"] = Node(id="db", name="DB", kind="database", ref="db")
    d.edges = [Edge(id="e1", source="web", target="orders", kind="connection", ref="r1")]
    document = exchange.export_document(d)
    document["model"] = {
        "elements": [
            e.model_dump(mode="json")
            for e in (
                Element(id="web", name="Web", kind="component"),
                Element(id="orders", name="Orders", kind="component"),
                Element(id="db", name="DB", kind="data"),
            )
        ],
        "relations": [Relation(id="r1", kind="communicates", source="web", target="db").model_dump(mode="json")],
    }
    model = exchange.parse_document_model(document)
    ada = root.for_actor(ADA)
    ada.projects.create("Leer", "empty")
    created, carried = ada.diagrams.import_carrying(exchange.parse_document(document), model, "Alt", project="empty")
    assert ada.diagrams.get(created).edges[0].ref is None
    assert "r1" in [d["id"] for d in carried["relations"]["dropped"]]


def test_a_kept_element_whose_kind_changed_meanwhile_is_unlinked(root, monkeypatch):
    from arcviz.services import ProjectService

    original = ProjectService.place

    def rekind_then_place(self, diagram_id, *args, **kwargs):
        if diagram_id == "platform":  # another editor makes the copied "web" an actor, which a service cannot show
            root.for_actor(ADA).projects.apply(
                "other", "update_element", {"element": "web", "kind": "actor"}, internal=True
            )
        return original(self, diagram_id, *args, **kwargs)

    monkeypatch.setattr(ProjectService, "place", rekind_then_place)
    _, _, carried = request(root).diagrams.move("platform", "other")
    assert refs(root)["web"] is None
    assert "web" in [d["id"] for d in carried["elements"]["dropped"]]


def test_uncarry_leaves_a_copy_whose_description_changed(root, monkeypatch):
    from arcviz.services import ProjectService

    def describe_then_fail(self, diagram_id, *args, **kwargs):
        ada = root.for_actor(ADA)
        ada.projects.apply(
            "other", "update_element", {"element": "web", "description": "neu beschrieben"}, internal=True
        )
        raise projects.ProjectError("Unknown section (removed meanwhile).")

    monkeypatch.setattr(ProjectService, "place", describe_then_fail)
    with pytest.raises(projects.ProjectError):
        request(root).diagrams.move("platform", "other")
    web = next(e for e in root.for_actor(ADA).projects.get("other").elements if e.id == "web")
    assert web.description == "neu beschrieben"  # an edit made meanwhile is never lost
