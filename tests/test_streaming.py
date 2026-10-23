"""Streaming reply text deltas (spec: docs/architecture/specs/reply-streaming.md)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace as NS
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz.agent import DiagramAgent
from arcviz.llm import ProviderConfig, create_provider
from arcviz.llm.anthropic_provider import AnthropicProvider
from arcviz.llm.base import ModelReply, TextDelta, ToolSpec
from arcviz.llm.openai_provider import OpenAICompatibleProvider
from arcviz.server import create_app
from arcviz.services import DiagramService
from tests.conftest import ScriptedProvider, call

TOOL = [ToolSpec("add_node", "Add.", {"type": "object", "properties": {}})]


def script() -> list[ModelReply]:
    return [
        ModelReply(
            text="Ich lege das VNet an.",
            tool_calls=[call("add_node", "c1", name="prod-vnet", kind="vnet", parent="prod-deployment")],
            stop_reason="tool_use",
        ),
        ModelReply(text="VNet angelegt.", tool_calls=[], stop_reason="end_turn"),
    ]


def rounds(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Per round: (concatenated deltas, the round's text event)."""
    out, buf = [], ""
    for e in events:
        if e["type"] == "text_delta":
            buf += e["text"]
        elif e["type"] == "text":
            out.append((buf, e["text"]))
            buf = ""
    return out


# ---- loop ---------------------------------------------------------------------------------------


def test_agent_streams_deltas_before_each_round_text(store, demo):
    events = list(DiagramAgent(ScriptedProvider(script(), stream=True), DiagramService(store)).chat("demo", "vnet"))
    types = [e["type"] for e in events]
    assert types.count("text_delta") == 7  # "Ich ", "lege ", ... word by word
    assert rounds(events) == [("Ich lege das VNet an.", "Ich lege das VNet an."), ("VNet angelegt.", "VNet angelegt.")]
    first_text = types.index("text")
    assert set(types[:first_text]) == {"text_delta"}
    assert types[first_text + 1 : first_text + 3] == ["tool_call", "tool_result"]
    assert types[-2:] == ["done", "final"]
    final = events[-1]
    assert final["reply"] == "Ich lege das VNet an.\n\nVNet angelegt."
    assert store.load_chat("demo")[-1]["content"] == final["reply"]  # deltas are not persisted separately


def test_non_streaming_provider_emits_no_deltas(store, demo):
    events = list(DiagramAgent(ScriptedProvider(script()), DiagramService(store)).chat("demo", "vnet"))
    assert "text_delta" not in {e["type"] for e in events}
    assert events[-1]["reply"] == "Ich lege das VNet an.\n\nVNet angelegt."


class BrokenStream(ScriptedProvider):
    def __init__(self, mode: str):
        super().__init__([])
        self.mode = mode

    def _stream(self, system, messages, tools) -> Iterator[TextDelta | ModelReply]:
        yield TextDelta("Ich lege ")
        if self.mode == "raise":
            raise ConnectionError("stream reset")


@pytest.mark.parametrize(
    ("mode", "message"),
    [("raise", "ConnectionError: stream reset"), ("no_reply", "The provider stream ended without a reply.")],
)
def test_broken_stream_ends_the_turn_with_an_error(store, demo, mode, message):
    events = list(DiagramAgent(BrokenStream(mode), DiagramService(store)).chat("demo", "vnet"))
    assert [e["type"] for e in events] == ["text_delta", "error", "final"]
    assert events[1]["message"] == message and events[-1]["error"] == message
    assert store.load_chat("demo")[-1]["content"] == f"(error: {message})"


def test_sse_stream_carries_text_deltas(store):
    app = create_app(store=store, provider=ScriptedProvider(script(), stream=True))
    client = TestClient(app)
    client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"})
    r = client.post("/api/diagrams/demo/chat", json={"message": "vnet"})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[0] == {"type": "text_delta", "text": "Ich "}
    assert rounds(events)[1] == ("VNet angelegt.", "VNet angelegt.")


