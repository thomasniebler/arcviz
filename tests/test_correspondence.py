"""Correspondence rules across diagrams over model relations (spec: model-relations.md, ADR-0141).

Gaps: `relation-not-shown`, `connection-without-relation`, `complete-view-missing-relation`; contradictions:
`relation-property-conflict`, `deployed-on-several-nodes`, `containment-conflict`; drift: `invalid-relation-link`;
the generalised `message-without-connection`.
"""

from __future__ import annotations

from arcviz import project_review as pr
from arcviz import projects
from arcviz.model import Diagram
from tests.test_project_review import link, model_snapshot, op, rule, snapshot


def relate(snap, kind, source, target, **properties):
    args = {"kind": kind, "source": source, "target": target}
    if properties:
        args["properties"] = properties
    return projects.apply(snap.project, "add_relation", args)["result"]["id"]


def link_relation(d, view, relation, kind, source, target):
    op(d, "link_relation", id=view, relation=relation, kind=kind, source=source, target=target)


def checks(snap):
    return pr.run_project_checks(snap)


def test_the_rules_are_listed():
    ids = [
        r.id
        for r in pr.PROJECT_RULES
        if not r.id.startswith(("requirement-", "quality-requirement", "scenario-no", "decision-no", "stakeholder-no"))
    ]
    assert ids[-7:] == [
        "relation-not-shown",
        "connection-without-relation",
        "complete-view-missing-relation",
        "relation-property-conflict",
        "deployed-on-several-nodes",
        "containment-conflict",
        "invalid-relation-link",
    ]
    assert "message-without-connection" in ids


def test_projects_without_relations_get_no_relation_gap_findings():
    snap, *_ = model_snapshot()
    found = {f.rule for f in checks(snap)}
    assert not found & {"relation-not-shown", "connection-without-relation", "complete-view-missing-relation"}


# ---- gaps ----------------------------------------------------------------------------------------------------


def test_relation_not_shown_in_any_deployment_diagram():
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "communicates", "api", "db")
    found = rule(checks(snap), "relation-not-shown")
    assert [f.relation for f in found] == [r]
    f = found[0]
    assert f.id == f"relation-not-shown:{r}" and f.diagrams == ["dep"] and f.severity == "low"
    assert "„API“" in f.message and "„DB“" in f.message and "„Plattform“" in f.message
    op(dep, "add_edge", source="db", target="api")  # any edge that can show it, linked or not
    assert not rule(checks(snap), "relation-not-shown")


def test_relation_not_shown_needs_both_ends_in_a_deployment_diagram():
    snap, *_ = model_snapshot()
    relate(snap, "communicates", "api", "billing")  # billing is in no deployment diagram
    assert not rule(checks(snap), "relation-not-shown")


def test_connection_without_relation_suggests_linking_or_creating():
    snap, dep, seq, _ = model_snapshot()
    r = relate(snap, "communicates", "web", "api")
    found = rule(checks(snap), "connection-without-relation")
    by_diagram = {f.diagram: f for f in found}
    assert set(by_diagram) == {"dep", "seq"}  # e1 in the deployment, order() in the sequence
    assert by_diagram["dep"].relation == r and "verknüpft" in by_diagram["dep"].message
    edge = dep.edges[0].id
    link_relation(dep, edge, r, "communicates", "web", "api")
    assert {f.diagram for f in rule(checks(snap), "connection-without-relation")} == {"seq"}
    op(dep, "add_edge", source="api", target="db")  # no relation behind it
    created = [f for f in rule(checks(snap), "connection-without-relation") if f.diagram == "dep"]
    assert len(created) == 1 and created[0].relation is None and "keine Beziehung" in created[0].message


def test_messages_without_relation_or_deployment_connection_are_reported_once():
    snap, *_ = model_snapshot()
    relate(snap, "communicates", "web", "api")
    findings = checks(snap)
    assert rule(findings, "message-without-connection")  # api -> db
    assert not [f for f in rule(findings, "connection-without-relation") if "DB" in f.message]


def test_complete_view_missing_relation():
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "data_flow", "api", "db")
    assert not rule(checks(snap), "complete-view-missing-relation")  # open world
    op(dep, "set_completeness", kinds=["communicates"])
    assert not rule(checks(snap), "complete-view-missing-relation")  # another kind
    op(dep, "set_completeness", kinds=["all"])
    found = rule(checks(snap), "complete-view-missing-relation")
    assert [(f.diagram, f.relation) for f in found] == [("dep", r)]
    assert found[0].severity == "medium" and set(found[0].elements) == {"api", "db"}
    op(dep, "set_relation_hidden", relation=r, hidden=True)
    assert not rule(checks(snap), "complete-view-missing-relation")
    op(dep, "set_relation_hidden", relation=r, hidden=False)
    op(dep, "add_edge", source="api", target="db")  # a fitting connection shows it
    assert not rule(checks(snap), "complete-view-missing-relation")


def test_complete_sequence_diagram_and_a_relation_it_cannot_show():
    snap, dep, seq, _ = model_snapshot()
    projects.apply(snap.project, "add_element", {"name": "Host", "kind": "node"})
    relate(snap, "deployed_on", "api", "host")
    r = relate(snap, "communicates", "web", "db")
    op(seq, "set_completeness", kinds=["all"])
    found = rule(checks(snap), "complete-view-missing-relation")
    assert [(f.diagram, f.relation) for f in found] == [("seq", r)]


# ---- contradictions ------------------------------------------------------------------------------------------


