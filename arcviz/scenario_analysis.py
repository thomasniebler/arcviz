"""What-if analysis of a quality scenario (spec: docs/architecture/specs/decision-support.md, ADR-0167).

One `LLMProvider.complete_tool` call offering only `submit_scenario_analysis`, over the project digest of the project
review plus the drivers with their core scenarios, the stakeholders, the accepted decisions and the scenario in its six
parts. The answer is validated item by item and never stored: a decision taken from it is (ADR-0167). Never raises
because of the model: a failed call answers `llm.ok = false` and no options.
"""

from __future__ import annotations

from typing import Any

from . import decision_model as dm
from .llm import LLMProvider, ToolSpec
from .project_model import active
from .project_review import ProjectSnapshot, digest_lines
from .review import in_language, localized_tool

MAX_OPTIONS = 4
EFFECTS = ("supports", "at_risk", "violates", "neutral")
SUMMARY_LIMIT = 1_500
REASON_LIMIT = 400
INSTRUCTION_LIMIT = 600
MAX_LIST = 8

_STRINGS = {"type": "array", "items": {"type": "string"}, "maxItems": MAX_LIST}

ANALYSIS_TOOL = ToolSpec(
    "submit_scenario_analysis",
    "Submit the what-if analysis of the scenario: its effect on the drivers, the affected elements, sensitivity "
    "points, trade-offs and the options to handle it.",
    {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "What happens in this scenario today, 2–4 German sentences."},
            "drivers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "driver": {"type": "string", "description": "Id of the driver (e.g. d1)."},
                        "effect": {"type": "string", "enum": list(EFFECTS)},
                        "reason": {"type": "string"},
                    },
                    "required": ["driver", "effect", "reason"],
                    "additionalProperties": False,
                },
            },
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "element": {"type": "string", "description": "Id of the project element."},
                        "reason": {"type": "string"},
                    },
                    "required": ["element", "reason"],
                    "additionalProperties": False,
                },
            },
            "sensitivity_points": {**_STRINGS, "description": "Decisions or elements the response depends on."},
            "tradeoffs": {**_STRINGS, "description": "Where improving one driver costs another."},
            "options": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_OPTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "pros": _STRINGS,
                        "cons": _STRINGS,
                        "instruction": {
                            "type": "string",
                            "description": "Optional: a German instruction to the diagram assistant that makes this "
                            "option visible in a diagram, naming the diagram and elements by id.",
                        },
                    },
                    "required": ["title", "description", "pros", "cons"],
                    "additionalProperties": False,
                },
            },
            "recommended": {"type": "integer", "description": "Index (from 0) of the option you recommend."},
        },
        "required": ["summary", "drivers", "elements", "options", "recommended"],
        "additionalProperties": False,
    },
)

ANALYSIS_SYSTEM_PROMPT = """You analyse "what if" scenarios for a software architecture documented with Arcviz.

You get the project (diagrams, shared model elements and relations), its architecture drivers (most important first,
each with its core scenarios), its stakeholders, its accepted architecture decisions, and one quality scenario in the
six parts of ATAM (source, stimulus, environment, artifact, response, response measure). Analyse the scenario as an
experienced architect in an ATAM evaluation would:
- what happens today when the scenario occurs, judged only by what the project shows;
- the effect on every driver it touches: supports, at_risk, violates or neutral, with a short reason;
- the elements it affects (ids in square brackets), sensitivity points and trade-offs between drivers;
- 2 to 4 options to handle it (including "keep as is" when that is reasonable), each with pros and cons in terms of
  the drivers and stakeholders; recommend one.

Rules:
- Accepted decisions are binding: when an option would contradict one, name it (ADR-<n>) among its cons.
- Reference drivers and elements only by the ids shown. Do not invent components; say what is missing instead.
- Write every text in German, concise: one or two sentences per reason, pros and cons as short phrases.
- Answer by calling submit_scenario_analysis exactly once. Do not answer in text.
"""