# ---- configuration -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, True), ("1", True), ("0", False), ("false", False), ("no", False)]
)
def test_arcviz_stream_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ARCVIZ_STREAM", raising=False)
    else:
        monkeypatch.setenv("ARCVIZ_STREAM", raw)
    assert ProviderConfig.from_env().stream is expected


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_create_provider_passes_stream(provider):
    assert create_provider(ProviderConfig(provider=provider, api_key="k")).stream is True  # type: ignore[attr-defined]
    assert create_provider(ProviderConfig(provider=provider, api_key="k", stream=False)).stream is False  # type: ignore[attr-defined]


# ---- Anthropic -------------------------------------------------------------------------------------


class FakeAnthropicStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


class FakeAnthropicMessages:
    def __init__(self, stream):
        self._stream = stream
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def stream(self, **params):
        self.calls.append(("stream", params))
        return self._stream

    def create(self, **params):
        self.calls.append(("create", params))
        return self._stream.final


def anthropic_fake() -> tuple[NS, FakeAnthropicMessages, FakeAnthropicMessages]:
    final = NS(
        content=[
            NS(type="thinking", thinking=""),
            NS(type="text", text="Ich lege das an."),
            NS(type="tool_use", id="t1", name="add_node", input={"name": "x", "kind": "service"}),
        ],
        stop_reason="tool_use",
        usage=NS(input_tokens=10, output_tokens=7),
    )
    events = [
        NS(type="message_start"),
        NS(type="text", text="Ich lege "),
        NS(type="text", text="das an."),
        NS(type="content_block_stop"),
    ]
    plain, beta = (
        FakeAnthropicMessages(FakeAnthropicStream(events, final)),
        FakeAnthropicMessages(FakeAnthropicStream(events, final)),
    )
    return NS(messages=plain, beta=NS(messages=beta)), plain, beta


@pytest.mark.parametrize("fallbacks", [False, True])
def test_anthropic_stream_yields_deltas_and_the_final_reply(fallbacks):
    client, plain, beta = anthropic_fake()
    provider = AnthropicProvider(client=client, use_fallbacks=fallbacks)  # type: ignore[arg-type]
    items = list(provider._stream("sys", [{"role": "user", "content": "hi"}], TOOL))
    assert items[:2] == [TextDelta("Ich lege "), TextDelta("das an.")]
    reply = items[2]
    assert isinstance(reply, ModelReply) and len(items) == 3
    assert reply.text == "Ich lege das an." and reply.stop_reason == "tool_use"
    assert reply.tool_calls[0].name == "add_node" and reply.usage == {"input_tokens": 10, "output_tokens": 7}
    used = beta if fallbacks else plain
    kind, params = used.calls[0]
    assert kind == "stream" and params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert (params.get("fallbacks") == "default") is fallbacks


def test_anthropic_without_streaming_uses_create():
    client, plain, _ = anthropic_fake()
    provider = AnthropicProvider(client=client, use_fallbacks=False, stream=False)  # type: ignore[arg-type]
    items = list(provider._stream("sys", [], TOOL))
    assert len(items) == 1 and isinstance(items[0], ModelReply) and plain.calls[0][0] == "create"


def anthropic_sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


