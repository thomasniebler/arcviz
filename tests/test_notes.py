"""Notes on nodes and lifelines (issue #87; spec: docs/architecture/specs/collaboration.md, ADR-0092, ADR-0093)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import diff, exchange
from arcviz import operations as ops
from arcviz.agent import TOOLS, build_tools, get_diagram_result
from arcviz.llm.base import ModelReply
from arcviz.model import (
    BUILTIN_KINDS,
    MAX_DESCRIPTION,
    NOTE_CHARS,
    SHORT_NOTE_CHARS,
    Diagram,
    Node,
    fingerprint,
)
from arcviz.sequence import Lifeline
from arcviz.server import create_app
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider, call


def sequence() -> Diagram:
    d = Diagram(id="s", title="S", type="sequence")
    ops.apply(d, "add_lifeline", {"name": "Kasse"})
    return d


# ---- model ---------------------------------------------------------------------------------


def test_an_unset_description_is_omitted_from_dumps_and_fingerprints():
    node = Node(id="a", name="A")
    assert "description" not in node.model_dump()
    assert "description" not in Lifeline(id="l", name="L").model_dump()
    d = demo_diagram()
    before = fingerprint(d)
    ops.update_node(d, "service-a", description="Bestellungen")
    assert d.nodes["service-a"].model_dump()["description"] == "Bestellungen"
    assert fingerprint(d) != before
    ops.update_node(d, "service-a", description="")
    assert "description" not in d.nodes["service-a"].model_dump()
    assert fingerprint(d) == before


def test_a_description_longer_than_the_limit_is_refused_by_the_model():
    with pytest.raises(ValueError):
        Node(id="a", name="A", description="x" * (MAX_DESCRIPTION + 1))


# ---- operations ----------------------------------------------------------------------------------


def test_add_and_update_node_set_change_and_clear_the_note():
    d = demo_diagram()
    result = ops.apply(d, "add_node", {"name": "cache", "kind": "storage", "description": "  Redis, 1 GB  "})
    assert result["result"]["description"] == "Redis, 1 GB"
    assert d.nodes["cache"].description == "Redis, 1 GB"
    ops.apply(d, "update_node", {"id": "cache", "name": "Cache"})
    assert d.nodes["cache"].description == "Redis, 1 GB"  # None leaves it as it is
    ops.apply(d, "update_node", {"id": "cache", "description": "Redis"})
    assert d.nodes["cache"].description == "Redis"
    ops.apply(d, "update_node", {"id": "cache", "description": "   "})
    assert d.nodes["cache"].description is None
    assert ops.apply(d, "add_node", {"name": "x", "description": ""})["result"].get("description") is None


def test_a_too_long_note_is_an_operation_error_and_nothing_changes():
    d = demo_diagram()
    before = d.model_dump()
    with pytest.raises(ops.OperationError, match=str(MAX_DESCRIPTION)):
        ops.apply(d, "update_node", {"id": "service-a", "name": "Neu", "description": "x" * (MAX_DESCRIPTION + 1)})
    assert d.model_dump() == before
    with pytest.raises(ops.OperationError, match="description"):
        ops.apply(d, "update_node", {"id": "service-a", "description": 5})


def test_lifeline_notes():
    d = sequence()
    ops.apply(d, "add_lifeline", {"name": "Bank", "description": "Externer Zahlungsdienst"})
    ops.apply(d, "update_lifeline", {"id": "kasse", "description": "Frontend"})
    lifelines = {lf.id: lf for lf in d.interaction.lifelines}
    assert lifelines["bank"].description == "Externer Zahlungsdienst"
    assert lifelines["kasse"].description == "Frontend"
    ops.apply(d, "update_lifeline", {"id": "kasse", "description": ""})
    assert "description" not in d.interaction.lifelines[0].model_dump()
    with pytest.raises(ops.OperationError):
        ops.apply(d, "update_lifeline", {"id": "kasse", "description": "x" * (MAX_DESCRIPTION + 1)})


def test_activity_nodes_take_notes_too():
    d = Diagram(id="a", title="A", type="activity")
    ops.apply(d, "add_node", {"name": "Pruefen", "description": "Vier-Augen-Prinzip"})
    assert d.nodes["pruefen"].description == "Vier-Augen-Prinzip"


def test_a_note_works_in_a_batch_and_a_failing_batch_keeps_none():
    d = demo_diagram()
    with pytest.raises(ops.OperationError):
        ops.apply(
            d,
            "apply_batch",
            {
                "operations": [
                    {"op": "update_node", "args": {"id": "service-a", "description": "neu"}},
                    {"op": "update_node", "args": {"id": "ghost"}},
                ]
            },
        )
    assert d.nodes["service-a"].description is None


# ---- the assistant -------------------------------------------------------------------------------


def test_the_summary_shows_notes_on_one_line_and_cut():
    d = demo_diagram()
    ops.update_node(d, "service-a", description="Bestellungen\nund Rechnungen")
    ops.update_node(d, "service-b", description="y" * (NOTE_CHARS + 50))
    text = d.summary()
    line = next(line for line in text.splitlines() if "[service-a]" in line)
    assert line.endswith('— note: "Bestellungen und Rechnungen"')
    long_line = next(line for line in text.splitlines() if "[service-b]" in line)
    assert ("y" * NOTE_CHARS + '…"') in long_line and ("y" * (NOTE_CHARS + 1)) not in long_line


def test_without_notes_the_summary_is_unchanged():
    d = demo_diagram()
    assert "note" not in d.summary()
    assert d.summary(budget=200) == demo_diagram().summary(budget=200)


def test_over_budget_notes_are_shortened_then_dropped_before_containers_collapse():
    d = demo_diagram()
    for node_id in ("service-a", "service-b", "service-c"):
        ops.update_node(d, node_id, description="z" * 150)
    full = d.summary()
    short = d._summary(set(), SHORT_NOTE_CHARS)
    hinted = d._summary(set(), 0)
    assert len(hinted) < len(short) < len(full)
    assert d.summary(budget=len(short)) == short
    assert d.summary(budget=len(hinted)) == hinted
    assert "— note (call get_diagram)" in hinted and "collapsed" not in hinted
    assert "collapsed" in d.summary(budget=len(hinted) - 1)


def test_sequence_summary_shows_lifeline_notes():
    d = sequence()
    ops.apply(d, "update_lifeline", {"id": "kasse", "description": "Frontend"})
    assert '[kasse] Kasse — note: "Frontend"' in d.summary()


def test_get_diagram_returns_full_notes():
    d = demo_diagram()
    ops.update_node(d, "service-a", description="w" * 500)
    result = json.loads(get_diagram_result(d, call("get_diagram", ids=["service-a"])).content)
    assert result["nodes"]["service-a"]["description"] == "w" * 500


def test_the_tools_offer_the_description():
    tools = {t.name: t for t in build_tools(BUILTIN_KINDS, "deployment")}
    for name in ("add_node", "update_node"):
        assert "description" in tools[name].parameters["properties"], name
    assert {t.name for t in TOOLS} >= {"add_node", "update_node"}
    activity = {t.name: t for t in build_tools(BUILTIN_KINDS, "activity")}
    assert "description" in activity["add_node"].parameters["properties"]
    seq = {t.name: t for t in build_tools(BUILTIN_KINDS, "sequence")}
    for name in ("add_lifeline", "update_lifeline"):
        assert "description" in seq[name].parameters["properties"], name


# ---- exchange, comparison ---------------------------------------------------------------------------


def test_export_and_import_keep_notes():
    d = demo_diagram()
    ops.update_node(d, "service-a", description="Bestellungen")
    back = exchange.parse_document(exchange.export_document(d))
    assert back.nodes["service-a"].description == "Bestellungen"
    plain = exchange.export_document(demo_diagram())
    assert "description" not in json.dumps(plain)


def test_an_import_with_a_too_long_note_is_refused():
    document = exchange.export_document(demo_diagram())
    document["diagram"]["nodes"]["service-a"]["description"] = "x" * (MAX_DESCRIPTION + 1)
    with pytest.raises(exchange.ExchangeError):
        exchange.parse_document(document)


def test_a_changed_note_is_a_changed_node_in_the_comparison():
    old, new = demo_diagram(), demo_diagram()
    ops.update_node(new, "service-a", description="neu")
    assert diff.compare(old, new).changed_nodes == ["service-a"]


# ---- through the server and the agent ---------------------------------------------------------------


def test_rest_ops_and_the_agent_set_notes(store):
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("update_node", "c1", id="service-b", description="vom Assistenten")],
                stop_reason="tool_use",
            ),
            ModelReply(text="Notiz gesetzt.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    client = TestClient(create_app(store=store, provider=provider))
    r = client.post(
        "/api/diagrams/demo/ops", json={"op": "update_node", "args": {"id": "service-a", "description": "x"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["diagram"]["nodes"]["service-a"]["description"] == "x"
    with client.stream("POST", "/api/diagrams/demo/chat", json={"message": "Notiz"}) as response:
        body = "".join(response.iter_text())
    assert '"type": "final"' in body
    assert store.load("demo").nodes["service-b"].description == "vom Assistenten"
    assert 'note: "x"' in provider.requests[0]["system"]
