"""The eval harness and case file, offline (spec: docs/architecture/specs/agent-evals.md).

These tests never call a real provider; the eval run itself is `make eval`.
"""

from __future__ import annotations

import json

import pytest

from arcviz import operations, prompts
from arcviz.llm.base import ModelReply
from arcviz.model import DIAGRAM_TYPES
from arcviz.templates import TEMPLATES
from tests.conftest import ScriptedProvider, call
from tests.evals import run as cli
from tests.evals.harness import CASE_KEYS, EXPECT_KEYS, check_case, load_cases, run_case, start_diagram

CASES = load_cases()


def test_case_file_is_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) >= 10 and len(ids) == len(set(ids))
    for case in CASES:
        assert case["template"] in TEMPLATES and case["request"].strip()
        assert set(case["expect"]) <= EXPECT_KEYS, case["id"]
        assert set(case) <= CASE_KEYS, case["id"]
        diagram = start_diagram(case)  # template + setup operations apply cleanly
        assert diagram.type == case.get("type", diagram.type), case["id"]


def test_cases_cover_design_help_for_every_diagram_type():
    """Each type has a design-help case on an empty diagram (spec: assistant-prompts.md)."""
    covered = {start_diagram(c).type for c in CASES if c["id"].startswith("design-help-")}
    assert covered == set(DIAGRAM_TYPES)


def test_start_diagram_honours_the_case_type():
    assert start_diagram({"template": "empty", "type": "sequence"}).type == "sequence"
    assert start_diagram({"template": "empty"}).type == "deployment"
    with pytest.raises(ValueError):
        start_diagram({"template": "demo", "type": "activity"})


def case(case_id: str) -> dict:
    return next(c for c in CASES if c["id"] == case_id)


def solve_vnet() -> ScriptedProvider:
    return ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[
                    call(
                        "add_node",
                        "c1",
                        name="prod-vnet",
                        kind="vnet",
                        parent="prod-deployment",
                        properties={"cidr": "10.0.0.0/16"},
                    )
                ],
                stop_reason="tool_use",
            ),
            ModelReply(
                text="",
                tool_calls=[
                    call("move_node", "c2", id="service-a", parent="prod-vnet"),
                    call("move_node", "c3", id="Service-B", parent="prod-vnet"),
                ],
                stop_reason="tool_use",
            ),
            ModelReply(
                text="Erledigt.",
                tool_calls=[],
                stop_reason="end_turn",
                usage={"input_tokens": 100, "output_tokens": 10},
            ),
        ]
    )


def test_run_case_scores_a_correct_turn_fully():
    result = run_case(solve_vnet(), case("vnet-move"))
    assert result.score == 1.0 and result.passed, [c for c in result.checks if not c["ok"]]
    assert (
        result.operations == 3 and result.usage == {"input_tokens": 100, "output_tokens": 10} and result.error is None
    )


def test_run_case_scores_a_useless_turn_partially():
    provider = ScriptedProvider([ModelReply(text="Mache ich gleich.", tool_calls=[], stop_reason="end_turn")])
    result = run_case(provider, case("vnet-move"))
    assert 0 < result.score < 1 and not result.passed
    failed = [c["name"] for c in result.checks if not c["ok"]]
    assert "node prod-vnet exists" in failed and "service-a parent = prod-vnet" in failed
    assert "service-c parent = prod-deployment" not in failed  # untouched expectations still pass


def test_provider_error_is_a_failed_check():
    provider = ScriptedProvider([ModelReply(text="", tool_calls=[], stop_reason="refusal")])
    result = run_case(provider, case("rename-diagram"))
    assert result.error == "The model declined to answer this request."
    assert any(c["name"] == "turn finished without error" and not c["ok"] for c in result.checks)


# ---- check_case per expectation kind -------------------------------------------------------------


def scored(expect: dict, mutate=None, reply: str = "", applied: int = 0, error: str | None = None):
    c = {"id": "t", "template": "demo", "request": "x", "expect": expect}
    before = start_diagram(c)
    after = before.model_copy(deep=True)
    if mutate:
        mutate(after)
    return check_case(c, before, after, reply, applied, error)


def test_node_checks_are_loose_about_case_and_names():
    def mutate(d):
        operations.apply(
            d, "update_node", {"id": "orders-db", "name": "Orders Database", "properties": {"engine": "Postgres 16 "}}
        )

    score, checks = scored(
        {"nodes": [{"ref": "Orders Database", "name": "orders database", "properties": {"engine": "postgres 16"}}]},
        mutate,
    )
    assert score == 1.0, checks


def test_parent_null_means_top_level():
    assert scored({"nodes": [{"ref": "idp", "parent": None}]})[0] == 1.0
    assert scored({"nodes": [{"ref": "service-a", "parent": None}]})[0] < 1.0


