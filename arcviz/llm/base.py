"""Provider-neutral types and the shared tool-calling loop.

A provider only has to translate between these neutral types and its native wire format;
the agentic loop (call model -> execute tools -> feed results back -> repeat) lives here.
"""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .. import telemetry

# One record per turn and per single call (spec: operations.md §4): counts and usage, never messages or replies.
turn_logger = logging.getLogger("arcviz.llm")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments object


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class ChatMessage:
    """One turn of the persisted, provider-neutral transcript (text only)."""

    role: str  # "user" | "assistant"
    content: str


@dataclass
class ModelReply:
    """What a provider returns for a single model call."""

    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | "refusal" | "other"
    native: Any = None  # provider-specific payload needed to append the assistant turn
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class TextDelta:
    """A piece of reply text yielded by a streaming provider before the round's `ModelReply`."""

    text: str


@dataclass
class AgentEvent:
    type: str  # "text_delta" | "text" | "tool_call" | "tool_result" | "done" | "error"
    data: dict[str, Any] = field(default_factory=dict)


ToolExecutor = Callable[[ToolCall], ToolResult]


class LLMProvider(ABC):
    """Base class: subclasses implement the four native-format hooks."""

    name: str = "abstract"
    model: str = ""

    def config_error(self) -> str | None:
        """Reason the provider cannot make requests, determined locally (no network); None if usable."""
        return None

    # ---- hooks -----------------------------------------------------------

    @abstractmethod
    def _history_to_native(self, history: list[ChatMessage]) -> list[Any]:
        """Convert the persisted transcript into the provider's message list."""

    @abstractmethod
    def _user_message(self, text: str) -> Any:
        """Build a native user message."""

    @abstractmethod
    def _complete(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> ModelReply:
        """Perform one model call."""

    def _stream(self, system: str, messages: list[Any], tools: list[ToolSpec]) -> Iterator[TextDelta | ModelReply]:
        """One model call that yields text deltas while the model writes, then exactly one `ModelReply`.

        The default does not stream; streaming providers override it.
        """
        yield self._complete(system, messages, tools)

    @abstractmethod
    def _assistant_message(self, reply: ModelReply) -> Any:
        """Native assistant message to append after a call (must carry the tool calls)."""

    @abstractmethod
    def _tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        """Native message(s) carrying all tool results of one round."""

    # ---- plain completion ------------------------------------------------

    def complete_text(self, system: str, user_text: str) -> str:
        """One model call without tools; returns the reply text. Used for chat compaction summaries."""
        with self._logged_call("text") as usage:
            reply = self._complete(system, [self._user_message(user_text)], [])
            usage.update(reply.usage or {})
            return self._text_of(reply)

    @staticmethod
    def _text_of(reply: ModelReply) -> str:
        if reply.stop_reason == "refusal":
            raise RuntimeError("The model declined to produce the text.")
        if reply.stop_reason == "max_tokens":
            raise RuntimeError("The model response was cut off (max_tokens); refusing a truncated text.")
        if not reply.text.strip():
            raise RuntimeError(f"The model returned no text (stop_reason={reply.stop_reason}).")
        return reply.text.strip()

    def complete_tool(self, system: str, user_text: str, tool: ToolSpec) -> tuple[dict[str, Any], dict[str, int]]:
        """One model call offering only `tool`; returns the arguments of the model's call to it and the usage.

        Used for structured answers (the architecture review). A JSON object as the reply text (optionally
        in a ```json fence) is accepted as a fallback; anything else raises RuntimeError.
        """
        with self._logged_call(tool.name) as logged:
            reply = self._complete(system, [self._user_message(user_text)], [tool])
            logged.update(reply.usage or {})
            return self._tool_arguments(reply, tool)

    @staticmethod
    def _tool_arguments(reply: ModelReply, tool: ToolSpec) -> tuple[dict[str, Any], dict[str, int]]:
        if reply.stop_reason == "refusal":
            raise RuntimeError("The model declined to answer.")
        if reply.stop_reason == "max_tokens":
            raise RuntimeError("The model response was cut off (max_tokens).")
        usage = {key: int(value or 0) for key, value in (reply.usage or {}).items()}
        for tool_call in reply.tool_calls:
            if tool_call.name == tool.name:
                return tool_call.arguments, usage
        parsed = _json_object(reply.text)
        if parsed is None:
            raise RuntimeError(f"The model did not call {tool.name}.")
        return parsed, usage

    @contextmanager
    def _logged_call(self, purpose: str) -> Iterator[dict[str, int]]:
        """Times one single call and writes its `llm_call` record; the caller fills in the usage."""
        usage: dict[str, int] = {}
        started = time.perf_counter()
        error: BaseException | None = None
        attributes = {"gen_ai.system": getattr(self, "name", type(self).__name__), "gen_ai.request.model": self.model}
        with telemetry.span("arcviz.llm.call", attributes) as span:
            try:
                yield usage
            except Exception as exc:
                error = exc
                span.set("arcviz.llm.outcome", "error")
                raise
            finally:
                _log_model_use(self, "llm_call", started, {"purpose": purpose}, usage, error=error)
            span.set("arcviz.llm.outcome", "ok")
            for key in ("input_tokens", "output_tokens"):
                if key in usage:
                    span.set(f"gen_ai.usage.{key}", int(usage[key] or 0))

    # ---- shared loop -----------------------------------------------------

    def run_turn(
        self,
        system: str,
        history: list[ChatMessage],
        user_message: str,
        tools: list[ToolSpec] | Callable[[], list[ToolSpec]],
        execute: ToolExecutor,
        max_rounds: int = 12,
        token_budget: int | None = None,
    ) -> Iterator[AgentEvent]:
        """Run one chat turn. `token_budget` (input + output tokens of all calls) stops the turn before a
        further model call once reached; the first call always runs. `tools` may be a callable, evaluated
        before every model call, when the offered tools depend on what earlier calls did (project chat).
        Writes one `llm_turn` record when the turn ends, also when the stream is closed early (`aborted`)."""
        stats = TurnStats()
        started = time.perf_counter()
        try:
            yield from self._turn(system, history, user_message, tools, execute, max_rounds, token_budget, stats)
        finally:
            _log_model_use(
                self,
                "llm_turn",
                started,
                {"rounds": stats.rounds, "tool_calls": stats.tool_calls},
                stats.usage,
                outcome=stats.outcome or "aborted",
                error_kind=stats.error_kind,
                error_type=stats.error_type,
            )

    def _turn(
        self,
        system: str,
        history: list[ChatMessage],
        user_message: str,
        tools: list[ToolSpec] | Callable[[], list[ToolSpec]],
        execute: ToolExecutor,
        max_rounds: int,
        token_budget: int | None,
        stats: TurnStats,
    ) -> Iterator[AgentEvent]:

        messages = self._history_to_native(history)
        messages.append(self._user_message(user_message))
        final_text_parts: list[str] = []
        usage = stats.usage  # summed over all model calls of this turn; also sent with every error
        parent = telemetry.context()  # spans of this turn's model calls (#187): children of the request span
        system_name, model = getattr(self, "name", type(self).__name__), getattr(self, "model", None)

        for round_no in range(max_rounds):
            spent = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            if token_budget and round_no > 0 and spent >= token_budget:
                stats.fail("budget")
                yield AgentEvent(
                    "error",
                    {
                        "message": f"Token budget of {token_budget} tokens for this turn exhausted ({spent} used); "
                        "stopped before the next model call.",
                        "usage": dict(usage),
                    },
                )
                return
            reply: ModelReply | None = None
            # not made current: the streamed call yields in between, and a context attached on one worker thread
            # cannot be detached on the next
            call_span = telemetry.start(
                "arcviz.llm.call",
                {"gen_ai.system": system_name, "gen_ai.request.model": model, "arcviz.llm.round": round_no + 1},
                parent,
            )
            try:
                offered = tools() if callable(tools) else tools
                for item in self._stream(system, messages, offered):
                    if isinstance(item, TextDelta):
                        if item.text:
                            yield AgentEvent("text_delta", {"text": item.text})
                    else:
                        reply = item
            except Exception as exc:  # surface provider errors to the UI instead of crashing the stream
                call_span.set("arcviz.llm.outcome", "error")
                call_span.failed(exc)
                call_span.end()
                stats.fail("exception", type(exc).__name__)
                yield AgentEvent("error", {"message": f"{type(exc).__name__}: {exc}", "usage": dict(usage)})
                return
            except BaseException:  # the stream was closed (client gone)
                call_span.set("arcviz.llm.outcome", "error")
                call_span.failed("aborted")
                call_span.end()
                raise
            call_span.set("arcviz.llm.outcome", "ok" if reply is not None else "error")
            for key in ("input_tokens", "output_tokens"):
                if reply is not None and (reply.usage or {}).get(key) is not None:
                    call_span.set(f"gen_ai.usage.{key}", int(reply.usage[key] or 0))
            call_span.end()
            stats.rounds += 1
            if reply is None:
                stats.fail("no_reply")
                yield AgentEvent(
                    "error", {"message": "The provider stream ended without a reply.", "usage": dict(usage)}
                )
                return

            for key, value in (reply.usage or {}).items():
                usage[key] = usage.get(key, 0) + int(value or 0)

            if reply.text:
                final_text_parts.append(reply.text)
                yield AgentEvent("text", {"text": reply.text})

            if reply.stop_reason == "refusal":
                stats.fail("refusal")
                yield AgentEvent(
                    "error", {"message": "The model declined to answer this request.", "usage": dict(usage)}
                )
                return
            if reply.stop_reason == "max_tokens":
                stats.fail("max_tokens")
                yield AgentEvent(
                    "error", {"message": "The model response was cut off (max_tokens).", "usage": dict(usage)}
                )
                return
            if not reply.tool_calls:
                break

            messages.append(self._assistant_message(reply))
            results: list[ToolResult] = []
            names = {tool.name for tool in offered}
            for call in reply.tool_calls:
                stats.tool_calls += 1
                yield AgentEvent("tool_call", {"id": call.id, "name": call.name, "arguments": call.arguments})
                with telemetry.span("arcviz.tool", {"arcviz.tool.name": telemetry.tool_name(call.name, names)}) as span:
                    result = execute(call)  # store writes inside are children of this span
                    span.set("arcviz.tool.outcome", "error" if result.is_error else "ok")
                results.append(result)
                yield AgentEvent(
                    "tool_result",
                    {"id": call.id, "name": call.name, "is_error": result.is_error, "content": result.content},
                )
            messages.extend(self._tool_results_messages(results))
        else:
            stats.fail("max_rounds")
            yield AgentEvent(
                "error",
                {"message": f"Stopped after {max_rounds} tool rounds without a final answer.", "usage": dict(usage)},
            )

        stats.outcome = stats.outcome or "done"
        yield AgentEvent("done", {"text": "\n\n".join(p for p in final_text_parts if p.strip()), "usage": usage})


@dataclass
class TurnStats:
    """What the `llm_turn` record of one turn reports."""

    rounds: int = 0
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    outcome: str | None = None  # done | error; None while running (a closed stream: aborted)
    error_kind: str | None = None  # budget | exception | no_reply | refusal | max_tokens | max_rounds
    error_type: str | None = None  # the exception's class name, never its message

    def fail(self, kind: str, error_type: str | None = None) -> None:
        self.outcome, self.error_kind, self.error_type = "error", kind, error_type


def _log_model_use(
    provider: LLMProvider,
    event: str,
    started: float,
    data: dict[str, Any],
    usage: dict[str, int],
    outcome: str | None = None,
    error_kind: str | None = None,
    error_type: str | None = None,
    error: BaseException | None = None,
) -> None:
    """The `llm_turn` / `llm_call` record (spec: operations.md §4): counts, usage and timing only."""
    if error is not None:
        outcome, error_type = "error", type(error).__name__
    outcome = outcome or "done"
    seconds = time.perf_counter() - started
    duration = int(seconds * 1000)
    values: dict[str, Any] = {
        "event": event,
        "provider": getattr(provider, "name", type(provider).__name__),
        "model": getattr(provider, "model", None),
        "outcome": outcome,
        **data,
        **{key: int(value or 0) for key, value in usage.items()},
        "duration_ms": duration,
    }
    if error_kind:
        values["error_kind"] = error_kind
    if error_type:
        values["error_type"] = error_type
    telemetry.record_llm(
        "turn" if event == "llm_turn" else "call", values["provider"], values["model"], outcome, seconds, usage
    )
    tokens = values.get("input_tokens", 0) + values.get("output_tokens", 0)
    what = "LLM turn" if event == "llm_turn" else f"LLM call {data.get('purpose')}"
    detail = f" ({error_kind or error_type})" if outcome == "error" else ""
    turn_logger.log(
        logging.INFO if outcome == "done" else logging.WARNING,
        "%s %s%s: %d tokens in %d ms",
        what,
        outcome,
        detail,
        tokens,
        duration,
        extra={**values, "fields": tuple(values)},
    )


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments may arrive as dict or as a JSON string; never string-match on them."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Tool arguments are not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return parsed


_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _json_object(text: str) -> dict[str, Any] | None:
    """The JSON object a reply text consists of, or carries in a ```json fence; None otherwise."""
    match = _FENCE.search(text or "")
    candidate = match.group(1) if match else (text or "").strip()
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
