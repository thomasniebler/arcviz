"""LLM architecture review: one structured model call, validated into findings next to the rule checks.

Spec: docs/architecture/specs/llm-review.md (ADR-0023, ADR-0024). The model answers by calling the tool
`submit_review`; its arguments are validated finding by finding and every reference is checked against
the diagram. A failed call or unusable output never fails the review: the rule findings remain.
"""

from __future__ import annotations

from typing import Any, cast

from . import rules
from .i18n import N_, _, language, language_name
from .llm import LLMProvider, ToolSpec
from .model import Diagram
from .rules import CATEGORIES, SEVERITY_ORDER, Category, Finding, Severity

REVIEW_CONTEXT_BUDGET = 100_000  # characters of Diagram.summary() in the prompt before containers collapse
PARTIAL_NOTE = (
    "Note: this diagram is too large to show completely, so the largest containers above are collapsed to their "
    "element count. get_diagram is NOT available in this review. Do not report findings about the hidden contents "
    "of collapsed containers; you may say in the summary that they were not reviewed."
)

EXAMPLE_SUGGESTION = N_("Füge vor service-a ein Gateway hinzu und leite die Kante e3 darüber.")
CONTRADICTS_PREFIX = N_("Widerspricht ADR-<n>:")


def in_language(value: Any, *german_texts: str) -> Any:
    """The LLM-facing prompt or tool schema `value` (written for German) in the request language.

    The constants stay the German-default texts (tests pin them). Under another language "German" names the request
    language (`i18n.language_name()`) and each of `german_texts` (N_ marked examples inside the value) is translated."""
    if language() == "de":
        return value
    if isinstance(value, str):
        for text in german_texts:
            value = value.replace(text, _(text))
        return value.replace("German", language_name())
    if isinstance(value, list):
        return [in_language(v, *german_texts) for v in value]
    if isinstance(value, dict):
        return {k: in_language(v, *german_texts) for k, v in value.items()}
    return value


def localized_tool(spec: ToolSpec) -> ToolSpec:
    """`spec` with its descriptions in the request language (see `in_language`)."""
    if language() == "de":
        return spec
    return ToolSpec(spec.name, in_language(spec.description), in_language(spec.parameters))


MAX_LLM_FINDINGS = 20
MESSAGE_LIMIT = 600  # characters per message and per suggestion
SUMMARY_LIMIT = 1_500

REVIEW_TOOL = ToolSpec(
    "submit_review",
    "Submit the architecture review: an overall summary and the findings (strengths, risks, suggestions).",
    {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Overall assessment in two or three German sentences."},
            "findings": {
                "type": "array",
                "maxItems": MAX_LLM_FINDINGS,
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "severity": {"type": "string", "enum": list(reversed(SEVERITY_ORDER))},
                        "message": {"type": "string", "description": "The finding in one or two German sentences."},
                        "elements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ids (in square brackets in the diagram) of the elements it concerns.",
                        },
                        "edges": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ids (e1, e2, ...) of the edges it concerns.",
                        },
                        "suggestion": {
                            "type": "string",
                            "description": "For risks and suggestions: a German instruction to the diagram assistant "
                            "that changes the diagram to fix it. Omit if no diagram change helps.",
                        },
                    },
                    "required": ["category", "severity", "message", "elements"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "findings"],
        "additionalProperties": False,
    },
)

REVIEW_SYSTEM_PROMPT = """You review software architecture diagrams made with Arcviz.

You get the diagram as an indented tree of elements (ids in square brackets, kinds in «»,
properties in braces) plus its edges (ids e1, e2, ...), and the findings of deterministic rule checks
that were already reported. Review the architecture as an experienced architect would:
- strengths: what is already good (category "strength", severity "info");
- risks: what can fail, leak or not scale (category "risk", severity low, medium or high);
- suggestions: improvements without an acute risk (category "suggestion").

Rules:
- Do not repeat the rule findings; add what they do not cover.
- Reference elements only by the ids shown in square brackets and edges by their e-ids. Only
  reference what exists. A remark about the whole diagram may have no elements.
- Judge only what the diagram shows; say what is missing rather than guessing details.
- Write message, suggestion and summary in German. Keep each message to one or two sentences.
- A suggestion is an instruction to the diagram assistant that changes the diagram, naming the
  elements by id, e.g. "Füge vor service-a ein Gateway hinzu und leite die Kante e3 darüber."
- Report at most 12 findings, the most important first.
- Accepted architecture decisions, when listed, are binding: when the diagram contradicts one, report a risk
  finding (severity medium or high) whose message starts with "Widerspricht ADR-<n>:" and says how.
- Answer by calling submit_review exactly once. Do not answer in text.
"""


