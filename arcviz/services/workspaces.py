"""WorkspaceService and the migration to personal workspaces (spec: docs/architecture/specs/personal-workspaces.md,
ADR-0100, ADR-0102).

Every user has exactly one personal workspace (`access.ensure_personal_workspace`); the service answers the caller's
own. `migrate_to_personal_workspaces` moves v0.1.8 and M20 data into personal workspaces, turning every effective M20
role into an explicit share (system context, idempotent, one conditional write per project).
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..operations import OperationError
from ..ports import MUTATION_ATTEMPTS, ConflictError, RepositoryPort, StoreError, is_missing
from ..projects import Membership, Project
from ..workspaces import (
    DEFAULT_WORKSPACE_ID,
    SHARE_ROLES,
    Identity,
    Workspace,
    is_reserved_project_id,
    legacy_role,
    personal_workspace_id,
    user_key,
)
from .access import WORKSPACE, Guard, ensure_personal_workspace
from .listing import LISTING, OWN, SHARED, parse_listing, prune_listing, read_listing_document, update_listing
from .projects import DEFAULT_PROJECT_ID, PLACEMENT, ProjectService, ReconcileReport
from .users import Person, UserService

logger = logging.getLogger(__name__)
RENAME = "rename"  # keyed documents: the intent of a rename of a project with a reserved id (crash-safe)


class WorkspaceError(OperationError):
    """Invalid input for a workspace operation of the operator (HTTP 400, CLI error); nothing was written."""


@dataclass
class WorkspaceSummary:
    id: str
    title: str
    owner: Person
    project_count: int  # the caller's own projects
    shared_count: int  # projects shared with the caller
    created_at: str


class WorkspaceService:
    """The caller's personal workspace (a caller is required: there is nothing to list in the system context)."""

    def __init__(self, repository: RepositoryPort, users: UserService, guard: Guard | None = None):
        self._repo = repository
        self._users = users
        self._guard = guard

    def mine(self) -> WorkspaceSummary:
        """The caller's personal workspace (created if missing) with the counts of own and shared projects."""
        if self._guard is None:
            raise WorkspaceError("The personal workspace is answered for a caller only.")
        workspace = self._guard.own_workspace()
        listed = self._guard.listed()  # the caller's listing with role checks, no scan
        own = sum(1 for _, role in listed if role == "owner")
        shared = len(listed) - own
        records = self._users.records([workspace.owner])
        return WorkspaceSummary(
            workspace.id, workspace.title, Person.of(workspace.owner, records), own, shared, workspace.created_at
        )


# ---- migration (ADR-0102) -------------------------------------------------------------------------------


@dataclass
class PersonalMigration:
    """What `migrate_to_personal_workspaces` did."""

    created: list[str] = field(default_factory=list)  # users whose personal workspace was created
    moved: dict[str, str] = field(default_factory=dict)  # project -> the user into whose workspace it moved
    # projects that moved to the member who owned them explicitly (their creator), not to the workspace's owner:
    # project -> (new owner, the former workspace owner, now an editor)
    reowned: dict[str, tuple[str, str]] = field(default_factory=dict)
    renamed: dict[str, str] = field(default_factory=dict)  # M20 projects with a reserved id: old id -> new id
    failed: dict[str, str] = field(default_factory=dict)  # projects that could not be moved (retried next run)
    shares: int = 0  # shares written by the moves
    indexed: int = 0  # listing entries the repair added (own or shared projects missing from a user's listing)
    pruned: int = 0  # stale listing entries the repair removed (gone or foreign projects, #183)
    waiting_for_admin: bool = False  # data without a workspace (or in a missing `default`) and no admin configured
    hidden: int = 0  # projects waiting for the admin (nobody sees them)
    stranded: dict[str, list[str]] = field(default_factory=dict)  # missing workspace -> projects naming it
    repair_error: str | None = None  # the membership repair failed (best effort; the moves still ran)
    repair: ReconcileReport = field(default_factory=ReconcileReport)  # the membership repair

    @property
    def changed(self) -> bool:
        return bool(self.created or self.moved or self.renamed or self.indexed or self.pruned or self.repair.changed)


