"""State machine diagrams (spec: docs/architecture/specs/state-machine-diagrams.md, ADR-0178)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import agent, diff, exchange, mermaid, operations, prompts, render
from arcviz.graph_types import profile_of
from arcviz.model import Diagram, kinds_for
from arcviz.operations import OperationError, apply
from arcviz.project_model import linkable_kinds
from arcviz.server import create_app
from arcviz.store import FileStore
from arcviz.templates import TEMPLATE_TYPES, TEMPLATES, create_from_template, templates_of

LEAVES = {
    "state",
    "initial_pseudostate",
    "final_state",
    "terminate_pseudostate",
    "choice_pseudostate",
    "junction_pseudostate",
    "fork_pseudostate",
    "join_pseudostate",
    "shallow_history_pseudostate",
    "deep_history_pseudostate",
}


def node(d: Diagram, name: str, kind: str = "state", **args) -> str:
    return apply(d, "add_node", {"name": name, "kind": kind, **args})["touched"][0]


def edge(d: Diagram, source: str, target: str, **args) -> None:
    apply(d, "add_edge", {"source": source, "target": target, **args})


def machine() -> Diagram:
    d = Diagram(id="m", title="M", type="state_machine")
    start = node(d, "Start", "initial_pseudostate")
    neu = node(d, "Neu")
    bezahlt = node(d, "Bezahlt", fields={"entry": "x"})
    end = node(d, "Ende", "final_state")
    edge(d, start, neu)
    edge(d, neu, bezahlt, label="bezahlen", guard="Betrag ok", fields={"effect": "buchen"})
    edge(d, bezahlt, end, label="abschliessen")
    return d


# ---- catalogue, defaults, profile ---------------------------------------------------------------------------


def test_catalogue_is_the_uml_subset():
    kinds = kinds_for("state_machine")
    assert kinds.all_kinds == LEAVES | {"composite_state", "state_region"}
    assert kinds.container_kinds == {"composite_state", "state_region"}
    assert kinds.edge_kinds == {"transition"}


def test_profile_and_defaults():
    profile = profile_of("state_machine")
    assert profile is not None and profile.guard and profile.directed_noun == "Transitions"
    d = Diagram(id="m", title="M", type="state_machine")
    assert d.nodes == {}
    a, b = node(d, "A", kind="state"), apply(d, "add_node", {"name": "B"})["touched"][0]
    assert d.nodes[b].kind == "state"
    edge(d, a, b)
    assert d.edges[0].kind == "transition"


def test_other_types_kinds_are_refused():
    d = Diagram(id="m", title="M", type="state_machine")
    with pytest.raises(OperationError, match="state"):
        apply(d, "add_node", {"name": "x", "kind": "opaque_action"})
    with pytest.raises(OperationError):
        apply(Diagram(id="a", title="A", type="activity"), "add_node", {"name": "x", "kind": "state"})


def test_nothing_in_a_state_machine_can_be_linked_to_a_project_element():
    ops = operations.TYPE_OPERATIONS["state_machine"]
    assert {"add_node", "apply_batch", "set_direction"} <= set(ops) and "set_completeness" not in ops
    assert set(operations.MODEL_OPERATIONS) <= set(ops)  # every type has them (test_diagram_types)
    for kind in kinds_for("state_machine").all_kinds:
        assert linkable_kinds("state_machine", kind) == ()


# ---- typed fields --------------------------------------------------------------------------------------------


def test_state_behaviours_and_transition_texts():
    d = machine()
    bezahlt = d.nodes["bezahlt"]
    assert bezahlt.properties == {"entry": "x"}
    apply(d, "update_node", {"id": "bezahlt", "fields": {"do": "prüfen", "exit": "", "entry": "Rechnung senden"}})
    assert d.nodes["bezahlt"].properties == {"do": "prüfen", "entry": "Rechnung senden"}
    t = d.edges[1]
    assert (t.label, t.properties) == ("bezahlen", {"guard": "Betrag ok", "effect": "buchen"})
    apply(d, "update_edge", {"id": t.id, "guard": "", "fields": {"effect": ""}})
    assert d.edges[1].properties == {}


def test_fields_are_limited_to_their_kind():
    d = machine()
    comp = node(d, "Bearbeitung", "composite_state")
    with pytest.raises(OperationError, match="have no field 'entry'"):
        apply(d, "update_node", {"id": comp, "fields": {"entry": "x"}})
    with pytest.raises(OperationError, match="have no field 'effect'"):
        apply(d, "add_node", {"name": "i", "kind": "initial_pseudostate", "fields": {"effect": "x"}})
    with pytest.raises(OperationError, match=r"use fields\.entry"):
        apply(d, "update_node", {"id": "bezahlt", "properties": {"entry": "x"}})
    with pytest.raises(OperationError, match="at most 120 characters"):
        apply(d, "update_node", {"id": "bezahlt", "fields": {"do": "x" * 121}})


def test_guard_is_for_activity_and_state_machines_only():
    with pytest.raises(OperationError, match="activity and state_machine diagrams, not deployment"):
        apply(TEMPLATES["demo"]("x", "X"), "add_edge", {"source": "service-a", "target": "service-b", "guard": "g"})


# ---- hard rules ----------------------------------------------------------------------------------------------


def test_no_transition_at_a_region():
    d = Diagram(id="m", title="M", type="state_machine")
    comp = node(d, "C", "composite_state")
    region = node(d, "R", "state_region", parent=comp)
    other = node(d, "S")
    for source, target in ((other, region), (region, other)):
        with pytest.raises(OperationError, match="not a vertex"):
            edge(d, source, target)


def test_initial_and_final_rules():
    d = Diagram(id="m", title="M", type="state_machine")
    start, a, end, term = (
        node(d, "i", "initial_pseudostate"),
        node(d, "a"),
        node(d, "e", "final_state"),
        node(d, "t", "terminate_pseudostate"),
    )
    with pytest.raises(OperationError, match="no incoming"):
        edge(d, a, start)
    for final in (end, term):
        with pytest.raises(OperationError, match="no outgoing"):
            edge(d, final, a)
    edge(d, start, a)
    edge(d, a, end)


@pytest.mark.parametrize(
    "build",
    [
        lambda d, s: edge(d, node(d, "i", "initial_pseudostate"), s, label="evt"),
        lambda d, s: edge(d, node(d, "i", "initial_pseudostate"), s, guard="g"),
        lambda d, s: edge(d, node(d, "f", "fork_pseudostate"), s, label="evt"),
        lambda d, s: edge(d, s, node(d, "j", "join_pseudostate"), guard="g"),
    ],
)
def test_no_trigger_or_guard_on_initial_fork_and_join_transitions(build):
    d = Diagram(id="m", title="M", type="state_machine")
    s = node(d, "S")
    with pytest.raises(OperationError, match="no trigger and no guard"):
        build(d, s)


def test_an_initial_transition_may_have_an_effect():
    d = Diagram(id="m", title="M", type="state_machine")
    i, s = node(d, "i", "initial_pseudostate"), node(d, "S")
    edge(d, i, s, fields={"effect": "init"})
    with pytest.raises(OperationError, match="no trigger and no guard"):
        apply(d, "update_edge", {"id": d.edges[0].id, "label": "evt"})


def test_transitions_are_directed():
    d = Diagram(id="m", title="M", type="state_machine")
    a, b = node(d, "A"), node(d, "B")
    with pytest.raises(OperationError, match="Transitions are always directed"):
        edge(d, a, b, direction="both")


def test_regions_belong_to_composite_states_and_are_not_mixed_with_states():
    d = Diagram(id="m", title="M", type="state_machine")
    with pytest.raises(OperationError, match="state_region"):
        node(d, "R", "state_region")  # top level
    comp = node(d, "C", "composite_state")
    inner = node(d, "Inner", parent=comp)
    with pytest.raises(OperationError, match="already has states directly"):
        node(d, "R", "state_region", parent=comp)
    comp2 = node(d, "C2", "composite_state")
    region = node(d, "R2", "state_region", parent=comp2)
    with pytest.raises(OperationError, match="has regions"):
        node(d, "S", parent=comp2)
    with pytest.raises(OperationError, match="has regions"):
        apply(d, "move_node", {"id": inner, "parent": comp2})
    node(d, "S", parent=region)  # a state goes into a region
    with pytest.raises(OperationError, match="state_region"):
        node(d, "R3", "state_region", parent=region)  # regions do not nest


def test_only_composites_have_children():
    d = Diagram(id="m", title="M", type="state_machine")
    s = node(d, "S")
    with pytest.raises(OperationError, match="cannot contain"):
        node(d, "x", parent=s)


def test_a_state_becomes_composite_and_back_when_empty():
    d = Diagram(id="m", title="M", type="state_machine")
    s = node(d, "S", fields={"entry": "a"})
    apply(d, "update_node", {"id": s, "kind": "composite_state"})
    assert "entry" not in d.nodes[s].properties  # the new kind has no such field
    inner = node(d, "Inner", parent=s)
    with pytest.raises(OperationError, match="still contains"):
        apply(d, "update_node", {"id": s, "kind": "state"})
    apply(d, "remove_node", {"id": inner})
    apply(d, "update_node", {"id": s, "kind": "state"})


def test_a_kind_change_may_not_break_edges():
    d = machine()
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": "neu", "kind": "initial_pseudostate"})  # has an incoming transition
    with pytest.raises(OperationError, match="would break a rule"):
        apply(d, "update_node", {"id": "bezahlt", "kind": "final_state"})  # has an outgoing transition


def test_import_applies_the_same_rules():
    d = machine()
    document = exchange.export_document(d)
    assert document["schema_version"] == 2 and document["diagram"]["type"] == "state_machine"
    assert exchange.parse_document(document).nodes.keys() == d.nodes.keys()
    bad = exchange.export_document(d)
    bad["diagram"]["edges"].append({"id": "e9", "source": "bezahlt", "target": "start", "kind": "transition"})
    with pytest.raises(exchange.ExchangeError, match="no incoming"):
        exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["bezahlt"]["parent"] = "neu"
    with pytest.raises(exchange.ExchangeError, match="not a container"):
        exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["bezahlt"]["properties"]["entry"] = "x" * 200
    with pytest.raises(exchange.ExchangeError, match="longer than 120"):
        exchange.parse_document(bad)
    bad = exchange.export_document(d)
    bad["diagram"]["nodes"]["bezahlt"]["parent"] = None
    bad["diagram"]["nodes"]["region"] = {"id": "region", "name": "R", "kind": "state_region"}
    with pytest.raises(exchange.ExchangeError, match="state_region"):
        exchange.parse_document(bad)


# ---- rendering -----------------------------------------------------------------------------------------------


def test_mermaid_shapes_and_texts():
    d = machine()
    apply(d, "update_node", {"id": "bezahlt", "fields": {"do": "prüfen"}})
    src = mermaid.render(d)
    assert src.startswith("flowchart TB")
    assert "n_start@{ shape: f-circ }" in src and "n_ende@{ shape: fr-circ }" in src
    assert 'n_neu("Neu")' in src
    assert 'n_bezahlt("Bezahlt<br/>──────────<br/>entry / x<br/>do / prüfen")' in src
    assert '-->|"bezahlen [Betrag ok] / buchen"|' in src
    assert "«state»" not in src and "«composite_state»" not in src


def test_every_kind_has_a_notation():
    d = Diagram(id="m", title="M", type="state_machine")
    comp = node(d, "Comp", "composite_state")
    region = node(d, "Reg", "state_region", parent=comp)
    for kind in sorted(LEAVES):
        node(d, kind, kind, parent=region)
    src = mermaid.render(d)
    for shape in ("f-circ", "fr-circ", "cross-circ", "sm-circ", "diam", "fork"):
        assert f"shape: {shape}" in src
    assert 'label: "H"' in src and 'label: "H*"' in src
    assert "subgraph n_comp[" in src and "subgraph n_reg[" in src
    assert src.count("fork }") + src.count('fork, label: " " }') == 2  # fork and join are bars


def test_hostile_names_and_texts_are_shown_literally():
    d = Diagram(id="m", title="M", type="state_machine")
    a = node(d, '<b>x</b> "q" & #lt;', fields={"entry": "<script>alert(1)</script>"})
    b = node(d, "end")
    edge(d, a, b, label="a|b", guard='"g"', fields={"effect": "<i>x</i>"})
    src = mermaid.render(d)
    assert "<script>" not in src and "<b>" not in src and "<i>" not in src
    assert "#lt;b#gt;x#lt;/b#gt;" in src and "#35;lt;" in src  # an entity code in a name is shown as typed
    lines = [line for line in src.splitlines() if "-->" in line]
    assert len(lines) == 1


def test_transition_text_parts_are_optional():
    from arcviz.model import Edge
    from arcviz.state_machine import transition_text

    def text(label=None, guard="", effect=""):
        props = {k: v for k, v in (("guard", guard), ("effect", effect)) if v}
        return transition_text(Edge(id="e", source="a", target="b", label=label, kind="transition", properties=props))

    assert text() == ""
    assert text("evt") == "evt"
    assert text(guard="g") == "[g]"
    assert text(effect="act") == "/ act"
    assert text("evt", "g", "act") == "evt [g] / act"
    assert text("evt", effect="act") == "evt / act"


def test_the_renderer_is_mermaid():
    assert render.RENDERERS["state_machine"].name == "mermaid"
    payload = render.payload(machine())
    assert payload["renderer"] == "mermaid" and payload["mermaid"] and payload["svg"] is None


def test_focus_on_a_composite_state():
    d = Diagram(id="m", title="M", type="state_machine")
    comp = node(d, "C", "composite_state")
    inner = node(d, "Inner", parent=comp)
    other = node(d, "Other")
    edge(d, other, inner)
    src = mermaid.render(d, focus=comp)
    assert "subgraph n_c[" in src and "n_inner" in src


# ---- agent ---------------------------------------------------------------------------------------------------


def test_tools_and_prompt():
    tools = {t.name: t for t in agent.build_tools(kinds_for("state_machine"), "state_machine")}
    node_kinds = tools["add_node"].parameters["properties"]["kind"]["enum"]
    assert set(node_kinds) == kinds_for("state_machine").all_kinds
    assert tools["add_edge"].parameters["properties"]["kind"]["enum"] == ["transition"]
    assert "direction" not in tools["add_edge"].parameters["properties"]
    assert "guard" in tools["add_edge"].parameters["properties"]
    edge_fields = tools["add_edge"].parameters["properties"]["fields"]
    assert set(edge_fields["properties"]) == {"effect"} and edge_fields["additionalProperties"] is False
    node_fields = tools["add_node"].parameters["properties"]["fields"]
    assert set(node_fields["properties"]) == {"entry", "do", "exit"}
    assert "link_element" not in tools and "set_completeness" not in tools
    step_ops = tools["apply_batch"].parameters["properties"]["operations"]["items"]["properties"]["op"]["enum"]
    assert set(step_ops) == set(operations.batch_operations("state_machine")) and "add_edge" in step_ops


def test_prompt_has_the_expert_part():
    assert "state_machine" in prompts.EXPERT_PARTS
    prompt = agent.build_system_prompt(machine())
    for kind in ("composite_state", "choice_pseudostate", "initial_pseudostate"):
        assert kind in prompt
    assert "State Machines" in prompt and "Guided start" in prompt


# ---- template, exchange, comparison --------------------------------------------------------------------------


def test_template():
    assert TEMPLATE_TYPES["order-lifecycle"] == "state_machine"
    assert templates_of("state_machine") == ["order-lifecycle", "empty"]
    d = create_from_template("order-lifecycle", "x", "X")
    assert d.type == "state_machine" and d.integrity_errors() == []
    assert exchange.parse_document(exchange.export_document(d)).model_dump() == d.model_dump()
    kinds = {n.kind for n in d.nodes.values()}
    assert {
        "initial_pseudostate",
        "final_state",
        "composite_state",
        "state_region",
        "join_pseudostate",
        "state",
    } <= kinds
    with pytest.raises(ValueError):
        create_from_template("order-lifecycle", "x", "X", diagram_type="activity")


def test_plantuml_export():
    d = machine()
    apply(d, "update_node", {"id": "bezahlt", "fields": {"do": "prüfen"}})
    text = exchange.export_plantuml(d)
    assert text.startswith("@startuml") and text.rstrip().endswith("@enduml")
    assert 'state "Start" as n_start <<start>>' in text and "n_start --> n_neu" in text
    assert 'state "Neu" as n_neu' in text
    assert "n_bezahlt : entry / x" in text and "n_bezahlt : do / prüfen" in text
    assert "n_neu --> n_bezahlt : bezahlen [Betrag ok] / buchen" in text
    assert 'state "Ende" as n_ende <<end>>' in text and "n_bezahlt --> n_ende : abschliessen" in text


def test_plantuml_of_the_template_has_every_construct():
    text = exchange.export_plantuml(create_from_template("order-lifecycle", "x", "X"))
    assert "<<join>>" in text and "--" in text and text.count("{") == text.count("}")
    assert "state " in text


def test_comparison_marks_changes():
    old = machine()
    new = machine()
    apply(new, "update_edge", {"id": new.edges[1].id, "guard": "anders"})
    apply(new, "add_node", {"name": "Neu2"})
    assert "state_machine" in diff.SUPPORTED_TYPES
    result = diff.compare(old, new)
    assert result.added_nodes == ["neu2"] and result.changed_edges == [new.edges[1].id]


# ---- API -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(store=FileStore(tmp_path)))


def test_config_describes_the_type(client):
    config = client.get("/api/config").json()
    t = config["diagram_types"]["state_machine"]
    assert t["renderer"] == "mermaid" and "Statechart" not in t["uml"] and "14" in t["uml"]
    assert set(t["node_kinds"]) == kinds_for("state_machine").all_kinds
    assert t["features"]["edge_direction"] is False and t["features"]["plantuml"] and t["features"]["compare"]
    assert [f["name"] for f in t["node_fields"]["state"]] == ["entry", "do", "exit"]
    assert [f["name"] for f in t["edge_fields"]["transition"]] == ["effect"]
    assert t["templates"] == ["order-lifecycle", "empty"]
    assert config["template_types"]["order-lifecycle"] == "state_machine"


def test_create_edit_check_export_over_http(client):
    created = client.post("/api/diagrams", json={"title": "Bestellung", "template": "order-lifecycle"}).json()
    did = created["diagram"]["id"]
    assert created["diagram"]["type"] == "state_machine"
    assert created["renderer"] == "mermaid"
    ops = client.post(
        f"/api/diagrams/{did}/ops",
        json={"op": "add_node", "args": {"name": "Parkend", "fields": {"entry": "x"}}},
    )
    assert ops.status_code == 200, ops.text
    bad = client.post(
        f"/api/diagrams/{did}/ops",
        json={"op": "add_node", "args": {"name": "P2", "fields": {"nope": "x"}}},
    )
    assert bad.status_code == 400 and "no field 'nope'" in bad.text
    assert client.get(f"/api/diagrams/{did}/checks").status_code == 200
    assert client.get(f"/api/diagrams/{did}/export?format=plantuml").status_code == 200
    exported = client.get(f"/api/diagrams/{did}/export").json()
    imported = client.post("/api/diagrams/import", json={"document": exported})
    assert imported.status_code in (200, 201), imported.text
    assert imported.json()["diagram"]["type"] == "state_machine"


def test_empty_template_creates_an_empty_machine(client):
    created = client.post("/api/diagrams", json={"title": "Leer", "template": "empty", "type": "state_machine"}).json()
    assert created["diagram"]["type"] == "state_machine" and created["diagram"]["nodes"] == {}


def test_removing_a_container_must_leave_its_content_valid():
    d = Diagram(id="m", title="M", type="state_machine")
    comp = node(d, "C", "composite_state")
    region = node(d, "R", "state_region", parent=comp)
    inner = node(d, "In", parent=region)
    with pytest.raises(OperationError, match="Cannot remove 'c' and keep its content"):
        apply(d, "remove_node", {"id": comp})  # the region would become top level
    assert comp in d.nodes and region in d.nodes
    second = node(d, "R2", "state_region", parent=comp)
    with pytest.raises(OperationError, match="has regions"):
        apply(d, "remove_node", {"id": region})  # its state would sit next to a region
    apply(d, "move_node", {"id": inner, "parent": second})
    apply(d, "remove_node", {"id": region})
    apply(d, "remove_node", {"id": comp, "cascade": True})
    assert inner not in d.nodes


def test_a_region_cannot_be_moved_to_the_top_level():
    # found by the property test: move_node with parent=None skipped the node rules
    d = Diagram(id="m", title="M", type="state_machine")
    operations.apply(d, "add_node", {"name": "Aktiv", "kind": "composite_state", "id": "aktiv"})
    operations.apply(d, "add_node", {"name": "Teil", "kind": "state_region", "id": "teil", "parent": "aktiv"})
    with pytest.raises(OperationError, match="state_region"):
        operations.apply(d, "move_node", {"id": "teil", "parent": None})
    assert d.nodes["teil"].parent == "aktiv"
