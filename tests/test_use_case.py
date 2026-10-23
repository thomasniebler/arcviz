"""Use case diagrams (spec: docs/architecture/specs/use-case-diagrams.md, ADR-0180)."""

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


def node(d: Diagram, name: str, kind: str = "use_case", **args) -> str:
    return apply(d, "add_node", {"name": name, "kind": kind, "id": name.lower(), **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, kind: str = "association", **args) -> str:
    apply(d, "add_edge", {"source": source, "target": target, "kind": kind, **args})
    return d.edges[-1].id


def diagram() -> Diagram:
    d = Diagram(id="u", title="U", type="use_case")
    node(d, "Webshop", "subject")
    node(d, "Kunde", "actor")
    node(d, "Bestellen", parent="webshop", description="Ziel: Ware kaufen.")
    node(d, "Zahlen", parent="webshop")
    edge(d, "kunde", "bestellen")
    return d


# ---- catalogue, defaults, profile ---------------------------------------------------------------------------


def test_catalogue_is_the_uml_subset():
    kinds = kinds_for("use_case")
    assert kinds.all_kinds == {"actor", "use_case", "subject"}
    assert kinds.container_kinds == {"subject"}
    assert kinds.edge_kinds == {"association", "include", "extend", "generalization"}


def test_shared_kinds_are_listed():
    assert SHARED_KINDS["association"] == {"deployment", "class", "use_case"}
    assert SHARED_KINDS["generalization"] == {"class", "use_case"}


def test_defaults_and_direction():
    profile = profile_of("use_case")
    assert profile is not None and not profile.guard and profile.directed_noun is None
    d = Diagram(id="u", title="U", type="use_case")
    assert d.direction == "LR"  # a use case diagram reads left to right
    assert Diagram(id="s", title="S", type="state_machine").direction == "TB"
    assert Diagram.model_validate({"id": "u", "title": "U", "type": "use_case", "direction": "TB"}).direction == "TB"
    a = apply(d, "add_node", {"name": "A"})["touched"][0]
    assert d.nodes[a].kind == "use_case"
    b = apply(d, "add_node", {"name": "B", "kind": "actor"})["touched"][0]
    apply(d, "add_edge", {"source": b, "target": a})
    assert d.edges[0].kind == "association"


def test_a_stored_diagram_keeps_its_direction():
    d = Diagram(id="u", title="U", type="use_case", direction="BT")
    assert Diagram.model_validate(d.model_dump()).direction == "BT"


# ---- hard rules ----------------------------------------------------------------------------------------------


def test_a_subject_takes_part_in_no_edge():
    d = diagram()
    for source, target in (("webshop", "kunde"), ("bestellen", "webshop")):
        with pytest.raises(OperationError, match="takes part in no edge"):
            edge(d, source, target)


def test_association_joins_an_actor_and_a_use_case():
    d = diagram()
    node(d, "Admin", "actor")
    edge(d, "zahlen", "kunde")  # either way round
    for source, target in (("kunde", "admin"), ("bestellen", "zahlen"), ("kunde", "kunde")):
        with pytest.raises(OperationError, match="joins an actor and a use case"):
            edge(d, source, target)
    edge(d, "admin", "bestellen", direction="forward")
    edge(d, "admin", "zahlen", direction="none")
    with pytest.raises(OperationError, match="only be directed forward"):
        edge(d, "admin", "zahlen", label="x", direction="both")


@pytest.mark.parametrize("kind", ["include", "extend"])
def test_include_and_extend_join_two_different_use_cases(kind):
    d = diagram()
    edge(d, "bestellen", "zahlen", kind)
    for source, target in (("kunde", "bestellen"), ("bestellen", "kunde"), ("bestellen", "bestellen")):
        with pytest.raises(OperationError, match=f"an {kind} joins two different use cases"):
            edge(d, source, target, kind)
    with pytest.raises(OperationError, match="fixed notation"):
        edge(d, "zahlen", "bestellen", kind, direction="forward")


def test_includes_form_no_cycle():
    d = diagram()
    node(d, "Pruefen", parent="webshop")
    edge(d, "bestellen", "zahlen", "include")
    edge(d, "zahlen", "pruefen", "include")
    with pytest.raises(OperationError, match="would form a cycle"):
        edge(d, "pruefen", "bestellen", "include")
    edge(d, "pruefen", "bestellen", "extend")  # extends may point back


def test_generalization_joins_nodes_of_one_kind_without_a_cycle():
    d = diagram()
    node(d, "Stammkunde", "actor")
    node(d, "Express", parent="webshop")
    edge(d, "stammkunde", "kunde", "generalization")
    edge(d, "express", "bestellen", "generalization")
    with pytest.raises(OperationError, match="would form a cycle"):
        edge(d, "kunde", "stammkunde", "generalization")
    with pytest.raises(OperationError, match="cannot specialise itself"):
        edge(d, "kunde", "kunde", "generalization")
    with pytest.raises(OperationError, match="two actors or two use cases"):
        edge(d, "kunde", "bestellen", "generalization")


def test_actors_and_subjects_stand_at_the_top_level():
    d = diagram()
    with pytest.raises(OperationError, match="actor stands outside every subject"):
        node(d, "Gast", "actor", parent="webshop")
    with pytest.raises(OperationError, match="subject stands at the top level"):
        node(d, "Innen", "subject", parent="webshop")
    apply(d, "move_node", {"id": "bestellen", "parent": None})  # a use case may stand outside a subject
    with pytest.raises(OperationError, match="outside every subject"):
        apply(d, "move_node", {"id": "kunde", "parent": "webshop"})
    with pytest.raises(OperationError, match="cannot contain"):
        node(d, "x", parent="bestellen")


def test_a_kind_change_may_not_break_the_rules():
    d = diagram()
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": "bestellen", "kind": "actor"})  # an association with an actor, and in a subject
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": "kunde", "kind": "subject"})  # an association at a subject


def test_extend_fields():
    d = diagram()
    eid = edge(
        d, "bestellen", "zahlen", "extend", fields={"condition": "Gutschein vorhanden", "extension_point": "Zahlung"}
    )
    assert d.edges[-1].properties == {"condition": "Gutschein vorhanden", "extension_point": "Zahlung"}
    apply(d, "update_edge", {"id": eid, "fields": {"condition": ""}})
    assert d.edges[-1].properties == {"extension_point": "Zahlung"}
    with pytest.raises(OperationError, match="at most 60 characters"):
        apply(d, "update_edge", {"id": eid, "fields": {"extension_point": "x" * 61}})
    with pytest.raises(OperationError, match="have no field 'condition'"):
        edge(d, "bestellen", "zahlen", "include", fields={"condition": "x"})
    with pytest.raises(OperationError, match="have no field"):
        apply(d, "update_node", {"id": "bestellen", "fields": {"condition": "x"}})
    with pytest.raises(OperationError, match="belong to typed fields"):
        apply(d, "update_edge", {"id": eid, "properties": {"condition": "x"}})


def test_the_specification_is_the_note():
    d = diagram()
    assert d.nodes["bestellen"].description == "Ziel: Ware kaufen."
    apply(d, "update_node", {"id": "bestellen", "description": "Ziel: x\nVorbedingung: y"})
    assert "Vorbedingung" in d.nodes["bestellen"].description


def test_import_applies_the_same_rules():
    d = diagram()
    document = exchange.export_document(d)
    assert document["schema_version"] == 2 and exchange.parse_document(document).nodes.keys() == d.nodes.keys()
    for patch, message in (
        ({"id": "e9", "source": "webshop", "target": "bestellen", "kind": "association"}, "takes part in no edge"),
        ({"id": "e9", "source": "kunde", "target": "kunde", "kind": "association"}, "joins an actor and a use case"),
        ({"id": "e9", "source": "kunde", "target": "bestellen", "kind": "include"}, "joins two different use cases"),
        (
            {"id": "e9", "source": "kunde", "target": "bestellen", "kind": "generalization"},
            "two actors or two use cases",
        ),
        (
            {"id": "e9", "source": "bestellen", "target": "zahlen", "kind": "include", "direction": "forward"},
            "fixed notation",
        ),
    ):
        bad = exchange.export_document(d)
        bad["diagram"]["edges"].append(patch)
        with pytest.raises(exchange.ExchangeError, match=message):
            exchange.parse_document(bad)
    cycle = exchange.export_document(d)
    cycle["diagram"]["edges"] += [
        {"id": "e8", "source": "bestellen", "target": "zahlen", "kind": "include"},
        {"id": "e9", "source": "zahlen", "target": "bestellen", "kind": "include"},
    ]
    with pytest.raises(exchange.ExchangeError, match="cycle"):
        exchange.parse_document(cycle)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["kunde"]["parent"] = "webshop"
    with pytest.raises(exchange.ExchangeError, match="outside every subject"):
        exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["edges"].append(
        {
            "id": "e9",
            "source": "bestellen",
            "target": "zahlen",
            "kind": "extend",
            "properties": {"condition": "x" * 200},
        }
    )
    with pytest.raises(exchange.ExchangeError, match="longer than 120"):
        exchange.parse_document(bad)


# ---- rendering -----------------------------------------------------------------------------------------------


def test_mermaid_notation():
    d = diagram()
    src = mermaid.render(d)
    assert src.startswith("flowchart LR")
    assert 'subgraph n_webshop["Webshop"]' in src and "«subject»" not in src
    assert 'n_kunde["«actor»<br/>Kunde"]' in src
    assert 'n_bestellen(["Bestellen"])' in src
    assert "n_kunde --- n_bestellen" in src


def test_relationship_notation_and_automatic_texts():
    d = diagram()
    node(d, "Stammkunde", "actor")
    node(d, "Gutschein", parent="webshop")
    edge(d, "bestellen", "zahlen", "include")
    edge(
        d, "gutschein", "bestellen", "extend", fields={"condition": "Gutschein vorhanden", "extension_point": "Zahlung"}
    )
    edge(d, "gutschein", "zahlen", "extend", label="Variante")
    edge(d, "stammkunde", "kunde", "generalization")
    edge(d, "stammkunde", "zahlen", direction="forward")
    src = mermaid.render(d)
    assert 'n_bestellen -.->|"«include»"| n_zahlen' in src
    assert 'n_gutschein -.->|"«extend» [Gutschein vorhanden] (Zahlung)"| n_bestellen' in src
    assert 'n_gutschein -.->|"«extend»<br/>Variante"| n_zahlen' in src
    assert "n_stammkunde --> n_kunde" in src and "n_stammkunde --> n_zahlen" in src


def test_hostile_names_and_conditions_are_text():
    d = Diagram(id="u", title="U", type="use_case")
    node(d, '<img src=x onerror=alert(1)> "q"', "actor")
    node(d, "<b>x</b>")
    node(d, "y")
    edge(d, "<b>x</b>", "y", "extend", fields={"condition": "<i>c</i>"})
    src = mermaid.render(d)
    assert "<img" not in src and "<b>" not in src and "<i>" not in src


def test_the_renderer_is_mermaid():
    assert render.RENDERERS["use_case"].name == "mermaid"
    payload = render.payload(diagram())
    assert payload["renderer"] == "mermaid" and payload["mermaid"] and payload["svg"] is None


# ---- agent ---------------------------------------------------------------------------------------------------


def test_tools_and_prompt():
    tools = {t.name: t for t in agent.build_tools(kinds_for("use_case"), "use_case")}
    assert set(tools["add_node"].parameters["properties"]["kind"]["enum"]) == {"actor", "use_case", "subject"}
    assert set(tools["add_edge"].parameters["properties"]["kind"]["enum"]) == kinds_for("use_case").edge_kinds
    assert "fields" not in tools["add_node"].parameters["properties"]
    assert set(tools["add_edge"].parameters["properties"]["fields"]["properties"]) == {"condition", "extension_point"}
    assert "guard" not in tools["add_edge"].parameters["properties"]
    assert prompts.EXPERT_PARTS["use_case"].strip()
    prompt = agent.build_system_prompt(diagram())
    assert "chapter 18" in prompt and "Guided start" in prompt and "system boundary" in prompt


# ---- template, exchange, comparison, project model ---------------------------------------------------------------


def test_template():
    assert TEMPLATE_TYPES["webshop-use-cases"] == "use_case"
    assert templates_of("use_case") == ["webshop-use-cases", "empty"]
    d = create_from_template("webshop-use-cases", "x", "X")
    assert d.type == "use_case" and d.direction == "LR" and d.integrity_errors() == []
    assert exchange.parse_document(exchange.export_document(d)).model_dump() == d.model_dump()
    assert {n.kind for n in d.nodes.values()} == {"actor", "use_case", "subject"}
    assert {e.kind for e in d.edges} == {"association", "include", "extend", "generalization"}
    assert all(n.description for n in d.nodes.values() if n.kind == "use_case")


def test_plantuml_export():
    d = diagram()
    node(d, "Stammkunde", "actor")
    node(d, "Gutschein", parent="webshop")
    edge(d, "bestellen", "zahlen", "include")
    edge(
        d, "gutschein", "bestellen", "extend", fields={"condition": "Gutschein vorhanden", "extension_point": "Zahlung"}
    )
    edge(d, "stammkunde", "kunde", "generalization")
    edge(d, "stammkunde", "zahlen", direction="forward")
    text = exchange.export_plantuml(d)
    assert text.startswith("@startuml") and text.rstrip().endswith("@enduml")
    assert "left to right direction" in text
    assert 'actor "Kunde" as n_kunde' in text
    assert 'rectangle "Webshop" as n_webshop {\n  usecase "Bestellen" as n_bestellen' in text
    assert "n_kunde -- n_bestellen" in text and "n_stammkunde --> n_zahlen" in text
    assert "n_bestellen ..> n_zahlen : <<include>>" in text
    assert "n_gutschein ..> n_bestellen : <<extend>> [Gutschein vorhanden] (Zahlung)" in text
    assert "n_kunde <|-- n_stammkunde" in text
    assert "note right of n_bestellen : Ziel: Ware kaufen." in text


def test_comparison_marks_a_changed_specification():
    old, new = diagram(), diagram()
    apply(new, "update_node", {"id": "bestellen", "description": "Ziel: anders"})
    assert "use_case" in diff.SUPPORTED_TYPES
    assert diff.compare(old, new).changed_nodes == ["bestellen"]


def test_project_model_links_and_adoption():
    assert linkable_kinds("use_case", "actor") == ("actor",)
    assert linkable_kinds("use_case", "subject") == ("system", "subsystem")
    assert linkable_kinds("use_case", "use_case") == ()
    assert linkable_table("use_case") == {"actor": ["actor"], "subject": ["system", "subsystem"]}
    assert adoptable("use_case", "actor") and adoptable("use_case", "subject") and not adoptable("use_case", "use_case")


def test_edge_stereotypes_are_part_of_the_text_lines():
    assert mermaid.edge_lines(Edge(id="e", source="a", target="b", kind="include")) == ["«include»"]
    assert mermaid.edge_lines(Edge(id="e", source="a", target="b", kind="extend", label="x")) == ["«extend»", "x"]


# ---- API -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(store=FileStore(tmp_path)))


def test_config_describes_the_type(client):
    t = client.get("/api/config").json()["diagram_types"]["use_case"]
    assert t["renderer"] == "mermaid" and "18" in t["uml"]
    assert t["node_fields"] == {} and [f["name"] for f in t["edge_fields"]["extend"]] == [
        "condition",
        "extension_point",
    ]
    assert t["templates"] == ["webshop-use-cases", "empty"]
    assert t["linkable"] == {"actor": ["actor"], "subject": ["system", "subsystem"]}


def test_create_edit_check_export_over_http(client):
    created = client.post("/api/diagrams", json={"title": "Anwendungsfälle", "template": "webshop-use-cases"}).json()
    did = created["diagram"]["id"]
    assert created["diagram"]["type"] == "use_case" and created["diagram"]["direction"] == "LR"
    ok = client.post(
        f"/api/diagrams/{did}/ops", json={"op": "add_node", "args": {"name": "Neu", "description": "Ziel: x"}}
    )
    assert ok.status_code == 200, ok.text
    bad = client.post(
        f"/api/diagrams/{did}/ops",
        json={"op": "add_edge", "args": {"source": "neu", "target": "neu", "kind": "include"}},
    )
    assert bad.status_code == 400 and "two different use cases" in bad.text
    assert client.get(f"/api/diagrams/{did}/checks").status_code == 200
    assert client.get(f"/api/diagrams/{did}/export?format=plantuml").status_code == 200
    exported = client.get(f"/api/diagrams/{did}/export").json()
    imported = client.post("/api/diagrams/import", json={"document": exported})
    assert imported.status_code in (200, 201), imported.text
    assert imported.json()["diagram"]["type"] == "use_case"


def test_an_empty_use_case_diagram_starts_left_to_right(client):
    created = client.post("/api/diagrams", json={"title": "Leer", "template": "empty", "type": "use_case"}).json()
    assert created["diagram"]["direction"] == "LR"
