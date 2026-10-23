"""Component connectors link to model relations (spec: component-diagrams.md, ADR-0181; model-relations.md).

`connection` and `dependency` edges of a component diagram may show `communicates` (and `connection` `data_flow`);
`usage` and `realization` end at an interface, which is no element: never linked.
"""

from __future__ import annotations

import pytest

from arcviz import agent_model, operations, project_model, projects
from arcviz import project_review as pr
from arcviz.agent import build_tools
from arcviz.model import BUILTIN_KINDS, RELATION_DIAGRAM_TYPES, Diagram, fingerprint
from arcviz.operations import OperationError
from tests.test_project_review import link, op, rule, snapshot


def components() -> Diagram:
    d = Diagram(id="comp", title="Komponenten", type="component")
    op(d, "add_node", name="Web", kind="component", id="web")
    op(d, "add_node", name="API", kind="component", id="api")
    op(d, "add_node", name="DB", kind="component", id="db")
    op(d, "add_node", name="Zahlung", kind="interface", id="pay")
    op(d, "add_edge", source="web", target="api", kind="connection")
    op(d, "add_edge", source="api", target="db", kind="dependency")
    op(d, "add_edge", source="api", target="pay", kind="usage")
    return d


def linked() -> tuple[Diagram, object]:
    d = components()
    snap = snapshot(members=[(d, None)])
    for view, name in (("web", "Web"), ("api", "API"), ("db", "DB")):
        projects.apply(snap.project, "add_element", {"name": name, "kind": "component"})
        link(d, view, view, name)
    return d, snap


def relate(snap, kind, source, target):
    return projects.apply(snap.project, "add_relation", {"kind": kind, "source": source, "target": target})["result"][
        "id"
    ]


def test_component_is_a_relation_type():
    assert "component" in RELATION_DIAGRAM_TYPES
    assert project_model.shown_relation_kinds("component") == ("communicates", "data_flow")
    assert project_model.relation_table("component") == {
        "connection": ["communicates", "data_flow"],
        "dependency": ["communicates"],
    }


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("connection", ("communicates", "data_flow")),
        ("dependency", ("communicates",)),
        ("usage", ()),
        ("realization", ()),
    ],
)
def test_which_connectors_may_show_which_relations(kind, expected):
    assert project_model.relation_kinds_for("component", kind) == expected


def test_link_unlink_and_hide():
    d, _ = linked()
    before = fingerprint(d)
    op(d, "link_relation", id="e1", relation="r1", kind="communicates", source="web", target="api")
    assert d.edges[0].ref == "r1" and fingerprint(d) != before
    op(d, "unlink_relation", id="e1")
    assert d.edges[0].ref is None and fingerprint(d) == before
    op(d, "set_relation_hidden", relation="r2", hidden=True)
    assert d.hidden_relations == ["r2"]


def test_a_dependency_may_show_communicates_but_not_data_flow():
    d, _ = linked()
    op(d, "link_relation", id="e2", relation="r1", kind="communicates", source="api", target="db")
    op(d, "unlink_relation", id="e2")
    with pytest.raises(OperationError):
        op(d, "link_relation", id="e2", relation="r1", kind="data_flow", source="api", target="db")


def test_usage_and_unlinked_ends_are_refused():
    d, _ = linked()
    with pytest.raises(OperationError):
        op(d, "link_relation", id="e3", relation="r1", kind="communicates", source="api", target="pay")
    d.nodes["web"].ref = None
    with pytest.raises(OperationError):
        op(d, "link_relation", id="e1", relation="r1", kind="communicates", source="web", target="api")


def test_completeness_is_declared_per_relation_kind():
    d, _ = linked()
    op(d, "set_completeness", kinds=["communicates"])
    assert d.complete == ["communicates"]
    with pytest.raises(OperationError):
        op(d, "set_completeness", kinds=["deployed_on"])


def test_links_are_cleared_when_the_ends_change():
    d, _ = linked()
    op(d, "link_relation", id="e1", relation="r1", kind="communicates", source="web", target="api")
    op(d, "unlink_element", id="api")
    assert d.edges[0].ref is None


def test_the_model_view_of_the_diagram_shows_its_connections():
    d, _ = linked()
    connections = {c.id: c for c in project_model.view_connections(d)}
    assert connections["e1"].source_ref == "web" and connections["e1"].target_ref == "api"
    elements, relations = project_model.model_refs(d)
    assert {"web", "api", "db"} <= elements and relations == set()


def test_the_agent_gets_link_and_completeness_tools():
    names = {t.name for t in build_tools(BUILTIN_KINDS, "component")}
    assert "set_completeness" in names
    tool = next(t for t in build_tools(BUILTIN_KINDS, "component") if t.name == "set_completeness")
    assert tool.parameters["properties"]["kinds"]["items"]["enum"] == ["all", "communicates", "data_flow"]
    assert {"link_relation", "unlink_relation"} <= {t.name for t in agent_model.model_tools_for("component")}
    assert "unlink_relation" in operations.batch_operations("component")
    assert "set_completeness" not in {t.name for t in build_tools(BUILTIN_KINDS, "class")}


def test_a_relation_shown_in_a_component_diagram_is_not_a_gap():
    d, snap = linked()
    r = relate(snap, "communicates", "web", "api")
    assert rule(pr.run_project_checks(snap), "connection-without-relation")  # e1 fits r1 but is not linked
    assert not rule(pr.run_project_checks(snap), "relation-not-shown")
    op(d, "link_relation", id="e1", relation=r, kind="communicates", source="web", target="api")
    assert not [f for f in rule(pr.run_project_checks(snap), "connection-without-relation") if "web+api" in f.id]


def test_a_relation_missing_in_the_component_diagram_is_reported():
    d, snap = linked()
    r = relate(snap, "communicates", "web", "db")
    found = rule(pr.run_project_checks(snap), "relation-not-shown")
    assert [f.relation for f in found] == [r] and found[0].diagrams == ["comp"]
    assert "Komponentendiagramm" in found[0].message
    op(d, "set_completeness", kinds=["communicates"])
    assert [f.rule for f in pr.run_project_checks(snap) if f.relation == r and f.diagrams == ["comp"]] == [
        "complete-view-missing-relation"
    ]


def test_a_linked_connector_shows_the_relation():
    c, snap = linked()
    r = relate(snap, "communicates", "web", "api")
    kinds = {e.id: e.kind for e in snap.project.elements}
    assert project_model.shown_views(c, snap.project.relations[0], kinds) == ([], ["e1"])  # fits, not yet linked
    op(c, "link_relation", id="e1", relation=r, kind="communicates", source="web", target="api")
    assert project_model.shown_views(c, snap.project.relations[0], kinds) == (["e1"], [])
