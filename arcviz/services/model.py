"""ModelService: the shared project model across the project document and its diagrams.

Spec: docs/architecture/specs/shared-project-model.md (ADR-0070..0074). Elements live in the project document
and are written through `ProjectService` (conditional on the project revision); links live in the diagrams
and are written through `DiagramService` (conditional, versioned). A change that reaches several documents is
a sequence of such writes, ordered so that a crash leaves only drift or an unused element (ADR-0071/0072).
Access is checked by the bound services it works on (spec: workspaces-and-sharing.md); a change that writes
several documents checks the editor role before the first write.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import operations
from ..model import RELATION_DIAGRAM_TYPES, Diagram, single_line
from ..ports import Change, ConflictError, Expected, StoreError, Updated
from ..project_model import (
    CodeRef,
    DiagramViews,
    Driver,
    Element,
    Relation,
    adoptable_views,
    adoption_target,
    default_kind,
    find_view,
    fits,
    known_elements,
    known_relations,
    linkable_kinds,
    name_key,
    owner_of_path,
    ranked_drivers,
    shown_views,
    view_connections,
    view_elements,
)
from ..project_model import references as view_refs
from ..projects import CODE_REF_OPERATIONS, DRIVER_OPERATIONS, Project, ProjectError
from .access import Forbidden
from .adoption import Adopter, adopted_views, adopting, without_taken_back
from .diagrams import DiagramService
from .projects import ProjectService

UNSET: object = object()  # `parent` not given (None means: top level)


logger = logging.getLogger(__name__)
COMPENSATION_ATTEMPTS = 3  # tries to undo a write that a concurrent change made invalid (ADR-0128)


@dataclass
class Usage:
    """A view element that represents a model element."""

    diagram: str
    title: str
    type: str
    view: str
    name: str


@dataclass
class ElementView:
    id: str
    name: str
    kind: str
    description: str
    parent: str | None
    usages: list[Usage] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    code_refs: list[dict[str, Any]] = field(default_factory=list)  # where the code lives (agent-access.md)


@dataclass
class ElementChange:
    """The outcome of `update_element`: the element and the diagrams its new name was carried into."""

    element: Element
    propagated: list[str] = field(default_factory=list)  # diagrams whose views were renamed
    failed: list[str] = field(default_factory=list)  # diagrams that could not be written (drift until sync)


@dataclass
class ElementRemoval:
    """The outcome of `remove_element`."""

    element: dict[str, Any]  # what `remove_element` of the project reported
    unlinked: list[str] = field(default_factory=list)  # diagrams whose views were unlinked
    failed: list[str] = field(default_factory=list)


@dataclass
class SyncReport:
    changed: list[str] = field(default_factory=list)  # diagrams whose views were renamed
    failed: list[str] = field(default_factory=list)  # diagrams that kept changing (try again)


@dataclass
class RelationUsage:
    """A view connection (deployment edge or sequence message) that shows a relation."""

    diagram: str
    title: str
    type: str
    view: str
    label: str


@dataclass
class RelationView:
    id: str
    kind: str
    source: str
    target: str
    properties: dict[str, str]
    description: str
    usages: list[RelationUsage] = field(default_factory=list)
    shown: list[RelationUsage] = field(default_factory=list)  # unlinked connections between views of its ends


@dataclass
class RelationChange:
    """The outcome of `update_relation`: the relation and the diagrams whose links of it no longer fitted."""

    relation: Relation
    unlinked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@dataclass
class RelationRemoval:
    relation: dict[str, Any]
    unlinked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@dataclass
class ProjectModel:
    project: str
    elements: list[ElementView]
    relations: list[RelationView] = field(default_factory=list)
    diagrams: list[dict[str, Any]] = field(default_factory=list)  # id, title, type, complete, hidden (the matrix)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for element in data["elements"]:
            if not element["code_refs"]:  # like the stored element: an answer for data without links is unchanged
                del element["code_refs"]
        return data


def _relation_views(project: Project, diagrams: list[Diagram]) -> list[RelationView]:
    """The relations with the connections that show them (`usages`) and the unlinked ones that could (`shown`)."""
    views = {
        r.id: RelationView(r.id, r.kind, r.source, r.target, dict(r.properties), r.description)
        for r in project.relations
    }
    kinds = {e.id: e.kind for e in project.elements}
    for diagram in diagrams:
        diagram_views = DiagramViews(diagram, kinds)  # connections and nesting once per diagram
        labels = {c.id: c.label for c in diagram_views.connections}
        for relation in project.relations:  # the one definition of "shown" (project_model.shown_views)
            linked, unlinked = shown_views(diagram, relation, kinds, diagram_views)
            for target, ids in ((views[relation.id].usages, linked), (views[relation.id].shown, unlinked)):
                target += [RelationUsage(diagram.id, diagram.title, diagram.type, v, labels.get(v, "")) for v in ids]
    return list(views.values())


def _carries(diagram: Diagram) -> bool:
    """Whether the diagram's type carries relation refs, completeness and hidden markers (ADR-0139)."""
    return diagram.type in RELATION_DIAGRAM_TYPES