def test_edges_absent_edges_and_labels():
    score, checks = scored(
        {
            "edges": [
                {"source": "api-gateway", "target": "service-a", "label": "HTTPS", "kind": "connection"},
                {"source": "service-c", "target": "orders-db"},
            ],
            "absent_edges": [{"source": "events", "target": "service-b"}],
        }
    )
    outcome = {c["name"]: c["ok"] for c in checks}
    assert outcome["edge api-gateway → service-a exists"] and outcome["edge api-gateway → service-a label ~ HTTPS"]
    assert not outcome["edge service-c → orders-db exists"]
    assert not outcome["no edge events ↔ service-b"]  # either direction counts
    assert score == pytest.approx(4 / 6)


def test_unchanged_title_reply_and_operation_limit():
    def rename(d):
        operations.apply(d, "rename_diagram", {"title": " shop produktion"})

    assert scored({"title": "Shop Produktion"}, rename)[0] == 1.0
    assert scored({"unchanged": True}, rename)[0] < 1.0
    assert scored({"unchanged": True})[0] == 1.0
    assert scored({"reply_contains": ["Service-B", "service-c"]}, reply="service-b und SERVICE-C")[0] == 1.0
    assert scored({"reply_contains_any": ["audit-db", "orders-db"]}, reply="Meinst du Orders-DB?")[0] == 1.0
    assert scored({"reply_contains_any": ["audit-db"]}, reply="Fertig.")[0] < 1.0
    assert scored({"max_operations": 1}, applied=2)[0] < 1.0
    assert scored({"reply_max_chars": 10}, reply=" Nein, dazu nicht. ")[0] < 1.0
    assert scored({"reply_max_chars": 20}, reply=" Nein, dazu nicht. ")[0] == 1.0


def test_cases_cover_the_topic_scope():
    """Off-topic, borderline and disguised requests (spec: topic-scope.md)."""
    ids = {c["id"] for c in CASES}
    assert {"off-topic-recipe", "borderline-nat-gateway", "off-topic-disguised-as-change"} <= ids
    for case_id in ("off-topic-recipe", "off-topic-disguised-as-change", "off-topic-sequence"):
        expect = case(case_id)["expect"]
        assert expect["unchanged"] and expect["max_operations"] == 0 and expect["reply_max_chars"] <= 300
        assert expect["reply_contains_any"], case_id  # an empty or unrelated reply must not pass


# ---- CLI ------------------------------------------------------------------------------------------


def test_cli_prints_a_table_writes_json_and_applies_the_threshold(tmp_path, capsys):
    out = tmp_path / "report.json"
    code = cli.main(["--case", "vnet-move", "--json", str(out), "--min-score", "0.5"], provider_factory=solve_vnet)
    printed = capsys.readouterr().out
    assert code == 0 and "vnet-move" in printed and "mean score 1.00" in printed
    report = json.loads(out.read_text())
    assert report["mean_score"] == 1.0 and report["cases"][0]["id"] == "vnet-move"

    def lazy():
        return ScriptedProvider([])

    assert cli.main(["--case", "vnet-move", "--min-score", "0.9"], provider_factory=lazy) == 1
    assert cli.main(["--case", "nope"], provider_factory=lazy) == 2


def test_cli_reports_a_provider_that_cannot_be_built(capsys):
    def broken():
        raise ValueError("no key")

    assert cli.main([], provider_factory=broken) == 2
    assert "no key" in capsys.readouterr().err


def test_a_project_case_runs_through_the_project_chat():
    """`"scope": "project"` cases (spec: project-chat.md): the model has to open the eval diagram first."""
    provider = ScriptedProvider(
        [
            ModelReply("", [call("open_diagram", "c1", diagram="Shop deployment")], "tool_use"),
            ModelReply(
                "",
                [
                    call("add_node", "c2", name="payment", kind="service", parent="prod-deployment"),
                    call("add_edge", "c3", source="service-a", target="payment"),
                ],
                "tool_use",
            ),
            ModelReply("Payment ergänzt.", [], "end_turn"),
        ]
    )
    result = run_case(provider, case("project-open-and-edit"))
    assert result.passed, result.checks
    assert "Checkout" in provider.requests[0]["system"]  # the project structure, with the other diagram


def test_a_project_case_can_expect_a_created_diagram():
    provider = ScriptedProvider(
        [
            ModelReply("", [call("create_diagram", "c1", title="Bestellprozess", type="activity")], "tool_use"),
            ModelReply("", [call("add_node", "c2", name="Start", kind="initial_node")], "tool_use"),
            ModelReply("Angelegt.", [], "end_turn"),
        ]
    )
    result = run_case(provider, case("project-create-diagram"))
    assert result.passed, result.checks
    failing = run_case(ScriptedProvider([ModelReply("Nein.", [], "end_turn")]), case("project-create-diagram"))
    assert not failing.passed and any(
        c["name"] == "diagram Bestellprozess created" and not c["ok"] for c in failing.checks
    )


