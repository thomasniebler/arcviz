"""Diagram types (spec: docs/architecture/specs/diagram-types.md): per-type tables, compatibility, API."""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from arcviz import agent, diff, exchange, mermaid, operations, render, templates
from arcviz.model import DIAGRAM_TYPES, SHARED_KINDS, Diagram, kinds_for
from arcviz.server import create_app
from arcviz.store import FileStore, fingerprint
from arcviz.templates import TEMPLATE_TYPES, TEMPLATES

# Fingerprints of the starter templates before diagram types existed: stored versions must keep them.
FINGERPRINTS = {
    "aks-database": "345613e2252a090c",
    "demo": "8299fcbdb3de73e4",
    "empty": "d10b9903de4dbf79",
    "event-driven": "94d5579faa89c3a0",
    "hub-spoke": "552c8b6b3455b6e0",
}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(store=FileStore(tmp_path)))


# ---- every per-type table covers every type -------------------------------------------------


def test_every_layer_covers_every_type():
    for diagram_type in DIAGRAM_TYPES:
        assert isinstance(kinds_for(diagram_type).all_kinds, frozenset)
        assert diagram_type in operations.TYPE_OPERATIONS
        assert set(operations.TYPE_OPERATIONS[diagram_type]) <= set(operations.OPERATIONS)
        assert {"rename_diagram", "apply_batch"} <= set(operations.TYPE_OPERATIONS[diagram_type])
        assert diagram_type in render.RENDERERS
        assert agent.build_tools(kinds_for(diagram_type), diagram_type)
        assert agent.build_system_prompt(Diagram(id="x", title="X", type=diagram_type))
        assert diagram_type in set(TEMPLATE_TYPES.values()) | {"deployment"}


def test_every_operation_belongs_to_a_type():
    offered = {name for names in operations.TYPE_OPERATIONS.values() for name in names}
    assert offered == set(operations.OPERATIONS)


def test_tools_match_the_operations_of_the_type():
    for diagram_type in DIAGRAM_TYPES:
        tools = {t.name for t in agent.build_tools(kinds_for(diagram_type), diagram_type)}
        # the model-link operations come with the element tools of a diagram in a project (shared-project-model.md)
        editing = set(operations.TYPE_OPERATIONS[diagram_type]) - set(operations.MODEL_OPERATIONS)
        editing -= set(operations.RELATION_OPERATIONS)  # likewise the relation links (model-relations.md)
        assert set(operations.MODEL_OPERATIONS) <= set(operations.TYPE_OPERATIONS[diagram_type])
        assert tools == editing | {"get_diagram"}, diagram_type
        batch = next(t for t in agent.build_tools(kinds_for(diagram_type), diagram_type) if t.name == "apply_batch")
        step_ops = batch.parameters["properties"]["operations"]["items"]["properties"]["op"]["enum"]
        assert set(step_ops) == set(operations.TYPE_OPERATIONS[diagram_type]) - {"apply_batch"} - set(
            operations.INTERNAL_OPERATIONS
        )


def test_kind_names_are_disjoint_across_types_except_the_shared_ones():
    """ADR-0185: a name in several types is listed in SHARED_KINDS with exactly those types and has one container-ness."""
    owners: dict[str, set[str]] = {}
    containers: dict[str, set[bool]] = {}
    for diagram_type in DIAGRAM_TYPES:
        catalog = kinds_for(diagram_type)
        for kind in catalog.all_kinds | catalog.edge_kinds:
            owners.setdefault(kind, set()).add(diagram_type)
            containers.setdefault(kind, set()).add(kind in catalog.container_kinds)
    for kind, types in owners.items():
        if len(types) > 1:
            assert SHARED_KINDS.get(kind) == types, f"{kind} is a kind of {sorted(types)} and not listed as shared"
            assert len(containers[kind]) == 1, f"{kind} is a container in one type and a leaf in another"
    for kind, types in SHARED_KINDS.items():
        assert owners.get(kind) == set(types), f"SHARED_KINDS lists {kind} for {sorted(types)}, but it is not"


# Words Mermaid reads as syntax where a style class is named: `classDef class …` is a parse error.
MERMAID_KEYWORDS = {
    "class",
    "classDef",
    "style",
    "linkStyle",
    "click",
    "subgraph",
    "end",
    "direction",
    "graph",
    "flowchart",
    "default",
}


def test_no_style_class_is_a_mermaid_keyword():
    for diagram_type in DIAGRAM_TYPES:
        for kind in kinds_for(diagram_type).all_kinds:
            assert mermaid._style_name(kind) not in MERMAID_KEYWORDS, (
                f"{kind} of {diagram_type} needs another style name"
            )


# ---- model and compatibility -----------------------------------------------------------------


def test_type_defaults_to_deployment_and_unknown_types_are_rejected():
    assert Diagram(id="x", title="X").type == "deployment"
    assert Diagram.model_validate({"id": "x", "title": "X"}).type == "deployment"
    with pytest.raises(ValidationError, match="Unknown diagram type"):
        Diagram(id="x", title="X", type="mindmap")


@pytest.mark.parametrize("template", sorted(FINGERPRINTS))
def test_fingerprints_of_deployment_diagrams_are_unchanged(template):
    assert fingerprint(TEMPLATES[template]("x", "X")) == FINGERPRINTS[template]


