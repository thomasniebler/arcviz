"""Claude via the official Anthropic SDK (Messages API with client-side tools)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import anthropic

from .base import ChatMessage, LLMProvider, ModelReply, TextDelta, ToolCall, ToolResult, ToolSpec

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 16000,
        effort: str | None = None,
        use_fallbacks: bool = True,
        client: anthropic.Anthropic | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        stream: bool = True,
    ):
        self.model = model
        self.stream = stream
        self.max_tokens = max_tokens
        self.effort = effort
        self.use_fallbacks = use_fallbacks
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if timeout is not None:  # otherwise the SDK defaults apply (10 minutes, 2 retries)
            kwargs["timeout"] = timeout
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        self.client = client or anthropic.Anthropic(**kwargs)

    def config_error(self) -> str | None:
        # The SDK builds a client without credentials and only fails on the first request.
        if not (self.client.api_key or self.client.auth_token):
            return "ANTHROPIC_API_KEY is not set."
        return None

    # ---- hooks -----------------------------------------------------------

    def _history_to_native(self, history: list[ChatMessage]) -> list[Any]:
        messages: list[Any] = []
        for msg in history:
            if msg.role not in ("user", "assistant") or not msg.content.strip():
                continue
            # The API requires alternating roles; merge consecutive same-role turns.
            if messages and messages[-1]["role"] == msg.role:
                messages[-1]["content"] += "\n\n" + msg.content
            else:
                messages.append({"role": msg.role, "content": msg.content})
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] += "\n\n(continued)"
            messages.append({"role": "assistant", "content": "OK."})
        return messages

    def _user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def _tool_params(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]

    def _params(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "tools": self._tool_params(tools),
        }
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        return params

    def _complete(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> ModelReply:
        params = self._params(system, messages, tools)
        if self.use_fallbacks:
            response = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            response = self.client.messages.create(**params)
        return self._to_reply(response)

    def _stream(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> Iterator[TextDelta | ModelReply]:
        if not self.stream:
            yield self._complete(system, messages, tools)
            return
        params = self._params(system, messages, tools)
        if self.use_fallbacks:
            manager: Any = self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            manager = self.client.messages.stream(**params)
        with manager as stream:
            for event in stream:
                if event.type == "text" and event.text:
                    yield TextDelta(event.text)
            response = stream.get_final_message()
        yield self._to_reply(response)

    @staticmethod
    def _to_reply(response: Any) -> ModelReply:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))

        stop = response.stop_reason or "other"
        if stop not in ("end_turn", "tool_use", "max_tokens", "refusal"):
            stop = "tool_use" if calls else "other"
        usage = {}
        if response.usage is not None:
            usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        return ModelReply(
            text="".join(text_parts), tool_calls=calls, stop_reason=stop, native=response.content, usage=usage
        )

    def _assistant_message(self, reply: ModelReply) -> Any:
        # Echo the full content (thinking blocks included) so the conversation stays valid.
        return {"role": "assistant", "content": reply.native}

    def _tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        blocks = [
            {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}
            for r in results
        ]
        return [{"role": "user", "content": blocks}]
