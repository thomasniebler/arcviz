"""Review findings of component diagrams (spec: docs/architecture/specs/component-diagrams.md)."""

from __future__ import annotations

import time

from arcviz import rules
from arcviz.component_diagram import MAX_PORTS, port_names, port_text
from arcviz.model import Diagram, Edge
from arcviz.operations import apply
from arcviz.templates import create_from_template

RULE_IDS = {
    "component-interface-unprovided",
    "component-interface-unused",
    "component-dependency-cycle",
    "component-fan-out",
    "component-isolated",
    "component-port-unconnected",
    "component-subsystem-empty",
}


def diagram() -> Diagram:
    return Diagram(id="c", title="C", type="component")


def node(d: Diagram, name: str, kind: str = "component", **args) -> str:
    return apply(d, "add_node", {"name": name, "kind": kind, "id": name.lower(), **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, kind: str = "connection", **args) -> str:
    apply(d, "add_edge", {"source": source, "target": target, "kind": kind, **args})
    return d.edges[-1].id


def found(d: Diagram, rule: str) -> list[rules.Finding]:
    return [f for f in rules.run_checks(d) if f.rule == rule]


def test_every_rule_is_registered():
    assert {r.id for r in rules.rules_for("component")} == RULE_IDS


def test_the_template_is_clean():
    assert rules.run_checks(create_from_template("webshop-components", "x", "X")) == []


def test_a_required_interface_needs_a_provider():
    d = diagram()
    web, api = node(d, "Web"), node(d, "OrderApi", "interface")
    use = edge(d, web, api, "usage")
    [finding] = found(d, "component-interface-unprovided")
    assert finding.elements == [api, web] and finding.edges == [use]
    assert (finding.category, finding.severity) == ("risk", "medium")
    edge(d, node(d, "Orders"), api, "realization")
    assert found(d, "component-interface-unprovided") == []


def test_a_provided_interface_needs_a_user():
    d = diagram()
    orders, api = node(d, "Orders"), node(d, "OrderApi", "interface")
    provide = edge(d, orders, api, "realization")
    [finding] = found(d, "component-interface-unused")
    assert finding.elements == [api] and finding.edges == [provide]
    assert (finding.category, finding.severity) == ("suggestion", "low")
    edge(d, node(d, "Web"), api, "usage")
    assert found(d, "component-interface-unused") == []


def test_dependency_cycles_over_connections_dependencies_and_interfaces():
    d = diagram()
    a, b, c = node(d, "A"), node(d, "B"), node(d, "C")
    edge(d, a, b)
    edge(d, b, c, "dependency")
    assert found(d, "component-dependency-cycle") == []
    back = edge(d, c, a)
    [finding] = found(d, "component-dependency-cycle")
    assert finding.elements == [a, b, c] and back in finding.edges and len(finding.edges) == 3


def test_a_usage_depends_on_the_providers_of_the_interface():
    d = diagram()
    a, b = node(d, "A"), node(d, "B")
    api = node(d, "Api", "interface")
    edge(d, a, api, "usage")
    edge(d, b, api, "realization")  # a uses what b provides: a depends on b
    assert found(d, "component-dependency-cycle") == []
    edge(d, b, a)
    [finding] = found(d, "component-dependency-cycle")
    assert finding.elements == [a, b]


def test_a_component_depending_on_itself_through_its_own_interface_is_no_cycle():
    d = diagram()
    a = node(d, "A")
    api = node(d, "Api", "interface")
    edge(d, a, api, "usage")
    edge(d, a, api, "realization", label="own")
    assert found(d, "component-dependency-cycle") == []


def test_fan_out():
    d = diagram()
    hub = node(d, "Hub")
    targets = [node(d, f"T{i}") for i in range(8)]
    for t in targets[:7]:
        edge(d, hub, t)
    assert found(d, "component-fan-out") == []
    edge(d, hub, targets[7])
    [finding] = found(d, "component-fan-out")
    assert finding.elements == [hub] and "8" in finding.message
    edge(d, hub, targets[7], "dependency")  # the same target twice counts once
    assert len(found(d, "component-fan-out")) == 1


def test_isolated_components_only_in_a_diagram_of_three():
    d = diagram()
    a, b = node(d, "A"), node(d, "B")
    assert found(d, "component-isolated") == []
    c = node(d, "C")
    assert {f.elements[0] for f in found(d, "component-isolated")} == {a, b, c}
    edge(d, a, b)
    assert [f.elements for f in found(d, "component-isolated")] == [[c]]


def test_unconnected_ports():
    d = diagram()
    a = node(d, "A", fields={"ports": ["in", "out", "log"]})
    b = node(d, "B")
    assert found(d, "component-port-unconnected") == []  # no edge names a port: nothing to compare with
    edge(d, a, b, fields={"source_port": "out"})
    assert sorted(f.message for f in found(d, "component-port-unconnected")) == [
        "Der Port „in“ von „A“ ist mit keiner Kante verbunden.",
        "Der Port „log“ von „A“ ist mit keiner Kante verbunden.",
    ]
    edge(d, b, a, label="x", fields={"target_port": "in"})
    edge(d, b, a, label="y", fields={"target_port": "log"})
    assert found(d, "component-port-unconnected") == []


def test_empty_subsystems():
    d = diagram()
    s = node(d, "S", "subsystem")
    [finding] = found(d, "component-subsystem-empty")
    assert finding.elements == [s]
    node(d, "C", parent=s)
    assert found(d, "component-subsystem-empty") == []


def test_port_names_and_texts():
    d = diagram()
    a = node(d, "A", fields={"ports": ["orders-in: OrderApi", "plain", " spaced : T "]})
    assert port_names(d.nodes[a]) == ["orders-in", "plain", "spaced"]
    assert MAX_PORTS == 10
    for props, text in (
        ({"source_port": "p", "target_port": "q"}, "(p → q)"),
        ({"source_port": "p"}, "(p →)"),
        ({"target_port": "q"}, "(→ q)"),
        ({}, ""),
    ):
        assert port_text(Edge(id="e", source="a", target="b", properties=props)) == text


def test_findings_are_linear_in_the_size_of_the_diagram():
    d = diagram()
    previous = node(d, "N0")
    for n in range(1, 200):
        current = node(d, f"N{n}")
        edge(d, previous, current)
        previous = current
    start = time.perf_counter()
    rules.run_checks(d)
    assert time.perf_counter() - start < 2.0
