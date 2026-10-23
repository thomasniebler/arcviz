"""Version comparison (spec: docs/architecture/specs/version-comparison.md)."""

import re

import pytest
from fastapi.testclient import TestClient

from arcviz import diff, mermaid
from arcviz import operations as ops
from arcviz.server import create_app
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider


def _pair():
    old = demo_diagram("demo", "Demo")
    new = old.model_copy(deep=True)
    ops.add_node(new, "prod-vnet", kind="vnet", id="prod-vnet", parent="prod-deployment")
    ops.move_node(new, "service-a", "prod-vnet")  # changed: parent
    ops.update_node(new, "orders-db", properties={"engine": "pg"})  # changed: properties
    ops.remove_node(new, "events")  # removed with edges e4 (service-b) and e5 (service-c)
    ops.update_edge(new, "e1", label="https/2")  # changed edge
    ops.add_edge(new, "service-c", "orders-db", label="sql")  # added edge, reuses the freed id e5
    return old, new


def test_compare_reports_nodes_and_edges():
    old, new = _pair()
    d = diff.compare(old, new)
    assert d.added_nodes == ["prod-vnet"]
    assert d.removed_nodes == ["events"]
    assert d.changed_nodes == ["orders-db", "service-a"]
    before = {e.id: (e.source, e.target) for e in old.edges}
    after = {e.id: (e.source, e.target) for e in new.edges}
    removed = [i for i in before if after.get(i) != before[i]]  # events' edges; e5 is reused by the new edge
    added = [i for i in after if before.get(i) != after[i]]
    assert d.removed_edges == sorted(removed) and d.added_edges == sorted(added) == ["e5"]
    assert d.changed_edges == ["e1"]
    assert not d.title_changed and not d.direction_changed
    out = d.to_dict()
    assert out["summary"] == {"added": 1 + len(added), "removed": 1 + len(removed), "changed": 3}


@pytest.mark.parametrize(
    "change",
    [
        lambda d: ops.update_node(d, "idp", name="IdP"),
        lambda d: ops.update_node(d, "idp", kind="service"),
        lambda d: ops.update_node(d, "idp", properties={"x": "1"}),
        lambda d: ops.move_node(d, "idp", "prod-deployment"),
    ],
)
def test_every_node_attribute_counts_as_a_change(change):
    old = demo_diagram()
    new = old.model_copy(deep=True)
    change(new)
    assert diff.compare(old, new).changed_nodes == ["idp"]


def test_reused_edge_id_with_new_endpoints_is_removed_and_added():
    old = demo_diagram()
    new = old.model_copy(deep=True)
    edge = new.edges[0]
    edge.target = "idp"
    d = diff.compare(old, new)
    assert d.removed_edges == [edge.id] and d.added_edges == [edge.id] and d.changed_edges == []


def test_title_direction_and_identity():
    old = demo_diagram()
    new = old.model_copy(deep=True)
    assert diff.compare(old, new).to_dict()["summary"] == {"added": 0, "removed": 0, "changed": 0}
    ops.rename_diagram(new, "Other")
    ops.set_direction(new, "LR")
    d = diff.compare(old, new)
    assert d.title_changed and d.direction_changed


def test_merged_view_keeps_removed_elements_in_place():
    old, new = _pair()
    ops.add_node(old, "legacy", kind="subnet", id="legacy", parent="prod-deployment")
    ops.add_node(old, "batch", kind="service", id="batch", parent="legacy")
    ops.add_node(old, "tmp-group", kind="group", id="tmp-group")
    ops.add_node(old, "orphan", kind="service", id="orphan", parent="tmp-group")
    new.nodes["tmp-group"] = old.nodes["tmp-group"].model_copy(update={"kind": "service"})  # container became a leaf
    d = diff.compare(old, new)
    view, marks, edge_marks = diff.merged(old, new, d)
    assert view.title == new.title and view.direction == new.direction
    assert view.nodes["events"].parent == "prod-deployment"  # surviving container
    assert view.nodes["legacy"].parent == "prod-deployment" and view.nodes["batch"].parent == "legacy"
    assert view.nodes["orphan"].parent is None  # old parent is a leaf now
    assert view.integrity_errors() == []
    assert marks["prod-vnet"] == "added" and marks["events"] == "removed" and marks["service-a"] == "changed"
    assert marks["batch"] == "removed" and marks["tmp-group"] == "changed"
    removed_ids = [f"-{e}" for e in d.removed_edges]
    assert all(edge_marks[r] == "removed" for r in removed_ids)
    assert {e.id for e in view.edges} >= set(removed_ids)
    assert edge_marks["e1"] == "changed"
    # the inputs are untouched
    assert "events" not in new.nodes and "prod-vnet" not in old.nodes


