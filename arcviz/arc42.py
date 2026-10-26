"""arc42 documentation of a project, assembled from its model (spec: docs/architecture/specs/arc42-export.md, ADR-0207).

`build` is pure: it turns a project, its member diagrams, its requirements and its review findings into a tree of twelve
sections of plain-text blocks. Nothing here renders: `arc42_html` and `arc42_export` write the tree as an HTML page,
Markdown, AsciiDoc and docx. The tree is deterministic (two builds of one state are equal) and never invents text: a
section without a source says so in `gaps`. Texts of people and models are plain text; every writer escapes them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .decision_model import adr_label
from .i18n import N_, _
from .model import Diagram
from .project_model import ARC42_SECTIONS, Element, active, ranked_drivers
from .project_review import ProjectFinding, ProjectSnapshot, is_overview
from .projects import Project
from .quality_model import QUALITY_CHARACTERISTICS
from .requirement_model import RequirementBox

MAX_ROWS = 100  # rows of one table: the rest is counted, not listed
MAX_TEXT = 400  # characters of a description in a table cell

TITLES = {
    "01": N_("Einführung und Ziele"),
    "02": N_("Randbedingungen"),
    "03": N_("Kontextabgrenzung"),
    "04": N_("Lösungsstrategie"),
    "05": N_("Bausteinsicht"),
    "06": N_("Laufzeitsicht"),
    "07": N_("Verteilungssicht"),
    "08": N_("Querschnittliche Konzepte"),
    "09": N_("Architekturentscheidungen"),
    "10": N_("Qualitätsanforderungen"),
    "11": N_("Risiken und technische Schulden"),
    "12": N_("Glossar"),
}
LEVEL_TEXT = {"high": N_("hoch"), "medium": N_("mittel"), "low": N_("niedrig")}
STRUCTURE_TYPES = ("component",)
DEPLOYMENT_TYPES = ("deployment",)
RUNTIME_TYPES = ("sequence",)


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class Bullets:
    items: tuple[str, ...]


@dataclass(frozen=True)
class Heading:
    level: int  # 3 and 4: the sections themselves are level 2
    text: str


@dataclass(frozen=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Figure:
    diagram: str  # id of a member diagram, in `Document.diagrams`
    title: str


Block = Paragraph | Bullets | Heading | Table | Figure


@dataclass
class Section:
    id: str  # "01".."12"
    title: str
    blocks: list[Block] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)  # what the model does not say (German, shown as a hint)


@dataclass
class Document:
    project: str  # project id
    title: str
    sections: list[Section]
    diagrams: dict[str, Diagram]  # the figures' diagrams


def single(text: str) -> str:
    """One line of text: whitespace collapsed (table cells and list items)."""
    return " ".join(str(text).split())


def short(text: str, limit: int = MAX_TEXT) -> str:
    one = single(text)
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def paragraphs(text: str) -> list[Paragraph]:
    """The paragraphs of a note: blocks separated by blank lines."""
    return [Paragraph(" ".join(part.split())) for part in text.replace("\r", "").split("\n\n") if part.strip()]


def _level(value: str) -> str:
    return _(LEVEL_TEXT[value]) if value in LEVEL_TEXT else value


def _capped(rows: Sequence[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    if len(rows) <= MAX_ROWS:
        return tuple(rows)
    rest = len(rows) - MAX_ROWS
    filler = ("", *(("",) * (len(rows[0]) - 2)))
    return (*rows[:MAX_ROWS], (_("… und {n} weitere", n=rest), *filler))


def build(
    snapshot: ProjectSnapshot, findings: Sequence[ProjectFinding] = (), requirements: RequirementBox | None = None
) -> Document:
    """The twelve sections of `snapshot`'s project; `findings` are its deterministic review findings."""
    project = snapshot.project
    diagrams = {d.id: d for _m, d in snapshot.members}
    requirements = requirements if requirements is not None else snapshot.requirements
    sections = [
        _introduction(project, requirements),
        _constraints(project, requirements),
        _context(project, snapshot),
        _strategy(project),
        _building_blocks(project, snapshot),
        _runtime(project, snapshot),
        _deployment(project, snapshot),
        _crosscutting(project),
        _decisions(project),
        _quality(project),
        _risks(project, findings),
        _glossary(project),
    ]
    for section in sections:
        note = project.arc42_notes.get(section.id, "")
        if note:
            section.blocks = [*paragraphs(note), *section.blocks]
    return Document(project=project.id, title=project.title, sections=sections, diagrams=diagrams)


