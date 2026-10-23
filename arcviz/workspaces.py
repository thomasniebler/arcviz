"""Users, personal workspaces and project roles (M20, reworked in M21).

Spec: docs/architecture/specs/personal-workspaces.md (ADR-0100..0103; identities: workspaces-and-sharing.md,
ADR-0080). Pure types and functions: who a caller is (`Identity`), what the directory knows about a user
(`UserRecord`), a user's personal workspace and the role a user has on a project (owner → share → none).
Persistence goes through the services; nothing here reads or writes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .model import now_iso
from .projects import Project

MAX_ID_LENGTH = 256
MAX_NAME_LENGTH = 200


@dataclass(frozen=True)
class Identity:
    """The caller of a request: a stable user id (from the authenticating proxy, or the admin id), a display
    name (falls back to the id) and an optional e-mail address. `email_trusted`: the address came from the proxy's
    configured e-mail header (or the operator), so it may identify the user when a workspace owner adds a member by
    e-mail; an address taken from a name or `preferred_username` claim is display data only (ADR-0085)."""

    id: str
    name: str
    email: str | None = None
    email_trusted: bool = field(default=False, compare=False)

    @classmethod
    def of(
        cls, user_id: str, name: str | None = None, email: str | None = None, email_trusted: bool = False
    ) -> Identity | None:
        """A validated identity, or None when `user_id` is empty, too long or contains control characters."""
        user_id = (user_id or "").strip()
        if not user_id or len(user_id) > MAX_ID_LENGTH or not user_id.isprintable():
            return None
        clean_name = " ".join((name or "").split())[:MAX_NAME_LENGTH] or user_id
        clean_email = (email or "").strip()
        valid_email = 3 <= len(clean_email) <= MAX_ID_LENGTH and "@" in clean_email and clean_email.isprintable()
        return cls(user_id, clean_name, clean_email if valid_email else None, email_trusted and valid_email)


class UserRecord(BaseModel):
    """A user of the directory, written when the user is first seen (spec: workspaces-and-sharing.md)."""

    id: str
    name: str
    email: str | None = None
    email_trusted: bool = False  # the address came from the proxy's e-mail header: it may identify the user
    first_seen: str
    last_seen: str  # day granularity is enough: it is refreshed at most once a day


def user_key(user_id: str) -> str:
    """The storage key of a user: ids may contain any character, keys must be ids (`ports.ID_RE`)."""
    return "u-" + hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]


# ---- personal workspaces (ADR-0100) ---------------------------------------------------------------------

DEFAULT_WORKSPACE_ID = "default"  # M20's workspace of the data from before workspaces; a reserved id since (ADR-0102)
PERSONAL_PREFIX = "user_"  # "_" never occurs in M20 workspace ids (slugs of titles), so no M20 id can be taken
PERSONAL_TITLE = "Persönlicher Arbeitsbereich"


PERSONAL_PROJECT_PREFIX = "home_"  # the user's default project ("Standardprojekt")
PERSONAL_PROJECT_TITLE = "Standardprojekt"
_PERSONAL_PROJECT_RE = re.compile(r"^home_[0-9a-f]{32}$")
_RESERVED_PROJECT_RE = re.compile(r"^(home|user)_[0-9a-f]{32}$")


def _digest(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]


def personal_workspace_id(user_id: str) -> str:
    """The id of the user's personal workspace: deterministic, so every replica derives the same one."""
    return PERSONAL_PREFIX + _digest(user_id)


def personal_project_id(user_id: str) -> str:
    """The id of the user's default project, where diagrams created without a project go (deterministic)."""
    return PERSONAL_PROJECT_PREFIX + _digest(user_id)


def is_personal_project_id(project_id: str) -> bool:
    """Whether the id has the form of a user's default project (`home_` + 32 hex digits)."""
    return _PERSONAL_PROJECT_RE.match(project_id) is not None


def is_reserved_project_id(project_id: str) -> bool:
    """Whether a project id has a reserved form (`home_`/`user_` + 32 hex digits): no create gets it, and the
    migration renames M20 projects that have one (ADR-0100)."""
    return _RESERVED_PROJECT_RE.match(project_id) is not None


class Member(BaseModel):
    """A member of an M20 team workspace (read by the migration only)."""

    user: str  # user id
    added_at: str


class Workspace(BaseModel):
    """A user's personal workspace: the container of the projects they own. M20 team workspaces (with members,
    `personal` False) are still read by the migration and give nobody access (ADR-0103)."""

    id: str
    title: str
    owner: str  # user id
    members: list[Member] = Field(default_factory=list)  # M20 teams only; never contains the owner
    personal: bool = False
    # the user's default project when `personal_project_id(owner)` was taken by somebody else's project (ADR-0100)
    default_project: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def member(self, user_id: str) -> Member | None:
        return next((m for m in self.members if m.user == user_id), None)

    def is_personal_of(self, user_id: str) -> bool:
        """Whether this is `user_id`'s personal workspace (flag, owner and derived id all agree)."""
        return self.personal and self.owner == user_id and self.id == personal_workspace_id(user_id)


# ---- project roles (ADR-0101) ---------------------------------------------------------------------------

ROLES = ("viewer", "editor", "owner")  # ascending: every role includes the rights of the ones before it
SHARE_ROLES = ("viewer", "editor")  # what a share can be: a project has exactly one owner (ADR-0101)
LEGACY_DEFAULT_ROLE = "viewer"  # what an M20 member had on a project nobody shared explicitly (migration only)
_RANK = {role: n for n, role in enumerate(ROLES)}


def allows(role: str | None, need: str) -> bool:
    """Whether `role` includes the rights of `need`."""
    return role is not None and _RANK[role] >= _RANK[need]


def project_role(user_id: str, workspace: Workspace | None, project: Project) -> str | None:
    """The effective role of a user on a project, or None when the user may not see it (spec:
    personal-workspaces.md): the owner of the project's personal workspace owns it; a share gives its role; nothing
    else gives access (an "owner" entry, as M20 wrote them, grants nothing), and a project outside a personal workspace
    (not migrated yet) is visible to nobody."""
    if workspace is None or project.workspace != workspace.id or not workspace.is_personal_of(workspace.owner):
        return None
    if user_id == workspace.owner:
        return "owner"
    role = project.roles.get(user_id)
    return role if role in SHARE_ROLES else None


def legacy_role(user_id: str, workspace: Workspace, project: Project) -> str | None:
    """The role M20 gave a user on a project of a team workspace (the migration keeps it as a share, ADR-0102):
    the workspace owner owned every project, members had their explicit role or the default, others nothing."""
    if user_id == workspace.owner:
        return "owner"
    if workspace.member(user_id) is None:
        return None  # stale roles of former members were inert
    role = project.roles.get(user_id, LEGACY_DEFAULT_ROLE)
    return role if role in _RANK else LEGACY_DEFAULT_ROLE
