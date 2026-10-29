"""An advisory model pass over the arc42 sections (spec: docs/architecture/specs/arc42-gap-analysis.md, ADR-0211).

One `LLMProvider.complete_tool` call offering only `submit_gap_review` over a bounded digest of the arc42 document. The
remarks (a section is thin, inconsistent with another, outdated) are validated one by one and never stored, never
counted in the completeness of `arc42_gaps`. Never raises because of the model: a failed call is `llm.ok = false`.
"""

from __future__ import annotations

from typing import Any

from .arc42 import Document
from .arc42_export import markdown_section
from .llm import LLMProvider, ToolSpec
from .project_model import ARC42_SECTIONS
from .review import in_language, localized_tool

REMARK_KINDS = ("thin", "inconsistent", "outdated")
MAX_REMARKS = 3  # per section
MAX_TEXT = 300
DIGEST_BUDGET = 12_000  # characters of the digest in all
SECTION_LIMIT = DIGEST_BUDGET // len(ARC42_SECTIONS)

GAP_REVIEW_TOOL = ToolSpec(
    "submit_gap_review",
    "Submit remarks about the content of the arc42 sections: thin, inconsistent with another section, or outdated.",
    {
        "type": "object",
        "properties": {
            "remarks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "enum": list(ARC42_SECTIONS)},
                        "kind": {"type": "string", "enum": list(REMARK_KINDS)},
                        "text": {
                            "type": "string",
                            "description": "One sentence in German: what is wrong and what to add.",
                        },
                        "conflicts_with": {"type": "string", "enum": list(ARC42_SECTIONS)},
                    },
                    "required": ["section", "kind", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["remarks"],
        "additionalProperties": False,
    },
)

GAP_REVIEW_SYSTEM_PROMPT = f"""You review the arc42 architecture documentation of a software project.

You get the twelve sections as assembled from the project's model, each cut to a limit; the lines in italics name what
the model does not say. Do not repeat those gaps. Look at the content that is there and report, per section, at most
{MAX_REMARKS} remarks of one of these kinds:
- thin: the section has content but too little to be useful (a vision that is a slogan, a goal without a measure);
- inconsistent: it contradicts another section; name that section in conflicts_with;
- outdated: it names things the other sections no longer have.

Rules:
- Only remark on what the text shows; never invent facts about the system.
- Write each remark in German as one sentence that says what to add or change.
- Report nothing when you find nothing: an empty list is a good answer.
- Answer by calling submit_gap_review exactly once. Do not answer in text.
"""


def digest(doc: Document) -> str:
    """The user message: every section as Markdown, cut to `SECTION_LIMIT`, in arc42 order."""
    parts = []
    for section in doc.sections:
        text = markdown_section(section, doc, lambda diagram: "").strip()
        parts.append(text if len(text) <= SECTION_LIMIT else text[: SECTION_LIMIT - 1] + "…")
    return "\n\n".join(parts)[:DIGEST_BUDGET]


def clean_remarks(raw: list[Any]) -> tuple[list[dict[str, str]], int]:
    """The usable remarks of a model answer and how many items were dropped."""
    kept: list[dict[str, str]] = []
    per_section: dict[str, int] = {}
    dropped = 0
    for item in raw:
        section = item.get("section") if isinstance(item, dict) else None
        kind = item.get("kind") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        text = " ".join(text.split()) if isinstance(text, str) else ""
        if (
            section not in ARC42_SECTIONS
            or kind not in REMARK_KINDS
            or not text
            or per_section.get(section, 0) >= MAX_REMARKS
        ):
            dropped += 1
            continue
        per_section[section] = per_section.get(section, 0) + 1
        remark = {"section": section, "kind": kind, "text": text[:MAX_TEXT]}
        other = item.get("conflicts_with")
        if other in ARC42_SECTIONS and other != section:
            remark["conflicts_with"] = other
        kept.append(remark)
    return kept, dropped


def review(provider: LLMProvider, doc: Document) -> dict[str, Any]:
    """The answer of `POST /api/projects/{pid}/arc42/gaps/review`; nothing is stored."""
    outcome: dict[str, Any] = {"ok": True, "error": None, "dropped": 0, "usage": {}}
    remarks: list[dict[str, str]] = []
    try:
        data, outcome["usage"] = provider.complete_tool(
            in_language(GAP_REVIEW_SYSTEM_PROMPT), digest(doc), localized_tool(GAP_REVIEW_TOOL)
        )
        raw = data.get("remarks") if isinstance(data, dict) else None
        if isinstance(raw, list):
            remarks, outcome["dropped"] = clean_remarks(raw)
        else:
            outcome.update(ok=False, error="The answer had no list of remarks.")
    except Exception as exc:  # the panel shows the error; nothing was written
        outcome.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    return {"remarks": remarks, "llm": outcome}
