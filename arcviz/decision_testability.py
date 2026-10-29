"""Suggest assumptions, tests and a falsification criterion for a decision (spec: docs/architecture/specs/adr-testability.md,
ADR-0209).

One `LLMProvider.complete_tool` call offering only `submit_testability` over the decision as the user typed it. The
answer is validated item by item (`clean_assumptions`) and never stored: the person edits and saves it in the editor.
Never raises because of the model: a failed call or an answer without a usable item gives `llm.ok = false`.
"""

from __future__ import annotations

from typing import Any

from . import decision_model as dm
from .llm import LLMProvider, ToolSpec
from .review import in_language, localized_tool

TESTABILITY_TOOL = ToolSpec(
    "submit_testability",
    "Submit the assumptions the decision rests on, how to test each cheaply, and when to reverse the decision.",
    {
        "type": "object",
        "properties": {
            "assumptions": {
                "type": "array",
                "maxItems": dm.MAX_ASSUMPTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {"type": "string", "description": "One falsifiable sentence."},
                        "test": {
                            "type": "string",
                            "description": "The cheapest decisive test and the result that would count (a threshold).",
                        },
                        "effort": {"type": "string", "enum": list(dm.INFLUENCE), "description": "Effort of the test."},
                    },
                    "required": ["statement", "test", "effort"],
                    "additionalProperties": False,
                },
            },
            "falsification": {
                "type": "string",
                "description": "The observation that would make the team reverse the decision.",
            },
        },
        "required": ["assumptions", "falsification"],
        "additionalProperties": False,
    },
)

TESTABILITY_SYSTEM_PROMPT = f"""You review an architecture decision record for testability.

You get one decision, possibly incomplete: title, context, the options with pros and cons, the decision and its
consequences. Name the assumptions the chosen option rests on, the ones that would hurt most if wrong first, at most
{dm.MAX_ASSUMPTIONS}. For each assumption give the cheapest test that would decide it, and the result that counts:
a spike, a load or failure-injection test with a stated threshold, a prototype, a cost estimate, a look at the
existing data, an interview. State the effort of that test (low: hours, medium: days, high: weeks). Finally state in
one sentence which observation would make the team reverse the decision.

Rules:
- An assumption is a sentence that can be wrong, not a wish or a requirement.
- A test names what is measured and what result counts; "evaluate" or "think about" is not a test.
- Use only what the decision says or what follows directly from it; do not invent facts about the system.
- Write in German, short sentences.
- Answer by calling submit_testability exactly once. Do not answer in text.
"""


def clean_assumptions(raw: list[Any]) -> tuple[list[dm.Assumption], int]:
    """The usable assumptions of a model answer (untested, bounded) and how many items were dropped."""
    kept: list[dm.Assumption] = []
    dropped = 0
    for item in raw:
        statement = " ".join(str(item.get("statement") or "").split()) if isinstance(item, dict) else ""
        if not statement or len(kept) >= dm.MAX_ASSUMPTIONS:
            dropped += 1
            continue
        test = item.get("test")
        effort = item.get("effort")
        kept.append(
            dm.Assumption(
                statement=statement[: dm.MAX_ASSUMPTION],
                test=test.strip()[: dm.MAX_TEST] if isinstance(test, str) else "",
                effort=effort if effort in dm.INFLUENCE else "medium",
            )
        )
    return kept, dropped


def suggestion_input(values: dict[str, Any]) -> str:
    """The user message: the decision as typed, each part bounded."""
    lines = [f"title: {str(values.get('title') or '').strip()[: dm.MAX_TITLE]}"]
    for key in ("context", "decision", "consequences"):
        text = str(values.get(key) or "").strip()[: dm.MAX_LONG_TEXT]
        if text:
            lines.append(f"{key}: {text}")
    options = values.get("options")
    for n, option in enumerate(options[: dm.MAX_OPTIONS] if isinstance(options, list) else [], start=1):
        if not isinstance(option, dict):
            continue
        lines.append(f"option {n}: {str(option.get('title') or '').strip()[: dm.MAX_TITLE]}")
        for label in ("pros", "cons"):
            points = option.get(label)
            for point in points[: dm.MAX_POINTS] if isinstance(points, list) else []:
                lines.append(f"  {label}: {str(point).strip()[: dm.MAX_POINT]}")
    chosen = values.get("chosen")
    if isinstance(chosen, int) and not isinstance(chosen, bool):
        lines.append(f"chosen option: {chosen + 1}")
    return "\n".join(lines)


def has_text(values: dict[str, Any]) -> bool:
    """Whether there is anything to review: a title or one of the texts."""
    return any(str(values.get(k) or "").strip() for k in ("title", "context", "decision", "consequences"))


def suggest(provider: LLMProvider, values: dict[str, Any]) -> dict[str, Any]:
    """The answer of `POST /api/projects/{pid}/decisions/testability`; nothing is stored (ADR-0209)."""
    outcome: dict[str, Any] = {"ok": True, "error": None, "dropped": 0, "usage": {}}
    assumptions: list[dm.Assumption] = []
    falsification = ""
    try:
        data, outcome["usage"] = provider.complete_tool(
            in_language(TESTABILITY_SYSTEM_PROMPT), suggestion_input(values), localized_tool(TESTABILITY_TOOL)
        )
        data = data if isinstance(data, dict) else {}
        raw = data.get("assumptions")
        assumptions, outcome["dropped"] = clean_assumptions(raw if isinstance(raw, list) else [])
        text = data.get("falsification")
        falsification = text.strip()[: dm.MAX_LONG_TEXT] if isinstance(text, str) else ""
        if not assumptions and not falsification:
            outcome.update(ok=False, error="The answer named no usable assumption.")
    except Exception as exc:  # the editor shows the error; nothing was written
        outcome.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    return {"assumptions": [a.model_dump() for a in assumptions], "falsification": falsification, "llm": outcome}
