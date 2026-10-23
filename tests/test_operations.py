import pytest

from arcviz import operations as ops
from arcviz.model import Diagram
from arcviz.templates import demo_diagram


def test_add_vnet_and_move_services_keeps_edges():
    d = demo_diagram()
    vnet = ops.add_node(d, "prod-vnet", kind="vnet", parent="prod-deployment", properties={"cidr": "10.0.0.0/16"})
    for svc in ("service-a", "service-b", "service-c"):
        ops.move_node(d, svc, vnet.id)
    assert {n.id for n in d.children(vnet.id)} == {"service-a", "service-b", "service-c"}
    assert d.nodes["service-a"].parent == "prod-vnet"
    assert d.ancestors("service-a") == ["prod-vnet", "prod-deployment"]
    assert len(d.edges) == 6  # untouched
    assert d.integrity_errors() == []


def test_resolve_by_name_is_case_insensitive():
    d = demo_diagram()
    assert d.find_node("Identity Provider").id == "idp"
    assert d.find_node("IDENTITY PROVIDER").id == "idp"
    assert d.find_node("nope") is None


def test_add_node_treats_a_blank_id_as_none():
    # found by tests/test_properties.py: " " used to create a node with the empty id ""
    d = Diagram(id="x", title="x")
    assert ops.add_node(d, "Web App", id="   ").id == "web-app"
    assert ops.add_node(d, "Web App", id="").id == "web-app-2"
    assert ops.add_node(d, "api", id=" svc ").id == "svc"


def test_add_node_generates_unique_ids():
    d = Diagram(id="x", title="x")
    a = ops.add_node(d, "Service A", kind="service")
    b = ops.add_node(d, "Service A", kind="service")
    assert a.id == "service-a"
    assert b.id == "service-a-2"


def test_parent_must_be_container():
    d = demo_diagram()
    with pytest.raises(ops.OperationError, match="cannot contain"):
        ops.add_node(d, "x", kind="service", parent="service-a")


def test_move_into_own_descendant_is_rejected():
    d = demo_diagram()
    ops.add_node(d, "sub", kind="subnet", id="sub", parent="prod-deployment")
    with pytest.raises(ops.OperationError, match="cycle"):
        ops.move_node(d, "prod-deployment", "sub")


def test_remove_node_reparents_children_and_drops_edges():
    d = demo_diagram()
    result = ops.remove_node(d, "prod-deployment")
    assert "prod-deployment" not in d.nodes
    assert d.nodes["service-a"].parent is None
    assert sorted(result["reparented"]) == ["api-gateway", "events", "orders-db", "service-a", "service-b", "service-c"]
    assert len(d.edges) == 6  # no edge touched the container itself


def test_remove_node_cascade():
    d = demo_diagram()
    result = ops.remove_node(d, "prod-deployment", cascade=True)
    assert set(d.nodes) == {"idp"}
    assert d.edges == []
    assert len(result["removed_edges"]) == 6


def test_add_edge_is_idempotent_and_validates_kind():
    d = demo_diagram()
    before = len(d.edges)
    e = ops.add_edge(d, "service-a", "service-b", label="grpc")
    again = ops.add_edge(d, "service-a", "service-b", label="grpc")
    assert e.id == again.id
    assert len(d.edges) == before + 1
    with pytest.raises(ops.OperationError, match="Unknown edge kind"):
        ops.add_edge(d, "service-a", "service-b", kind="teleport")


def test_remove_edge_by_pair_and_by_id():
    d = demo_diagram()
    removed = ops.remove_edge(d, source="api-gateway", target="service-a")
    assert removed == ["e1"]
    assert ops.remove_edge(d, id="e2") == ["e2"]
    with pytest.raises(ops.OperationError):
        ops.remove_edge(d, id="e999")


def test_apply_rolls_back_on_error():
    d = demo_diagram()
    snapshot = d.model_dump(exclude={"updated_at"})
    with pytest.raises(ops.OperationError):
        ops.apply(d, "add_node", {"name": "x", "kind": "service", "parent": "does-not-exist"})
    with pytest.raises(ops.OperationError, match="Invalid arguments"):
        ops.apply(d, "add_node", {"name": "x", "kind": "service", "bogus": 1})
    assert d.model_dump(exclude={"updated_at"}) == snapshot


def test_apply_reports_touched_ids():
    d = demo_diagram()
    res = ops.apply(d, "add_edge", {"source": "service-a", "target": "service-c"})
    assert res["ok"] and res["touched"] == ["service-a", "service-c"]
    res = ops.apply(d, "update_node", {"id": "service-a", "properties": {"replicas": 3}})
    assert d.nodes["service-a"].properties["replicas"] == "3"
    assert res["touched"] == ["service-a"]


