import pytest

from arcviz import mermaid
from arcviz import operations as ops
from arcviz.model import CONTAINER_KINDS, EDGE_KINDS, LEAF_KINDS, Diagram, Edge, KindCatalog, use_kinds
from arcviz.templates import demo_diagram


def test_render_nests_subgraphs_and_marks_changes():
    d = demo_diagram()
    ops.add_node(d, "prod-vnet", kind="vnet", id="prod-vnet", parent="prod-deployment")
    ops.move_node(d, "service-a", "prod-vnet")
    src = mermaid.render(d, highlight=["service-a", "prod-vnet"])
    lines = [line.strip() for line in src.splitlines()]
    assert lines[0] == "flowchart TB"
    assert 'subgraph n_prod_deployment["«deployment» prod-deployment"]' in lines
    assert 'subgraph n_prod_vnet["«vnet» prod-vnet"]' in lines
    # service-a is nested two levels deep
    idx_vnet = lines.index('subgraph n_prod_vnet["«vnet» prod-vnet"]')
    idx_svc = lines.index('n_service_a["«service»<br/>service-a"]')
    idx_end = lines.index("end", idx_vnet)
    assert idx_vnet < idx_svc < idx_end
    assert 'n_api_gateway -.->|"oidc"| n_idp' in lines
    assert "class n_prod_vnet,n_service_a changed" in lines


def test_ids_and_labels_are_escaped():
    d = Diagram(id="x", title="x")
    ops.add_node(d, 'The "end" service', kind="service", id="end")
    ops.add_node(d, "graph", kind="database", id="graph")
    ops.add_edge(d, "end", "graph", label='say "hi"')
    src = mermaid.render(d)
    assert 'n_end["«service»<br/>The #quot;end#quot; service"]' in src
    assert 'n_graph[("«database»<br/>graph")]' in src
    assert 'n_end -->|"say #quot;hi#quot;"| n_graph' in src


def test_markup_in_names_is_neutralised():
    d = Diagram(id="x", title="x")
    ops.add_node(d, '<img src=x onerror="alert(1)"> & co', kind="service", id="evil")
    ops.add_node(d, "db", kind="database", id="db")
    ops.add_edge(d, "evil", "db", label="<b>bold</b>")
    src = mermaid.render(d)
    assert "<img" not in src and "<b>" not in src
    assert 'n_evil["«service»<br/>#lt;img src=x onerror=#quot;alert(1)#quot;#gt; #amp; co"]' in src
    assert '|"#lt;b#gt;bold#lt;/b#gt;"|' in src


def test_hash_is_escaped_first_so_entity_codes_in_names_stay_literal():
    # Mermaid decodes `#name;` and `#<number>;` in labels: `#` itself becomes `#35;` before anything else (#102)
    d = Diagram(id="x", title="x")
    ops.add_node(d, "#lt;b#gt; C#", kind="service", id="a")
    ops.add_node(d, "db", kind="database", id="db")
    ops.add_edge(d, "a", "db", label="#35; & #quot;")
    src = mermaid.render(d)
    assert 'n_a["«service»<br/>#35;lt;b#35;gt; C#35;"]' in src
    assert '|"#35;35; #amp; #35;quot;"|' in src
    assert mermaid.escape_text("#") == "#35;" and mermaid.shape_label("#\\") == "#35;#92;"
    assert mermaid.escape_text("Lifestyle:#1") == "Lifestyle#58;#35;1"  # defeats Mermaid's `style…:` pre-pass


def test_distinct_node_ids_never_share_a_mermaid_id():
    d = Diagram(id="x", title="x")
    ops.add_node(d, "foo bar", kind="service", id="foo-bar")
    ops.add_node(d, "foo bar 2", kind="service", id="foo_bar")
    ops.add_node(d, "foo bar 3", kind="service", id="foo.bar")
    ids = mermaid.id_map(d)
    assert len(set(ids.values())) == 3
    assert ids["foo-bar"] == "n_foo_bar"
    src = mermaid.render(d, highlight=["foo_bar"])
    for mid in ids.values():
        assert src.count(f"{mid}[") == 1
    assert f"class {ids['foo_bar']} changed" in src


def test_empty_diagram_renders():
    assert mermaid.render(Diagram(id="x", title="x")) == "flowchart TB\n"