def _mentions(diagram: Diagram, relation_id: str) -> bool:
    """Whether the diagram links the relation or marks it hidden."""
    return _carries(diagram) and (_links(diagram, relation_id) or relation_id in diagram.hidden_relations)


def _relation_repairs(project: Project, diagram: Diagram) -> list[tuple[str, dict[str, Any]]]:
    """The steps that clear relation links (and hidden markers) of the diagram that drifted from the model: links
    that no longer fit their relation, links and markers of relations that no longer exist."""
    if not _carries(diagram):
        return []
    known = {r.id: r for r in project.relations}
    steps: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for connection in view_connections(diagram):
        ref = connection.ref
        if ref is None or ref in seen:
            continue
        relation = known.get(ref)
        if relation is None:
            steps.append(("clear_relation", {"relation": ref}))
            seen.add(ref)
        elif not fits(diagram.type, connection, relation):
            steps.append(("sync_relation", _relation_args(relation)))
            seen.add(ref)
    for ref in diagram.hidden_relations:
        if ref not in known and ref not in seen:
            steps.append(("clear_relation", {"relation": ref}))
            seen.add(ref)
    return steps


def _links(diagram: Diagram, relation_id: str) -> bool:
    """Whether a connection of the diagram links the relation."""
    return any(c.ref == relation_id for c in view_connections(diagram))


def _relation_args(relation: Relation) -> dict[str, Any]:
    return {"relation": relation.id, "kind": relation.kind, "source": relation.source, "target": relation.target}


