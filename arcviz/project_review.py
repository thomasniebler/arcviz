"""Project review: rule checks across a project and one LLM review of the project as a whole.

Spec: docs/architecture/specs/project-review.md (ADR-0062..0064). Project rules are pure functions of a
`ProjectSnapshot`; the rule findings of every member diagram are aggregated with their diagram id; the
model is asked once, over a budgeted digest, and its findings are validated one by one. Nothing is stored,
and a failed model call never fails the review. Finding messages are in the request language (German by default, `i18n`) because the UI shows them.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from . import review, rules, sequence
from .decision_model import adr_label, decision_lines
from .i18n import N_, _
from .llm import LLMProvider, ToolSpec
from .model import Diagram, single_line
from .project_model import (
    RELATION_RULES,
    DiagramViews,
    Relation,
    ViewConnection,
    ViewElement,
    active,
    fits,
    linkable_kinds,
    loose_key,
    nested,
    property_problem,
    relation_kinds_for,
    relation_line,
    shown_relation_kinds,
    shows,
    view_connections,
    view_elements,
)
from .projects import Membership, Project
from .requirement_model import Requirement, RequirementBox, coverage_map, duplicate_candidates
from .rules import CATEGORIES, SEVERITY_ORDER, Category, Finding, Severity

PROJECT_REVIEW_BUDGET = 100_000  # characters of diagram summaries in the prompt, shared by the diagrams
MIN_DIAGRAM_BUDGET = 2_000
NEAR_DUPLICATE_RATIO = 0.85
NEAR_DUPLICATE_MIN_LENGTH = 4
OVERVIEW_WORDS = ("übersicht", "overview", "kontext", "context", "landschaft", "landscape", "gesamtbild", "big picture")
STRUCTURE_TYPES = frozenset({"deployment", "class", "component"})
BEHAVIOUR_TYPES = frozenset({"activity", "sequence", "state_machine", "use_case"})
PARTIAL_NOTE = (
    "Note: some diagrams are too large to show completely, so their largest containers are collapsed to their "
    "element count. No tool to fetch details is available in this review. Do not report findings about hidden "
    "contents; you may say in the summary that they were not reviewed."
)


class Resolution(BaseModel):
    """One way a person can resolve a finding (spec: model-relations.md, ADR-0138); the review applies none.

    `action`: propose (a propose-mode turn on `diagram` with `prompt`), hide (`relation` in `diagram`), link_relation
    (`view` of `diagram` to `relation`), update_relation / remove_relation (`relation`, `args`), create_relation
    (`args`), move_element (`model_element`, `args.parent`), sync."""

    action: str
    label: str  # for the button, in the request language
    diagram: str | None = None
    relation: str | None = None
    view: str | None = None
    model_element: str | None = None
    prompt: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class ProjectFinding(Finding):
    """A finding of the project review: a `Finding` plus the diagram(s) and section it concerns (ADR-0062)."""

    diagram: str | None = None  # the diagram `elements`/`edges` belong to and the UI opens
    diagrams: list[str] = Field(default_factory=list)  # every diagram it concerns (includes `diagram`)
    section: str | None = None
    model_element: str | None = None  # the project model element it concerns (ADR-0075)
    relation: str | None = None  # the model relation it concerns (spec: model-relations.md)
    requirements: list[str] = Field(default_factory=list)  # the requirements it concerns (spec: requirements.md)
    resolutions: list[Resolution] = Field(default_factory=list)  # what a person can do about it (ADR-0138)


@dataclass(frozen=True)
class ProjectSnapshot:
    """A project and its existing member diagrams in membership order (`ProjectService.load_members`)."""

    project: Project
    members: list[tuple[Membership, Diagram]]
    requirements: RequirementBox | None = None  # the project's requirements (their own document, ADR-0188)


@dataclass(frozen=True)
class ProjectRule:
    id: str
    title: str  # one-liner for the rule list (an `N_` text, `_()` it when shown)
    category: Category
    check: Callable[[ProjectSnapshot], Iterable[ProjectFinding]]


# ---- helpers ---------------------------------------------------------------------------------------------


def is_overview(diagram: Diagram) -> bool:
    """Whether the title marks the diagram as an overview (ADR-0063)."""
    title = diagram.title.casefold()
    return any(word in title for word in OVERVIEW_WORDS)


def normalise_title(title: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", title.casefold()).split())


def _is_empty(diagram: Diagram) -> bool:
    if diagram.interaction is not None:
        return not diagram.interaction.lifelines and not diagram.interaction.fragments
    return not diagram.nodes


def _finding(
    rule: str,
    subject: str,
    category: Category,
    severity: Severity,
    message: str,
    *,
    diagrams: list[str] | None = None,
    section: str | None = None,
    elements: list[str] | None = None,
    edges: list[str] | None = None,
    model_element: str | None = None,
    relation: str | None = None,
    requirements: list[str] | None = None,
    resolutions: list[Resolution] | None = None,
) -> ProjectFinding:
    diagrams = diagrams or []
    return ProjectFinding(
        id=f"{rule}:{subject}",
        source="rule",
        rule=rule,
        category=category,
        severity=severity,
        message=message,
        diagram=diagrams[0] if diagrams else None,
        diagrams=diagrams,
        section=section,
        elements=elements or [],
        edges=edges or [],
        model_element=model_element,
        relation=relation,
        requirements=requirements or [],
        resolutions=resolutions or [],
    )


def _direct(snapshot: ProjectSnapshot, section_id: str | None) -> list[Diagram]:
    return [d for m, d in snapshot.members if m.section == section_id]


# ---- project rules ---------------------------------------------------------------------------------------


def empty_section(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    project = snapshot.project
    holding = {m.section for m, __ in snapshot.members}

    def has_diagrams(section_id: str) -> bool:
        return section_id in holding or any(s in holding for s in project.descendants(section_id))

    reported: set[str] = set()
    for section in project.sections:
        if has_diagrams(section.id):
            continue
        ancestors, parent = set(), section.parent
        while parent is not None and parent not in ancestors:
            ancestors.add(parent)
            found = project.find_section(parent)
            parent = found.parent if found is not None else None
        if ancestors & reported:
            continue  # inside an empty section that is reported already
        reported.add(section.id)
        yield _finding(
            "empty-section",
            section.id,
            "suggestion",
            "low",
            _("Abschnitt „{section_title}“ enthält keine Diagramme.", section_title=section.title),
            section=section.id,
        )


def section_without_overview(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    for section in snapshot.project.sections:
        direct = _direct(snapshot, section.id)
        if len(direct) >= 2 and not any(is_overview(d) for d in direct):
            yield _finding(
                "section-without-overview",
                section.id,
                "suggestion",
                "low",
                _(
                    "Abschnitt „{section_title}“ hat {count} Diagramme, aber keine Übersicht (ein Diagramm mit „Übersicht“ oder „Kontext“ im Titel, das die Teile zusammenführt).",
                    section_title=section.title,
                    count=len(direct),
                ),
                diagrams=[d.id for d in direct],
                section=section.id,
            )


def missing_diagram_types(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    for section in snapshot.project.sections:
        types = {d.type for d in _direct(snapshot, section.id)}
        if not types:
            continue
        if types <= STRUCTURE_TYPES:
            message = _(
                "Abschnitt „{section_title}“ zeigt nur die Struktur (Verteilungsdiagramm oder Klassendiagramm); ein Verhaltensdiagramm (Aktivität, Sequenz oder Zustandsautomat) fehlt.",
                section_title=section.title,
            )
        elif types <= BEHAVIOUR_TYPES:
            message = _(
                "Abschnitt „{section_title}“ zeigt nur Abläufe; ein Strukturdiagramm (Verteilungsdiagramm oder Klassendiagramm) fehlt.",
                section_title=section.title,
            )
        else:
            continue
        yield _finding("missing-diagram-types", section.id, "suggestion", "low", message, section=section.id)


def duplicate_title(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    diagrams = [d for __, d in snapshot.members]
    groups: dict[str, list[Diagram]] = {}
    for d in diagrams:
        groups.setdefault(normalise_title(d.title), []).append(d)
    for group in groups.values():
        if len(group) > 1:
            names = _(" und ").join(_q(d.title) for d in group)
            yield _finding(
                "duplicate-title",
                "+".join(d.id for d in group),
                "risk",
                "medium",
                _(
                    "Die Diagramme {names} haben denselben Titel; im Navigator sind sie nicht zu unterscheiden.",
                    names=names,
                ),
                diagrams=[d.id for d in group],
            )
    keys = [k for k in groups if len(k) >= NEAR_DUPLICATE_MIN_LENGTH]
    for i, first in enumerate(keys):
        for second in keys[i + 1 :]:
            if difflib.SequenceMatcher(None, first, second).ratio() >= NEAR_DUPLICATE_RATIO:
                a, b = groups[first][0], groups[second][0]
                yield _finding(
                    "duplicate-title",
                    f"{a.id}+{b.id}",
                    "risk",
                    "low",
                    _(
                        "Die Diagramme „{a_title}“ und „{b_title}“ haben fast denselben Titel; ist eines davon überflüssig oder sollten die Titel den Unterschied nennen?",
                        a_title=a.title,
                        b_title=b.title,
                    ),
                    diagrams=[a.id, b.id],
                )


def empty_diagram(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    for __, d in snapshot.members:
        if _is_empty(d):
            yield _finding(
                "empty-diagram",
                d.id,
                "suggestion",
                "low",
                _("Diagramm „{title}“ ist leer.", title=d.title),
                diagrams=[d.id],
            )


# ---- consistency of the shared model (spec: shared-project-model.md, ADR-0075) ---------------------------------


def _diagrams(snapshot: ProjectSnapshot) -> list[Diagram]:
    return [d for __, d in snapshot.members]


def _linked(diagram: Diagram) -> list[ViewElement]:
    return [v for v in view_elements(diagram) if v.ref is not None]


def _q(value: str) -> str:
    """`value` in the quotation marks of the language („…“ in German, “…” in English)."""
    return _("„{value}“", value=value)


def _quoted(names: Iterable[str]) -> str:
    return ", ".join(_q(n) for n in names)


def dangling_element_ref(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    known = {e.id for e in snapshot.project.elements}
    for d in _diagrams(snapshot):
        dangling = [v for v in _linked(d) if v.ref not in known]
        if dangling:
            yield _finding(
                "dangling-element-ref",
                d.id,
                "risk",
                "medium",
                _(
                    "In „{title}“ verweisen {quoted} auf Projektelemente, die es in diesem Projekt nicht gibt; die Verknüpfung lösen oder das Element anlegen.",
                    title=d.title,
                    quoted=_quoted(v.name for v in dangling),
                ),
                diagrams=[d.id],
                elements=[v.id for v in dangling],
            )


def element_name_drift(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    for element in snapshot.project.elements:
        for d in _diagrams(snapshot):
            name = single_line(element.name).strip()  # stored names may predate #103; views show them normalised
            drifted = [v for v in _linked(d) if v.ref == element.id and single_line(v.name).strip() != name]
            if drifted:
                yield _finding(
                    "element-name-drift",
                    f"{d.id}:{element.id}",
                    "risk",
                    "low",
                    _(
                        "In „{title}“ heißt das Projektelement „{name}“ noch {quoted}; „Modell abgleichen“ übernimmt den Namen.",
                        title=d.title,
                        name=element.name,
                        quoted=_quoted(v.name for v in drifted),
                    ),
                    diagrams=[d.id],
                    elements=[v.id for v in drifted],
                    model_element=element.id,
                )


def incompatible_element_link(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    for element in snapshot.project.elements:
        for d in _diagrams(snapshot):
            wrong = [
                v for v in _linked(d) if v.ref == element.id and element.kind not in linkable_kinds(d.type, v.kind)
            ]
            if wrong:
                kinds = ", ".join(sorted({f"«{v.kind}»" for v in wrong}))
                yield _finding(
                    "incompatible-element-link",
                    f"{d.id}:{element.id}",
                    "risk",
                    "medium",
                    _(
                        "In „{title}“ steht ein {kinds} für das Projektelement „{name}“ ({kind}); diese Art kann es nicht darstellen.",
                        title=d.title,
                        kinds=kinds,
                        name=element.name,
                        kind=element.kind,
                    ),
                    diagrams=[d.id],
                    elements=[v.id for v in wrong],
                    model_element=element.id,
                )


def _usages(snapshot: ProjectSnapshot) -> dict[str, list[tuple[Diagram, ViewElement]]]:
    usages: dict[str, list[tuple[Diagram, ViewElement]]] = {}
    for d in _diagrams(snapshot):
        for v in _linked(d):
            usages.setdefault(v.ref or "", []).append((d, v))
    return usages


def unused_element(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    usages = _usages(snapshot)
    sections = {s.element for s in snapshot.project.sections}
    parents = {e.parent for e in snapshot.project.elements}
    for element in snapshot.project.elements:
        if element.id not in usages and element.id not in sections and element.id not in parents:
            yield _finding(
                "unused-element",
                element.id,
                "suggestion",
                "low",
                _("Das Projektelement „{name}“ kommt in keinem Diagramm und keinem Abschnitt vor.", name=element.name),
                model_element=element.id,
            )


def _deployed(snapshot: ProjectSnapshot) -> tuple[set[str], set[frozenset[str]]]:
    """The elements shown in deployment diagrams and the pairs of them that are connected there: by an edge
    between nodes standing for them, or by containment. A node stands for its own element and those of its
    ancestors (a service inside a node of subsystem X belongs to X)."""
    known = {e.id for e in snapshot.project.elements}
    present: set[str] = set()
    pairs: set[frozenset[str]] = set()
    for d in _diagrams(snapshot):
        if d.type != "deployment":
            continue
        stands: dict[str, set[str]] = {}
        for node in d.nodes.values():
            chain = [d.nodes[n] for n in (node.id, *d.ancestors(node.id)) if n in d.nodes]
            stands[node.id] = {n.ref for n in chain if n.ref in known}
            present |= stands[node.id]
            pairs |= {frozenset((a, b)) for a in stands[node.id] for b in stands[node.id] if a != b}
        for edge in d.edges:
            pairs |= {
                frozenset((a, b)) for a in stands.get(edge.source, ()) for b in stands.get(edge.target, ()) if a != b
            }
    return present, pairs


def element_not_in_structure(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    present, __ = _deployed(snapshot)
    if not present:
        return  # no deployment diagram shows linked elements: nothing to compare with
    usages = _usages(snapshot)
    for element in snapshot.project.elements:
        if element.kind == "actor" or element.id in present:
            continue
        behaviour = [(d, v) for d, v in usages.get(element.id, []) if d.type in BEHAVIOUR_TYPES]
        if behaviour:
            where = list(dict.fromkeys(d.id for d, __ in behaviour))
            yield _finding(
                "element-not-in-structure",
                element.id,
                "suggestion",
                "low",
                _(
                    "„{name}“ wirkt in Abläufen mit, kommt aber in keinem Verteilungsdiagramm vor; wo läuft es?",
                    name=element.name,
                ),
                diagrams=where,
                elements=[v.id for d, v in behaviour if d.id == where[0]],
                model_element=element.id,
            )


def message_without_connection(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    present, pairs = _deployed(snapshot)
    if not present:
        return
    # a communicates relation in the model connects the two as well (generalised in M25, ADR-0141); whether a
    # deployment diagram draws it is relation-not-shown's business
    pairs = pairs | {frozenset((r.source, r.target)) for r in snapshot.project.relations if r.kind == "communicates"}
    names = {e.id: e.name for e in snapshot.project.elements}
    for d in _diagrams(snapshot):
        if d.interaction is None:
            continue
        refs = {lf.id: lf.ref for lf in d.interaction.lifelines if lf.ref in present}
        missing: dict[frozenset[str], list[str]] = {}
        for fragment, __, __ in sequence.walk(d.interaction.fragments):
            if fragment.element != "message":
                continue
            a, b = refs.get(fragment.sender or ""), refs.get(fragment.receiver or "")
            if a is None or b is None or a == b or frozenset((a, b)) in pairs:
                continue
            missing.setdefault(frozenset((a, b)), []).append(fragment.id)
        for pair, messages in missing.items():
            first, second = sorted(pair, key=lambda e: names[e])
            yield _finding(
                "message-without-connection",
                f"{d.id}:{first}+{second}",
                "risk",
                "medium",
                _(
                    "„{title}“ zeigt Nachrichten zwischen „{first}“ und „{second}“, aber kein Verteilungsdiagramm verbindet die beiden.",
                    title=d.title,
                    first=names[first],
                    second=names[second],
                ),
                diagrams=[d.id],
                elements=messages,
                model_element=first,
            )


def unlinked_namesake(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    for element in snapshot.project.elements:
        found: list[tuple[Diagram, ViewElement]] = [
            (d, v)
            for d in _diagrams(snapshot)
            for v in view_elements(d)
            if v.ref is None
            and v.name.strip().casefold() == element.name.strip().casefold()
            and element.kind in linkable_kinds(d.type, v.kind)
        ]
        if found:
            where = list(dict.fromkeys(d.id for d, __ in found))
            titles = _quoted(dict.fromkeys(d.title for d, __ in found))
            yield _finding(
                "unlinked-namesake",
                element.id,
                "suggestion",
                "low",
                _(
                    "„{name}“ kommt in {titles} vor, ist dort aber nicht mit dem Projektelement verknüpft.",
                    name=element.name,
                    titles=titles,
                ),
                diagrams=where,
                elements=[v.id for d, v in found if d.id == where[0]],
                model_element=element.id,
            )


# Near-duplicate element names (spec: elements-by-default.md, ADR-0164): since views become elements when they are
# created, a variant of a name creates a second element. Conservative: synonyms of one generic suffix class only, and
# one-letter typos inside a word (never the last letter or a digit: "service-a"/"service-b" are different things).
_SUFFIX_CLASSES = {
    "service": "svc",
    "svc": "svc",
    "server": "svc",
    "dienst": "svc",
    "srv": "svc",
    "database": "db",
    "datenbank": "db",
    "db": "db",
    "application": "app",
    "app": "app",
    "component": "cmp",
    "komponente": "cmp",
    "system": "sys",
}
_SUFFIXES = sorted(_SUFFIX_CLASSES, key=len, reverse=True)
MAX_SIMILAR_FINDINGS = 50


def _stem(key: str) -> tuple[str, str | None]:
    for suffix in _SUFFIXES:
        if key.endswith(suffix) and len(key) - len(suffix) >= 3:
            return key[: -len(suffix)], _SUFFIX_CLASSES[suffix]
    return key, None


def _typo(a: str, b: str) -> bool:
    """One substitution or one swap of neighbours, inside the word and between letters (not digits, not at the end)."""
    if len(a) != len(b) or len(a) < 6 or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if any(i == len(a) - 1 or a[i].isdigit() or b[i].isdigit() for i in diff):
        return False
    if len(diff) == 1:
        return True
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def similar_element_names(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    elements = snapshot.project.elements
    keyed = [(e, loose_key(e.name)) for e in elements]
    stems = [(e, k, _stem(k)) for e, k in keyed if k]
    found = 0
    for i, (a, key_a, stem_a) in enumerate(stems):
        for b, key_b, stem_b in stems[i + 1 :]:
            if found >= MAX_SIMILAR_FINDINGS:
                return
            if not (key_a == key_b or stem_a == stem_b or _typo(key_a, key_b)):
                continue
            found += 1
            yield _finding(
                "similar-element-names",
                f"{a.id}+{b.id}",
                "suggestion",
                "low",
                _(
                    "Die Projektelemente „{a_name}“ und „{b_name}“ heißen fast gleich – vermutlich dasselbe. Verwende eines davon: das andere löschen und seine Diagrammelemente mit dem verbleibenden verknüpfen.",
                    a_name=a.name,
                    b_name=b.name,
                ),
                model_element=a.id,
            )


# ---- correspondence over model relations (spec: model-relations.md, ADR-0141) -----------------------------------
# Every finding names its resolutions (ADR-0138); the review applies none of them.

_VERBS = {"communicates": N_("kommuniziert mit"), "deployed_on": N_("läuft auf"), "data_flow": N_("Datenfluss zu")}


def _names(snapshot: ProjectSnapshot) -> dict[str, str]:
    return {e.id: e.name for e in snapshot.project.elements}


def _verb(kind: str) -> str:
    return _(_VERBS[kind]) if kind in _VERBS else kind


def _relation_text(relation: Relation, names: dict[str, str]) -> str:
    source, target = names.get(relation.source, relation.source), names.get(relation.target, relation.target)
    return _("„{source}“ {verb} „{target}“", source=source, verb=_verb(relation.kind), target=target)


def _views_of(diagram: Diagram, element_id: str) -> list[str]:
    return [v.id for v in view_elements(diagram) if v.ref == element_id]


def _placed(diagram: Diagram, ids: list[str]) -> tuple[list[str], list[str]]:
    """Connection ids as a finding marks them, `(elements, edges)`: messages are elements, deployment edges edges."""
    return ([], ids) if diagram.interaction is None else (ids, [])


def _kinds(snapshot: ProjectSnapshot) -> dict[str, str]:
    return {e.id: e.kind for e in snapshot.project.elements}


def _connections(snapshot: ProjectSnapshot) -> dict[str, list[ViewConnection]]:
    """Every member diagram's connections, computed once per rule (the rules loop over relations × diagrams)."""
    return {d.id: view_connections(d) for d in _diagrams(snapshot)}


