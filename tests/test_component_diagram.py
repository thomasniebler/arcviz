"""Component diagrams (spec: docs/architecture/specs/component-diagrams.md, ADR-0181)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import agent, diff, exchange, mermaid, prompts, render
from arcviz.graph_types import profile_of
from arcviz.model import SHARED_KINDS, Diagram, Edge, kinds_for
from arcviz.operations import OperationError, apply
from arcviz.project_model import adoptable, linkable_kinds, linkable_table
from arcviz.server import create_app
from arcviz.store import FileStore
from arcviz.templates import TEMPLATE_TYPES, create_from_template, templates_of


def node(d: Diagram, name: str, kind: str = "component", **args) -> str:
    return apply(d, "add_node", {"name": name, "kind": kind, "id": name.lower(), **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, kind: str = "connection", **args) -> str:
    apply(d, "add_edge", {"source": source, "target": target, "kind": kind, **args})
    return d.edges[-1].id


def diagram() -> Diagram:
    d = Diagram(id="c", title="C", type="component")
    node(d, "Shop", "subsystem")
    node(d, "Web", parent="shop")
    node(d, "Orders", parent="shop", fields={"ports": ["orders-in: OrderApi", "payments-out"]})
    node(d, "OrderApi", "interface", parent="shop")
    node(d, "Payment")
    return d


# ---- catalogue, defaults, profile ---------------------------------------------------------------------------


def test_catalogue_is_the_uml_subset():
    kinds = kinds_for("component")
    assert kinds.all_kinds == {"component", "subsystem", "interface", "package"}
    assert kinds.container_kinds == {"subsystem", "package"}
    assert kinds.edge_kinds == {"connection", "realization", "usage", "dependency"}


def test_shared_kinds_are_listed():
    assert SHARED_KINDS["component"] == {"deployment", "component"}
    assert SHARED_KINDS["interface"] == {"class", "component"} and SHARED_KINDS["package"] == {"class", "component"}
    assert SHARED_KINDS["connection"] == {"deployment", "component"}
    assert SHARED_KINDS["realization"] == {"class", "component"}
    assert SHARED_KINDS["dependency"] == {"deployment", "class", "component"}


def test_defaults():
    profile = profile_of("component")
    assert profile is not None and not profile.guard and profile.directed_noun is None
    d = Diagram(id="c", title="C", type="component")
    assert d.direction == "LR"
    a = apply(d, "add_node", {"name": "A"})["touched"][0]
    b = apply(d, "add_node", {"name": "B"})["touched"][0]
    assert d.nodes[a].kind == "component"
    apply(d, "add_edge", {"source": a, "target": b})
    assert d.edges[0].kind == "connection"


# ---- ports ---------------------------------------------------------------------------------------------------


def test_ports_are_a_list_field_of_components():
    d = diagram()
    assert d.nodes["orders"].properties["ports"] == "orders-in: OrderApi\npayments-out"
    apply(d, "update_node", {"id": "orders", "fields": {"ports": ["a", "b", "c"]}})
    assert d.nodes["orders"].properties["ports"] == "a\nb\nc"
    with pytest.raises(OperationError, match="at most 10 lines"):
        apply(d, "update_node", {"id": "orders", "fields": {"ports": [f"p{i}" for i in range(11)]}})
    with pytest.raises(OperationError, match="at most 40 characters"):
        apply(d, "update_node", {"id": "orders", "fields": {"ports": ["x" * 41]}})
    with pytest.raises(OperationError, match="have no field 'ports'"):
        apply(d, "update_node", {"id": "shop", "fields": {"ports": ["a"]}})
    with pytest.raises(OperationError, match="have no field 'ports'"):
        apply(d, "update_node", {"id": "orderapi", "fields": {"ports": ["a"]}})


def test_port_names_are_unique_per_component():
    d = diagram()
    with pytest.raises(OperationError, match="port 'a' twice"):
        apply(d, "update_node", {"id": "orders", "fields": {"ports": ["a: X", "a: Y"]}})


def test_a_connector_names_ports_of_its_ends():
    d = diagram()
    edge(d, "orders", "payment", fields={"source_port": "payments-out"})
    assert d.edges[-1].properties == {"source_port": "payments-out"}
    edge(d, "web", "orders", fields={"target_port": "orders-in"})
    with pytest.raises(OperationError, match="'web' has no port 'x'"):
        edge(d, "web", "orders", label="y", fields={"source_port": "x"})
    with pytest.raises(OperationError, match="'payment' has no port 'orders-in'"):
        edge(d, "orders", "payment", label="z", fields={"target_port": "orders-in"})
    edge(d, "orders", "orderapi", "realization", fields={"source_port": "orders-in"})
    with pytest.raises(OperationError, match="have no field 'target_port'"):
        edge(d, "orders", "orderapi", "realization", label="q", fields={"target_port": "orders-in"})


def test_a_port_in_use_cannot_be_renamed_or_removed():
    d = diagram()
    edge(d, "orders", "payment", fields={"source_port": "payments-out"})
    with pytest.raises(OperationError, match="port 'payments-out' is used by edge"):
        apply(d, "update_node", {"id": "orders", "fields": {"ports": ["orders-in"]}})
    with pytest.raises(OperationError, match="port 'payments-out' is used by edge"):
        apply(d, "update_node", {"id": "orders", "fields": {"ports": []}})
    apply(d, "update_node", {"id": "orders", "fields": {"ports": ["orders-in", "payments-out: Pay"]}})  # the name stays
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": "orders", "kind": "interface"})  # an interface has no connections


def test_ports_survive_a_kind_change_only_where_the_kind_has_them():
    d = diagram()
    apply(d, "update_node", {"id": "orders", "kind": "subsystem"})
    assert "ports" not in d.nodes["orders"].properties


# ---- hard rules ----------------------------------------------------------------------------------------------


def test_packages_only_take_part_in_dependencies():
    d = diagram()
    node(d, "P", "package")
    for kind in ("connection", "realization", "usage"):
        with pytest.raises(OperationError, match="takes part only in dependency"):
            edge(d, "p", "web", kind)
    edge(d, "p", "web", "dependency")


def test_connections_join_components_and_subsystems():
    d = diagram()
    node(d, "Other", "subsystem")
    edge(d, "web", "orders")
    edge(d, "shop", "other")
    edge(d, "payment", "other")
    for source, target in (("web", "web"), ("web", "orderapi"), ("orderapi", "web")):
        with pytest.raises(OperationError, match="joins two different components or subsystems"):
            edge(d, source, target)


@pytest.mark.parametrize("kind", ["realization", "usage"])
def test_realization_and_usage_go_from_a_component_to_an_interface(kind):
    d = diagram()
    node(d, "Other", "subsystem")
    edge(d, "web", "orderapi", kind)
    edge(d, "other", "orderapi", kind, label="all")
    for source, target in (("orderapi", "web"), ("web", "orders"), ("orderapi", "orderapi")):
        with pytest.raises(OperationError, match=f"a {kind} goes from a component or subsystem to an interface"):
            edge(d, source, target, kind)


def test_dependencies_join_anything_and_direction_is_free():
    d = diagram()
    edge(d, "web", "orderapi", "dependency")
    edge(d, "orderapi", "payment", "dependency", direction="both")
    edge(d, "web", "orders", direction="both")


def test_no_edge_between_a_node_and_its_own_container():
    d = diagram()
    for source, target in (("shop", "web"), ("web", "shop")):
        with pytest.raises(OperationError, match="its own container"):
            edge(d, source, target)
    edge(d, "shop", "payment")


def test_only_subsystems_and_packages_contain_nodes():
    d = diagram()
    with pytest.raises(OperationError, match="cannot contain"):
        node(d, "x", parent="web")
    p = node(d, "P", "package")
    q = node(d, "Q", "package", parent=p)
    node(d, "R", "subsystem", parent=q)
    node(d, "S", "component", parent=p)
    with pytest.raises(OperationError, match="a package stands in a package or at the top level"):
        node(d, "T", "package", parent="shop")


def test_a_kind_change_may_not_break_the_rules():
    d = diagram()
    edge(d, "web", "orderapi", "usage")
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": "web", "kind": "interface"})
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": "web", "kind": "package"})


def test_import_applies_the_same_rules():
    d = diagram()
    edge(d, "orders", "payment", fields={"source_port": "payments-out"})
    document = exchange.export_document(d)
    assert document["schema_version"] == 2 and exchange.parse_document(document).nodes.keys() == d.nodes.keys()
    for patch, message in (
        ({"id": "e9", "source": "web", "target": "orderapi", "kind": "connection"}, "joins two different components"),
        ({"id": "e9", "source": "orderapi", "target": "web", "kind": "realization"}, "goes from a component"),
        ({"id": "e9", "source": "shop", "target": "web", "kind": "connection"}, "its own container"),
        (
            {
                "id": "e9",
                "source": "web",
                "target": "orders",
                "kind": "connection",
                "properties": {"source_port": "nope"},
            },
            "has no port 'nope'",
        ),
    ):
        bad = exchange.export_document(d)
        bad["diagram"]["edges"].append(patch)
        with pytest.raises(exchange.ExchangeError, match=message):
            exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["orders"]["properties"]["ports"] = "a\na"
    with pytest.raises(exchange.ExchangeError, match="port 'a' twice"):
        exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["orders"]["properties"]["ports"] = "orders-in"  # drops payments-out, which an edge names
    with pytest.raises(exchange.ExchangeError, match="port 'payments-out' is used by edge"):
        exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["orderapi"]["parent"] = "payment"
    with pytest.raises(exchange.ExchangeError, match="not a container"):
        exchange.parse_document(bad)


# ---- rendering -----------------------------------------------------------------------------------------------


def test_mermaid_notation():
    d = diagram()
    src = mermaid.render(d)
    assert src.startswith("flowchart LR")
    assert 'subgraph n_shop["«subsystem» Shop"]' in src
    assert 'n_web["«component»<br/>Web"]' in src
    rule = "─" * 10
    assert f'n_orders["«component»<br/>Orders<br/>{rule}<br/>port orders-in#58; OrderApi<br/>port payments-out"]' in src
    assert 'n_orderapi@{ shape: circle, label: "OrderApi" }' in src
    assert 'n_payment["«component»<br/>Payment"]' in src


def test_a_long_interface_name_is_a_stadium_with_its_keyword():
    d = Diagram(id="c", title="C", type="component")
    node(d, "VeryLongInterfaceNameForTheApi", "interface")
    node(d, "Short", "interface")
    src = mermaid.render(d)
    assert 'n_verylonginterfacenamefortheapi(["«interface»<br/>VeryLongInterfaceNameForTheApi"])' in src
    assert 'n_short@{ shape: circle, label: "Short" }' in src


def test_the_same_kinds_render_as_before_in_other_types():
    deployment = Diagram(id="d", title="D")
    apply(deployment, "add_node", {"name": "C", "kind": "component", "id": "c"})
    assert 'n_c["«component»<br/>C"]' in mermaid.render(deployment)
    classes = Diagram(id="k", title="K", type="class")
    apply(classes, "add_node", {"name": "I", "kind": "interface", "id": "i", "fields": {"operations": ["+ f()"]}})
    assert "«interface»<br/>I<br/>" in mermaid.render(classes) and "shape: circle" not in mermaid.render(classes)


def test_relationship_notation_and_ports():
    d = diagram()
    edge(d, "web", "orders", fields={"target_port": "orders-in"})
    edge(
        d, "orders", "payment", label="zahlt", fields={"source_port": "payments-out"}, properties={"protocol": "https"}
    )
    edge(d, "orders", "orderapi", "realization", fields={"source_port": "orders-in"})
    edge(d, "web", "orderapi", "usage")
    edge(d, "web", "payment", "dependency")
    src = mermaid.render(d)
    assert 'n_web -->|"(→ orders-in)"| n_orders' in src
    assert 'n_orders -->|"zahlt (https) (payments-out →)"| n_payment' in src
    assert 'n_orders -.->|"«realizes» (orders-in →)"| n_orderapi' in src
    assert 'n_web -.->|"«use»"| n_orderapi' in src
    assert "n_web -.-> n_payment" in src


def test_hostile_names_ports_and_labels_are_text():
    d = Diagram(id="c", title="C", type="component")
    a = node(d, '<img src=x onerror=alert(1)> "q"', fields={"ports": ["<b>p</b>: #lt;"]})
    b = node(d, "B")
    edge(d, a, b, label="<i>l</i>", fields={"source_port": "<b>p</b>"})
    src = mermaid.render(d)
    assert "<img" not in src and "<b>" not in src and "<i>" not in src


def test_the_renderer_is_mermaid():
    assert render.RENDERERS["component"].name == "mermaid"
    payload = render.payload(diagram())
    assert payload["renderer"] == "mermaid" and payload["mermaid"] and payload["svg"] is None


# ---- agent ---------------------------------------------------------------------------------------------------


def test_tools_and_prompt():
    tools = {t.name: t for t in agent.build_tools(kinds_for("component"), "component")}
    assert set(tools["add_node"].parameters["properties"]["kind"]["enum"]) == kinds_for("component").all_kinds
    assert set(tools["add_node"].parameters["properties"]["fields"]["properties"]) == {"ports"}
    assert set(tools["add_edge"].parameters["properties"]["fields"]["properties"]) == {"source_port", "target_port"}
    assert "guard" not in tools["add_edge"].parameters["properties"]
    assert prompts.EXPERT_PARTS["component"].strip()
    prompt = agent.build_system_prompt(diagram())
    assert "chapter 11" in prompt and "Guided start" in prompt and "interface" in prompt


# ---- template, exchange, comparison, project model ---------------------------------------------------------------


def test_template():
    assert TEMPLATE_TYPES["webshop-components"] == "component"
    assert templates_of("component") == ["webshop-components", "empty"]
    d = create_from_template("webshop-components", "x", "X")
    assert d.type == "component" and d.direction == "LR" and d.integrity_errors() == []
    assert exchange.parse_document(exchange.export_document(d)).model_dump() == d.model_dump()
    assert {n.kind for n in d.nodes.values()} == {"component", "subsystem", "interface"}
    assert {e.kind for e in d.edges} == {"connection", "realization", "usage"}
    assert any("ports" in n.properties for n in d.nodes.values())


def test_comparison_marks_a_changed_port():
    old, new = diagram(), diagram()
    apply(new, "update_node", {"id": "orders", "fields": {"ports": ["orders-in: OrderApi", "payments-out", "audit"]}})
    assert "component" in diff.SUPPORTED_TYPES
    assert diff.compare(old, new).changed_nodes == ["orders"]


def test_project_model_links_and_adoption():
    assert linkable_kinds("component", "component") == ("component", "system", "subsystem")
    assert linkable_kinds("component", "subsystem") == ("subsystem", "system")
    assert linkable_kinds("component", "interface") == () and linkable_kinds("component", "package") == ()
    assert linkable_table("component") == {
        "component": ["component", "system", "subsystem"],
        "subsystem": ["subsystem", "system"],
    }
    assert adoptable("component", "component") and adoptable("component", "subsystem")
    assert not adoptable("component", "interface") and not adoptable("component", "package")


def test_edge_stereotypes_and_port_text():
    assert mermaid.edge_lines(Edge(id="e", source="a", target="b", kind="usage")) == ["«use»"]
    edge_with_ports = Edge(
        id="e", source="a", target="b", kind="connection", properties={"source_port": "p", "target_port": "q"}
    )
    assert mermaid.edge_lines(edge_with_ports, "component") == ["(p → q)"]
    assert mermaid.edge_lines(edge_with_ports, "deployment") == []  # free properties of other types are not ports


# ---- API -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(store=FileStore(tmp_path)))


def test_config_describes_the_type(client):
    t = client.get("/api/config").json()["diagram_types"]["component"]
    assert t["renderer"] == "mermaid" and "11" in t["uml"]
    assert [f["name"] for f in t["node_fields"]["component"]] == ["ports"]
    assert [f["name"] for f in t["edge_fields"]["connection"]] == ["source_port", "target_port"]
    assert [f["name"] for f in t["edge_fields"]["usage"]] == ["source_port"]
    assert t["templates"] == ["webshop-components", "empty"]


def test_create_edit_check_export_over_http(client):
    created = client.post("/api/diagrams", json={"title": "Bausteine", "template": "webshop-components"}).json()
    did = created["diagram"]["id"]
    assert created["diagram"]["type"] == "component" and created["diagram"]["direction"] == "LR"
    ok = client.post(
        f"/api/diagrams/{did}/ops",
        json={"op": "add_node", "args": {"name": "Neu", "fields": {"ports": ["in"]}}},
    )
    assert ok.status_code == 200, ok.text
    bad = client.post(
        f"/api/diagrams/{did}/ops",
        json={"op": "add_edge", "args": {"source": "neu", "target": "neu"}},
    )
    assert bad.status_code == 400 and "two different components" in bad.text
    assert client.get(f"/api/diagrams/{did}/checks").status_code == 200
    assert client.get(f"/api/diagrams/{did}/export?format=plantuml").status_code == 200
    exported = client.get(f"/api/diagrams/{did}/export").json()
    imported = client.post("/api/diagrams/import", json={"document": exported})
    assert imported.status_code in (200, 201), imported.text
    assert imported.json()["diagram"]["type"] == "component"