def is_partial(diagram: Diagram) -> bool:
    """True if the diagram does not fit the review prompt and containers are shown collapsed."""
    return len(diagram.summary()) > REVIEW_CONTEXT_BUDGET


def review_input(diagram: Diagram, rule_findings: list[Finding], decisions: str | None = None) -> str:
    """The user message of the review call: diagram type, the diagram, and the rule findings.

    The model has no tool to fetch collapsed containers here, so a collapsed summary carries PARTIAL_NOTE."""
    lines = [
        f"Diagram type: {rules.diagram_type(diagram)}",
        "",
        "Diagram:",
        diagram.summary(budget=REVIEW_CONTEXT_BUDGET),
        "",
    ]
    if is_partial(diagram):
        lines += [PARTIAL_NOTE, ""]
    if decisions:  # the project's accepted decisions (spec: decision-support.md, ADR-0168)
        lines += ["Accepted architecture decisions of the project (binding):", decisions, ""]
    if rule_findings:
        lines.append("Rule findings (already reported, do not repeat):")
        for f in rule_findings:
            refs = ", ".join(f.elements + f.edges)
            lines.append(f"- [{f.severity}] {f.message}" + (f" (elements: {refs})" if refs else ""))
    else:
        lines.append("Rule findings: none")
    return "\n".join(lines)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _strings(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _finding(raw: Any, diagram: Diagram, number: int) -> Finding | None:
    """A validated LLM finding, or None if `raw` is unusable."""
    if not isinstance(raw, dict):
        return None
    category, severity = raw.get("category"), raw.get("severity")
    message = _text(raw.get("message"), MESSAGE_LIMIT)
    if category not in CATEGORIES or severity not in SEVERITY_ORDER or message is None:
        return None
    if category == "strength":
        severity = "info"
    elif severity == "info":
        severity = "low"  # risks and suggestions are at least low
    elements: list[str] = []
    for ref in _strings(raw.get("elements")):
        if diagram.interaction is not None:  # sequence diagrams: lifelines and fragments by their ids
            element_id: str | None = ref if diagram.has_element(ref) else None
        else:
            node = diagram.find_node(ref)
            element_id = node.id if node is not None else None
        if element_id is not None and element_id not in elements:
            elements.append(element_id)
    edge_ids = {e.id for e in diagram.edges}
    edges = list(dict.fromkeys(e for e in _strings(raw.get("edges")) if e in edge_ids))
    suggestion = None if category == "strength" else _text(raw.get("suggestion"), MESSAGE_LIMIT)
    return Finding(
        id=f"llm-{number}",
        source="llm",
        category=cast(Category, category),
        severity=cast(Severity, severity),
        message=message,
        elements=elements,
        edges=edges,
        suggestion=suggestion,
    )


def parse_review(data: Any, diagram: Diagram) -> tuple[str | None, list[Finding], int]:
    """Validate the arguments of a `submit_review` call: (summary, findings, number of dropped findings).

    Raises ValueError if `data` is not an object with a `findings` list.
    """
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("The review has no list of findings.")
    findings: list[Finding] = []
    dropped = 0
    for raw in data["findings"]:
        finding = _finding(raw, diagram, len(findings) + 1) if len(findings) < MAX_LLM_FINDINGS else None
        if finding is None:
            dropped += 1
        else:
            findings.append(finding)
    return _text(data.get("summary"), SUMMARY_LIMIT), findings, dropped


def review(provider: LLMProvider, diagram: Diagram, decisions: str | None = None) -> dict[str, Any]:
    """Rule checks plus the LLM review of `diagram`, as the JSON answer of `POST /review`; `decisions` are the accepted
    decisions of its project (`decision_model.decision_lines`), which the review checks the diagram against."""
    rule_findings = rules.run_checks(diagram)
    summary: str | None = None
    llm_findings: list[Finding] = []
    outcome: dict[str, Any] = {"ok": True, "error": None, "dropped": 0, "usage": {}, "partial": is_partial(diagram)}
    try:
        data, outcome["usage"] = provider.complete_tool(
            in_language(REVIEW_SYSTEM_PROMPT, EXAMPLE_SUGGESTION, CONTRADICTS_PREFIX),
            review_input(diagram, rule_findings, decisions),
            localized_tool(REVIEW_TOOL),
        )
        summary, llm_findings, outcome["dropped"] = parse_review(data, diagram)
    except Exception as exc:  # a failed review still returns the rule findings
        outcome.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    findings = sorted(rule_findings + llm_findings, key=rules.sort_key)
    return {
        "diagram_type": rules.diagram_type(diagram),
        "summary": summary,
        "findings": [f.model_dump() for f in findings],
        "llm": outcome,
    }