def test_property_conflict_between_views_and_the_model():
    snap, dep, seq, _ = model_snapshot()
    r = relate(snap, "communicates", "web", "api", protocol="HTTPS")
    edge = dep.edges[0].id
    op(dep, "update_edge", id=edge, properties={"protocol": "gRPC"})
    link_relation(dep, edge, r, "communicates", "web", "api")
    found = rule(checks(snap), "relation-property-conflict")
    assert len(found) == 1
    f = found[0]
    assert f.id == f"relation-property-conflict:{r}:protocol" and f.relation == r and f.diagrams == ["dep"]
    assert "„HTTPS“" in f.message and "„gRPC“" in f.message and "Modell" in f.message and "„Plattform“" in f.message
    projects.apply(snap.project, "update_relation", {"relation": r, "properties": {"protocol": "grpc "}})
    assert not rule(checks(snap), "relation-property-conflict")  # compared case-insensitively, trimmed


def test_property_conflict_between_two_views_names_both_diagrams():
    snap, dep, seq, _ = model_snapshot()
    r = relate(snap, "communicates", "web", "api")
    link_relation(dep, dep.edges[0].id, r, "communicates", "web", "api")
    message = next(f for f in seq.interaction.fragments if getattr(f, "name", "") == "order()")
    link_relation(seq, message.id, r, "communicates", "web", "api")  # synchCall: sync
    other = Diagram(id="dep2", title="Betrieb")
    op(other, "add_node", name="Web", kind="service", id="web")
    op(other, "add_node", name="API", kind="service", id="api")
    link(other, "web", "web", "Web")
    link(other, "api", "api", "API")
    op(other, "add_edge", source="web", target="api", properties={"protocol": "HTTP"})
    op(dep, "update_edge", id=dep.edges[0].id, properties={"protocol": "HTTPS"})
    link_relation(other, other.edges[0].id, r, "communicates", "web", "api")
    projects.apply(snap.project, "add_diagram", {"diagram": "dep2"})
    snap = pr.ProjectSnapshot(snap.project, [*snap.members, (snap.project.member("dep2"), other)])
    found = rule(checks(snap), "relation-property-conflict")
    assert len(found) == 1 and set(found[0].diagrams) == {"dep", "dep2"}
    assert "„Plattform“" in found[0].message and "„Betrieb“" in found[0].message
    # a message's sort is the relation's mode
    projects.apply(snap.project, "update_relation", {"relation": r, "properties": {"mode": "async"}})
    modes = [f for f in rule(checks(snap), "relation-property-conflict") if f.id.endswith(":mode")]
    assert len(modes) == 1 and "„Checkout“" in modes[0].message


def test_an_element_deployed_on_two_nodes():
    snap, dep, *_ = model_snapshot()
    for name in ("Host A", "Host B"):
        projects.apply(snap.project, "add_element", {"name": name, "kind": "node"})
    relate(snap, "deployed_on", "api", "host-a")
    assert not rule(checks(snap), "deployed-on-several-nodes")
    op(dep, "add_node", name="Host B", kind="node", id="host-b")
    link(dep, "host-b", "host-b", "Host B", "node")
    op(dep, "move_node", id="api", parent="host-b")  # nesting under a node element reads as deployed_on
    found = rule(checks(snap), "deployed-on-several-nodes")
    assert len(found) == 1 and found[0].model_element == "api"
    assert "„Host A“" in found[0].message and "„Host B“" in found[0].message and "„Plattform“" in found[0].message


def test_containment_that_disagrees_with_the_model_or_another_view():
    snap, dep, *_ = model_snapshot()
    projects.apply(snap.project, "add_element", {"name": "Shop", "kind": "system"})
    projects.apply(snap.project, "add_element", {"name": "Backoffice", "kind": "subsystem"})
    projects.apply(snap.project, "move_element", {"element": "api", "parent": "shop"})
    op(dep, "add_node", name="Backoffice", kind="group", id="bo")
    link(dep, "bo", "backoffice", "Backoffice", "subsystem")
    assert not rule(checks(snap), "containment-conflict")
    op(dep, "move_node", id="api", parent="bo")
    found = rule(checks(snap), "containment-conflict")
    assert len(found) == 1 and found[0].model_element == "api" and "„Shop“" in found[0].message
    projects.apply(snap.project, "move_element", {"element": "backoffice", "parent": "shop"})
    projects.apply(snap.project, "move_element", {"element": "api", "parent": "backoffice"})
    assert not rule(checks(snap), "containment-conflict")


def test_invalid_relation_links_are_reported_until_sync():
    snap, dep, *_ = model_snapshot()
    r = relate(snap, "communicates", "web", "api")
    link_relation(dep, dep.edges[0].id, r, "communicates", "web", "api")
    assert not rule(checks(snap), "invalid-relation-link")
    projects.apply(snap.project, "update_relation", {"relation": r, "target": "db"})  # behind the link's back
    found = rule(checks(snap), "invalid-relation-link")
    assert [f.diagram for f in found] == ["dep"] and found[0].edges == [dep.edges[0].id]
    projects.apply(snap.project, "remove_relation", {"relation": r})
    assert rule(checks(snap), "invalid-relation-link")  # a link to a relation that is gone


def test_message_without_connection_counts_a_communicates_relation():
    snap, *_ = model_snapshot()
    before = rule(checks(snap), "message-without-connection")
    assert [f.id for f in before] == ["message-without-connection:seq:api+db"]  # the id is unchanged since M18
    relate(snap, "communicates", "db", "api")
    assert not rule(checks(snap), "message-without-connection")
    assert rule(checks(snap), "relation-not-shown")  # the gap moves to the relation


def test_the_review_digest_lists_relations():
    snap, *_ = model_snapshot()
    relate(snap, "communicates", "web", "api", protocol="HTTPS")
    text = pr.review_input(snap, checks(snap))
    assert "Model relations:" in text and "[r1] communicates: web -> api {protocol=HTTPS}" in text
    assert "Model relations:" not in pr.review_input(snapshot(), [])
