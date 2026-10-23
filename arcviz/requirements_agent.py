"""The requirements chat: an assistant for collecting, clarifying and refining requirements (spec: requirements-workshop.md).

It works on the project's requirements, stakeholders, drivers, scenarios and decisions, not on diagrams. Its tools are the
MCP tools of `AgentTools` (so the same write policy holds: add and edit staging data, suggest a refinement, never delete,
accept or decide; ADR-0186) plus one chunk of the workshop steps (`refine_requirements`, `derive_requirements`,
`requirement_workshop`). Every write is on request of the user who is present; nothing it does binds anyone else. The
transcript is the project's `reqchat` document (`services/requirement_chat.py`, ADR-0193), written in a `finally` block so a
disconnected client still keeps what was done.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Generator
from typing import Any

from . import prompts, requirement_derive, requirement_refine
from .agent import tool_error
from .agent_model import architecture_context
from .agent_tools import AgentTools, ToolFailure
from .llm import ChatMessage, LLMProvider, ToolCall, ToolResult, ToolSpec
from .operations import OperationError
from .ports import StoreError
from .requirement_workshop import derive_chunk, refine_chunk
from .services import Forbidden, Services

logger = logging.getLogger(__name__)

HISTORY_TURNS = 30

_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> ToolSpec:
    return ToolSpec(
        name,
        description,
        {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False},
    )


REQUIREMENT_TOOLS: list[ToolSpec] = [
    _spec(
        "list_requirements",
        "List the requirements (id, text, status, kind, priority, theme, coverage), filtered and paged (limit up to 50). "
        "status: raw, refined, rejected, duplicate; coverage: uncovered, driver, scenario, decision, none.",
        {
            "status": _STRING,
            "kind": _STRING,
            "theme": _STRING,
            "query": _STRING,
            "coverage": _STRING,
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
    ),
    _spec(
        "get_requirement",
        "One requirement with its original wording, statement, suggestion, issues, links and the drivers, scenarios and "
        "decisions that name it. Address it by id (req3), external id or the start of its text.",
        {"requirement": _STRING},
        ["requirement"],
    ),
    _spec(
        "get_traceability",
        "For one requirement the objects that name it and its coverage; without one the refined requirements nothing "
        "covers yet.",
        {"requirement": _STRING},
    ),
    _spec("list_stakeholders", "The stakeholders: id, name, role, influence, concerns, drivers.", {}),
    _spec(
        "list_drivers",
        "The architecture drivers, most important first.",
        {"kind": _STRING, "limit": {"type": "integer"}},
    ),
    _spec(
        "list_scenarios",
        "The quality scenarios, optionally of one driver.",
        {"driver": _STRING, "limit": {"type": "integer"}},
    ),
    _spec(
        "add_requirements",
        "Add requirements exactly as the user gave them (at most 50 per call; the same text or external id is skipped). "
        "Items: {raw, source?, external_id?, theme?, priority? (must, should, could, wont), stakeholders? (ids)}.",
        {
            "items": {
                "type": "array",
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "raw": _STRING,
                        "source": _STRING,
                        "external_id": _STRING,
                        "theme": _STRING,
                        "priority": _STRING,
                        "stakeholders": _STRINGS,
                    },
                    "required": ["raw"],
                    "additionalProperties": False,
                },
            }
        },
        ["items"],
    ),
    _spec(
        "update_requirement",
        "Change source, theme, priority, stakeholders or elements of a requirement (fields: an object). Statement, kind, "
        "measure, status and deleting are for people: use suggest_refinement.",
        {"requirement": _STRING, "fields": {"type": "object"}},
        ["requirement", "fields"],
    ),
    _spec(
        "suggest_refinement",
        "Suggest the normalised form of requirements (at most 25): {requirement, statement (one sentence), kind?, "
        "priority?, measure?, theme?, stakeholders?, issues? [{kind, text, other?}], duplicate_of?}. Nothing changes until a "
        "person accepts a suggestion in the table.",
        {"suggestions": {"type": "array", "maxItems": 25, "items": {"type": "object"}}},
        ["suggestions"],
    ),
    _spec(
        "add_stakeholder",
        "Add a stakeholder (influence: high, medium, low).",
        {"name": _STRING, "role": _STRING, "concerns": _STRING, "influence": _STRING, "drivers": _STRINGS},
        ["name"],
    ),
    _spec(
        "refine_requirements",
        f"Refine one chunk (at most {requirement_refine.MAX_CHUNK}) of requirements with one model call and store the "
        "result as suggestions that a person accepts. Without ids it takes the raw ones that carry no suggestion yet; "
        "`remaining` says how many are left. Call it only when the user asks to process requirements.",
        {"ids": _STRINGS, "limit": {"type": "integer"}},
    ),
    _spec(
        "derive_requirements",
        f"Derive drivers and scenarios from one chunk (at most {requirement_derive.MAX_CHUNK}) of refined requirements "
        "and store them as a proposal that a person applies in part (nothing is written to the project). Without ids it "
        "takes the refined ones nothing covers yet, optionally of one theme. Call it only when the user asks for it.",
        {"ids": _STRINGS, "theme": _STRING, "limit": {"type": "integer"}},
    ),
]
WRITE_TOOLS = (
    "add_requirements",
    "update_requirement",
    "suggest_refinement",
    "add_stakeholder",
    "refine_requirements",
    "derive_requirements",
)


def _ints(args: dict[str, Any], key: str) -> int | None:
    value = args.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _strs(args: dict[str, Any], key: str) -> list[str] | None:
    value = args.get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else None


def _str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) else None


class RequirementsAgent:
    def __init__(
        self,
        provider: LLMProvider,
        max_rounds: int = 10,
        history_turns: int = HISTORY_TURNS,
        token_budget: int | None = None,
    ):
        self.provider = provider
        self.max_rounds = max_rounds
        self.history_turns = history_turns
        self.token_budget = token_budget or None

    # ---- prompt and history -----------------------------------------------------------------------------------

    @staticmethod
    def system_prompt(svc: Services, project_id: str) -> str:
        """The stable prefix, the architecture block (drivers, stakeholders, the accepted decisions that bind), then the
        project and the counts of its requirements (the requirements themselves are read with tools)."""
        project = svc.projects.get(project_id)
        context = architecture_context(project)
        architecture = prompts.architecture_part(context.drivers, context.stakeholders, context.decisions)
        digest = svc.requirements.page(project_id, limit=1)["digest"]
        steps = ", ".join(f"{name}: {count}" for name, count in digest["steps"].items())
        themes = ", ".join(f"{name} ({count})" for name, count in list(digest["themes"].items())[:12]) or "none"
        state = (
            f"Project: {project.title}\n\nRequirements: {digest['total']} ({steps}).\nThemes: {themes}.\n"
            "Read them with list_requirements and get_requirement."
        )
        return prompts.compose(prompts.requirements_prompt_prefix(), *([architecture] if architecture else []), state)

    def _history(self, entries: list[dict[str, Any]]) -> list[ChatMessage]:
        msgs = [
            ChatMessage(role=e["role"], content=e["content"])
            for e in entries
            if e.get("role") in ("user", "assistant") and e.get("content")
        ]
        return msgs[-self.history_turns :]

    # ---- tools -------------------------------------------------------------------------------------------------

    def _executor(
        self, svc: Services, project_id: str, applied: list[dict[str, Any]]
    ) -> Callable[[ToolCall], ToolResult]:
        agent = AgentTools(svc, "write", client="chat")
        runners: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "list_requirements": lambda a: agent.list_requirements(
                project_id,
                _str(a, "status"),
                _str(a, "kind"),
                _str(a, "theme"),
                _str(a, "query"),
                _str(a, "coverage"),
                _ints(a, "limit"),
                _ints(a, "offset"),
            ),
            "get_requirement": lambda a: agent.get_requirement(project_id, _str(a, "requirement") or ""),
            "get_traceability": lambda a: agent.get_traceability(project_id, _str(a, "requirement")),
            "list_stakeholders": lambda a: agent.list_stakeholders(project_id),
            "list_drivers": lambda a: agent.list_drivers(project_id, _str(a, "kind"), _ints(a, "limit")),
            "list_scenarios": lambda a: agent.list_scenarios(project_id, _str(a, "driver"), _ints(a, "limit")),
            "add_requirements": lambda a: agent.add_requirements(project_id, _dicts(a.get("items"))),
            "update_requirement": lambda a: agent.update_requirement(
                project_id, _str(a, "requirement") or "", _object(a, "fields")
            ),
            "suggest_refinement": lambda a: agent.suggest_refinement(project_id, _dicts(a.get("suggestions"))),
            "add_stakeholder": lambda a: agent.add_stakeholder(
                project_id,
                _str(a, "name") or "",
                _str(a, "role") or "",
                _str(a, "concerns") or "",
                _str(a, "influence") or "medium",
                _strs(a, "drivers"),
            ),
            "refine_requirements": lambda a: refine_chunk(
                svc, self.provider, project_id, _strs(a, "ids"), _ints(a, "limit") or requirement_refine.MAX_CHUNK
            ),
            "derive_requirements": lambda a: derive_chunk(
                svc,
                self.provider,
                project_id,
                _strs(a, "ids"),
                _str(a, "theme"),
                _ints(a, "limit") or requirement_derive.MAX_CHUNK,
            ),
        }

        def execute(call: ToolCall) -> ToolResult:
            run = runners.get(call.name)
            if run is None:
                return tool_error(call, f"Unknown tool '{call.name}'. Available: {', '.join(runners)}.")
            try:
                result = run(call.arguments or {})
            except (ToolFailure, Forbidden, OperationError, StoreError) as exc:  # their message is for the model
                return tool_error(call, str(exc))
            if call.name in WRITE_TOOLS:
                applied.append({"op": call.name, **_counts(result)})
            return ToolResult(call.id, json.dumps({"ok": True, **result}, ensure_ascii=False, default=str))

        return execute

    # ---- the turn ----------------------------------------------------------------------------------------------

    def chat(self, svc: Services, project_id: str, user_message: str) -> Generator[dict[str, Any], None, None]:
        """Run one turn and yield JSON-able events (`text_delta`, `text`, `tool_call`, `tool_result`, `done`, `error`) and
        a last `final`. Forbidden / ProjectNotFound before the first event for a caller who may not write."""
        svc.projects.require(project_id, "editor")  # viewers never start a turn
        generation = svc.requirement_chat.generation(project_id)
        history = self._history(svc.requirement_chat.entries(project_id))
        system = self.system_prompt(svc, project_id)
        applied: list[dict[str, Any]] = []
        final_text, error, completed = "", None, False
        usage: dict[str, Any] = {}
        try:
            for event in self.provider.run_turn(
                system,
                history,
                user_message,
                REQUIREMENT_TOOLS,
                self._executor(svc, project_id, applied),
                self.max_rounds,
                token_budget=self.token_budget,
            ):
                if event.type == "done":
                    final_text = event.data.get("text", "")
                if event.type in ("done", "error"):
                    usage = event.data.get("usage") or usage
                if event.type == "error":
                    error = event.data.get("message")
                yield {"type": event.type, **event.data}
            completed = True
        finally:
            if not completed and error is None:
                error = "Turn interrupted before the model finished (client disconnected)."
            problem = self._finalize(svc, project_id, user_message, final_text, error, applied, usage, generation)
            error = error or problem
        yield {"type": "final", "reply": final_text, "error": error, "operations": applied, "usage": usage}

    @staticmethod
    def _finalize(
        svc: Services,
        project_id: str,
        user_message: str,
        final_text: str,
        error: str | None,
        applied: list[dict[str, Any]],
        usage: dict[str, Any],
        generation: str,
    ) -> str | None:
        """The transcript entries of the turn; a problem for `final.error` when they could not be written."""
        entries: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        text = final_text or (f"(error: {error})" if error else "")
        if text:
            entries.append(
                {"role": "assistant", "content": text, "operations": applied, "error": error, "usage": usage}
            )
        try:
            if not svc.requirement_chat.append(project_id, entries, generation):
                logger.info("Requirements chat of '%s' cleared during the turn: no transcript entry", project_id)
        except Exception as exc:  # never let bookkeeping break a turn (also: the caller's role dropped meanwhile)
            logger.warning("Requirements chat entry for '%s' not written (%s)", project_id, type(exc).__name__)
            return "The conversation could not be saved."
        return None


def _object(args: dict[str, Any], key: str) -> dict[str, Any]:
    value = args.get(key)
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _counts(result: dict[str, Any]) -> dict[str, int]:
    """What a write tool did, as counts (never the texts, they stay out of the transcript's bookkeeping)."""
    return {key: len(value) for key, value in result.items() if isinstance(value, list)}