def _section(number: str) -> Section:
    return Section(number, _(TITLES[number]))


def _gap_if_empty(section: Section, text: str, *, has_note: bool = False) -> Section:
    if not section.blocks and not has_note:
        section.gaps.append(text)
    return section


def _introduction(project: Project, requirements: RequirementBox | None) -> Section:
    section = _section("01")
    if project.description.strip():
        section.blocks += paragraphs(project.description)
    vision = project.vision
    if vision is not None and not vision.is_empty():
        if vision.statement:
            section.blocks += [Heading(3, _("Vision")), Paragraph(single(vision.statement))]
        if vision.goals:
            section.blocks += [Heading(3, _("Ziele")), Bullets(tuple(single(g) for g in vision.goals))]
        if vision.non_goals:
            section.blocks += [Heading(3, _("Nicht-Ziele")), Bullets(tuple(single(g) for g in vision.non_goals))]
    else:
        section.gaps.append(_("Das Projekt hat keine Vision."))
    goals = [d for d in ranked_drivers(active(project.drivers)) if d.kind == "quality_goal"]
    if goals:
        rows = [(d.title, _level(d.importance), _level(d.risk), short(d.description)) for d in goals]
        section.blocks += [
            Heading(3, _("Qualitätsziele")),
            Table((_("Qualitätsziel"), _("Wichtigkeit"), _("Risiko"), _("Beschreibung")), _capped(rows)),
        ]
    else:
        section.gaps.append(_("Keine Qualitätsziele als Treiber erfasst."))
    functional = [r for r in (requirements.requirements if requirements else []) if r.status == "refined"]
    if functional:
        rows = [(r.id, short(r.statement), r.kind, r.priority or "–") for r in functional if r.kind != "constraint"]
        if rows:
            section.blocks += [
                Heading(3, _("Anforderungen")),
                Table((_("Id"), _("Anforderung"), _("Art"), _("Priorität")), _capped(rows)),
            ]
    if project.stakeholders:
        rows = [(s.name, s.role or "–", short(s.concerns) or "–", _level(s.influence)) for s in project.stakeholders]
        section.blocks += [
            Heading(3, _("Stakeholder")),
            Table((_("Name"), _("Rolle"), _("Anliegen"), _("Einfluss")), _capped(rows)),
        ]
    else:
        section.gaps.append(_("Keine Stakeholder erfasst."))
    return section


def _constraints(project: Project, requirements: RequirementBox | None) -> Section:
    section = _section("02")
    rows = [
        (d.title, _level(d.importance), short(d.description) or "–")
        for d in ranked_drivers(active(project.drivers))
        if d.kind == "constraint"
    ]
    rows += [
        (r.id, "–", short(r.statement))
        for r in (requirements.requirements if requirements else [])
        if r.kind == "constraint" and r.status == "refined"
    ]
    if rows:
        section.blocks.append(Table((_("Randbedingung"), _("Wichtigkeit"), _("Beschreibung")), _capped(rows)))
    return _gap_if_empty(
        section,
        _("Keine Randbedingungen als Treiber oder Anforderung erfasst."),
        has_note=bool(project.arc42_notes.get("02")),
    )


def _members(snapshot: ProjectSnapshot, types: tuple[str, ...]) -> list[Diagram]:
    return [d for _m, d in snapshot.members if d.type in types]


def _context(project: Project, snapshot: ProjectSnapshot) -> Section:
    section = _section("03")
    overviews = [d for _m, d in snapshot.members if is_overview(d)]
    section.blocks += [Figure(d.id, d.title) for d in overviews]
    return _gap_if_empty(
        section,
        _("Keine Kontext- oder Übersichtsdiagramme und keine Notiz."),
        has_note=bool(project.arc42_notes.get("03")),
    )


