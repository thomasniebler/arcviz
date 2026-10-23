"""Sequence diagrams (spec: docs/architecture/specs/sequence-diagrams.md)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from arcviz import agent, exchange, operations, proposal, render, sequence, sequence_svg
from arcviz.model import Diagram, kinds_for
from arcviz.server import create_app
from arcviz.store import FileStore, fingerprint
from arcviz.templates import TEMPLATE_TYPES, TEMPLATES

SVG = "{http://www.w3.org/2000/svg}"


def seq() -> Diagram:
    return Diagram(id="s", title="S", type="sequence")


def ok(d: Diagram, op: str, **args):
    return operations.apply(d, op, args)["result"]


def basic() -> Diagram:
    d = seq()
    ok(d, "add_lifeline", name="Client", actor=True)
    ok(d, "add_lifeline", name="Server", type="OrderService")
    ok(d, "add_lifeline", name="DB")
    ok(d, "add_message", sender="client", receiver="server", name="order(id)")
    ok(d, "add_message", sender="server", receiver="db", name="insert", sort="asynchSignal")
    ok(d, "add_message", sender="server", receiver="client", name="ok", sort="reply")
    return d


def svg_of(d: Diagram, **kwargs) -> ET.Element:
    return ET.fromstring(sequence_svg.render(d, **kwargs))


def groups(root: ET.Element, element: str | None = None) -> dict[str, ET.Element]:
    return {
        g.get("data-id"): g
        for g in root.iter(f"{SVG}g")
        if g.get("data-id") is not None and (element is None or g.get("data-element") == element)
    }


# ---- model ---------------------------------------------------------------------------------------


def test_sequence_diagrams_have_an_interaction_and_no_kinds():
    d = seq()
    assert d.interaction is not None and d.interaction.lifelines == []
    assert kinds_for("sequence").all_kinds == frozenset()
    with pytest.raises(ValidationError, match="only sequence diagrams"):
        Diagram(id="x", title="X", interaction={"lifelines": []})


def test_fingerprint_sees_the_interaction_and_its_order():
    d = basic()
    before = fingerprint(d)
    ok(d, "move_lifeline", id="db", before="client")
    assert fingerprint(d) != before
    assert [lifeline.id for lifeline in d.interaction.lifelines] == ["db", "client", "server"]


def test_integrity_errors_report_broken_references():
    d = basic()
    d.interaction.fragments[0].receiver = "ghost"
    d.interaction.executions.append(sequence.ExecutionSpecification(id="ex1", lifeline="db", start="m1"))
    errors = d.integrity_errors()
    assert any("unknown lifeline 'ghost'" in e for e in errors)
    assert any("does not involve 'db'" in e for e in errors)


# ---- lifelines -------------------------------------------------------------------------------------


def test_lifelines_ids_order_and_updates():
    d = basic()
    assert [lifeline.id for lifeline in d.interaction.lifelines] == ["client", "server", "db"]
    ok(d, "add_lifeline", name="Cache", before="db")
    assert [lifeline.id for lifeline in d.interaction.lifelines] == ["client", "server", "cache", "db"]
    ok(d, "update_lifeline", id="Server", name="API", type="")
    server = sequence.find(d.interaction, "server")
    assert server.name == "API" and server.type is None
    with pytest.raises(operations.OperationError, match="already exists"):
        ok(d, "add_lifeline", name="X", id="db")
    with pytest.raises(operations.OperationError, match="Known lifelines"):
        ok(d, "update_lifeline", id="nope", name="x")


def test_remove_lifeline_removes_what_depends_on_it():
    d = basic()
    ok(d, "add_execution_specification", lifeline="db", start="m2")
    ok(d, "add_interaction_use", refers_to="Audit", covered=["db"])
    ok(d, "add_combined_fragment", operator="opt", covered=["server", "db"], contents=["m2"])
    result = ok(d, "remove_lifeline", id="db")
    assert set(result["removed"]) >= {"db", "m2", "ex1", "ref1"}
    cf = sequence.find(d.interaction, "cf1")
    assert cf is not None and cf.covered == ["server"]
    assert d.integrity_errors() == []


# ---- messages --------------------------------------------------------------------------------------


def test_message_kinds_follow_the_ends():
    d = basic()
    found = ok(d, "add_message", receiver="server", name="timeout")
    lost = ok(d, "add_message", sender="server", name="audit")
    assert found["element"]["kind"] == "found" and lost["element"]["kind"] == "lost"
    with pytest.raises(operations.OperationError, match="found"):
        ok(d, "add_message", sender="client", receiver="server", name="x", kind="found")
    with pytest.raises(operations.OperationError, match="sender or a receiver"):
        ok(d, "add_message", name="nothing")
    ok(d, "update_message", id="m1", sender="")
    assert sequence.find(d.interaction, "m1").kind == "found"


def test_message_sorts_are_normalised_and_validated():
    d = basic()
    m = ok(d, "add_message", sender="client", receiver="server", name="x", sort="asynch_call")
    assert m["element"]["sort"] == "asynchCall"
    with pytest.raises(operations.OperationError, match="synchCall"):
        ok(d, "add_message", sender="client", receiver="server", name="x", sort="call")


def test_messages_are_placed_by_before_and_operand():
    d = basic()
    ok(d, "add_message", sender="client", receiver="server", name="first", before="m1", id="m0")
    assert [m.id for m in sequence.messages(d.interaction)][:2] == ["m0", "m1"]
    ok(d, "add_combined_fragment", operator="loop", guard="for each item")
    ok(d, "add_message", sender="server", receiver="db", name="row", operand="op1")
    cf = sequence.find(d.interaction, "cf1")
    assert [f.id for f in cf.operands[0].fragments] == ["m4"]
    with pytest.raises(operations.OperationError, match="not in operand"):
        ok(d, "add_message", sender="server", receiver="db", name="x", operand="op1", before="m1")


# ---- combined fragments ------------------------------------------------------------------------


def test_combined_fragment_wraps_contents_and_validates_operands():
    d = basic()
    result = ok(d, "add_combined_fragment", operator="alt", guard="ok", contents=["m2", "m3"])
    assert result["element"]["covered"] == ["client", "server", "db"]
    assert [f.id for f in d.interaction.fragments] == ["m1", "cf1"]
    ok(d, "add_interaction_operand", fragment="cf1", guard="else")
    ok(d, "add_message", sender="server", receiver="client", name="error", operand="op2")
    ok(d, "add_combined_fragment", operator="loop", guard="1..3")
    with pytest.raises(operations.OperationError, match="exactly one operand"):
        ok(d, "add_interaction_operand", fragment="cf2")
    with pytest.raises(operations.OperationError, match="exactly one operand"):
        ok(d, "update_combined_fragment", id="cf1", operator="opt")
    ok(d, "update_interaction_operand", id="op1", guard="")
    assert sequence.find(d.interaction, "op1").guard is None
    with pytest.raises(operations.OperationError, match="last operand"):
        ok(d, "remove_fragment", id="op3")


def test_every_interaction_operator_is_accepted():
    for operator in sequence.INTERACTION_OPERATORS:
        d = basic()
        ok(d, "add_combined_fragment", operator=operator.upper(), contents=["m1"])
        assert d.integrity_errors() == []


def test_ignore_and_consider_list_messages():
    d = basic()
    ok(d, "add_combined_fragment", operator="ignore", messages=["ping"], contents=["m1"])
    with pytest.raises(operations.OperationError, match="ignore and consider"):
        ok(d, "add_combined_fragment", operator="alt", messages=["ping"])


def test_move_fragment_and_cycles():
    d = basic()
    ok(d, "add_combined_fragment", operator="par", contents=["m2"])
    ok(d, "add_combined_fragment", operator="opt", operand="op1")
    with pytest.raises(operations.OperationError, match="inside itself"):
        ok(d, "move_fragment", id="cf1", operand="op2")
    ok(d, "move_fragment", id="m1", operand="op2")
    ok(d, "move_fragment", id="m1")
    assert d.interaction.fragments[-1].id == "m1"


# ---- executions, destruction, interaction uses ---------------------------------------------------


def test_execution_specifications_follow_message_order():
    d = basic()
    ok(d, "add_execution_specification", lifeline="server", start="m1", finish="m3")
    with pytest.raises(operations.OperationError, match="does not involve"):
        ok(d, "add_execution_specification", lifeline="db", start="m1")
    with pytest.raises(operations.OperationError, match="before it starts"):
        ok(d, "add_execution_specification", lifeline="server", start="m3", finish="m1")
    with pytest.raises(operations.OperationError, match="before it starts"):
        ok(d, "move_fragment", id="m1")  # m1 would come after m3
    assert sequence.find(d.interaction, "ex1") is not None
    result = ok(d, "remove_fragment", id="m3")
    assert result["removed"] == ["m3", "ex1"]


def test_destruction_and_interaction_use():
    d = basic()
    ok(d, "add_destruction_occurrence_specification", lifeline="db")
    with pytest.raises(operations.OperationError, match="already destroyed"):
        ok(d, "add_destruction_occurrence_specification", lifeline="db")
    use = ok(d, "add_interaction_use", refers_to="Authenticate", covered=["client", "server"])
    assert use["element"]["covered"] == ["client", "server"]
    ok(d, "update_interaction_use", id="ref1", refers_to="Login")
    with pytest.raises(operations.OperationError, match="Unknown lifeline"):
        ok(d, "update_interaction_use", id="ref1", covered=["ghost"])


def test_rejected_operations_change_nothing():
    d = basic()
    before = d.model_dump()
    for op, args in [
        ("add_message", {"sender": "ghost", "receiver": "server", "name": "x"}),
        ("add_combined_fragment", {"operator": "maybe"}),
        ("move_fragment", {"id": "m9"}),
        ("remove_fragment", {"id": "nope"}),
        ("add_node", {"name": "x"}),
        (
            "apply_batch",
            {
                "operations": [
                    {"op": "add_lifeline", "args": {"name": "Z"}},
                    {"op": "remove_fragment", "args": {"id": "?"}},
                ]
            },
        ),
    ]:
        with pytest.raises(operations.OperationError):
            operations.apply(d, op, args)
        assert d.model_dump() == before


def test_touched_ids_exist_and_batches_report_them():
    d = basic()
    outcome = operations.apply(
        d,
        "apply_batch",
        {
            "operations": [
                {"op": "add_lifeline", "args": {"name": "Cache"}},
                {"op": "add_message", "args": {"sender": "server", "receiver": "cache", "name": "get"}},
            ]
        },
    )
    assert outcome["touched"] and all(d.has_element(t) for t in outcome["touched"])
    assert "cache" in outcome["touched"]


# ---- summary, tools, prompt ---------------------------------------------------------------------


def test_summary_lists_lifelines_and_fragments():
    d = basic()
    ok(d, "add_combined_fragment", operator="alt", guard="ok", contents=["m3"])
    text = d.summary()
    assert "sequence diagram" in text.splitlines()[0]
    assert "[client] Client «actor»" in text and "[server] Server : OrderService" in text
    assert "[m1] client -> server: order(id) (synchCall)" in text
    assert "[cf1] alt covering client, server, db" in text and "operand [op1] [ok]" in text


def test_tools_use_uml_terms():
    tools = {t.name: t for t in agent.build_tools(kinds_for("sequence"), "sequence")}
    editing = set(operations.TYPE_OPERATIONS["sequence"]) - set(operations.MODEL_OPERATIONS)
    editing -= set(operations.RELATION_OPERATIONS)
    assert set(tools) == editing | {"get_diagram"}
    assert tools["add_message"].parameters["properties"]["sort"]["enum"] == list(sequence.MESSAGE_SORTS)
    ops_enum = tools["add_combined_fragment"].parameters["properties"]["operator"]["enum"]
    assert ops_enum == list(sequence.INTERACTION_OPERATORS)
    assert "CombinedFragment" in tools["add_combined_fragment"].description
    prompt = agent.build_system_prompt(basic())
    assert "UML 2.5.1" in prompt and "InteractionOperand" in prompt and "[m1]" in prompt


# ---- rendering ---------------------------------------------------------------------------------------


def test_every_element_is_a_group_with_its_id():
    d = basic()
    ok(d, "add_combined_fragment", operator="alt", guard="ok", contents=["m2"])
    ok(d, "add_interaction_operand", fragment="cf1", guard="else")
    ok(d, "add_interaction_use", refers_to="Audit")
    ok(d, "add_execution_specification", lifeline="server", start="m1", finish="m3")
    ok(d, "add_destruction_occurrence_specification", lifeline="db")
    root = svg_of(d)
    found = groups(root)
    assert set(found) == set(sequence.element_ids(d.interaction))
    kinds = {i: g.get("data-element") for i, g in found.items()}
    assert kinds["client"] == "lifeline" and kinds["m1"] == "message" and kinds["cf1"] == "combined_fragment"
    assert kinds["op2"] == "interaction_operand" and kinds["ref1"] == "interaction_use"
    assert kinds["ex1"] == "execution_specification" and kinds["x1"] == "destruction_occurrence_specification"


def test_message_notation():
    d = basic()
    for sort in ("asynchCall", "createMessage", "deleteMessage"):
        ok(d, "add_message", sender="client", receiver="db", name=sort, sort=sort)
    ok(d, "add_message", receiver="server", name="found")
    ok(d, "add_message", sender="server", name="lost")
    ok(d, "add_message", sender="server", receiver="server", name="self")
    root = svg_of(d)
    msgs = groups(root, "message")
    cls = {i: msgs[i].get("class") for i in msgs}
    assert "sort-synchCall" in cls["m1"] and "sort-reply" in cls["m3"] and "kind-found" in cls["m7"]
    lines = {i: msgs[i].find(f"{SVG}*[@class='msg']") for i in msgs}
    assert lines["m1"].get("marker-end") == "url(#arcviz-seq-filled)"
    assert lines["m3"].get("marker-end") == "url(#arcviz-seq-open)" and lines["m3"].get("stroke-dasharray")
    assert lines["m4"].get("marker-end") == "url(#arcviz-seq-open)" and not lines["m4"].get("stroke-dasharray")
    assert lines["m5"].get("stroke-dasharray")  # createMessage is dashed
    assert msgs["m6"].find(f"{SVG}path[@class='destroy']") is not None  # deleteMessage ends in an X
    assert msgs["m7"].find(f"{SVG}circle") is not None and msgs["m8"].find(f"{SVG}circle") is not None
    assert lines["m9"].tag == f"{SVG}path"  # self message loops
    heads = groups(root, "lifeline")
    assert heads["client"].find(f"{SVG}*[@class='actor']") is not None
    assert "Server : OrderService" in "".join(heads["server"].itertext())


def test_created_lifeline_starts_at_its_create_message():
    d = basic()
    ok(d, "add_lifeline", name="Order")
    ok(d, "add_message", sender="server", receiver="order", name="new", sort="createMessage")
    root = svg_of(d)
    head = groups(root, "lifeline")["order"].find(f"{SVG}rect")
    other = groups(root, "lifeline")["server"].find(f"{SVG}rect")
    assert float(head.get("y")) > float(other.get("y")) + 40


def test_combined_fragment_notation():
    d = basic()
    ok(d, "add_combined_fragment", operator="consider", messages=["ok"], contents=["m3"])
    ok(d, "add_combined_fragment", operator="alt", guard="x > 1", contents=["m1"])
    ok(d, "add_interaction_operand", fragment="cf2", guard="else")
    root = svg_of(d)
    text = "".join(groups(root, "combined_fragment")["cf1"].itertext())
    assert "consider {ok}" in text
    alt = groups(root, "combined_fragment")["cf2"]
    assert "alt" in "".join(alt.itertext())
    assert "[else]" in "".join(groups(root, "interaction_operand")["op3"].itertext())
    assert alt.find(f"{SVG}line[@class='separator']") is not None


def test_hostile_text_is_escaped():
    hostile = "<img src=x onerror=\"alert(1)\"> & 'q' ]]>"
    d = seq()
    ok(d, "add_lifeline", name=hostile, type=hostile, id='a"b')
    ok(d, "add_lifeline", name="b")
    ok(d, "add_message", sender='a"b', receiver="b", name=hostile)
    ok(d, "add_combined_fragment", operator="alt", guard=hostile, contents=["m1"])
    ok(d, "add_interaction_use", refers_to=hostile)
    source = sequence_svg.render(d)
    assert "<img" not in source and 'onerror="' not in source
    root = ET.fromstring(source)  # well-formed
    text = "".join(root.itertext())
    assert hostile in text and f"[{hostile}]" in text and f"{hostile} : {hostile}" in text
    assert 'a"b' in groups(root)


def test_highlight_and_marks_set_classes():
    d = basic()
    root = svg_of(d, highlight=["m1", "client"], marks={"m2": "added", "db": "removed"})
    found = groups(root)
    assert "changed" in found["m1"].get("class").split() and "changed" in found["client"].get("class").split()
    assert "added" in found["m2"].get("class").split() and "removed" in found["db"].get("class").split()


def test_renderer_payload_and_view_box():
    d = basic()
    payload = render.payload(d, highlight=["m1"], focus="client")
    assert payload["renderer"] == "svg" and payload["mermaid"] is None and payload["mermaid_ids"] == {}
    root = ET.fromstring(payload["svg"])
    x, y, w, h = (float(v) for v in root.get("viewBox").split())
    assert w > 300 and h > 100 and root.get("width") == f"{w:g}"


# ---- exchange, proposal, API ---------------------------------------------------------------------


def test_json_round_trip_and_import_rules():
    d = TEMPLATES["checkout"]("c", "C")
    document = exchange.export_document(d)
    assert document["schema_version"] == 2
    assert exchange.parse_document(document) == d
    document["diagram"]["interaction"]["fragments"][0]["receiver"] = "ghost"
    with pytest.raises(exchange.ExchangeError, match="ghost"):
        exchange.parse_document(document)
    assert "L_kunde -> L_shop : checkout(warenkorb)" in exchange.export_plantuml(d).splitlines()  # #107


def test_proposal_diff_lists_interaction_elements():
    before = basic()
    after = before.model_copy(deep=True)
    ok(after, "add_message", sender="db", receiver="server", name="rows", sort="reply")
    ok(after, "update_message", id="m1", name="order(42)")
    ok(after, "remove_fragment", id="m2")
    diff = proposal.diagram_diff(before, after)
    elements = diff["elements"]
    assert [e["id"] for e in elements["added"]] == ["m4"] and [e["id"] for e in elements["removed"]] == ["m2"]
    assert [e["id"] for e in elements["changed"]] == ["m1"] and "order(42)" in elements["changed"][0]["description"]


def test_template_and_api(tmp_path):
    assert TEMPLATE_TYPES["checkout"] == "sequence"
    d = TEMPLATES["checkout"]("c", "C")
    assert d.integrity_errors() == []
    assert {
        f.operator for f, _, _ in sequence.walk(d.interaction.fragments) if isinstance(f, sequence.CombinedFragment)
    } >= {"alt", "loop"}
    client = TestClient(create_app(store=FileStore(tmp_path)))
    cfg = client.get("/api/config").json()["diagram_types"]["sequence"]
    assert cfg["renderer"] == "svg" and cfg["message_sorts"] == list(sequence.MESSAGE_SORTS)
    assert cfg["interaction_operators"] == list(sequence.INTERACTION_OPERATORS)
    assert not any(cfg["features"][f] for f in ("focus", "direction")) and cfg["features"]["plantuml"]
    assert cfg["features"]["compare"]
    created = client.post("/api/diagrams", json={"title": "Checkout", "template": "checkout"}).json()
    assert created["renderer"] == "svg" and created["svg"].startswith("<svg") and created["mermaid"] is None
    diagram_id = created["diagram"]["id"]
    op = client.post(
        f"/api/diagrams/{diagram_id}/ops", json={"op": "update_message", "args": {"id": "m1", "name": "go"}}
    )
    assert (
        op.status_code == 200
        and "go" in op.json()["svg"]
        and 'class="message sort-synchCall changed' in op.json()["svg"]
    )
    assert client.get(f"/api/diagrams/{diagram_id}/mermaid").status_code == 400
    assert client.get(f"/api/diagrams/{diagram_id}/render").json()["renderer"] == "svg"
    compare = client.get(f"/api/diagrams/{diagram_id}/compare?base=1")
    assert compare.status_code == 200 and compare.json()["diff"]["changed_elements"] == ["m1"]  # #108
    assert (
        client.post(
            f"/api/diagrams/{diagram_id}/ops", json={"op": "set_direction", "args": {"direction": "LR"}}
        ).status_code
        == 400
    )


def test_stored_values_cannot_break_out_of_attributes():
    d = basic()
    d.interaction.fragments[0].sort = '"><script>alert(1)</script>'  # bypasses validation, as a hand-edited file could
    source = sequence_svg.render(d)
    assert "<script" not in source
    ET.fromstring(source)


def test_git_mirror_file_name_follows_the_renderer():
    assert render.file_name(basic()) == "diagram.svg"
    assert render.file_name(Diagram(id="d", title="D")) == "diagram.mmd"


def test_review_accepts_interaction_ids_and_checks_run_the_sequence_rules(tmp_path):
    from arcviz import review, rules

    d = basic()
    data = {
        "summary": "ok",
        "findings": [
            {"category": "risk", "severity": "high", "message": "m", "elements": ["m1", "client", "ghost"]},
        ],
    }
    _, findings, _ = review.parse_review(data, d)
    assert findings[0].elements == ["m1", "client"]
    assert rules.diagram_type(d) == "sequence" and rules.run_checks(d) == []
    client = TestClient(create_app(store=FileStore(tmp_path)))
    created = client.post("/api/diagrams", json={"title": "C", "template": "checkout"}).json()
    checks = client.get(f"/api/diagrams/{created['diagram']['id']}/checks").json()
    assert checks["diagram_type"] == "sequence" and checks["findings"] == []  # the template satisfies them (#123)
    assert [r["id"] for r in checks["rules"]] == [r.id for r in rules.rules_for("sequence")]


def test_lifelines_are_not_used_after_their_destruction():
    d = basic()
    ok(d, "add_destruction_occurrence_specification", lifeline="db")
    with pytest.raises(operations.OperationError, match="after its destruction"):
        ok(d, "add_message", sender="server", receiver="db", name="late")
    with pytest.raises(operations.OperationError, match="after its destruction"):
        ok(d, "move_fragment", id="x1", before="m2")  # m2 (server -> db) would follow the destruction
    with pytest.raises(operations.OperationError, match="after its destruction"):
        ok(d, "add_destruction_occurrence_specification", lifeline="server", before="m3")
    ok(d, "remove_fragment", id="x1")
    ok(d, "add_message", sender="client", receiver="db", name="drop", sort="deleteMessage")
    with pytest.raises(operations.OperationError, match="after its destruction"):
        ok(d, "add_message", sender="db", receiver="client", name="ghost")
    with pytest.raises(operations.OperationError, match="destroyed twice"):
        ok(d, "add_destruction_occurrence_specification", lifeline="db")
    assert d.integrity_errors() == []


def test_frames_are_wide_enough_for_every_guard():
    d = basic()
    ok(d, "add_combined_fragment", operator="alt", guard="a", covered=["client"], contents=["m1"])
    long_guard = "a very long guard condition that is wider than the covered lifelines"
    ok(d, "add_interaction_operand", fragment="cf1", guard=long_guard)
    frame = groups(svg_of(d), "combined_fragment")["cf1"].find(f"{SVG}rect[@class='frame']")
    width = float(frame.get("width"))
    assert width >= sequence_svg.text_width(f"[{long_guard}]") + 10


def test_review_input_describes_the_interaction():
    from arcviz import review

    text = review.review_input(basic(), [])
    assert "Diagram type: sequence" in text and "[m1] client -> server: order(id) (synchCall)" in text