def test_update_node_to_leaf_kind_requires_empty_container():
    d = demo_diagram()
    with pytest.raises(ops.OperationError, match="still contains"):
        ops.update_node(d, "prod-deployment", kind="service")


# ---- apply_batch (spec docs/architecture/specs/batch-operations.md) ----------


def test_batch_builds_structure_with_references_to_earlier_steps():
    d = demo_diagram()
    res = ops.apply(
        d,
        "apply_batch",
        {
            "operations": [
                {"op": "add_node", "args": {"name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"}},
                {"op": "add_node", "args": {"name": "Cache", "kind": "storage", "parent": "prod-vnet"}},
                {"op": "move_node", "args": {"id": "service-a", "parent": "prod-vnet"}},
                {"op": "add_edge", "args": {"source": "service-a", "target": "Cache", "label": "redis"}},
            ]
        },
    )
    assert d.nodes["cache"].parent == "prod-vnet" and d.nodes["service-a"].parent == "prod-vnet"
    assert any(e.source == "service-a" and e.target == "cache" for e in d.edges)
    assert res["ok"] and res["op"] == "apply_batch"
    assert [r["op"] for r in res["result"]["results"]] == ["add_node", "add_node", "move_node", "add_edge"]
    assert res["touched"] == ["prod-vnet", "cache", "service-a"]
    assert d.integrity_errors() == []


def test_batch_failure_leaves_diagram_unchanged_and_names_the_step():
    d = demo_diagram()
    before = d.model_dump(exclude={"updated_at"})
    batch = [
        {"op": "add_node", "args": {"name": "prod-vnet", "kind": "vnet", "parent": "prod-deployment"}},
        {"op": "rename_diagram", "args": {"title": "Changed"}},
        {"op": "move_node", "args": {"id": "service-a", "parent": "orders-db"}},
    ]
    with pytest.raises(ops.OperationError, match=r"Batch step 3 \(move_node\) failed: .*cannot contain.*No step"):
        ops.apply(d, "apply_batch", {"operations": batch})
    assert d.model_dump(exclude={"updated_at"}) == before
    # also atomic when called directly, without the dispatcher's own rollback
    with pytest.raises(ops.OperationError, match="Batch step 3"):
        ops.apply_batch(d, batch)
    assert d.model_dump(exclude={"updated_at"}) == before


def test_batch_reports_wrong_arguments_of_a_step():
    d = demo_diagram()
    with pytest.raises(ops.OperationError, match=r"Batch step 1 \(add_node\) failed: Invalid arguments"):
        ops.apply(d, "apply_batch", {"operations": [{"op": "add_node", "args": {"name": "x", "bogus": 1}}]})


@pytest.mark.parametrize(
    ("operations", "message"),
    [
        ([], "at least one"),
        ("add_node", "list"),
        ([{"op": "add_node", "args": {"name": f"n{i}"}} for i in range(ops.MAX_BATCH + 1)], "at most"),
        (["add_node"], "step 1 must be an object"),
        ([{"args": {}}], "step 1 needs an 'op'"),
        ([{"op": "explode"}], "Unknown operation 'explode' in step 1"),
        ([{"op": "apply_batch", "args": {"operations": []}}], "cannot be nested"),
        ([{"op": "rename_diagram", "args": ["x"]}], "args of step 1 must be an object"),
    ],
)
def test_batch_shape_is_validated(operations, message):
    d = demo_diagram()
    with pytest.raises(ops.OperationError, match=message):
        ops.apply(d, "apply_batch", {"operations": operations})


def test_batch_shape_is_validated_before_the_first_step():
    d = demo_diagram()
    before = d.model_dump(exclude={"updated_at"})
    batch = [{"op": "rename_diagram", "args": {"title": "Changed"}}, {"op": "explode"}]
    with pytest.raises(ops.OperationError, match="step 2"):
        ops.apply_batch(d, batch)
    assert d.model_dump(exclude={"updated_at"}) == before


def test_batch_args_may_be_omitted():
    d = demo_diagram()
    with pytest.raises(ops.OperationError, match="Invalid arguments"):
        ops.apply(d, "apply_batch", {"operations": [{"op": "rename_diagram"}]})