def test_focus_renders_subtree_with_stubs_for_crossing_edges():
    d = demo_diagram()
    ops.add_node(d, "prod-vnet", kind="vnet", id="prod-vnet", parent="prod-deployment")
    ops.move_node(d, "service-a", "prod-vnet")
    src = mermaid.render(d, focus="prod-vnet", highlight=["service-a", "idp"])
    lines = [line.strip() for line in src.splitlines()]
    # the focused container is the only subgraph, its child is inside
    assert lines.count("end") == 1
    assert 'subgraph n_prod_vnet["«vnet» prod-vnet"]' in lines
    assert 'n_service_a["«service»<br/>service-a"]' in lines
    assert not any("n_prod_deployment" in line for line in lines)
    # crossing edges are kept, their outside endpoints become stubs, inside-only edges elsewhere are dropped
    assert 'n_api_gateway -->|"https"| n_service_a' in lines
    assert 'n_service_a -->|"sql"| n_orders_db' in lines
    assert not any("n_events" in line for line in lines)
    assert "class n_api_gateway,n_orders_db stub" in lines
    assert 'n_orders_db[("«database»<br/>orders-db")]' in lines
    # stubs keep no kind class, highlights outside the subtree are ignored
    assert "class n_api_gateway gateway" not in lines
    assert "class n_service_a changed" in lines


def test_focus_on_whole_deployment_hides_external_but_keeps_edge_as_stub():
    d = demo_diagram()
    src = mermaid.render(d, focus="prod-deployment")
    assert 'n_idp(["«external»<br/>Identity Provider"])' in src
    assert 'n_api_gateway -.->|"oidc"| n_idp' in src
    assert "class n_idp stub" in src
    assert "class n_idp external" not in src


def test_focus_on_leaf_and_unknown_focus():
    d = demo_diagram()
    src = mermaid.render(d, focus="orders-db")
    assert "subgraph" not in src
    assert 'n_service_a -->|"sql"| n_orders_db' in src
    assert "class n_service_a stub" in src
    try:
        mermaid.render(d, focus="nope")
    except ValueError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("unknown focus must raise")


def test_breadcrumb():
    d = demo_diagram()
    ops.add_node(d, "prod-vnet", kind="vnet", id="prod-vnet", parent="prod-deployment")
    assert mermaid.breadcrumb(d, None) == []
    assert [b["id"] for b in mermaid.breadcrumb(d, "prod-vnet")] == ["prod-deployment", "prod-vnet"]
    assert mermaid.breadcrumb(d, "missing") == []


# ---- edge direction and properties ------------------------------------------


@pytest.mark.parametrize(
    ("kind", "direction", "arrow"),
    [
        ("connection", None, "-->"),
        ("dependency", None, "-.->"),
        ("deploys", None, "==>"),
        ("association", None, "---"),
        ("replication", None, "<-->"),
        ("connection", "both", "<-->"),
        ("connection", "none", "---"),
        ("dependency", "both", "<-.->"),
        ("dependency", "none", "-.-"),
        ("deploys", "both", "<==>"),
        ("deploys", "none", "==="),
        ("association", "forward", "-->"),
        ("replication", "forward", "-->"),
    ],
)
def test_edge_arrow_combines_kind_line_and_direction(kind, direction, arrow):
    d = Diagram(id="d", title="d")
    ops.add_node(d, "a", kind="service", id="a")
    ops.add_node(d, "b", kind="service", id="b")
    ops.add_edge(d, "a", "b", kind=kind, direction=direction)
    assert f"  n_a {arrow} n_b" in mermaid.render(d).splitlines()


@pytest.mark.parametrize(
    ("kind", "direction", "effective"),
    [
        ("connection", None, "forward"),
        ("association", None, "none"),
        ("replication", None, "both"),
        ("connection", "both", "both"),
        ("replication", "forward", "forward"),
        ("unknown-kind", None, "forward"),
    ],
)
def test_edge_direction_is_the_override_or_the_kind_default(kind, direction, effective):
    edge = Edge(id="e1", source="a", target="b", kind=kind, direction=direction)
    assert mermaid.edge_direction(edge) == effective


def test_edge_direction_of_a_configured_kind():
    use_kinds(KindCatalog(CONTAINER_KINDS, LEAF_KINDS, EDGE_KINDS | {"rpc"}, edge_lines={"rpc": ("dotted", "both")}))
    assert mermaid.edge_direction(Edge(id="e1", source="a", target="b", kind="rpc")) == "both"


@pytest.mark.parametrize(
    ("label", "props", "shown"),
    [
        ("orders", {"protocol": "https", "port": "443"}, "orders (https#58;443)"),  # `:` escaped (#102 review)
        (None, {"protocol": "amqp"}, "amqp"),
        (None, {"port": "5432"}, "#58;5432"),
        ("sql", {"engine": "postgres"}, "sql"),
        (None, {"protocol": "<b>x</b>"}, "#lt;b#gt;x#lt;/b#gt;"),
    ],
)
def test_edge_label_shows_protocol_and_port(label, props, shown):
    d = Diagram(id="d", title="d")
    ops.add_node(d, "a", kind="service", id="a")
    ops.add_node(d, "b", kind="service", id="b")
    ops.add_edge(d, "a", "b", label=label, properties=props)
    assert f'  n_a -->|"{shown}"| n_b' in mermaid.render(d).splitlines()
