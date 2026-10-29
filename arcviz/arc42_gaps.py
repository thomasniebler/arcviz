"""Gap analysis of a project against the twelve arc42 sections (spec: docs/architecture/specs/arc42-gap-analysis.md,
ADR-0211).

`analyse` is pure and deterministic: it reads the document that `arc42.build` assembled (a section without a source says
so with a coded `arc42.Gap`) and the project, adds the checks that need a look into the model (a vision without goals, a
quality goal no scenario serves, an accepted decision without assumptions), and gives every gap a severity and a next
step the UI can follow. It never reads a model's opinion and never writes anything.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .arc42 import Document, Section
from .decision_model import adr_label
from .i18n import _
from .project_model import active
from .projects import Project

SEVERITIES = ("high", "medium", "low")
_RANK = {name: n for n, name in enumerate(SEVERITIES)}


def tab(target: str, **more: str) -> dict[str, str]:
    return {"kind": "tab", "target": target, **more}


def new_diagram(diagram_type: str) -> dict[str, str]:
    return {"kind": "new-diagram", "diagram_type": diagram_type}


# code -> (severity, next step); the order is the order of importance inside a section (spec table)
CATALOG: dict[str, tuple[str, dict[str, str]]] = {
    "vision-missing": ("high", tab("vision")),
    "vision-without-goals": ("medium", tab("vision")),
    "quality-goals-missing": ("high", tab("drivers")),
    "stakeholders-missing": ("high", tab("stakeholders")),
    "constraints-missing": ("medium", tab("drivers")),
    "context-missing": ("medium", new_diagram("deployment")),
    "strategy-missing": ("medium", tab("decisions")),
    "building-blocks-missing": ("high", {"kind": "model"}),
    "runtime-missing": ("medium", new_diagram("sequence")),
    "deployment-missing": ("medium", new_diagram("deployment")),
    "crosscutting-missing": ("low", tab("documentation", section="08")),
    "decisions-missing": ("high", tab("decisions")),
    "decision-without-assumptions": ("low", tab("decisions")),
    "scenarios-missing": ("high", tab("scenarios")),
    "quality-goal-without-scenario": ("medium", tab("scenarios")),
    "risks-missing": ("medium", tab("drivers")),
    "glossary-missing": ("low", {"kind": "model"}),
}
_ORDER = {code: n for n, code in enumerate(CATALOG)}


class Gap(BaseModel):
    id: str  # a key of CATALOG
    section: str  # "01".."12"
    severity: str  # SEVERITIES
    message: str  # in the request language
    action: dict[str, str]  # the next step: {"kind": "tab" | "model" | "new-diagram", ...}
    items: list[str] = Field(default_factory=list)  # ids of the objects it is about


class SectionStatus(BaseModel):
    id: str
    title: str
    status: str  # "complete" | "partial" | "missing"
    gaps: list[Gap]


class GapReport(BaseModel):
    sections: list[SectionStatus]
    complete: int  # sections without a gap
    total: int
    next: Gap | None  # the most important gap, earliest section first


def _gap(code: str, section: str, message: str, items: list[str] | None = None, **action: str) -> Gap:
    severity, step = CATALOG[code]
    return Gap(
        id=code, section=section, severity=severity, message=message, action={**step, **action}, items=items or []
    )


def _deep_gaps(section_id: str, project: Project) -> list[Gap]:
    """The gaps of a section that only a look into the model finds."""
    found: list[Gap] = []
    if section_id == "01":
        vision = project.vision
        if vision is not None and not vision.is_empty() and not vision.goals:
            found.append(_gap("vision-without-goals", "01", _("Die Vision nennt keine Ziele.")))
    elif section_id == "09":
        bare = [d for d in project.decisions if d.status == "accepted" and not d.assumptions]
        if bare:
            names = ", ".join(adr_label(d.id) for d in bare)
            found.append(
                _gap(
                    "decision-without-assumptions",
                    "09",
                    _("Angenommene Entscheidungen ohne Annahmen und Test: {names}.", names=names),
                    [d.id for d in bare],
                    item=bare[0].id,
                )
            )
    elif section_id == "10":
        served = {s.driver for s in active(project.scenarios) if s.driver}
        bare_goals = [d for d in active(project.drivers) if d.kind == "quality_goal" and d.id not in served]
        if bare_goals:
            titles = ", ".join(d.title for d in bare_goals)
            found.append(
                _gap(
                    "quality-goal-without-scenario",
                    "10",
                    _("Qualitätsziele ohne Szenario: {titles}.", titles=titles),
                    [d.id for d in bare_goals],
                )
            )
    return found


def _status(section: Section, gaps: list[Gap]) -> str:
    if not section.blocks:
        return "missing"
    return "partial" if gaps else "complete"


def analyse(doc: Document, project: Project) -> GapReport:
    """The twelve sections of `doc` with their gaps, most important first, and the next step overall."""
    statuses: list[SectionStatus] = []
    for section in doc.sections:
        gaps = [_gap(g.code, section.id, g.text) for g in section.gap_items if g.code in CATALOG]
        gaps += _deep_gaps(section.id, project)
        gaps.sort(key=lambda g: (_RANK[g.severity], _ORDER[g.id]))
        statuses.append(SectionStatus(id=section.id, title=section.title, status=_status(section, gaps), gaps=gaps))
    everything = [(g, n) for n, s in enumerate(statuses) for g in s.gaps]
    first = min(everything, key=lambda pair: (_RANK[pair[0].severity], pair[1]), default=None)
    return GapReport(
        sections=statuses,
        complete=sum(1 for s in statuses if s.status == "complete"),
        total=len(statuses),
        next=first[0] if first else None,
    )
