"""ProjectService: projects, their sections and the placement of diagrams in them.

Spec: docs/architecture/specs/projects.md, ADR-0051..0054. Every project write is a read-modify-write
conditional on the revision it read, retried on conflicts. "Every diagram belongs to exactly one project"
spans several documents; the writes are ordered so that a crash leaves only orphans, duplicates or
dangling memberships, which `reconcile()` repairs (ADR-0052). Before a diagram is created its target project is
recorded (a `placement` document, ADR-0088), so the repair puts an orphan back where it was meant to go.

With a `Guard` every method checks the caller's role first (spec: personal-workspaces.md, ADR-0084, ADR-0101): new
projects go into the caller's personal workspace, only their owner shares them. The repairs
(`reconcile`, `ensure_default`) run in the system context: at start-up and from `arcviz-migrate`, never because a
caller read something (ADR-0088).
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .. import ports, projects
from ..live import EventSink
from ..model import Diagram, now_iso, slugify
from ..operations import OperationError
from ..ports import (
    MUTATION_ATTEMPTS,
    ConflictError,
    DiagramInfo,
    DiagramRepositoryPort,
    Expected,
    RepositoryPort,
    RevisionMismatch,
    StoreError,
    check_id,
    is_definitely_missing,
)
from ..project_model import Driver, Element, Relation, Vision, view_connections, view_elements
from ..projects import Membership, Project, ProjectError, Section
from ..workspaces import (
    DEFAULT_WORKSPACE_ID,
    PERSONAL_PROJECT_TITLE,
    SHARE_ROLES,
    Workspace,
    is_reserved_project_id,
    personal_project_id,
)
from .access import WORKSPACE, Guard, ProjectNotFound, authored_entries, missing_diagram
from .listing import OWN, SHARED, update_listing, update_quietly
from .locations import (
    Located,
    add_location,
    holders,
    listed_holders,
    locate_membership,
    read_holder,
    read_location,
    remove_location,
    repair_locations,
    settle_after_failure,
    settle_location,
    writer_of,
)
from .users import UserService

DEFAULT_PROJECT_ID = "default"
DEFAULT_PROJECT_TITLE = "Standardprojekt"
PLACEMENT = "placement"  # keyed document: the project a diagram is being created for (ADR-0088)
PLACEMENT_STALE = timedelta(hours=1)  # a record without its diagram is dropped only after this long
logger = logging.getLogger(__name__)


class RequirementLinks(Protocol):
    """What the project service needs from the requirement service (ADR-0188, ADR-0189): the ids that exist, to check the
    references of drivers, scenarios and decisions, and the cascade of a removed stakeholder or element."""

    def ids(self, project_id: str) -> set[str]: ...

    def removed(self, project_id: str, stakeholder: str | None = None, element: str | None = None) -> None: ...


# store -> users whose squatted default-project id was logged (once per store and process)
_squat_logged: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()


def unknown_user(ref: str) -> ProjectError:
    """One answer for every reference that does not identify exactly one known user (no user enumeration)."""
    return ProjectError(
        f"Cannot share with '{ref}': use the user id, or the e-mail address, of exactly one person who has signed in "
        "to Arcviz."
    )


class ProjectNotEmpty(StoreError):
    """A project that still has diagrams cannot be deleted (ADR-0054)."""


@dataclass
class ProjectSummary:
    id: str
    title: str
    description: str
    diagram_count: int
    updated_at: str
    workspace: str | None = None  # spec: personal-workspaces.md
    role: str | None = None  # the caller's role (None in the system context)
    owner: str | None = None  # the user id of the owner of the project's workspace (None in the system context)
    shared: bool = False  # the caller sees it because it was shared with them (not their own)


@dataclass
class ProjectDiagram:
    """A member diagram as listed in a project view: its `DiagramInfo` plus its section."""

    id: str
    title: str
    updated_at: str
    version_count: int
    section: str | None


@dataclass
class ProjectView:
    project: Project
    tree: dict[str, Any]
    diagrams: list[ProjectDiagram]
    role: str | None = None  # the caller's role (None in the system context)
    revision: str | None = None  # of `project`: the ETag a conditional delete expects (#175)


@dataclass
class ProjectShare:
    """A user the project is shared with, and the role (spec: personal-workspaces.md)."""

    id: str
    role: str


@dataclass
class ProjectAccess:
    project: str
    owner: str  # the owner of the project's workspace
    role: str  # the caller's
    shares: list[ProjectShare]  # every share for an owner, only the caller's own for anybody else
    added: str | None = None  # share(): the user id the reference resolved to (not shown: the API shows `added_ref`)
    added_ref: str | None = None  # share(): what the owner typed


@dataclass
class ReconcileReport:
    adopted: list[str] = field(default_factory=list)  # orphans put into their recorded (or the default) project
    quarantined: list[str] = field(default_factory=list)  # orphans whose recorded project is gone: left alone
    into: dict[str, str] = field(default_factory=dict)  # adopted diagram -> the project it went into
    dropped: list[str] = field(default_factory=list)  # memberships of diagrams that no longer exist
    deduplicated: list[str] = field(default_factory=list)  # diagrams that were in more than one project
    located: int = 0  # diagram locator entries written or dropped (#181, ADR-0120)
    skipped: list[str] = field(default_factory=list)  # diagrams a store failure kept from repair now (retried next run)

    @property
    def changed(self) -> bool:
        return bool(self.adopted or self.dropped or self.deduplicated)


class ProjectService:
    def __init__(
        self,
        projects_port: RepositoryPort,
        diagrams_port: DiagramRepositoryPort,
        guard: Guard | None = None,
        events: EventSink | None = None,
        users: UserService | None = None,
        default_workspace: str | None = None,
    ):
        self._projects = projects_port
        self._diagrams = diagrams_port
        self.guard = guard  # None: the system context
        self.events = events  # live updates (spec: collaboration.md); None: nobody listens
        self._users = users  # the directory: recipients of shares
        # where the default project lives: the admin's personal workspace; unknown (no admin): the reserved id, which
        # nobody sees until the migration moves it (spec: personal-workspaces.md, *Default project*)
        self._default_workspace = default_workspace or DEFAULT_WORKSPACE_ID
        self._requirements: RequirementLinks | None = None  # set by the service root (the requirement service is above)

    def attach_requirements(self, links: RequirementLinks) -> None:
        self._requirements = links

    def notify(self, project_id: str) -> None:
        """Tell the open views of the project that something of it changed (the requirement service's writes)."""
        self._publish({"type": "project", "project": project_id})

    # ---- queries ---------------------------------------------------------------------

    def get(self, project_id: str) -> Project:
        """The project (viewer); ProjectNotFound when it does not exist or the caller may not see it."""
        if self.guard is not None:
            return self.guard.project(project_id)
        return self._read(project_id)[0]

    def fresh(self, project_id: str) -> Project:
        """`get`, but never the copy this request cached (#166): for reads after a write whose outcome is unknown (a
        commit that landed and raised), as the compensation of a failed move or import needs them."""
        if self.guard is not None:
            self.guard.forget(project_id)
        return self.get(project_id)

    def holds(self, project_id: str, diagram_id: str) -> bool:
        """Whether the diagram is a member of the project now (editor; read again, never the request's cached copy):
        the re-check before a confirmed proposal's note goes into the project transcript (ADR-0127)."""
        project = self.guard.project(project_id, "editor") if self.guard is not None else self._read(project_id)[0]
        return project.member(diagram_id) is not None

    def require(self, project_id: str, need: str = "viewer") -> None:
        """ProjectNotFound when the caller may not see the project, Forbidden below `need`; nothing in the system
        context."""
        if self.guard is not None:
            self.guard.project(project_id, need)

    def visible_diagrams(self) -> set[str]:
        """Ids of the member diagrams of every project the caller may see (all of them in the system context)."""
        if self.guard is None:
            return {m.diagram for p, _ in self._projects.list_projects() for m in p.diagrams}
        return {m.diagram for p in self._visible_projects() for m in p.diagrams}

    def _visible_projects(self) -> list[Project]:
        """The projects of the caller's listing that the caller has a role on (`Guard.listed`, no scan)."""
        assert self.guard is not None
        return [project for project, _ in self.guard.listed()]

    def _publish(self, event: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(event)

    def watch(self, project_id: str) -> tuple[Project, str, str | None]:
        """A live stream's (re-)check (spec: collaboration.md, ADR-0096): the project, its revision and the caller's
        role, read fresh (also the workspace); ProjectNotFound when the caller may not see it (any more)."""
        project, revision = self._read(project_id)
        role = self.guard.project_doc(project, "viewer", fresh=True) if self.guard is not None else None
        return project, revision, role

    def _read(self, project_id: str) -> tuple[Project, str]:
        try:
            return self._projects.read_project(project_id)
        except ConflictError:
            raise
        except StoreError as exc:
            raise ProjectNotFound(str(exc)) from exc

    def list_projects(self) -> list[ProjectSummary]:
        """Every project the caller may see: their own and those shared with them (all of them in the system context,
        where the membership is repaired first: a caller's read never writes, ADR-0088)."""
        if self.guard is None:
            self.reconcile()
        listed = [p for p, _ in self._projects.list_projects()] if self.guard is None else self._visible_projects()
        summaries = []
        for p in listed:
            role = owner = None
            if self.guard is not None:
                role = self.guard.role_of(p)
                if role is None:
                    continue
                owner = self.guard.owner_of(p)
            shared = self.guard is not None and owner != self.guard.actor.id
            summaries.append(
                ProjectSummary(
                    p.id, p.title, p.description, len(p.diagrams), p.updated_at, p.workspace, role, owner, shared
                )
            )
        return sorted(summaries, key=lambda s: (s.title.casefold(), s.id))

    def view(self, project_id: str, infos: list[DiagramInfo] | None = None) -> ProjectView:
        project, revision = self.guard.project_entry(project_id) if self.guard is not None else self._read(project_id)
        if infos is None:  # the members only, never every diagram of the store (#181)
            infos = self._diagrams.diagram_infos([m.diagram for m in project.diagrams])
        known = {d.id: d for d in infos}
        members = [
            ProjectDiagram(info.id, info.title, info.updated_at, info.version_count, m.section)
            for m in project.diagrams
            if (info := known.get(m.diagram)) is not None
        ]
        role = self.guard.role_of(project) if self.guard is not None else None
        return ProjectView(project, project.tree(), members, role, revision)

    def _holder(self, diagram_id: str) -> Located | None:
        """The project holding the diagram and its membership, through the locator (#181, ADR-0120)."""
        return locate_membership(self._projects, self._projects, diagram_id, self._diagrams.exists)

    def holder(self, diagram_id: str) -> Located | None:
        """The project holding the diagram (as just read), its membership and the project's revision, or None (also
        when the caller may not see it)."""
        found = self._holder(diagram_id)
        if found is None or (self.guard is not None and self.guard.role_of(found.project) is None):
            return None
        return found

    def locate(self, diagram_id: str) -> tuple[str, str | None] | None:
        """(project id, section id) of the diagram's membership, or None (also when the caller may not see it)."""
        found = self.holder(diagram_id)
        return (found.project.id, found.member.section) if found is not None else None

    def load_members(self, project_id: str) -> tuple[Project, list[tuple[Membership, Diagram]]]:
        """The project and every member diagram that still exists, in membership order (a diagram deleted
        meanwhile is left out). Read-only: the project chat and the project review use it."""
        project = self.get(project_id)
        members: list[tuple[Membership, Diagram]] = []
        for member in project.diagrams:
            try:
                members.append((member, self._diagrams.load(member.diagram)))
            except StoreError:
                continue
        return project, members

    # ---- project transcript (spec: docs/architecture/specs/project-chat.md, ADR-0057) ----------------
    # Same names and signatures as DiagramService's transcript methods, so the agent's compaction works on both.

    def chat(self, project_id: str) -> list[dict[str, Any]]:
        self.require(project_id, "viewer")
        return self._projects.load_project_chat(project_id)

    def append_chat(self, project_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> bool:
        """Append transcript entries (editor); False when the project no longer exists (nothing is recreated) or, with
        `generation`, when the transcript has another one (cleared, or the project re-created; ADR-0127)."""
        if self.guard is not None:
            try:
                self.guard.project(project_id, "editor")
            except ProjectNotFound:
                return False
        author = self.guard.actor if self.guard is not None else None
        try:
            self._projects.append_project_chat(project_id, authored_entries(entries, author), generation)
        except ConflictError:
            if generation is None:
                raise
            return False
        except StoreError:
            try:
                self._projects.read_project(project_id)
            except StoreError:
                return False
            raise
        self._publish({"type": "chat", "project": project_id})
        return True

    def chat_generation(self, project_id: str) -> str:
        if self.guard is not None:
            try:
                self.guard.project(project_id)
            except ProjectNotFound:
                return ""  # like an unknown project
        return self._projects.project_chat_generation(project_id)

    def compact_chat(self, project_id: str, upto_seq: int, summary: dict[str, Any], generation: str) -> bool:
        self.require(project_id, "editor")
        compacted = self._projects.compact_project_chat(project_id, upto_seq, summary, generation)
        if compacted:
            self._publish({"type": "chat", "project": project_id})
        return compacted

    def clear_chat(self, project_id: str) -> None:
        """ProjectNotFound when the project does not exist. Clears the transcript of the project incarnation the access
        check approved (ADR-0127): the generation is read between two checks, and the clear is conditional on it."""
        for _ in range(MUTATION_ATTEMPTS):
            self.require(project_id, "editor")
            generation = self._projects.project_chat_generation(project_id)
            self.require(project_id, "editor")
            try:
                self._projects.clear_project_chat(project_id, generation)
            except ConflictError:
                continue  # cleared or re-created meanwhile: check again
            except StoreError as exc:
                raise ProjectNotFound(str(exc)) from exc
            self._publish({"type": "chat", "project": project_id})
            return
        raise ConflictError(f"The transcript of project '{project_id}' kept changing; try again.")

    # ---- project lifecycle -----------------------------------------------------------

    def create(
        self, title: str, project_id: str | None = None, description: str = "", workspace: str | None = None
    ) -> Project:
        """A new project in the caller's personal workspace (`workspace`, if given, must be it: WorkspaceNotFound
        otherwise; in the system context it names the workspace, default the default project's), owned by the caller.
        Its id is `<project_id or the title's slug>-<random suffix>` (ADR-0131): the requested id is only a prefix, so
        the answer is the same whether or not anybody holds it (#167). StoreError for an invalid id."""
        target = self._creatable_in(workspace)
        return self._create_free(
            check_id(project_id or slugify(title) or "project"), title, description, target, [], []
        )

    def _creatable_in(self, workspace: str | None) -> str:
        """The workspace a new project goes into: the caller's own (created if missing, ADR-0100)."""
        if self.guard is not None:
            return self.guard.own_workspace(workspace).id
        return workspace or self._default_workspace

    def create_with_sections(
        self,
        title: str,
        description: str,
        sections: list[Section],
        elements: list[Element] | None = None,
        relations: list[Relation] | None = None,
        workspace: str | None = None,
        relation_seq: int = 0,
        drivers: list[Driver] | None = None,
        driver_seq: int = 0,
        decision_support: dict[str, Any] | None = None,
        vision: Vision | None = None,
    ) -> Project:
        """A new project in the caller's personal workspace (as `create`) with the given section tree (and model
        elements, relations and architecture drivers) and a new id `<slug>-<random suffix>` (ADR-0131): imports."""
        target = self._creatable_in(workspace)
        base = check_id(slugify(title)[:60].strip("-") or "project")
        return self._create_free(
            base,
            title,
            description,
            target,
            sections,
            elements or [],
            relations or [],
            relation_seq,
            drivers=drivers or [],
            driver_seq=driver_seq,
            decision_support=decision_support,
            vision=vision,
        )

    def _create_free(
        self,
        base: str,
        title: str,
        description: str,
        workspace: str,
        sections: list[Section],
        elements: list[Element],
        relations: list[Relation] | None = None,
        relation_seq: int = 0,
        drivers: list[Driver] | None = None,
        driver_seq: int = 0,
        decision_support: dict[str, Any] | None = None,
        vision: Vision | None = None,
    ) -> Project:
        """Create the project under a new id `<base>-<random suffix>` (`ports.id_attempts`, ADR-0131): a taken candidate
        (practically never) moves on to a fresh suffix, so the answer never depends on other tenants' ids (#167). The
        ids of the default project and of every user's default project (`home_<hash>`) are reserved (only
        `ensure_default` / `ensure_own_default` create them)."""
        for candidate in ports.id_attempts(base, "project"):
            if candidate != DEFAULT_PROJECT_ID and not is_reserved_project_id(candidate):
                project = projects.new_project(title, candidate, description, workspace)
                project.sections = [s.model_copy() for s in sections]
                project.elements = [e.model_copy() for e in elements]
                project.relations = [r.model_copy(deep=True) for r in relations or []]
                project.relation_seq = relation_seq
                project.drivers = [d.model_copy(deep=True) for d in drivers or []]
                project.driver_seq = driver_seq
                project.vision = vision.model_copy(deep=True) if vision is not None else None
                for key, value in (decision_support or {}).items():  # stakeholders, scenarios, decisions (M29)
                    setattr(
                        project, key, [v.model_copy(deep=True) for v in value] if isinstance(value, list) else value
                    )
                errors = project.integrity_errors()
                if errors:
                    raise ProjectError("Invalid sections: " + "; ".join(errors))
                projects.check_document_size(project, None)  # a bundle import never creates an oversize project
                try:
                    self._projects.create_project(project)
                except ConflictError:
                    raise
                except StoreError as exc:
                    try:
                        self._projects.read_project(candidate)
                    except StoreError:
                        raise exc from None  # not a taken id (a write failure, an id too long): give up
                    continue  # taken (also by a concurrent create): a fresh suffix
                self._list_own(project)
                return project
        raise AssertionError("unreachable")  # pragma: no cover

    def _list_own(self, project: Project) -> None:
        """Add a new project to its owner's listing (best effort; the start-up repair adds what is missing)."""
        owner = self.guard.actor.id if self.guard is not None else self._owner_of_workspace(project.workspace)
        if owner is not None:
            update_quietly(self._projects, owner, add=[(OWN, project.id)])

    def _owner_of_workspace(self, workspace_id: str | None) -> str | None:
        """The owner of a personal workspace (None for any other or a missing one)."""
        if workspace_id is None:
            return None
        try:
            body, _ = self._projects.read_document(WORKSPACE, workspace_id)
            workspace = Workspace.model_validate(body)
        except (StoreError, ValueError):
            return None
        return workspace.owner if workspace.is_personal_of(workspace.owner) else None

    def ensure_own_default(self) -> Project:
        """The caller's own default project ("Standardprojekt", ADR-0100), created in their personal workspace if
        missing: create-if-absent under `personal_project_id(user)`, so replicas racing for it end with the same one.
        If that id is taken by somebody else's project (an M20 id the migration did not rename), `<id>-<random suffix>` is
        created instead and recorded on the workspace (`default_project`), logged once; never an error on every
        request."""
        if self.guard is None:
            raise ProjectError("A caller's default project needs a caller.")
        workspace = self.guard.own_workspace()
        project_id = workspace.default_project or personal_project_id(self.guard.actor.id)
        for _ in range(2):
            try:
                project = self._read(project_id)[0]
            except ProjectNotFound:
                created = projects.new_project(PERSONAL_PROJECT_TITLE, project_id, "", workspace.id)
                try:
                    self._projects.create_project(created)
                except ConflictError:
                    raise
                except StoreError as exc:
                    if "already exists" not in str(exc):
                        raise  # a real failure, with its cause
                    continue  # created concurrently (another replica): read it
                self._list_own(created)
                self._publish({"type": "project", "project": project_id})
                return created
            if project.workspace == workspace.id:
                return project
            return self._fallback_default(workspace, project_id)
        raise StoreError(f"The default project of '{self.guard.actor.id}' could not be read.")

    def _fallback_default(self, workspace: Workspace, squatted: str) -> Project:
        """The default project id is somebody else's: create `<id>-<random suffix>` and record it on the workspace; a
        concurrent fallback that recorded first wins (ours is removed again), but only with a project in the caller's
        own workspace: a recorded id that is gone or somebody else's (deleted, then taken) is overwritten."""
        assert self.guard is not None
        user = self.guard.actor.id
        logged = _squat_logged.setdefault(self._projects, set())
        if user not in logged:
            logged.add(user)
            logger.warning(
                "The default project id '%s' of '%s' is squatted by another project; using a variant", squatted, user
            )
        created = self._create_free(personal_project_id(user), PERSONAL_PROJECT_TITLE, "", workspace.id, [], [])
        for _ in range(MUTATION_ATTEMPTS):
            body, revision = self._projects.read_document(WORKSPACE, workspace.id)
            current = Workspace.model_validate(body)
            if current.default_project and current.default_project not in (created.id, squatted):
                recorded = self._own_project(current.default_project, workspace.id)
                if recorded is not None:
                    with suppress(StoreError):  # another replica recorded its variant first
                        self._projects.delete_project(created.id, self._read(created.id)[1])
                    return recorded
            updated = current.model_copy(update={"default_project": created.id})
            try:
                self._projects.commit_document(WORKSPACE, workspace.id, updated.model_dump(mode="json"), revision)
                return created
            except ConflictError:
                continue
        raise ConflictError(f"The workspace of '{user}' kept changing; try again.")

    def _own_project(self, project_id: str, workspace_id: str) -> Project | None:
        """The project if it exists in the given workspace, else None."""
        try:
            project = self._read(project_id)[0]
        except ProjectNotFound:
            return None
        return project if project.workspace == workspace_id else None

    def ensure_default(self) -> Project:
        """The default project, created in the default project's workspace if missing (a repair: system context, or
        for its owner through `check_target`)."""
        try:
            return self._read(DEFAULT_PROJECT_ID)[0]
        except StoreError:
            pass
        project = projects.new_project(DEFAULT_PROJECT_TITLE, DEFAULT_PROJECT_ID, "", self._default_workspace)
        try:
            self._projects.create_project(project)
        except StoreError:  # created concurrently
            return self._read(DEFAULT_PROJECT_ID)[0]
        self._list_own(project)
        return project

    def apply(self, project_id: str, op: str, args: dict[str, Any], internal: bool = False) -> dict[str, Any]:
        """One project operation as a conditional read-modify-write. Only `PUBLIC_OPERATIONS` unless
        `internal` (the membership operations are this service's business)."""
        return self.apply_all(project_id, [(op, args)], internal)[0]

    def apply_all(
        self, project_id: str, steps: list[tuple[str, dict[str, Any]]], internal: bool = False
    ) -> list[dict[str, Any]]:
        """Several project operations as one conditional write: all of them or (on any error) none. Only
        `PUBLIC_OPERATIONS` unless `internal`."""
        for op, _ in steps:
            if not internal and op in projects.ELEMENT_OPERATIONS:
                raise ProjectError(
                    f"'{op}' changes the shared model: use the model routes (/api/projects/{{pid}}/elements)."
                )
            if not internal and op in projects.RELATION_OPERATIONS:
                raise ProjectError(
                    f"'{op}' changes the shared model: use the model routes (/api/projects/{{pid}}/relations)."
                )
            if not internal and op in projects.INTERNAL_OPERATIONS:
                raise ProjectError(
                    f"'{op}' is internal: diagrams join a project when they are created, imported or moved."
                )
        self.require(project_id, "editor")
        self._check_requirement_refs(project_id, steps)
        results = self._update(project_id, lambda project: [projects.apply(project, op, args) for op, args in steps])
        assert results is not None
        self._cascade_to_requirements(project_id, results)
        return results

    def _check_requirement_refs(self, project_id: str, steps: list[tuple[str, dict[str, Any]]]) -> None:
        """ProjectError when a step names a requirement that does not exist (they live in their own document, so the
        project operations check the format only)."""
        wanted: list[str] = []
        for _, args in steps:
            if isinstance(args, dict) and isinstance(args.get("requirements"), list):
                wanted += [r for r in args["requirements"] if isinstance(r, str)]
        if not wanted or self._requirements is None:
            return
        known = self._requirements.ids(project_id)
        missing = [r for r in dict.fromkeys(wanted) if r.strip() not in known]
        if missing:
            listed = ", ".join(sorted(known)[:20]) or "none"
            raise ProjectError(
                f"Unknown requirement {', '.join(repr(m) for m in missing)} in project '{project_id}'. Requirements: {listed}."
            )

    def _cascade_to_requirements(self, project_id: str, results: list[dict[str, Any]]) -> None:
        """A removed stakeholder or element leaves the requirements: a second, ordered write; a failure leaves references
        that reads drop and `sync` removes (ADR-0188)."""
        if self._requirements is None:
            return
        for result in results:
            payload = result.get("result")
            if result.get("op") == "remove_stakeholder" and isinstance(payload, dict):
                self._cascade(project_id, stakeholder=payload.get("stakeholder", {}).get("id"))
            elif result.get("op") == "remove_element" and isinstance(payload, dict):
                self._cascade(project_id, element=payload.get("element"))

    def _cascade(self, project_id: str, stakeholder: str | None = None, element: str | None = None) -> None:
        assert self._requirements is not None
        try:
            self._requirements.removed(project_id, stakeholder=stakeholder, element=element)
        except Exception as exc:
            logger.warning("Requirement references of project '%s' not cleaned: %s", project_id, type(exc).__name__)

    def strip_requirement_refs(self, project_id: str, requirement_ids: set[str]) -> None:
        """Remove the ids from the `requirements` of every driver, scenario and decision (a removed requirement; editor)."""
        self.require(project_id, "editor")

        def strip(project: Project) -> None:
            carriers: list[Any] = [*project.drivers, *project.scenarios, *project.decisions]
            for item in carriers:
                item.requirements = [r for r in item.requirements if r not in requirement_ids]

        self._update(project_id, strip)

    def carry(
        self, project_id: str, source: Project, element_ids: set[str], relation_ids: set[str]
    ) -> projects.CarriedModel:
        """Bring the elements and relations a diagram of `source` refers to into the project, as one conditional write
        planned again on every conflict (`projects.carry_model`, #153, ADR-0161); needs editor. What is left out is
        reported, never refused: the size guard
        (`MAX_PROJECT_BYTES`, #212) is checked after every element and relation, so `_update`'s own check passes."""
        self.require(project_id, "editor")

        def change(project: Project) -> projects.CarriedModel:
            return projects.carry_model(
                project, source.elements, source.relations, element_ids, relation_ids, projects.MAX_PROJECT_BYTES
            )

        carried = self._update(project_id, change)
        assert carried is not None
        return carried

    def uncarry(self, project_id: str, carried: projects.CarriedModel) -> list[str]:
        """Take back copies `carry` added to the project, after the move or import it was for failed (#153 review). An
        orphan copy is harmless, a lost element is not: a copy is removed only while it is still exactly what this
        carry added (every field, so an edit made meanwhile is never lost), no member diagram links to it,
        and nothing else hangs on it (a relation, child or section this carry did not add). Relations first, then the
        elements, children before parents. Returns the ids left in the project. A link written while this runs is
        taken back by the link's own check (ADR-0128), since the element is gone then."""
        self.require(project_id, "editor")
        if self.guard is not None:
            self.guard.forget(project_id)  # who links to the copies now, not as this request read it
        _, members = self.load_members(project_id)
        diagrams = [d for _, d in members]
        linked_elements = {v.ref for d in diagrams for v in view_elements(d) if v.ref is not None}
        linked_relations = {c.ref for d in diagrams for c in view_connections(d) if c.ref is not None}
        linked_relations |= {r for d in diagrams for r in d.hidden_relations}
        left: list[str] = []

        def change(project: Project) -> None:
            left.clear()
            for _, relation_id in reversed(carried.copied_relations):
                relation = next((r for r in project.relations if r.id == relation_id), None)
                if relation is None:
                    continue
                if relation.model_dump() != carried.added_relations.get(relation_id) or relation_id in linked_relations:
                    left.append(relation_id)
                    continue
                projects.apply(project, "remove_relation", {"relation": relation_id})
            for _, element_id in reversed(carried.copied_elements):
                element = next((e for e in project.elements if e.id == element_id), None)
                if element is None:
                    continue
                held = (
                    element_id in linked_elements
                    or any(element_id in (r.source, r.target) for r in project.relations)
                    or any(e.parent == element_id for e in project.elements)
                    or any(s.element == element_id for s in project.sections)
                )
                if element.model_dump() != carried.added_elements.get(element_id) or held:
                    left.append(element_id)
                    continue
                projects.apply(project, "remove_element", {"element": element_id})

        self._update(project_id, change)
        return list(left)

    def delete(self, project_id: str, expected: Expected | None = None) -> None:
        """Delete an empty project (project owner, ADR-0085). With `expected` (HTTP `If-Match`, #175) one conditional
        attempt: RevisionMismatch when the project is at another revision or changes before the delete."""
        self.require(project_id, "owner")
        accepted = (expected,) if isinstance(expected, str) else expected
        for _ in range(MUTATION_ATTEMPTS if accepted is None else 1):
            project, revision = self._read(project_id)
            if accepted is not None and revision not in accepted:
                raise RevisionMismatch(
                    f"Project '{project_id}' has changed since revision {', '.join(accepted)}.", revision
                )
            if project.diagrams:
                raise ProjectNotEmpty(
                    f"Project '{project_id}' still contains {len(project.diagrams)} diagram(s); "
                    "move or delete them first."
                )
            try:
                self._projects.delete_project(project_id, revision)
            except ConflictError:
                if accepted is not None:
                    raise RevisionMismatch(
                        f"Project '{project_id}' was changed by another request while it was being deleted."
                    ) from None
                continue
            # the listings keep the id: a stale entry is skipped on read, and removing it here could hide a project
            # created again under the same id meanwhile (spec: personal-workspaces.md, *Listings*)
            if self.guard is not None:
                self.guard.forget(project_id)
            self._publish({"type": "project", "project": project_id})
            return
        raise ConflictError(f"Project '{project_id}' kept changing while it was being deleted; try again.")

    # ---- placement -------------------------------------------------------------------

    def check_target(
        self, project_id: str | None, section: str | None, incarnation: str | None = None
    ) -> tuple[str, str | None]:
        """Resolve a placement target before anything is written: StoreError when the project does not
        exist (or the caller may not see it), Forbidden below editor, ProjectError when the section does not exist;
        None is the caller's own default project (the default project in the system context), created if missing.
        With `incarnation` (`projects.incarnation_of`), ProjectNotFound unless the project is that incarnation: one
        deleted and created again under the same id is another project (ADR-0127)."""
        if project_id is None:
            project_id = self.ensure_own_default().id if self.guard is not None else self.ensure_default().id
        # editor: read again, never the request's cached copy (ADR-0121)
        project = self.guard.project(project_id, "editor") if self.guard is not None else self._read(project_id)[0]
        _check_incarnation(project, incarnation)
        section_id = None
        if section:
            found = project.find_section(section)
            if found is None:
                known = ", ".join(s.id for s in project.sections) or "none"
                raise ProjectError(f"Unknown section '{section}' in project '{project.id}'. Known sections: {known}.")
            section_id = found.id
        return project.id, section_id

    def intend(self, diagram_id: str, project_id: str) -> str | None:
        """Record the project a diagram is about to be created for (before the create, ADR-0088); returns the
        record's revision for `settle`, None when another create claimed the id (then it counts as taken)."""
        body = {"project": project_id, "at": now_iso()}
        try:
            return self._projects.create_document(PLACEMENT, check_id(diagram_id), body)
        except ConflictError:
            raise
        except StoreError:
            try:  # claimed by another create (in flight, or one that crashed): the id is taken
                self._projects.read_document(PLACEMENT, diagram_id)
            except StoreError:
                raise  # not a claimed id (a failed write, an invalid id)
            return None

    def settle(self, diagram_id: str, revision: str) -> None:
        """Drop the placement record once the diagram is placed (or its creation given up); best effort, the repair
        drops leftovers."""
        with suppress(StoreError):
            self._projects.delete_document(PLACEMENT, diagram_id, revision)

    def place(
        self,
        diagram_id: str,
        project_id: str | None = None,
        section: str | None = None,
        position: int | None = None,
        new: bool = False,
        source: Located | None = None,
        incarnation: str | None = None,
    ) -> tuple[str, str | None]:
        """Make the diagram a member of the target project (moving it within when it is one already), then remove it
        from the project that held it before (`source`, located here unless given; none for `new`: a just-created
        diagram or an orphan) and from every other project its locator entry lists that holds it (duplicates). The target is listed in the locator entry before the membership is added and the source
        dropped from it after its membership is gone (ADR-0120: the entry lists every holder, and a reader always finds
        a project holding the diagram). Returns (project id, section id). With `incarnation` the target must be that
        incarnation of the project, checked again inside the conditional membership write (ADR-0127)."""
        target, section_id = self.check_target(project_id, section, incarnation)
        if not new and source is None:
            source = self._holder(diagram_id)
        args = {"diagram": diagram_id, "section": section_id, "position": position}

        def place_in(project: Project) -> None:
            _check_incarnation(project, incarnation)
            writer.check()  # right before the membership write: a writer fenced meanwhile writes nothing (#189)
            op = "move_diagram" if project.member(diagram_id) is not None else "add_diagram"
            projects.apply(project, op, args)

        # every other holder: the source and any duplicate the entry lists (a crash), each with the membership seen; a
        # holder that cannot be read aborts the placement before anything is written (never taken as "not a holder")
        entry = read_location(self._projects, diagram_id)
        listed = [*(entry.projects if entry is not None else []), *([source.project.id] if source is not None else [])]
        others = [
            h
            for p in dict.fromkeys(listed)
            if p != target and (h := read_holder(self._projects, p, diagram_id)) is not None
        ]
        if not self._diagrams.exists(
            diagram_id
        ):  # never a membership for a diagram that is gone (a failed read raises)
            raise missing_diagram(diagram_id)
        writer = writer_of(self._projects)
        token = add_location(self._projects, diagram_id, target)  # listed (pending) before the membership is written
        try:
            self._update(target, place_in, projects.MAX_MEMBERSHIP_BYTES)  # only a membership: bounded overshoot
        except BaseException:
            with suppress(Exception):  # cleared once a re-read shows whether the write happened (else retried)
                settle_after_failure(self._projects, self._projects, diagram_id, target, token)
            raise
        if not self._diagrams.exists(diagram_id):  # deleted meanwhile: its delete may not have seen this membership
            written = read_holder(self._projects, target, diagram_id)
            if written is not None:
                self._remove_member(target, diagram_id, written.member.added_at)
            settle_location(self._projects, diagram_id, target, token)
            raise missing_diagram(diagram_id)
        settle_location(self._projects, diagram_id, target, token, relist=True)  # relist: fenced while paused (#189)
        for other in others:
            self._remove_member(other.project.id, diagram_id, other.member.added_at)
            remove_location(self._projects, self._projects, diagram_id, other.project.id)
        return target, section_id

    def vacate(self, diagram_id: str) -> None:
        """Before a diagram is created under `diagram_id` (no diagram has it, its placement record is claimed): remove
        the id from every project its locator entry lists (none may be pending: a placement in flight, or a crashed one,
        makes it fail closed). Such a membership belongs to a deleted diagram whose cleanup
        failed; left in place it would show the new diagram in that project. The entry lists every project holding the
        id (ADR-0120), so no scan is needed. Fails closed: a project that cannot be read, or whose membership is not
        removed, raises (StoreError/ConflictError) and the create is given up (the caller then takes the next free id).
        The placement record the caller holds keeps concurrent creates of the id away, so a removal is final."""
        entry = read_location(self._projects, diagram_id)
        if entry is not None and any(entry.pending.values()):  # a placement of this id is in flight (or crashed)
            raise ConflictError(f"A placement of diagram id '{diagram_id}' is in flight; the id is not used now.")
        for project_id in entry.projects if entry is not None else []:
            self._remove_member(project_id, diagram_id)  # raises unless removed (conditional write) or definitely gone
            remove_location(self._projects, self._projects, diagram_id, project_id, strict=True)

    def memberships(self, diagram_id: str, holder: Project | None = None) -> list[tuple[str, str]]:
        """(project id, added_at) of every membership of a diagram about to be deleted, read before the delete: `holder`
        (the project the caller's check just read, reused) plus the other projects its locator entry lists; in the
        system context a scan when there is no entry. `unplace` removes exactly these, so a membership of a diagram
        created again under the same id after the delete is never touched. A project that cannot be read raises (the
        delete is given up before anything is deleted)."""
        entry = read_location(self._projects, diagram_id)
        if entry is None and self.guard is None:
            return [(h.project.id, h.member.added_at) for h in holders(self._projects, diagram_id)]
        found: list[tuple[str, str]] = []
        own = holder.member(diagram_id) if holder is not None else None
        if holder is not None and own is not None:
            found.append((holder.id, own.added_at))
        rest = (
            None
            if entry is None
            else replace(entry, projects=[p for p in entry.projects if holder is None or p != holder.id])
        )
        found += [(h.project.id, h.member.added_at) for h in listed_holders(self._projects, diagram_id, rest)]
        return found

    def unplace(self, diagram_id: str, observed: list[tuple[str, str]] | None = None) -> None:
        """After a delete: remove the memberships `memberships` observed before it (each only if it still has the
        `added_at` seen; without `observed` those found now), then those projects from the locator entry. The deletion
        checked the caller's owner role on the diagram's project; other observed memberships are crash leftovers of
        the same, now deleted, diagram."""
        for project_id, added_at in observed if observed is not None else self.memberships(diagram_id):
            self._remove_member(project_id, diagram_id, added_at)
            remove_location(self._projects, self._projects, diagram_id, project_id)

    def withdraw(self, diagram_id: str, project_id: str, added_at: str | None = None) -> None:
        """Remove the membership of a diagram whose creation was given up after it was placed (`DiagramService`
        removed the diagram again); its creator checked editor on the project before creating it. Without this, a
        later create of the same id by anybody would appear in this project until the repair. Also the source
        membership of a move that finished although `place` failed (`added_at`: only that membership, #153)."""
        self._remove_member(project_id, diagram_id, added_at)
        remove_location(self._projects, self._projects, diagram_id, project_id)

    def _remove_member(self, project_id: str, diagram_id: str, added_at: str | None = None) -> None:
        """Remove the membership; with `added_at` only the membership that was observed (a newer placement
        into the same project, made after the caller's read, stays)."""

        def remove(project: Project) -> None:
            member = project.member(diagram_id)
            if member is not None and (added_at is None or member.added_at == added_at):
                projects.apply(project, "remove_diagram", {"diagram": diagram_id})

        try:
            self._update(project_id, remove)
        except ProjectNotFound as exc:
            if not is_definitely_missing(exc):
                raise  # a failed read is never "nothing to remove"
            # a project deleted meanwhile has nothing to remove

    # ---- repair ------------------------------------------------------------------------

    def reconcile(self) -> ReconcileReport:
        """Repair membership across documents (ADR-0052, ADR-0088): duplicates keep the newest membership, dangling
        memberships go when the diagram is confirmed gone, orphans (confirmed by a fresh read) go into the project
        recorded before their creation; an orphan without a record is data from before projects and goes into the
        default project; one whose recorded project is gone stays in no project (quarantined: nobody sees it until an
        admin places it, `arcviz-migrate --projects STORE --adopt DIAGRAM=PROJECT`). Idempotent; every repair is a
        conditional write. System context only."""
        if self.guard is not None:
            raise ProjectError("The membership repair runs in the system context only.")
        report = ReconcileReport()
        existing = {d.id for d in self._diagrams.list_diagrams()}
        stored = [p for p, _ in self._projects.list_projects()]

        holders: dict[str, list[tuple[str, str]]] = {}  # diagram id -> [(added_at, project id)]
        for project in stored:
            for member in project.diagrams:
                holders.setdefault(member.diagram, []).append((member.added_at, project.id))
        for diagram_id, found in sorted(holders.items()):
            with self._item(report, diagram_id):
                if len(found) > 1:
                    # the newest added_at is the intended placement; on a tie the lowest project id
                    keep = max(sorted(found, key=lambda h: h[1]), key=lambda h: h[0])[1]
                    for observed, project_id in found:
                        if project_id != keep:
                            self._remove_member(project_id, diagram_id, observed)
                    report.deduplicated.append(diagram_id)
                if diagram_id not in existing and not self._diagrams.exists(diagram_id):
                    for _, project_id in found:
                        self._remove_member(project_id, diagram_id)
                    report.dropped.append(diagram_id)

        orphans = sorted(existing - set(holders))
        if orphans:  # confirm against a fresh read: a diagram being moved is briefly in neither project
            placed = {m.diagram for p, _ in self._projects.list_projects() for m in p.diagrams}
            confirmed = []
            for diagram_id in orphans:
                with self._item(report, diagram_id):
                    if diagram_id not in placed and self._diagrams.exists(diagram_id):
                        confirmed.append(diagram_id)
            orphans = confirmed
        records = {key: (body, revision) for key, body, revision in self._projects.list_documents(PLACEMENT)}
        targets: dict[str, list[str]] = {}
        for diagram_id in orphans:
            record = records.get(diagram_id)
            target = str(record[0].get("project", "")) if record is not None else DEFAULT_PROJECT_ID
            with self._item(report, diagram_id):
                if record is not None and not self._exists(target):
                    report.quarantined.append(diagram_id)
                    continue
                targets.setdefault(target, []).append(diagram_id)
        for target, adopted in sorted(targets.items()):
            with self._item(report, *adopted):
                if target == DEFAULT_PROJECT_ID:
                    self.ensure_default()
                try:
                    self._adopt(report, target, adopted)
                except ProjectError:
                    if len(adopted) == 1:
                        raise
                    # together they do not fit (#212): one by one, so those that fit are placed and only the rest waits
                    for diagram_id in adopted:
                        with self._item(report, diagram_id):
                            self._adopt(report, target, [diagram_id])
        report.adopted.sort()
        with self._item(report):
            self._drop_settled_records(records, set(report.quarantined))
        report.located, skipped = repair_locations(self._projects, self._projects, self._diagrams.exists)
        report.skipped.extend(d for d in skipped if d not in report.skipped)
        return report

    def _adopt(self, report: ReconcileReport, target: str, adopted: list[str]) -> None:
        """One membership write of the repair: `adopted` orphans into `target`, listed in the locator before (ADR-0120);
        ProjectError when the membership would grow the document past MAX_MEMBERSHIP_BYTES (#212)."""
        tokens: dict[str, str] = {}
        try:
            for diagram_id in adopted:  # listed before the membership
                tokens[diagram_id] = add_location(self._projects, diagram_id, target)
        except BaseException:
            for diagram_id, token in tokens.items():  # nothing was written for them: the heartbeat settles
                writer_of(self._projects).abandon(token, diagram_id, target)
            raise

        def adopt(project: Project) -> None:
            writer_of(self._projects).check()  # right before the membership write (#189)
            for diagram_id in adopted:
                if project.member(diagram_id) is None:
                    projects.apply(project, "add_diagram", {"diagram": diagram_id})

        try:
            self._update(target, adopt, projects.MAX_MEMBERSHIP_BYTES)
        except BaseException:
            for diagram_id, token in tokens.items():  # cleared once a re-read shows whether it was written
                with suppress(Exception):
                    settle_after_failure(self._projects, self._projects, diagram_id, target, token)
            raise
        for diagram_id, token in tokens.items():
            settle_location(self._projects, diagram_id, target, token, relist=True)
        report.adopted.extend(adopted)
        report.into.update(dict.fromkeys(adopted, target))

    @contextmanager
    def _item(self, report: ReconcileReport, *diagram_ids: str) -> Iterator[None]:
        """One item of the repair, best effort: a store failure (a throttle, a timeout) or a refused write (a project
        over every size budget, #212) is logged and the item skipped (reported in `skipped`; its placement record
        stays); the repair goes on with the others and the next run retries it."""
        try:
            yield
        except (StoreError, ProjectError):
            logger.warning(
                "The membership repair skipped %s for now", ", ".join(diagram_ids) or "a step", exc_info=True
            )
            report.skipped.extend(d for d in diagram_ids if d not in report.skipped)

    def _exists(self, project_id: str) -> bool:
        try:
            self._read(project_id)
            return True
        except ProjectNotFound:
            return False

    def _drop_settled_records(self, records: dict[str, tuple[dict[str, Any], str]], keep: set[str]) -> None:
        """Placement records whose diagram is placed now, or that are old and have no diagram (a create that never
        happened); a record younger than PLACEMENT_STALE may belong to a create in flight."""
        placed = {m.diagram for p, _ in self._projects.list_projects() for m in p.diagrams}
        cutoff = (datetime.now(UTC) - PLACEMENT_STALE).isoformat(timespec="seconds")
        for diagram_id, (body, revision) in records.items():
            if diagram_id in keep:
                continue
            if diagram_id in placed or (str(body.get("at", "")) < cutoff and not self._diagrams.exists(diagram_id)):
                self.settle(diagram_id, revision)

    def adopt(self, diagram_id: str, project_id: str) -> None:
        """Place an orphan (a quarantined diagram) into a project: an admin's decision, system context only."""
        if self.guard is not None:
            raise ProjectError("Orphans are placed by an admin (arcviz-migrate), not through the API.")
        if self.locate(diagram_id) is not None:
            raise ProjectError(f"Diagram '{diagram_id}' is already in a project; move it instead.")
        if not self._diagrams.exists(diagram_id):
            raise ProjectNotFound(f"Diagram '{diagram_id}' not found.")
        self.place(diagram_id, project_id, new=True)
        with suppress(StoreError):
            _, revision = self._projects.read_document(PLACEMENT, diagram_id)
            self.settle(diagram_id, revision)

    # ---- sharing (spec: personal-workspaces.md, ADR-0101) -------------------------------------------

    def _owner_project(self, project_id: str) -> tuple[Project, str]:
        """The project and its owner, for a caller who sees it (ProjectNotFound otherwise)."""
        if self.guard is None:
            raise ProjectError("Sharing is done by a caller only.")
        project = self.guard.project(project_id)
        owner = self.guard.owner_of(project)
        assert owner is not None  # a visible project is in a personal workspace
        return project, owner

    def access(self, project_id: str) -> ProjectAccess:
        """The owner, the caller's role and the shares (viewer): all shares for an owner, the caller's own for anybody
        else (a recipient learns nothing about the other recipients)."""
        project, owner = self._owner_project(project_id)
        assert self.guard is not None
        role = self.guard.role_of(project) or "viewer"
        shares = [ProjectShare(user, r) for user, r in sorted(project.roles.items()) if user != owner]
        if role != "owner":
            shares = [s for s in shares if s.id == self.guard.actor.id]
        return ProjectAccess(project.id, owner, role, shares)

    def share(self, project_id: str, user: str, role: str) -> ProjectAccess:
        """Share the project with a known user (exact id, or a trusted and unambiguous e-mail, ADR-0091) as viewer or
        editor, or change their share (project owners only; ProjectError for anything else)."""
        project, owner = self._owner_project(project_id)
        assert self.guard is not None
        self.guard.project_doc(project, "owner")
        if role not in SHARE_ROLES:
            raise ProjectError(f"Unknown role '{role}' for a share. Roles: {', '.join(SHARE_ROLES)}.")
        if self._users is None:
            raise ProjectError("Sharing needs the user directory.")
        try:
            record = self._users.find(user)
        except OperationError:
            record = None  # ambiguous: answered like an unknown user (no enumeration, ADR-0101)
        if record is None:
            raise unknown_user(user)
        if record.id == owner:
            raise ProjectError(f"'{user}' owns the project.")
        recipient = record.id
        update_listing(self._projects, recipient, add=[(SHARED, project_id)])  # before the share: a hint, re-validated

        def change(current: Project) -> None:
            current.roles = {**current.roles, recipient: role}

        self._update_as_owner(project_id, change)
        access = self.access(project_id)
        access.added, access.added_ref = recipient, user
        return access

    def revoke(self, project_id: str, user_id: str) -> ProjectAccess:
        """Remove a user's share (project owners only); a missing share changes nothing."""
        project, owner = self._owner_project(project_id)
        assert self.guard is not None
        self.guard.project_doc(project, "owner")
        if user_id == owner:
            raise ProjectError(f"'{user_id}' owns the project; owners cannot be removed.")

        def change(current: Project) -> None:
            current.roles = {u: r for u, r in current.roles.items() if u != user_id}

        self._update_as_owner(project_id, change)  # the listing keeps the id (a re-share meanwhile stays listed)
        return self.access(project_id)

    def _update_as_owner(self, project_id: str, change: Callable[[Project], Any]) -> None:
        """A share change, re-checked on the document it writes: the caller must still own the project."""

        def checked(current: Project) -> None:
            assert self.guard is not None
            self.guard.project_doc(current, "owner")
            change(current)

        self._update(project_id, checked)

    # ---- read-modify-write -----------------------------------------------------------

    def _update(
        self, project_id: str, change: Callable[[Project], Any], size_limit: int = projects.MAX_PROJECT_BYTES
    ) -> Any:
        """Run `change` on a fresh copy and write it conditionally; again on every conflict.
        StoreError if the project does not exist, ConflictError after MUTATION_ATTEMPTS; ProjectError when the write would
        grow the document past `size_limit` (#212; membership writes pass MAX_MEMBERSHIP_BYTES)."""
        for _ in range(MUTATION_ATTEMPTS):
            project, revision = self._read(project_id)
            before = project.model_copy(deep=True)
            result = change(project)
            if project == before:
                return result  # nothing to write
            projects.check_document_size(project, before, size_limit)  # never grows a document past the budget (#212)
            try:
                self._projects.commit_project(project, revision)
            except ConflictError:
                continue
            if self.guard is not None:
                self.guard.forget(project_id)  # the request's later reads see this write (#166)
            self._publish({"type": "project", "project": project_id})
            return result
        raise ConflictError(f"Project '{project_id}' was changed by other requests meanwhile; try again.")


def _check_incarnation(project: Project, incarnation: str | None) -> None:
    """ProjectNotFound unless `project` is the incarnation (`projects.incarnation_of`) a caller started from
    (ADR-0127)."""
    if incarnation is not None and projects.incarnation_of(project) != incarnation:
        raise ProjectNotFound(f"Project '{project.id}' not found (it was deleted and created again).")
