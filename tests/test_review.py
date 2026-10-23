"""LLM architecture review (spec: docs/architecture/specs/llm-review.md)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import operations as ops
from arcviz import review
from arcviz.llm.base import ModelReply, ToolSpec
from arcviz.server import create_app
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider, call

TOOL = ToolSpec("submit_review", "Submit.", {"type": "object", "properties": {}})


def submit(**arguments: Any) -> ModelReply:
    return ModelReply(
        text="",
        tool_calls=[call("submit_review", "r1", **arguments)],
        stop_reason="tool_use",
        usage={"input_tokens": 900, "output_tokens": 120},
    )


GOOD = {
    "summary": "Solide Grundstruktur mit Gateway.",
    "findings": [
        {
            "category": "strength",
            "severity": "info",
            "message": "Alle Zugriffe laufen über das API-Gateway.",
            "elements": ["api-gateway"],
            "edges": ["e1", "e2"],
            "suggestion": "ignored for strengths",
        },
        {
            "category": "risk",
            "severity": "medium",
            "message": "Die Queue events hat keine Dead-Letter-Queue.",
            "elements": ["events"],
            "suggestion": "Füge eine Dead-Letter-Queue für events hinzu.",
        },
    ],
}


# ---- LLMProvider.complete_tool -------------------------------------------------------------


def test_complete_tool_returns_the_arguments_and_usage():
    provider = ScriptedProvider([submit(summary="x", findings=[])])
    args, usage = provider.complete_tool("sys", "input", TOOL)
    assert args == {"summary": "x", "findings": []}
    assert usage == {"input_tokens": 900, "output_tokens": 120}
    [request] = provider.requests
    assert request["tools"] == ["submit_review"] and request["system"] == "sys"
    assert request["messages"] == [{"role": "user", "content": "input"}]


@pytest.mark.parametrize(
    "text",
    ['{"summary": "x", "findings": []}', 'Hier:\n```json\n{"summary": "x", "findings": []}\n```\n'],
)
def test_complete_tool_accepts_a_json_object_in_the_text(text):
    provider = ScriptedProvider([ModelReply(text=text, tool_calls=[], stop_reason="end_turn")])
    assert provider.complete_tool("s", "u", TOOL)[0] == {"summary": "x", "findings": []}


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (ModelReply(text="Sieht gut aus.", tool_calls=[], stop_reason="end_turn"), "did not call submit_review"),
        (ModelReply(text="[1, 2]", tool_calls=[], stop_reason="end_turn"), "did not call submit_review"),
        (ModelReply(text="", tool_calls=[call("other", "x")], stop_reason="tool_use"), "did not call submit_review"),
        (ModelReply(text="", tool_calls=[], stop_reason="refusal"), "declined"),
        (ModelReply(text="", tool_calls=[], stop_reason="max_tokens"), "cut off"),
    ],
)
def test_complete_tool_errors(reply, error):
    with pytest.raises(RuntimeError, match=error):
        ScriptedProvider([reply]).complete_tool("s", "u", TOOL)


# ---- parse_review ----------------------------------------------------------------------------


def test_parse_review_builds_llm_findings():
    summary, findings, dropped = review.parse_review(GOOD, demo_diagram())
    assert summary == "Solide Grundstruktur mit Gateway." and dropped == 0
    assert [f.id for f in findings] == ["llm-1", "llm-2"]
    strength, risk = findings
    assert strength.source == "llm" and strength.rule is None
    assert strength.category == "strength" and strength.severity == "info" and strength.suggestion is None
    assert strength.elements == ["api-gateway"] and strength.edges == ["e1", "e2"]
    assert risk.severity == "medium" and risk.suggestion == "Füge eine Dead-Letter-Queue für events hinzu."
    assert risk.edges == []


def test_parse_review_resolves_names_and_drops_unknown_ids():
    data = {
        "summary": "s",
        "findings": [
            {
                "category": "risk",
                "severity": "high",
                "message": "m",
                "elements": ["Identity Provider", "ORDERS-DB", "ghost", "idp", 7],
                "edges": ["e3", "e99", None],
            }
        ],
    }
    _, [finding], dropped = review.parse_review(data, demo_diagram())
    assert finding.elements == ["idp", "orders-db"] and finding.edges == ["e3"] and dropped == 0


@pytest.mark.parametrize(
    "bad",
    [
        "not an object",
        {"category": "praise", "severity": "info", "message": "m", "elements": []},
        {"category": "risk", "severity": "high", "message": "  ", "elements": []},
        {"category": "risk", "severity": "high", "elements": []},
        {"category": "risk", "severity": "critical", "message": "m", "elements": []},
        {"category": "risk", "severity": "high", "message": 5, "elements": []},
    ],
)
def test_parse_review_drops_invalid_findings(bad):
    data = {"summary": "s", "findings": [bad, GOOD["findings"][1]]}
    _, findings, dropped = review.parse_review(data, demo_diagram())
    assert dropped == 1 and [f.message for f in findings] == ["Die Queue events hat keine Dead-Letter-Queue."]
    assert findings[0].id == "llm-1"


def test_parse_review_normalises_severity_and_tolerates_missing_lists():
    data = {
        "findings": [
            {"category": "strength", "severity": "high", "message": "gut"},
            {"category": "suggestion", "severity": "info", "message": "besser", "elements": "orders-db"},
        ]
    }
    summary, findings, _ = review.parse_review(data, demo_diagram())
    assert summary is None
    assert [(f.category, f.severity) for f in findings] == [("strength", "info"), ("suggestion", "low")]
    assert findings[1].elements == [] and findings[1].suggestion is None


def test_parse_review_caps_and_truncates():
    long = "x" * 5000
    data = {
        "summary": long,
        "findings": [{"category": "risk", "severity": "low", "message": long, "suggestion": long}] * 25,
    }
    summary, findings, dropped = review.parse_review(data, demo_diagram())
    assert len(summary or "") == review.SUMMARY_LIMIT
    assert len(findings) == review.MAX_LLM_FINDINGS and dropped == 25 - review.MAX_LLM_FINDINGS
    assert (
        len(findings[0].message) == review.MESSAGE_LIMIT and len(findings[0].suggestion or "") == review.MESSAGE_LIMIT
    )


@pytest.mark.parametrize("data", [None, [], "text", {"summary": "s"}, {"findings": {"a": 1}}])
def test_parse_review_rejects_output_without_a_findings_list(data):
    with pytest.raises(ValueError):
        review.parse_review(data, demo_diagram())


# ---- review() --------------------------------------------------------------------------------


def test_review_merges_rule_and_llm_findings():
    provider = ScriptedProvider([submit(**GOOD)])
    result = review.review(provider, demo_diagram())
    assert result["diagram_type"] == "deployment"
    assert result["summary"] == "Solide Grundstruktur mit Gateway."
    assert result["llm"] == {
        "ok": True,
        "error": None,
        "dropped": 0,
        "usage": {"input_tokens": 900, "output_tokens": 120},
        "partial": False,
    }
    assert [(f["source"], f["severity"]) for f in result["findings"]] == [
        ("rule", "high"),
        ("llm", "medium"),
        ("llm", "info"),
    ]
    [request] = provider.requests
    assert request["tools"] == ["submit_review"]
    assert request["system"] == review.REVIEW_SYSTEM_PROMPT
    prompt = request["messages"][0]["content"]
    assert "Diagram type: deployment" in prompt
    assert "[orders-db]" in prompt and "[e1] api-gateway -> service-a" in prompt
    assert "Die Datenbank „orders-db“ hat keine Replikation" in prompt  # rule findings are given


def test_review_input_is_complete_when_the_diagram_fits():
    text = review.review_input(demo_diagram(), [])
    assert review.PARTIAL_NOTE not in text and "collapsed" not in text


def test_a_diagram_too_large_for_the_prompt_is_marked_partial(monkeypatch):
    monkeypatch.setattr(review, "REVIEW_CONTEXT_BUDGET", 300)
    d = demo_diagram()
    assert review.is_partial(d)
    text = review.review_input(d, [])
    assert "collapsed" in text and review.PARTIAL_NOTE in text  # the model cannot call get_diagram here
    result = review.review(ScriptedProvider([submit(**GOOD)]), d)
    assert result["llm"]["partial"] is True and result["llm"]["ok"] is True


def test_review_input_without_rule_findings_says_so():
    d = demo_diagram()
    ops.add_edge(d, "orders-db", "service-b", kind="replication")
    assert "Rule findings: none" in review.review_input(d, [])


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (ModelReply(text="Alles gut.", tool_calls=[], stop_reason="end_turn"), "RuntimeError: The model did not"),
        (submit(summary="s", findings="nope"), "ValueError"),
        (ModelReply(text="", tool_calls=[], stop_reason="refusal"), "RuntimeError: The model declined"),
    ],
)
def test_review_degrades_to_rule_findings(reply, error):
    result = review.review(ScriptedProvider([reply]), demo_diagram())
    assert result["llm"]["ok"] is False and result["llm"]["error"].startswith(error)
    assert result["summary"] is None
    assert [f["source"] for f in result["findings"]] == ["rule"]


def test_review_survives_a_provider_exception():
    class Broken(ScriptedProvider):
        def _complete(self, system, messages, tools):
            raise ConnectionError("network down")

    result = review.review(Broken([]), demo_diagram())
    assert result["llm"]["error"] == "ConnectionError: network down"
    assert len(result["findings"]) == 1


# ---- endpoint --------------------------------------------------------------------------------


def test_review_endpoint(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([submit(**GOOD)])))
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    r = client.post("/api/diagrams/demo/review")
    assert r.status_code == 200
    body = r.json()
    assert body["llm"]["ok"] is True and len(body["findings"]) == 3
    assert {f["id"] for f in body["findings"]} == {"database-without-replication:orders-db", "llm-1", "llm-2"}
    assert len(store.list_versions("demo")) == 1 and store.load_chat("demo") == []  # nothing written
    assert client.post("/api/diagrams/nope/review").status_code == 404


def test_review_endpoint_without_provider_is_503(store, monkeypatch):
    client = TestClient(create_app(store=store))
    monkeypatch.setenv("ARCVIZ_PROVIDER", "nonsense")  # after start-up, which would refuse it (ADR-0107)
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    r = client.post("/api/diagrams/demo/review")
    assert r.status_code == 503 and "ARCVIZ_PROVIDER must be one of" in r.json()["detail"]
    assert client.get("/api/diagrams/demo/checks").status_code == 200  # rule checks need no provider


# ---- applying a suggestion (spec: docs/architecture/specs/review-suggestions.md) ----------------


def test_applying_a_rule_suggestion_as_a_proposal_resolves_the_finding(store):
    replica = [
        {"op": "add_node", "args": {"name": "orders-db-replica", "kind": "database", "parent": "prod-deployment"}},
        {"op": "add_edge", "args": {"source": "orders-db", "target": "orders-db-replica", "kind": "replication"}},
    ]
    provider = ScriptedProvider(
        [
            ModelReply(text="", tool_calls=[call("apply_batch", "b1", operations=replica)], stop_reason="tool_use"),
            ModelReply(text="Replik vorgeschlagen.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    client = TestClient(create_app(store=store, provider=provider))
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    [finding] = client.get("/api/diagrams/demo/checks").json()["findings"]
    message = f"Setze diesen Vorschlag aus dem Architektur-Review um: {finding['suggestion']}"

    events = [
        json.loads(line[6:])
        for line in client.post(
            "/api/diagrams/demo/chat", json={"message": message, "mode": "propose"}
        ).text.splitlines()
        if line.startswith("data: ")
    ]
    final = events[-1]
    assert final["type"] == "final" and final["proposal"] is not None
    assert [n["id"] for n in final["proposal"]["diff"]["nodes"]["added"]] == ["orders-db-replica"]
    assert provider.requests[0]["messages"][-1] == {"role": "user", "content": message}
    assert len(store.list_versions("demo")) == 1  # nothing saved before the confirmation
    assert client.get("/api/diagrams/demo/checks").json()["findings"][0]["rule"] == "database-without-replication"

    r = client.post("/api/diagrams/demo/proposal/apply", json={**final["proposal"], "label": "Replik"})
    assert r.status_code == 200 and len(store.list_versions("demo")) == 2
    after = client.get("/api/diagrams/demo/checks").json()["findings"]
    assert {f["rule"] for f in after} == {"replicated-database"}