def _workspaces(repository: RepositoryPort) -> dict[str, Workspace]:
    out: dict[str, Workspace] = {}
    for key, body, _ in repository.list_documents(WORKSPACE):
        try:
            out[key] = Workspace.model_validate(body)
        except ValidationError:
            logger.warning("Ignoring the malformed workspace document '%s'", key)
    return out


def _is_personal(workspace_id: str | None, known: dict[str, Workspace]) -> bool:
    found = known.get(workspace_id or "")
    return found is not None and found.is_personal_of(found.owner)


@dataclass
class _Plan:
    owner: str  # the user into whose personal workspace the project moves
    shares: dict[str, str]
    former: str | None = None  # the M20 workspace owner, when the project goes to an explicit owner instead


def _plan(project: Project, known: dict[str, Workspace], admin: Identity | None) -> _Plan | None:
    """Where a project that is not in a personal workspace yet goes, and its shares (ADR-0102); None when it cannot
    move now (waiting for an admin, or stranded: decided by the caller). A project has exactly one owner: the M20
    workspace owner keeps it when it has no explicit owner among the members, or when the workspace owner is one of
    its explicit owners (the creator) themselves; otherwise it goes to its explicit owner among the members (several:
    the first by user id, projects do not record their creator) and the workspace owner becomes an editor. The
    reserved project `default` always stays with the owner of the M20 workspace `default`. Every other explicit owner
    becomes an editor, everybody else keeps their M20 effective role; roles of non-members (inert in M20) are
    dropped."""
    source = known.get(project.workspace or "")
    if source is not None:  # an M20 workspace: `default` or a team
        roles: dict[str, str] = {}
        for user in dict.fromkeys([*(m.user for m in source.members), *project.roles]):
            role = legacy_role(user, source, project) if user != source.owner else None
            if role is not None:
                roles[user] = role
        explicit = sorted(u for u, role in roles.items() if role == "owner")
        keeps = (
            not explicit
            or project.roles.get(source.owner) == "owner"
            or (project.id == DEFAULT_PROJECT_ID and project.workspace == DEFAULT_WORKSPACE_ID)
        )
        owner = source.owner if keeps else explicit[0]
        shares = {u: (role if role in SHARE_ROLES else "editor") for u, role in roles.items() if u != owner}
        if keeps:
            return _Plan(owner, shares)
        shares[source.owner] = "editor"
        return _Plan(owner, shares, former=source.owner)
    if project.workspace in (None, DEFAULT_WORKSPACE_ID) and admin is not None:  # v0.1.8 data: no roles
        return _Plan(admin.id, {u: r for u, r in project.roles.items() if u != admin.id and r in SHARE_ROLES})
    return None


def _move(
    repository: RepositoryPort, project: Project, revision: str, admin: Identity | None, report: PersonalMigration
) -> None:
    """One project into the personal workspace of its new owner: one conditional write (workspace and shares together);
    a conflict re-reads the project and the workspaces and plans again. The listings of the owner and the recipients
    are written first (hints, re-validated on read)."""
    for _ in range(MUTATION_ATTEMPTS):
        known = _workspaces(repository)
        if _is_personal(project.workspace, known):
            return  # migrated meanwhile
        plan = _plan(project, known, admin)
        if plan is None:
            return
        owner, shares = plan.owner, plan.shares
        target = ensure_personal_workspace(repository, Identity(owner, owner))
        if target.id not in known:
            report.created.append(owner)
        update_listing(repository, owner, add=[(OWN, project.id)])
        for user in shares:
            update_listing(repository, user, add=[(SHARED, project.id)])
        moved = project.model_copy(update={"workspace": target.id, "roles": shares})
        try:
            repository.commit_project(moved, revision)
        except ConflictError:
            try:
                project, revision = repository.read_project(project.id)
            except StoreError:
                return  # deleted meanwhile
            continue
        report.moved[project.id] = owner
        report.shares += len(shares)
        if plan.former is not None:
            report.reowned[project.id] = (owner, plan.former)
        return
    raise ConflictError(f"Project '{project.id}' kept changing during the migration; the next run moves it.")


