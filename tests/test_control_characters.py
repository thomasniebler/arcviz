"""Control characters in names, titles and labels (#103, ADR-0160): line breaks and tabs become one space, other C0/C1
control characters are dropped, in the operations and again when rendering stored data. Never an error."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import mermaid, operations, projects
from arcviz.model import Diagram, Edge, Node, single_line
from arcviz.server import create_app
from arcviz.store import FileStore

RAW = "a\r\nb\tc\x00d\x85e\x7ff g"
CLEAN = "a b cdef g"


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        (RAW, CLEAN),
        ("one\n\n\ntwo", "one two"),  # a run of line breaks is one space
        ("x \n y", "x   y"),  # spaces themselves stay
        ("\x01\x1b[31mred\x9b", "[31mred"),  # escape sequences lose their control characters
        ("plain «text» ünï", "plain «text» ünï"),
        ("", ""),
    ],
)
def test_single_line(raw, clean):
    assert single_line(raw) == clean


def ok(d: Diagram, op: str, **args):
    return operations.apply(d, op, args)["result"]


def test_deployment_operations_normalise_names_titles_and_labels():
    d = Diagram(id="d", title="D")
    ok(d, "add_node", name=f"  {RAW}\n", kind="vnet", id="net")
    ok(d, "add_node", name="db", kind="database", id="db")
    ok(d, "add_edge", source="net", target="db", label=f"uses\n{RAW}")
    assert d.nodes["net"].name == CLEAN
    assert d.edges[0].label == f"uses {CLEAN}"
    ok(d, "update_node", id="db", name="orders\r\ndb")
    ok(d, "update_edge", id=d.edges[0].id, label="sql\t/tls")
    ok(d, "rename_diagram", title="Prod\nview\x07")
    assert (d.nodes["db"].name, d.edges[0].label, d.title) == ("orders db", "sql /tls", "Prod view")


def test_a_name_of_control_characters_only_is_empty():
    d = Diagram(id="d", title="D")
    with pytest.raises(operations.OperationError, match="name"):
        operations.apply(d, "add_node", {"name": "\n\t\x00", "kind": "service"})
    assert d.nodes == {}


def test_descriptions_keep_their_line_breaks():
    d = Diagram(id="d", title="D")
    ok(d, "add_node", name="svc", kind="service", id="svc", description="line one\nline two")
    assert d.nodes["svc"].description == "line one\nline two"


def test_batches_normalise_every_step():
    d = Diagram(id="d", title="D")
    ok(d, "apply_batch", operations=[{"op": "add_node", "args": {"name": "a\nb", "kind": "service", "id": "ab"}}])
    assert d.nodes["ab"].name == "a b"


def test_activity_guards_are_normalised():
    d = Diagram(id="a", title="A", type="activity")
    ok(d, "add_node", name="start", kind="initial_node", id="start")
    ok(d, "add_node", name="do\nit", kind="opaque_action", id="act")
    ok(d, "add_edge", source="start", target="act", guard="ok\r\nnow")
    assert d.nodes["act"].name == "do it" and d.edges[0].properties["guard"] == "ok now"


def test_sequence_operations_normalise_names_and_guards():
    d = Diagram(id="s", title="S", type="sequence")
    ok(d, "add_lifeline", name="Cli\nent", type="Order\tService", id="client")
    ok(d, "add_lifeline", name="Server", id="server")
    ok(d, "add_message", sender="client", receiver="server", name="order(\nid)")
    ok(d, "add_combined_fragment", operator="opt", covered=["client", "server"], guard="x\n> 1")
    ok(d, "add_interaction_use", covered=["client", "server"], refers_to="Log\nin")
    interaction = d.interaction
    assert interaction is not None
    client = next(lf for lf in interaction.lifelines if lf.id == "client")
    assert (client.name, client.type) == ("Cli ent", "Order Service")
    texts = repr(interaction.model_dump())
    assert "order( id)" in texts and "x > 1" in texts and "Log in" in texts and "\\n" not in texts


def test_project_operations_normalise_titles_and_names():
    project = projects.new_project("Shop\nproject", "p")
    assert project.title == "Shop project"
    projects.apply(project, "rename_project", {"title": "Shop\r\n2"})
    projects.apply(project, "add_section", {"title": "Back\nend"})
    projects.apply(project, "add_element", {"name": "Order\tservice", "kind": "component"})
    projects.apply(project, "add_element", {"name": "Store", "kind": "data"})
    projects.apply(project, "update_element", {"element": "store", "name": "Order\nstore"})
    assert project.title == "Shop 2"
    assert [s.title for s in project.sections] == ["Back end"]
    assert sorted(e.name for e in project.elements) == ["Order service", "Order store"]
    projects.apply(project, "rename_section", {"section": project.sections[0].id, "title": "Front\x00end"})
    assert project.sections[0].title == "Frontend"


def test_stored_control_characters_still_render():
    # stored before #103 (or written around the operations): the renderer applies the same normalisation
    d = Diagram(id="d", title="D")
    d.nodes["a"] = Node(id="a", name="first\nline\x00", kind="service")
    d.nodes["b"] = Node(id="b", name="b", kind="database")
    d.edges.append(Edge(id="e1", source="a", target="b", label="x\r\ny"))
    src = mermaid.render(d)
    assert 'n_a["«service»<br/>first line"]' in src
    assert '|"x y"|' in src
    assert mermaid.escape_text(RAW) == CLEAN
    assert all(ch >= " " for line in src.splitlines() for ch in line)


def test_http_create_and_operations_normalise(tmp_path):
    client = TestClient(create_app(store=FileStore(tmp_path)))
    created = client.post("/api/diagrams", json={"title": "New\ndiagram"}).json()
    diagram_id = created["diagram"]["id"]
    assert created["diagram"]["title"] == "New diagram"
    answer = client.post(
        f"/api/diagrams/{diagram_id}/ops", json={"op": "add_node", "args": {"name": "a\nb", "kind": "service"}}
    )
    assert answer.status_code == 200
    assert [n["name"] for n in answer.json()["diagram"]["nodes"].values()] == ["a b"]


# ---- review of M28: every renderer, the importer, stored data, new titles -----------------------------------------


def test_the_sequence_svg_never_carries_control_characters():
    import xml.etree.ElementTree as ET

    from arcviz import sequence_svg
    from arcviz.sequence import Interaction, Lifeline

    d = Diagram(id="s", title="S\x01eq", type="sequence")
    d.interaction = Interaction(
        lifelines=[Lifeline(id="a", name="A\x00\x1b", type="T\x07"), Lifeline(id="b", name="B\nline")]
    )
    svg = sequence_svg.render(d)  # stored before #103 (or imported): must stay well-formed XML
    ET.fromstring(svg)
    assert "\x00" not in svg and "\x1b" not in svg and "B line" in svg
    assert sequence_svg.escape("x\x01y\nz") == "xy z"


def test_imported_documents_are_normalised():
    from arcviz import exchange

    d = Diagram(id="d", title="Im\nport")
    d.nodes["a"] = Node(id="a", name="first\x00\nline", kind="service", description="keeps\nits lines")
    d.nodes["b"] = Node(id="b", name="b", kind="database")
    d.edges.append(Edge(id="e1", source="a", target="b", label="x\ty"))
    parsed = exchange.parse_document(exchange.export_document(d))
    assert (parsed.title, parsed.nodes["a"].name, parsed.edges[0].label) == ("Im port", "first line", "x y")
    assert parsed.nodes["a"].description == "keeps\nits lines"


def test_a_stored_name_with_control_characters_is_not_a_rename():
    d = Diagram(id="d", title="D")
    d.nodes["a"] = Node(id="a", name="Order\nService", kind="service", ref="order-service")  # stored before #103
    ok(d, "update_node", id="a", name="Order\nService", description="unchanged name, new note")
    assert d.nodes["a"].name == "Order Service"
    with pytest.raises(operations.OperationError, match="represents the project element"):
        operations.apply(d, "update_node", {"id": "a", "name": "Other"})


def test_a_stored_lifeline_name_with_control_characters_is_not_a_rename():
    from arcviz.sequence import Interaction, Lifeline

    d = Diagram(id="s", title="S", type="sequence")
    d.interaction = Interaction(lifelines=[Lifeline(id="a", name="Order\tService", ref="order-service")])
    ok(d, "update_lifeline", id="a", name="Order\tService", description="note")
    assert d.interaction.lifelines[0].name == "Order Service"


def test_stored_element_names_converge_with_their_views(store):
    from arcviz import project_review
    from arcviz.services import DiagramService, ModelService, ProjectService

    project_service = ProjectService(store, store)
    diagrams = DiagramService(store, project_service)
    models = ModelService(project_service, diagrams)
    project_service.create("Shop", "shop")
    d = Diagram(id="platform", title="Plattform")
    d.nodes["svc"] = Node(id="svc", name="Order Service", kind="service", ref="order-service")
    diagrams.create("Plattform", "platform", d, "shop")
    models.add_element("shop", "Order Service", "component", element_id="order-service")

    def store_raw_name(project):  # an element name stored before #103
        project.elements[0].name = "Order\nService"

    project_service._update("shop", store_raw_name)
    project, members = project_service.load_members("shop")
    snapshot = project_review.ProjectSnapshot(project, members)
    assert [f for f in project_review.element_name_drift(snapshot)] == []  # the views show it normalised
    before = len(diagrams.versions("platform"))
    models.sync("shop")
    assert project_service.get("shop").elements[0].name == "Order Service"  # sync normalises the element itself
    assert len(diagrams.versions("platform")) == before


@pytest.mark.parametrize(
    ("title", "status", "stored"), [("\n\t", 400, None), ("New\n", 201, "New"), (" a\nb ", 201, "a b")]
)
def test_new_titles_are_stripped_and_never_empty(tmp_path, title, status, stored):
    client = TestClient(create_app(store=FileStore(tmp_path)))
    r = client.post("/api/diagrams", json={"title": title, "id": "new"})
    assert r.status_code == status, r.text
    if stored is not None:
        assert r.json()["diagram"]["title"] == stored


def test_sync_normalises_names_in_one_project_write(store):
    from arcviz.services import DiagramService, ModelService, ProjectService

    project_service = ProjectService(store, store)
    models = ModelService(project_service, DiagramService(store, project_service))
    project_service.create("Shop", "shop")
    for name in ("A", "B", "C"):
        models.add_element("shop", name, "component")

    def raw(project):
        for element in project.elements:
            element.name = element.name + "\n"

    project_service._update("shop", raw)
    writes = []
    original = store.commit_project
    store.commit_project = lambda project, revision: writes.append(1) or original(project, revision)
    models.sync("shop")
    assert [e.name for e in project_service.get("shop").elements] == ["A", "B", "C"] and len(writes) == 1
