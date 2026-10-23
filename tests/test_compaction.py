"""Chat compaction (spec: docs/architecture/specs/chat-compaction.md)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz.agent import SUMMARY_SYSTEM_PROMPT, DiagramAgent
from arcviz.llm.base import ModelReply
from arcviz.server import create_app
from arcviz.services import DiagramService
from tests.conftest import ScriptedProvider


def turn(i: int, ops: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": f"request {i}"},
        {"role": "assistant", "content": f"reply {i}", "operations": ops or [], "version": None, "error": None},
    ]


def reply(text: str) -> ModelReply:
    return ModelReply(text=text, tool_calls=[], stop_reason="end_turn")


def seed(store, n_turns: int, diagram_id: str = "demo") -> None:
    entries: list[dict[str, Any]] = []
    for i in range(1, n_turns + 1):
        entries += turn(i, [{"op": "add_node", "arguments": {"name": f"svc-{i}", "kind": "service"}, "result": {}}])
    store.append_chat(diagram_id, entries)


# ---- LLMProvider.complete_text -----------------------------------------------------


def test_complete_text_calls_model_without_tools():
    provider = ScriptedProvider([reply("the summary")])
    assert provider.complete_text("sys", "text") == "the summary"
    req = provider.requests[0]
    assert req["system"] == "sys" and req["tools"] == []
    assert req["messages"] == [{"role": "user", "content": "text"}]


def test_complete_text_rejects_refusal_and_empty_reply():
    with pytest.raises(RuntimeError):
        ScriptedProvider([ModelReply(text="", tool_calls=[], stop_reason="refusal")]).complete_text("s", "t")
    with pytest.raises(RuntimeError, match="max_tokens"):
        ScriptedProvider([ModelReply(text="partial sum", tool_calls=[], stop_reason="max_tokens")]).complete_text(
            "s", "t"
        )
    with pytest.raises(RuntimeError):
        ScriptedProvider([reply("")]).complete_text("s", "t")


# ---- fold rules ----------------------------------------------------------------------


def test_below_threshold_nothing_happens(store, demo):
    seed(store, 2)  # 4 entries
    provider = ScriptedProvider([reply("ok")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=6, keep=4, background=False)
    list(agent.chat("demo", "request 3"))  # 6 entries: not > 6
    assert len(provider.requests) == 1
    assert [e["role"] for e in store.load_chat("demo")] == ["user", "assistant"] * 3


def test_turn_over_threshold_compacts_and_keeps_tail_starting_with_user(store, demo):
    seed(store, 3)  # seq 1..6
    provider = ScriptedProvider([reply("ok"), reply("Earlier: svc-1 and svc-2 were added.")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=6, keep=4, background=False)
    events = list(agent.chat("demo", "request 4"))  # 8 entries > 6 -> compact
    assert events[-1]["type"] == "final" and events[-1]["reply"] == "ok"

    chat = store.load_chat("demo")
    assert [(e["seq"], e["role"]) for e in chat] == [
        (4, "summary"),
        (5, "user"),
        (6, "assistant"),
        (7, "user"),
        (8, "assistant"),
    ]
    summary = chat[0]
    assert summary["content"] == "Earlier: svc-1 and svc-2 were added."
    assert summary["covers"] == {"from_seq": 1, "to_seq": 4, "turns": 2}
    assert summary["created_at"]

    summary_request = provider.requests[1]
    assert summary_request["tools"] == [] and summary_request["system"] == SUMMARY_SYSTEM_PROMPT
    prompt = summary_request["messages"][0]["content"]
    assert "user: request 1" in prompt and "assistant: reply 2" in prompt and "request 3" not in prompt
    assert "[add_node svc-1]" in prompt  # compact operation list
    assert "Previous summary" not in prompt
    # versions are untouched
    assert [v.version for v in store.list_versions("demo")] == [1]


def test_tail_boundary_moves_back_to_a_user_entry(store, demo):
    seed(store, 3)
    store.append_chat("demo", [{"role": "user", "content": "dangling"}])  # seq 7: n=7, keep=2 -> index 5 is assistant
    provider = ScriptedProvider([reply("s")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=0, keep=2, background=False)
    assert agent.compact("demo") is True
    chat = store.load_chat("demo")
    assert [(e["seq"], e["role"]) for e in chat] == [(4, "summary"), (5, "user"), (6, "assistant"), (7, "user")]


def test_compaction_is_cumulative(store, demo):
    seed(store, 3)
    provider = ScriptedProvider([reply("first summary"), reply("second summary")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=0, keep=2, background=False)
    assert agent.compact("demo")
    assert store.load_chat("demo")[0]["covers"]["turns"] == 2
    store.append_chat("demo", turn(4) + turn(5))
    assert agent.compact("demo")
    prompt = provider.requests[1]["messages"][0]["content"]
    assert prompt.startswith("Previous summary:\nfirst summary")
    assert "request 3" in prompt and "request 4" in prompt and "request 5" not in prompt
    chat = store.load_chat("demo")
    assert chat[0]["content"] == "second summary"
    assert chat[0]["covers"] == {"from_seq": 1, "to_seq": 8, "turns": 4}
    assert [(e["seq"], e["role"]) for e in chat[1:]] == [(9, "user"), (10, "assistant")]


def test_prompt_cap_shortens_the_fold_instead_of_dropping_entries(store, demo, monkeypatch):
    import arcviz.agent as agent_module

    seed(store, 6)  # seq 1..12, each turn ~60 chars of prompt
    monkeypatch.setattr(agent_module, "SUMMARY_INPUT_LIMIT", 150)
    provider = ScriptedProvider([reply("s")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=0, keep=2, background=False)
    assert agent.compact("demo") is True
    prompt = provider.requests[0]["messages"][0]["content"]
    assert len(prompt) <= 150
    assert "request 1" in prompt  # the oldest entries are what gets summarised ...
    chat = store.load_chat("demo")
    assert chat[0]["role"] == "summary"
    # ... and everything not in the prompt is still in the transcript, nothing was deleted unsummarised
    folded_to = chat[0]["covers"]["to_seq"]
    assert folded_to < 10
    assert [e["seq"] for e in chat[1:]] == list(range(folded_to + 1, 13))
    assert all(f"request {i}" not in prompt for i in range((folded_to // 2) + 1, 7))


def test_truncated_summary_leaves_chat_unchanged(store, demo):
    seed(store, 3)
    provider = ScriptedProvider([ModelReply(text="half a su", tool_calls=[], stop_reason="max_tokens")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=0, keep=2, background=False)
    with pytest.raises(RuntimeError):
        agent.compact("demo")
    assert len(store.load_chat("demo")) == 6


def test_nothing_to_fold_returns_false(store, demo):
    provider = ScriptedProvider([])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=0, keep=4, background=False)
    assert agent.compact("demo") is False  # empty
    seed(store, 2)
    assert agent.compact("demo") is False  # everything fits into the tail
    store.append_chat("demo", [{"role": "user", "content": "x"}])
    assert agent.compact("demo") is False  # fold would hold no complete turn
    assert provider.requests == []


def test_history_starts_with_summary_and_acknowledgement(store, demo):
    seed(store, 3)
    provider = ScriptedProvider([reply("s"), reply("ok")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=0, keep=2, background=False, history_turns=30)
    agent.compact("demo")
    list(agent.chat("demo", "request 4"))
    messages = provider.requests[1]["messages"]
    assert messages[0] == {"role": "user", "content": "Summary of the earlier conversation:\ns"}
    assert messages[1] == {"role": "assistant", "content": "Understood."}
    assert [m["content"] for m in messages[2:]] == ["request 3", "reply 3", "request 4"]


class FailingSummaryProvider(ScriptedProvider):
    def _complete(self, system, messages, tools):
        if system == SUMMARY_SYSTEM_PROMPT:
            raise RuntimeError("boom")
        return super()._complete(system, messages, tools)


def test_failed_compaction_leaves_chat_and_turn_intact(store, demo, caplog):
    seed(store, 3)
    provider = FailingSummaryProvider([reply("ok")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=6, keep=4, background=False)
    events = list(agent.chat("demo", "request 4"))
    assert events[-1]["type"] == "final" and events[-1]["error"] is None
    chat = store.load_chat("demo")
    assert len(chat) == 8 and all(e["role"] != "summary" for e in chat)
    assert any("compaction" in r.getMessage().lower() and "demo" in r.getMessage() for r in caplog.records)


def test_clearing_the_chat_during_compaction_does_not_resurrect_it(store, demo):
    seed(store, 3)

    class ClearingProvider(ScriptedProvider):
        def _complete(self, system, messages, tools):
            if system == SUMMARY_SYSTEM_PROMPT:
                store.clear_chat("demo")  # the user presses "Chat leeren" while the summary is being written
            return super()._complete(system, messages, tools)

    provider = ClearingProvider([reply("ok"), reply("s")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=6, keep=4, background=False)
    list(agent.chat("demo", "request 4"))
    assert store.load_chat("demo") == []  # the stale summary is not written back


def test_a_stale_summary_does_not_replace_a_repopulated_chat(store, demo):
    """#67: clear and new messages with the same seq numbers while the old summary is being written."""
    seed(store, 3)

    class ClearingProvider(ScriptedProvider):
        def _complete(self, system, messages, tools):
            if system == SUMMARY_SYSTEM_PROMPT:
                store.clear_chat("demo")
                seed(store, 4)  # fresh entries reuse seq 1..8, including the fold's last seq
            return super()._complete(system, messages, tools)

    provider = ClearingProvider([reply("ok"), reply("OLD SUMMARY")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=6, keep=4, background=False)
    list(agent.chat("demo", "request 4"))
    chat = store.load_chat("demo")
    assert [e["content"] for e in chat] == [e["content"] for e in sum((turn(i) for i in range(1, 5)), [])]
    assert all(e["role"] != "summary" for e in chat)


def test_background_compaction_runs_on_the_agent_executor(store, demo):
    seed(store, 3)
    provider = ScriptedProvider([reply("ok"), reply("s")])
    agent = DiagramAgent(provider, DiagramService(store), compact_after=6, keep=4, background=True)
    list(agent.chat("demo", "request 4"))
    agent.drain()
    assert store.load_chat("demo")[0]["role"] == "summary"


# ---- server -----------------------------------------------------------------------


def test_manual_compaction_endpoint_and_clear(store, demo, monkeypatch):
    monkeypatch.setenv("ARCVIZ_CHAT_COMPACT_AFTER", "0")
    monkeypatch.setenv("ARCVIZ_CHAT_KEEP", "2")
    seed(store, 3)
    client = TestClient(create_app(store=store, provider=ScriptedProvider([reply("s")])))

    r = client.post("/api/diagrams/demo/chat/compact")
    assert r.status_code == 200
    body = r.json()
    assert body["compacted"] is True
    assert [e["role"] for e in body["chat"]] == ["summary", "user", "assistant"]
    assert client.get("/api/diagrams/demo").json()["chat"][0]["role"] == "summary"

    r = client.post("/api/diagrams/demo/chat/compact")
    assert r.json()["compacted"] is False

    assert client.delete("/api/diagrams/demo/chat").status_code == 204
    assert client.get("/api/diagrams/demo").json()["chat"] == []
    assert client.post("/api/diagrams/nope/chat/compact").status_code == 404


def test_server_reads_compaction_config_from_env(store, demo, monkeypatch):
    monkeypatch.setenv("ARCVIZ_CHAT_COMPACT_AFTER", "4")
    monkeypatch.setenv("ARCVIZ_CHAT_KEEP", "2")
    seed(store, 2)  # 4 entries
    client = TestClient(create_app(store=store, provider=ScriptedProvider([reply("ok"), reply("s")])))
    r = client.post("/api/diagrams/demo/chat", json={"message": "request 3"})
    assert r.status_code == 200
    # the agent compacts in the background; drain via the agent the app created
    client.app.state.agent.drain()
    chat = client.get("/api/diagrams/demo").json()["chat"]
    assert [(e["seq"], e["role"]) for e in chat] == [(4, "summary"), (5, "user"), (6, "assistant")]
