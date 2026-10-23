"""Fixes of the coordinator review of M25 (PR #210; spec: model-relations.md).

One block per finding: references that a diagram type cannot carry, bounded and never reused relation ids, the one
definition of "shown" (nesting under a node), the open-world gap rules, direction by orientation, and the cost of
relation changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arcviz import exchange, project_model, projects
from arcviz import project_review as pr
from arcviz.model import Diagram, Edge, Node
from arcviz.project_model import (
    MAX_ELEMENT_DESCRIPTION,
    MAX_ELEMENT_NAME,
    MAX_ELEMENTS,
    MAX_RELATIONS,
    Element,
    Relation,
)
from arcviz.projects import Project, ProjectError
from arcviz.services import DiagramService, ModelService, ProjectService
from arcviz.templates import create_from_template
from tests.test_correspondence import checks, link_relation, relate
from tests.test_project_review import link, model_snapshot, op, rule

SHOWN_CASES = json.loads((Path(__file__).parent / "fixtures" / "relations_shown.json").read_text(encoding="utf-8"))


# ---- 1. references a type cannot carry ------------------------------------------------------------------------


def test_activity_diagrams_cannot_carry_relation_declarations():
    d = create_from_template("order-process", "order", "Bestellprozess")
    d.hidden_relations = ["r1"]
    assert any("hidden_relations" in e for e in d.integrity_errors())
    d.hidden_relations, d.complete = [], ["communicates"]
    assert any("complete" in e for e in d.integrity_errors())
    d.complete = []
    edge = d.edges[0]
    edge.ref = "r1"
    assert any(edge.id in e for e in d.integrity_errors())
    document = exchange.export_document(create_from_template("order-process", "order", "Bestellprozess"))
    document["diagram"]["hidden_relations"] = ["r1"]
    with pytest.raises(exchange.ExchangeError, match="hidden_relations"):
        exchange.parse_document(document)


@pytest.fixture
def world(store):
    projects_service = ProjectService(store, store)
    diagrams = DiagramService(store, projects_service)
    models = ModelService(projects_service, diagrams)
    projects_service.create("Shop", "shop")
    return projects_service, diagrams, models


def test_removing_an_element_shown_in_an_activity_diagram_with_relations(world):
    _, diagrams, models = world
    diagrams.create("Ablauf", "ablauf", create_from_template("order-process", "ablauf", "Ablauf"), "shop")
    partition = next(n.id for n in diagrams.get("ablauf").nodes.values() if n.kind == "activity_partition")
    models.add_element("shop", "Shop", "system", element_id="shop")
    models.add_element("shop", "Kunde", "actor", element_id="kunde")
    models.link("ablauf", partition, "shop")
    models.add_relation("shop", "communicates", "kunde", "shop")
    removal = models.remove_element("shop", "shop")  # clear_relation is no activity operation: never sent there
    assert removal.unlinked == ["ablauf"] and models.relations("shop") == []
    assert diagrams.get("ablauf").nodes[partition].ref is None


# ---- 2. bounded ids -------------------------------------------------------------------------------------------


def two_elements() -> Project:
    project = Project(id="p", title="P")
    projects.apply(project, "add_element", {"name": "A"})
    projects.apply(project, "add_element", {"name": "B"})
    return project


def test_relation_and_element_ids_are_at_most_64_characters():
    project = two_elements()
    with pytest.raises(ProjectError, match="64"):
        projects.apply(project, "add_relation", {"kind": "communicates", "source": "a", "target": "b", "id": "r" * 65})
    with pytest.raises(ProjectError, match="64"):
        projects.apply(project, "add_element", {"name": "C", "id": "c" * 65})
    project.relations = [Relation(id="r" * 65, kind="communicates", source="a", target="b")]
    assert any("64" in e for e in project.integrity_errors())
    project.elements.append(Element(id="x" * 65, name="X"))
    project.relations = [Relation(id="r1", kind="communicates", source="a", target="x" * 65)]
    assert any("64" in e for e in project.integrity_errors())


def test_the_worst_case_project_of_the_budget_fits_the_cosmos_item():
    """The arithmetic of the spec's size budget: 500 relations and 500 elements (#212) with every field at its bound in
    three-byte characters, 1 000 memberships and sections."""
    wide = "€"  # three bytes in UTF-8
    project = Project(id="p" * 64, title="Projekt")
    project.elements = [
        Element(
            id=f"{'e' * 58}{i:05d}",
            name=f"{i:05d}" + wide * (MAX_ELEMENT_NAME - 5),
            kind="subsystem",
            description=wide * MAX_ELEMENT_DESCRIPTION,
            parent=f"{'e' * 58}{i - 1:05d}" if i else None,
        )
        for i in range(MAX_ELEMENTS)
    ]
    for element in project.elements:  # every element is one a write could create (#212)
        assert projects.apply(
            Project(id="x", title="x"), "add_element", {"name": element.name, "description": element.description}
        )
    project.relations = [
        Relation(
            id=f"{'r' * 58}{i:05d}",
            kind="communicates",
            source=project.elements[i].id,
            target=project.elements[(i + 1) % 500].id,
            properties={"protocol": wide * 64, "port": wide * 64, "direction": "forward", "mode": "async"},
            description=wide * 300,
        )
        for i in range(MAX_RELATIONS)
    ]
    projects.apply(project, "add_section", {"title": "Abschnitt"})
    project.diagrams = [
        projects.Membership(
            diagram=f"{'d' * 53}-{i:010d}", section="abschnitt", added_at="2026-10-11T00:00:00.000000+00:00"
        )
        for i in range(1000)
    ]
    assert project.integrity_errors() == []
    size = len(json.dumps(project.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))
    assert size < 2_000_000, size


# ---- 3. relation ids are never reused ---------------------------------------------------------------------------


def test_generated_relation_ids_are_never_reused():
    project = two_elements()
    projects.apply(project, "add_element", {"name": "C"})
    first = projects.apply(project, "add_relation", {"kind": "communicates", "source": "a", "target": "b"})["result"]
    second = projects.apply(project, "add_relation", {"kind": "communicates", "source": "b", "target": "c"})["result"]
    projects.apply(project, "remove_relation", {"relation": second["id"]})
    third = projects.apply(project, "add_relation", {"kind": "communicates", "source": "a", "target": "c"})["result"]
    assert (first["id"], second["id"], third["id"]) == ("r1", "r2", "r3")
    projects.apply(project, "remove_relation", {"relation": "r3"})
    projects.apply(project, "remove_relation", {"relation": "r1"})
    assert (
        projects.apply(project, "add_relation", {"kind": "communicates", "source": "a", "target": "b"})["result"]["id"]
        == "r4"
    )
    assert Project.model_validate(project.model_dump(mode="json")).relation_seq == 4
    assert "relation_seq" not in Project(id="q", title="Q").model_dump()  # omitted while unset


def test_hidden_markers_of_unknown_relations_are_reported_and_repaired(world):
    _, diagrams, models = world
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {"web": Node(id="web", name="Web", kind="service")}
    diagrams.create("Plattform", "platform", d, "shop")
    models.add_element("shop", "Web", "component", element_id="web")
    models.add_element("shop", "API", "component", element_id="api")
    models.add_relation("shop", "communicates", "web", "api")
    models.hide_relation("platform", "r1", True)
    models.remove_relation("shop", "r1")
    diagrams.apply_operation("platform", "set_relation_hidden", {"relation": "r1", "hidden": True}, "undo-artig")
    snap = pr.ProjectSnapshot(*models._projects.load_members("shop"))  # noqa: SLF001
    (found,) = rule(pr.run_project_checks(snap), "invalid-relation-link")
    assert found.diagram == "platform" and "ausgeblendet" in found.message
    assert models.sync("shop").changed == ["platform"]
    assert diagrams.get("platform").hidden_relations == []


# ---- 4./5. one definition of "shown" ----------------------------------------------------------------------------


@pytest.mark.parametrize("case", SHOWN_CASES, ids=[c["name"] for c in SHOWN_CASES])
def test_the_shared_fixture_of_shown_relations(case):
    diagram = Diagram.model_validate(case["diagram"])
    relation = Relation.model_validate(case["relation"])
    assert project_model.shows(diagram, relation, case["kinds"]) is case["shows"]


def test_nesting_under_a_node_counts_through_other_containers():
    snap, dep, *_ = model_snapshot()
    projects.apply(snap.project, "add_element", {"name": "Abrechnung", "kind": "subsystem", "id": "billing-sub"})
    projects.apply(snap.project, "add_element", {"name": "Srv", "kind": "node"})
    op(dep, "add_node", name="Srv", kind="node", id="srv")
    link(dep, "srv", "srv", "Srv", "node")
    op(dep, "add_node", name="Abrechnung", kind="group", id="bill", parent="srv")
    link(dep, "bill", "billing-sub", "Abrechnung", "subsystem")
    op(dep, "move_node", id="api", parent="bill")
    relate(snap, "deployed_on", "api", "srv")
    op(dep, "set_completeness", kinds=["all"])
    findings = checks(snap)
    assert not rule(findings, "complete-view-missing-relation") and not rule(findings, "relation-not-shown")


def test_the_model_answer_counts_nesting_as_shown(world):
    _, diagrams, models = world
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {
        "srv": Node(id="srv", name="Srv", kind="node"),
        "grp": Node(id="grp", name="Gruppe", kind="group", parent="srv"),
        "api": Node(id="api", name="API", kind="service", parent="grp"),
    }
    diagrams.create("Plattform", "platform", d, "shop")
    models.add_element("shop", "Srv", "node", element_id="srv")
    models.add_element("shop", "API", "component", element_id="api")
    models.link("platform", "srv", "srv")
    models.link("platform", "api", "api")
    models.add_relation("shop", "deployed_on", "api", "srv")
    (relation,) = models.model("shop").to_dict()["relations"]
    assert [(u["diagram"], u["view"]) for u in relation["shown"]] == [("platform", "api")]


def test_deployed_on_several_nodes_counts_the_nearest_node_only():
    snap, dep, *_ = model_snapshot()
    for name in ("Cluster", "Pod"):
        projects.apply(snap.project, "add_element", {"name": name, "kind": "node"})
    op(dep, "add_node", name="Cluster", kind="cluster", id="cl")
    link(dep, "cl", "cluster", "Cluster", "node")
    op(dep, "add_node", name="Pod", kind="node", id="pod", parent="cl")
    link(dep, "pod", "pod", "Pod", "node")
    op(dep, "move_node", id="api", parent="pod")
    found = rule(checks(snap), "deployed-on-several-nodes")
    assert [f.model_element for f in found] == []  # api runs on the pod (which runs on the cluster)


# ---- 6. relation-not-shown and hidden or complete views -----------------------------------------------------------


def test_relation_not_shown_skips_hiding_and_complete_views():
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "communicates", "api", "db")
    assert rule(checks(snap), "relation-not-shown")
    op(dep, "set_relation_hidden", relation=r, hidden=True)
    assert not rule(checks(snap), "relation-not-shown")  # the only candidate hides it on purpose
    op(dep, "set_relation_hidden", relation=r, hidden=False)
    op(dep, "set_completeness", kinds=["communicates"])
    findings = checks(snap)
    assert not rule(findings, "relation-not-shown") and rule(findings, "complete-view-missing-relation")


def test_relation_not_shown_is_a_low_suggestion_naming_candidates():
    snap, *_ = model_snapshot()
    relate(snap, "communicates", "api", "db")
    (f,) = rule(checks(snap), "relation-not-shown")
    assert (f.category, f.severity) == ("suggestion", "low") and "„Plattform“" in f.message


# ---- 7. a message without relation is reported exactly once --------------------------------------------------


def test_a_message_between_elements_outside_any_deployment_is_reported_once():
    snap, dep, seq, _ = model_snapshot()
    relate(snap, "communicates", "web", "api")
    projects.apply(snap.project, "add_element", {"name": "Mail", "kind": "system"})
    op(seq, "add_lifeline", name="Mail", id="mail")
    link(seq, "mail", "mail", "Mail", "system")
    op(seq, "add_message", sender="api", receiver="mail", name="send()")
    findings = checks(snap)
    about = [
        f
        for f in findings
        if f.rule in ("connection-without-relation", "message-without-connection") and "Mail" in f.message
    ]
    assert len(about) == 1, [f.id for f in about]


# ---- 8. direction by orientation --------------------------------------------------------------------------------


def test_edge_direction_is_compared_relative_to_the_relation():
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "communicates", "web", "api", direction="forward")
    op(dep, "add_edge", source="api", target="web", kind="dependency", direction="forward")  # drawn the other way
    reverse = next(e for e in dep.edges if e.source == "api")
    link_relation(dep, reverse.id, r, "communicates", "web", "api")
    found = rule(checks(snap), "relation-property-conflict")
    assert [f.id for f in found] == [f"relation-property-conflict:{r}:direction"]
    assert all(x.args.get("properties", {}).get("direction") != "backward" for x in found[0].resolutions)
    op(dep, "update_edge", id=reverse.id, direction="both")
    projects.apply(snap.project, "update_relation", {"relation": r, "properties": {"direction": "both"}})
    assert not rule(checks(snap), "relation-property-conflict")


# ---- 9./10. cost ---------------------------------------------------------------------------------------------------


def test_a_property_change_reads_no_diagram(world, monkeypatch):
    projects_service, diagrams, models = world
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {"web": Node(id="web", name="Web", kind="service"), "api": Node(id="api", name="API", kind="service")}
    d.edges = [Edge(id="e1", source="web", target="api")]
    diagrams.create("Plattform", "platform", d, "shop")
    for name, element_id in (("Web", "web"), ("API", "api"), ("DB", "db")):
        models.add_element("shop", name, "component", element_id=element_id)
        if element_id != "db":
            models.link("platform", element_id, element_id)
    models.add_relation("shop", "communicates", "web", "api")
    models.link_relation("platform", "e1", "r1")
    calls = []
    real = projects_service.load_members
    monkeypatch.setattr(projects_service, "load_members", lambda pid: calls.append(pid) or real(pid))
    models.update_relation("shop", "r1", properties={"protocol": "HTTPS"})
    assert calls == []
    change = models.update_relation("shop", "r1", target="db")
    assert calls == ["shop"] and change.unlinked == ["platform"]


def test_property_conflicts_build_each_diagrams_connections_once(monkeypatch):
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "communicates", "web", "api", protocol="HTTPS")
    op(dep, "update_edge", id=dep.edges[0].id, properties={"protocol": "gRPC"})
    link_relation(dep, dep.edges[0].id, r, "communicates", "web", "api")
    for _ in range(5):
        op(
            dep,
            "add_edge",
            source="web",
            target="api",
            kind="dependency",
            label=f"x{_}",
            properties={"protocol": "gRPC"},
        )
        link_relation(dep, dep.edges[-1].id, r, "communicates", "web", "api")
    calls = []
    real = pr.view_connections
    monkeypatch.setattr(pr, "view_connections", lambda d: calls.append(d.id) or real(d))
    list(pr.relation_property_conflict(snap))
    assert len(calls) == len(snap.members)


def test_operations_are_unchanged_for_valid_relations():
    """Guard: the integrity rules still accept a valid project (the duplicate check by set)."""
    project = two_elements()
    projects.apply(project, "add_relation", {"kind": "communicates", "source": "a", "target": "b"})
    with pytest.raises(ProjectError, match="already"):
        projects.apply(project, "add_relation", {"kind": "communicates", "source": "b", "target": "a"})
    project.relations.append(Relation(id="r9", kind="communicates", source="b", target="a"))
    assert any("duplicates" in e for e in project.integrity_errors())


# ---- second round ---------------------------------------------------------------------------------------------------


def test_bundles_keep_the_relation_counter(world):
    from arcviz.services import ProjectExchangeService

    projects_service, diagrams, models = world
    d = Diagram(id="platform", title="Plattform")
    d.nodes = {"web": Node(id="web", name="Web", kind="service")}
    diagrams.create("Plattform", "platform", d, "shop")
    for name, element_id in (("Web", "web"), ("API", "api"), ("DB", "db")):
        models.add_element("shop", name, "component", element_id=element_id)
    models.add_relation("shop", "communicates", "web", "api")
    models.add_relation("shop", "communicates", "web", "db")
    models.hide_relation("platform", "r2", True)
    models.remove_relation("shop", "r1")
    bundles = ProjectExchangeService(diagrams, projects_service)
    bundle = bundles.export("shop")
    assert bundle["project"]["relation_seq"] == 2
    created = bundles.import_bundle(bundle)
    assert created.relation_seq == 2
    assert models.add_relation(created.id, "communicates", "api", "db").id == "r3"
    # an older bundle without the counter: the highest r<n> of the relations and of every link or hidden marker
    del bundle["project"]["relation_seq"]
    bundle["project"]["relations"] = []
    created = bundles.import_bundle(bundle)
    assert created.relation_seq == 2  # the hidden marker r2 of the platform diagram


def test_an_explicit_id_never_makes_generation_fail():
    project = two_elements()
    projects.apply(project, "add_element", {"name": "C"})
    with pytest.raises(ProjectError, match="too large"):
        projects.apply(
            project, "add_relation", {"kind": "communicates", "source": "a", "target": "b", "id": "r" + "9" * 63}
        )
    projects.apply(
        project, "add_relation", {"kind": "communicates", "source": "a", "target": "b", "id": "r" + "1" * 30}
    )
    assert (
        projects.apply(project, "add_relation", {"kind": "communicates", "source": "b", "target": "c"})["result"]["id"]
        == "r" + "1" * 29 + "2"
    )
    project.relation_seq = 10**63  # a stored counter at the very end: a refusal, never an integrity failure
    with pytest.raises(ProjectError):
        projects.apply(project, "add_relation", {"kind": "communicates", "source": "a", "target": "c"})


def test_bundles_with_too_long_element_ids_are_refused():
    project = Project(id="p", title="P")
    project.elements = [Element(id="x" * 65, name="X")]
    bundle = exchange.export_project(project, [])
    with pytest.raises(exchange.ExchangeError, match="64"):
        exchange.parse_project(bundle)


def test_a_reversed_arrow_is_described_not_called_backward():
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "communicates", "web", "api", direction="forward")
    op(dep, "add_edge", source="api", target="web", kind="dependency", direction="forward")
    reverse = next(e for e in dep.edges if e.source == "api")
    link_relation(dep, reverse.id, r, "communicates", "web", "api")
    (found,) = rule(checks(snap), "relation-property-conflict")
    assert "backward" not in found.message and "entgegen der Modellrichtung" in found.message
    assert "„Plattform“" in found.message


def test_shown_views_take_the_nesting_once(monkeypatch):
    snap, dep, *_ = model_snapshot()
    for i in range(5):
        projects.apply(snap.project, "add_element", {"name": f"Host {i}", "kind": "node"})
        relate(snap, "deployed_on", "api", f"host-{i}")
    calls = []
    real = project_model.nested
    monkeypatch.setattr(project_model, "nested", lambda d, k: calls.append(d.id) or real(d, k))
    monkeypatch.setattr(pr, "nested", project_model.nested)
    list(pr.relation_not_shown(snap))
    assert calls.count("dep") <= 1