@pytest.mark.parametrize("fallbacks", [False, True])
def test_anthropic_stream_against_the_real_sdk(fallbacks):
    """The real SDK stream helper (no fake) yields `text` events for text deltas, over an in-process transport."""
    import anthropic

    from tests.test_llm_resilience import ANTHROPIC_HTTP

    body = anthropic_sse(
        {
            "type": "message_start",
            "message": {
                "id": "m1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Ich lege "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "das an."}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "t1", "name": "add_node", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"name": "x", '},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '"kind": "service"}'},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    )
    seen: list[dict[str, Any]] = []

    def handler(request: Any) -> Any:
        seen.append(json.loads(request.content))
        return ANTHROPIC_HTTP.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = anthropic.Anthropic(
        api_key="k",
        base_url="http://llm.test",
        http_client=ANTHROPIC_HTTP.Client(transport=ANTHROPIC_HTTP.MockTransport(handler)),
    )
    items = list(
        AnthropicProvider(client=client, use_fallbacks=fallbacks)._stream(
            "sys", [{"role": "user", "content": "hi"}], TOOL
        )
    )
    assert items[:2] == [TextDelta("Ich lege "), TextDelta("das an.")] and len(items) == 3
    reply = items[2]
    assert isinstance(reply, ModelReply) and reply.text == "Ich lege das an." and reply.stop_reason == "tool_use"
    assert [(c.name, c.arguments) for c in reply.tool_calls] == [("add_node", {"name": "x", "kind": "service"})]
    assert reply.usage == {"input_tokens": 5, "output_tokens": 9}
    assert seen[0]["stream"] is True and (seen[0].get("fallbacks") == "default") is fallbacks


# ---- OpenAI-compatible -------------------------------------------------------------------------------


def chunk(content=None, tool_calls=None, finish=None, usage=None, choices=True):
    if not choices:
        return NS(choices=[], usage=usage)
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls), finish_reason=finish)], usage=usage)


def tc(index, id=None, name=None, arguments=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments))


class FakeOpenAIStream(list):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def openai_fake(chunks):
    calls: list[dict[str, Any]] = []

    def create(**params):
        calls.append(params)
        return FakeOpenAIStream(chunks)

    return NS(chat=NS(completions=NS(create=create))), calls


def test_openai_stream_accumulates_text_tool_calls_and_usage():
    chunks = [
        chunk(content="Ich "),
        chunk(content="mache das."),
        chunk(tool_calls=[tc(0, id="call_a", name="add_node", arguments='{"name": ')]),
        chunk(
            tool_calls=[
                tc(0, arguments='"x", "kind": "service"}'),
                tc(1, id="call_b", name="get_diagram", arguments=""),
            ]
        ),
        chunk(finish="tool_calls"),
        chunk(choices=False, usage=NS(prompt_tokens=12, completion_tokens=9)),
    ]
    client, calls = openai_fake(chunks)
    items = list(OpenAICompatibleProvider(client=client)._stream("sys", [], TOOL))  # type: ignore[arg-type]
    assert items[:2] == [TextDelta("Ich "), TextDelta("mache das.")]
    reply = items[-1]
    assert isinstance(reply, ModelReply) and len(items) == 3
    assert reply.text == "Ich mache das." and reply.stop_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("call_a", "add_node", {"name": "x", "kind": "service"}),
        ("call_b", "get_diagram", {}),
    ]
    assert reply.usage == {"input_tokens": 12, "output_tokens": 9}
    assert calls[0]["stream"] is True and calls[0]["stream_options"] == {"include_usage": True}
    # the assistant message echoed back carries the accumulated calls
    msg = OpenAICompatibleProvider(client=client)._assistant_message(reply)  # type: ignore[arg-type]
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"name": "x", "kind": "service"}'


@pytest.mark.parametrize(
    ("finish", "stop"), [("length", "max_tokens"), ("content_filter", "refusal"), ("stop", "end_turn")]
)
def test_openai_stream_maps_finish_reason(finish, stop):
    client, _ = openai_fake([chunk(content="x"), chunk(finish=finish)])
    reply = list(OpenAICompatibleProvider(client=client)._stream("sys", [], TOOL))[-1]  # type: ignore[arg-type]
    assert isinstance(reply, ModelReply) and reply.stop_reason == stop and reply.usage == {}


def test_openai_without_streaming_uses_plain_create():
    response = NS(
        choices=[NS(message=NS(content="Hallo.", tool_calls=None), finish_reason="stop")],
        usage=NS(prompt_tokens=1, completion_tokens=1),
    )
    calls: list[dict[str, Any]] = []
    client = NS(chat=NS(completions=NS(create=lambda **p: calls.append(p) or response)))
    items = list(OpenAICompatibleProvider(client=client, stream=False)._stream("sys", [], TOOL))  # type: ignore[arg-type]
    assert len(items) == 1 and items[0].text == "Hallo." and "stream" not in calls[0]  # type: ignore[union-attr]
