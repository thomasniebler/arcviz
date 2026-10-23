"""OpenAI-compatible chat completions (OpenAI, Ollama, vLLM, LM Studio, ...)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from openai import OpenAI

from .base import ChatMessage, LLMProvider, ModelReply, TextDelta, ToolCall, ToolResult, ToolSpec, parse_arguments

DEFAULT_MODEL = "gpt-4o"


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | Callable[[], str] | None = None,  # a callable: a fresh bearer token per request (Entra)
        base_url: str | None = None,
        max_tokens: int | None = None,
        client: OpenAI | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        stream: bool = True,
    ):
        self.model = model
        self.stream = stream
        self.max_tokens = max_tokens
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url
            kwargs["api_key"] = api_key or "not-needed"  # local servers ignore the key but the SDK wants one
        elif api_key:
            kwargs["api_key"] = api_key
        if timeout is not None:  # otherwise the SDK defaults apply (10 minutes, 2 retries)
            kwargs["timeout"] = timeout
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        self.client = client or OpenAI(**kwargs)

    # ---- hooks -----------------------------------------------------------

    def _history_to_native(self, history: list[ChatMessage]) -> list[Any]:
        return [
            {"role": m.role, "content": m.content} for m in history if m.role in ("user", "assistant") and m.content
        ]

    def _user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def _tool_params(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]

    def _params(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": self._tool_params(tools),
            "tool_choice": "auto",
        }
        if self.max_tokens:
            params["max_tokens"] = self.max_tokens
        return params

    def _complete(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> ModelReply:
        response = self.client.chat.completions.create(**self._params(system, messages, tools))
        choice = response.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            calls.append(ToolCall(id=tc.id, name=fn.name, arguments=parse_arguments(fn.arguments)))
        usage = {}
        if response.usage is not None:
            usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
        return ModelReply(
            text=message.content or "",
            tool_calls=calls,
            stop_reason=_stop_reason(choice.finish_reason, calls),
            native=message,
            usage=usage,
        )

    def _stream(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> Iterator[TextDelta | ModelReply]:
        if not self.stream:
            yield self._complete(system, messages, tools)
            return
        params = self._params(system, messages, tools)
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}  # the usage arrives in a final chunk without choices
        text_parts: list[str] = []
        slots: dict[int, dict[str, str]] = {}  # tool calls accumulated by index
        finish: str | None = None
        usage: dict[str, int] = {}
        with self.client.chat.completions.create(**params) as response:
            for chunk in response:
                if getattr(chunk, "usage", None) is not None:
                    usage = {"input_tokens": chunk.usage.prompt_tokens, "output_tokens": chunk.usage.completion_tokens}
                for choice in chunk.choices or []:
                    delta = choice.delta
                    if delta is not None and delta.content:
                        text_parts.append(delta.content)
                        yield TextDelta(delta.content)
                    for tc in (delta.tool_calls if delta is not None else None) or []:
                        slot = slots.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            slot["name"] += fn.name or ""
                            slot["arguments"] += fn.arguments or ""
                    if choice.finish_reason:
                        finish = choice.finish_reason
        calls = [
            ToolCall(id=slot["id"] or f"call_{index}", name=slot["name"], arguments=parse_arguments(slot["arguments"]))
            for index, slot in sorted(slots.items())
        ]
        yield ModelReply(
            text="".join(text_parts), tool_calls=calls, stop_reason=_stop_reason(finish, calls), usage=usage
        )

    def _assistant_message(self, reply: ModelReply) -> Any:
        msg: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
        if reply.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                }
                for c in reply.tool_calls
            ]
        return msg

    def _tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        return [{"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content} for r in results]


def _stop_reason(finish: str | None, calls: list[ToolCall]) -> str:
    """Map an OpenAI `finish_reason` onto the neutral stop reasons."""
    if finish == "length":
        return "max_tokens"
    if finish == "content_filter":
        return "refusal"
    return "tool_use" if calls else "end_turn"
