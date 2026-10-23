"""Timeouts, retries and the per-turn token budget (spec: docs/architecture/specs/llm-resilience.md)."""

from __future__ import annotations

import importlib
import json
from typing import Any

import anthropic
import openai
import pytest
from fastapi.testclient import TestClient

from arcviz.agent import DiagramAgent
from arcviz.llm import ProviderConfig, create_provider
from arcviz.llm.anthropic_provider import AnthropicProvider
from arcviz.llm.base import ModelReply
from arcviz.llm.openai_provider import OpenAICompatibleProvider
from arcviz.server import create_app
from arcviz.services import DiagramService
from tests.conftest import ScriptedProvider, call

# ---- configuration -----------------------------------------------------------------------------


def test_from_env_reads_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("ARCVIZ_LLM_TIMEOUT", "45.5")
    monkeypatch.setenv("ARCVIZ_LLM_MAX_RETRIES", "5")
    cfg = ProviderConfig.from_env()
    assert cfg.timeout == 45.5 and cfg.max_retries == 5


def test_from_env_defaults_keep_sdk_behaviour(monkeypatch):
    monkeypatch.delenv("ARCVIZ_LLM_TIMEOUT", raising=False)
    monkeypatch.delenv("ARCVIZ_LLM_MAX_RETRIES", raising=False)
    cfg = ProviderConfig.from_env()
    assert cfg.timeout is None and cfg.max_retries is None


@pytest.mark.parametrize(
    ("name", "value"),
    [("ARCVIZ_LLM_TIMEOUT", "soon"), ("ARCVIZ_LLM_TIMEOUT", "-1"), ("ARCVIZ_LLM_MAX_RETRIES", "-2")],
)
def test_from_env_rejects_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        ProviderConfig.from_env()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_create_provider_passes_timeout_and_retries_to_the_sdk(provider):
    p = create_provider(ProviderConfig(provider=provider, api_key="k", timeout=12.0, max_retries=4))
    assert p.client.timeout == 12.0  # type: ignore[attr-defined]
    assert p.client.max_retries == 4  # type: ignore[attr-defined]


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_create_provider_without_settings_uses_sdk_defaults(provider):
    p = create_provider(ProviderConfig(provider=provider, api_key="k"))
    assert p.client.max_retries == 2  # type: ignore[attr-defined]


# ---- retries against the real SDK clients (in-process transport, no network) ---------------------


def sdk_http(sdk_client: Any) -> Any:
    """The HTTP library an SDK client is built on: `httpx2` for the 1.x/3.x SDKs, `httpx` for older ones.

    Each SDK only accepts transports of its own library, so the tests must not hard-code one.
    """
    for cls in type(sdk_client._client).__mro__:  # the SDK wraps the library's Client in its own subclass
        root = cls.__module__.split(".")[0]
        if root in ("httpx2", "httpx"):
            return importlib.import_module(root)
    raise RuntimeError(f"Unknown HTTP client {type(sdk_client._client)!r}")


ANTHROPIC_HTTP = sdk_http(anthropic.Anthropic(api_key="k"))
OPENAI_HTTP = sdk_http(openai.OpenAI(api_key="k"))

ANTHROPIC_OK = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [{"type": "text", "text": "Hallo."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 3, "output_tokens": 2},
}
OPENAI_OK = {
    "id": "c1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hallo."}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}


def flaky_transport(http: Any, ok_body: dict, failures: int) -> tuple[Any, list[int]]:
    """Answers HTTP 500 `failures` times, then 200 with `ok_body`; records the status codes it sent."""
    sent: list[int] = []

    def handler(request: Any) -> Any:
        if len(sent) < failures:
            sent.append(500)
            return http.Response(
                500, json={"error": {"type": "api_error", "message": "boom"}}, headers={"retry-after-ms": "1"}
            )
        sent.append(200)
        return http.Response(200, json=ok_body)

    return http.MockTransport(handler), sent


