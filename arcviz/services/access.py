"""The access policy: what a caller may see and do (spec: docs/architecture/specs/personal-workspaces.md).

One policy for the whole application (ADR-0084): the services hold a `Guard` (the policy bound to one caller) and
ask it before they read or write; without a guard they run in the system context (tests, CLI, migration,
repairs). Resources the caller cannot see raise the not-found error the store would raise (HTTP 404, no existence
leak); visible ones with too low a role raise `Forbidden` (HTTP 403).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..ports import (
    ConflictError,
    DiagramRepositoryPort,
    DocumentRepositoryPort,
    ProjectRepositoryPort,
    StoreError,
    is_definitely_missing,
)
from ..projects import Project
from ..workspaces import PERSONAL_TITLE, Identity, Workspace, allows, personal_workspace_id, project_role
from .listing import prune_listing, read_listing_document
from .locations import locate_membership

WORKSPACE = "workspace"
logger = logging.getLogger(__name__)


class Forbidden(Exception):  # noqa: N818 - the HTTP name is the clearest
    """The caller sees the resource but its role does not allow the action (HTTP 403)."""


class WorkspaceNotFound(StoreError):
    """The workspace does not exist, or it is not the caller's (HTTP 404)."""


class ProjectNotFound(StoreError):
    """The project does not exist (any more), or the caller may not see it (HTTP 404)."""


class DiagramNotFound(StoreError):
    """The diagram does not exist, or the caller may not see it (HTTP 404)."""


def missing_workspace(workspace_id: str) -> WorkspaceNotFound:
    return WorkspaceNotFound(f"Workspace '{workspace_id}' not found.")


def missing_project(project_id: str) -> ProjectNotFound:
    return ProjectNotFound(f"Project '{project_id}' not found.")  # the store's own message


def missing_diagram(diagram_id: str) -> DiagramNotFound:
    return DiagramNotFound(f"Diagram '{diagram_id}' not found.")  # the store's own message


def too_low(what: str, role: str, need: str) -> Forbidden:
    if role == "viewer":
        return Forbidden(f"Read-only access: your role on {what} is viewer; this needs {need}.")
    return Forbidden(f"Your role on {what} is {role}; this needs {need}.")


def authored_entries(entries: list[dict[str, Any]], author: Identity | None) -> list[dict[str, Any]]:
    """Transcript entries with their author (ADR-0086): entries without one get the caller's id and display name;
    summaries and the system context stay without."""
    if author is None:
        return entries
    return [
        e if "author" in e or e.get("role") == "summary" else {**e, "author": author.id, "author_name": author.name}
        for e in entries
    ]


def ensure_personal_workspace(documents: DocumentRepositoryPort, identity: Identity) -> Workspace:
    """The caller's personal workspace, created on first use (ADR-0100): create-if-absent under the id derived from
    the user id, so replicas racing for it end with the same one. A document under that id that is not this user's
    personal workspace is never used (StoreError): it can only have been written by hand."""
    workspace_id = personal_workspace_id(identity.id)
    for _ in range(2):
        try:
            body, _ = documents.read_document(WORKSPACE, workspace_id)
        except ConflictError:
            raise
        except StoreError:
            fresh = Workspace(id=workspace_id, title=PERSONAL_TITLE, owner=identity.id, personal=True)
            try:
                documents.create_document(WORKSPACE, workspace_id, fresh.model_dump(mode="json"))
                return fresh
            except ConflictError:
                raise
            except StoreError as exc:
                if "already exists" not in str(exc):
                    raise
                continue  # created concurrently (another replica): read it
        try:
            workspace = Workspace.model_validate(body)
        except ValidationError:
            workspace = None
        if workspace is None or not workspace.is_personal_of(identity.id):
            logger.error(
                "Document '%s' is not the personal workspace of '%s'; it is not used", workspace_id, identity.id
            )
            raise StoreError(f"Workspace '{workspace_id}' is not the personal workspace of '{identity.id}'.")
        return workspace
    raise StoreError(f"The personal workspace of '{identity.id}' could not be read.")