def analysis_input(snapshot: ProjectSnapshot, scenario: dm.Scenario) -> str:
    """The user message: the project digest, the architecture context and the scenario."""
    project = snapshot.project
    lines = digest_lines(snapshot)
    drivers = dm.drivers_with_core_scenarios(project.drivers, project.scenarios)
    lines += ["Architecture drivers (most important first):", drivers or "(none)", ""]
    stakeholders = dm.stakeholder_lines(project.stakeholders, drivers=project.drivers)
    lines += ["Stakeholders:", stakeholders or "(none)", ""]
    decisions = dm.decision_lines(project.decisions)
    lines += ["Accepted decisions:", decisions or "(none)", ""]
    driver = next((d for d in active(project.drivers) if d.id == scenario.driver), None)
    serves = f" (serves driver [{driver.id}] {driver.title})" if driver else ""
    lines += [f"Scenario to analyse{serves}:", dm.scenario_text(scenario)]
    if scenario.elements:
        lines.append(f"Concerned elements: {', '.join(scenario.elements)}")
    return "\n".join(lines)


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _texts(value: Any, limit: int = dm.MAX_POINT, count: int = MAX_LIST) -> list[str]:
    if not isinstance(value, list):
        return []
    return [t for t in (_text(v, limit) for v in value[:count]) if t]


def parse_analysis(data: Any, snapshot: ProjectSnapshot) -> tuple[dict[str, Any], int]:
    """The validated analysis and how many items were dropped (unknown ids, missing titles, bad effects)."""
    project = snapshot.project
    data = data if isinstance(data, dict) else {}
    dropped = 0
    driver_ids = {d.id for d in active(project.drivers)}
    element_ids = {e.id for e in project.elements}
    drivers = []
    for raw in data.get("drivers") or []:
        if not isinstance(raw, dict) or raw.get("driver") not in driver_ids or raw.get("effect") not in EFFECTS:
            dropped += 1
            continue
        drivers.append(
            {"driver": raw["driver"], "effect": raw["effect"], "reason": _text(raw.get("reason"), REASON_LIMIT)}
        )
    elements = []
    for raw in data.get("elements") or []:
        if not isinstance(raw, dict) or raw.get("element") not in element_ids:
            dropped += 1
            continue
        elements.append({"element": raw["element"], "reason": _text(raw.get("reason"), REASON_LIMIT)})
    options = []
    for raw in (data.get("options") or [])[:MAX_OPTIONS]:
        title = _text(raw.get("title"), dm.MAX_TITLE) if isinstance(raw, dict) else ""
        if not title:
            dropped += 1
            continue
        option = {
            "title": title,
            "description": _text(raw.get("description"), dm.MAX_OPTION_DESCRIPTION),
            "pros": _texts(raw.get("pros"), count=dm.MAX_POINTS),
            "cons": _texts(raw.get("cons"), count=dm.MAX_POINTS),
        }
        instruction = _text(raw.get("instruction"), INSTRUCTION_LIMIT)
        if instruction:
            option["instruction"] = instruction
        options.append(option)
    recommended = data.get("recommended")
    if not isinstance(recommended, int) or isinstance(recommended, bool) or not 0 <= recommended < len(options):
        recommended = None
    return (
        {
            "summary": _text(data.get("summary"), SUMMARY_LIMIT) or None,
            "drivers": drivers,
            "elements": elements,
            "sensitivity_points": _texts(data.get("sensitivity_points")),
            "tradeoffs": _texts(data.get("tradeoffs")),
            "options": options,
            "recommended": recommended,
        },
        dropped,
    )


EMPTY: dict[str, Any] = {
    "summary": None,
    "drivers": [],
    "elements": [],
    "sensitivity_points": [],
    "tradeoffs": [],
    "options": [],
    "recommended": None,
}


def analyse(provider: LLMProvider, snapshot: ProjectSnapshot, scenario: dm.Scenario) -> dict[str, Any]:
    """The answer of `POST /api/projects/{pid}/scenarios/{sid}/analysis`; nothing is stored (ADR-0167)."""
    outcome: dict[str, Any] = {"ok": True, "error": None, "dropped": 0, "usage": {}}
    analysis = dict(EMPTY)
    try:
        data, outcome["usage"] = provider.complete_tool(
            in_language(ANALYSIS_SYSTEM_PROMPT), analysis_input(snapshot, scenario), localized_tool(ANALYSIS_TOOL)
        )
        analysis, outcome["dropped"] = parse_analysis(data, snapshot)
        if not analysis["options"]:
            outcome.update(ok=False, error="The analysis named no options.")
    except Exception as exc:  # the dialog shows the error; nothing was written
        outcome.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    return {"project": snapshot.project.id, "scenario": scenario.model_dump(), **analysis, "llm": outcome}