def _free_id(repository: RepositoryPort, base: str, n: int) -> str:
    """The first `<base>-<n>`, `<base>-<n+1>`, … that no project has (a hint: the create decides)."""
    while True:
        candidate = f"{base}-{n}"
        try:
            repository.read_project(candidate)
        except StoreError as exc:
            if is_missing(exc):
                return candidate
            raise
        n += 1


def _rename_intent(repository: RepositoryPort, project: Project) -> tuple[dict[str, Any], str]:
    """The recorded rename of `project` (kind `rename`, key the original id), recorded first if there is none: the
    target id (`to`) and, once the memberships leave the original, those memberships (`diagrams`)."""
    for _ in range(MUTATION_ATTEMPTS):
        try:
            return repository.read_document(RENAME, project.id)
        except StoreError as exc:
            if not is_missing(exc):
                raise
        intent = {"from": project.id, "to": _free_id(repository, project.id, 2), "diagrams": None}
        try:
            repository.create_document(RENAME, project.id, intent)
        except StoreError as exc:
            if "already exists" not in str(exc):
                raise
    raise ConflictError(f"The rename of project '{project.id}' kept changing; the next run resumes it.")


def _claim_copy(repository: RepositoryPort, project: Project, target: str) -> Project | None:
    """The copy of `project` under `target`, created without memberships if missing; None when `target` is somebody
    else's project (taken between the choice and the create). The copy is recognised by the original's workspace and
    creation time (only the migration writes projects into M20 workspaces, and it never moves a rename target
    before its rename is done)."""
    for _ in range(MUTATION_ATTEMPTS):
        try:
            existing = repository.read_project(target)[0]
        except StoreError as exc:
            if not is_missing(exc):
                raise
            try:
                repository.create_project(project.model_copy(update={"id": target, "diagrams": []}))
            except StoreError as create_exc:
                if "already exists" not in str(create_exc):
                    raise
            continue
        same = existing.workspace == project.workspace and existing.created_at == project.created_at
        return existing if same else None
    raise ConflictError(f"The copy '{target}' of project '{project.id}' kept changing; the next run resumes it.")


def _rename(repository: RepositoryPort, project: Project, revision: str) -> tuple[Project, str]:
    """An M20 project whose id has a reserved form (`home_`/`user_` + 32 hex digits, possible before M21) would squat a
    user's default project: it moves to the first free `<id>-2`, `<id>-3`, … with its transcript, its placement
    records and its memberships, and the original is deleted. Crash-safe and idempotent: the target is recorded first
    (a keyed document of kind `rename`), so a restart resumes the same target, and the memberships are recorded there,
    taken from the original and only then given to the copy, so two projects never hold the same diagram."""
    intent, record = _rename_intent(repository, project)
    while (copy := _claim_copy(repository, project, intent["to"])) is None:  # the target was taken: the next free id
        updated = {**intent, "to": _free_id(repository, project.id, 2)}
        record = repository.commit_document(RENAME, project.id, updated, record)
        intent = updated
    target = copy.id
    if not repository.load_project_chat(target):
        entries = [{k: v for k, v in e.items() if k != "seq"} for e in repository.load_project_chat(project.id)]
        if entries:
            repository.append_project_chat(target, entries)
    for key, body, placement in repository.list_documents(PLACEMENT):
        if body.get("project") == project.id:
            with suppress(StoreError):
                repository.commit_document(PLACEMENT, key, {**body, "project": target}, placement)
    for _ in range(MUTATION_ATTEMPTS):  # the memberships: recorded, taken from the original
        original, revision = repository.read_project(project.id)
        if not original.diagrams:
            break
        intent = {**intent, "diagrams": [m.model_dump(mode="json") for m in original.diagrams]}
        record = repository.commit_document(RENAME, project.id, intent, record)
        try:
            repository.commit_project(original.model_copy(update={"diagrams": []}), revision)
            break
        except ConflictError:
            continue
    else:
        raise ConflictError(f"Project '{project.id}' kept changing during its rename; the next run resumes it.")
    _finish_rename(repository, intent, record)
    return repository.read_project(target)


