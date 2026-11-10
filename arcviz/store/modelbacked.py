"""A repository that keeps diagrams as views of their project's model (spec: docs/architecture/specs/model-first.md,
ADR-0213..0218).

`ModelBackedStore` wraps a backend. Reads resolve a stored diagram against the model of the project that holds it
(`views.resolve`); writes split a diagram into what the model gets (elements, connections: written to the project
first, conditionally) and the document the backend stores (`views.decompose`; for structure views only the ids they
show). Versions keep the resolved diagram as their snapshot, so history, comparison and restore read what the view
showed then. Everything else is the backend's.

The revision of a diagram held by a project is `<backend revision>~<fingerprint of the resolved diagram>`: a model
change that alters what a view shows changes its revision (live polling, `If-Match`), one that does not leaves it.
A diagram outside every project is passed through unchanged, with the backend's revision.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import views
from ..model import Diagram, fingerprint
from ..operations import OperationError
from ..ports import ConflictError, StoreError, VersionInfo
from ..projects import Project, check_document_size
from .base import DiagramRepository

logger = logging.getLogger(__name__)

LOCATION = "location"  # services/locations.py: the projects that may hold a diagram (ADR-0120)
PLACEMENT = "placement"  # services/projects.py: the project a diagram is being created for (ADR-0088)
SEPARATOR = "~"


def split_revision(revision: str | None) -> tuple[str | None, str | None]:
    """(backend revision, content fingerprint) of a revision this store issued; a backend revision alone stays."""
    if revision is None:
        return None, None
    inner, sep, content = revision.rpartition(SEPARATOR)
    if not sep:
        return revision, None
    return inner, content


class ModelBackedStore(DiagramRepository):
    """Diagrams as views of the project model over any backend (see the module docstring)."""

    def __init__(self, backend: DiagramRepository):
        super().__init__(mirror=None)
        self.backend = backend
        self.name = backend.name

    def __getattr__(self, name: str) -> Any:  # backend specifics (paths, clients) for tests and tools
        return getattr(self.__dict__["backend"], name)

    # ---- locating the model ------------------------------------------------------------------------------

    def project_of(self, diagram_id: str) -> tuple[Project, str] | None:
        """The project (and its revision) whose membership holds the diagram, else the one it is being created for;
        None for a diagram outside every project."""
        candidates: list[str] = []
        try:
            body, _rev = self.backend.read_document(LOCATION, diagram_id)
            names = body.get("projects")
            if isinstance(names, list):
                candidates += [p for p in names if isinstance(p, str) and p]
        except StoreError:
            pass
        found: list[tuple[Project, str]] = []
        for project_id in dict.fromkeys(candidates):
            try:
                project, revision = self.backend.read_project(project_id)
            except StoreError:
                continue
            member = project.member(diagram_id)
            if member is not None:
                found.append((project, revision))
        if found:
            return max(found, key=lambda pr: pr[0].member(diagram_id).added_at)  # type: ignore[union-attr]
        try:
            body, _rev = self.backend.read_document(PLACEMENT, diagram_id)
            target = body.get("project")
            if isinstance(target, str) and target:
                return self.backend.read_project(target)
        except StoreError:
            pass
        return None

    def _revision(self, inner: str, resolved: Diagram, project: Project | None) -> str:
        return inner if project is None else f"{inner}{SEPARATOR}{fingerprint(resolved)}"

    # ---- primitives: reads ---------------------------------------------------------------------------------

    def read_latest(self, diagram_id: str) -> tuple[Diagram, str]:
        stored, inner = self.backend.read_latest(diagram_id)
        located = self.project_of(diagram_id)
        project = located[0] if located else None
        resolved = views.resolve(stored, project)
        return resolved, self._revision(inner, resolved, project)

    def read_revision(self, diagram_id: str) -> str:
        return self.read_latest(diagram_id)[1]

    def load_version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]:
        info, diagram = self.backend.load_version(diagram_id, version)
        if views.is_view(diagram):  # a snapshot not replaced by its resolved form (a crash in between): best effort
            located = self.project_of(diagram_id)
            diagram = views.resolve(diagram, located[0] if located else None)
        return info, diagram

    # ---- primitives: writes --------------------------------------------------------------------------------

    def _write_model(self, project: Project, before: Project, revision: str) -> None:
        errors = project.integrity_errors()
        if errors:
            raise StoreError("Refusing to persist an inconsistent project model: " + "; ".join(errors[:5]))
        check_document_size(project, before)
        self.backend.commit_project(project, revision)  # ConflictError: the caller's update retries

    def _refuse_represented(self, project: Project, diagram_id: str, removed: list[str]) -> None:
        """A deletion from the model through a diagram (scope "model") is refused while a lifeline, partition or data
        store of another diagram stands for the element, like `ModelService.remove_element` (ADR-0213)."""
        gone = {e for e in removed if not any(c.id == e for c in project.connections)}
        if not gone:
            return
        found: list[str] = []
        for member in project.diagrams:
            if member.diagram == diagram_id:
                continue
            try:
                other, _rev = self.backend.read_latest(member.diagram)
            except StoreError:
                continue
            if views.is_view(other):
                continue
            refs = {n.ref for n in other.nodes.values()}
            if other.interaction is not None:
                refs |= {lf.ref for lf in other.interaction.lifelines}
            hit = sorted(gone & {r for r in refs if r})
            if hit:
                found.append(f"{other.title} ({other.id}): {', '.join(hit)}")
        if found:
            raise OperationError(
                f"Represented in behaviour diagrams: {'; '.join(found)}. Remove those lifelines or partitions first, or "
                "hide the element here (scope view)."
            )

    def create_latest(self, diagram: Diagram, info: VersionInfo) -> tuple[str, VersionInfo]:
        located = self.project_of(diagram.id)
        if located is None:
            return self.backend.create_latest(diagram, info)
        before, project_revision = located
        project = before.model_copy(deep=True)
        result = views.decompose(diagram, None, project)
        if result.model_changed:
            self._write_model(project, before, project_revision)
        inner, stored = self.backend.create_latest(result.stored, info)
        resolved = views.resolve(result.stored, project)
        self._snapshot(diagram.id, resolved, stored)
        _become(diagram, resolved)
        return self._revision(inner, resolved, project), stored

    def commit_latest(
        self, diagram: Diagram, expected: str | None, info: VersionInfo | None
    ) -> tuple[str, VersionInfo | None]:
        located = self.project_of(diagram.id)
        if located is None:
            return self.backend.commit_latest(diagram, split_revision(expected)[0], info)
        before, project_revision = located
        stored, inner = self.backend.read_latest(diagram.id)
        expected_inner, expected_content = split_revision(expected)
        if expected_inner is not None and expected_inner != inner:
            raise ConflictError(f"Diagram '{diagram.id}' has changed.")
        if expected_content is not None and fingerprint(views.resolve(stored, before)) != expected_content:
            raise ConflictError(f"The model shown by diagram '{diagram.id}' has changed.")
        if diagram._detach:  # leaving the project: kept whole until the target's model takes it (a move)
            full = diagram.model_copy(update={"shows": None, "hidden_connections": []}, deep=True)
            new_inner, version = self.backend.commit_latest(full, inner, info)
            if version is not None:
                self._snapshot(diagram.id, full, version)
            return new_inner, version
        project = before.model_copy(deep=True)
        result = views.decompose(diagram, stored, project)
        self._refuse_represented(project, diagram.id, result.removed)
        if result.model_changed:
            self._write_model(project, before, project_revision)
        new_inner, version = self.backend.commit_latest(result.stored, inner, info)
        resolved = views.resolve(result.stored, project)
        if version is not None:
            self._snapshot(diagram.id, resolved, version)
        _become(diagram, resolved)
        return self._revision(new_inner, resolved, project), version

    def _snapshot(self, diagram_id: str, resolved: Diagram, version: VersionInfo) -> None:
        """Keep the resolved diagram as the version's snapshot (history shows what the view showed then), with the
        fingerprint and counts of what is kept (undo and redo compare fingerprints). Updates `version` in place."""
        version.fingerprint = fingerprint(resolved)
        version.node_count, version.edge_count = len(resolved.nodes), len(resolved.edges)
        try:
            self.backend.put_version(diagram_id, resolved, version)
        except StoreError:
            logger.warning("Keeping the resolved snapshot of a version failed")

    def purge(self, diagram_id: str, expected: str | None = None) -> None:
        inner, content = split_revision(expected)
        if content is not None:
            current = self.read_latest(diagram_id)[1]
            if split_revision(current)[1] != content:
                raise ConflictError(f"The model shown by diagram '{diagram_id}' has changed.")
        self.backend.purge(diagram_id, inner)

    def write_latest(self, diagram: Diagram) -> str:
        return self.backend.write_latest(diagram)

    # ---- everything else is the backend's ------------------------------------------------------------------

    def ping(self) -> None:
        self.backend.ping()


def _become(diagram: Diagram, resolved: Diagram) -> None:
    """Let the caller's diagram object show what was stored: the connections the model draws between the shown
    elements, the model's ids and names (the caller answers with it)."""
    for name in Diagram.model_fields:
        setattr(diagram, name, getattr(resolved, name))
    diagram._reserved, diagram._pool, diagram._edge_floor = resolved._reserved, resolved._pool, resolved._edge_floor
    diagram._hidden_edges = resolved._hidden_edges
    diagram._removals = set()


def _delegate(name: str) -> Any:
    def method(self: ModelBackedStore, *args: Any, **kwargs: Any) -> Any:
        return getattr(self.backend, name)(*args, **kwargs)

    method.__name__ = name
    return method


for _name in sorted(getattr(DiagramRepository, "__abstractmethods__", ())):
    if _name not in ModelBackedStore.__dict__:
        setattr(ModelBackedStore, _name, _delegate(_name))
for _name in ("presence_channel", "close"):
    if _name not in ModelBackedStore.__dict__:
        setattr(ModelBackedStore, _name, _delegate(_name))
ModelBackedStore.__abstractmethods__ = frozenset()
