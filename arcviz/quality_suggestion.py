"""Suggest the ISO/IEC 25010 characteristics of a quality scenario (spec: docs/architecture/specs/iso25010-qualities.md,
ADR-0202).

One `LLMProvider.complete_tool` call offering only `submit_qualities` over the scenario text as the user typed it. The
answer is validated (`quality_model.clean_qualities`: known names, each once, at most two) and never stored: the user
confirms it in the editor, where every characteristic stays selectable. Never raises because of the model: a failed call
or an answer without a valid characteristic gives `llm.ok = false` and no suggestion.
"""

from __future__ import annotations

from typing import Any

from . import decision_model as dm
from .llm import LLMProvider, ToolSpec
from .quality_model import MAX_QUALITIES, QUALITY_CHARACTERISTICS, QUALITY_MEANING, clean_qualities
from .review import in_language, localized_tool

REASON_LIMIT = 400

QUALITIES_TOOL = ToolSpec(
    "submit_qualities",
    f"Submit the one or at most {MAX_QUALITIES} ISO/IEC 25010 quality characteristics the scenario is about.",
    {
        "type": "object",
        "properties": {
            "qualities": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_QUALITIES,
                "items": {"type": "string", "enum": list(QUALITY_CHARACTERISTICS)},
                "description": "The main characteristic first; a second one only when the scenario clearly needs it.",
            },
            "reason": {"type": "string", "description": "One or two German sentences: why these characteristics."},
        },
        "required": ["qualities", "reason"],
        "additionalProperties": False,
    },
)

QUALITIES_SYSTEM_PROMPT = (
    """You classify quality scenarios of a software architecture by the product quality model ISO/IEC 25010 (2023).

You get one scenario, possibly incomplete, in the parts of ATAM (source, stimulus, environment, artifact, response,
response measure). Choose the one characteristic it is mainly about, and a second one only when the scenario clearly
cannot be understood without it. The characteristics:
"""
    + "\n".join(f"- {name}: {meaning}" for name, meaning in QUALITY_MEANING.items())
    + """

Rules:
- Decide from the response and its measure first (what is being protected or achieved), then from the stimulus.
- Use only the names above. Prefer one characteristic; never more than two.
- Write the reason in German, one or two sentences.
- Answer by calling submit_qualities exactly once. Do not answer in text.
"""
)


def suggestion_input(values: dict[str, Any]) -> str:
    """The user message: the scenario as typed (title, kind, the six parts), each bounded."""
    lines = [f"Title: {str(values.get('title') or '').strip()[: dm.MAX_TITLE]}"]
    if values.get("kind") in dm.SCENARIO_KINDS:
        lines.append(f"Kind: {values['kind']}")
    for part in dm.SCENARIO_PARTS:
        text = str(values.get(part) or "").strip()[: dm.MAX_PART]
        if text:
            lines.append(f"{part}: {text}")
    return "\n".join(lines)


def has_text(values: dict[str, Any]) -> bool:
    """Whether there is anything to classify: a title or one of the parts."""
    return any(str(values.get(k) or "").strip() for k in ("title", *dm.SCENARIO_PARTS))


def suggest(provider: LLMProvider, values: dict[str, Any]) -> dict[str, Any]:
    """The answer of `POST /api/projects/{pid}/scenarios/qualities`; nothing is stored (ADR-0202)."""
    outcome: dict[str, Any] = {"ok": True, "error": None, "dropped": 0, "usage": {}}
    qualities: list[str] = []
    reason = None
    try:
        data, outcome["usage"] = provider.complete_tool(
            in_language(QUALITIES_SYSTEM_PROMPT), suggestion_input(values), localized_tool(QUALITIES_TOOL)
        )
        data = data if isinstance(data, dict) else {}
        raw = data.get("qualities")
        qualities, outcome["dropped"] = clean_qualities(raw if isinstance(raw, list) else [])
        text = data.get("reason")
        reason = text.strip()[:REASON_LIMIT] if isinstance(text, str) and text.strip() else None
        if not qualities:
            outcome.update(ok=False, error="The answer named no valid ISO 25010 characteristic.")
            reason = None
    except Exception as exc:  # the editor shows the error; nothing was written
        outcome.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    return {"qualities": qualities, "reason": reason, "llm": outcome}
