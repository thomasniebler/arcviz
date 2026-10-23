"""Structural views become project elements when they are created (spec: docs/architecture/specs/elements-by-default.md,
ADR-0164).

`Adopter` works around one diagram write, project first (like `ModelService.promote`): `prepare` matches the new
adoptable views of a probed result against the project's elements and creates the missing ones, `steps` gives the
internal `link_element` operations to run inside the diagram's change (again on every conflict), and `finish` takes
back the elements that no view ended up standing for.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from .. import operations
from ..model import Diagram
from ..ports import StoreError
from ..project_model import (
    Element,
    ViewElement,
    adoptable_views,
    adoption_target,
    default_kind,
    linkable_kinds,
    name_key,
    view_elements,
)
from ..projects import ProjectError
from .access import Forbidden
from .projects import ProjectService

logger = logging.getLogger(__name__)

Steps = list[tuple[str, dict[str, Any]]]


@dataclass
class Adoption:
    """What an adoption did: views linked to an existing element, linked to a new one, or left unlinked (why)."""

    linked: list[dict[str, str]] = field(default_factory=list)  # {"view", "element", "name"}
    created: list[dict[str, str]] = field(default_factory=list)  # {"view", "element", "name", "kind"}
    skipped: list[dict[str, str]] = field(default_factory=list)  # {"view", "name", "reason"}

    def __bool__(self) -> bool:
        return bool(self.linked or self.created or self.skipped)

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        return {"linked": self.linked, "created": self.created, "skipped": self.skipped}


@dataclass
class AdoptionPlan:
    """The elements the new views of one write stand for, by view id and by name (a re-run after a conflict may give a
    view another id), the views that cannot be adopted, and the elements `prepare` created."""

    by_view: dict[str, Element] = field(default_factory=dict)
    by_name: dict[str, Element] = field(default_factory=dict)
    skipped: dict[str, dict[str, str]] = field(default_factory=dict)
    created: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.by_view or self.skipped)


def view_ids(diagram: Diagram) -> set[str]:
    return {v.id for v in view_elements(diagram)}


def _unfit(element: Element, view: ViewElement) -> str:
    return f"element '{element.id}' is a {element.kind} element, which a «{view.kind}» cannot represent"


class Adopter:
    """Adoption of the new views of one diagram write into project `project_id`."""

    def __init__(self, projects: ProjectService, project_id: str):
        self._projects = projects
        self.project_id = project_id

    def prepare(self, before: set[str], after: Diagram) -> AdoptionPlan:
        """Match the new adoptable unlinked views of `after` (ids not in `before`) and create the missing elements, one
        project write each (a few per operation). An element another request created meanwhile is matched instead;
        one that cannot be created (the limit, a bound) leaves its views unlinked with the reason."""
        plan = AdoptionPlan()
        views = adoptable_views(after, before)
        if not views:
            return plan
        elements = list(self._projects.get(self.project_id).elements)
        for view in views:
            key = name_key(view.name)
            target = plan.by_name.get(key) or adoption_target(elements, view.name)
            if target is None:
                target = self._create(view, after, elements, plan)
                if target is None:
                    continue
            if target.kind not in linkable_kinds(after.type, view.kind):
                plan.skipped[view.id] = {"view": view.id, "name": view.name, "reason": _unfit(target, view)}
                continue
            plan.by_view[view.id] = target
            plan.by_name.setdefault(key, target)
        return plan

    def _create(
        self, view: ViewElement, diagram: Diagram, elements: list[Element], plan: AdoptionPlan
    ) -> Element | None:
        kind = default_kind(diagram, view) or "component"
        try:
            result = self._projects.apply(
                self.project_id, "add_element", {"name": view.name, "kind": kind}, internal=True
            )
        except ProjectError as exc:
            # created meanwhile by another request: use it; anything else (the limit, a bound): leave the view local
            elements[:] = self._projects.get(self.project_id).elements
            found = adoption_target(elements, view.name)
            if found is None:
                plan.skipped[view.id] = {"view": view.id, "name": view.name, "reason": str(exc)}
            return found
        element = Element.model_validate(result["result"])
        elements.append(element)
        plan.created.add(element.id)
        return element

    def steps(self, before: set[str], diagram: Diagram, plan: AdoptionPlan) -> Steps:
        """The internal `link_element` operations for the new views of `diagram` (ids not in `before`) that the plan
        has an element for: still unlinked and still able to stand for it."""
        steps: Steps = []
        for view in adoptable_views(diagram, before):
            target = plan.by_view.get(view.id) or plan.by_name.get(name_key(view.name))
            if target is None or target.kind not in linkable_kinds(diagram.type, view.kind):
                continue
            steps.append(
                (
                    "link_element",
                    {"id": view.id, "element": target.id, "name": target.name, "element_kind": target.kind},
                )
            )
        return steps

    def outcome(self, plan: AdoptionPlan, steps: Steps, views: dict[str, str]) -> Adoption:
        """What the written steps did; `views` maps view ids to the names they had before the link."""
        adoption = Adoption()
        for _, args in steps:
            entry = {"view": args["id"], "element": args["element"], "name": args["name"]}
            if args["element"] in plan.created:
                adoption.created.append({**entry, "kind": args["element_kind"]})
            else:
                adoption.linked.append(entry)
        linked = {args["id"] for _, args in steps}
        adoption.skipped = [s for v, s in plan.skipped.items() if v not in linked and v in views]
        return adoption

    def finish(self, plan: AdoptionPlan, steps: Steps) -> None:
        """Take back the elements `prepare` created that no written link uses (best effort; ADR-0164). An element that a
        concurrent adoption or link reused meanwhile stays: only elements no diagram of the project refers to are
        removed (the members are read again), so no committed view is left with a dangling reference."""
        unused = plan.created - {args["element"] for _, args in steps}
        if not unused:
            return
        try:
            project, members = self._projects.load_members(self.project_id)
        except (StoreError, ProjectError, Forbidden):
            return  # cannot tell whether it is used: keep it (the review reports an unused element)
        referenced = {v.ref for _, d in members for v in view_elements(d) if v.ref is not None}
        referenced |= {s.element for s in project.sections if s.element is not None}
        for element_id in sorted(unused - referenced):
            with suppress(StoreError, ProjectError, Forbidden):
                self._projects.apply(self.project_id, "remove_element", {"element": element_id}, internal=True)


def adopted_views(adoption: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(view id, element id) of every link an adoption wrote."""
    if not adoption:
        return []
    return [(e["view"], e["element"]) for key in ("linked", "created") for e in adoption.get(key, [])]


