"""Projects: a section tree (folders), the ordered membership of diagrams and the shared model, with their
operations.

Spec: docs/architecture/specs/projects.md, ADR-0051; the model: shared-project-model.md (ADR-0070). A project
groups diagrams; it never contains diagram content, but holds the project-wide model elements its diagrams'
nodes and lifelines may reference. All mutations go through `apply()`, which validates the arguments against the
operation signatures and restores the project on any exception (like `operations.apply`, ADR-0045).
Which project a diagram belongs to is a cross-document invariant kept by `ProjectService`, so the
membership operations are internal (`PUBLIC_OPERATIONS` leaves them out); so are the element operations,
whose renames and deletes `ModelService` carries into the diagrams.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from . import decision_model as dm
from . import requirement_model as rqm
from .decision_model import Decision, DecisionOption, Scenario, Stakeholder
from .model import now_iso, single_line, slugify
from .operations import OperationError, PropertyValue, validate_arguments
from .project_model import (
    DRIVER_KINDS,
    DRIVER_LEVELS,
    ELEMENT_KINDS,
    MAX_CODE_REFS,
    MAX_DRIVER_DESCRIPTION,
    MAX_DRIVER_TITLE,
    MAX_DRIVERS,
    MAX_ELEMENT_DESCRIPTION,
    MAX_ELEMENT_NAME,
    MAX_ELEMENTS,
    MAX_ID_LENGTH,
    MAX_RELATION_DESCRIPTION,
    MAX_RELATIONS,
    OBJECT_STATUSES,
    RELATION_KINDS,
    SYMMETRIC_RELATIONS,
    CodeRef,
    Driver,
    Element,
    Relation,
    driver_number,
    find_driver,
    find_element,
    find_relation,
    is_retired,
    known_drivers,
    known_elements,
    known_relations,
    name_key,
    next_relation_number,
    property_problem,
    relation_number,
    relation_problem,
    same_relation,
)


class ProjectError(OperationError):
    """Invalid input for a project operation; the project is unchanged."""


class ProjectStateError(ProjectError):
    """The object is in a state that forbids the operation (a delete before the retirement, ADR-0198); REST 409."""


class Section(BaseModel):
    id: str
    title: str
    parent: str | None = None  # None = top level
    element: str | None = None  # the model element the section stands for (ADR-0074); its title follows

    @model_serializer(mode="wrap")
    def _omit_unset_element(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if data.get("element", "") is None:
            del data["element"]  # sections written before M18 dump (and export) unchanged
        return data


class Membership(BaseModel):
    diagram: str
    section: str | None = None  # None = directly in the project
    added_at: str  # when the diagram was placed in this project; the repair keeps the newest


class Project(BaseModel):
    id: str
    title: str
    description: str = ""
    sections: list[Section] = Field(default_factory=list)  # siblings ordered by list position
    diagrams: list[Membership] = Field(default_factory=list)  # members of a section ordered likewise
    elements: list[Element] = Field(default_factory=list)  # the shared model (spec: shared-project-model.md)
    relations: list[Relation] = Field(default_factory=list)  # between elements (spec: model-relations.md)
    # The highest generated relation number: `r<n>` ids are never reused, so a stale link or hidden marker (an undo,
    # a restore) never attaches to another relation. Omitted from dumps while 0.
    relation_seq: int = 0
    # Architecture drivers (spec: architecture-drivers.md, ADR-0163), omitted from dumps while empty; `driver_seq` is the
    # highest generated `d<n>`, so an id is never reused (omitted while 0).
    drivers: list[Driver] = Field(default_factory=list)
    driver_seq: int = 0
    # Decision support (spec: decision-support.md, ADR-0166), each omitted from dumps while empty / 0.
    stakeholders: list[Stakeholder] = Field(default_factory=list)
    stakeholder_seq: int = 0
    scenarios: list[Scenario] = Field(default_factory=list)
    scenario_seq: int = 0
    decisions: list[Decision] = Field(default_factory=list)
    decision_seq: int = 0
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    # The workspace the project belongs to (spec: workspaces-and-sharing.md); None only for data written before
    # workspaces existed, until the migration assigns the default workspace (ADR-0087).
    workspace: str | None = None
    # Explicit roles of workspace members: user id -> "owner" | "editor" | "viewer" (ADR-0083); members without an
    # entry have the default role.
    roles: dict[str, str] = Field(default_factory=dict)
    # A random token set when the project is created (ADR-0127): a project deleted and created again under the same
    # id is another incarnation. None for projects written before M23 (their `created_at` stands in, see
    # `incarnation_of`); omitted from dumps while unset.
    incarnation: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_access(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if data.get("workspace", "") is None:
            del data["workspace"]  # projects written before M20 dump unchanged
        if data.get("roles", None) == {}:
            del data["roles"]
        if data.get("incarnation", "") is None:
            del data["incarnation"]
        if data.get("relations", None) == []:
            del data["relations"]  # projects written before M25 dump (and export) unchanged
        if data.get("relation_seq", None) == 0:
            del data["relation_seq"]
        if data.get("drivers", None) == []:
            del data["drivers"]  # projects written before drivers dump (and export) unchanged
        if data.get("driver_seq", None) == 0:
            del data["driver_seq"]
        for key in ("stakeholders", "scenarios", "decisions"):  # projects written before M29 dump unchanged
            if data.get(key, None) == []:
                del data[key]
            if data.get(f"{key[:-1]}_seq", None) == 0:
                del data[f"{key[:-1]}_seq"]
        return data

    # ---- queries -------------------------------------------------------------------

    def find_section(self, ref: str | None) -> Section | None:
        """A section by id, case-insensitive title or slugified title; None for no match."""
        if not ref:
            return None
        for section in self.sections:
            if section.id == ref:
                return section
        lowered = ref.strip().lower()
        for section in self.sections:
            if section.title.strip().lower() == lowered or section.id == slugify(ref):
                return section
        return None

    def find_element(self, ref: str | None) -> Element | None:
        """A model element by id, case-insensitive name or slugified name; None for no match."""
        return find_element(self.elements, ref)

    def element_children(self, element_id: str) -> list[Element]:
        return [e for e in self.elements if e.parent == element_id]

    def member(self, diagram_id: str) -> Membership | None:
        return next((m for m in self.diagrams if m.diagram == diagram_id), None)

    def children(self, section_id: str | None) -> list[Section]:
        return [s for s in self.sections if s.parent == section_id]

    def members_of(self, section_id: str | None) -> list[Membership]:
        return [m for m in self.diagrams if m.section == section_id]

    def descendants(self, section_id: str) -> set[str]:
        """Ids of every section below `section_id` (terminates on cyclic data)."""
        out: set[str] = set()
        frontier = [section_id]
        while frontier:
            current = frontier.pop()
            for child in self.children(current):
                if child.id not in out and child.id != section_id:
                    out.add(child.id)
                    frontier.append(child.id)
        return out

    def tree(self) -> dict[str, Any]:
        """Nested view: `{"diagrams": [ids], "sections": [{"id", "title", "diagrams", "sections"}]}`."""

        def level(section_id: str | None, seen: frozenset[str]) -> dict[str, Any]:
            return {
                "diagrams": [m.diagram for m in self.members_of(section_id)],
                "sections": [
                    {"id": s.id, "title": s.title, **level(s.id, seen | {s.id})}
                    for s in self.children(section_id)
                    if s.id not in seen
                ],
            }

        return level(None, frozenset())

    def integrity_errors(self) -> list[str]:
        errors: list[str] = []
        ids: set[str] = set()
        for section in self.sections:
            if section.id in ids:
                errors.append(f"duplicate section id '{section.id}'")
            ids.add(section.id)
        parents = {s.id: s.parent for s in self.sections}
        for section in self.sections:
            if section.parent is not None and section.parent not in ids:
                errors.append(f"section '{section.id}' has unknown parent '{section.parent}'")
            seen = {section.id}
            parent = section.parent
            while parent is not None and parent in parents:
                if parent in seen:
                    errors.append(f"section '{section.id}' is part of a cycle")
                    break
                seen.add(parent)
                parent = parents[parent]
        listed: set[str] = set()
        for member in self.diagrams:
            if member.diagram in listed:
                errors.append(f"diagram '{member.diagram}' is listed more than once")
            listed.add(member.diagram)
            if member.section is not None and member.section not in ids:
                errors.append(f"diagram '{member.diagram}' is in unknown section '{member.section}'")
        return errors + self._model_errors()

    def _model_errors(self) -> list[str]:
        errors: list[str] = []
        ids: set[str] = set()
        for element in self.elements:
            if element.id in ids:
                errors.append(f"duplicate element id '{element.id}'")
            ids.add(element.id)
            if not element.id or slugify(element.id) != element.id:
                errors.append(f"invalid element id '{element.id}'")
            if not element.name.strip():
                errors.append(f"element '{element.id}' has an empty name")
            if element.kind not in ELEMENT_KINDS:
                errors.append(f"element '{element.id}' has unknown kind '{element.kind}'")
        parents = {e.id: e.parent for e in self.elements}
        for element in self.elements:
            if element.parent is not None and element.parent not in ids:
                errors.append(f"element '{element.id}' has unknown parent '{element.parent}'")
            seen, parent = {element.id}, element.parent
            while parent is not None and parent in parents:
                if parent in seen:
                    errors.append(f"element '{element.id}' is part of a cycle")
                    break
                seen.add(parent)
                parent = parents[parent]
        for section in self.sections:
            if section.element is not None and section.element not in ids:
                errors.append(f"section '{section.id}' stands for unknown element '{section.element}'")
        return errors + self._relation_errors() + self._driver_errors(ids) + self._decision_support_errors(ids)

    def _driver_errors(self, element_ids: set[str]) -> list[str]:
        errors: list[str] = []
        seen: set[str] = set()
        if len(self.drivers) > MAX_DRIVERS:
            errors.append(f"more than {MAX_DRIVERS} drivers")
        for driver in self.drivers:
            if driver.id in seen:
                errors.append(f"duplicate driver id '{driver.id}'")
            seen.add(driver.id)
            if not driver.id or slugify(driver.id) != driver.id or len(driver.id) > MAX_ID_LENGTH:
                errors.append(f"invalid driver id '{driver.id[:20]}'")
            if not driver.title.strip():
                errors.append(f"driver '{driver.id}' has an empty title")
            if driver.kind not in DRIVER_KINDS:
                errors.append(f"driver '{driver.id}' has unknown kind '{driver.kind}'")
            for axis in ("importance", "risk"):
                if getattr(driver, axis) not in DRIVER_LEVELS:
                    errors.append(f"driver '{driver.id}' has unknown {axis} '{getattr(driver, axis)}'")
            for element in driver.elements:
                if element not in element_ids:
                    errors.append(f"driver '{driver.id}' concerns unknown element '{element}'")
        return errors

    def _decision_support_errors(self, element_ids: set[str]) -> list[str]:
        errors: list[str] = []
        drivers = {d.id for d in self.drivers}
        scenarios = {s.id for s in self.scenarios}
        decisions = {d.id for d in self.decisions}
        people = {s.id for s in self.stakeholders}
        for kind, items in (
            ("stakeholder", self.stakeholders),
            ("scenario", self.scenarios),
            ("decision", self.decisions),
        ):
            seen: set[str] = set()
            for item in items:
                if item.id in seen:
                    errors.append(f"duplicate {kind} id '{item.id}'")
                seen.add(item.id)
                if not item.id or slugify(item.id) != item.id or len(item.id) > MAX_ID_LENGTH:
                    errors.append(f"invalid {kind} id '{item.id[:20]}'")
        for st in self.stakeholders:
            if st.influence not in dm.INFLUENCE:
                errors.append(f"stakeholder '{st.id}' has unknown influence '{st.influence}'")
            errors += [
                f"stakeholder '{st.id}' cares about unknown driver '{d}'" for d in st.drivers if d not in drivers
            ]
        for sc in self.scenarios:
            if sc.kind not in dm.SCENARIO_KINDS:
                errors.append(f"scenario '{sc.id}' has unknown kind '{sc.kind}'")
            if sc.driver is not None and sc.driver not in drivers:
                errors.append(f"scenario '{sc.id}' serves unknown driver '{sc.driver}'")
            errors += [
                f"scenario '{sc.id}' concerns unknown element '{e}'" for e in sc.elements if e not in element_ids
            ]
            errors += [
                f"scenario '{sc.id}' names unknown stakeholder '{s}'" for s in sc.stakeholders if s not in people
            ]
        for de in self.decisions:
            if de.status not in dm.DECISION_STATUSES:
                errors.append(f"decision '{de.id}' has unknown status '{de.status}'")
            if de.chosen is not None and not 0 <= de.chosen < len(de.options):
                errors.append(f"decision '{de.id}' chose option {de.chosen}, which it does not have")
            if de.scenario is not None and de.scenario not in scenarios:
                errors.append(f"decision '{de.id}' comes from unknown scenario '{de.scenario}'")
            if de.superseded_by is not None and (de.superseded_by not in decisions or de.superseded_by == de.id):
                errors.append(f"decision '{de.id}' is superseded by unknown decision '{de.superseded_by}'")
            errors += [f"decision '{de.id}' concerns unknown driver '{d}'" for d in de.drivers if d not in drivers]
            errors += [
                f"decision '{de.id}' names unknown stakeholder '{s}'" for s in de.stakeholders if s not in people
            ]
            errors += [
                f"decision '{de.id}' concerns unknown element '{e}'" for e in de.elements if e not in element_ids
            ]
        return errors

    def find_driver(self, ref: str | None) -> Driver | None:
        return find_driver(self.drivers, ref)

    def _relation_errors(self) -> list[str]:
        errors: list[str] = []
        kinds = {e.id: e.kind for e in self.elements}
        ids: set[str] = set()
        pairs: set[tuple[str, ...]] = set()  # kind and ends, unordered for symmetric kinds: duplicates in O(n)
        if len(self.relations) > MAX_RELATIONS:
            errors.append(f"more than {MAX_RELATIONS} relations")
        for relation in self.relations:
            if relation.id in ids:
                errors.append(f"duplicate relation id '{relation.id}'")
            ids.add(relation.id)
            if not relation.id or slugify(relation.id) != relation.id:
                errors.append(f"invalid relation id '{relation.id}'")
            if len(relation.id) > MAX_ID_LENGTH:
                errors.append(f"relation id '{relation.id[:20]}…' is longer than {MAX_ID_LENGTH} characters")
            for end in (relation.source, relation.target):
                if len(end) > MAX_ID_LENGTH:
                    errors.append(f"relation '{relation.id[:20]}' ends at an element id longer than {MAX_ID_LENGTH}")
            if relation.kind not in RELATION_KINDS:
                errors.append(f"relation '{relation.id}' has unknown kind '{relation.kind}'")
            for end in ("source", "target"):
                if getattr(relation, end) not in kinds:
                    errors.append(f"relation '{relation.id}' has unknown {end} '{getattr(relation, end)}'")
            if relation.source == relation.target:
                errors.append(f"relation '{relation.id}' connects an element with itself")
            elif relation.kind in RELATION_KINDS and relation.source in kinds and relation.target in kinds:
                problem = relation_problem(relation.kind, kinds[relation.source], kinds[relation.target])
                if problem:
                    errors.append(f"relation '{relation.id}': {problem}")
                ends = (relation.source, relation.target)
                pair = (relation.kind, *(sorted(ends) if relation.kind in SYMMETRIC_RELATIONS else ends))
                if pair in pairs:
                    errors.append(f"relation '{relation.id}' duplicates another {relation.kind} relation")
                pairs.add(pair)
            if relation.kind in RELATION_KINDS:
                for key, value in relation.properties.items():
                    problem = property_problem(relation.kind, key, value)
                    if problem:
                        errors.append(f"relation '{relation.id}': {problem}")
            if len(relation.description) > MAX_RELATION_DESCRIPTION:
                errors.append(f"relation '{relation.id}' has a description longer than {MAX_RELATION_DESCRIPTION}")
        return errors

    def find_relation(self, ref: str | None) -> Relation | None:
        return find_relation(self.relations, ref)


# The size guard of the project document (#212, ADR-0153): measured as the Cosmos DB SDK sends it (compact JSON,
# non-ASCII escaped: six bytes for `€`), whichever encoding the 2 MB item limit counts, with room for the item's own
# fields. Every backend applies it, so behaviour is the same everywhere.
MAX_ORIGIN = 40  # characters of an object's `origin`
MAX_PROJECT_BYTES = 1_900_000
# Membership writes (placing, adopting a diagram) add ~150 bytes each and may go this far above MAX_PROJECT_BYTES, so
# a create or the membership repair never fails on a full project (bounded overshoot, coordinator review of #212).
MAX_MEMBERSHIP_BYTES = 2_000_000


def document_size(project: Project) -> int:
    """The project document's size as the Cosmos DB SDK serialises a request body (`ensure_ascii=True`)."""
    return len(json.dumps(project.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True))


