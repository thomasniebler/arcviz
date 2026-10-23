"""Configurable element kinds (spec docs/architecture/specs/configurable-kinds.md)."""

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import mermaid
from arcviz import operations as ops
from arcviz.agent import TOOLS, build_system_prompt, build_tools
from arcviz.kinds import KindConfigError, load_kind_catalog
from arcviz.model import (
    ALL_KINDS,
    BUILTIN_KINDS,
    CONTAINER_KINDS,
    EDGE_KINDS,
    LEAF_KINDS,
    NODE_SHAPES,
    Diagram,
    active_kinds,
    use_kinds,
)
from arcviz.server import create_app

AWS = {
    "node_kinds": {
        "vpc": {"container": True, "style": "fill:#fff4e5,stroke:#ff9900,color:#232f3e"},
        "lambda": {"shape": "asymmetric", "style": "fill:#fff4e5,stroke:#ff9900,color:#232f3e"},
        "s3": {"shape": "cylinder"},
    },
    "edge_kinds": {"peering": {"line": "dotted", "direction": "both"}, "invokes": {}},
}


def write(tmp_path, content) -> str:
    path = tmp_path / "kinds.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(path)


def test_builtin_catalogue_matches_the_constants():
    assert active_kinds() is BUILTIN_KINDS
    assert BUILTIN_KINDS.container_kinds == CONTAINER_KINDS and BUILTIN_KINDS.leaf_kinds == LEAF_KINDS
    assert BUILTIN_KINDS.all_kinds == ALL_KINDS and BUILTIN_KINDS.edge_kinds == EDGE_KINDS


def test_a_file_extends_the_builtin_kinds(tmp_path):
    catalog = load_kind_catalog(write(tmp_path, AWS))
    assert catalog.container_kinds == CONTAINER_KINDS | {"vpc"}
    assert catalog.leaf_kinds == LEAF_KINDS | {"lambda", "s3"}
    assert catalog.edge_kinds == EDGE_KINDS | {"peering", "invokes"}
    assert catalog.node_shapes == {"lambda": "asymmetric", "s3": "cylinder"}
    assert catalog.edge_lines == {"peering": ("dotted", "both"), "invokes": ("solid", "forward")}
    assert set(catalog.node_styles) == {"vpc", "lambda"}
    assert load_kind_catalog(write(tmp_path, {})).all_kinds == ALL_KINDS


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "not valid JSON"),
        ([], "must be a JSON object"),
        ({"kinds": {}}, "unknown key 'kinds'"),
        ({"node_kinds": []}, "'node_kinds' must be an object"),
        ({"node_kinds": {"vnet": {}}}, "node kind 'vnet': 'vnet' is a built-in kind"),
        ({"edge_kinds": {"connection": {}}}, "edge kind 'connection': 'connection' is a built-in kind"),
        ({"node_kinds": {"connection": {}}}, "node kind 'connection': 'connection' is a built-in kind"),
        ({"edge_kinds": {"database": {}}}, "edge kind 'database': 'database' is a built-in kind"),
        ({"node_kinds": {"aws-vpc": {}}}, "invalid kind name 'aws-vpc'"),
        ({"node_kinds": {"Vpc": {}}}, "invalid kind name 'Vpc'"),
        ({"node_kinds": {"end": {}}}, "'end' is reserved"),
        ({"node_kinds": {"changed": {}}}, "'changed' is reserved"),
        ({"node_kinds": {"x": {}}, "edge_kinds": {"x": {}}}, "'x' is both a node and an edge kind"),
        ({"node_kinds": {"x": "leaf"}}, "node kind 'x' must be an object"),
        ({"node_kinds": {"x": {"colour": "red"}}}, "node kind 'x': unknown key 'colour'"),
        ({"node_kinds": {"x": {"container": "yes"}}}, "node kind 'x': 'container' must be true or false"),
        ({"node_kinds": {"x": {"shape": "star"}}}, "node kind 'x': unknown shape 'star'"),
        ({"node_kinds": {"x": {"style": "fill:red;}\nclick x call alert()"}}}, "node kind 'x': invalid style"),
        ({"node_kinds": {"x": {"style": "a" * 201}}}, "node kind 'x': invalid style"),
        ({"edge_kinds": {"y": {"line": "wavy"}}}, "edge kind 'y': unknown line 'wavy'"),
        ({"edge_kinds": {"y": {"direction": "up"}}}, "edge kind 'y': unknown direction 'up'"),
        ({"edge_kinds": {"y": {"arrow": "-->"}}}, "edge kind 'y': unknown key 'arrow'"),
    ],
)
def test_invalid_files_are_rejected_with_a_precise_message(tmp_path, content, message):
    path = write(tmp_path, content)
    with pytest.raises(KindConfigError, match=message) as exc:
        load_kind_catalog(path)
    assert path in str(exc.value)


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(KindConfigError, match="cannot be read"):
        load_kind_catalog(str(tmp_path / "missing.json"))