def anthropic_provider(failures: int, retries: int) -> tuple[AnthropicProvider, list[int]]:
    transport, sent = flaky_transport(ANTHROPIC_HTTP, ANTHROPIC_OK, failures)
    client = anthropic.Anthropic(
        api_key="k",
        base_url="http://llm.test",
        max_retries=retries,
        http_client=ANTHROPIC_HTTP.Client(transport=transport),
    )
    return AnthropicProvider(client=client, use_fallbacks=False, stream=False), sent


def openai_provider(failures: int, retries: int) -> tuple[OpenAICompatibleProvider, list[int]]:
    transport, sent = flaky_transport(OPENAI_HTTP, OPENAI_OK, failures)
    client = openai.OpenAI(
        api_key="k",
        base_url="http://llm.test/v1",
        max_retries=retries,
        http_client=OPENAI_HTTP.Client(transport=transport),
    )
    return OpenAICompatibleProvider(client=client, stream=False), sent


@pytest.mark.parametrize("make", [anthropic_provider, openai_provider])
def test_transient_error_is_retried(make):
    provider, sent = make(failures=1, retries=1)
    events = list(provider.run_turn("sys", [], "hi", [], lambda c: None))
    assert sent == [500, 200]
    assert events[-1].type == "done" and events[-1].data["text"] == "Hallo."


@pytest.mark.parametrize("make", [anthropic_provider, openai_provider])
def test_retries_exhausted_end_the_turn_with_an_error(make):
    provider, sent = make(failures=5, retries=0)
    events = list(provider.run_turn("sys", [], "hi", [], lambda c: None))
    assert sent == [500]
    assert events[-1].type == "error" and "InternalServerError" in events[-1].data["message"]


# ---- per-turn token budget ---------------------------------------------------------------------


def budget_script() -> list[ModelReply]:
    return [
        ModelReply(
            text="",
            tool_calls=[call("add_node", "c1", name="cache", kind="storage", parent="prod-deployment")],
            stop_reason="tool_use",
            usage={"input_tokens": 900, "output_tokens": 150},
        ),
        ModelReply(
            text="Fertig.", tool_calls=[], stop_reason="end_turn", usage={"input_tokens": 1000, "output_tokens": 5}
        ),
    ]


def test_budget_stops_before_the_next_model_call(store, demo):
    provider = ScriptedProvider(budget_script())
    agent = DiagramAgent(provider, DiagramService(store), token_budget=1000)
    events = list(agent.chat("demo", "cache bitte"))

    assert len(provider.requests) == 1  # the second call never happened
    error = next(e for e in events if e["type"] == "error")
    assert "Token budget of 1000 tokens" in error["message"] and "1050 used" in error["message"]
    assert error["usage"] == {"input_tokens": 900, "output_tokens": 150}

    final = events[-1]
    assert final["error"] == error["message"]
    assert final["version"]["version"] == 2  # the tool call of the crossing reply was applied and versioned
    assert "cache" in store.load("demo").nodes
    assert store.load_chat("demo")[-1]["error"] == error["message"]


def test_budget_not_reached_changes_nothing(store, demo):
    provider = ScriptedProvider(budget_script())
    events = list(DiagramAgent(provider, DiagramService(store), token_budget=10_000).chat("demo", "cache bitte"))
    assert len(provider.requests) == 2
    assert events[-1]["error"] is None and events[-1]["reply"] == "Fertig."


def test_first_call_always_runs_even_with_a_tiny_budget(store, demo):
    provider = ScriptedProvider(
        [ModelReply(text="Hi.", tool_calls=[], stop_reason="end_turn", usage={"input_tokens": 50})]
    )
    events = list(DiagramAgent(provider, DiagramService(store), token_budget=1).chat("demo", "hallo"))
    assert len(provider.requests) == 1 and events[-1]["reply"] == "Hi." and events[-1]["error"] is None


def test_server_reads_turn_token_budget(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_TURN_TOKEN_BUDGET", "1000")
    app = create_app(store=store, provider=ScriptedProvider(budget_script()))
    client = TestClient(app)
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    r = client.post("/api/diagrams/demo/chat", json={"message": "cache bitte"})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert app.state.agent.token_budget == 1000
    assert "Token budget" in events[-1]["error"]