class ModelService:
    def __init__(self, projects: ProjectService, diagrams: DiagramService):
        self._projects = projects
        self._diagrams = diagrams

    # ---- queries -------------------------------------------------------------------------

    def model(self, project_id: str) -> ProjectModel:
        """The project's elements with their usages in the member diagrams and the sections standing for them."""
        project, members = self._projects.load_members(project_id)
        return self._model(project, [d for _, d in members])

    @staticmethod
    def _model(project: Project, diagrams: list[Diagram]) -> ProjectModel:
        usages: dict[str, list[Usage]] = {}
        for diagram in diagrams:
            for view in view_elements(diagram):
                if view.ref is not None:
                    usages.setdefault(view.ref, []).append(
                        Usage(diagram.id, diagram.title, diagram.type, view.id, view.name)
                    )
        return ProjectModel(
            project.id,
            [
                ElementView(
                    e.id,
                    e.name,
                    e.kind,
                    e.description,
                    e.parent,
                    usages.get(e.id, []),
                    [s.id for s in project.sections if s.element == e.id],
                    [r.model_dump() for r in e.code_refs],
                )
                for e in project.elements
            ],
            _relation_views(project, diagrams),
            [
                {
                    "id": d.id,
                    "title": d.title,
                    "type": d.type,
                    "complete": list(d.complete),
                    "hidden": list(d.hidden_relations),
                }
                for d in diagrams
            ],
        )

    def project(self, project_id: str) -> Project:
        """The project document (elements, relations, drivers): one read for an assistant's prompt."""
        return self._projects.get(project_id)

    # ---- architecture drivers (spec: architecture-drivers.md, ADR-0163): project operations, no diagram involved ----

    def ranked_drivers(self, project_id: str) -> list[Driver]:
        return ranked_drivers(self._projects.get(project_id).drivers)

    def driver_operation(self, project_id: str, op: str, args: dict[str, Any]) -> dict[str, Any]:
        """A driver operation, or `add_scenario` (spec: decision-support.md), as one conditional project write
        (editor); the operation's result."""
        if op not in (*DRIVER_OPERATIONS, "add_scenario"):
            raise ProjectError(f"'{op}' is no driver or scenario operation.")
        result: dict[str, Any] = self._projects.apply(project_id, op, args)["result"]
        return result

    # ---- code links (spec: agent-access.md, ADR-0171): project operations, no diagram involved --------------------

    def code_ref_operation(self, project_id: str, op: str, args: dict[str, Any]) -> dict[str, Any]:
        """`add_code_ref` or `remove_code_ref` as one conditional project write (editor); the operation's result."""
        if op not in CODE_REF_OPERATIONS:
            raise ProjectError(f"'{op}' is no code link operation.")
        result: dict[str, Any] = self._projects.apply(project_id, op, args)["result"]
        return result

    def code_owners(self, project_id: str, path: str, repo: str | None = None) -> list[tuple[Element, CodeRef]]:
        """The elements that own `path` through their code links (viewer)."""
        return owner_of_path(self._projects.get(project_id).elements, path, repo)

    def elements(self, project_id: str) -> list[Element]:
        return self._projects.get(project_id).elements

    def element(self, project_id: str, ref: str) -> Element:
        """ProjectError when the project has no such element."""
        project = self._projects.get(project_id)
        found = project.find_element(ref)
        if found is None:
            known = known_elements(project.elements)
            raise ProjectError(f"Unknown element '{ref}' in project '{project_id}'. Elements: {known}.")
        return found

    def relations(self, project_id: str) -> list[Relation]:
        return self._projects.get(project_id).relations

    def relation(self, project_id: str, ref: str) -> Relation:
        """ProjectError when the project has no such relation."""
        project = self._projects.get(project_id)
        found = project.find_relation(ref)
        if found is None:
            known = known_relations(project.relations)
            raise ProjectError(f"Unknown relation '{ref}' in project '{project_id}'. Relations: {known}.")
        return found

    def project_of(self, diagram_id: str) -> str:
        """The project the diagram belongs to; DiagramNotFound when the caller may not see it, ProjectError when it
        is in none."""
        self._diagrams.require(diagram_id)
        located = self._projects.locate(diagram_id)
        if located is None:
            raise ProjectError(f"Diagram '{diagram_id}' is in no project, so it has no model to link to.")
        return located[0]

    # ---- the model ---------------------------------------------------------------------------

    def add_element(
        self,
        project_id: str,
        name: str,
        kind: str = "component",
        description: str = "",
        parent: str | None = None,
        element_id: str | None = None,
    ) -> Element:
        args: dict[str, Any] = {"name": name, "kind": kind, "description": description, "parent": parent}
        if element_id is not None:
            args["id"] = element_id
        result = self._projects.apply(project_id, "add_element", args, internal=True)
        return Element.model_validate(result["result"])

    def update_element(
        self,
        project_id: str,
        element: str,
        name: str | None = None,
        description: str | None = None,
        kind: str | None = None,
        parent: str | None | object = UNSET,
    ) -> ElementChange:
        """Change an element in one project write (a given `parent`, also None, moves it); a 400 means nothing was
        written. A new kind must suit every view that represents the element (ProjectError naming the views that
        cannot). A new name is then carried into every view of it (ADR-0071)."""
        self._projects.require(project_id, "editor")
        project, members = self._projects.load_members(project_id)
        target = project.find_element(element)
        if target is None:
            known = known_elements(project.elements)
            raise ProjectError(f"Unknown element '{element}' in project '{project_id}'. Elements: {known}.")
        diagrams = [d for _, d in members]
        new_kind = kind is not None and kind != target.kind
        if new_kind:
            assert kind is not None
            _refuse_kind(target.id, kind, _blocked_views(diagrams, target.id, kind))
        steps: list[tuple[str, dict[str, Any]]] = [
            ("update_element", {"element": target.id, "name": name, "description": description, "kind": kind})
        ]
        if parent is not UNSET:
            steps.append(("move_element", {"element": target.id, "parent": parent}))
        results = self._projects.apply_all(project_id, steps, internal=True)
        if new_kind:
            assert kind is not None
            written = dict(results[0]["result"]["element"])
            if parent is not UNSET:
                written["parent"] = results[-1]["result"]["parent"]
            self._verify_kind(project_id, target, written)
        updated = self.element(project_id, target.id)
        change = ElementChange(updated)
        if results[0]["result"]["renamed_from"] is not None:  # only a real rename reaches the diagrams
            change.propagated, change.failed = self._propagate(
                [d.id for d in diagrams if view_refs(d, updated.id)],
                [("sync_element", {"element": updated.id, "name": updated.name})],
                f"Element „{updated.name}“ umbenannt",
            )
        return change

    def _verify_kind(self, project_id: str, before: Element, written: dict[str, Any]) -> None:
        """After a kind change: read the member diagrams again; if a link that cannot stand for the new kind appeared
        meanwhile (a concurrent link passed its own check against the old kind), or the diagrams cannot be read
        (fail closed), undo the update and refuse like the up-front check (#155, ADR-0128). The undo
        (`revert_element`) puts back only the fields this update changed, each only while it still has the value this
        update wrote, so somebody else's later change is never undone; it is retried (all changed fields, then the kind
        alone), and if it still cannot be written the refusal says so (the project review reports the link)."""
        new = str(written["kind"])
        try:
            _, members = self._projects.load_members(project_id)
            blocked = _blocked_views([d for _, d in members], before.id, new)
        except StoreError as exc:
            blocked = [f"(the diagrams could not be read to confirm it: {type(exc).__name__})"]
        if not blocked:
            return
        before_values = {
            "name": before.name,
            "description": before.description,
            "kind": before.kind,
            "parent": before.parent,
        }
        changed = [f for f in ("kind", "name", "description", "parent") if written.get(f) != before_values[f]]
        # every field this update changed; if that cannot be written (the old name taken meanwhile, the old parent
        # gone), at least the kind
        for fields in (changed, ["kind"]):
            undo = {"element": before.id, "before": before_values, "written": written, "fields": fields}
            if self._compensate(project_id, undo):
                break
        else:
            logger.warning("A rejected element kind change could not be taken back; the project review reports it")
            _refuse_kind(before.id, new, [*blocked, "and the new kind could not be put back (the review reports it)"])
        _refuse_kind(before.id, new, blocked)

    def _compensate(self, project_id: str, undo: dict[str, Any]) -> bool:
        """One `revert_element`, retried on conflicts and store failures; False when it could not be written, also
        for a ProjectError (a compensation failure, never the caller's error)."""
        for _ in range(COMPENSATION_ATTEMPTS):
            try:
                self._projects.apply_all(project_id, [("revert_element", undo)], internal=True)
                return True
            except ProjectError:
                return False
            except (ConflictError, StoreError):
                continue
        return False

    def _propagate(
        self, diagram_ids: list[str], steps: list[tuple[str, dict[str, Any]]], label: str
    ) -> tuple[list[str], list[str]]:
        """Run internal model operations on each diagram (one conditional, versioned write per diagram, skipped
        when it changes nothing): (written, failed). A diagram deleted meanwhile is neither."""
        written: list[str] = []
        failed: list[str] = []
        for diagram_id in diagram_ids:
            try:
                if self._diagrams.apply_if_changed(diagram_id, steps, label) is not None:
                    written.append(diagram_id)
            except ConflictError:
                failed.append(diagram_id)
            except StoreError:
                if self._diagrams.exists(diagram_id):
                    raise
        return written, failed

    def move_element(self, project_id: str, element: str, parent: str | None) -> Element:
        target = self.element(project_id, element)
        self._projects.apply(project_id, "move_element", {"element": target.id, "parent": parent}, internal=True)
        return self.element(project_id, target.id)

    def remove_element(self, project_id: str, element: str) -> ElementRemoval:
        """Unlink every view of the element (versioned per diagram), then remove it from the model (ADR-0072).
        ConflictError, with the element kept, when a diagram kept changing."""
        self._projects.require(project_id, "editor")
        project, members = self._projects.load_members(project_id)
        target = project.find_element(element)
        if target is None:
            known = known_elements(project.elements)
            raise ProjectError(f"Unknown element '{element}' in project '{project_id}'. Elements: {known}.")
        relations = [r.id for r in project.relations if target.id in (r.source, r.target)]  # removed with it
        steps: list[tuple[str, dict[str, Any]]] = [("clear_relation", {"relation": r}) for r in relations]
        clear = ("clear_element", {"element": target.id})
        label = f"Element „{target.name}“ entfernt"
        affected = [d for _, d in members if view_refs(d, target.id) or any(_mentions(d, r) for r in relations)]
        # relation steps only where the type carries relation refs (an activity diagram has no clear_relation)
        unlinked, failed = self._propagate([d.id for d in affected if _carries(d)], [*steps, clear], label)
        others = self._propagate([d.id for d in affected if not _carries(d)], [clear], label)
        unlinked, failed = unlinked + others[0], failed + others[1]
        if failed:
            raise ConflictError(
                f"Diagram(s) {', '.join(failed)} kept changing; element '{target.id}' was not removed. Try again."
            )
        result = self._projects.apply(project_id, "remove_element", {"element": target.id}, internal=True)["result"]
        return ElementRemoval(result, unlinked, failed)

    def sync(self, project_id: str) -> SyncReport:
        """Give every linked view element its element's name again (repairs drift after a crash or a race between
        a link and a rename): one version per diagram that differs; writes nothing when the project is consistent."""
        self._projects.require(project_id, "editor")
        self._normalise_names(project_id)
        project, members = self._projects.load_members(project_id)
        report = SyncReport()
        for _, diagram in members:
            steps = [
                ("sync_element", {"element": e.id, "name": e.name})
                for e in project.elements
                if any(single_line(v.name).strip() != single_line(e.name).strip() for v in view_refs(diagram, e.id))
            ]
            known = {e.id for e in project.elements}  # dangling refs (an element removed around the model service)
            steps += [
                ("clear_element", {"element": ref})
                for ref in sorted({v.ref for v in view_elements(diagram) if v.ref} - known)
            ]
            steps += _relation_repairs(project, diagram)
            if steps:
                written, failed = self._propagate([diagram.id], steps, "Modell abgeglichen")
                report.changed += written
                report.failed += failed
        return report

    def _normalise_names(self, project_id: str) -> None:
        """Make element names stored before #103 one line, like every name written since (the views already show them
        so): one project write for all of them; only when that is refused (a normalised name another element holds),
        one write per element, and an element whose name is taken keeps it (the review reports the drift)."""
        project = self._projects.get(project_id)
        steps = [
            ("update_element", {"element": e.id, "name": single_line(e.name).strip()})
            for e in project.elements
            if single_line(e.name).strip() and single_line(e.name).strip() != e.name
        ]
        if not steps:
            return
        try:
            self._projects.apply_all(project_id, steps, internal=True)
            return
        except ProjectError:
            pass
        for step in steps:
            try:
                self._projects.apply(project_id, *step, internal=True)
            except ProjectError:
                logger.warning("An element name with control characters could not be normalised (taken)")

    # ---- relations (spec: model-relations.md) -------------------------------------------------------------------

    def add_relation(
        self,
        project_id: str,
        kind: str,
        source: str,
        target: str,
        properties: dict[str, Any] | None = None,
        description: str = "",
        relation_id: str | None = None,
    ) -> Relation:
        args: dict[str, Any] = {"kind": kind, "source": source, "target": target, "description": description}
        if properties:
            args["properties"] = properties
        if relation_id is not None:
            args["id"] = relation_id
        result = self._projects.apply(project_id, "add_relation", args, internal=True)
        return Relation.model_validate(result["result"])

    def update_relation(
        self,
        project_id: str,
        relation: str,
        kind: str | None = None,
        source: str | None = None,
        target: str | None = None,
        properties: dict[str, Any] | None = None,
        remove_properties: list[str] | None = None,
        description: str | None = None,
    ) -> RelationChange:
        """Change a relation in one project write (ADR-0138: nothing but link validity reaches the views)."""
        self._projects.require(project_id, "editor")
        args: dict[str, Any] = {"relation": relation}
        for key, value in (
            ("kind", kind),
            ("source", source),
            ("target", target),
            ("properties", properties),
            ("remove_properties", remove_properties),
            ("description", description),
        ):
            if value is not None:
                args[key] = value
        result = self._projects.apply(project_id, "update_relation", args, internal=True)["result"]
        change = RelationChange(Relation.model_validate(result["relation"]))
        if result["ends_changed"]:  # links that no longer fit are cleared, project first (ADR-0138, ADR-0139)
            # read after the conditional project write: every diagram that links it now is reached
            _, members = self._projects.load_members(project_id)
            change.unlinked, change.failed = self._propagate(
                [d.id for _, d in members if _links(d, change.relation.id)],
                [("sync_relation", _relation_args(change.relation))],
                f"Beziehung {change.relation.id} geändert",
            )
        return change

    def remove_relation(self, project_id: str, relation: str) -> RelationRemoval:
        """Clear every link of the relation (versioned per diagram), then remove it from the model. ConflictError,
        with the relation kept, when a diagram kept changing."""
        self._projects.require(project_id, "editor")
        project, members = self._projects.load_members(project_id)
        target = project.find_relation(relation)
        if target is None:
            known = known_relations(project.relations)
            raise ProjectError(f"Unknown relation '{relation}' in project '{project_id}'. Relations: {known}.")
        unlinked, failed = self._propagate(
            [d.id for _, d in members if _mentions(d, target.id)],
            [("clear_relation", {"relation": target.id})],
            f"Beziehung {target.id} entfernt",
        )
        if failed:
            raise ConflictError(
                f"Diagram(s) {', '.join(failed)} kept changing; relation '{target.id}' was not removed. Try again."
            )
        result = self._projects.apply(project_id, "remove_relation", {"relation": target.id}, internal=True)["result"]
        return RelationRemoval(result["relation"], unlinked)

    def link_relation(
        self, diagram_id: str, view: str, relation: str, expected: Expected | None = None
    ) -> Updated[dict[str, Any]]:
        """Let an edge or message of the diagram show a relation of the diagram's project (a version); with
        `expected` only on that diagram revision (HTTP `If-Match`)."""
        project_id = self.project_of(diagram_id)
        target = self.relation(project_id, relation)
        return self._diagrams.apply_operation(
            diagram_id,
            "link_relation",
            {"id": view, **_relation_args(target)},
            f"Mit Beziehung {target.id} verknüpft",
            expected,
        )

    def hide_relation(
        self, diagram_id: str, relation: str, hidden: bool, expected: Expected | None = None
    ) -> Updated[dict[str, Any]]:
        """Mark a relation of the diagram's project as intentionally left out of the diagram, or not any more
        (ADR-0137)."""
        project_id = self.project_of(diagram_id)
        target = self.relation(project_id, relation)
        label = (
            f"Beziehung {target.id} hier ausgeblendet" if hidden else f"Beziehung {target.id} nicht mehr ausgeblendet"
        )
        return self._diagrams.apply_operation(
            diagram_id, "set_relation_hidden", {"relation": target.id, "hidden": hidden}, label, expected
        )

    def unlink_relation(self, diagram_id: str, view: str, expected: Expected | None = None) -> Updated[dict[str, Any]]:
        return self._diagrams.apply_operation(
            diagram_id, "unlink_relation", {"id": view}, "Beziehungsverknüpfung gelöst", expected
        )

    def link_section(self, project_id: str, section: str, element: str | None) -> dict[str, Any]:
        return self._projects.apply(
            project_id, "link_section", {"section": section, "element": element}, internal=True
        )["result"]

    # ---- links ---------------------------------------------------------------------------------

    def link_args(self, project_id: str, view: str, element: str) -> dict[str, Any]:
        """The arguments of the internal `link_element` operation for an element of the project."""
        target = self.element(project_id, element)
        return {"id": view, "element": target.id, "name": target.name, "element_kind": target.kind}

    def link(
        self, diagram_id: str, view: str, element: str, expected: Expected | None = None
    ) -> Updated[dict[str, Any]]:
        """Let a node or lifeline of the diagram represent an element of the diagram's project (a version); with
        `expected` only on that diagram revision (HTTP `If-Match`)."""
        project_id = self.project_of(diagram_id)
        args = self.link_args(project_id, view, element)
        updated = self._diagrams.apply_operation(
            diagram_id, "link_element", args, f"Mit Element „{args['name']}“ verknüpft", expected
        )
        linked = find_view(updated.diagram, args["id"])
        try:
            problem = self.link_problem(
                project_id, args["element"], updated.diagram.type, linked.kind if linked else ""
            )
        except StoreError as exc:  # cannot confirm the link: take it back (fail closed), like the agents
            problem = (
                f"The project model could not be read to confirm the link ({type(exc).__name__}); "
                "the link was taken back."
            )
        if problem is None:
            return updated
        if not self._take_back(diagram_id, args["id"], args["element"]):
            logger.warning("A link that cannot stand for its element could not be taken back; the review reports it")
            raise ProjectError(not_taken_back(problem))
        raise ProjectError(problem)

    def link_problem(self, project_id: str, element_id: str, diagram_type: str, view_kind: str) -> str | None:
        """After a link was written: read the element again (never a cached copy) and say why the view cannot stand
        for it any more (its kind changed meanwhile, or it was removed), else None (#155, ADR-0128)."""
        if self._projects.guard is not None:
            self._projects.guard.forget(project_id)
        try:
            current = self.element(project_id, element_id)
        except ProjectError:
            return f"Element '{element_id}' was removed meanwhile; the link was taken back."
        if current.kind not in linkable_kinds(diagram_type, view_kind):
            return (
                f"Element '{element_id}' became a {current.kind} element meanwhile, which a «{view_kind}» cannot "
                "represent; the link was taken back."
            )
        return None

    def _take_back(self, diagram_id: str, view: str, element_id: str) -> bool:
        """Unlink `view` again (one version), only while it still stands for `element_id`; retried. True when the link
        is gone (also when somebody else unlinked it or the diagram was deleted), False when it could not be written."""

        def change(diagram: Diagram) -> Change[dict[str, Any]] | None:
            found = find_view(diagram, view)
            if found is None or found.ref != element_id:
                return None
            return Change(
                diagram, operations.apply(diagram, "unlink_element", {"id": view}), label="Verknüpfung zurückgenommen"
            )

        for _ in range(COMPENSATION_ATTEMPTS):
            try:
                self._diagrams.update(diagram_id, change)
                return True
            except (ConflictError, Forbidden):
                continue
            except StoreError:
                if not self._diagrams.exists(diagram_id):
                    return True
        return False

    def unlink(self, diagram_id: str, view: str, expected: Expected | None = None) -> Updated[dict[str, Any]]:
        return self._diagrams.apply_operation(
            diagram_id, "unlink_element", {"id": view}, "Verknüpfung gelöst", expected
        )

    # ---- project elements by default (spec: elements-by-default.md, ADR-0164) -----------------------------------

    def project_adopter(self, project_id: str) -> Adopter:
        """The adoption of new views into the model of `project_id` (the assistants know their project)."""
        return Adopter(self._projects, project_id)

    def adopter(self, diagram_id: str) -> Adopter | None:
        """The adoption of new views of a diagram into its project's model; None for a diagram in no project."""
        try:
            return Adopter(self._projects, self.project_of(diagram_id))
        except ProjectError:
            return None

    def adoption_problems(self, project_id: str, diagram: Diagram, adoption: dict[str, Any] | None) -> dict[str, str]:
        """After an adoption was written: the adopted views (view id -> why) whose element was removed or changed to a
        kind they cannot represent meanwhile, read again like `link_problem` (#155, ADR-0128); a failed read counts as
        a problem (fail closed). Views that no longer stand for their element are left alone."""
        problems: dict[str, str] = {}
        for view_id, element_id in adopted_views(adoption):
            view = find_view(diagram, view_id)
            if view is None or view.ref != element_id:
                continue
            try:
                problem = self.link_problem(project_id, element_id, diagram.type, view.kind)
            except StoreError as exc:
                problem = (
                    f"The project model could not be read to confirm the link ({type(exc).__name__}); "
                    "the link was taken back."
                )
            if problem is not None:
                problems[view.id] = problem
        return problems

    def settle_adoption(self, diagram_id: str, adoption: dict[str, Any] | None) -> dict[str, Any] | None:
        """Verify a written adoption and take back the links that cannot stand (a version "Verknüpfung
        zurückgenommen"); the adoption as it ends up. A link that could not be taken back stays and is reported by the
        project review (`dangling-element-ref`, `incompatible-element-link`)."""
        if not adopted_views(adoption):
            return adoption
        assert adoption is not None
        project_id = self.project_of(diagram_id)
        problems = self.adoption_problems(project_id, self._diagrams.get(diagram_id), adoption)
        undone: dict[str, str] = {}
        for view_id, element_id in adopted_views(adoption):
            if view_id in problems and self._take_back(diagram_id, view_id, element_id):
                undone[view_id] = problems[view_id]
        return without_taken_back(adoption, undone) if undone else adoption

    def apply_operation(
        self, diagram_id: str, op: str, args: dict[str, Any], label: str, expected: Expected | None = None
    ) -> Updated[dict[str, Any]]:
        """`DiagramService.apply_operation`, and the views it creates become project elements in the same version
        (the result then carries `adoption`)."""
        self._diagrams.require(diagram_id, "editor")
        adopter = self.adopter(diagram_id)
        diagram = self._diagrams.get(diagram_id)
        change, done = adopting(adopter, diagram, lambda target: operations.apply(target, op, args), lambda _: False)
        try:
            updated = self._diagrams.update(
                diagram_id, lambda target: Change(target, change(target), label=label), expected
            )
        finally:
            done()
        assert updated is not None and updated.version is not None
        if isinstance(updated.result, dict) and updated.result.get("adoption"):
            settled = self.settle_adoption(diagram_id, updated.result["adoption"])
            updated.result["adoption"] = settled
        return updated

    def adoption_plan(self, project_id: str) -> list[dict[str, Any]]:
        """What **Unverknüpfte übernehmen** would do, per member diagram with adoptable unlinked views (nothing is
        written): `{"id", "title", "linked", "created", "skipped"}`."""
        project, members = self._projects.load_members(project_id)
        elements = list(project.elements)
        planned: list[dict[str, Any]] = []
        for _, diagram in members:
            entry: dict[str, Any] = {
                "id": diagram.id,
                "title": diagram.title,
                "linked": [],
                "created": [],
                "skipped": [],
            }
            for view in adoptable_views(diagram):
                target = adoption_target(elements, view.name)
                if target is None:
                    kind = default_kind(diagram, view) or "component"
                    target = Element(id=f"+{name_key(view.name)}", name=view.name, kind=kind)
                    elements.append(target)
                    entry["created"].append({"view": view.id, "name": view.name, "kind": kind})
                elif target.kind not in linkable_kinds(diagram.type, view.kind):
                    reason = f"element '{target.id}' is a {target.kind} element, which a «{view.kind}» cannot represent"
                    entry["skipped"].append({"view": view.id, "name": view.name, "reason": reason})
                elif target.id.startswith("+"):
                    entry["created"].append(
                        {"view": view.id, "name": target.name, "kind": target.kind}
                    )  # the same new one
                else:
                    entry["linked"].append({"view": view.id, "element": target.id, "name": target.name})
            if entry["linked"] or entry["created"] or entry["skipped"]:
                planned.append(entry)
        return planned

    def adopt_all(self, project_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Adopt the unlinked adoptable views of every member diagram: the missing elements first, then one version
        per diagram ("Projektelemente übernommen"). Returns the outcome per changed diagram and the ids of diagrams that
        could not be written (they keep their views unlinked; the elements created for them are taken back)."""
        self._projects.require(project_id, "editor")
        _, members = self._projects.load_members(project_id)
        done: list[dict[str, Any]] = []
        failed: list[str] = []
        for _, diagram in members:
            adopter = Adopter(self._projects, project_id)
            plan = adopter.prepare(set(), diagram)
            if not plan:
                continue
            written: dict[str, Any] = {"steps": [], "names": {}}

            def change(
                target: Diagram, adopter: Adopter = adopter, plan: Any = plan, written: dict[str, Any] = written
            ) -> Change[None] | None:
                steps = adopter.steps(set(), target, plan)
                written["names"] = {v.id: v.name for v in view_elements(target)}
                for op, args in steps:
                    operations.apply(target, op, args)
                written["steps"] = steps
                return Change(target, None, label="Projektelemente übernommen") if steps else None

            try:
                self._diagrams.update(diagram.id, change)
            except (StoreError, Forbidden, operations.OperationError):
                written["steps"] = []
                failed.append(diagram.id)
            adopter.finish(plan, written["steps"])
            outcome = adopter.outcome(plan, written["steps"], written["names"])
            if outcome:
                settled = self.settle_adoption(diagram.id, outcome.to_dict()) or {}
                done.append({"id": diagram.id, "title": diagram.title, **settled})
        return done, failed

    def promote(
        self, diagram_id: str, view: str, kind: str | None = None, description: str = ""
    ) -> tuple[Element, Updated[dict[str, Any]]]:
        """Create an element named like the view element and link the view element to it."""
        project_id = self.project_of(diagram_id)
        diagram = self._diagrams.get(diagram_id)
        found = find_view(diagram, view)
        if found is None:
            raise ProjectError(f"Diagram '{diagram_id}' has no element '{view}'.")
        if found.ref is not None:
            raise ProjectError(f"'{found.id}' already represents the element '{found.ref}'.")
        chosen = kind or default_kind(diagram, found)
        if chosen is None or chosen not in linkable_kinds(diagram.type, found.kind):
            allowed = ", ".join(linkable_kinds(diagram.type, found.kind)) or "none"
            raise ProjectError(
                f"'{found.id}' («{found.kind}») cannot represent a {chosen} element (allowed: {allowed})."
            )
        element = self.add_element(project_id, found.name, chosen, description)
        try:
            updated = self.link(diagram_id, found.id, element.id)
        except BaseException:
            # the element would be unused: take it back (best effort; the review reports leftovers)
            with suppress(StoreError, ProjectError):
                self._projects.apply(project_id, "remove_element", {"element": element.id}, internal=True)
            raise
        return element, updated


def _blocked_views(diagrams: list[Diagram], element_id: str, kind: str) -> list[str]:
    """The views linked to the element that cannot represent `kind`, described for an error message."""
    return [
        f"'{view.id}' in '{d.id}' («{view.kind}»)"
        for d in diagrams
        for view in view_elements(d)
        if view.ref == element_id and kind not in linkable_kinds(d.type, view.kind)
    ]


def _refuse_kind(element_id: str, kind: str, blocked: list[str]) -> None:
    if blocked:
        raise ProjectError(
            f"Element '{element_id}' cannot become a {kind} element: {', '.join(blocked)} cannot represent one. "
            "Unlink them first."
        )


def not_taken_back(problem: str) -> str:
    """The error of a link that could not be taken back (the diagram kept changing, was deleted, or the caller lost
    editor): it stays stored, and the project review reports it as an incompatible link (ADR-0128)."""
    return (
        problem.removesuffix("; the link was taken back.").removesuffix(".")
        + "; the link could not be taken back and stays until it is unlinked (the project review reports it)."
    )
