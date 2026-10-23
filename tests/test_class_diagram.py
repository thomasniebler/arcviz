"""Class diagrams (spec: docs/architecture/specs/class-diagrams.md, ADR-0179)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from arcviz import agent, diff, exchange, mermaid, prompts, render
from arcviz.graph_types import profile_of
from arcviz.model import SHARED_KINDS, Diagram, kinds_for
from arcviz.operations import OperationError, apply
from arcviz.project_model import adoptable, linkable_kinds, linkable_table
from arcviz.server import create_app
from arcviz.store import FileStore
from arcviz.templates import TEMPLATE_TYPES, create_from_template, templates_of


def node(d: Diagram, name: str, kind: str = "class", **args) -> str:
    return apply(d, "add_node", {"name": name, "kind": kind, "id": name.lower(), **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, kind: str = "association", **args) -> str:
    apply(d, "add_edge", {"source": source, "target": target, "kind": kind, **args})
    return d.edges[-1].id


def model() -> Diagram:
    d = Diagram(id="c", title="C", type="class")
    node(d, "Kunde", fields={"attributes": ["- name: String"]})
    node(d, "Bestellung", fields={"attributes": ["- id: UUID", "- summe: Money"], "operations": ["+ aufgeben()"]})
    return d


# ---- catalogue, defaults, profile ---------------------------------------------------------------------------


def test_catalogue_is_the_uml_subset():
    kinds = kinds_for("class")
    assert kinds.all_kinds == {"class", "interface", "enumeration", "data_type", "package"}
    assert kinds.container_kinds == {"package"}
    assert kinds.edge_kinds == {
        "association",
        "aggregation",
        "composition",
        "generalization",
        "realization",
        "dependency",
    }


def test_association_and_dependency_are_shared_with_deployment():
    assert {"deployment", "class"} <= SHARED_KINDS["association"]
    assert {"deployment", "class"} <= SHARED_KINDS["dependency"]
    assert mermaid.edge_arrow(_edge("association")) == "---" and mermaid.edge_arrow(_edge("dependency")) == "-.->"


def _edge(kind: str, **args):
    from arcviz.model import Edge

    return Edge(id="e", source="a", target="b", kind=kind, **args)


def test_defaults():
    profile = profile_of("class")
    assert profile is not None and not profile.guard and profile.directed_noun is None
    d = Diagram(id="c", title="C", type="class")
    a = apply(d, "add_node", {"name": "A"})["touched"][0]
    assert d.nodes[a].kind == "class"
    b = apply(d, "add_node", {"name": "B"})["touched"][0]
    apply(d, "add_edge", {"source": a, "target": b})
    assert d.edges[0].kind == "association"


# ---- typed fields --------------------------------------------------------------------------------------------


def test_member_fields_are_lists_of_lines():
    d = model()
    assert d.nodes["bestellung"].properties == {
        "attributes": "- id: UUID\n- summe: Money",
        "operations": "+ aufgeben()",
    }
    apply(d, "update_node", {"id": "bestellung", "fields": {"attributes": ["- id: UUID", "", "  + x: int  "]}})
    assert d.nodes["bestellung"].properties["attributes"] == "- id: UUID\n+ x: int"  # replaced, blanks dropped
    apply(d, "update_node", {"id": "bestellung", "fields": {"operations": []}})
    assert "operations" not in d.nodes["bestellung"].properties


def test_each_kind_has_its_own_fields():
    d = Diagram(id="c", title="C", type="class")
    node(d, "I", "interface", fields={"operations": ["+ run()"]})
    node(d, "E", "enumeration", fields={"literals": ["OFFEN", "BEZAHLT"]})
    node(d, "T", "data_type", fields={"attributes": ["x: int"], "operations": ["f()"]})
    with pytest.raises(OperationError, match="have no field 'attributes'"):
        apply(d, "update_node", {"id": "i", "fields": {"attributes": ["x"]}})
    with pytest.raises(OperationError, match="have no field 'literals'"):
        apply(d, "update_node", {"id": "t", "fields": {"literals": ["x"]}})
    with pytest.raises(OperationError, match="have no field 'operations'"):
        apply(d, "update_node", {"id": "e", "fields": {"operations": ["x"]}})
    with pytest.raises(OperationError, match="have no field 'abstract'"):
        apply(d, "update_node", {"id": "i", "fields": {"abstract": True}})


def test_abstract_is_a_flag():
    d = model()
    apply(d, "update_node", {"id": "kunde", "fields": {"abstract": True}})
    assert d.nodes["kunde"].properties["abstract"] == "true"
    apply(d, "update_node", {"id": "kunde", "fields": {"abstract": False}})
    assert "abstract" not in d.nodes["kunde"].properties
    with pytest.raises(OperationError, match="'abstract' must be true or false"):
        apply(d, "update_node", {"id": "kunde", "fields": {"abstract": "yes"}})


def test_member_bounds():
    d = model()
    with pytest.raises(OperationError, match="at most 30 lines"):
        apply(d, "update_node", {"id": "kunde", "fields": {"attributes": [f"a{i}" for i in range(31)]}})
    with pytest.raises(OperationError, match="at most 120 characters"):
        apply(d, "update_node", {"id": "kunde", "fields": {"operations": ["x" * 121]}})
    e = node(d, "E", "enumeration")
    with pytest.raises(OperationError, match="at most 60 characters"):
        apply(d, "update_node", {"id": e, "fields": {"literals": ["x" * 61]}})


@pytest.mark.parametrize("value", ["1", "*", "0..1", "1..*", "2..5", "0..*", "3..3"])
def test_multiplicities_that_are_uml(value):
    d = model()
    edge(d, "kunde", "bestellung", fields={"source_multiplicity": value, "target_multiplicity": value})
    assert d.edges[-1].properties["source_multiplicity"] == value


@pytest.mark.parametrize("value", ["a", "5..2", "*..1", "1..", "..1", "1.2", "-1", "1..2..3", "0..x"])
def test_multiplicities_that_are_not(value):
    d = model()
    with pytest.raises(OperationError, match="multiplicity"):
        edge(d, "kunde", "bestellung", fields={"target_multiplicity": value})


def test_roles_multiplicities_and_removal():
    d = model()
    eid = edge(
        d,
        "kunde",
        "bestellung",
        label="bestellt",
        fields={
            "source_multiplicity": "1",
            "target_multiplicity": "0..*",
            "source_role": "kunde",
            "target_role": "bestellungen",
        },
    )
    assert d.edges[0].properties == {
        "source_multiplicity": "1",
        "target_multiplicity": "0..*",
        "source_role": "kunde",
        "target_role": "bestellungen",
    }
    apply(d, "update_edge", {"id": eid, "fields": {"target_multiplicity": "", "source_role": ""}})
    assert set(d.edges[0].properties) == {"source_multiplicity", "target_role"}
    with pytest.raises(OperationError, match="at most 40 characters"):
        apply(d, "update_edge", {"id": eid, "fields": {"target_role": "x" * 41}})
    with pytest.raises(OperationError, match="have no field"):
        edge(d, "kunde", "bestellung", "dependency", fields={"target_multiplicity": "1"})


# ---- hard rules ----------------------------------------------------------------------------------------------


def test_packages_only_take_part_in_dependencies():
    d = model()
    node(d, "P", "package")
    for kind in ("association", "generalization", "composition"):
        with pytest.raises(OperationError, match="takes part only in dependency"):
            edge(d, "p", "kunde", kind)
    edge(d, "p", "kunde", "dependency")
    edge(d, "kunde", "p", "dependency")


def test_generalization_rules():
    d = Diagram(id="c", title="C", type="class")
    a, b, c = node(d, "A"), node(d, "B"), node(d, "C")
    i, j = node(d, "I", "interface"), node(d, "J", "interface")
    e = node(d, "E", "enumeration")
    edge(d, b, a, "generalization")
    edge(d, c, b, "generalization")
    edge(d, j, i, "generalization")
    with pytest.raises(OperationError, match="cannot specialise itself"):
        edge(d, a, a, "generalization")
    with pytest.raises(OperationError, match="would form a cycle"):
        edge(d, a, c, "generalization")
    with pytest.raises(OperationError, match="would form a cycle"):
        edge(d, a, b, "generalization")
    with pytest.raises(OperationError, match="two classes, two interfaces or two data types"):
        edge(d, a, i, "generalization")
    with pytest.raises(OperationError, match="two classes, two interfaces or two data types"):
        edge(d, e, a, "generalization")
    with pytest.raises(OperationError, match="fixed notation"):
        edge(d, c, a, "generalization", direction="forward")


def test_realization_goes_from_a_class_to_an_interface():
    d = Diagram(id="c", title="C", type="class")
    a, i, j = node(d, "A"), node(d, "I", "interface"), node(d, "J", "interface")
    edge(d, a, i, "realization")
    for source, target in ((i, a), (a, a), (i, j)):
        with pytest.raises(OperationError, match="goes from a class to an interface"):
            edge(d, source, target, "realization")


def test_association_ends_and_direction():
    d = Diagram(id="c", title="C", type="class")
    a, i = node(d, "A"), node(d, "I", "interface")
    e, t = node(d, "E", "enumeration"), node(d, "T", "data_type")
    edge(d, a, i)  # class to interface
    edge(d, a, a, label="ist Vorgesetzter von")  # recursive
    for bad in (e, t):
        with pytest.raises(OperationError, match="joins two classes or interfaces"):
            edge(d, a, bad)
    edge(d, a, i, label="navigable", direction="forward")
    edge(d, a, i, label="plain", direction="none")
    with pytest.raises(OperationError, match="only be directed forward"):
        edge(d, a, i, label="both", direction="both")


@pytest.mark.parametrize("kind", ["aggregation", "composition"])
def test_whole_part_relationships_join_classes_and_have_a_fixed_notation(kind):
    d = Diagram(id="c", title="C", type="class")
    a, b, i = node(d, "A"), node(d, "B"), node(d, "I", "interface")
    edge(d, b, a, kind)
    with pytest.raises(OperationError, match=f"an {kind} joins two classes"):
        edge(d, i, a, kind)
    with pytest.raises(OperationError, match="fixed notation"):
        edge(d, a, b, kind, direction="forward")


def test_a_kind_change_may_not_break_relationships():
    d = Diagram(id="c", title="C", type="class")
    a, b = node(d, "A"), node(d, "B")
    edge(d, b, a, "generalization")
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": b, "kind": "interface"})
    c = node(d, "C")
    edge(d, c, a, "dependency")
    apply(d, "update_node", {"id": c, "kind": "package"})  # a package may be the client of a dependency
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": a, "kind": "package"})  # a generalization points at it


def test_only_packages_contain_nodes():
    d = Diagram(id="c", title="C", type="class")
    a = node(d, "A")
    p = node(d, "P", "package")
    q = node(d, "Q", "package", parent=p)
    node(d, "B", parent=q)
    with pytest.raises(OperationError, match="cannot contain"):
        node(d, "C", parent=a)


def test_a_kind_change_drops_the_fields_the_new_kind_lacks():
    d = model()
    apply(d, "update_node", {"id": "bestellung", "kind": "interface"})
    assert d.nodes["bestellung"].properties == {"operations": "+ aufgeben()"}
    apply(d, "update_node", {"id": "bestellung", "kind": "enumeration"})
    assert d.nodes["bestellung"].properties == {}


def test_import_applies_the_same_rules():
    d = model()
    node(d, "I", "interface")
    d2 = model()
    document = exchange.export_document(d)
    assert document["schema_version"] == 2 and exchange.parse_document(document).nodes.keys() == d.nodes.keys()
    for patch, message in (
        ({"id": "e1", "source": "kunde", "target": "i", "kind": "generalization"}, "two classes, two interfaces"),
        ({"id": "e1", "source": "i", "target": "kunde", "kind": "realization"}, "goes from a class to an interface"),
        ({"id": "e1", "source": "kunde", "target": "kunde", "kind": "generalization"}, "cannot specialise itself"),
        ({"id": "e1", "source": "kunde", "target": "i", "kind": "aggregation"}, "joins two classes"),
        (
            {"id": "e1", "source": "kunde", "target": "i", "kind": "composition", "direction": "forward"},
            "joins two classes",
        ),
    ):
        bad = exchange.export_document(d)
        bad["diagram"]["edges"].append(patch)
        with pytest.raises(exchange.ExchangeError, match=message):
            exchange.parse_document(bad)
    cycle = exchange.export_document(d2)
    cycle["diagram"]["edges"] += [
        {"id": "e1", "source": "kunde", "target": "bestellung", "kind": "generalization"},
        {"id": "e2", "source": "bestellung", "target": "kunde", "kind": "generalization"},
    ]
    with pytest.raises(exchange.ExchangeError, match="cycle"):
        exchange.parse_document(cycle)
    for field, value, message in (
        ("attributes", "\n".join(f"a{i}" for i in range(31)), "at most 30 lines"),
        ("operations", "x" * 121, "longer than 120"),
    ):
        bad = exchange.export_document(d)
        bad["diagram"]["nodes"]["kunde"]["properties"][field] = value
        with pytest.raises(exchange.ExchangeError, match=message):
            exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["kunde"]["properties"]["abstract"] = "maybe"
    with pytest.raises(exchange.ExchangeError, match="abstract"):
        exchange.parse_document(bad)


# ---- rendering -----------------------------------------------------------------------------------------------


def test_classifier_labels_have_compartments():
    d = model()
    node(d, "Basis", fields={"abstract": True})
    node(d, "Zahlungsverfahren", "interface", fields={"operations": ["+ zahlen(summe: Money)"]})
    node(d, "Status", "enumeration", fields={"literals": ["OFFEN", "BEZAHLT"]})
    node(d, "Geld", "data_type", fields={"attributes": ["betrag: Decimal"]})
    src = mermaid.render(d)
    rule = "─" * 10
    assert (
        f'n_bestellung["Bestellung<br/>{rule}<br/>- id#58; UUID<br/>- summe#58; Money<br/>{rule}<br/>+ aufgeben()"]'
        in src
    )
    assert f'n_kunde["Kunde<br/>{rule}<br/>- name#58; String"]' in src
    assert 'n_basis["«abstract»<br/>Basis"]' in src
    assert f'n_zahlungsverfahren["«interface»<br/>Zahlungsverfahren<br/>{rule}<br/>+ zahlen(summe#58; Money)"]' in src
    assert f'n_status["«enumeration»<br/>Status<br/>{rule}<br/>OFFEN<br/>BEZAHLT"]' in src
    assert f'n_geld["«dataType»<br/>Geld<br/>{rule}<br/>betrag#58; Decimal"]' in src


def test_long_compartments_are_cut_after_eight_lines():
    d = Diagram(id="c", title="C", type="class")
    node(d, "Big", fields={"attributes": [f"- a{i}: int" for i in range(12)]})
    src = mermaid.render(d)
    assert "- a7#58; int<br/>… (+4)" in src and "a8" not in src
    assert len(d.nodes["big"].properties["attributes"].split("\n")) == 12  # the model keeps all of them


def test_packages_are_subgraphs_labelled_with_the_name():
    d = Diagram(id="c", title="C", type="class")
    p = node(d, "Bestellwesen", "package")
    node(d, "A", parent=p)
    src = mermaid.render(d)
    assert 'subgraph n_bestellwesen["Bestellwesen"]' in src and "«package»" not in src


def test_relationship_notation():
    d = Diagram(id="c", title="C", type="class")
    names = {n: node(d, n.upper(), "interface" if n == "i" else "class") for n in "abcdi"}
    edge(d, names["a"], names["b"])  # association
    edge(d, names["a"], names["c"], direction="forward")
    edge(d, names["a"], names["d"], "aggregation")
    edge(d, names["b"], names["d"], "composition")
    edge(d, names["c"], names["d"], "generalization")
    edge(d, names["a"], names["i"], "realization")
    edge(d, names["b"], names["c"], "dependency")
    lines = [line.strip() for line in mermaid.render(d).splitlines() if "n_" in line and ("-" in line or "=" in line)]
    text = "\n".join(lines)
    assert "n_a --- n_b" in text and "n_a --> n_c" in text and "n_a --o n_d" in text
    assert "n_b ==o n_d" in text and "n_c --> n_d" in text and "n_b -.-> n_c" in text
    assert 'n_a -.->|"«realizes»"| n_i' in text


def test_association_text():
    d = model()
    edge(
        d,
        "kunde",
        "bestellung",
        label="bestellt",
        fields={
            "source_multiplicity": "1",
            "target_multiplicity": "0..*",
            "source_role": "kunde",
            "target_role": "bestellungen",
        },
    )
    edge(d, "bestellung", "kunde", fields={"target_multiplicity": "1"})
    edge(d, "kunde", "kunde", label="empfiehlt")
    src = mermaid.render(d)
    assert 'n_kunde ---|"bestellt<br/>kunde 1 – bestellungen 0..*"| n_bestellung' in src
    assert 'n_bestellung ---|"1"| n_kunde' in src
    assert 'n_kunde ---|"empfiehlt"| n_kunde' in src


def test_hostile_members_names_and_roles_are_text():
    d = Diagram(id="c", title="C", type="class")
    a = node(d, '<img src=x onerror=alert(1)> "q"', fields={"attributes": ["- <b>x</b>: #lt;", '+ f("a|b")']})
    b = node(d, "B")
    edge(d, a, b, label="<i>n</i>", fields={"source_role": "<u>r</u>"})
    src = mermaid.render(d)
    assert "<img" not in src and "<b>" not in src and "<i>" not in src and "<u>" not in src
    assert "- #lt;b#gt;x#lt;/b#gt;#58; #35;lt;" in src


def test_the_style_class_of_a_class_is_not_the_mermaid_keyword():
    src = mermaid.render(model())
    assert "classDef class_kind " in src and "classDef class " not in src
    assert re.search(r"^  class n_\w+,n_\w+ class_kind$", src, re.M)


def test_the_renderer_is_mermaid():
    assert render.RENDERERS["class"].name == "mermaid"
    payload = render.payload(model())
    assert payload["renderer"] == "mermaid" and payload["mermaid"] and payload["svg"] is None


# ---- agent ---------------------------------------------------------------------------------------------------


def test_tools_and_prompt():
    tools = {t.name: t for t in agent.build_tools(kinds_for("class"), "class")}
    assert set(tools["add_node"].parameters["properties"]["kind"]["enum"]) == kinds_for("class").all_kinds
    assert set(tools["add_edge"].parameters["properties"]["kind"]["enum"]) == kinds_for("class").edge_kinds
    assert "guard" not in tools["add_edge"].parameters["properties"]
    assert "direction" in tools["add_edge"].parameters["properties"]
    node_fields = tools["add_node"].parameters["properties"]["fields"]["properties"]
    assert set(node_fields) == {"attributes", "operations", "literals", "abstract"}
    assert node_fields["attributes"]["type"] == "array" and node_fields["attributes"]["maxItems"] == 30
    assert node_fields["abstract"]["type"] == "boolean"
    edge_fields = tools["add_edge"].parameters["properties"]["fields"]["properties"]
    assert set(edge_fields) == {"source_multiplicity", "target_multiplicity", "source_role", "target_role"}
    description = tools["add_edge"].description
    assert "PART" in description and "WHOLE" in description  # the orientation of aggregation and composition
    assert prompts.EXPERT_PARTS["class"].strip()
    prompt = agent.build_system_prompt(model())
    assert "chapter 11" in prompt and "Guided start" in prompt and "composition" in prompt


# ---- template, exchange, comparison, project model ---------------------------------------------------------------


def test_template():
    assert TEMPLATE_TYPES["webshop-domain"] == "class"
    assert templates_of("class") == ["webshop-domain", "empty"]
    d = create_from_template("webshop-domain", "x", "X")
    assert d.type == "class" and d.integrity_errors() == []
    assert exchange.parse_document(exchange.export_document(d)).model_dump() == d.model_dump()
    kinds = {n.kind for n in d.nodes.values()}
    assert kinds == {"class", "interface", "enumeration", "package"}
    assert {e.kind for e in d.edges} >= {"association", "composition", "generalization", "realization", "dependency"}
    assert any(n.properties.get("abstract") == "true" for n in d.nodes.values())


def test_plantuml_export():
    d = model()
    apply(d, "update_node", {"id": "kunde", "fields": {"abstract": True}})
    node(d, "Position")
    edge(
        d,
        "position",
        "bestellung",
        "composition",
        label="enthält",
        fields={"source_multiplicity": "1..*", "target_multiplicity": "1", "source_role": "positionen"},
    )
    text = exchange.export_plantuml(d)
    assert text.startswith("@startuml") and text.rstrip().endswith("@enduml")
    assert 'abstract class "Kunde" as n_kunde {\n  - name: String\n}' in text
    assert 'class "Bestellung" as n_bestellung {\n  - id: UUID\n  - summe: Money\n  + aufgeben()\n}' in text
    assert (
        'n_bestellung "1" *-- "1..*" n_position : enthält (positionen)' in text
    )  # whole first, orientation swapped back


def test_comparison_marks_a_changed_member():
    old, new = model(), model()
    apply(new, "update_node", {"id": "kunde", "fields": {"attributes": ["- name: String", "- email: String"]}})
    assert "class" in diff.SUPPORTED_TYPES
    result = diff.compare(old, new)
    assert result.changed_nodes == ["kunde"] and not result.added_nodes


def test_project_model_links():
    assert linkable_kinds("class", "class") == ("data",)
    assert linkable_kinds("class", "enumeration") == ("data",) and linkable_kinds("class", "data_type") == ("data",)
    assert linkable_kinds("class", "interface") == ("component",)
    assert linkable_kinds("class", "package") == ()
    assert linkable_table("class") == {
        "class": ["data"],
        "enumeration": ["data"],
        "data_type": ["data"],
        "interface": ["component"],
    }
    assert not any(adoptable("class", kind) for kind in kinds_for("class").all_kinds)  # on request only (ADR-0182)


# ---- API -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(store=FileStore(tmp_path)))


def test_config_describes_the_type(client):
    t = client.get("/api/config").json()["diagram_types"]["class"]
    assert t["renderer"] == "mermaid" and "11" in t["uml"]
    assert t["features"] == {
        "focus": True,
        "direction": True,
        "compare": True,
        "plantuml": True,
        "edge_direction": True,
    }
    assert {f["name"]: f["list"] for f in t["node_fields"]["class"]} == {
        "attributes": True,
        "operations": True,
        "abstract": False,
    }
    assert [f["name"] for f in t["edge_fields"]["aggregation"]] == [
        "source_multiplicity",
        "target_multiplicity",
        "source_role",
        "target_role",
    ]
    assert t["templates"] == ["webshop-domain", "empty"]


def test_create_edit_check_export_over_http(client):
    created = client.post("/api/diagrams", json={"title": "Domäne", "template": "webshop-domain"}).json()
    did = created["diagram"]["id"]
    assert created["diagram"]["type"] == "class" and created["renderer"] == "mermaid"
    ok = client.post(
        f"/api/diagrams/{did}/ops",
        json={
            "op": "add_node",
            "args": {"name": "Rechnung", "fields": {"attributes": ["- nr: int"], "abstract": True}},
        },
    )
    assert ok.status_code == 200, ok.text
    bad = client.post(
        f"/api/diagrams/{did}/ops",
        json={"op": "add_edge", "args": {"source": "rechnung", "target": "rechnung", "kind": "generalization"}},
    )
    assert bad.status_code == 400 and "cannot specialise itself" in bad.text
    assert client.get(f"/api/diagrams/{did}/checks").status_code == 200
    assert client.get(f"/api/diagrams/{did}/export?format=plantuml").status_code == 200
    exported = client.get(f"/api/diagrams/{did}/export").json()
    imported = client.post("/api/diagrams/import", json={"document": exported})
    assert imported.status_code in (200, 201), imported.text
    assert imported.json()["diagram"]["type"] == "class"


def test_a_dependency_has_a_fixed_notation():
    # Mermaid honours a direction override, PlantUML always writes `..>`: so the model refuses one (review of #248)
    d = Diagram(id="c", title="C", type="class")
    a, b = node(d, "A"), node(d, "B")
    edge(d, a, b, "dependency")
    for direction in ("none", "both", "forward"):
        with pytest.raises(OperationError, match="fixed notation"):
            edge(d, a, b, "dependency", direction=direction)