def without_taken_back(adoption: dict[str, Any], problems: dict[str, str]) -> dict[str, Any]:
    """The adoption after links were taken back again (view id -> why): they move to `skipped`."""
    kept = {key: [e for e in adoption.get(key, []) if e["view"] not in problems] for key in ("linked", "created")}
    skipped = list(adoption.get("skipped", []))
    for key in ("linked", "created"):
        skipped += [
            {"view": e["view"], "name": e["name"], "reason": problems[e["view"]]}
            for e in adoption.get(key, [])
            if e["view"] in problems
        ]
    return {**kept, "skipped": skipped}


def unlink_change(problems: dict[str, str], elements: dict[str, str]) -> Callable[[Diagram], list[str]]:
    """The change that takes adopted links back: each view only while it still stands for the element it was linked
    to (`elements`: view id -> element id); its result lists the views it unlinked."""

    def change(target: Diagram) -> list[str]:
        undone: list[str] = []
        for view_id in problems:
            found = next((v for v in view_elements(target) if v.id == view_id), None)
            if found is not None and found.ref == elements[view_id]:
                operations.apply(target, "unlink_element", {"id": view_id})
                undone.append(view_id)
        return undone

    return change


def adopting(
    adopter: Adopter | None,
    diagram: Diagram,
    apply: Callable[[Diagram], Any],
    failed: Callable[[Any], bool],
) -> tuple[Callable[[Diagram], Any], Callable[[], Adoption | None]]:
    """Wrap a diagram change so that the views it creates are adopted in the same write.

    The change is probed on a copy of `diagram` first; when it creates adoptable views, the elements are prepared
    (project write) and the returned change applies the operation and then the links. The second function, called once
    after the write (also when it failed), takes back unused elements and returns what was adopted (None: nothing).
    Without an adopter, for a failing change, or when the project cannot be written, the change is returned as it is."""
    if adopter is None:
        return apply, lambda: None
    probe = diagram.model_copy(deep=True)
    try:
        probed = apply(probe)
    except Exception:  # the real call reports it
        return apply, lambda: None
    if failed(probed):
        return apply, lambda: None
    try:
        plan = adopter.prepare(view_ids(diagram), probe)
    except (StoreError, ProjectError, Forbidden) as exc:
        logger.warning("New views could not be adopted into the project model (%s)", type(exc).__name__)
        return apply, lambda: None
    if not plan:
        return apply, lambda: None
    written: dict[str, Any] = {"steps": [], "names": {}}

    def change(target: Diagram) -> Any:
        before = view_ids(target)
        result = apply(target)
        if failed(result):
            written["steps"] = []
            return result
        steps = adopter.steps(before, target, plan)
        written["names"] = {v.id: v.name for v in view_elements(target) if v.id not in before}
        for op, args in steps:
            operations.apply(target, op, args)
        written["steps"] = steps
        adoption = adopter.outcome(plan, steps, written["names"])
        if adoption and isinstance(result, dict):  # the caller (a tool result, an HTTP answer) says what was adopted
            return {**result, "adoption": adoption.to_dict()}
        return result

    def done() -> Adoption | None:
        adopter.finish(plan, written["steps"])
        adoption = adopter.outcome(plan, written["steps"], written["names"])
        return adoption or None

    return change, done
