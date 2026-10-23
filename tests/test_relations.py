"""Model relations in the shared project model (spec: docs/architecture/specs/model-relations.md, ADR-0135, ADR-0136).

Project side: `Project.relations`, the kind rules, the internal project operations, the model service and the REST
routes. The links from view connections are in `tests/test_relation_links.py`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import project_model, projects
from arcviz.project_model import MAX_RELATIONS, RELATION_KINDS, Relation, relation_problem
from arcviz.projects import Project, ProjectError
from arcviz.server import create_app
from arcviz.services import DiagramService, ModelService, ProjectService


def shop() -> Project:
    project = Project(id="shop", title="Shop")
    for name, kind in (
        ("Web", "component"),
        ("Orders", "component"),
        ("DB", "data"),
        ("Cluster", "node"),
        ("Kunde", "actor"),
    ):
        projects.apply(project, "add_element", {"name": name, "kind": kind})
    return project


def add(project: Project, kind: str, source: str, target: str, **extra) -> dict:
    return projects.apply(project, "add_relation", {"kind": kind, "source": source, "target": target, **extra})[
        "result"
    ]


# ---- domain ------------------------------------------------------------------------------------------------


def test_relation_kinds_are_a_closed_set():
    assert RELATION_KINDS == ("communicates", "deployed_on", "data_flow")


@pytest.mark.parametrize(
    ("kind", "source", "target", "ok"),
    [
        ("communicates", "actor", "system", True),
        ("communicates", "node", "node", True),
        ("communicates", "data", "component", True),
        ("deployed_on", "component", "node", True),
        ("deployed_on", "node", "node", True),
        ("deployed_on", "component", "component", False),
        ("deployed_on", "actor", "node", False),
        ("data_flow", "component", "data", True),
        ("data_flow", "node", "data", False),
        ("data_flow", "data", "node", False),
        ("bogus", "component", "component", False),
    ],
)
def test_kind_rules(kind, source, target, ok):
    assert (relation_problem(kind, source, target) is None) is ok


def test_unset_relations_are_omitted_from_project_dumps():
    project = shop()
    assert "relations" not in project.model_dump()
    assert '"relations"' not in project.model_dump_json()
    add(project, "communicates", "web", "orders")
    assert project.model_dump()["relations"][0]["kind"] == "communicates"
    assert Project.model_validate(project.model_dump(mode="json")).relations == project.relations


# ---- project operations ------------------------------------------------------------------------------------


def test_add_relation_resolves_ends_and_numbers_ids():
    project = shop()
    first = add(project, "communicates", "Web", "orders", properties={"protocol": "HTTPS", "port": 443})
    assert first == {
        "id": "r1",
        "kind": "communicates",
        "source": "web",
        "target": "orders",
        "properties": {"protocol": "HTTPS", "port": "443"},
        "description": "",
    }
    second = add(project, "deployed_on", "orders", "cluster", description="läuft im Cluster", id="orders-on-cluster")
    assert second["id"] == "orders-on-cluster"
    assert add(project, "data_flow", "orders", "db")["id"] == "r2"  # generated numbers count generated ids only


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"kind": "deployed_on", "source": "web", "target": "orders"}, "deployed_on"),
        ({"kind": "talks", "source": "web", "target": "orders"}, "Unknown relation kind"),
        ({"kind": "communicates", "source": "web", "target": "ghost"}, "Unknown element 'ghost'"),
        ({"kind": "communicates", "source": "web", "target": "web"}, "itself"),
        ({"kind": "communicates", "source": "web", "target": "orders", "properties": {"colour": "red"}}, "colour"),
        ({"kind": "communicates", "source": "web", "target": "orders", "properties": {"mode": "maybe"}}, "mode"),
        ({"kind": "deployed_on", "source": "orders", "target": "cluster", "properties": {"port": "1"}}, "port"),
        ({"kind": "communicates", "source": "web", "target": "orders", "properties": {"port": "9" * 65}}, "64"),
        ({"kind": "communicates", "source": "web", "target": "orders", "description": "x" * 301}, "300"),
        ({"kind": "communicates", "source": "web", "target": "orders", "id": "Not A Slug"}, "Invalid relation id"),
    ],
)
def test_add_relation_refuses_invalid_input_and_changes_nothing(args, message):
    project = shop()
    before = project.model_dump()
    with pytest.raises(ProjectError, match=message):
        projects.apply(project, "add_relation", args)
    assert project.model_dump() == before


def test_duplicates_are_refused_communicates_in_either_orientation():
    project = shop()
    add(project, "communicates", "web", "orders")
    with pytest.raises(ProjectError, match="already"):
        add(project, "communicates", "orders", "web")
    add(project, "data_flow", "orders", "db")
    add(project, "data_flow", "db", "orders")  # a flow back is another relation
    with pytest.raises(ProjectError, match="already"):
        add(project, "data_flow", "orders", "db")


def test_at_most_max_relations():
    project = Project(id="big", title="Big")
    for n in range(40):
        projects.apply(project, "add_element", {"name": f"E{n}"})
    pairs = [(a, b) for a in range(40) for b in range(a + 1, 40)][:MAX_RELATIONS]
    project.relations = [
        Relation(id=f"r{i + 1}", kind="communicates", source=f"e{a}", target=f"e{b}") for i, (a, b) in enumerate(pairs)
    ]
    assert project.integrity_errors() == []
    with pytest.raises(ProjectError, match=str(MAX_RELATIONS)):
        add(project, "communicates", "e0", "e39")


def test_update_relation_changes_fields_and_checks_the_rules():
    project = shop()
    add(project, "communicates", "web", "orders", properties={"protocol": "HTTP", "port": "80"})
    result = projects.apply(
        project,
        "update_relation",
        {"relation": "r1", "properties": {"protocol": "HTTPS"}, "remove_properties": ["port"], "description": "TLS"},
    )["result"]
    assert result["relation"]["properties"] == {"protocol": "HTTPS"}
    assert result["ends_changed"] is False
    result = projects.apply(project, "update_relation", {"relation": "r1", "target": "DB"})["result"]
    assert result["relation"]["target"] == "db" and result["ends_changed"] is True
    with pytest.raises(ProjectError, match="deployed_on"):
        projects.apply(project, "update_relation", {"relation": "r1", "kind": "deployed_on"})
    with pytest.raises(ProjectError, match="Unknown relation 'r9'"):
        projects.apply(project, "update_relation", {"relation": "r9", "description": "x"})
    # a kind change drops the properties the new kind does not allow only when asked to: refused otherwise
    add(project, "communicates", "orders", "cluster", properties={"protocol": "TCP"})
    with pytest.raises(ProjectError, match="protocol"):
        projects.apply(project, "update_relation", {"relation": "r2", "kind": "deployed_on"})
    projects.apply(
        project, "update_relation", {"relation": "r2", "kind": "deployed_on", "remove_properties": ["protocol"]}
    )
    assert project.relations[1].kind == "deployed_on"


def test_remove_relation():
    project = shop()
    add(project, "communicates", "web", "orders")
    result = projects.apply(project, "remove_relation", {"relation": "r1"})["result"]
    assert result["relation"]["id"] == "r1" and project.relations == []


def test_removing_an_element_removes_its_relations():
    project = shop()
    add(project, "communicates", "web", "orders")
    add(project, "deployed_on", "orders", "cluster")
    add(project, "communicates", "kunde", "web")
    result = projects.apply(project, "remove_element", {"element": "orders"})["result"]
    assert result["removed_relations"] == ["r1", "r2"]
    assert [r.id for r in project.relations] == ["r3"]


def test_an_element_kind_change_that_breaks_a_relation_is_refused():
    project = shop()
    add(project, "deployed_on", "orders", "cluster")
    with pytest.raises(ProjectError, match="r1"):
        projects.apply(project, "update_element", {"element": "cluster", "kind": "component"})
    assert project.find_element("cluster").kind == "node"
    projects.apply(project, "update_element", {"element": "orders", "kind": "subsystem"})  # still allowed


def test_relation_operations_are_internal():
    for op in ("add_relation", "update_relation", "remove_relation"):
        assert op in projects.INTERNAL_OPERATIONS and op not in projects.PUBLIC_OPERATIONS


def test_integrity_reports_broken_relations():
    project = shop()
    project.relations = [
        Relation(id="r1", kind="communicates", source="web", target="ghost"),
        Relation(id="r1", kind="deployed_on", source="web", target="orders"),
        Relation(id="BAD", kind="nope", source="web", target="web"),
    ]
    text = "\n".join(project.integrity_errors())
    for part in (
        "unknown target 'ghost'",
        "duplicate relation id 'r1'",
        "invalid relation id 'BAD'",
        "unknown kind 'nope'",
        "itself",
    ):
        assert part in text
    assert "deployed_on" in text


def test_find_relation_by_id():
    project = shop()
    add(project, "communicates", "web", "orders")
    assert project_model.find_relation(project.relations, "r1").id == "r1"
    assert project_model.find_relation(project.relations, " R1 ").id == "r1"
    assert project_model.find_relation(project.relations, "r2") is None


# ---- the service --------------------------------------------------------------------------------------------


@pytest.fixture
def models(store):
    project_service = ProjectService(store, store)
    diagrams = DiagramService(store, project_service)
    project_service.create("Shop", "shop")
    service = ModelService(project_service, diagrams)
    for name, kind in (("Web", "component"), ("Orders", "component"), ("Cluster", "node")):
        service.add_element("shop", name, kind)
    return service


def test_service_crud(models):
    relation = models.add_relation("shop", "communicates", "web", "orders", {"protocol": "HTTPS"}, "Bestellungen")
    assert relation.id == "r1"
    assert [r.id for r in models.relations("shop")] == ["r1"]
    view = models.model("shop").to_dict()["relations"][0]
    assert view["source"] == "web" and view["usages"] == [] and view["description"] == "Bestellungen"
    change = models.update_relation("shop", "r1", properties={"port": "443"})
    assert change.relation.properties == {"protocol": "HTTPS", "port": "443"}
    removal = models.remove_relation("shop", "r1")
    assert removal.relation["id"] == "r1" and models.relations("shop") == []


def test_service_removing_an_element_removes_its_relations(models):
    models.add_relation("shop", "deployed_on", "orders", "cluster")
    removal = models.remove_element("shop", "cluster")
    assert removal.element["removed_relations"] == ["r1"]
    assert models.relations("shop") == []


# ---- REST ----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(store):
    client = TestClient(create_app(store=store))
    assert client.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    for name, kind in (("Web", "component"), ("Orders", "component"), ("Cluster", "node")):
        assert client.post("/api/projects/shop/elements", json={"name": name, "kind": kind}).status_code == 201
    return client


def test_rest_relations_crud(client):
    r = client.post(
        "/api/projects/shop/relations",
        json={"kind": "communicates", "source": "web", "target": "orders", "properties": {"protocol": "HTTPS"}},
    )
    assert r.status_code == 201, r.text
    assert r.json()["relation"]["id"] == "r1"
    assert r.json()["model"]["relations"][0]["properties"] == {"protocol": "HTTPS"}
    r = client.patch("/api/projects/shop/relations/r1", json={"properties": {"port": 443}, "description": "API"})
    assert r.status_code == 200, r.text
    assert r.json()["relation"]["properties"] == {"protocol": "HTTPS", "port": "443"}
    assert r.json()["unlinked"] == [] and r.json()["failed"] == []
    assert client.get("/api/projects/shop/model").json()["relations"][0]["description"] == "API"
    r = client.delete("/api/projects/shop/relations/r1")
    assert r.status_code == 200 and r.json()["relation"]["id"] == "r1"
    assert client.get("/api/projects/shop/model").json()["relations"] == []


def test_rest_relation_errors(client):
    r = client.post("/api/projects/shop/relations", json={"kind": "deployed_on", "source": "web", "target": "orders"})
    assert r.status_code == 400 and "deployed_on" in r.json()["detail"]
    assert client.patch("/api/projects/shop/relations/r9", json={"description": "x"}).status_code == 400
    assert client.delete("/api/projects/shop/relations/r9").status_code == 400
    assert (
        client.post("/api/projects/nope/relations", json={"kind": "x", "source": "a", "target": "b"}).status_code == 404
    )
    # internal operations are refused by the generic project route
    r = client.post(
        "/api/projects/shop/ops",
        json={"op": "add_relation", "args": {"kind": "communicates", "source": "web", "target": "orders"}},
    )
    assert r.status_code == 400 and "/relations" in r.json()["detail"]


def test_config_describes_relations(client):
    config = client.get("/api/config").json()["project_model"]
    assert config["relation_kinds"] == list(RELATION_KINDS)
    assert config["max_relations"] == MAX_RELATIONS
    assert config["relation_rules"]["deployed_on"]["target"] == ["node"]
    assert config["relation_properties"]["deployed_on"] == []
    assert "mode" in config["relation_properties"]["communicates"]