def _finish_rename(repository: RepositoryPort, intent: dict[str, Any], record: str) -> None:
    """Give the recorded memberships to the copy (unless it has some), delete the original (empty by now), then the
    record."""
    try:
        copy, copy_revision = repository.read_project(intent["to"])
    except StoreError as exc:
        if not is_missing(exc):
            raise
        copy = None  # never created (the original was deleted by somebody else before): nothing to give
    if copy is not None and not copy.diagrams and intent.get("diagrams"):
        members = [Membership.model_validate(m) for m in intent["diagrams"]]
        repository.commit_project(copy.model_copy(update={"diagrams": members}), copy_revision)
    try:
        original, revision = repository.read_project(intent["from"])
    except StoreError as exc:
        if not is_missing(exc):
            raise
    else:
        if original.diagrams:
            raise ConflictError(f"Project '{original.id}' got diagrams during its rename; the next run resumes it.")
        repository.delete_project(original.id, revision)
    repository.delete_document(RENAME, intent["from"], record)


def _resume_renames(repository: RepositoryPort) -> set[str]:
    """Finish the recorded renames whose original is gone already (a crash after its delete); the targets of every
    other recorded rename, which wait for their original (skipped until it is renamed)."""
    waiting = set()
    for key, intent, record in repository.list_documents(RENAME):
        try:
            repository.read_project(key)
        except StoreError as exc:
            if not is_missing(exc):
                raise
            with suppress(StoreError):
                _finish_rename(repository, intent, record)
            continue
        if isinstance(intent.get("to"), str):
            waiting.add(intent["to"])
    return waiting


def _move_all(repository: RepositoryPort, admin: Identity | None, report: PersonalMigration) -> None:
    """Every project that is not in a personal workspace yet; a project that fails is logged and reported, the others
    go on (one bad document never keeps other tenants' projects hidden), and the next run retries it."""
    known = _workspaces(repository)
    try:
        waiting = _resume_renames(repository)
    except Exception:
        logger.warning("The recorded renames could not be read now; the next run resumes them", exc_info=True)
        return
    for project, revision in repository.list_projects():
        if _is_personal(project.workspace, known) or project.id in waiting:
            continue  # a rename target moves with its original's rename
        old = project.id
        try:
            if is_reserved_project_id(project.id):
                project, revision = _rename(repository, project, revision)
                report.renamed[old] = project.id
                logger.warning("Project '%s' had a reserved id and is now '%s'", old, project.id)
            _move(repository, project, revision, admin, report)
            report.failed.pop(old, None)
            report.failed.pop(project.id, None)
        except Exception as exc:
            logger.warning("Project '%s' could not be migrated now; the next run retries it", project.id, exc_info=True)
            report.failed[project.id] = f"{type(exc).__name__}: {exc}"


def _repair_listings(repository: RepositoryPort, report: PersonalMigration) -> None:
    """Prune the entries of every listing whose project is gone or foreign (conditional on the listed revision, only
    after the grace period: `prune_listing`, #183, ADR-0124), then add every migrated project that a listing misses
    (its owner's own, its recipients' shared): a listing write that failed after a create, share or move, or data from
    before the listings."""
    known = _workspaces(repository)
    stored = {project.id: project for project, _ in repository.list_projects()}
    listed: dict[str, tuple[set[str], set[str]]] = {}
    for key, body, revision in repository.list_documents(LISTING):
        user = body.get("user")
        if isinstance(user, str):
            listing = parse_listing(user, body, revision)
            stale = [p for p in listing.ids() if p not in stored or _foreign(stored[p], user)]
            if stale and key == user_key(user):
                pruned = prune_listing(repository, listing, stale)
                if pruned:
                    report.pruned += pruned
                    listing = read_listing_document(repository, user)
            listed[user] = (set(listing.own), set(listing.shared))
    missing: dict[str, list[tuple[str, str]]] = {}
    for project in stored.values():
        workspace = known.get(project.workspace or "")
        if workspace is None or not workspace.is_personal_of(workspace.owner):
            continue
        if project.id not in listed.get(workspace.owner, (set(), set()))[0]:
            missing.setdefault(workspace.owner, []).append((OWN, project.id))
        for user in project.roles:
            if project.id not in listed.get(user, (set(), set()))[1]:
                missing.setdefault(user, []).append((SHARED, project.id))
    for user, entries in sorted(missing.items()):
        try:
            update_listing(repository, user, add=entries)
            report.indexed += len(entries)
        except Exception:
            logger.warning("The project listing of '%s' could not be repaired now", user, exc_info=True)