def test_mermaid_of_deployment_diagrams_is_unchanged():
    source = mermaid.render(TEMPLATES["demo"]("x", "X"))
    assert hashlib.sha256(source.encode()).hexdigest()[:16] == "c03b42cc4a9ceb69"


def test_stored_diagram_without_type_loads_as_deployment(tmp_path):
    store = FileStore(tmp_path)
    store.create("Old", "old", templates.demo_diagram("old", "Old"))
    path = tmp_path / "diagrams" / "old" / "latest.json"
    data = json.loads(path.read_text())
    data.pop("type", None)
    path.write_text(json.dumps(data))
    assert store.load("old").type == "deployment"


# ---- operations --------------------------------------------------------------------------------


def test_operations_outside_the_type_are_rejected(monkeypatch):
    monkeypatch.setitem(
        operations.TYPE_OPERATIONS,
        "deployment",
        tuple(n for n in operations.TYPE_OPERATIONS["deployment"] if n != "set_direction"),
    )
    d = templates.demo_diagram()
    with pytest.raises(operations.OperationError, match="not available for deployment diagrams"):
        operations.apply(d, "set_direction", {"direction": "LR"})
    with pytest.raises(operations.OperationError, match="step 1"):
        operations.apply(d, "apply_batch", {"operations": [{"op": "set_direction", "args": {"direction": "LR"}}]})
    assert d.direction == "TB"


def test_kind_errors_list_the_kinds_of_the_type():
    d = Diagram(id="x", title="X")
    with pytest.raises(operations.OperationError) as exc:
        operations.apply(d, "add_node", {"name": "a", "kind": "nope"})
    for kind in sorted(kinds_for("deployment").all_kinds):
        assert kind in str(exc.value)


# ---- rendering ---------------------------------------------------------------------------------


def test_payload_names_the_renderer():
    d = templates.demo_diagram()
    payload = render.payload(d, highlight=["service-a"])
    assert payload["renderer"] == "mermaid"
    assert payload["svg"] is None
    assert payload["mermaid"] == mermaid.render(d, highlight=["service-a"])
    assert payload["mermaid_ids"] == mermaid.id_map(d)


# ---- exchange ----------------------------------------------------------------------------------


def test_deployment_documents_stay_schema_version_1():
    document = exchange.export_document(templates.demo_diagram())
    assert document["schema_version"] == 1
    assert document["diagram"]["type"] == "deployment"


def test_documents_of_schema_version_2_are_read():
    document = exchange.export_document(templates.demo_diagram())
    document["schema_version"] = 2
    assert exchange.parse_document(document) == templates.demo_diagram()
    document["schema_version"] = 3
    with pytest.raises(exchange.ExchangeError, match="newer Arcviz"):
        exchange.parse_document(document)


def test_unknown_type_in_a_document_is_reported():
    document = exchange.export_document(templates.demo_diagram())
    document["diagram"]["type"] = "mindmap"
    with pytest.raises(exchange.ExchangeError, match="Unknown diagram type"):
        exchange.parse_document(document)


# ---- HTTP API ------------------------------------------------------------------------------------


def test_config_describes_every_type(client):
    cfg = client.get("/api/config").json()
    assert set(cfg["diagram_types"]) == set(DIAGRAM_TYPES)
    dep = cfg["diagram_types"]["deployment"]
    assert dep["renderer"] == "mermaid"
    assert dep["node_kinds"] == cfg["node_kinds"]
    assert dep["container_kinds"] == cfg["container_kinds"]
    assert dep["edge_kinds"] == cfg["edge_kinds"]
    assert dep["features"] == {
        "focus": True,
        "direction": True,
        "compare": True,
        "plantuml": True,
        "edge_direction": True,
    }
    assert "uml" in dep
    assert set(dep["operations"]) == set(operations.TYPE_OPERATIONS["deployment"])
    assert set(cfg["template_types"]) == set(TEMPLATES)
    assert "demo" in dep["templates"] and "empty" in dep["templates"]


def test_create_takes_a_type(client):
    created = client.post("/api/diagrams", json={"title": "D", "type": "deployment"}).json()
    assert created["diagram"]["type"] == "deployment"
    assert created["renderer"] == "mermaid"
    bad = client.post("/api/diagrams", json={"title": "M", "type": "mindmap"})
    assert bad.status_code == 400 and "deployment" in bad.json()["detail"]


def test_responses_carry_the_render_payload(client):
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    detail = client.get("/api/diagrams/demo").json()
    assert detail["renderer"] == "mermaid" and detail["svg"] is None and detail["mermaid"].startswith("flowchart")
    rendered = client.get("/api/diagrams/demo/render").json()
    assert rendered["renderer"] == "mermaid" and rendered["mermaid"] == detail["mermaid"]
    ops = client.post("/api/diagrams/demo/ops", json={"op": "rename_diagram", "args": {"title": "X"}}).json()
    assert ops["renderer"] == "mermaid"
    version = client.get("/api/diagrams/demo/versions/1").json()
    assert version["renderer"] == "mermaid" and version["mermaid"]
    compare = client.get("/api/diagrams/demo/compare?base=1").json()
    assert compare["renderer"] == "mermaid" and compare["mermaid"]


def test_diff_supports_deployment():
    assert "deployment" in diff.SUPPORTED_TYPES
