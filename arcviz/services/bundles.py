"""ProjectExchangeService: export and import of whole projects as bundles.

Spec: docs/architecture/specs/project-exchange.md, ADR-0056. An import validates the whole bundle first
and always creates a new project with fresh diagram ids; a failing write removes what it created.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import exchange
from ..ports import StoreError
from ..projects import Project
from .diagrams import UNTITLED, DiagramService, untitled
from .projects import ProjectService
from .requirements import RequirementService

logger = logging.getLogger(__name__)


class ProjectExchangeService:
    def __init__(
        self, diagrams: DiagramService, projects: ProjectService, requirements: RequirementService | None = None
    ):
        self._diagrams = diagrams
        self._projects = projects
        self._requirements = requirements  # None: bundles without requirements (tests of the older shape)

    def export(self, project_id: str) -> dict[str, Any]:
        project = self._projects.get(project_id)
        diagrams = []
        for member in project.diagrams:
            try:
                diagrams.append(self._diagrams.get(member.diagram))
            except StoreError:
                if self._diagrams.exists(member.diagram):
                    raise  # a storage failure: never hand out an incomplete bundle
                continue  # deleted meanwhile (a dangling membership): not exported
        box = self._requirements.box(project_id) if self._requirements is not None else None
        return exchange.export_project(project, diagrams, box)

    def import_bundle(self, bundle: Any, title: str | None = None) -> Project:
        """Create a new project in the caller's personal workspace from `bundle` (ExchangeError before anything is
        written)."""
        return self.import_with_warnings(bundle, title)[0]

    def import_with_warnings(self, bundle: Any, title: str | None = None) -> tuple[Project, list[str]]:
        """`import_bundle`, and what the import changed on the way (shortened element texts, #212)."""
        parsed = exchange.parse_project(bundle)
        project = self._projects.create_with_sections(
            (title or "").strip() or parsed.title,
            parsed.description,
            parsed.sections,
            parsed.elements,
            parsed.relations,
            relation_seq=parsed.relation_seq,
            drivers=parsed.drivers,
            driver_seq=parsed.driver_seq,
            decision_support=parsed.decision_support,
            vision=parsed.vision,
        )
        created: list[str] = []
        try:
            if parsed.requirements is not None and self._requirements is not None:
                self._requirements.restore(project.id, parsed.requirements)
            for diagram_id, section in parsed.members:
                diagram = parsed.diagrams[diagram_id]
                created.append(
                    self._diagrams.import_new(diagram, diagram.title, None, project.id, section, keep_refs=True)
                )
        except BaseException:
            self._roll_back(project.id, created)
            raise
        warnings = list(parsed.warnings)
        blank = sum(1 for d in parsed.diagrams.values() if untitled(d.title))
        if blank:  # imported all the same (#103 review)
            warnings.append(f"{blank} diagram(s) without a title were called „{UNTITLED}“.")
        return self._projects.get(project.id), warnings

    def _roll_back(self, project_id: str, created: list[str]) -> None:
        for diagram_id in created:
            try:
                self._diagrams.delete(diagram_id)
            except Exception:  # best effort: the repair step drops what is left
                logger.warning("Rollback of the import could not delete diagram '%s'", diagram_id, exc_info=True)
        try:
            self._projects.delete(project_id)
        except Exception:
            logger.warning("Rollback of the import could not delete project '%s'", project_id, exc_info=True)