def _foreign(project: Project, user: str) -> bool:
    """Not in the user's personal workspace and no share for them: a listing entry nobody needs (ADR-0124)."""
    return project.workspace != personal_workspace_id(user) and user not in project.roles


def migrate_to_personal_workspaces(repository: RepositoryPort, admin: Identity | None) -> PersonalMigration:
    """Move every project that is not in a personal workspace into the one of its owner (system context, idempotent;
    spec: personal-workspaces.md, ADR-0102): projects of an M20 workspace (`default` or a team) go to that workspace's
    owner, or to their explicit owner (the member who created them; the workspace owner then gets an editor share),
    and every member keeps their M20 effective role as a share; data without a workspace (v0.1.8) goes to `admin`, or
    waits (hidden) without one. M20 projects with a reserved id are renamed first. The M20 workspace documents are
    kept (ADR-0103). Then the membership is repaired (best effort), what the repair created (the default project) is
    moved too, and the users' listings are completed."""
    report = PersonalMigration()
    _move_all(repository, admin, report)
    try:  # best effort: the moves above do not depend on it, and the next start or the CLI repeats it
        # the repair creates the default project (if it needs one) under the reserved legacy id `default`, which the
        # second pass below moves to the owner of `default`, or to the admin, like every project from before workspaces
        report.repair = ProjectService(repository, repository).reconcile()
    except Exception as exc:
        logger.exception("The project membership repair failed; run `arcviz-migrate --projects STORE` or restart")
        report.repair_error = f"{type(exc).__name__}: {exc}"
    if report.repair.changed or report.failed:
        _move_all(repository, admin, report)
    _repair_listings(repository, report)
    report.created = sorted(set(report.created))
    known = _workspaces(repository)
    for project, _ in repository.list_projects():
        if _is_personal(project.workspace, known) or project.id in report.failed:
            continue
        if project.workspace in (None, DEFAULT_WORKSPACE_ID) and project.workspace not in known:
            report.hidden += 1  # waits for its admin
        elif project.workspace not in known:
            report.stranded.setdefault(str(project.workspace), []).append(project.id)
    report.waiting_for_admin = report.hidden > 0
    return report


def reassign_workspace(repository: RepositoryPort, missing: str, user_id: str) -> list[str]:
    """Operator recovery (system context, `arcviz-migrate --workspaces STORE --reassign MISSING=USER`): move every
    project that names the workspace `missing`, which must not exist, into the personal workspace of `user_id`,
    without shares (nobody else gains access). Returns the moved project ids. The default workspace is never
    "missing" here: its data waits for the admin (`--admin`)."""
    if missing == DEFAULT_WORKSPACE_ID:
        raise WorkspaceError(
            "The default workspace is not reassigned; set ARCVIZ_ADMIN_USER (or --admin) to give its data an owner."
        )
    if missing in _workspaces(repository):  # a lost personal workspace (`user_…`) can be recovered like any other
        raise WorkspaceError(f"Workspace '{missing}' exists; nothing to reassign.")
    identity = Identity.of(user_id)
    if identity is None:
        raise WorkspaceError(f"Invalid user id '{user_id}'.")
    target = ensure_personal_workspace(repository, identity)
    moved: list[str] = []
    for project, revision in repository.list_projects():
        for _ in range(MUTATION_ATTEMPTS):
            if project.workspace != missing:
                break
            update_listing(repository, identity.id, add=[(OWN, project.id)])
            try:
                repository.commit_project(project.model_copy(update={"workspace": target.id, "roles": {}}), revision)
                moved.append(project.id)
                break
            except ConflictError:
                try:
                    project, revision = repository.read_project(project.id)
                except StoreError:
                    break
    return moved