def _strategy(project: Project) -> Section:
    section = _section("04")
    accepted = [d for d in project.decisions if d.status == "accepted"]
    if accepted:
        section.blocks.append(
            Bullets(tuple(single(f"{adr_label(d.id)} {d.title}: {short(d.decision, 200)}") for d in accepted))
        )
    return _gap_if_empty(
        section, _("Keine angenommenen Entscheidungen und keine Notiz."), has_note=bool(project.arc42_notes.get("04"))
    )


def _tree(elements: list[Element]) -> list[str]:
    """The element tree as indented lines, parents before children, siblings in list order."""
    known = {e.id for e in elements}
    children: dict[str | None, list[Element]] = {}
    for e in elements:
        children.setdefault(e.parent if e.parent in known else None, []).append(e)
    lines: list[str] = []

    def walk(parent: str | None, depth: int, seen: frozenset[str]) -> None:
        for e in children.get(parent, []):
            if e.id in seen:
                continue
            text = f"{e.name} ({e.kind})" + (f": {short(e.description, 200)}" if e.description else "")
            lines.append(f"{'  ' * depth}{single(text)}")
            walk(e.id, depth + 1, seen | {e.id})

    walk(None, 0, frozenset())
    return lines


def _building_blocks(project: Project, snapshot: ProjectSnapshot) -> Section:
    section = _section("05")
    if project.elements:
        section.blocks += [Heading(3, _("Bausteine")), Bullets(tuple(_tree(project.elements)))]
    names = {e.id: e.name for e in project.elements}
    rows = [
        (names.get(r.source, r.source), r.kind, names.get(r.target, r.target), short(r.description) or "–")
        for r in project.relations
        if r.kind != "deployed_on"
    ]
    if rows:
        section.blocks += [
            Heading(3, _("Beziehungen")),
            Table((_("Von"), _("Art"), _("Nach"), _("Beschreibung")), _capped(rows)),
        ]
    section.blocks += [Figure(d.id, d.title) for d in _members(snapshot, STRUCTURE_TYPES)]
    return _gap_if_empty(
        section,
        _("Keine Projektelemente und keine Komponentendiagramme."),
        has_note=bool(project.arc42_notes.get("05")),
    )


def _runtime(project: Project, snapshot: ProjectSnapshot) -> Section:
    section = _section("06")
    core = [s for s in active(project.scenarios) if s.core]
    if core:
        rows = [
            (s.title, short(s.stimulus) or "–", short(s.response) or "–", short(s.response_measure) or "–")
            for s in core
        ]
        section.blocks += [
            Heading(3, _("Kernszenarien")),
            Table((_("Szenario"), _("Stimulus"), _("Antwort"), _("Messgröße")), _capped(rows)),
        ]
    section.blocks += [Figure(d.id, d.title) for d in _members(snapshot, RUNTIME_TYPES)]
    return _gap_if_empty(
        section, _("Keine Sequenzdiagramme und keine Kernszenarien."), has_note=bool(project.arc42_notes.get("06"))
    )


def _deployment(project: Project, snapshot: ProjectSnapshot) -> Section:
    section = _section("07")
    names = {e.id: e.name for e in project.elements}
    rows = [
        (names.get(r.source, r.source), names.get(r.target, r.target))
        for r in project.relations
        if r.kind == "deployed_on"
    ]
    section.blocks += [Figure(d.id, d.title) for d in _members(snapshot, DEPLOYMENT_TYPES) if not is_overview(d)]
    if rows:
        section.blocks += [Heading(3, _("Zuordnung")), Table((_("Baustein"), _("Läuft auf")), _capped(rows))]
    return _gap_if_empty(
        section, _("Keine Verteilungsdiagramme und keine Zuordnungen."), has_note=bool(project.arc42_notes.get("07"))
    )


def _crosscutting(project: Project) -> Section:
    return _gap_if_empty(
        _section("08"),
        _("Nicht aus dem Modell ableitbar: bitte als Notiz ergänzen."),
        has_note=bool(project.arc42_notes.get("08")),
    )


