"""LiveService: live streams and presence for one caller (spec: docs/architecture/specs/collaboration.md).

Only callers who may view a diagram (or project) open its stream; every re-check reads the project, the workspace
(fresh) and the diagram's revision again, so a removed member, a deleted diagram or one moved out of sight ends the
stream (ADR-0096). The re-checks of all streams share one read per document and change (`LiveHub.shared`); the role
is computed per caller. Presence is the hub's (in memory, ADR-0097); a selection can only be set through an open
stream of the caller and is access-checked against that stream's project.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from ..live import LiveHub, Subscription, Watch
from ..ports import MUTATION_ATTEMPTS, ConflictError, RepositoryPort, StoreError
from ..projects import Project
from ..workspaces import project_role
from .access import Guard, ProjectNotFound, WorkspaceNotFound, missing_diagram, missing_project
from .projects import ProjectService


class UnknownSession(Exception):  # noqa: N818 - named after what is missing
    """No open live stream of the caller with this session on this diagram (HTTP 409)."""


class LiveService:
    def __init__(self, hub: LiveHub | None, repository: RepositoryPort, projects: ProjectService, guard: Guard | None):
        self._hub = hub
        self._repo = repository
        self._projects = projects
        self._guard = guard

    @property
    def hub(self) -> LiveHub:
        if self._hub is None or self._guard is None:
            raise RuntimeError("Live streams need the hub and a caller.")
        return self._hub

    @property
    def guard(self) -> Guard:
        if self._guard is None:
            raise RuntimeError("Live streams need a caller.")
        return self._guard

    # ---- watching ---------------------------------------------------------------------------------

    def watch_diagram(self, diagram_id: str) -> Watch:
        """What a stream of the diagram watches; DiagramNotFound unless the caller may view it."""
        project = self.guard.diagram(diagram_id)
        return self._diagram_watch(diagram_id, project.id)

    def watch_project(self, project_id: str) -> Watch:
        """What a stream of the project watches (no diagram open); ProjectNotFound unless the caller may view it."""
        project, revision = self._project(project_id)
        role = self._role(project)
        if role is None:
            raise missing_project(project_id)
        return Watch(None, project.id, project.workspace, role, None, revision)

    def recheck(self, watch: Watch) -> Watch | None:
        """The watch read again (access, revisions); None when it is gone for the caller: deleted, moved out of
        sight, or the caller lost access. Never says which (ADR-0096). The documents are read once per change for
        every stream (`LiveHub.shared`); the role is computed for this caller from them."""
        try:
            if watch.diagram is None:
                return self.watch_project(watch.project)
            return self._diagram_watch(watch.diagram, watch.project)
        except ConflictError:
            raise
        except StoreError:  # not found, or a failed read (stores report both as StoreError): look again
            if self._gone(watch):
                return None
            raise  # a transient failure: the stream ends with an error and the client reconnects

    def _gone(self, watch: Watch) -> bool:
        """A second, fresh look after a failed re-check (unshared reads): True when the diagram or project is gone
        for the caller, False when it is there (the failure was transient); a failing read again counts as gone."""
        try:
            if watch.diagram is None:
                self._projects.watch(watch.project)
            else:
                if not self._repo.exists(watch.diagram):
                    return True
                self.guard.diagram(watch.diagram, fresh=True)
        except StoreError:
            return True
        return False

    def _project(self, project_id: str) -> tuple[Project, str]:
        """The project document and its revision, shared by all re-checks until it changes."""
        try:
            return self.hub.shared("project", project_id, lambda: self._repo.read_project(project_id))
        except ConflictError:
            raise
        except StoreError as exc:
            raise missing_project(project_id) from exc

    def _role(self, project: Project) -> str | None:
        """The caller's role, from the workspace document shared by all re-checks until it changes."""
        if project.workspace is None:
            return None
        workspace_id = project.workspace
        try:
            workspace, _ = self.hub.shared(
                "workspace", workspace_id, lambda: self.guard.policy.read_workspace(workspace_id)
            )
        except WorkspaceNotFound:
            return None
        return project_role(self.guard.actor.id, workspace, project)

    def _diagram_watch(self, diagram_id: str, project_id: str) -> Watch:
        try:
            project, project_revision = self._project(project_id)
        except ProjectNotFound:
            project = None
        if project is None or project.member(diagram_id) is None:  # moved (or its project deleted): locate it again
            located = self.guard.policy.locate(diagram_id)  # the role is checked below, on the shared reads
            if located is None:
                raise missing_diagram(diagram_id)
            project, project_revision = self._project(located.id)
            if project.member(diagram_id) is None:
                raise missing_diagram(diagram_id)
        role = self._role(project)
        if role is None:
            raise missing_diagram(diagram_id)
        revision = self.hub.shared("diagram", diagram_id, lambda: self._repo.read_revision(diagram_id))
        return Watch(diagram_id, project.id, project.workspace, role, revision, project_revision)

    # ---- streams: opened only for what this service checked ------------------------------------------

    def open_diagram(self, diagram_id: str, session: str | None, loop: asyncio.AbstractEventLoop) -> Subscription:
        """A stream of the diagram for the caller (DiagramNotFound unless they may view it; StreamLimit at the caps,
        ADR-0098)."""
        self.hub  # noqa: B018 - RuntimeError without a hub, before anything is read
        return self._open(self.watch_diagram(diagram_id), session, loop)

    def open_project(self, project_id: str, session: str | None, loop: asyncio.AbstractEventLoop) -> Subscription:
        """A stream of the project for the caller (ProjectNotFound unless they may view it)."""
        self.hub  # noqa: B018 - RuntimeError without a hub, before anything is read
        return self._open(self.watch_project(project_id), session, loop)

    def _open(self, watch: Watch, session: str | None, loop: asyncio.AbstractEventLoop) -> Subscription:
        actor = self.guard.actor
        return self.hub.subscribe(actor.id, actor.name, session, watch, loop)

    # ---- presence -------------------------------------------------------------------------------------

    def select(self, diagram_id: str, session: str, selection: str | None) -> None:
        """The element the caller's stream `session` on the diagram has selected (None: nothing). DiagramNotFound
        unless the caller may view the diagram, UnknownSession unless that stream is open; ValueError for an invalid
        selection. With an open stream the access check reads the stream's project (shared), never every project; a
        diagram moved since the stream last checked moves the stream first, so the selection is shown to the viewers
        of the project it is in now, never to those of the one it left."""
        sub = self.hub.find(self.guard.actor.id, session)
        if sub is None or sub.watch.diagram != diagram_id:
            self.guard.diagram(diagram_id)  # 404 for a diagram the caller may not view, else:
            raise UnknownSession("Unknown live session.")
        for _ in range(MUTATION_ATTEMPTS):
            base, current = sub.watch_version, sub.watch
            watch = self._diagram_watch(diagram_id, current.project)  # DiagramNotFound when out of the caller's sight
            if (watch.project, watch.workspace, watch.role) == (current.project, current.workspace, current.role):
                break
            # moved: re-point the stream (presence index included) before anything is recorded or poked; its revisions
            # stay as the stream last sent them, so its next re-check still reports the change. Versioned (ADR-0105):
            # a stream re-check that finished meanwhile wins, and this one looks again.
            if self.hub.update_watch(sub, replace(watch, revision=current.revision), based_on=base):
                break
        else:
            raise ConflictError("The live session kept changing; try again.")
        if not self.hub.select(self.guard.actor.id, session, diagram_id, selection):
            raise UnknownSession("Unknown live session.")

    @contextmanager
    def chatting(self, diagram_id: str | None = None, project_id: str | None = None) -> Iterator[None]:
        """The caller is in a chat turn on the diagram (and its project) or the project until the block ends;
        nothing without a hub. The diagram is only named when the caller may view it and it belongs to the project
        (a project turn's `active` comes from the request), else the turn counts for the project alone."""
        if self._hub is None or self._guard is None:
            yield
            return
        shown: str | None = None
        if diagram_id is not None:
            try:
                located = self._guard.diagram(diagram_id).id
            except StoreError:
                located = None
            if located is not None and (project_id is None or located == project_id):
                shown, project_id = diagram_id, located
        actor = self._guard.actor
        with self._hub.chatting(actor.id, actor.name, shown, project_id):
            yield
