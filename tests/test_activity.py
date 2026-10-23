"""Activity diagrams (spec: docs/architecture/specs/activity-diagrams.md)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from arcviz import agent, exchange, mermaid, operations
from arcviz.model import Diagram, kinds_for
from arcviz.server import create_app
from arcviz.store import FileStore
from arcviz.templates import TEMPLATE_TYPES, TEMPLATES

UML_KINDS = {
    "opaque_action",
    "initial_node",
    "activity_final_node",
    "flow_final_node",
    "decision_node",
    "merge_node",
    "fork_node",
    "join_node",
    "central_buffer_node",
    "data_store_node",
    "activity_partition",
}


def activity() -> Diagram:
    d = Diagram(id="a", title="A", type="activity")
    apply = operations.apply
    apply(d, "add_node", {"name": "Shop", "kind": "activity_partition"})
    apply(d, "add_node", {"name": "start", "kind": "initial_node"})
    apply(d, "add_node", {"name": "Bestellen", "kind": "opaque_action", "parent": "shop"})
    apply(d, "add_node", {"name": "ok?", "kind": "decision_node", "id": "check"})
    apply(d, "add_node", {"name": "end", "kind": "activity_final_node"})
    apply(d, "add_edge", {"source": "start", "target": "bestellen", "kind": "control_flow"})
    apply(d, "add_edge", {"source": "bestellen", "target": "check", "kind": "control_flow"})
    apply(d, "add_edge", {"source": "check", "target": "end", "kind": "control_flow", "guard": "ja"})
    return d


def test_catalogue_is_the_uml_subset():
    kinds = kinds_for("activity")
    assert kinds.all_kinds == UML_KINDS
    assert kinds.container_kinds == {"activity_partition"}
    assert kinds.edge_kinds == {"control_flow", "object_flow"}


def test_deployment_kinds_are_not_activity_kinds():
    d = Diagram(id="a", title="A", type="activity")
    with pytest.raises(operations.OperationError, match="opaque_action"):
        operations.apply(d, "add_node", {"name": "x", "kind": "service"})
    dep = Diagram(id="d", title="D")
    with pytest.raises(operations.OperationError):
        operations.apply(dep, "add_node", {"name": "x", "kind": "opaque_action"})


def test_guard_is_stored_as_property_and_removable():
    d = activity()
    edge = next(e for e in d.edges if e.target == "end")
    assert edge.properties == {"guard": "ja"}
    operations.apply(d, "update_edge", {"id": edge.id, "guard": "nein"})
    assert edge.properties["guard"] == "nein"
    operations.apply(d, "update_edge", {"id": edge.id, "guard": " "})
    assert "guard" not in edge.properties


def test_guard_is_rejected_outside_activity_diagrams():
    d = Diagram(id="d", title="D")
    operations.apply(d, "add_node", {"name": "a", "kind": "service"})
    operations.apply(d, "add_node", {"name": "b", "kind": "service"})
    with pytest.raises(operations.OperationError, match="guard"):
        operations.apply(d, "add_edge", {"source": "a", "target": "b", "guard": "x"})


@pytest.mark.parametrize(
    "source,target,message",
    [
        ("shop", "bestellen", "ActivityPartition"),
        ("bestellen", "shop", "ActivityPartition"),
        ("bestellen", "start", "InitialNode"),
        ("end", "bestellen", "final"),
    ],
)
def test_uml_rules_for_edges(source, target, message):
    d = activity()
    with pytest.raises(operations.OperationError, match=message):
        operations.apply(d, "add_edge", {"source": source, "target": target, "kind": "control_flow"})


def test_edges_are_always_directed():
    d = activity()
    with pytest.raises(operations.OperationError, match="directed"):
        operations.apply(d, "add_edge", {"source": "check", "target": "bestellen", "direction": "both"})
    operations.apply(d, "update_edge", {"id": "e1", "direction": "default"})


def test_kind_changes_respect_the_rules():
    d = activity()
    with pytest.raises(operations.OperationError, match="InitialNode"):
        operations.apply(d, "update_node", {"id": "bestellen", "kind": "initial_node"})
    with pytest.raises(operations.OperationError, match="final"):
        operations.apply(d, "update_node", {"id": "check", "kind": "flow_final_node"})
    with pytest.raises(operations.OperationError, match="ActivityPartition"):
        operations.apply(d, "update_node", {"id": "check", "kind": "activity_partition"})
    operations.apply(d, "update_node", {"id": "check", "kind": "merge_node"})


def test_import_reports_rule_violations():
    d = activity()
    doc = exchange.export_document(d)
    assert doc["schema_version"] == 2
    assert exchange.parse_document(doc) == d
    doc["diagram"]["edges"].append({"id": "e9", "source": "end", "target": "bestellen", "kind": "control_flow"})
    doc["diagram"]["edges"].append({"id": "e12", "source": "check", "target": "shop", "kind": "control_flow"})
    doc["diagram"]["edges"].append({"id": "e10", "source": "check", "target": "start", "kind": "control_flow"})
    doc["diagram"]["edges"].append({"id": "e11", "source": "check", "target": "end", "direction": "both"})
    with pytest.raises(exchange.ExchangeError) as exc:
        exchange.parse_document(doc)
    text = str(exc.value)
    assert "ActivityPartition" in text and "InitialNode" in text and "final" in text and "directed" in text
    assert "connection" in text  # the default edge kind is not an activity kind


def test_mermaid_uses_uml_notation():
    d = activity()
    operations.apply(d, "add_node", {"name": "split", "kind": "fork_node"})
    operations.apply(d, "add_node", {"name": "Orders", "kind": "data_store_node"})
    operations.apply(d, "add_node", {"name": "stop", "kind": "flow_final_node"})
    src = mermaid.render(d)
    ids = mermaid.id_map(d)
    assert f"{ids['start']}@{{ shape: f-circ" in src
    assert f"{ids['end']}@{{ shape: fr-circ" in src
    assert f"{ids['stop']}@{{ shape: cross-circ" in src
    assert f"{ids['split']}@{{ shape: fork" in src
    assert f'{ids["check"]}@{{ shape: diam, label: "ok?" }}' in src
    assert f'{ids["bestellen"]}("Bestellen")' in src
    assert f'{ids["orders"]}["«datastore»<br/>Orders"]' in src
    assert f'subgraph {ids["shop"]}["Shop"]' in src
    assert f'{ids["check"]} -->|"[ja]"| {ids["end"]}' in src
    assert "classDef initial_node" in src


def test_hostile_labels_are_escaped_in_shape_syntax():
    d = Diagram(id="a", title="A", type="activity")
    operations.apply(d, "add_node", {"name": 'a\\b <i>"x"</i> & }', "kind": "decision_node", "id": "d"})
    operations.apply(d, "add_node", {"name": "n", "kind": "opaque_action"})
    operations.apply(d, "add_edge", {"source": "d", "target": "n", "guard": '<b>"g"</b>'})
    src = mermaid.render(d)
    assert 'label: "a#92;b #lt;i#gt;#quot;x#quot;#lt;/i#gt; #amp; }"' in src
    assert '|"[#lt;b#gt;#quot;g#quot;#lt;/b#gt;]"|' in src


def test_tools_and_prompt_use_uml_terms():
    tools = {t.name: t for t in agent.build_tools(kinds_for("activity"), "activity")}
    assert "ActivityNode" in tools["add_node"].description
    assert set(tools["add_node"].parameters["properties"]["kind"]["enum"]) == UML_KINDS
    assert "guard" in tools["add_edge"].parameters["properties"]
    assert "guard" in tools["update_edge"].parameters["properties"]
    assert "direction" not in tools["add_edge"].parameters["properties"]
    assert tools["add_edge"].parameters["properties"]["kind"]["enum"] == ["control_flow", "object_flow"]
    prompt = agent.build_system_prompt(activity())
    assert "UML 2.5.1" in prompt and "ActivityPartition" in prompt and "[bestellen]" in prompt


def test_template_and_api(tmp_path):
    assert TEMPLATE_TYPES["order-process"] == "activity"
    d = TEMPLATES["order-process"]("o", "O")
    assert d.type == "activity" and exchange._rule_errors(d) == []
    client = TestClient(create_app(store=FileStore(tmp_path)))
    cfg = client.get("/api/config").json()["diagram_types"]["activity"]
    assert cfg["renderer"] == "mermaid" and cfg["container_kinds"] == ["activity_partition"]
    assert cfg["features"]["focus"] and cfg["features"]["direction"] and cfg["features"]["compare"]
    assert cfg["features"]["plantuml"] and not cfg["features"]["edge_direction"]
    assert cfg["templates"] == ["order-process", "empty"]
    created = client.post("/api/diagrams", json={"title": "Prozess", "template": "order-process"}).json()
    assert created["diagram"]["type"] == "activity" and "f-circ" in created["mermaid"]
    empty = client.post("/api/diagrams", json={"title": "Leer", "type": "activity"}).json()
    assert empty["diagram"]["type"] == "activity" and empty["diagram"]["nodes"] == {}
    wrong = client.post("/api/diagrams", json={"title": "X", "type": "activity", "template": "demo"})
    assert wrong.status_code == 400
    plantuml = client.get(f"/api/diagrams/{created['diagram']['id']}/export?format=plantuml")
    assert plantuml.status_code == 200 and "if (Artikel verfügbar?) then (ja)" in plantuml.text
    op = client.post("/api/diagrams/leer/ops", json={"op": "add_node", "args": {"name": "x", "kind": "service"}})
    assert op.status_code == 400


def test_partitions_use_mermaids_swimlane_layout():
    d = activity()
    assert mermaid.render(d).startswith("---\nconfig:\n  layout: swimlane\n---\nflowchart TB\n")
    operations.apply(d, "remove_node", {"id": "shop"})
    assert mermaid.render(d).startswith("flowchart TB\n")  # no partitions, no lanes
    assert not mermaid.render(Diagram(id="x", title="X")).startswith("---")


def test_review_input_and_checks_for_activity_diagrams():
    from arcviz import review, rules

    d = activity()
    # activity rules exist (#123), but this diagram satisfies them: one way out of `check` decides nothing
    assert rules.diagram_type(d) == "activity" and rules.run_checks(d) == []
    text = review.review_input(d, [])
    assert "Diagram type: activity" in text and "«activity_partition» Shop" in text and "[e3] check -> end" in text
    _, findings, _ = review.parse_review(
        {
            "summary": "s",
            "findings": [
                {
                    "category": "risk",
                    "severity": "low",
                    "message": "m",
                    "elements": ["Bestellen"],
                    "edges": ["e1", "e9"],
                }
            ],
        },
        d,
    )
    assert findings[0].elements == ["bestellen"] and findings[0].edges == ["e1"]