class AccessPolicy:
    """Reads what a decision needs through the ports; one instance per application. `diagrams` answers whether a
    diagram exists for the locator's fallback (ADR-0120); without it every miss of the locator scans."""

    def __init__(
        self,
        documents: DocumentRepositoryPort,
        projects: ProjectRepositoryPort,
        diagrams: DiagramRepositoryPort | None = None,
    ):
        self.documents = documents
        self._projects = projects
        self._exists: Callable[[str], bool] = diagrams.exists if diagrams is not None else (lambda _id: True)

    def read_project(self, project_id: str) -> Project:
        return self.read_project_entry(project_id)[0]

    def read_project_entry(self, project_id: str) -> tuple[Project, str]:
        """The project and its revision; ProjectNotFound if it does not exist."""
        try:
            return self._projects.read_project(project_id)
        except ConflictError:
            raise
        except StoreError as exc:
            raise missing_project(project_id) from exc

    def locate(self, diagram_id: str) -> Project | None:
        """The project the diagram is a member of (as just read), or None: through the locator (#181, ADR-0120), a
        hint the caller of this method checks the role on."""
        found = self.locate_entry(diagram_id)
        return found[0] if found is not None else None

    def locate_entry(self, diagram_id: str) -> tuple[Project, str] | None:
        """As `locate`, with the project's revision."""
        found = locate_membership(self.documents, self._projects, diagram_id, self._exists)
        return (found.project, found.revision) if found is not None else None

    def read_workspace(self, workspace_id: str) -> tuple[Workspace, str]:
        """The workspace and its revision; WorkspaceNotFound if it does not exist."""
        try:
            body, revision = self.documents.read_document(WORKSPACE, workspace_id)
        except ConflictError:
            raise
        except StoreError as exc:
            raise missing_workspace(workspace_id) from exc
        return Workspace.model_validate(body), revision

    def guard(self, actor: Identity, cache_projects: bool = False) -> Guard:
        return Guard(self, actor, cache_projects)


