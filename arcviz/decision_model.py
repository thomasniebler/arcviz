"""Decision support of a project: stakeholders, quality scenarios and architecture decisions.

Spec: docs/architecture/specs/decision-support.md (ADR-0166..0168). The types live in the project document next to the
drivers (ADR-0163); their operations are in `projects.py`. This module holds the types, their bounds, the lines the
assistants read and the Markdown form of a decision (an ADR).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from .i18n import N_, _
from .model import single_line
from .project_model import DRIVER_LEVELS, Driver, Element, active, driver_line, is_retired, name_key, ranked_drivers

# ---- bounds ----------------------------------------------------------------------------------------------------------

MAX_STAKEHOLDERS = 50
MAX_SCENARIOS = 200
MAX_DECISIONS = 100
MAX_TITLE = 160  # scenario and decision titles
MAX_NAME = 120  # stakeholder name and role
MAX_CONCERNS = 1000
MAX_PART = 500  # each of the six parts of a scenario
MAX_REFS = 20  # drivers or elements a stakeholder, scenario or decision refers to
MAX_LONG_TEXT = 3000  # a decision's context, decision and consequences
MAX_OPTIONS = 5
MAX_OPTION_DESCRIPTION = 1000
MAX_POINTS = 6  # pros or cons of an option
MAX_POINT = 300
PROMPT_BLOCK_BUDGET = 3_000  # characters of stakeholders, and of decisions, in a system prompt

INFLUENCE = DRIVER_LEVELS
SCENARIO_KINDS = ("usage", "growth", "change", "failure", "threat", "cost", "other")
SCENARIO_PARTS = ("source", "stimulus", "environment", "artifact", "response", "response_measure")
DECISION_STATUSES = ("draft", "accepted", "superseded", "rejected")

SCENARIO_KIND_TEXT = {
    "usage": N_("Nutzung"),
    "growth": N_("Wachstum"),
    "change": N_("Änderung"),
    "failure": N_("Ausfall"),
    "threat": N_("Bedrohung"),
    "cost": N_("Kosten"),
    "other": N_("Sonstiges"),
}
PART_TEXT = {
    "source": N_("Quelle"),
    "stimulus": N_("Stimulus"),
    "environment": N_("Umgebung"),
    "artifact": N_("Artefakt"),
    "response": N_("Reaktion"),
    "response_measure": N_("Reaktionsmaß"),
}
STATUS_TEXT = {
    "draft": N_("Entwurf"),
    "accepted": N_("Angenommen"),
    "superseded": N_("Ersetzt"),
    "rejected": N_("Verworfen"),
}


# ---- types -----------------------------------------------------------------------------------------------------------


class Stakeholder(BaseModel):
    id: str  # s<n> from Project.stakeholder_seq, never reused
    name: str
    role: str = ""
    concerns: str = ""
    influence: str = "medium"  # INFLUENCE
    drivers: list[str] = Field(default_factory=list)  # ids of the drivers they care about


class Scenario(BaseModel):
    """A quality attribute scenario in the six parts of ATAM."""

    id: str  # q<n> from Project.scenario_seq, never reused
    title: str
    kind: str = "usage"  # SCENARIO_KINDS
    source: str = ""
    stimulus: str = ""
    environment: str = ""
    artifact: str = ""
    response: str = ""
    response_measure: str = ""
    driver: str | None = None  # the one driver it serves
    core: bool = False  # a core scenario: one of the core goals of its driver
    elements: list[str] = Field(default_factory=list)
    # spec: requirements.md (ADR-0189), ADR-0187; omitted from dumps while empty
    requirements: list[str] = Field(default_factory=list)
    stakeholders: list[str] = Field(default_factory=list)  # whose concern it states, ids
    origin: str = ""
    status: str = "active"  # OBJECT_STATUSES; omitted from dumps while active (ADR-0198)

    @model_serializer(mode="wrap")
    def _omit_empty_links(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = _without_empty(handler(self))
        if data.get("status") == "active":
            data.pop("status", None)
        return data


class DecisionOption(BaseModel):
    title: str
    description: str = ""
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    """An architecture decision record (ADR)."""

    id: str  # adr<n> from Project.decision_seq, shown as ADR-<n>
    title: str
    status: str = "draft"  # DECISION_STATUSES
    context: str = ""
    options: list[DecisionOption] = Field(default_factory=list)
    chosen: int | None = None  # index into options
    decision: str = ""
    consequences: str = ""
    scenario: str | None = None
    drivers: list[str] = Field(default_factory=list)
    elements: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    created_at: str
    decided_at: str | None = None
    requirements: list[str] = Field(default_factory=list)  # the requirements it addresses, ids (ADR-0189)
    stakeholders: list[str] = Field(default_factory=list)  # who it concerns or who decided, ids
    origin: str = ""

    @model_serializer(mode="wrap")
    def _omit_empty_links(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _without_empty(handler(self))


def _without_empty(data: dict[str, Any]) -> dict[str, Any]:
    """The link fields of M31 are omitted from dumps while empty, so stored data and fingerprints do not change."""
    for key in ("requirements", "stakeholders", "origin"):
        if not data.get(key):
            data.pop(key, None)
    return data


# ---- small helpers ---------------------------------------------------------------------------------------------------


def number_of(item_id: str, prefix: str) -> int | None:
    """n of a generated id `<prefix><n>`; None for any other id (an imported one)."""
    rest = item_id[len(prefix) :] if item_id.startswith(prefix) else ""
    return int(rest) if rest.isdigit() else None


def adr_label(decision_id: str) -> str:
    """`adr3` -> `ADR-3` (other ids as they are)."""
    n = number_of(decision_id, "adr")
    return f"ADR-{n}" if n is not None else decision_id


def find_by_title(items: list[Any], ref: str | None, attr: str) -> Any | None:
    """An item by id or by case-insensitive `attr` (title or name)."""
    if not ref or not ref.strip():
        return None
    found = next((i for i in items if i.id == ref.strip()), None)
    if found is not None:
        return found
    wanted = name_key(ref)
    return next((i for i in items if name_key(getattr(i, attr)) == wanted), None)


def known(items: list[Any], attr: str) -> str:
    """Items for an error message the model can act on."""
    text = ", ".join(f"{i.id} ({getattr(i, attr)})" for i in items[:30])
    return (text + (" …" if len(items) > 30 else "")) or "none"


def _short(text: str, limit: int) -> str:
    text = single_line(text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cut(lines: list[str], budget: int, what: str, tool: str) -> str:
    out: list[str] = []
    used = 0
    for n, line in enumerate(lines):
        if used + len(line) + 80 > budget:
            out.append(f"(+ {len(lines) - n} more {what}; call {tool} for all of them)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


# ---- prompt lines ----------------------------------------------------------------------------------------------------


def stakeholder_lines(
    stakeholders: list[Stakeholder], budget: int = PROMPT_BLOCK_BUDGET, drivers: list[Driver] | None = None
) -> str | None:
    """`- [s1] Name (role, influence high): concerns; cares about d1, d2`, the most influential first. With `drivers`,
    the retired ones are not named (ADR-0198)."""
    retired = {d.id for d in drivers or [] if is_retired(d)}
    if not stakeholders:
        return None
    rank = {level: n for n, level in enumerate(INFLUENCE)}
    ordered = sorted(stakeholders, key=lambda s: rank.get(s.influence, len(INFLUENCE)))
    lines = []
    for s in ordered:
        role = f"{s.role}, " if s.role else ""
        concerns = f": {_short(s.concerns, 200)}" if s.concerns else ""
        named = [d for d in s.drivers if d not in retired]
        cares = f"; cares about {', '.join(named)}" if named else ""
        lines.append(f"- [{s.id}] {s.name} ({role}influence {s.influence}){concerns}{cares}")
    return _cut(lines, budget, "stakeholders", "list_stakeholders")


def core_scenario_lines(scenarios: list[Scenario], driver_id: str) -> list[str]:
    """The core scenarios of a driver for its line in the drivers block: `core: [q2] stimulus → response measure`."""
    out = []
    for s in scenarios:
        if s.driver == driver_id and s.core and not is_retired(s):
            what = _short(s.stimulus or s.title, 140)
            measure = f" → {_short(s.response_measure, 100)}" if s.response_measure else ""
            out.append(f"  core: [{s.id}] {what}{measure}")
    return out


def decision_lines(decisions: list[Decision], budget: int = PROMPT_BLOCK_BUDGET) -> str | None:
    """The accepted decisions for a system prompt, newest first: `- [adr3] ADR-3 Title — decision`."""
    accepted = [d for d in decisions if d.status == "accepted"]
    if not accepted:
        return None
    accepted.sort(key=lambda d: d.decided_at or d.created_at, reverse=True)
    lines = []
    for d in accepted:
        text = f" — {_short(d.decision, 300)}" if d.decision else ""
        concerns = f" (concerns: {', '.join(d.elements)})" if d.elements else ""
        lines.append(f"- [{d.id}] {adr_label(d.id)} {d.title}{text}{concerns}")
    return _cut(lines, budget, "accepted decisions", "list_decisions")


def drivers_with_core_scenarios(
    drivers: list[Driver], scenarios: list[Scenario], budget: int = PROMPT_BLOCK_BUDGET + 1_000
) -> str | None:
    """The drivers most important first, each followed by its core scenarios (the drivers block of the prompts and the
    analysis); descriptions are dropped, then the lowest drivers counted, when it does not fit `budget`."""
    drivers = active(drivers)
    if not drivers:
        return None
    ranked = ranked_drivers(drivers)
    for limit in (200, 0):
        lines = [line for d in ranked for line in [driver_line(d, limit), *core_scenario_lines(scenarios, d.id)]]
        if sum(len(line) + 1 for line in lines) <= budget:
            return "\n".join(lines)
    return _cut(lines, budget, "drivers", "list_drivers")


def scenario_text(scenario: Scenario) -> str:
    """A scenario in its six parts, one per line (what the analysis and the decision context show)."""
    kind = _(SCENARIO_KIND_TEXT[scenario.kind]) if scenario.kind in SCENARIO_KIND_TEXT else scenario.kind
    lines = [f"[{scenario.id}] {scenario.title} ({kind})"]
    lines += [f"{_(PART_TEXT[k])}: {getattr(scenario, k)}" for k in SCENARIO_PARTS if getattr(scenario, k)]
    return "\n".join(lines)


# ---- Markdown ADR ----------------------------------------------------------------------------------------------------


def decision_markdown(
    decision: Decision,
    drivers: list[Driver],
    elements: list[Element],
    scenarios: list[Scenario],
) -> str:
    """The decision as a Markdown ADR (spec: decision-support.md, Markdown ADR)."""
    if decision.status == "accepted":
        status = _("Angenommen ({date})", date=decision.decided_at[:10]) if decision.decided_at else _("Angenommen")
    elif decision.status == "superseded" and decision.superseded_by:
        status = _("Ersetzt durch {adr}", adr=adr_label(decision.superseded_by))
    else:
        status = _(STATUS_TEXT[decision.status]) if decision.status in STATUS_TEXT else decision.status
    lines = [f"# {adr_label(decision.id)}: {decision.title}", "", _("- Status: {status}", status=status)]
    scenario = next((s for s in scenarios if s.id == decision.scenario), None)
    if scenario is not None:
        lines.append(_("- Szenario: {title} ({id})", title=scenario.title, id=scenario.id))
    by_driver = {d.id: d.title for d in drivers}
    if decision.drivers:
        names = ", ".join(by_driver.get(d, d) for d in decision.drivers)
        lines.append(_("- Treiber: {names}", names=names))
    by_element = {e.id: e.name for e in elements}
    if decision.elements:
        names = ", ".join(by_element.get(e, e) for e in decision.elements)
        lines.append(_("- Elemente: {names}", names=names))
    lines += ["", _("## Kontext"), "", decision.context.strip() or "–", "", _("## Optionen"), ""]
    if not decision.options:
        lines += ["–", ""]
    for n, option in enumerate(decision.options):
        mark = _(" (gewählt)") if decision.chosen == n else ""
        lines += [f"### {n + 1}. {option.title}{mark}", ""]
        if option.description:
            lines += [option.description.strip(), ""]
        if option.pros:
            lines += [_("Pro:"), "", *[f"- {p}" for p in option.pros], ""]
        if option.cons:
            lines += [_("Contra:"), "", *[f"- {c}" for c in option.cons], ""]
    lines += [_("## Entscheidung"), "", decision.decision.strip() or "–", "", _("## Konsequenzen"), ""]
    lines += [decision.consequences.strip() or "–", ""]
    return "\n".join(lines)
