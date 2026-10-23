import json

from arcviz import operations as ops
from arcviz.model import SUMMARY_SLACK, Diagram, Edge, Node
from arcviz.templates import demo_diagram


def big_diagram(containers: int = 4, per_container: int = 12) -> Diagram:
    d = Diagram(id="big", title="Big")
    for c in range(containers):
        ops.add_node(d, f"zone-{c}", kind="vnet", id=f"zone-{c}", properties={"cidr": f"10.{c}.0.0/16"})
        ops.add_node(d, f"subnet-{c}", kind="subnet", id=f"subnet-{c}", parent=f"zone-{c}")
        for s in range(per_container):
            ops.add_node(d, f"svc-{c}-{s}", kind="service", id=f"svc-{c}-{s}", parent=f"subnet-{c}")
            if s:
                ops.add_edge(d, f"svc-{c}-{s - 1}", f"svc-{c}-{s}", label="http")
    ops.add_node(d, "edge-gw", kind="gateway", id="edge-gw")
    for c in range(containers):
        ops.add_edge(d, "edge-gw", f"svc-{c}-0", label="https")
    return d


def test_summary_without_budget_is_complete():
    d = big_diagram()
    text = d.summary()
    assert text.count("- [svc-") == 48 and "collapsed" not in text
    assert d.summary(budget=len(text)) == text  # exactly at the budget: nothing collapses


def test_summary_collapses_largest_containers_until_it_fits():
    d = big_diagram()
    full = d.summary()
    text = d.summary(budget=len(full) // 2)
    assert len(text) <= len(full) // 2
    assert "collapsed: 13 elements, 1 containers inside" in text
    assert 'call get_diagram with focus="zone-' in text
    # top level stays visible, including the gateway and the diagram-level note
    assert "- [edge-gw] «gateway» edge-gw" in text
    assert "Note:" in text and "edges touching collapsed containers" in text
    # a small budget collapses every zone; the top level stays while it fits
    collapsed = d._summary(set(d.nodes) & {f"zone-{c}" for c in range(4)}, 0)
    small = d.summary(budget=len(collapsed))
    assert small == collapsed and small.count("(collapsed:") == 4 and "- [edge-gw]" in small
    assert "- [svc-" not in small
    assert "Edges:" not in small  # every edge touches a collapsed container
    # a tiny one cuts the top level too (#115): leaves first, counted, with a hint to call get_diagram
    tiny = d.summary(budget=400)
    assert len(tiny) <= 400 + SUMMARY_SLACK
    assert "- [edge-gw]" not in tiny and "more elements not shown" in tiny and "call get_diagram" in tiny


def test_a_flat_diagram_respects_the_budget():
    """#115: 2,000 top-level services and 1,999 edges gave a 147,351-character prompt at a budget of 12,000."""
    d = Diagram(id="flat", title="Flach")
    for i in range(2000):
        d.nodes[f"s{i}"] = Node(id=f"s{i}", name=f"Service {i}", kind="service")
    d.edges = [Edge(id=f"e{i}", source=f"s{i - 1}", target=f"s{i}", label="ruft") for i in range(1, 2000)]
    text = d.summary(budget=12_000)
    assert len(text) <= 12_000
    assert "- [s0] «service» Service 0" in text  # the beginning is kept as it was
    assert "Edges:" in text and "more edges not shown; call get_diagram" in text
    elements = d.summary(budget=80_000)  # every element fits, only edges are cut
    assert "- [s1999]" in elements and "more elements" not in elements and "more edges not shown" in elements


def test_a_long_sequence_respects_the_budget():
    d = Diagram(id="seq", title="T" * 5000, type="sequence")
    ops.apply(d, "add_lifeline", {"name": "Kasse"})
    ops.apply(d, "add_lifeline", {"name": "Lager"})
    for i in range(300):
        ops.apply(d, "add_message", {"sender": "kasse", "receiver": "lager", "name": f"Nachricht {i}"})
    text = d.summary(budget=2_000)
    assert len(text) <= 2_000 + SUMMARY_SLACK and "T" * 201 not in text  # a long title is cut too
    assert "more lines of the interaction not shown" in text and "call get_diagram" in text


def test_summary_budget_on_small_diagram_is_a_no_op():
    d = demo_diagram()
    assert d.summary(budget=12_000) == d.summary()


def test_subset_by_focus_and_ids():
    d = big_diagram(containers=2, per_container=3)
    sub = d.subset(focus="zone-1")
    assert set(sub["nodes"]) == {"zone-1", "subnet-1", "svc-1-0", "svc-1-1", "svc-1-2"}
    assert {e["source"] for e in sub["edges"]} == {"edge-gw", "svc-1-0", "svc-1-1"}  # crossing edge kept
    assert sub["partial"] is True

    by_ids = d.subset(ids=["svc-0-2", "Edge-GW"])  # names resolve case-insensitively
    assert set(by_ids["nodes"]) == {"svc-0-2", "subnet-0", "zone-0", "edge-gw"}  # ancestors included
    assert d.subset()["partial"] is False and len(d.subset()["nodes"]) == len(d.nodes)
    json.dumps(sub)  # JSON-able


def test_edges_stored_without_direction_or_properties_load_with_defaults():
    raw = {"id": "d", "title": "d", "nodes": {}, "edges": [{"id": "e1", "source": "a", "target": "b"}]}
    edge = Diagram.model_validate(raw).edges[0]
    assert edge.direction is None and edge.properties == {} and edge.kind == "connection"


def test_summary_shows_edge_direction_and_properties():
    d = demo_diagram()
    ops.update_edge(d, "e1", direction="both", properties={"protocol": "https", "port": "443"})
    text = d.summary()
    assert '- [e1] api-gateway -> service-a (connection, direction=both) "https" {port=443, protocol=https}' in text
    assert '- [e2] api-gateway -> service-b (connection) "https"' in text