class Guard:
    """The policy bound to one caller, for one request or one chat turn. Reads (viewer) use the workspaces it cached;
    every check for more than viewer reads the workspace again (ADR-0090). A share is read with the project on every
    check, so a revoked or lowered share is effective at the caller's next step, also within a chat turn; only a guard
    of one ordinary HTTP request (`cache_projects`, #166, ADR-0121) lets its viewer checks reuse the project documents
    it read (checks for more than viewer and `fresh` ones read again; `forget` drops a copy the request wrote)."""

    def __init__(self, policy: AccessPolicy, actor: Identity, cache_projects: bool = False):
        self.policy = policy
        self.actor = actor
        self.cache_projects = cache_projects
        self._workspaces: dict[str, Workspace | None] = {}
        self._located: dict[str, str | None] = {}  # diagram id -> project id
        self._projects: dict[str, tuple[Project, str]] = {}  # project id -> (project, revision), with cache_projects

    # ---- project documents (#166) ----------------------------------------------------------------------

    def _project_entry(self, project_id: str, reuse: bool) -> tuple[Project, str]:
        """The project and its revision: the copy this request read before when `reuse` (and the cache is on), else
        read now (and kept). ProjectNotFound if it does not exist."""
        if reuse and self.cache_projects and project_id in self._projects:
            return self._projects[project_id]
        entry = self.policy.read_project_entry(project_id)
        self._remember(*entry)
        return entry

    def _remember(self, project: Project, revision: str) -> None:
        if self.cache_projects:
            self._projects[project.id] = (project, revision)

    def forget(self, project_id: str) -> None:
        """Drop the cached copy of a project this request wrote or deleted: its next read sees the write."""
        self._projects.pop(project_id, None)

    # ---- workspaces ----------------------------------------------------------------------------------

    def _workspace(self, workspace_id: str, fresh: bool = False) -> Workspace | None:
        if fresh or workspace_id not in self._workspaces:
            try:
                self._workspaces[workspace_id] = self.policy.read_workspace(workspace_id)[0]
            except WorkspaceNotFound:
                self._workspaces[workspace_id] = None
        return self._workspaces[workspace_id]

    def own_workspace(self, workspace_id: str | None = None) -> Workspace:
        """The caller's personal workspace, created if missing (ADR-0100). `workspace_id`, when given, must be its id:
        any other workspace is WorkspaceNotFound (404), whether it exists or not."""
        own = personal_workspace_id(self.actor.id)
        if workspace_id is not None and workspace_id != own:
            raise missing_workspace(workspace_id)
        workspace = ensure_personal_workspace(self.policy.documents, self.actor)
        self._workspaces[own] = workspace
        return workspace

    # ---- projects --------------------------------------------------------------------------------------

    def listed(self) -> list[tuple[Project, str]]:
        """The projects of the caller's listing (own and shared ids, ADR-0100) that exist and that the caller has a
        role on, with that role: one point read per listed project, never a scan of other tenants' projects (a stale
        or extra id is skipped: the listing is a hint, never a grant). Entries of projects that are definitely gone or
        foreign are pruned, safely (`prune_listing`)."""
        listing = read_listing_document(self.policy.documents, self.actor.id)
        found = []
        stale = []
        for project_id in listing.ids():
            try:
                project = self._project_entry(project_id, reuse=True)[0]
            except ProjectNotFound as exc:
                if is_definitely_missing(exc):
                    stale.append(project_id)  # deleted; a failed read is no reason to prune
                continue
            role = self.role_of(project)
            if role is not None:
                found.append((project, role))
            elif project.workspace != personal_workspace_id(self.actor.id) and self.actor.id not in project.roles:
                stale.append(project_id)  # foreign: not the caller's own, no share for them (revoked)
        if stale:
            prune_listing(self.policy.documents, listing, stale)  # conditional, after the grace (#183, ADR-0124)
        return found

    def role_of(self, project: Project, fresh: bool = False) -> str | None:
        """The caller's effective role on `project` (None: it may not see it); `fresh` reads the workspace again."""
        workspace = self._workspace(project.workspace, fresh) if project.workspace is not None else None
        return project_role(self.actor.id, workspace, project)

    def owner_of(self, project: Project) -> str | None:
        """The user who owns the project (the owner of its personal workspace), None outside a personal workspace."""
        workspace = self._workspace(project.workspace) if project.workspace is not None else None
        return workspace.owner if workspace is not None and workspace.is_personal_of(workspace.owner) else None

    def project_doc(self, project: Project, need: str = "viewer", fresh: bool = False) -> str:
        """Check the caller's role on a project read by the caller of this method; returns the role. `fresh` reads
        the workspace again also for viewer (a live stream's re-check, ADR-0096)."""
        role = self.role_of(project, fresh=fresh or need != "viewer")
        if role is None:
            raise missing_project(project.id)
        if not allows(role, need):
            raise too_low(f"project '{project.id}'", role, need)
        return role

    def project(self, project_id: str, need: str = "viewer") -> Project:
        """The project, if the caller has at least `need` on it (ProjectNotFound / Forbidden otherwise)."""
        return self.project_entry(project_id, need)[0]

    def project_entry(self, project_id: str, need: str = "viewer") -> tuple[Project, str]:
        """The project and its revision, if the caller has at least `need` on it (ProjectNotFound / Forbidden)."""
        project, revision = self._project_entry(project_id, reuse=need == "viewer")
        self.project_doc(project, need)
        return project, revision

    # ---- diagrams --------------------------------------------------------------------------------------

    def diagram(self, diagram_id: str, need: str = "viewer", fresh: bool = False) -> Project:
        """The diagram's project (as just read), if the caller has at least `need` on it. DiagramNotFound when the
        diagram does not exist, is in no project or in one the caller cannot see; Forbidden for too low a role.
        `fresh` reads the workspace again also for viewer."""
        reuse = need == "viewer" and not fresh
        project_id = self._located.get(diagram_id)
        project = None
        if project_id is not None:
            try:
                project = self._project_entry(project_id, reuse)[0]
            except ProjectNotFound:
                project = None
            if project is not None and project.member(diagram_id) is None:
                project = None  # moved meanwhile
        if project is None:
            found = self.policy.locate_entry(diagram_id)
            if found is not None:
                self._remember(*found)
            project = found[0] if found is not None else None
            self._located[diagram_id] = project.id if project is not None else None
        role = self.role_of(project, fresh=fresh or need != "viewer") if project is not None else None
        if project is None or role is None:
            raise missing_diagram(diagram_id)
        if not allows(role, need):
            raise too_low(f"project '{project.id}' (diagram '{diagram_id}')", role, need)
        return project