def test_render_marks_nodes_and_edges():
    old, new = _pair()
    view, marks, edge_marks = diff.merged(old, new, diff.compare(old, new))
    src = mermaid.render(view, marks=marks, edge_marks=edge_marks)
    lines = [line.strip() for line in src.splitlines()]
    assert "class n_prod_vnet added" in lines
    assert "class n_events removed" in lines
    assert "class n_orders_db,n_service_a changed" in lines
    assert any(line.startswith("classDef removed ") and "stroke-dasharray" in line for line in lines)
    edge_lines = [line for line in lines if re.match(r"^n_\w+ (-->|-\.->|==>|---|<-->)", line)]
    for i, edge in enumerate(e for e in view.edges):
        mark = edge_marks.get(edge.id)
        styled = [line for line in lines if line.startswith(f"linkStyle {i} ")]
        assert bool(styled) == bool(mark), (edge.id, mark, styled)
    assert len(edge_lines) == len(view.edges)


def test_link_indices_count_only_emitted_edges():
    old, new = _pair()
    view, marks, edge_marks = diff.merged(old, new, diff.compare(old, new))
    src = mermaid.render(view, marks=marks, edge_marks=edge_marks, focus="prod-vnet")
    emitted = [e for e in view.edges if "service-a" in (e.source, e.target)]
    for i, edge in enumerate(emitted):
        has_style = any(line.strip().startswith(f"linkStyle {i} ") for line in src.splitlines())
        assert has_style == (edge.id in edge_marks)
    assert f"linkStyle {len(emitted)} " not in src


@pytest.fixture
def client(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    client.post(
        "/api/diagrams/demo/ops",
        json={"op": "add_node", "args": {"name": "cache", "kind": "storage", "parent": "prod-deployment"}},
    )
    client.post("/api/diagrams/demo/ops", json={"op": "remove_node", "args": {"id": "events"}})
    return client


def test_compare_endpoint(client):
    r = client.get("/api/diagrams/demo/compare?base=1")
    assert r.status_code == 200
    body = r.json()
    assert body["base"]["version"] == 1 and body["target"] is None
    assert body["diff"]["added_nodes"] == ["cache"] and body["diff"]["removed_nodes"] == ["events"]
    assert "class n_cache added" in body["mermaid"] and "class n_events removed" in body["mermaid"]
    assert body["mermaid_ids"]["events"] == "n_events" and body["focus"] is None
    r = client.get("/api/diagrams/demo/compare?base=1&target=2")
    assert r.json()["target"]["version"] == 2 and r.json()["diff"]["removed_nodes"] == []
    r = client.get("/api/diagrams/demo/compare?base=2&target=2")
    assert r.json()["diff"]["summary"] == {"added": 0, "removed": 0, "changed": 0}
    r = client.get("/api/diagrams/demo/compare?base=1&focus=prod-deployment")
    assert r.json()["focus"] == "prod-deployment" and "n_events" in r.json()["mermaid"]
    assert client.get("/api/diagrams/demo/compare?base=1&focus=ghost").json()["focus"] is None
    assert client.get("/api/diagrams/demo/compare?base=9").status_code == 404
    assert client.get("/api/diagrams/demo/compare?base=1&target=9").status_code == 404
    assert client.get("/api/diagrams/nope/compare?base=1").status_code == 404
    assert client.get("/api/diagrams/demo/compare").status_code == 422


def test_removed_edge_ids_never_collide_with_real_ones():
    old = demo_diagram()
    new = old.model_copy(deep=True)
    removed = new.edges.pop(0)  # e1 is removed ...
    new.edges.append(removed.model_copy(update={"id": f"-{removed.id}"}))  # ... and an imported edge is called "-e1"
    new.edges.append(removed.model_copy(update={"id": f"--{removed.id}", "label": "x"}))
    d = diff.compare(old, new)
    view, _, edge_marks = diff.merged(old, new, d)
    ids = [e.id for e in view.edges]
    assert len(ids) == len(set(ids))
    assert edge_marks[f"-{removed.id}"] == "added" and edge_marks[f"--{removed.id}"] == "added"
    assert edge_marks[f"---{removed.id}"] == "removed"


def test_edge_direction_and_property_changes_count_as_changed():
    old = demo_diagram("demo", "Demo")
    new = old.model_copy(deep=True)
    first, second = new.edges[0].id, new.edges[1].id
    ops.update_edge(new, first, direction="both")
    ops.update_edge(new, second, properties={"port": "443"})
    assert diff.compare(old, new).changed_edges == sorted([first, second])
