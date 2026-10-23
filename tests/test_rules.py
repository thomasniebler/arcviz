"""Rule-based architecture checks (spec: docs/architecture/specs/rule-checks.md)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import operations as ops
from arcviz import rules
from arcviz.model import CONTAINER_KINDS, EDGE_KINDS, LEAF_KINDS, Diagram, KindCatalog, use_kinds
from arcviz.rules import Finding, Rule
from arcviz.server import create_app
from arcviz.templates import TEMPLATES, demo_diagram
from tests.conftest import ScriptedProvider


def diagram(*nodes: tuple[str, str], parent: str | None = None) -> Diagram:
    d = Diagram(id="t", title="T")
    for name, kind in nodes:
        ops.add_node(d, name, kind=kind, id=name, parent=parent)
    return d


def by_rule(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


# ---- database-without-replication / replicated-database -------------------------------------


def test_database_without_replication_is_a_high_risk():
    d = diagram(("api", "service"), ("db", "database"))
    ops.add_edge(d, "api", "db")
    [finding] = by_rule(rules.run_checks(d), "database-without-replication")
    assert finding.category == "risk" and finding.severity == "high" and finding.source == "rule"
    assert finding.elements == ["db"] and finding.edges == []
    assert finding.id == "database-without-replication:db"
    assert "db" in finding.message and finding.suggestion
    assert not by_rule(rules.run_checks(d), "replicated-database")


def test_replication_edge_makes_the_database_a_strength():
    d = diagram(("api", "service"), ("db", "database"), ("db-replica", "database"))
    ops.add_edge(d, "api", "db")
    ops.add_edge(d, "db", "db-replica", kind="replication")
    findings = rules.run_checks(d)
    assert not by_rule(findings, "database-without-replication")
    strengths = by_rule(findings, "replicated-database")
    assert {f.elements[0] for f in strengths} == {"db", "db-replica"}
    assert all(f.category == "strength" and f.severity == "info" and f.edges == ["e2"] for f in strengths)


@pytest.mark.parametrize(
    ("key", "value", "replicated"),
    [
        ("replicas", "3", True),
        ("ha", "true", True),
        ("high_availability", "zone", True),
        ("zone_redundant", "yes", True),
        ("geo_redundant", "true", True),
        ("replication", "async", True),
        ("replicas", "1", False),
        ("replicas", "0", False),
        ("ha", "false", False),
        ("ha", "No", False),
        ("replication", "none", False),
        ("replication", "", False),
        ("engine", "postgres", False),
    ],
)
def test_replication_by_property(key, value, replicated):
    d = diagram(("db", "database"))
    ops.update_node(d, "db", properties={key: value})
    findings = rules.run_checks(d)
    assert bool(by_rule(findings, "replicated-database")) is replicated
    assert bool(by_rule(findings, "database-without-replication")) is not replicated


def test_storage_is_not_a_database():
    d = diagram(("blob", "storage"))
    assert not by_rule(rules.run_checks(d), "database-without-replication")


# ---- public-entry-without-gateway / gateway-entry ---------------------------------------------


def test_external_calling_a_service_directly_is_a_public_entry_without_gateway():
    d = diagram(("users", "external"), ("api", "service"))
    ops.add_edge(d, "users", "api", label="https")
    [finding] = by_rule(rules.run_checks(d), "public-entry-without-gateway")
    assert finding.severity == "medium" and finding.category == "risk"
    assert finding.elements == ["users", "api"] and finding.edges == ["e1"]
    assert finding.id == "public-entry-without-gateway:users+api:e1"


@pytest.mark.parametrize("target_kind", ["service", "function", "component"])
def test_public_entry_applies_to_compute_kinds(target_kind):
    d = diagram(("users", "external"), ("x", target_kind))
    ops.add_edge(d, "users", "x")
    assert by_rule(rules.run_checks(d), "public-entry-without-gateway")


def test_outgoing_call_to_an_external_system_is_not_an_entry():
    d = diagram(("api", "service"), ("idp", "external"))
    ops.add_edge(d, "api", "idp", kind="dependency")
    assert not by_rule(rules.run_checks(d), "public-entry-without-gateway")


@pytest.mark.parametrize(("kind", "direction"), [("association", None), ("connection", "both"), ("connection", "none")])
def test_undirected_or_bidirectional_edges_count_both_ways(kind, direction):
    d = diagram(("api", "service"), ("users", "external"))
    ops.add_edge(d, "api", "users", kind=kind, direction=direction)
    assert by_rule(rules.run_checks(d), "public-entry-without-gateway")


def test_deploys_edges_are_not_traffic():
    d = diagram(("ci", "external"), ("api", "service"))
    ops.add_edge(d, "ci", "api", kind="deploys")
    findings = rules.run_checks(d)
    assert not by_rule(findings, "public-entry-without-gateway")
    assert not by_rule(findings, "unconnected-element")  # a deploys edge still connects


def test_entry_through_the_gateway_is_a_strength():
    d = diagram(("users", "external"), ("gw", "gateway"), ("api", "service"))
    ops.add_edge(d, "users", "gw")
    ops.add_edge(d, "gw", "api")
    findings = rules.run_checks(d)
    assert not by_rule(findings, "public-entry-without-gateway")
    [strength] = by_rule(findings, "gateway-entry")
    assert strength.category == "strength" and strength.severity == "info"
    assert strength.elements == ["users", "gw"] and strength.edges == ["e1"]


# ---- external-to-datastore -----------------------------------------------------------------


@pytest.mark.parametrize("store_kind", ["database", "storage", "queue"])
@pytest.mark.parametrize("reverse", [False, True])
def test_external_talking_to_a_data_store_is_a_high_risk(store_kind, reverse):
    d = diagram(("partner", "external"), ("data", store_kind))
    if reverse:
        ops.add_edge(d, "data", "partner")
    else:
        ops.add_edge(d, "partner", "data")
    [finding] = by_rule(rules.run_checks(d), "external-to-datastore")
    assert finding.severity == "high" and finding.elements == ["partner", "data"] and finding.edges == ["e1"]


def test_external_to_datastore_ignores_deploys_edges():
    d = diagram(("ci", "external"), ("data", "storage"))
    ops.add_edge(d, "ci", "data", kind="deploys")
    assert not by_rule(rules.run_checks(d), "external-to-datastore")


# ---- unconnected-element -------------------------------------------------------------------


def test_unconnected_leaves_are_reported_containers_are_not():
    d = Diagram(id="t", title="T")
    ops.add_node(d, "dep", kind="deployment", id="dep")
    ops.add_node(d, "lonely", kind="service", id="lonely", parent="dep")
    ops.add_node(d, "empty-vnet", kind="vnet", id="empty-vnet")
    ops.add_node(d, "a", kind="service", id="a")
    ops.add_node(d, "b", kind="queue", id="b")
    ops.add_edge(d, "a", "b")
    findings = by_rule(rules.run_checks(d), "unconnected-element")
    assert [f.elements for f in findings] == [["lonely"]]
    assert findings[0].category == "suggestion" and findings[0].severity == "low"


def test_configured_kinds_count_as_connected_and_are_otherwise_ignored():
    use_kinds(
        KindCatalog(
            CONTAINER_KINDS, LEAF_KINDS | {"lambda"}, EDGE_KINDS | {"rpc"}, edge_lines={"rpc": ("solid", "none")}
        )
    )
    d = diagram(("users", "external"), ("fn", "lambda"), ("single", "lambda"))
    ops.add_edge(d, "users", "fn")
    findings = rules.run_checks(d)
    assert not by_rule(findings, "public-entry-without-gateway")  # not a known compute kind
    assert [f.elements for f in by_rule(findings, "unconnected-element")] == [["single"]]


# ---- whole diagrams, ordering, ids -----------------------------------------------------------


def test_demo_diagram_has_exactly_the_database_finding():
    findings = rules.run_checks(demo_diagram())
    assert [(f.rule, f.elements) for f in findings] == [("database-without-replication", ["orders-db"])]


def test_findings_are_sorted_by_severity_then_category_and_deterministic():
    d = diagram(("users", "external"), ("api", "service"), ("db", "database"), ("lonely", "service"))
    ops.add_edge(d, "users", "api")
    ops.add_edge(d, "api", "db")
    first = rules.run_checks(d)
    assert [f.severity for f in first] == ["high", "medium", "low"]
    assert [f.model_dump() for f in first] == [f.model_dump() for f in rules.run_checks(d)]


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_findings_of_every_template_reference_existing_ids(template):
    d = TEMPLATES[template]("x", "X")
    findings = rules.run_checks(d)
    edge_ids = {e.id for e in d.edges}
    assert len({f.id for f in findings}) == len(findings)
    for f in findings:
        assert f.elements or f.edges
        assert set(f.elements) <= set(d.nodes), f
        assert set(f.edges) <= edge_ids, f
        assert f.message
        assert (f.severity == "info") == (f.category == "strength")


# ---- registry --------------------------------------------------------------------------------


def test_deployment_is_the_default_type_and_lists_its_rules():
    assert rules.diagram_type(demo_diagram()) == "deployment"
    ids = [r.id for r in rules.rules_for("deployment")]
    assert ids == [
        "database-without-replication",
        "public-entry-without-gateway",
        "external-to-datastore",
        "unconnected-element",
        "replicated-database",
        "gateway-entry",
    ]


class Typed:
    """Stands in for a diagram once M13 adds the `type` field."""

    def __init__(self, inner: Diagram, diagram_type: str):
        self._inner, self.type = inner, diagram_type

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_the_diagram_type_selects_the_rules(monkeypatch):
    monkeypatch.setattr(rules, "RULES", {"deployment": list(rules.RULES["deployment"])})  # a type without rules
    d = demo_diagram()
    assert rules.run_checks(Typed(d, "sequence")) == []  # type: ignore[arg-type]
    assert rules.rules_for("sequence") == []

    def every_node(diagram: Diagram):
        for node_id in sorted(diagram.nodes):
            yield Finding(
                id=f"x:{node_id}", source="rule", rule="x", category="suggestion", severity="low", message=node_id
            )

    rules.register("sequence", Rule("x", "Test", "suggestion", every_node))
    assert len(rules.run_checks(Typed(d, "sequence"))) == len(d.nodes)  # type: ignore[arg-type]
    assert rules.diagram_type(Typed(d, "")) == "deployment"  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="already registered"):
        rules.register("sequence", Rule("x", "Again", "risk", every_node))


# ---- endpoint --------------------------------------------------------------------------------


def test_checks_endpoint(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    r = client.get("/api/diagrams/demo/checks")
    assert r.status_code == 200
    body = r.json()
    assert body["diagram_type"] == "deployment"
    assert [rule["id"] for rule in body["rules"]][0] == "database-without-replication"
    assert set(body["rules"][0]) == {"id", "title", "category"}
    [finding] = body["findings"]
    assert finding["id"] == "database-without-replication:orders-db" and finding["elements"] == ["orders-db"]
    assert set(finding) == {
        "id",
        "source",
        "rule",
        "category",
        "severity",
        "message",
        "elements",
        "edges",
        "suggestion",
    }
    assert client.get("/api/diagrams/nope/checks").status_code == 404
    assert len(store.list_versions("demo")) == 1  # read-only