def _views(snapshot: ProjectSnapshot, kinds: dict[str, str]) -> dict[str, DiagramViews]:
    """Every member diagram's connections and nesting, each built at most once per rule run."""
    return {d.id: DiagramViews(d, kinds) for d in _diagrams(snapshot)}


def _shows(diagram: Diagram, relation: Relation, kinds: dict[str, str], views: dict[str, DiagramViews]) -> bool:
    """Whether the diagram shows the relation (the one definition, `project_model.shows`)."""
    return shows(diagram, relation, kinds, views[diagram.id])


def _shows_both_ends(diagram: Diagram, relation: Relation) -> bool:
    refs = {v.ref for v in view_elements(diagram)}
    return relation.source in refs and relation.target in refs


def _draw_prompt(relation: Relation, names: dict[str, str]) -> str:
    source, target = names.get(relation.source, relation.source), names.get(relation.target, relation.target)
    if relation.kind == "deployed_on":
        how = _(
            "Platziere „{source}“ in „{target}“ oder verbinde beide mit einer deploys-Kante",
            source=source,
            target=target,
        )
    elif relation.kind == "data_flow":
        how = _("Zeichne eine Verbindung von „{source}“ zu „{target}“", source=source, target=target)
    else:
        how = _("Zeichne eine Verbindung zwischen „{source}“ und „{target}“", source=source, target=target)
    return _(
        "{how} und verknüpfe sie mit der Projektbeziehung {relation_id} ({relation_text}).",
        how=how,
        relation_id=relation.id,
        relation_text=_relation_text(relation, names),
    )