def test_a_project_case_can_expect_an_off_topic_refusal():
    reply = ModelReply("Dazu kann ich nicht helfen – soll ich am Projekt weiterarbeiten?", [], "end_turn")
    provider = ScriptedProvider([reply])
    result = run_case(provider, case("project-off-topic"))
    assert result.passed, result.checks
    assert prompts.TOPIC_SCOPE in provider.requests[0]["system"]


# ---- model relations (spec: model-relations.md, ADR-0140) --------------------------------------------------------


def test_cases_cover_relations():
    ids = {c["id"] for c in CASES if c.get("model", {})}
    assert {"relation-linked-after-edge", "relation-missing-is-proposed", "relation-created-on-request"} <= ids


def test_a_relation_case_scores_a_linked_edge_fully():
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[call("add_edge", "c1", source="api-gateway", target="service-c")],
                stop_reason="tool_use",
            ),
            ModelReply(
                text="", tool_calls=[call("link_relation", "c2", id="e7", relation="r1")], stop_reason="tool_use"
            ),
            ModelReply(text="Verbunden und mit der Beziehung verknüpft.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    result = run_case(provider, case("relation-linked-after-edge"))
    assert result.passed, result.checks
    assert "link_relation" in provider.requests[0]["tools"] and "Relations:" in provider.requests[0]["system"]


def test_a_relation_case_fails_when_the_agent_creates_a_relation_unasked():
    provider = ScriptedProvider(
        [
            ModelReply(
                text="",
                tool_calls=[
                    call("add_edge", "c1", source="service-c", target="orders-db"),
                    call("create_relation", "c2", kind="communicates", source="svc-c", target="orders-db"),
                ],
                stop_reason="tool_use",
            ),
            ModelReply(text="Erledigt.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    result = run_case(provider, case("relation-missing-is-proposed"))
    failed = {c["name"] for c in result.checks if not c["ok"]}
    assert "0 relation(s) in the model" in failed and not result.passed


# ---- the requirements chat (spec: requirements-workshop.md) --------------------------------------------------------


def test_cases_cover_the_requirements_chat():
    ids = {c["id"] for c in CASES if c.get("scope") == "requirements"}
    assert {"requirements-add-as-said", "requirements-suggest-never-decide", "requirements-off-topic"} <= ids


def test_a_requirements_case_scores_adding_as_said_fully():
    provider = ScriptedProvider(
        [
            ModelReply(
                "",
                [
                    call(
                        "add_requirements",
                        "c1",
                        items=[
                            {"raw": "Der Shop ist rund um die Uhr erreichbar"},
                            {"raw": "Bestellungen lassen sich als CSV exportieren"},
                        ],
                    )
                ],
                "tool_use",
            ),
            ModelReply("Zwei Anforderungen aufgenommen.", [], "end_turn"),
        ]
    )
    result = run_case(provider, case("requirements-add-as-said"))
    assert result.passed, result.checks
    assert "Requirements: 0" in provider.requests[0]["system"]


def test_a_requirements_case_fails_when_the_model_decides_itself():
    provider = ScriptedProvider(
        [
            ModelReply(
                "",
                [call("update_requirement", "c1", requirement="req1", fields={"statement": "Eigenmächtig."})],
                "tool_use",
            ),
            ModelReply("Erledigt.", [], "end_turn"),
        ]
    )
    result = run_case(provider, case("requirements-suggest-never-decide"))
    assert not result.passed
    assert any(c["name"] == "at least 1 suggestion(s) stored" and not c["ok"] for c in result.checks)
    # the tool refused the statement, so nothing is in force either way: people decide
    assert next(c for c in result.checks if c["name"].startswith("no statement or status set"))["ok"]


def test_a_requirements_case_scores_a_suggestion_without_a_decision():
    provider = ScriptedProvider(
        [
            ModelReply(
                "",
                [
                    call(
                        "suggest_refinement",
                        "c1",
                        suggestions=[{"requirement": "req1", "statement": "Das System antwortet in 200 ms."}],
                    )
                ],
                "tool_use",
            ),
            ModelReply("Ich habe einen Vorschlag gespeichert; du übernimmst ihn in der Tabelle.", [], "end_turn"),
        ]
    )
    result = run_case(provider, case("requirements-suggest-never-decide"))
    assert result.passed, result.checks


def test_the_requirements_off_topic_case_expects_a_short_refusal_without_tools():
    result = run_case(
        ScriptedProvider(
            [ModelReply("Dazu kann ich nicht helfen – soll ich bei den Anforderungen weitermachen?", [], "end_turn")]
        ),
        case("requirements-off-topic"),
    )
    assert result.passed, result.checks