def check_document_size(project: Project, before: Project | None, limit: int = MAX_PROJECT_BYTES) -> None:
    """ProjectError when writing `project` would make a document above `limit` (MAX_PROJECT_BYTES; membership writes
    MAX_MEMBERSHIP_BYTES) larger than `before` (None: a new project). A project is never locked: a write that shrinks an
    oversize document, or keeps its size, passes, so a project stored above the budget can always be repaired (#212)."""
    size = document_size(project)
    if size <= limit or (before is not None and size <= document_size(before)):
        return
    raise ProjectError(
        f"The project document would be too large ({size:,} bytes, at most {limit:,}): shorten element "
        "or relation descriptions, or remove elements or relations."
    )


def placement_time() -> str:
    """`added_at` of a new membership: microseconds, so two placements in the same second still order
    (the membership repair keeps the newest one, ADR-0052)."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def new_project(title: str, project_id: str, description: str = "", workspace: str | None = None) -> Project:
    title = single_line(title).strip()
    if not title:
        raise ProjectError("The project title must not be empty.")
    return Project(
        id=project_id,
        title=title,
        description=description.strip(),
        workspace=workspace,
        incarnation=secrets.token_hex(12),
    )


def incarnation_of(project: Project) -> str:
    """The token of this incarnation of the project (ADR-0127): its `incarnation`, else (written before M23) its
    `created_at`."""
    return project.incarnation or project.created_at


# ---------------------------------------------------------------------------------------
# helpers


def _section(project: Project, ref: str | None) -> str | None:
    """Resolve a section reference to its id; None/"" is the top level."""
    if ref is None or ref == "":
        return None
    section = project.find_section(ref)
    if section is None:
        known = ", ".join(s.id for s in project.sections) or "none"
        raise ProjectError(f"Unknown section '{ref}' in project '{project.id}'. Known sections: {known}.")
    return section.id


def _existing_section(project: Project, ref: str) -> Section:
    """The section an operation acts on (the top level is not a section)."""
    section_id = _section(project, ref)
    if section_id is None:
        known = ", ".join(s.id for s in project.sections) or "none"
        raise ProjectError(f"Name the section to change. Known sections: {known}.")
    found = project.find_section(section_id)
    assert found is not None
    return found


def _check_position(position: int | None) -> None:
    if position is not None and position < 0:
        raise ProjectError(f"Invalid position {position}: use 0 for the first place, or omit it for the end.")


def _insert_at(items: list[Any], item: Any, siblings: list[Any], position: int | None) -> None:
    """Insert `item` into `items` before the sibling at `position` (append after the last sibling when
    `position` is None or too large; at the end of `items` when there are no siblings)."""
    if position is not None and position < len(siblings):
        items.insert(items.index(siblings[position]), item)
    elif siblings:
        items.insert(items.index(siblings[-1]) + 1, item)
    else:
        items.append(item)


def _member(project: Project, diagram: str) -> Membership:
    member = project.member(diagram)
    if member is None:
        known = ", ".join(m.diagram for m in project.diagrams) or "none"
        raise ProjectError(f"Diagram '{diagram}' is not in project '{project.id}'. Members: {known}.")
    return member


# ---------------------------------------------------------------------------------------
# operations


def rename_project(project: Project, title: str) -> dict[str, Any]:
    if not title.strip():
        raise ProjectError("The project title must not be empty.")
    project.title = title.strip()
    return {"title": project.title}


def set_description(project: Project, description: str) -> dict[str, Any]:
    project.description = description.strip()
    return {"description": project.description}


def add_section(
    project: Project, title: str, parent: str | None = None, id: str | None = None, position: int | None = None
) -> Section:
    if not title.strip():
        raise ProjectError("The section title must not be empty.")
    _check_position(position)
    parent_id = _section(project, parent)
    taken = {s.id for s in project.sections}
    if id is not None:
        if slugify(id) != id or not id:
            raise ProjectError(f"Invalid section id '{id}' (use lowercase letters, digits and '-').")
        if id in taken:
            raise ProjectError(f"Section id '{id}' is already used in project '{project.id}'.")
        section_id = id
    else:
        base = slugify(title) or "section"
        section_id, n = base, 2
        while section_id in taken:
            section_id, n = f"{base}-{n}", n + 1
    section = Section(id=section_id, title=title.strip(), parent=parent_id)
    _insert_at(project.sections, section, project.children(parent_id), position)
    return section


def rename_section(project: Project, section: str, title: str) -> dict[str, Any]:
    if not title.strip():
        raise ProjectError("The section title must not be empty.")
    target = _existing_section(project, section)
    if target.element is not None and title.strip() != target.title:
        raise ProjectError(
            f"Section '{target.id}' stands for model element '{target.element}'; rename the element "
            "(update_element) and the section follows."
        )
    target.title = title.strip()
    return {"section": target.id, "title": target.title}


def move_section(
    project: Project, section: str, parent: str | None = None, position: int | None = None
) -> dict[str, Any]:
    _check_position(position)
    target = _existing_section(project, section)
    section_id = target.id
    parent_id = _section(project, parent)
    if parent_id is not None and (parent_id == section_id or parent_id in project.descendants(section_id)):
        raise ProjectError(f"Section '{section_id}' cannot be moved into itself or one of its subsections.")
    project.sections.remove(target)
    target.parent = parent_id
    _insert_at(project.sections, target, project.children(parent_id), position)
    return {"section": section_id, "parent": parent_id}


def remove_section(project: Project, section: str) -> dict[str, Any]:
    """Remove a section; its subsections and diagrams take its place in its parent (ADR-0054)."""
    target = _existing_section(project, section)
    section_id = target.id
    children = project.children(section_id)
    index = project.sections.index(target)
    for child in children:
        project.sections.remove(child)
        child.parent = target.parent
    project.sections[index:index] = children
    project.sections.remove(target)
    moved = project.members_of(section_id)
    for member in moved:
        member.section = target.parent
    return {
        "section": section_id,
        "moved_sections": [c.id for c in children],
        "moved_diagrams": [m.diagram for m in moved],
    }


def move_diagram(
    project: Project, diagram: str, section: str | None = None, position: int | None = None
) -> dict[str, Any]:
    _check_position(position)
    member = _member(project, diagram)
    section_id = _section(project, section)
    project.diagrams.remove(member)
    member.section = section_id
    _insert_at(project.diagrams, member, project.members_of(section_id), position)
    return {"diagram": diagram, "section": section_id}


def add_diagram(
    project: Project, diagram: str, section: str | None = None, position: int | None = None
) -> dict[str, Any]:
    _check_position(position)
    if project.member(diagram) is not None:
        raise ProjectError(f"Diagram '{diagram}' is already in project '{project.id}'.")
    section_id = _section(project, section)
    member = Membership(diagram=diagram, section=section_id, added_at=placement_time())
    _insert_at(project.diagrams, member, project.members_of(section_id), position)
    return {"diagram": diagram, "section": section_id}


def remove_diagram(project: Project, diagram: str) -> dict[str, Any]:
    project.diagrams.remove(_member(project, diagram))
    return {"diagram": diagram}


# ---- the shared model (spec: shared-project-model.md) -------------------------------------


def _element(project: Project, ref: str) -> Element:
    found = project.find_element(ref)
    if found is None:
        raise ProjectError(
            f"Unknown element '{ref}' in project '{project.id}'. Elements: {known_elements(project.elements)}."
        )
    return found


def _element_kind(kind: str) -> str:
    if kind not in ELEMENT_KINDS:
        raise ProjectError(f"Unknown element kind '{kind}'. Available: {', '.join(ELEMENT_KINDS)}.")
    return kind


def _element_parent(project: Project, parent: str | None, element_id: str | None = None) -> str | None:
    if parent is None or parent == "":
        return None
    found = _element(project, parent)
    if element_id is not None:
        chain: set[str] = set()
        current: Element | None = found
        while current is not None and current.id not in chain:  # walk up from the new parent
            if current.id == element_id:
                raise ProjectError(f"Element '{element_id}' cannot be placed inside itself or one of its parts.")
            chain.add(current.id)
            current = project.find_element(current.parent) if current.parent else None
    return found.id


def add_element(
    project: Project,
    name: str,
    kind: str = "component",
    description: str = "",
    parent: str | None = None,
    id: str | None = None,
) -> Element:
    if not name.strip():
        raise ProjectError("The element name must not be empty.")
    _check_element_text(name, description)
    if len(project.elements) >= MAX_ELEMENTS:
        raise ProjectError(f"A project holds at most {MAX_ELEMENTS} model elements.")
    kind = _element_kind(kind)
    parent_id = _element_parent(project, parent)
    taken = {e.id for e in project.elements}
    if id is not None:
        if not id or slugify(id) != id:
            raise ProjectError(f"Invalid element id '{id}' (use lowercase letters, digits and '-').")
        if len(id) > MAX_ID_LENGTH:
            raise ProjectError(f"Element ids are at most {MAX_ID_LENGTH} characters long.")
        if id in taken:
            raise ProjectError(f"Element id '{id}' is already used in project '{project.id}'.")
        element_id = id
    else:
        base = slugify(name)[:60].strip("-") or "element"
        element_id, n = base, 2
        while element_id in taken:
            element_id, n = f"{base}-{n}", n + 1
    duplicate = next((e for e in project.elements if name_key(e.name) == name_key(name)), None)
    if duplicate is not None:
        raise ProjectError(
            f"Element '{duplicate.id}' is already called '{duplicate.name}'; use it or choose another name."
        )
    element = Element(id=element_id, name=name.strip(), kind=kind, description=description.strip(), parent=parent_id)
    project.elements.append(element)
    return element


def _check_element_text(name: str | None, description: str | None) -> None:
    """The bounds of a new or changed name or description (#212, ADR-0153), in words the model can act on."""
    if name is not None and len(name.strip()) > MAX_ELEMENT_NAME:
        raise ProjectError(
            f"The element name is too long ({len(name.strip())} characters, at most {MAX_ELEMENT_NAME})."
        )
    if description is not None and len(description.strip()) > MAX_ELEMENT_DESCRIPTION:
        raise ProjectError(
            f"The element description is too long ({len(description.strip())} characters, at most "
            f"{MAX_ELEMENT_DESCRIPTION})."
        )


def update_element(
    project: Project, element: str, name: str | None = None, description: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    """Rename, describe or re-kind an element; sections standing for it are renamed with it. Whether its views
    can represent a new kind is the model service's check (it knows the diagrams)."""
    target = _element(project, element)
    _check_element_text(name, description)  # before anything changes; only what this update writes (#212)
    renamed_from = None
    renamed_sections: list[str] = []
    if name is not None:
        if not name.strip():
            raise ProjectError("The element name must not be empty.")
        wanted = name_key(name)  # names directly: find_element would match another element's id first
        clash = next((e for e in project.elements if e.id != target.id and name_key(e.name) == wanted), None)
        if clash is not None:
            raise ProjectError(f"Element '{clash.id}' is already called '{clash.name}'.")
        if name.strip() != target.name:
            renamed_from, target.name = target.name, name.strip()
        for section in project.sections:
            if section.element == target.id and section.title != target.name:
                section.title = target.name
                renamed_sections.append(section.id)
    if description is not None:
        target.description = description.strip()
    if kind is not None:
        new_kind = _element_kind(kind)
        _check_relations_allow(project, target.id, new_kind)
        target.kind = new_kind
    return {"element": target.model_dump(), "renamed_from": renamed_from, "sections": renamed_sections}


def revert_element(
    project: Project, element: str, before: dict[str, Any], written: dict[str, Any], fields: list[str]
) -> dict[str, Any]:
    """Undo the `fields` an element update changed, when a concurrent link made its new kind incompatible (#155,
    ADR-0128): each field goes back to its `before` value only while it still has the value the update `written`, so
    somebody else's later change is never undone and a field the update did not touch is never written. Sections
    standing for the element follow its name. Returns the fields put back."""
    target = _element(project, element)
    reverted: list[str] = []
    for field in fields:
        if field not in ("name", "description", "kind", "parent"):
            raise ProjectError(f"'{field}' is not an element field.")
        if getattr(target, field) != written.get(field):
            continue  # changed by somebody else since: theirs stays
        if field == "parent":
            move_element(project, target.id, before.get("parent"))
        else:
            update_element(project, target.id, **{field: before[field]})
        reverted.append(field)
    return {"element": target.id, "reverted": reverted}


def add_code_ref(
    project: Project, element: str, path: str, repo: str = "", symbol: str = "", note: str = ""
) -> dict[str, Any]:
    """Link an element to the code that implements it (a repository-relative file or directory, optionally a symbol
    in it and a repository label). The same path, repo and symbol again only updates the note (spec: agent-access.md)."""
    target = _element(project, element)
    try:
        ref = CodeRef(path=path, repo=repo, symbol=symbol, note=note)
    except ValueError as exc:
        raise ProjectError(f"Invalid code link: {_first_problem(exc)}.") from exc
    existing = next((r for r in target.code_refs if r.key == ref.key), None)
    if existing is not None:
        existing.note = ref.note
    elif len(target.code_refs) >= MAX_CODE_REFS:
        raise ProjectError(f"An element has at most {MAX_CODE_REFS} code links; remove one of '{target.id}' first.")
    else:
        target.code_refs.append(ref)
    return {"element": target.id, "code_refs": [r.model_dump() for r in target.code_refs]}


def remove_code_ref(project: Project, element: str, path: str, repo: str = "", symbol: str = "") -> dict[str, Any]:
    """Remove the code link with this path, repo and symbol from an element."""
    target = _element(project, element)
    try:
        wanted = CodeRef(path=path, repo=repo, symbol=symbol).key
    except ValueError as exc:
        raise ProjectError(f"Invalid code link: {_first_problem(exc)}.") from exc
    kept = [r for r in target.code_refs if r.key != wanted]
    if len(kept) == len(target.code_refs):
        known = ", ".join(r.path + (f" ({r.symbol})" if r.symbol else "") for r in target.code_refs) or "none"
        raise ProjectError(f"Element '{target.id}' has no code link '{wanted[0]}'. Its code links: {known}.")
    target.code_refs = kept
    return {"element": target.id, "code_refs": [r.model_dump() for r in kept]}


def _first_problem(exc: ValueError) -> str:
    """The message of a validation error without pydantic's framing."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        first = errors()[0]
        return f"{first['loc'][-1]}: {str(first['msg']).removeprefix('Value error, ')}"
    return str(exc)


def move_element(project: Project, element: str, parent: str | None = None) -> dict[str, Any]:
    target = _element(project, element)
    target.parent = _element_parent(project, parent, target.id)
    return {"element": target.id, "parent": target.parent}


def remove_element(project: Project, element: str) -> dict[str, Any]:
    """Remove an element from the model: its parts move to its parent, sections standing for it lose the link
    (ADR-0072). The views' references are the model service's business."""
    target = _element(project, element)
    moved = project.element_children(target.id)
    for child in moved:
        child.parent = target.parent
    unlinked = [s for s in project.sections if s.element == target.id]
    for section in unlinked:
        section.element = None
    project.elements.remove(target)
    removed = [r for r in project.relations if target.id in (r.source, r.target)]  # an end is gone (ADR-0135)
    project.relations = [r for r in project.relations if r not in removed]
    concerned = [d for d in project.drivers if target.id in d.elements]  # the driver stays, without the element
    for driver in concerned:
        driver.elements = [e for e in driver.elements if e != target.id]
    for scenario in project.scenarios:  # decision support (ADR-0166): the reference goes
        scenario.elements = [e for e in scenario.elements if e != target.id]
    for decision in project.decisions:
        decision.elements = [e for e in decision.elements if e != target.id]
    return {
        "element": target.id,
        "name": target.name,
        "moved_elements": [c.id for c in moved],
        "unlinked_sections": [s.id for s in unlinked],
        "removed_relations": [r.id for r in removed],
        "drivers": [d.id for d in concerned],
    }


def link_section(project: Project, section: str, element: str | None = None) -> dict[str, Any]:
    """Let a section stand for a model element (its title becomes the element's name), or unlink it."""
    target = _existing_section(project, section)
    if element is None or element == "":
        target.element = None
        return {"section": target.id, "element": None}
    found = _element(project, element)
    target.element, target.title = found.id, found.name
    return {"section": target.id, "element": found.id, "title": target.title}


# ---- relations (spec: model-relations.md, ADR-0135, ADR-0136) -------------------------------------------------


def _check_relations_allow(project: Project, element_id: str, kind: str) -> None:
    """An element may only change to a kind every relation it is an end of still allows."""
    kinds = {e.id: e.kind for e in project.elements} | {element_id: kind}
    blocked = [
        r.id
        for r in project.relations
        if element_id in (r.source, r.target) and relation_problem(r.kind, kinds[r.source], kinds[r.target])
    ]
    if blocked:
        raise ProjectError(
            f"Element '{element_id}' cannot become a {kind} element: relation(s) {', '.join(blocked)} would not allow "
            "it. Remove or change them first."
        )


def _relation(project: Project, ref: str) -> Relation:
    found = project.find_relation(ref)
    if found is None:
        raise ProjectError(
            f"Unknown relation '{ref}' in project '{project.id}'. Relations: {known_relations(project.relations)}."
        )
    return found


def _check_relation(
    project: Project,
    kind: str,
    source: str,
    target: str,
    properties: dict[str, str],
    description: str,
    exclude: Relation | None = None,
) -> None:
    if kind not in RELATION_KINDS:
        raise ProjectError(f"Unknown relation kind '{kind}'. Available: {', '.join(RELATION_KINDS)}.")
    if source == target:
        raise ProjectError("A relation cannot connect an element with itself.")
    if max(len(source), len(target)) > MAX_ID_LENGTH:
        raise ProjectError(
            f"A relation can only end at elements whose ids are at most {MAX_ID_LENGTH} characters long."
        )
    kinds = {e.id: e.kind for e in project.elements}
    problem = relation_problem(kind, kinds[source], kinds[target])
    if problem:
        raise ProjectError(problem)
    for key, value in properties.items():
        problem = property_problem(kind, key, value)
        if problem:
            raise ProjectError(problem)
    if len(description) > MAX_RELATION_DESCRIPTION:
        raise ProjectError(
            f"The description is too long ({len(description)} characters, at most {MAX_RELATION_DESCRIPTION})."
        )
    clash = next((r for r in project.relations if r is not exclude and same_relation(r, kind, source, target)), None)
    if clash is not None:
        raise ProjectError(f"Relation '{clash.id}' already is the {kind} relation between '{source}' and '{target}'.")


def _properties(properties: dict[str, Any] | None) -> dict[str, str]:
    return {str(k).strip(): str(v).strip() for k, v in (properties or {}).items()}


def add_relation(
    project: Project,
    kind: str,
    source: str,
    target: str,
    properties: dict[str, PropertyValue] | None = None,
    description: str = "",
    id: str | None = None,
) -> Relation:
    if len(project.relations) >= MAX_RELATIONS:
        raise ProjectError(f"A project holds at most {MAX_RELATIONS} relations.")
    source_id, target_id = _element(project, source).id, _element(project, target).id
    props = _properties(properties)
    _check_relation(project, kind, source_id, target_id, props, description.strip())
    taken = {r.id for r in project.relations}
    if id is not None:
        if not id or slugify(id) != id:
            raise ProjectError(f"Invalid relation id '{id}' (use lowercase letters, digits and '-').")
        if len(id) > MAX_ID_LENGTH:
            raise ProjectError(f"Relation ids are at most {MAX_ID_LENGTH} characters long.")
        number = relation_number(id)
        if number is not None and len(f"r{number + 1}") > MAX_ID_LENGTH:
            raise ProjectError(
                f"Relation id '{id[:20]}…' is too large: the next generated id would not fit; use a name."
            )
        if id in taken:
            raise ProjectError(f"Relation id '{id}' is already used in project '{project.id}'.")
        relation_id = id
    else:
        n = next_relation_number(project.relation_seq, [r.id for r in project.relations])
        if n is None:  # never an integrity failure: a refusal the caller can act on
            raise ProjectError("No further relation ids can be generated in this project; give the relation an id.")
        relation_id = f"r{n}"  # above every r<n> present, so never taken
    number = relation_number(relation_id)
    if number is not None:
        project.relation_seq = max(project.relation_seq, number)  # never generated again
    relation = Relation(
        id=relation_id, kind=kind, source=source_id, target=target_id, properties=props, description=description.strip()
    )
    project.relations.append(relation)
    return relation


def update_relation(
    project: Project,
    relation: str,
    kind: str | None = None,
    source: str | None = None,
    target: str | None = None,
    properties: dict[str, PropertyValue] | None = None,
    remove_properties: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Change a relation; `ends_changed` tells the model service to check the links of its views."""
    found = _relation(project, relation)
    new_kind = kind if kind is not None else found.kind
    new_source = _element(project, source).id if source is not None else found.source
    new_target = _element(project, target).id if target is not None else found.target
    props = {k: v for k, v in found.properties.items() if k not in {str(r) for r in remove_properties or []}}
    props.update(_properties(properties))
    new_description = description.strip() if description is not None else found.description
    _check_relation(project, new_kind, new_source, new_target, props, new_description, exclude=found)
    ends_changed = (new_kind, new_source, new_target) != (found.kind, found.source, found.target)
    found.kind, found.source, found.target = new_kind, new_source, new_target
    found.properties, found.description = props, new_description
    return {"relation": found.model_dump(), "ends_changed": ends_changed}


def remove_relation(project: Project, relation: str) -> dict[str, Any]:
    found = _relation(project, relation)
    project.relations.remove(found)
    return {"relation": found.model_dump()}


# ---- architecture drivers (spec: architecture-drivers.md, ADR-0163) -----------------------------------------------


def _driver(project: Project, ref: str) -> Driver:
    found = project.find_driver(ref)
    if found is None:
        raise ProjectError(
            f"Unknown driver '{ref}' in project '{project.id}'. Drivers: {known_drivers(project.drivers)}."
        )
    return found


def _live_driver(project: Project, ref: str, current: list[str | None] | None = None) -> Driver:
    """A driver a scenario or decision may point to: a retired one only where the link exists already (ADR-0198)."""
    found = _driver(project, ref)
    if is_retired(found) and found.id not in (current or []):
        raise ProjectError(f"Driver '{found.id}' is retired; reactivate it (status active) before linking to it.")
    return found


def _driver_choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise ProjectError(f"Unknown driver {what} '{value}'. Available: {', '.join(allowed)}.")
    return value


def _driver_title(project: Project, title: str, driver_id: str | None = None) -> str:
    title = single_line(title).strip()
    if not title:
        raise ProjectError("The driver title must not be empty.")
    if len(title) > MAX_DRIVER_TITLE:
        raise ProjectError(f"Driver titles are at most {MAX_DRIVER_TITLE} characters long.")
    other = next((d for d in project.drivers if d.id != driver_id and name_key(d.title) == name_key(title)), None)
    if other is not None:
        raise ProjectError(f"Driver '{other.id}' is already called '{other.title}'; change it or choose another title.")
    return title


def _driver_description(description: str) -> str:
    if len(description.strip()) > MAX_DRIVER_DESCRIPTION:
        raise ProjectError(f"Driver descriptions are at most {MAX_DRIVER_DESCRIPTION} characters long.")
    return description.strip()


def _driver_elements(project: Project, elements: list[str]) -> list[str]:
    """Element refs (id or name) as ids, each once, in the given order."""
    out: list[str] = []
    for ref in elements:
        element_id = _element(project, ref).id
        if element_id not in out:
            out.append(element_id)
    return out


def add_driver(
    project: Project,
    title: str,
    kind: str = "quality_goal",
    importance: str = "medium",
    risk: str = "medium",
    description: str = "",
    elements: list[str] | None = None,
    position: int | None = None,
    requirements: list[str] | None = None,
    origin: str = "",
) -> Driver:
    if len(project.drivers) >= MAX_DRIVERS:
        raise ProjectError(f"A project holds at most {MAX_DRIVERS} architecture drivers.")
    _check_position(position)
    numbers = [n for n in (driver_number(d.id) for d in project.drivers) if n is not None]
    project.driver_seq = max([project.driver_seq, *numbers]) + 1
    driver = Driver(
        id=f"d{project.driver_seq}",
        title=_driver_title(project, title),
        kind=_driver_choice(kind, DRIVER_KINDS, "kind"),
        importance=_driver_choice(importance, DRIVER_LEVELS, "importance"),
        risk=_driver_choice(risk, DRIVER_LEVELS, "risk"),
        description=_driver_description(description),
        elements=_driver_elements(project, elements or []),
        requirements=_requirement_ids(requirements or []),
        origin=_origin(origin),
    )
    _insert_at(project.drivers, driver, project.drivers, position)
    return driver


def update_driver(
    project: Project,
    driver: str,
    title: str | None = None,
    kind: str | None = None,
    importance: str | None = None,
    risk: str | None = None,
    description: str | None = None,
    elements: list[str] | None = None,
    requirements: list[str] | None = None,
    status: str | None = None,
) -> Driver:
    target = _driver(project, driver)
    if title is not None:
        target.title = _driver_title(project, title, target.id)
    if kind is not None:
        target.kind = _driver_choice(kind, DRIVER_KINDS, "kind")
    if importance is not None:
        target.importance = _driver_choice(importance, DRIVER_LEVELS, "importance")
    if risk is not None:
        target.risk = _driver_choice(risk, DRIVER_LEVELS, "risk")
    if description is not None:
        target.description = _driver_description(description)
    if elements is not None:
        target.elements = _driver_elements(project, elements)
    if requirements is not None:
        target.requirements = _requirement_ids(requirements)
    if status is not None:
        target.status = _choice(status, OBJECT_STATUSES, "status")
    return target


def move_driver(project: Project, driver: str, position: int) -> Driver:
    """Move a driver to `position` of the list (its order inside a priority class)."""
    target = _driver(project, driver)
    _check_position(position)
    project.drivers.remove(target)
    _insert_at(project.drivers, target, project.drivers, position)
    return target


def remove_driver(project: Project, driver: str) -> dict[str, Any]:
    target = _driver(project, driver)
    if not is_retired(target):
        raise ProjectStateError(f"Retire driver {target.id} (status retired) before deleting it.")
    project.drivers.remove(target)
    # decision support (ADR-0166): its scenarios stay, unassigned; stakeholders and decisions lose the reference
    unassigned = [sc.id for sc in project.scenarios if sc.driver == target.id]
    for sc in project.scenarios:
        if sc.driver == target.id:
            sc.driver, sc.core = None, False
    for stakeholder in project.stakeholders:
        stakeholder.drivers = [d for d in stakeholder.drivers if d != target.id]
    for decision in project.decisions:
        decision.drivers = [d for d in decision.drivers if d != target.id]
    return {"driver": target.model_dump(), "unassigned_scenarios": unassigned}


# ---- decision support: stakeholders, scenarios, decisions (spec: decision-support.md, ADR-0166) ---------------------


def _text(value: str, limit: int, what: str, one_line: bool = False) -> str:
    text = (single_line(value) if one_line else value).strip()
    if len(text) > limit:
        raise ProjectError(f"{what} is at most {limit} characters long.")
    return text


def _title(items: list[Any], value: str, limit: int, what: str, attr: str, own_id: str | None = None) -> str:
    title = _text(value, limit, what, one_line=True)
    if not title:
        raise ProjectError(f"{what} must not be empty.")
    other = next((i for i in items if i.id != own_id and name_key(getattr(i, attr)) == name_key(title)), None)
    if other is not None:
        raise ProjectError(f"'{other.id}' is already called '{getattr(other, attr)}'; change it or choose another one.")
    return title


def _choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise ProjectError(f"Unknown {what} '{value}'. Available: {', '.join(allowed)}.")
    return value


def _refs(resolve: Callable[[str], Any], refs: list[str]) -> list[str]:
    """References (ids or names) as ids, each once, at most `MAX_REFS`."""
    out: list[str] = []
    for ref in refs:
        found = resolve(ref).id
        if found not in out:
            out.append(found)
    if len(out) > dm.MAX_REFS:
        raise ProjectError(f"At most {dm.MAX_REFS} references.")
    return out


def _requirement_ids(ids: list[str]) -> list[str]:
    """Requirement ids as given, each once, at most `MAX_LINKED`. Whether they exist is the service's check (the
    requirements live in their own document, ADR-0188), so this is the format only."""
    out: list[str] = []
    for rid in ids:
        rid = rid.strip()
        if not rid:
            raise ProjectError("A requirement reference must not be empty.")
        if rid not in out:
            out.append(rid)
    if len(out) > rqm.MAX_LINKED:
        raise ProjectError(f"At most {rqm.MAX_LINKED} requirements can be named.")
    return out


def _origin(origin: str) -> str:
    """The proposal item that created an object (`<proposal>/<key>`); a format check only."""
    origin = origin.strip()
    if len(origin) > MAX_ORIGIN or not origin.isprintable():
        raise ProjectError(f"An origin is at most {MAX_ORIGIN} printable characters.")
    return origin


def _stakeholder_refs(project: Project, refs: list[str]) -> list[str]:
    out = _refs(lambda r: _stakeholder(project, r), refs)
    if len(out) > rqm.MAX_STAKEHOLDERS:
        raise ProjectError(f"At most {rqm.MAX_STAKEHOLDERS} stakeholders.")
    return out


def link_requirements(project: Project, target_kind: str, target: str, requirements: list[str]) -> dict[str, Any]:
    """Add requirement ids to a driver, scenario or decision (a union, idempotent; never a replacement, so an earlier
    trace is not lost; ADR-0189). `target_kind` is `driver`, `scenario` or `decision`."""
    finders: dict[str, Callable[[Project, str], Any]] = {
        "driver": _driver,
        "scenario": _scenario,
        "decision": _decision,
    }
    find = finders.get(target_kind)
    if find is None:
        raise ProjectError(f"Unknown target_kind '{target_kind}'. Available: {', '.join(finders)}.")
    item = find(project, target)
    item.requirements = _requirement_ids([*item.requirements, *requirements])
    return {"target_kind": target_kind, "target": item.id, "requirements": list(item.requirements)}


def target_of(project: Project, op: str, args: dict[str, Any]) -> BaseModel | None:
    """The driver, scenario or decision an `update_*` (or `link_requirements`) names, or None when it is not one of
    those or does not exist (the operation reports that)."""
    refs = {
        "update_driver": (_driver, args.get("driver")),
        "update_scenario": (_scenario, args.get("scenario")),
        "update_decision": (_decision, args.get("decision")),
    }
    if op == "link_requirements":
        finder = {"driver": _driver, "scenario": _scenario, "decision": _decision}.get(str(args.get("target_kind")))
        refs = {op: (finder, args.get("target"))} if finder is not None else {}
    found = refs.get(op)
    if found is None or found[0] is None or not isinstance(found[1], str):
        return None
    try:
        result: BaseModel = found[0](project, found[1])
    except ProjectError:
        return None
    return result


def object_fingerprint(item: BaseModel) -> str:
    """A short fingerprint of a driver, scenario or decision as it is now: what an `update_*` proposal item was made on."""
    body = json.dumps(item.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(body.encode()).hexdigest()[:16]


def _next_id(project: Project, items: list[Any], prefix: str, seq: str) -> str:
    numbers = [n for n in (dm.number_of(i.id, prefix) for i in items) if n is not None]
    setattr(project, seq, max([getattr(project, seq), *numbers]) + 1)
    return f"{prefix}{getattr(project, seq)}"


def _stakeholder(project: Project, ref: str) -> Stakeholder:
    found = dm.find_by_title(project.stakeholders, ref, "name")
    if found is None:
        known = dm.known(project.stakeholders, "name")
        raise ProjectError(f"Unknown stakeholder '{ref}' in project '{project.id}'. Stakeholders: {known}.")
    return found


def _scenario(project: Project, ref: str) -> Scenario:
    found = dm.find_by_title(project.scenarios, ref, "title")
    if found is None:
        known = dm.known(project.scenarios, "title")
        raise ProjectError(f"Unknown scenario '{ref}' in project '{project.id}'. Scenarios: {known}.")
    return found


def _decision(project: Project, ref: str) -> Decision:
    wanted = ref.strip()
    if wanted.upper().startswith("ADR-") and wanted[4:].isdigit():
        wanted = f"adr{wanted[4:]}"
    found = dm.find_by_title(project.decisions, wanted, "title")
    if found is None:
        known = dm.known(project.decisions, "title")
        raise ProjectError(f"Unknown decision '{ref}' in project '{project.id}'. Decisions: {known}.")
    return found


def add_stakeholder(
    project: Project,
    name: str,
    role: str = "",
    concerns: str = "",
    influence: str = "medium",
    drivers: list[str] | None = None,
) -> Stakeholder:
    if len(project.stakeholders) >= dm.MAX_STAKEHOLDERS:
        raise ProjectError(f"A project holds at most {dm.MAX_STAKEHOLDERS} stakeholders.")
    stakeholder = Stakeholder(
        id="s0",
        name=_title(project.stakeholders, name, dm.MAX_NAME, "The stakeholder name", "name"),
        role=_text(role, dm.MAX_NAME, "The role", one_line=True),
        concerns=_text(concerns, dm.MAX_CONCERNS, "The concerns"),
        influence=_choice(influence, dm.INFLUENCE, "influence"),
        drivers=_refs(lambda r: _driver(project, r), drivers or []),
    )
    stakeholder.id = _next_id(project, project.stakeholders, "s", "stakeholder_seq")
    project.stakeholders.append(stakeholder)
    return stakeholder


def update_stakeholder(
    project: Project,
    stakeholder: str,
    name: str | None = None,
    role: str | None = None,
    concerns: str | None = None,
    influence: str | None = None,
    drivers: list[str] | None = None,
) -> Stakeholder:
    target = _stakeholder(project, stakeholder)
    if name is not None:
        target.name = _title(project.stakeholders, name, dm.MAX_NAME, "The stakeholder name", "name", target.id)
    if role is not None:
        target.role = _text(role, dm.MAX_NAME, "The role", one_line=True)
    if concerns is not None:
        target.concerns = _text(concerns, dm.MAX_CONCERNS, "The concerns")
    if influence is not None:
        target.influence = _choice(influence, dm.INFLUENCE, "influence")
    if drivers is not None:
        target.drivers = _refs(lambda r: _driver(project, r), drivers)
    return target


def remove_stakeholder(project: Project, stakeholder: str) -> dict[str, Any]:
    target = _stakeholder(project, stakeholder)
    project.stakeholders.remove(target)
    carriers: list[Scenario | Decision] = [*project.scenarios, *project.decisions]
    for item in carriers:  # the references of scenarios and decisions
        item.stakeholders = [s for s in item.stakeholders if s != target.id]
    return {"stakeholder": target.model_dump()}


def _scenario_parts(target: Scenario, parts: dict[str, str | None]) -> None:
    for key, value in parts.items():
        if value is not None:
            setattr(target, key, _text(value, dm.MAX_PART, f"The scenario's {key}"))


def add_scenario(
    project: Project,
    title: str,
    kind: str = "usage",
    source: str = "",
    stimulus: str = "",
    environment: str = "",
    artifact: str = "",
    response: str = "",
    response_measure: str = "",
    driver: str | None = None,
    core: bool = False,
    elements: list[str] | None = None,
    requirements: list[str] | None = None,
    stakeholders: list[str] | None = None,
    origin: str = "",
) -> Scenario:
    if len(project.scenarios) >= dm.MAX_SCENARIOS:
        raise ProjectError(f"A project holds at most {dm.MAX_SCENARIOS} scenarios.")
    scenario = Scenario(
        id="q0",
        title=_title(project.scenarios, title, dm.MAX_TITLE, "The scenario title", "title"),
        kind=_choice(kind, dm.SCENARIO_KINDS, "scenario kind"),
        driver=_live_driver(project, driver).id if driver else None,
        elements=_refs(lambda r: _element(project, r), elements or []),
        requirements=_requirement_ids(requirements or []),
        stakeholders=_stakeholder_refs(project, stakeholders or []),
        origin=_origin(origin),
    )
    _scenario_parts(
        scenario,
        {
            "source": source,
            "stimulus": stimulus,
            "environment": environment,
            "artifact": artifact,
            "response": response,
            "response_measure": response_measure,
        },
    )
    if core and scenario.driver is None:
        raise ProjectError("A core scenario states a core goal of its driver: choose the driver it serves.")
    scenario.core = core
    scenario.id = _next_id(project, project.scenarios, "q", "scenario_seq")
    project.scenarios.append(scenario)
    return scenario


def update_scenario(
    project: Project,
    scenario: str,
    title: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    stimulus: str | None = None,
    environment: str | None = None,
    artifact: str | None = None,
    response: str | None = None,
    response_measure: str | None = None,
    driver: str | None = None,
    core: bool | None = None,
    elements: list[str] | None = None,
    requirements: list[str] | None = None,
    stakeholders: list[str] | None = None,
    status: str | None = None,
) -> Scenario:
    """`driver=""` unassigns the scenario (and it is no core scenario any more)."""
    target = _scenario(project, scenario)
    if title is not None:
        target.title = _title(project.scenarios, title, dm.MAX_TITLE, "The scenario title", "title", target.id)
    if kind is not None:
        target.kind = _choice(kind, dm.SCENARIO_KINDS, "scenario kind")
    _scenario_parts(
        target,
        {
            "source": source,
            "stimulus": stimulus,
            "environment": environment,
            "artifact": artifact,
            "response": response,
            "response_measure": response_measure,
        },
    )
    if driver is not None:
        target.driver = _live_driver(project, driver, [target.driver]).id if driver else None
        if target.driver is None:
            target.core = False
    if core is not None:
        if core and target.driver is None:
            raise ProjectError("A core scenario states a core goal of its driver: choose the driver it serves.")
        target.core = core
    if elements is not None:
        target.elements = _refs(lambda r: _element(project, r), elements)
    if requirements is not None:
        target.requirements = _requirement_ids(requirements)
    if stakeholders is not None:
        target.stakeholders = _stakeholder_refs(project, stakeholders)
    if status is not None:
        target.status = _choice(status, OBJECT_STATUSES, "status")
    return target


def remove_scenario(project: Project, scenario: str) -> dict[str, Any]:
    target = _scenario(project, scenario)
    if not is_retired(target):
        raise ProjectStateError(f"Retire scenario {target.id} (status retired) before deleting it.")
    project.scenarios.remove(target)
    for decision in project.decisions:  # the decision stays; it only came from the scenario
        if decision.scenario == target.id:
            decision.scenario = None
    return {"scenario": target.model_dump()}


def _options(options: list[dict[str, Any]]) -> list[DecisionOption]:
    if len(options) > dm.MAX_OPTIONS:
        raise ProjectError(f"A decision weighs at most {dm.MAX_OPTIONS} options.")
    out: list[DecisionOption] = []
    for n, raw in enumerate(options, start=1):
        if not isinstance(raw, dict):
            raise ProjectError(f"Option {n} must be an object with title, description, pros and cons.")
        title = _text(str(raw.get("title") or ""), dm.MAX_TITLE, f"The title of option {n}", one_line=True)
        if not title:
            raise ProjectError(f"Option {n} needs a title.")
        points: dict[str, list[str]] = {}
        for key in ("pros", "cons"):
            values = raw.get(key) or []
            if not isinstance(values, list) or len(values) > dm.MAX_POINTS:
                raise ProjectError(f"Option {n}: {key} is a list of at most {dm.MAX_POINTS} texts.")
            points[key] = [_text(str(v), dm.MAX_POINT, f"A point of option {n}", one_line=True) for v in values]
            points[key] = [v for v in points[key] if v]
        description = _text(str(raw.get("description") or ""), dm.MAX_OPTION_DESCRIPTION, f"Option {n}'s description")
        out.append(DecisionOption(title=title, description=description, **points))
    return out


def _decision_texts(target: Decision, texts: dict[str, str | None]) -> None:
    for key, value in texts.items():
        if value is not None:
            setattr(target, key, _text(value, dm.MAX_LONG_TEXT, f"The decision's {key}"))


def _decision_status(project: Project, target: Decision, status: str, superseded_by: str | None) -> None:
    target.status = _choice(status, dm.DECISION_STATUSES, "decision status")
    if superseded_by is not None:
        newer = _decision(project, superseded_by).id if superseded_by else None
        if newer == target.id:
            raise ProjectError("A decision cannot supersede itself.")
        target.superseded_by = newer
    if target.status == "superseded" and target.superseded_by is None:
        raise ProjectError("A superseded decision names the decision that supersedes it (superseded_by).")
    if target.status == "accepted" and target.decided_at is None:
        target.decided_at = now_iso()


def add_decision(
    project: Project,
    title: str,
    status: str = "draft",
    context: str = "",
    options: list[dict[str, Any]] | None = None,
    chosen: int | None = None,
    decision: str = "",
    consequences: str = "",
    scenario: str | None = None,
    drivers: list[str] | None = None,
    elements: list[str] | None = None,
    superseded_by: str | None = None,
    requirements: list[str] | None = None,
    stakeholders: list[str] | None = None,
    origin: str = "",
) -> Decision:
    if len(project.decisions) >= dm.MAX_DECISIONS:
        raise ProjectError(f"A project holds at most {dm.MAX_DECISIONS} decisions.")
    target = Decision(
        id="adr0",
        title=_title(project.decisions, title, dm.MAX_TITLE, "The decision title", "title"),
        options=_options(options or []),
        scenario=_scenario(project, scenario).id if scenario else None,
        drivers=_refs(lambda r: _live_driver(project, r), drivers or []),
        elements=_refs(lambda r: _element(project, r), elements or []),
        requirements=_requirement_ids(requirements or []),
        stakeholders=_stakeholder_refs(project, stakeholders or []),
        origin=_origin(origin),
        created_at=now_iso(),
    )
    _decision_texts(target, {"context": context, "decision": decision, "consequences": consequences})
    if chosen is not None and not 0 <= chosen < len(target.options):
        raise ProjectError(f"Option {chosen} does not exist; the decision has {len(target.options)} options (from 0).")
    target.chosen = chosen
    target.id = _next_id(project, project.decisions, "adr", "decision_seq")
    _decision_status(project, target, status, superseded_by)
    project.decisions.append(target)
    return target


def update_decision(
    project: Project,
    decision: str,
    title: str | None = None,
    status: str | None = None,
    context: str | None = None,
    options: list[dict[str, Any]] | None = None,
    chosen: int | None = None,
    text: str | None = None,
    consequences: str | None = None,
    drivers: list[str] | None = None,
    elements: list[str] | None = None,
    superseded_by: str | None = None,
    requirements: list[str] | None = None,
    stakeholders: list[str] | None = None,
) -> Decision:
    """`text` is the decision itself (its rationale); `chosen=-1` clears the choice."""
    target = _decision(project, decision)
    if title is not None:
        target.title = _title(project.decisions, title, dm.MAX_TITLE, "The decision title", "title", target.id)
    if options is not None:
        target.options = _options(options)
        if target.chosen is not None and target.chosen >= len(target.options):
            target.chosen = None
    if chosen is not None:
        if chosen != -1 and not 0 <= chosen < len(target.options):
            raise ProjectError(f"Option {chosen} does not exist; the decision has {len(target.options)} options.")
        target.chosen = None if chosen == -1 else chosen
    _decision_texts(target, {"context": context, "decision": text, "consequences": consequences})
    if drivers is not None:
        target.drivers = _refs(lambda r: _live_driver(project, r, list(target.drivers)), drivers)
    if elements is not None:
        target.elements = _refs(lambda r: _element(project, r), elements)
    if requirements is not None:
        target.requirements = _requirement_ids(requirements)
    if stakeholders is not None:
        target.stakeholders = _stakeholder_refs(project, stakeholders)
    if status is not None or superseded_by is not None:
        _decision_status(project, target, status or target.status, superseded_by)
    return target


def remove_decision(project: Project, decision: str) -> dict[str, Any]:
    target = _decision(project, decision)
    if target.status == "accepted":
        raise ProjectStateError(
            f"{dm.adr_label(target.id)} is accepted: reject or supersede it before deleting it (status rejected)."
        )
    project.decisions.remove(target)
    for other in project.decisions:  # a decision it superseded is no longer superseded by anything
        if other.superseded_by == target.id:
            other.superseded_by = None
            if other.status == "superseded":
                other.status = "rejected"
    return {"decision": target.model_dump()}


OPERATIONS: dict[str, Callable[..., Any]] = {
    "add_code_ref": add_code_ref,
    "remove_code_ref": remove_code_ref,
    "rename_project": rename_project,
    "set_description": set_description,
    "add_section": add_section,
    "rename_section": rename_section,
    "move_section": move_section,
    "remove_section": remove_section,
    "move_diagram": move_diagram,
    "add_diagram": add_diagram,
    "remove_diagram": remove_diagram,
    "add_element": add_element,
    "update_element": update_element,
    "revert_element": revert_element,
    "move_element": move_element,
    "remove_element": remove_element,
    "link_section": link_section,
    "add_relation": add_relation,
    "update_relation": update_relation,
    "remove_relation": remove_relation,
    "add_driver": add_driver,
    "update_driver": update_driver,
    "move_driver": move_driver,
    "remove_driver": remove_driver,
    "add_stakeholder": add_stakeholder,
    "update_stakeholder": update_stakeholder,
    "remove_stakeholder": remove_stakeholder,
    "add_scenario": add_scenario,
    "update_scenario": update_scenario,
    "remove_scenario": remove_scenario,
    "add_decision": add_decision,
    "update_decision": update_decision,
    "remove_decision": remove_decision,
    "link_requirements": link_requirements,
}
DECISION_SUPPORT_OPERATIONS = tuple(
    name for name in OPERATIONS if name.split("_", 1)[1] in ("stakeholder", "scenario", "decision")
)
DRIVER_OPERATIONS = ("add_driver", "update_driver", "move_driver", "remove_driver")  # public: one document
CODE_REF_OPERATIONS = ("add_code_ref", "remove_code_ref")  # public: they touch only the project document
# Membership is ProjectService's invariant, the model ModelService's (renames and deletes reach the diagrams).
ELEMENT_OPERATIONS = (
    "add_element",
    "update_element",
    "revert_element",
    "move_element",
    "remove_element",
    "link_section",
)
RELATION_OPERATIONS = ("add_relation", "update_relation", "remove_relation")
INTERNAL_OPERATIONS = ("add_diagram", "remove_diagram", *ELEMENT_OPERATIONS, *RELATION_OPERATIONS)
PUBLIC_OPERATIONS = tuple(op for op in OPERATIONS if op not in INTERNAL_OPERATIONS)


def apply(project: Project, op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Apply a named project operation; `{"ok": True, "op", "result"}`.

    ProjectError on invalid input; on that or any other exception the project is left unchanged.
    """
    func = OPERATIONS.get(op)
    if func is None:
        raise ProjectError(f"Unknown project operation '{op}'. Available: {', '.join(OPERATIONS)}")
    args = validate_arguments(func, op, args, ProjectError)
    snapshot = project.model_copy(deep=True)
    try:
        result = func(project, **args)
        errors = project.integrity_errors()
        if errors:  # a defect of an operation, never bad input
            raise AssertionError(f"Project operation {op} broke integrity: {'; '.join(errors)}")
        project.updated_at = now_iso()
    except BaseException:
        for field in type(project).model_fields:
            setattr(project, field, getattr(snapshot, field))
        raise
    payload = result.model_dump() if isinstance(result, BaseModel) else result
    return {"ok": True, "op": op, "result": payload}


# ---- carrying linked elements into another project (#153, ADR-0161) ----------------------------------------------


@dataclass
class CarriedModel:
    """What `carry_model` did with the elements and relations a diagram brings into another project: source id ->
    target id of everything that came along (mapped or copied), and per id what happened."""

    elements: dict[str, str] = dataclasses.field(default_factory=dict)
    relations: dict[str, str] = dataclasses.field(default_factory=dict)
    copied_elements: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    mapped_elements: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    dropped_elements: list[tuple[str, str]] = dataclasses.field(default_factory=list)  # (source id, reason)
    copied_relations: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    mapped_relations: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    dropped_relations: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    # each copy as this carry added it (target id -> its whole dump): `uncarry` removes a copy only while it is still
    # exactly that, so an edit made meanwhile (a description, a parent, properties) is never lost
    added_elements: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    added_relations: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)

    def drop_element(self, element_id: str, reason: str) -> None:
        """A planned element lost after the project write (removed meanwhile): its links are dropped too."""
        if self.elements.pop(element_id, None) is not None:
            self.dropped_elements.append((element_id, reason))

    def drop_relation(self, relation_id: str, reason: str) -> None:
        if self.relations.pop(relation_id, None) is not None:
            self.dropped_relations.append((relation_id, reason))

    def report(self) -> dict[str, Any]:
        """The JSON of the move and import answers: copied / mapped (`{id, as}`) and dropped (`{id, reason}`)."""

        def pairs(items: list[tuple[str, str]], key: str) -> list[dict[str, str]]:
            return [{"id": a, key: b} for a, b in items]

        return {
            "elements": {
                "copied": pairs(self.copied_elements, "as"),
                "mapped": pairs(self.mapped_elements, "as"),
                "dropped": pairs(self.dropped_elements, "reason"),
            },
            "relations": {
                "copied": pairs(self.copied_relations, "as"),
                "mapped": pairs(self.mapped_relations, "as"),
                "dropped": pairs(self.dropped_relations, "reason"),
            },
        }


TOO_LARGE = "the target project would be too large"


def carry_model(
    project: Project,
    elements: list[Element],
    relations: list[Relation],
    element_ids: set[str],
    relation_ids: set[str],
    limit: int | None = None,
) -> CarriedModel:
    """Bring the elements `element_ids` and the relations `relation_ids` of a source model
    (`elements`, `relations`) into `project`, through the project operations (#153, ADR-0161):

    - an element with the same id, kind and name (case-insensitive) in `project` is mapped to;
    - else it is copied under the same id when that id is free, under a new id when another element holds it;
    - a relation whose ends both came along is mapped to the same relation between them, else copied with a new id
      from `relation_seq` (never one used before); besides the referenced ones, the relations between elements of
      which at least one was copied come along;
    - what an operation refuses (a name held by another element, `MAX_ELEMENTS`, the element bounds) or what would make
      the document larger than `limit` bytes (the size guard, `document_size`; a document already above it takes
      nothing) is left out and reported: its links are dropped (ADR-0079).

    The size is tracked incrementally (the serialised length of each added item plus its separator, #153 review), with
    one full measure before and one after; if that after-measure still exceeds the limit, what was added last is taken
    back until it fits.

    The ends of a referenced relation and the ancestors of every element are needed too. Children of an element left
    out are left out too. `project` is changed in place."""
    carried = CarriedModel()
    budget = _Budget(project, limit)
    by_id = {e.id: e for e in elements}
    ends = {end for r in relations if r.id in relation_ids for end in (r.source, r.target)}  # needed by those
    wanted: list[str] = []
    for element_id in sorted(element_ids | ends, key=lambda i: (i not in by_id, i)):
        if element_id not in by_id:
            carried.dropped_elements.append((element_id, "not in the source project"))
            continue
        chain, current, seen = [], by_id.get(element_id), set()
        while current is not None and current.id not in seen:
            seen.add(current.id)
            chain.append(current.id)
            current = by_id.get(current.parent) if current.parent else None
        wanted.extend(i for i in reversed(chain) if i not in wanted)
    order = {e.id: n for n, e in enumerate(elements)}
    depth = {i: _depth(by_id, i) for i in wanted}
    copied: set[str] = set()
    for element_id in sorted(wanted, key=lambda i: (depth[i], order[i])):
        source = by_id[element_id]
        if source.parent is not None and source.parent in by_id and source.parent not in carried.elements:
            carried.dropped_elements.append((element_id, f"its parent '{source.parent}' could not be carried"))
            continue
        same = project.find_element(source.id)
        if (
            same is not None
            and same.id == source.id
            and same.kind == source.kind
            and name_key(same.name) == name_key(source.name)  # stored names may predate #103
        ):
            carried.elements[element_id] = same.id
            carried.mapped_elements.append((element_id, same.id))
            continue
        taken = any(e.id == source.id for e in project.elements)
        args: dict[str, Any] = {"name": source.name, "kind": source.kind, "description": source.description}
        if source.parent is not None and source.parent in carried.elements:
            args["parent"] = carried.elements[source.parent]
        if not taken:
            args["id"] = source.id
        outcome = budget.add(project, "add_element", args)
        if isinstance(outcome, str):
            carried.dropped_elements.append((element_id, outcome))
            continue
        carried.elements[element_id] = outcome["result"]["id"]
        carried.copied_elements.append((element_id, outcome["result"]["id"]))
        carried.added_elements[outcome["result"]["id"]] = outcome["result"]
        copied.add(element_id)
    for relation in relations:
        both = relation.source in carried.elements and relation.target in carried.elements
        referenced = relation.id in relation_ids
        if not referenced and not (both and (relation.source in copied or relation.target in copied)):
            continue
        if not both:
            carried.dropped_relations.append((relation.id, "an end could not be carried"))
            continue
        source_id, target_id = carried.elements[relation.source], carried.elements[relation.target]
        existing = next((r for r in project.relations if same_relation(r, relation.kind, source_id, target_id)), None)
        if existing is not None:
            carried.relations[relation.id] = existing.id
            carried.mapped_relations.append((relation.id, existing.id))
            continue
        args = {
            "kind": relation.kind,
            "source": source_id,
            "target": target_id,
            "properties": dict(relation.properties),
            "description": relation.description,
        }
        outcome = budget.add(project, "add_relation", args)
        if isinstance(outcome, str):
            carried.dropped_relations.append((relation.id, outcome))
            continue
        carried.relations[relation.id] = outcome["result"]["id"]
        carried.copied_relations.append((relation.id, outcome["result"]["id"]))
        carried.added_relations[outcome["result"]["id"]] = outcome["result"]
    for relation_id in sorted(relation_ids - {r.id for r in relations}):
        carried.dropped_relations.append((relation_id, "not in the source project"))
    budget.settle(project, carried)
    return carried


def _depth(by_id: dict[str, Element], element_id: str) -> int:
    depth, current, seen = 0, by_id[element_id], {element_id}
    while current.parent is not None and current.parent in by_id and current.parent not in seen:
        seen.add(current.parent)
        current, depth = by_id[current.parent], depth + 1
    return depth


class _Budget:
    """The size guard of `carry_model`, measured incrementally: one `document_size` before, then the serialised length
    of every added element or relation (plus a separator, and the list's key for the first of its kind)."""

    def __init__(self, project: Project, limit: int | None) -> None:
        self.limit = limit
        self.base = document_size(project) if limit is not None else 0
        self.size = self.base
        self.added: list[tuple[str, str, int]] = []  # (list, id, relation_seq before)

    def add(self, project: Project, op: str, args: dict[str, Any]) -> dict[str, Any] | str:
        """`apply(project, op, args)`, or why it was left out (the refusal, or TOO_LARGE; the project is unchanged)."""
        seq = project.relation_seq
        items = project.elements if op == "add_element" else project.relations
        first = not items
        try:
            outcome = apply(project, op, args)
        except ProjectError as exc:
            return str(exc)
        name = "elements" if op == "add_element" else "relations"
        if self.limit is not None:
            item = items[-1]
            cost = _json_size(item.model_dump(mode="json")) + 1 + (len(name) + 5 if first else 0)
            cost += len(str(project.relation_seq)) + 18 if name == "relations" and seq == 0 else 0  # "relation_seq"
            if self.size + cost > max(self.limit, self.base) or (self.base > self.limit):
                items.pop()
                project.relation_seq = seq
                return TOO_LARGE
            self.size += cost
        self.added.append((name, outcome["result"]["id"], seq))
        return outcome

    def settle(self, project: Project, carried: CarriedModel) -> None:
        """The one full measure after: take back what was added last until the document is within the limit (an
        estimate that was off never lets the write fail the guard)."""
        if self.limit is None:
            return
        while self.added and document_size(project) > max(self.limit, self.base):
            name, item_id, seq = self.added.pop()
            if name == "elements":
                project.elements = [e for e in project.elements if e.id != item_id]
                source = next((s for s, t in carried.elements.items() if t == item_id), item_id)
                carried.copied_elements = [c for c in carried.copied_elements if c[1] != item_id]
                carried.drop_element(source, TOO_LARGE)
            else:
                project.relations = [r for r in project.relations if r.id != item_id]
                project.relation_seq = seq
                source = next((s for s, t in carried.relations.items() if t == item_id), item_id)
                carried.copied_relations = [c for c in carried.copied_relations if c[1] != item_id]
                carried.drop_relation(source, TOO_LARGE)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=True))