def _remove(relation: Relation) -> Resolution:
    return Resolution(action="remove_relation", label=_("Beziehung aus dem Modell entfernen"), relation=relation.id)


def relation_not_shown(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    kinds, names = _kinds(snapshot), _names(snapshot)
    deployments = [d for d in _diagrams(snapshot) if d.type in ("deployment", "component")]
    views = _views(snapshot, kinds)
    for relation in snapshot.project.relations:
        if any(_shows(d, relation, kinds, views) for d in deployments):
            continue
        # a model-level gap, never a view's omission (open world, ADR-0137/0141): only views that could draw it are
        # named; one that hides it on purpose is not a candidate, and a complete one is complete-view-missing-relation's
        candidates = [d for d in deployments if _shows_both_ends(d, relation) and relation.id not in d.hidden_relations]
        if not candidates or any(relation.kind in d.complete for d in candidates):
            continue
        yield _finding(
            "relation-not-shown",
            relation.id,
            "suggestion",
            "low",
            _(
                "Die Beziehung {relation_text} steht im Projektmodell, aber kein Verteilungs- oder Komponentendiagramm zeigt sie; zeigen könnte sie {quoted} (beide Elemente sind dort).",
                relation_text=_relation_text(relation, names),
                quoted=_quoted(d.title for d in candidates),
            ),
            diagrams=[d.id for d in candidates],
            elements=_views_of(candidates[0], relation.source) + _views_of(candidates[0], relation.target),
            relation=relation.id,
            resolutions=[
                Resolution(
                    action="propose",
                    label=_("In „{title}“ zeichnen (Vorschlag)", title=candidates[0].title),
                    diagram=candidates[0].id,
                    prompt=_draw_prompt(relation, names),
                ),
                _remove(relation),
            ],
        )


def _creatable_kind(diagram: Diagram, connection: ViewConnection, kinds: dict[str, str]) -> str | None:
    """The first relation kind the connection could show whose rules allow its ends' element kinds."""
    a, b = kinds.get(connection.source_ref or ""), kinds.get(connection.target_ref or "")
    for kind in relation_kinds_for(diagram.type, connection.kind):
        sources, targets, __ = RELATION_RULES[kind]
        if a in sources and b in targets:
            return kind
    return None


def connection_without_relation(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    relations = snapshot.project.relations
    if not relations:
        return
    kinds, names = _kinds(snapshot), _names(snapshot)
    present, deployed = _deployed(snapshot)
    related = {frozenset((r.source, r.target)) for r in relations if r.kind == "communicates"}
    for d in _diagrams(snapshot):
        groups: dict[frozenset[str], list[ViewConnection]] = {}
        for connection in view_connections(d):
            a, b = connection.source_ref, connection.target_ref
            if connection.ref is not None or a not in kinds or b not in kinds or a == b:
                continue
            groups.setdefault(frozenset((a, b)), []).append(connection)
        for pair, connections in groups.items():
            fitting = [(c, r) for c in connections for r in relations if fits(d.type, c, r)]
            first, second = sorted(pair, key=lambda e: names[e])
            between = _("„{first}“ und „{second}“", first=names[first], second=names[second])
            ids = [c.id for c in connections]
            remove = Resolution(
                action="propose",
                label=_("Verbindung entfernen (Vorschlag)"),
                diagram=d.id,
                prompt=_(
                    "Entferne in diesem Diagramm die Verbindung(en) {items} zwischen {between}.",
                    items=", ".join(ids),
                    between=between,
                ),
            )
            if fitting:
                connection, matching = fitting[0]
                message = _(
                    "„{title}“ zeigt eine Verbindung zwischen {between}, die nicht mit der Beziehung {relation_text} verknüpft ist.",
                    title=d.title,
                    between=between,
                    relation_text=_relation_text(matching, names),
                )
                fixes = [
                    Resolution(
                        action="link_relation",
                        label=_("Mit Beziehung {matching_id} verknüpfen", matching_id=matching.id),
                        diagram=d.id,
                        view=connection.id,
                        relation=matching.id,
                    ),
                    remove,
                ]
            else:
                if d.interaction is not None and pair <= present and pair not in deployed and pair not in related:
                    continue  # exactly what message-without-connection reports: reported there, once
                message = _(
                    "„{title}“ zeigt eine Verbindung zwischen {between}, für die das Projektmodell keine Beziehung kennt; Beziehung anlegen oder die Verbindung entfernen.",
                    title=d.title,
                    between=between,
                )
                kind = _creatable_kind(d, connections[0], kinds)
                fixes = [remove]
                if kind is not None:
                    source, target = connections[0].source_ref, connections[0].target_ref
                    fixes.insert(
                        0,
                        Resolution(
                            action="create_relation",
                            label=_(
                                "Beziehung anlegen: „{source}“ {verb} „{target}“",
                                source=names[source or ""],
                                verb=_verb(kind),
                                target=names[target or ""],
                            ),
                            args={"kind": kind, "source": source, "target": target},
                        ),
                    )
            yield _finding(
                "connection-without-relation",
                f"{d.id}:{first}+{second}",
                "suggestion",
                "low",
                message,
                diagrams=[d.id],
                relation=fitting[0][1].id if fitting else None,
                resolutions=fixes,
                elements=_placed(d, ids)[0],
                edges=_placed(d, ids)[1],
            )


def complete_view_missing_relation(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    kinds, names = _kinds(snapshot), _names(snapshot)
    views = _views(snapshot, kinds)
    for d in _diagrams(snapshot):
        complete = set(d.complete) & set(shown_relation_kinds(d.type))
        if not complete:
            continue
        for relation in snapshot.project.relations:
            if relation.kind not in complete or relation.id in d.hidden_relations:
                continue
            if not _shows_both_ends(d, relation) or _shows(d, relation, kinds, views):
                continue
            yield _finding(
                "complete-view-missing-relation",
                f"{d.id}:{relation.id}",
                "risk",
                "medium",
                _(
                    "„{title}“ ist für diese Beziehungsart als vollständig erklärt und zeigt beide Elemente, aber nicht die Beziehung {relation_text}; zeichnen oder hier bewusst ausblenden.",
                    title=d.title,
                    relation_text=_relation_text(relation, names),
                ),
                diagrams=[d.id],
                elements=_views_of(d, relation.source) + _views_of(d, relation.target),
                relation=relation.id,
                resolutions=[
                    Resolution(
                        action="propose",
                        label=_("Zeichnen (Vorschlag)"),
                        diagram=d.id,
                        prompt=_draw_prompt(relation, names),
                    ),
                    Resolution(action="hide", label=_("Hier bewusst ausblenden"), diagram=d.id, relation=relation.id),
                    _remove(relation),
                ],
            )


_SORT_MODES = {"synchCall": "sync", "asynchCall": "async", "asynchSignal": "async"}
_MODE_SORTS = {"sync": "synchCall", "async": "asynchCall"}


_REVERSED_DIRECTIONS = {"forward": "backward"}  # an edge drawn target -> source: its arrow points the other way


def _observed(diagram: Diagram, connection: ViewConnection, relation: Relation) -> dict[str, str]:
    """The property values a linked view states, relative to the relation: an edge's protocol, port and explicit
    direction (an edge drawn from the relation's target to its source turns forward into backward, a value no
    relation has, so it always differs); a message's mode."""
    if diagram.interaction is not None:
        message = sequence.find(diagram.interaction, connection.id)
        mode = _SORT_MODES.get(getattr(message, "sort", ""))
        return {"mode": mode} if mode else {}
    edge = next(e for e in diagram.edges if e.id == connection.id)
    values = {k: edge.properties[k] for k in ("protocol", "port") if edge.properties.get(k)}
    if edge.direction is not None:
        reversed_ = (connection.source_ref, connection.target_ref) == (relation.target, relation.source)
        values["direction"] = _REVERSED_DIRECTIONS.get(edge.direction, edge.direction) if reversed_ else edge.direction
    return values


def _shown_value(key: str, value: str) -> str:
    """A value as the finding names it: the synthetic `backward` of an edge drawn against the relation in words."""
    if key == "direction" and value == _REVERSED_DIRECTIONS["forward"]:
        return _("ein Pfeil entgegen der Modellrichtung")
    return _q(value)


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _adapt_prompt(diagram: Diagram, ids: list[str], key: str, value: str, relation: Relation) -> str:
    if key == "mode":
        sort = _MODE_SORTS.get(value, value)
        change = _("setze bei den Nachrichten {items} die Art (sort) auf {sort}", items=", ".join(ids), sort=sort)
    elif key == "direction":
        change = _(
            "setze bei den Kanten {items} die Richtung (direction) so, dass sie „{value}“ von {relation_source} zu {relation_target} bedeutet",
            items=", ".join(ids),
            value=value,
            relation_source=relation.source,
            relation_target=relation.target,
        )
    else:
        change = _(
            "setze bei den Kanten {items} die Eigenschaft {key} auf „{value}“",
            items=", ".join(ids),
            key=key,
            value=value,
        )
    return _(
        "Gleiche dieses Diagramm an die Projektbeziehung {relation_id} an: {change}, wie im Projektmodell.",
        relation_id=relation.id,
        change=change,
    )


def relation_property_conflict(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    names = _names(snapshot)
    connections = _connections(snapshot)
    for relation in snapshot.project.relations:
        allowed = RELATION_RULES[relation.kind][2] if relation.kind in RELATION_RULES else ()
        # key -> normalised value -> [(diagram or None for the model, shown value, connection id)]
        seen: dict[str, dict[str, list[tuple[Diagram | None, str, str | None]]]] = {}
        observed: dict[tuple[str, str], dict[str, str]] = {}  # (diagram, connection) -> its values
        for key, value in relation.properties.items():
            seen.setdefault(key, {}).setdefault(_norm(value), []).append((None, value, None))
        for d in _diagrams(snapshot):
            for connection in connections[d.id]:
                if connection.ref != relation.id:
                    continue
                observed[(d.id, connection.id)] = _observed(d, connection, relation)
                for key, value in observed[(d.id, connection.id)].items():
                    if key in allowed:
                        seen.setdefault(key, {}).setdefault(_norm(value), []).append((d, value, connection.id))
        for key, values in seen.items():
            if len(values) < 2:
                continue
            parts = []
            for sources in values.values():
                where = dict.fromkeys(
                    _("im Modell") if d is None else _("in „{title}“", title=d.title) for d, _v, _c in sources
                )
                joined = _(" und ").join(where)
                parts.append(f"{_shown_value(key, sources[0][1])} {joined}")
            views = [(d, c) for sources in values.values() for d, __, c in sources if d is not None and c]
            diagrams = list(dict.fromkeys(d.id for d, __ in views))
            first = next(d for d, __ in views)
            # one model change per value seen, never a default (ADR-0138) …
            fixes = [
                Resolution(
                    action="update_relation",
                    label=_("Im Modell {key} = „{value}“ setzen", key=key, value=sources[0][1]),
                    relation=relation.id,
                    args={"properties": {key: sources[0][1].strip()}},
                )
                for sources in values.values()
                if any(d is not None for d, _v, _c in sources)
                and property_problem(relation.kind, key, sources[0][1].strip()) is None  # never "backward"
            ]
            # … and, where the model states a value, a proposal per view that differs from it
            model_value = relation.properties.get(key)
            if model_value is not None:
                by_id = {d.id: d for d, __ in views}
                for diagram_id in diagrams:
                    d = by_id[diagram_id]
                    ids = [
                        c
                        for v, c in views
                        if v.id == diagram_id and _norm(observed[(diagram_id, c)][key]) != _norm(model_value)
                    ]
                    if ids:
                        fixes.append(
                            Resolution(
                                action="propose",
                                label=_("„{title}“ an das Modell anpassen (Vorschlag)", title=d.title),
                                diagram=d.id,
                                prompt=_adapt_prompt(d, ids, key, model_value, relation),
                            )
                        )
            yield _finding(
                "relation-property-conflict",
                f"{relation.id}:{key}",
                "risk",
                "medium",
                _(
                    "Die Beziehung {relation_text} hat widersprüchliche Angaben für {key}: {items}.",
                    relation_text=_relation_text(relation, names),
                    key=key,
                    items="; ".join(parts),
                ),
                diagrams=diagrams,
                relation=relation.id,
                resolutions=fixes,
                elements=_placed(first, list(dict.fromkeys(c for d, c in views if d.id == first.id)))[0],
                edges=_placed(first, list(dict.fromkeys(c for d, c in views if d.id == first.id)))[1],
            )


def deployed_on_several_nodes(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    kinds, names = _kinds(snapshot), _names(snapshot)
    hosts: dict[str, dict[str, list[str]]] = {}  # element -> node element -> where ("" = model, else diagram ids)
    relations: dict[str, list[Relation]] = {}
    for relation in snapshot.project.relations:
        if relation.kind == "deployed_on":
            hosts.setdefault(relation.source, {}).setdefault(relation.target, []).append("")
            relations.setdefault(relation.source, []).append(relation)
    for d in _diagrams(snapshot):
        for _a, element, _b, host in nested(d, kinds):
            if host is not None:  # the nearest node it runs on (a pod inside a cluster runs on the pod)
                hosts.setdefault(element, {}).setdefault(host, []).append(d.id)
    by_id = {d.id: d for d in _diagrams(snapshot)}
    for element, nodes in hosts.items():
        if len(nodes) < 2:
            continue
        parts = []
        for node, where in nodes.items():
            places = [_("Modell") if w == "" else _q(by_id[w].title) for w in dict.fromkeys(where)]
            parts.append(_("„{name}“ ({items})", name=names.get(node, node), items=", ".join(places)))
        diagrams = list(dict.fromkeys(w for where in nodes.values() for w in where if w))
        name = names.get(element, element)
        fixes = [
            Resolution(
                action="remove_relation",
                label=_("Modell: nicht mehr auf „{name}“", name=names.get(r.target, r.target)),
                relation=r.id,
            )
            for r in relations.get(element, [])
        ]
        fixes += [
            Resolution(
                action="propose",
                label=_("„{title}“ anpassen (Vorschlag)", title=by_id[d].title),
                diagram=d,
                prompt=_(
                    "„{name}“ läuft laut Projektmodell oder anderen Diagrammen auf einem anderen Knoten; platziere es in diesem Diagramm auf dem richtigen Knoten ({items}).",
                    name=name,
                    items=", ".join(parts),
                ),
            )
            for d in diagrams
        ]
        yield _finding(
            "deployed-on-several-nodes",
            element,
            "risk",
            "medium",
            _("„{name}“ läuft auf mehreren Knoten: {items}.", name=name, items=", ".join(parts)),
            diagrams=diagrams,
            elements=_views_of(by_id[diagrams[0]], element) if diagrams else [],
            model_element=element,
            resolutions=fixes,
        )


def _element_ancestors(snapshot: ProjectSnapshot, element_id: str) -> list[str]:
    parents = {e.id: e.parent for e in snapshot.project.elements}
    out: list[str] = []
    current = parents.get(element_id)
    while current is not None and current not in out:
        out.append(current)
        current = parents.get(current)
    return out


def containment_conflict(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    kinds, names = _kinds(snapshot), _names(snapshot)
    parents = {e.id: e.parent for e in snapshot.project.elements}
    holders: dict[str, dict[str, list[Diagram]]] = {}  # element -> containing element -> diagrams
    for d in _diagrams(snapshot):
        for _a, element, holder, _b in nested(d, kinds):
            if holder is not None and kinds.get(holder) != "node":  # nesting in a node is deployed_on
                holders.setdefault(element, {}).setdefault(holder, []).append(d)
    for element, found in holders.items():
        parent = parents.get(element)
        ancestors = _element_ancestors(snapshot, element)
        wrong = {h: ds for h, ds in found.items() if parent is not None and h not in ancestors}
        if parent is None and len(found) > 1:
            wrong = found
        if not wrong:
            continue
        diagrams = list(dict.fromkeys(d.id for ds in wrong.values() for d in ds))
        views = ", ".join(
            _("„{name}“ ({quoted})", name=names.get(h, h), quoted=_quoted(dict.fromkeys(d.title for d in ds)))
            for h, ds in wrong.items()
        )
        model = (
            _("im Modell ist es Teil von „{name}“", name=names.get(parent, parent))
            if parent
            else _("das Modell sagt nichts")
        )
        first = next(d for ds in wrong.values() for d in ds)
        name = names.get(element, element)
        fixes = [
            Resolution(
                action="move_element",
                label=_("Im Modell Teil von „{name}“", name=names.get(h, h)),
                model_element=element,
                args={"parent": h},
            )
            for h in wrong
        ]
        if parent is not None:
            fixes += [
                Resolution(
                    action="propose",
                    label=_("„{title}“ an das Modell anpassen (Vorschlag)", title=d.title),
                    diagram=d.id,
                    prompt=_(
                        "„{name}“ ist laut Projektmodell Teil von „{parent}“; platziere es in diesem Diagramm entsprechend (oder außerhalb des anderen Elements).",
                        name=name,
                        parent=names.get(parent, parent),
                    ),
                )
                for d in {d.id: d for ds in wrong.values() for d in ds}.values()
            ]
        yield _finding(
            "containment-conflict",
            element,
            "risk",
            "medium",
            _("„{name}“ liegt in den Diagrammen in {views}; {model}.", name=name, views=views, model=model),
            diagrams=diagrams,
            elements=_views_of(first, element),
            model_element=element,
            resolutions=fixes,
        )


def invalid_relation_link(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    relations = {r.id: r for r in snapshot.project.relations}
    for d in _diagrams(snapshot):
        stale = [
            c
            for c in view_connections(d)
            if c.ref is not None and (c.ref not in relations or not fits(d.type, c, relations[c.ref]))
        ]
        hidden = [h for h in d.hidden_relations if h not in relations]  # an undo or restore brought them back
        if not stale and not hidden:
            continue
        parts = []
        if stale:
            parts.append(
                _(
                    "{count} Verbindung(en) verweisen auf Beziehungen, die es nicht mehr gibt oder deren Elemente nicht mehr passen",
                    count=len(stale),
                )
            )
        if hidden:
            parts.append(
                _(
                    "{count} ausgeblendete Beziehung(en) gibt es nicht mehr ({items})",
                    count=len(hidden),
                    items=", ".join(hidden),
                )
            )
        elements, edges = _placed(d, [c.id for c in stale])
        yield _finding(
            "invalid-relation-link",
            d.id,
            "risk",
            "medium",
            _(
                "In „{title}“: {items}. „Modell abgleichen“ löst diese Verknüpfungen.",
                title=d.title,
                items="; ".join(parts),
            ),
            diagrams=[d.id],
            elements=elements,
            edges=edges,
            relation=stale[0].ref if stale else hidden[0],
            resolutions=[Resolution(action="sync", label=_("Modell abgleichen"))],
        )


# ---- requirements (spec: requirements.md) ----------------------------------------------------------------------

REQUIREMENT_LISTED = 40  # requirement ids a finding names; the message counts the rest
RAW_AFTER = timedelta(days=1)


def _listed(ids: list[str]) -> list[str]:
    return ids[:REQUIREMENT_LISTED]


def _title_of(requirement: Requirement) -> str:
    return requirement.statement or requirement.raw


def _more(count: int) -> str:
    return "" if count <= REQUIREMENT_LISTED else _(" (die ersten {limit} sind markiert)", limit=REQUIREMENT_LISTED)


def _refined(snapshot: ProjectSnapshot) -> list[Requirement]:
    box = snapshot.requirements
    return [r for r in box.requirements if r.status == "refined"] if box is not None else []


def requirement_unrefined(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    if snapshot.requirements is None:
        return
    cutoff = datetime.now(UTC) - RAW_AFTER
    old = []
    for r in snapshot.requirements.requirements:
        if r.status != "raw":
            continue
        try:
            created = datetime.fromisoformat(r.created_at)
        except ValueError:
            continue
        if created.astimezone(UTC) < cutoff:
            old.append(r.id)
    if old:
        yield _finding(
            "requirement-unrefined",
            "all",
            "suggestion",
            "low",
            _(
                "{count} Anforderung(en) sind seit über einem Tag unbearbeitet (roh){more}. Aufbereiten und übernehmen macht sie zur Grundlage für Szenarien und Treiber.",
                count=len(old),
                more=_more(len(old)),
            ),
            requirements=_listed(old),
        )


def requirement_uncovered(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    box = snapshot.requirements
    if box is None:
        return
    cover = coverage_map(box, snapshot.project)
    themes: dict[str, list[str]] = {}
    for r in _refined(snapshot):
        if cover.get(r.id) == "uncovered":
            themes.setdefault(r.theme, []).append(r.id)
    for theme, ids in sorted(themes.items()):
        label = _("Thema „{theme}“", theme=theme) if theme else _("Ohne Thema")
        yield _finding(
            "requirement-uncovered",
            theme or "none",
            "risk",
            "medium",
            _(
                "{label}: {count} verfeinerte Anforderung(en) haben weder Treiber noch Szenario noch Entscheidung{more}.",
                label=label,
                count=len(ids),
                more=_more(len(ids)),
            ),
            requirements=_listed(ids),
        )


def requirement_no_stakeholder(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    known = {s.id for s in snapshot.project.stakeholders}
    ids = [r.id for r in _refined(snapshot) if not any(s in known for s in r.stakeholders)]
    if ids:
        yield _finding(
            "requirement-no-stakeholder",
            "all",
            "suggestion",
            "low",
            _(
                "{count} verfeinerte Anforderung(en) nennen keinen Stakeholder{more}.",
                count=len(ids),
                more=_more(len(ids)),
            ),
            requirements=_listed(ids),
        )


def quality_requirement_no_measure(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    ids = [r.id for r in _refined(snapshot) if r.kind == "quality" and not r.measure]
    if ids:
        yield _finding(
            "quality-requirement-no-measure",
            "all",
            "risk",
            "medium",
            _(
                "{count} Qualitätsanforderung(en) haben kein Maß, an dem man sie prüfen kann{more}.",
                count=len(ids),
                more=_more(len(ids)),
            ),
            requirements=_listed(ids),
        )


def requirement_conflict_open(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    if snapshot.requirements is None:
        return
    for r in snapshot.requirements.requirements:
        if r.status in ("rejected", "duplicate"):
            continue
        for issue in r.issues:
            if issue.kind == "conflict" and not issue.resolved:
                other = _(" mit {issue_other}", issue_other=issue.other) if issue.other else ""
                yield _finding(
                    "requirement-conflict-open",
                    f"{r.id}-{issue.other or 'x'}",
                    "risk",
                    "medium",
                    _(
                        "Anforderung {r_id} „{title_of}“ widerspricht{other}: {reason}.",
                        r_id=r.id,
                        title_of=_title_of(r)[:80],
                        other=other,
                        reason=issue.text or _("ungelöst"),
                    ),
                    requirements=[r.id, *([issue.other] if issue.other else [])],
                )


def requirement_duplicate_open(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    box = snapshot.requirements
    if box is None:
        return
    seen: set[tuple[str, str]] = set()
    for r in box.requirements:
        if r.status in ("rejected", "duplicate"):
            continue
        for issue in r.issues:
            if issue.kind == "duplicate" and not issue.resolved and issue.other:
                pair = tuple(sorted((r.id, issue.other)))
                seen.add((pair[0], pair[1]))
    refined = RequirementBox(requirements=[r for r in box.requirements if r.status == "refined"], seq=box.seq)
    for a, b, __ in duplicate_candidates(refined):
        seen.add((a, b) if a < b else (b, a))
    for a, b in sorted(seen):
        yield _finding(
            "requirement-duplicate-open",
            f"{a}-{b}",
            "suggestion",
            "low",
            _("Anforderungen {a} und {b} sagen fast dasselbe: als Duplikat markieren oder abgrenzen.", a=a, b=b),
            requirements=[a, b],
        )


def scenario_no_requirement(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    if not _refined(snapshot):
        return
    bare = [s for s in active(snapshot.project.scenarios) if not s.requirements]
    if bare:
        names = ", ".join(_q(s.title) for s in bare[:5]) + (" …" if len(bare) > 5 else "")
        yield _finding(
            "scenario-no-requirement",
            "all",
            "suggestion",
            "low",
            _("{count} Szenario(en) nennen keine Anforderung: {names}.", count=len(bare), names=names),
        )


def scenario_no_quality(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    bare = [s for s in active(snapshot.project.scenarios) if not s.qualities]
    if bare:
        names = ", ".join(_q(s.title) for s in bare[:5]) + (" …" if len(bare) > 5 else "")
        yield _finding(
            "scenario-no-quality",
            "all",
            "suggestion",
            "low",
            _("{count} Szenario(en) sind keiner ISO-25010-Qualität zugeordnet: {names}.", count=len(bare), names=names),
        )


def decision_no_basis(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    for d in snapshot.project.decisions:
        if d.status == "accepted" and not d.drivers and not d.requirements:
            yield _finding(
                "decision-no-basis",
                d.id,
                "risk",
                "medium",
                _(
                    "{adr} „{title}“ ist angenommen, nennt aber weder Treiber noch Anforderung: die Begründung fehlt.",
                    adr=adr_label(d.id),
                    title=d.title,
                ),
            )


def stakeholder_no_requirement(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    box = snapshot.requirements
    if box is None or not box.requirements:
        return
    named = {s for r in box.requirements for s in r.stakeholders}
    missing = [s for s in snapshot.project.stakeholders if s.influence == "high" and s.id not in named]
    if missing:
        names = ", ".join(_q(s.name) for s in missing[:5]) + (" …" if len(missing) > 5 else "")
        yield _finding(
            "stakeholder-no-requirement",
            "all",
            "suggestion",
            "low",
            _("Einflussreiche Stakeholder ohne Anforderung: {names}.", names=names),
        )


def vision_missing(snapshot: ProjectSnapshot) -> Iterator[ProjectFinding]:
    project = snapshot.project
    if (project.vision is None or project.vision.is_empty()) and (project.diagrams or project.drivers):
        yield _finding(
            "vision-missing",
            "all",
            "suggestion",
            "low",
            _("Das Projekt hat keine Vision: ohne sie fehlt die Richtung für Vorschläge und Dokumentation."),
        )


PROJECT_RULES: list[ProjectRule] = [
    ProjectRule("empty-section", N_("Abschnitt ohne Diagramme"), "suggestion", empty_section),
    ProjectRule(
        "section-without-overview",
        N_("Abschnitt mit mehreren Diagrammen ohne Übersicht"),
        "suggestion",
        section_without_overview,
    ),
    ProjectRule(
        "missing-diagram-types",
        N_("Abschnitt nur mit Struktur oder nur mit Abläufen"),
        "suggestion",
        missing_diagram_types,
    ),
    ProjectRule("duplicate-title", N_("Gleiche oder fast gleiche Diagrammtitel"), "risk", duplicate_title),
    ProjectRule("empty-diagram", N_("Leeres Diagramm"), "suggestion", empty_diagram),
    ProjectRule("vision-missing", N_("Projekt ohne Vision"), "suggestion", vision_missing),
    ProjectRule("dangling-element-ref", N_("Verweis auf ein fehlendes Projektelement"), "risk", dangling_element_ref),
    ProjectRule("element-name-drift", N_("Verknüpftes Element mit abweichendem Namen"), "risk", element_name_drift),
    ProjectRule(
        "incompatible-element-link",
        N_("Verknüpfung, die die Art des Elements nicht darstellen kann"),
        "risk",
        incompatible_element_link,
    ),
    ProjectRule("unused-element", N_("Projektelement ohne Verwendung"), "suggestion", unused_element),
    ProjectRule(
        "element-not-in-structure",
        N_("Element in Abläufen, aber in keinem Verteilungsdiagramm"),
        "suggestion",
        element_not_in_structure,
    ),
    ProjectRule(
        "message-without-connection",
        N_("Nachrichten zwischen Elementen ohne Verbindung in der Verteilung"),
        "risk",
        message_without_connection,
    ),
    ProjectRule(
        "unlinked-namesake", N_("Gleichnamiges, aber nicht verknüpftes Element"), "suggestion", unlinked_namesake
    ),
    ProjectRule(
        "similar-element-names", N_("Projektelemente mit fast gleichem Namen"), "suggestion", similar_element_names
    ),
    ProjectRule(
        "relation-not-shown",
        N_("Beziehung in keinem Verteilungs- oder Komponentendiagramm"),
        "suggestion",
        relation_not_shown,
    ),
    ProjectRule(
        "connection-without-relation",
        N_("Verbindung ohne Beziehung im Modell"),
        "suggestion",
        connection_without_relation,
    ),
    ProjectRule(
        "complete-view-missing-relation",
        N_("Vollständiges Diagramm ohne eine seiner Beziehungen"),
        "risk",
        complete_view_missing_relation,
    ),
    ProjectRule(
        "relation-property-conflict",
        N_("Widersprüchliche Angaben zu einer Beziehung"),
        "risk",
        relation_property_conflict,
    ),
    ProjectRule("deployed-on-several-nodes", N_("Element auf mehreren Knoten"), "risk", deployed_on_several_nodes),
    ProjectRule("containment-conflict", N_("Widersprüchliche Enthaltenseins-Angaben"), "risk", containment_conflict),
    ProjectRule(
        "invalid-relation-link", N_("Verknüpfung mit einer ungültigen Beziehung"), "risk", invalid_relation_link
    ),
    ProjectRule(
        "requirement-unrefined", N_("Anforderungen, die lange roh bleiben"), "suggestion", requirement_unrefined
    ),
    ProjectRule(
        "requirement-uncovered",
        N_("Anforderung ohne Treiber, Szenario oder Entscheidung"),
        "risk",
        requirement_uncovered,
    ),
    ProjectRule(
        "requirement-no-stakeholder", N_("Anforderung ohne Stakeholder"), "suggestion", requirement_no_stakeholder
    ),
    ProjectRule(
        "quality-requirement-no-measure", N_("Qualitätsanforderung ohne Maß"), "risk", quality_requirement_no_measure
    ),
    ProjectRule(
        "requirement-conflict-open", N_("Offener Widerspruch zwischen Anforderungen"), "risk", requirement_conflict_open
    ),
    ProjectRule(
        "requirement-duplicate-open", N_("Fast gleiche Anforderungen"), "suggestion", requirement_duplicate_open
    ),
    ProjectRule("scenario-no-requirement", N_("Szenario ohne Anforderung"), "suggestion", scenario_no_requirement),
    ProjectRule("scenario-no-quality", N_("Szenario ohne ISO-25010-Qualität"), "suggestion", scenario_no_quality),
    ProjectRule("decision-no-basis", N_("Angenommene Entscheidung ohne Grundlage"), "risk", decision_no_basis),
    ProjectRule(
        "stakeholder-no-requirement",
        N_("Einflussreicher Stakeholder ohne Anforderung"),
        "suggestion",
        stakeholder_no_requirement,
    ),
]


# ---- checks -------------------------------------------------------------------------------------------------


def _sorted(snapshot: ProjectSnapshot, findings: list[ProjectFinding]) -> list[ProjectFinding]:
    order = {d.id: i for i, (__, d) in enumerate(snapshot.members)}
    return sorted(findings, key=lambda f: (rules.sort_key(f), order.get(f.diagram or "", -1)))


def run_project_checks(snapshot: ProjectSnapshot) -> list[ProjectFinding]:
    """Findings of every project rule plus the rule findings of every member diagram, most severe first."""
    findings = [f for rule in PROJECT_RULES for f in rule.check(snapshot)]
    for __, d in snapshot.members:
        for f in rules.run_checks(d):
            findings.append(ProjectFinding(**{**f.model_dump(), "id": f"{d.id}:{f.id}"}, diagram=d.id, diagrams=[d.id]))
    return _sorted(snapshot, findings)


def diagram_index(snapshot: ProjectSnapshot) -> dict[str, dict[str, Any]]:
    """Every member's title, type and section, for labelling findings in the UI."""
    return {d.id: {"title": d.title, "type": d.type, "section": m.section} for m, d in snapshot.members}


def model_index(snapshot: ProjectSnapshot) -> dict[str, dict[str, Any]]:
    """Every model element's name and kind, for labelling findings in the UI."""
    return {e.id: {"name": e.name, "kind": e.kind} for e in snapshot.project.elements}


def checks(snapshot: ProjectSnapshot) -> dict[str, Any]:
    """The answer of `GET /api/projects/{pid}/checks`."""
    return {
        "project": snapshot.project.id,
        "rules": [{"id": r.id, "title": _(r.title), "category": r.category} for r in PROJECT_RULES],
        "findings": [f.model_dump() for f in run_project_checks(snapshot)],
        "diagrams": diagram_index(snapshot),
        "model": model_index(snapshot),
    }


# ---- LLM review --------------------------------------------------------------------------------------------

PROJECT_REVIEW_TOOL = ToolSpec(
    "submit_project_review",
    "Submit the review of the whole project: an overall summary and the findings.",
    {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Overall assessment in two or three German sentences."},
            "findings": {
                "type": "array",
                "maxItems": review.MAX_LLM_FINDINGS,
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "severity": {"type": "string", "enum": list(reversed(SEVERITY_ORDER))},
                        "message": {"type": "string", "description": "The finding in one or two German sentences."},
                        "diagram": {
                            "type": "string",
                            "description": "Id of the diagram it concerns; omit for a remark about the project.",
                        },
                        "section": {"type": "string", "description": "Id of the section it concerns, if any."},
                        "elements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ids of elements of that diagram (only together with `diagram`).",
                        },
                        "edges": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ids (e1, e2, ...) of edges of that diagram.",
                        },
                        "suggestion": {
                            "type": "string",
                            "description": "For risks and suggestions about one diagram: a German instruction to the "
                            "diagram assistant that changes that diagram. Omit otherwise.",
                        },
                    },
                    "required": ["category", "severity", "message"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "findings"],
        "additionalProperties": False,
    },
)

PROJECT_REVIEW_SYSTEM_PROMPT = """You review the architecture documentation of a whole project made with Arcviz.

You get the project's sections and diagrams (ids in square brackets, types in «»), the content of every
diagram, and the findings of deterministic rule checks that were already reported. Review the project as
a whole, as an experienced architect would: is the system described completely and consistently across
its diagrams? Do structure (deployment) and behaviour (activity, sequence) fit together? Are names
consistent between diagrams? Is anything important missing? When the project has shared model elements,
diagram elements marked "(= element <id>)" are views of the same element across diagrams.

- strengths: what is already good (category "strength", severity "info");
- risks: inconsistencies or gaps that can mislead or fail (category "risk", severity low, medium or high);
- suggestions: improvements without an acute risk (category "suggestion").

Rules:
- Do not repeat the rule findings and do not re-review single diagrams in detail; add what only the
  whole project shows.
- Name the diagram a finding concerns by its id in `diagram`, and only then elements (ids in square
  brackets) and edges (e-ids) of that diagram. A remark about the whole project has no diagram; a remark
  about a section names its id in `section`.
- Judge only what the diagrams show; say what is missing rather than guessing details.
- Write message, suggestion and summary in German. Keep each message to one or two sentences.
- A suggestion is an instruction to the diagram assistant that changes the named diagram.
- Report at most 12 findings, the most important first.
- Accepted architecture decisions, when listed, are binding: when a diagram or the project contradicts one, report a risk
  finding (severity medium or high) whose message starts with "Widerspricht ADR-<n>:" and says how.
- Answer by calling submit_project_review exactly once. Do not answer in text.
"""


def _budget(snapshot: ProjectSnapshot) -> int:
    return max(MIN_DIAGRAM_BUDGET, PROJECT_REVIEW_BUDGET // max(1, len(snapshot.members)))


def is_partial(snapshot: ProjectSnapshot) -> bool:
    """True if a diagram does not fit its share of the prompt and is shown with collapsed containers."""
    budget = _budget(snapshot)
    return any(len(d.summary()) > budget for __, d in snapshot.members)


def _structure(snapshot: ProjectSnapshot) -> list[str]:
    project = snapshot.project
    by_section: dict[str | None, list[Diagram]] = {}
    for member, d in snapshot.members:
        by_section.setdefault(member.section, []).append(d)
    lines: list[str] = []

    def level(section_id: str | None, depth: int, seen: frozenset[str]) -> None:
        for section in project.children(section_id):
            if section.id not in seen:
                lines.append(f"{'  ' * depth}- Section: {section.title} [{section.id}]")
                level(section.id, depth + 1, seen | {section.id})
        lines.extend(f"{'  ' * depth}- [{d.id}] «{d.type}» {d.title}" for d in by_section.get(section_id, []))

    level(None, 0, frozenset())
    return lines or ["(no diagrams)"]


def _model_lines(snapshot: ProjectSnapshot) -> list[str]:
    usages = _usages(snapshot)
    lines = []
    for element in snapshot.project.elements:
        where = sorted({d.id for d, __ in usages.get(element.id, [])})
        parent = f" (part of {element.parent})" if element.parent else ""
        description = f" — {element.description}" if element.description else ""
        lines.append(
            f"- [{element.id}] «{element.kind}» {element.name}{parent}{description}"
            + (f" (in: {', '.join(where)})" if where else " (not shown in any diagram)")
        )
    return lines


RELATION_DIGEST_BUDGET = 6_000  # characters of model relations in the review digest


def _relation_lines(snapshot: ProjectSnapshot) -> list[str]:
    """The relations, one line each, within RELATION_DIGEST_BUDGET (descriptions dropped first, then counted)."""
    relations = snapshot.project.relations
    for described in (True, False):
        lines = [relation_line(r, described) for r in relations]
        if sum(len(line) + 1 for line in lines) <= RELATION_DIGEST_BUDGET:
            return lines
    out: list[str] = []
    used = 0
    for relation in relations:
        line = relation_line(relation, False)
        if used + len(line) + 60 > RELATION_DIGEST_BUDGET:
            out.append(f"(+ {len(relations) - len(out)} more relations)")
            break
        out.append(line)
        used += len(line) + 1
    return out


def digest_lines(snapshot: ProjectSnapshot) -> list[str]:
    """The project digest (ADR-0064): title, structure, model, relations and every diagram within the budget. The
    project review and the scenario analysis (spec: decision-support.md) read the project through it."""
    project = snapshot.project
    lines = [f"Project: {project.title}"]
    if project.description:
        lines.append(f"Description: {project.description}")
    lines += ["", "Structure:", *_structure(snapshot), ""]
    if project.elements:
        lines += [
            "Model elements (shared by the diagrams; views marked '(= element id)'):",
            *_model_lines(snapshot),
            "",
        ]
    if project.relations:
        lines += ["Model relations:", *_relation_lines(snapshot), ""]
    budget = _budget(snapshot)
    for member, d in snapshot.members:
        where = f" (section {member.section})" if member.section else ""
        lines += [f"Diagram [{d.id}] «{d.type}» {d.title}{where}:", d.summary(budget=budget), ""]
    if is_partial(snapshot):
        lines += [PARTIAL_NOTE, ""]
    return lines


def review_input(snapshot: ProjectSnapshot, rule_findings: list[ProjectFinding]) -> str:
    """The user message of the review call: the project digest (ADR-0064) and its accepted decisions (ADR-0168)."""
    lines = digest_lines(snapshot)
    decisions = decision_lines(snapshot.project.decisions)
    if decisions:
        lines += ["Accepted architecture decisions (binding):", decisions, ""]
    if rule_findings:
        lines.append("Rule findings (already reported, do not repeat):")
        for f in rule_findings:
            where = f" (diagram {f.diagram})" if f.diagram else f" (section {f.section})" if f.section else ""
            refs = ", ".join(f.elements + f.edges)
            lines.append(f"- [{f.severity}]{where} {f.message}" + (f" (elements: {refs})" if refs else ""))
    else:
        lines.append("Rule findings: none")
    return "\n".join(lines)


def _resolve_diagram(snapshot: ProjectSnapshot, ref: Any) -> Diagram | None:
    if not isinstance(ref, str) or not ref.strip():
        return None
    for __, d in snapshot.members:
        if d.id == ref:
            return d
    matches = [d for __, d in snapshot.members if d.title.strip().casefold() == ref.strip().casefold()]
    return matches[0] if len(matches) == 1 else None


def _llm_finding(raw: Any, snapshot: ProjectSnapshot, number: int) -> ProjectFinding | None:
    if not isinstance(raw, dict):
        return None
    target = _resolve_diagram(snapshot, raw.get("diagram"))
    if target is None:  # a project-level finding: element and edge ids mean nothing without a diagram
        raw = {k: v for k, v in raw.items() if k not in ("elements", "edges")}
    base = review._finding(raw, target if target is not None else Diagram(id="-", title=""), number)
    if base is None:
        return None
    section_ref = raw.get("section")
    section = snapshot.project.find_section(section_ref) if isinstance(section_ref, str) else None
    return ProjectFinding(
        **base.model_dump(),
        diagram=target.id if target is not None else None,
        diagrams=[target.id] if target is not None else [],
        section=section.id if section is not None else None,
    )


def parse_project_review(data: Any, snapshot: ProjectSnapshot) -> tuple[str | None, list[ProjectFinding], int]:
    """Validate the arguments of a `submit_project_review` call: (summary, findings, dropped count).

    Raises ValueError if `data` is not an object with a `findings` list."""
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("The review has no list of findings.")
    findings: list[ProjectFinding] = []
    dropped = 0
    for raw in data["findings"]:
        finding = _llm_finding(raw, snapshot, len(findings) + 1) if len(findings) < review.MAX_LLM_FINDINGS else None
        if finding is None:
            dropped += 1
        else:
            findings.append(finding)
    return review._text(data.get("summary"), review.SUMMARY_LIMIT), findings, dropped


def review_project(provider: LLMProvider, snapshot: ProjectSnapshot) -> dict[str, Any]:
    """Project checks plus one LLM review of the whole project: the answer of `POST /api/projects/{pid}/review`.
    Never raises because of the model: a failed call gives `llm.ok = false` and the rule findings."""
    rule_findings = run_project_checks(snapshot)
    summary: str | None = None
    llm_findings: list[ProjectFinding] = []
    outcome: dict[str, Any] = {"ok": True, "error": None, "dropped": 0, "usage": {}, "partial": is_partial(snapshot)}
    try:
        data, outcome["usage"] = provider.complete_tool(
            review.in_language(PROJECT_REVIEW_SYSTEM_PROMPT, review.CONTRADICTS_PREFIX),
            review_input(snapshot, rule_findings),
            review.localized_tool(PROJECT_REVIEW_TOOL),
        )
        summary, llm_findings, outcome["dropped"] = parse_project_review(data, snapshot)
    except Exception as exc:  # a failed review still returns the rule findings
        outcome.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    return {
        "project": snapshot.project.id,
        "summary": summary,
        "findings": [f.model_dump() for f in _sorted(snapshot, rule_findings + llm_findings)],
        "diagrams": diagram_index(snapshot),
        "model": model_index(snapshot),
        "llm": outcome,
    }