def test_batch_restores_on_unexpected_exceptions_and_reraises_them(monkeypatch):
    def broken(diagram, id: str) -> None:
        diagram.nodes.pop(id)
        raise AttributeError("defect")

    monkeypatch.setitem(ops.OPERATIONS, "remove_edge", broken)  # an internal defect, not an input error
    d = demo_diagram()
    before = d.model_dump()
    batch = [
        {"op": "rename_diagram", "args": {"title": "Changed"}},
        {"op": "remove_edge", "args": {"id": "service-a"}},
    ]
    with pytest.raises(AttributeError):
        ops.apply(d, "apply_batch", {"operations": batch})
    assert d.model_dump() == before


def test_batch_with_an_ill_typed_step_is_a_controlled_error():  # the #66 example inside a batch
    d = demo_diagram()
    before = d.model_dump()
    batch = [
        {"op": "rename_diagram", "args": {"title": "Changed"}},
        {"op": "update_node", "args": {"id": "service-a", "name": "renamed", "kind": 42}},
    ]
    with pytest.raises(ops.OperationError, match=r"Batch step 2 \(update_node\) failed: Invalid arguments"):
        ops.apply(d, "apply_batch", {"operations": batch})
    assert d.model_dump() == before


def test_batch_touched_skips_nodes_removed_later_in_the_batch():
    d = demo_diagram()
    res = ops.apply(
        d,
        "apply_batch",
        {
            "operations": [
                {"op": "add_node", "args": {"name": "tmp", "kind": "service"}},
                {"op": "update_node", "args": {"id": "service-b", "name": "Service B"}},
                {"op": "remove_node", "args": {"id": "tmp"}},
            ]
        },
    )
    assert res["touched"] == ["service-b"]


# ---- edge direction and properties (spec docs/architecture/specs/edge-attributes.md) ----


def test_add_edge_with_direction_and_properties():
    d = demo_diagram()
    e = ops.add_edge(d, "service-a", "service-b", kind="dependency", direction="Both", properties={"port": 8080})
    assert e.direction == "both" and e.properties == {"port": "8080"}
    assert ops.add_edge(d, "service-a", "service-c", direction="default").direction is None


def test_add_edge_idempotency_includes_direction_and_merges_properties():
    d = demo_diagram()
    count = len(d.edges)
    same = ops.add_edge(d, "api-gateway", "service-a", label="https", properties={"protocol": "https"})
    assert same.id == "e1" and same.properties == {"protocol": "https"} and len(d.edges) == count
    other = ops.add_edge(d, "api-gateway", "service-a", label="https", direction="both")
    assert other.id != "e1" and len(d.edges) == count + 1


def test_update_edge_direction_properties_and_reset():
    d = demo_diagram()
    ops.update_edge(d, "e1", direction="none", properties={"protocol": "grpc", "port": "50051"})
    ops.update_edge(d, "e1", remove_properties=["port"], properties={"tls": "true"})
    edge = d.edges[0]
    assert edge.direction == "none" and edge.properties == {"protocol": "grpc", "tls": "true"}
    ops.update_edge(d, "e1", direction="default")
    assert d.edges[0].direction is None


@pytest.mark.parametrize(
    ("op", "args", "message"),
    [
        ("add_edge", {"source": "service-a", "target": "service-b", "direction": "sideways"}, "Unknown edge direction"),
        ("add_edge", {"source": "service-a", "target": "service-b", "properties": ["x"]}, "must be an object"),
        ("update_edge", {"id": "e1", "label": "changed", "direction": "up"}, "Unknown edge direction"),
        ("update_edge", {"id": "e1", "label": "changed", "kind": "bogus"}, "Unknown edge kind"),
        ("update_edge", {"id": "e1", "label": "changed", "properties": "port=1"}, "must be an object"),
        ("update_edge", {"id": "e1", "label": "changed", "remove_properties": "port"}, "must be a list"),
    ],
)
def test_invalid_edge_arguments_change_nothing(op, args, message):
    d = demo_diagram()
    before = d.model_dump(exclude={"updated_at"})
    with pytest.raises(ops.OperationError, match=message):
        getattr(ops, op)(d, **args)  # validated before any assignment, even without apply()'s rollback
    assert d.model_dump(exclude={"updated_at"}) == before


def test_failed_batch_also_rolls_back_the_layout_direction():
    d = demo_diagram("demo", "Demo")
    steps = [{"op": "set_direction", "args": {"direction": "LR"}}, {"op": "remove_node", "args": {"id": "nope"}}]
    with pytest.raises(ops.OperationError, match="Batch step 2"):
        ops.apply(d, "apply_batch", {"operations": steps})
    assert d.direction == "TB"