def test_operations_follow_the_active_catalogue(tmp_path):
    d = Diagram(id="d", title="d")
    with pytest.raises(ops.OperationError, match="Unknown node kind 'vpc'"):
        ops.add_node(d, "main", kind="vpc")
    use_kinds(load_kind_catalog(write(tmp_path, AWS)))
    vpc = ops.add_node(d, "main", kind="VPC")
    assert vpc.is_container
    fn = ops.add_node(d, "resize", kind="lambda", parent="main")
    with pytest.raises(ops.OperationError, match="cannot contain.*Container kinds are: .*vpc"):
        ops.add_node(d, "x", kind="s3", parent=fn.id)
    ops.add_edge(d, "resize", "main", kind="invokes")
    with pytest.raises(ops.OperationError, match="still contains"):
        ops.update_node(d, "main", kind="s3")
    with pytest.raises(ops.OperationError, match="Allowed kinds: .*peering"):
        ops.add_edge(d, "resize", "main", kind="calls")


def test_rendering_uses_configured_styles_shapes_and_lines(tmp_path):
    use_kinds(load_kind_catalog(write(tmp_path, AWS)))
    d = Diagram(id="d", title="d")
    ops.add_node(d, "main", kind="vpc", id="main")
    ops.add_node(d, "fn", kind="lambda", id="fn", parent="main")
    ops.add_node(d, "bucket", kind="s3", id="bucket", parent="main")
    ops.add_edge(d, "fn", "bucket", kind="peering")
    ops.add_edge(d, "bucket", "fn", kind="invokes", direction="none")
    lines = mermaid.render(d).splitlines()
    assert '  subgraph n_main["«vpc» main"]' in lines
    assert '    n_fn>"«lambda»<br/>fn"]' in lines
    assert '    n_bucket[("«s3»<br/>bucket")]' in lines
    assert "  n_fn <-.-> n_bucket" in lines and "  n_bucket --- n_fn" in lines
    assert "  classDef vpc fill:#fff4e5,stroke:#ff9900,color:#232f3e" in lines
    assert "  class n_fn lambda" in lines
    assert not any(line.startswith("  classDef s3") for line in lines)  # no style: theme default


def test_every_shape_name_has_mermaid_syntax():
    assert set(mermaid.SHAPE_SYNTAX) == set(NODE_SHAPES)


def test_node_with_children_of_an_unconfigured_kind_still_renders_as_subgraph(tmp_path):
    use_kinds(load_kind_catalog(write(tmp_path, AWS)))
    d = Diagram(id="d", title="d")
    ops.add_node(d, "main", kind="vpc", id="main")
    ops.add_node(d, "svc", kind="service", id="svc", parent="main")
    use_kinds(BUILTIN_KINDS)  # the kind was removed from the file
    src = mermaid.render(d)
    assert 'subgraph n_main["«vpc» main"]' in src and "n_svc" in src
    ops.update_node(d, "main", name="Main VPC")  # still editable
    with pytest.raises(ops.OperationError, match="cannot contain"):
        ops.add_node(d, "x", kind="service", parent="main")


def test_tools_and_prompt_list_configured_kinds(tmp_path):
    assert [t.name for t in build_tools(BUILTIN_KINDS)] == [t.name for t in TOOLS]
    catalog = load_kind_catalog(write(tmp_path, AWS))
    use_kinds(catalog)
    tools = {t.name: t for t in build_tools(catalog)}
    assert "vpc" in tools["add_node"].parameters["properties"]["kind"]["enum"]
    assert "vpc" in tools["add_node"].description
    assert "peering" in tools["add_edge"].parameters["properties"]["kind"]["enum"]
    assert "peering" in tools["update_edge"].parameters["properties"]["kind"]["enum"]
    assert "lambda" in build_system_prompt(Diagram(id="d", title="d"))


def test_server_activates_the_file_and_refuses_an_invalid_one(tmp_path, monkeypatch, store):
    monkeypatch.setenv("ARCVIZ_KINDS_FILE", write(tmp_path, AWS))
    config = TestClient(create_app(store=store)).get("/api/config").json()
    assert "vpc" in config["container_kinds"] and "lambda" in config["node_kinds"]
    assert "peering" in config["edge_kinds"]
    monkeypatch.setenv("ARCVIZ_KINDS_FILE", write(tmp_path, {"node_kinds": {"vnet": {}}}))
    with pytest.raises(KindConfigError, match="built-in kind"):
        create_app(store=store)
    monkeypatch.delenv("ARCVIZ_KINDS_FILE")
    create_app(store=store)
    assert active_kinds() is BUILTIN_KINDS


def test_configured_kinds_survive_export_and_import(tmp_path):
    from arcviz import exchange

    use_kinds(load_kind_catalog(write(tmp_path, AWS)))
    d = Diagram(id="aws", title="AWS")
    ops.add_node(d, "main-vpc", kind="vpc")
    ops.add_node(d, "fn", kind="lambda", parent="main-vpc")
    ops.add_node(d, "bucket", kind="s3")
    ops.add_edge(d, "fn", "bucket", kind="invokes")
    assert exchange.parse_document(exchange.export_document(d)).nodes["fn"].parent == "main-vpc"


def test_comparison_keeps_removed_elements_inside_configured_containers(tmp_path):
    from arcviz import diff

    use_kinds(load_kind_catalog(write(tmp_path, AWS)))
    old = Diagram(id="aws", title="AWS")
    ops.add_node(old, "main-vpc", kind="vpc")
    ops.add_node(old, "fn", kind="lambda", parent="main-vpc")
    new = old.model_copy(deep=True)
    ops.remove_node(new, "fn")
    view, _, _ = diff.merged(old, new, diff.compare(old, new))
    assert view.nodes["fn"].parent == "main-vpc"