STATUS_TEXT = {"accepted": N_("angenommen"), "superseded": N_("ersetzt")}


def _decisions(project: Project) -> Section:
    section = _section("09")
    shown = [d for d in project.decisions if d.status in STATUS_TEXT]
    shown.sort(key=lambda d: (d.status != "accepted", d.created_at, d.id))
    for d in shown:
        status = _(STATUS_TEXT[d.status])
        if d.status == "superseded" and d.superseded_by:
            status = _("ersetzt durch {adr}", adr=adr_label(d.superseded_by))
        section.blocks.append(Heading(3, f"{adr_label(d.id)}: {single(d.title)}"))
        section.blocks.append(Bullets((_("Status: {status}", status=status),)))
        for label, text in (
            (_("Kontext"), d.context),
            (_("Entscheidung"), d.decision),
            (_("Konsequenzen"), d.consequences),
        ):
            if text.strip():
                section.blocks += [Paragraph(f"{label}: {single(text)}")]
        if d.options:
            section.blocks.append(
                Bullets(
                    tuple(
                        single(f"{o.title}" + (f" ({_('gewählt')})" if d.chosen == n else ""))
                        for n, o in enumerate(d.options)
                    )
                )
            )
    hidden = len(project.decisions) - len(shown)
    if hidden:
        section.blocks.append(
            Paragraph(_("{n} Entwürfe oder verworfene Entscheidungen sind nicht aufgeführt.", n=hidden))
        )
    return _gap_if_empty(
        section, _("Keine angenommenen Entscheidungen erfasst."), has_note=bool(project.arc42_notes.get("09"))
    )


def _qualities(values: list[str]) -> str:
    return ", ".join(v for v in values if v in QUALITY_CHARACTERISTICS) or "–"


def _quality(project: Project) -> Section:
    section = _section("10")
    scenarios = active(project.scenarios)
    drivers = {d.id: d.title for d in project.drivers}
    if scenarios:
        rows = [
            (
                s.title,
                _qualities(s.qualities),
                drivers.get(s.driver or "", "–"),
                short(s.stimulus) or "–",
                short(s.response) or "–",
                short(s.response_measure) or "–",
            )
            for s in scenarios
        ]
        section.blocks.append(
            Table(
                (_("Szenario"), _("Qualitätsmerkmal"), _("Treiber"), _("Stimulus"), _("Antwort"), _("Messgröße")),
                _capped(rows),
            )
        )
    return _gap_if_empty(section, _("Keine Qualitätsszenarien erfasst."), has_note=bool(project.arc42_notes.get("10")))


def _risks(project: Project, findings: Sequence[ProjectFinding]) -> Section:
    section = _section("11")
    risks = [d for d in ranked_drivers(active(project.drivers)) if d.kind == "risk"]
    if risks:
        rows = [(d.title, _level(d.importance), _level(d.risk), short(d.description) or "–") for d in risks]
        section.blocks += [
            Heading(3, _("Risiken")),
            Table((_("Risiko"), _("Wichtigkeit"), _("Eintrittsrisiko"), _("Beschreibung")), _capped(rows)),
        ]
    open_findings = [f for f in findings if f.severity in ("high", "medium")]
    if open_findings:
        section.blocks += [
            Heading(3, _("Offene Befunde der Prüfung")),
            Bullets(tuple(short(f.message, 300) for f in open_findings[:MAX_ROWS])),
        ]
    return _gap_if_empty(
        section, _("Keine Risiken und keine offenen Befunde."), has_note=bool(project.arc42_notes.get("11"))
    )


def _glossary(project: Project) -> Section:
    section = _section("12")
    rows = sorted(
        ((e.name, short(e.description) or "–") for e in project.elements if e.description.strip()),
        key=lambda r: (r[0].casefold(), r[0]),
    )
    if rows:
        section.blocks.append(Table((_("Begriff"), _("Bedeutung")), _capped(rows)))
    return _gap_if_empty(
        section, _("Keine beschriebenen Projektelemente und keine Notiz."), has_note=bool(project.arc42_notes.get("12"))
    )


assert tuple(TITLES) == ARC42_SECTIONS
