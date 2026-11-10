"""The migration of existing diagrams into the project model (spec: docs/architecture/specs/model-first.md, ADR-0220).

Every member diagram that the repository still holds as a full document (written before model first) is written once
through the repository, which turns its nodes and edges into the project's elements and connections and stores the
view. Elements merge by link, id or name (the reuse rule); a converted view never shows more than the document did.
System context, idempotent, best effort: a diagram that fails is logged and left for the next start.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..model import Diagram
from ..ports import Change, RepositoryPort, StoreError

logger = logging.getLogger(__name__)

LABEL = "Ins Projektmodell übernommen"  # the version a converted diagram gets (German like every version label)


@dataclass
class ModelMigration:
    converted: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # diagram id -> exception type


def needs_migration(diagram: Diagram) -> bool:
    """Whether the repository still holds the diagram as a full document (see `views.resolve`)."""
    return bool(diagram._legacy)


def migrate_to_model(repository: RepositoryPort) -> ModelMigration:
    report = ModelMigration()
    for project, _revision in repository.list_projects():
        for member in project.diagrams:
            try:
                diagram = repository.load(member.diagram)
                if not needs_migration(diagram):
                    continue
                repository.update(
                    member.diagram,
                    lambda d: Change(d, None, label=LABEL) if needs_migration(d) else None,
                )
                report.converted.append(member.diagram)
            except (StoreError, ValueError) as exc:  # one diagram never stops the others
                report.failed[member.diagram] = type(exc).__name__
                logger.warning("A diagram could not be taken into the project model (%s)", type(exc).__name__)
    return report
