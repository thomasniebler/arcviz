"""Application services: the only callers of the persistence ports.

Spec: docs/architecture/specs/application-services.md, ADR-0050. Entry points (HTTP routes, the agent,
proposals) call services; services own every read-modify-write with conditional writes and depend only
on domain types and `arcviz.ports`.
"""

from ..projects import ProjectStateError
from .access import AccessPolicy, DiagramNotFound, Forbidden, Guard, WorkspaceNotFound
from .bundles import ProjectExchangeService
from .context import ServiceRoot, Services
from .diagrams import DiagramService, DiagramState, LiveWrite
from .live import LiveService, UnknownSession
from .model import (
    ElementChange,
    ElementRemoval,
    ElementView,
    ModelService,
    ProjectModel,
    RelationChange,
    RelationRemoval,
    RelationUsage,
    RelationView,
    SyncReport,
    Usage,
)
from .projects import (
    DEFAULT_PROJECT_ID,
    ProjectAccess,
    ProjectDiagram,
    ProjectNotEmpty,
    ProjectNotFound,
    ProjectService,
    ProjectSummary,
    ProjectView,
    ReconcileReport,
)
from .proposals import ProposalError, ProposalService
from .requirement_chat import RequirementChatService
from .requirements import RequirementService
from .tokens import AgentCredential, TokenError, TokenService
from .users import Person, UserError, UserService
from .workspaces import (
    PersonalMigration,
    WorkspaceError,
    WorkspaceService,
    WorkspaceSummary,
    migrate_to_personal_workspaces,
    reassign_workspace,
)
from .writers import Heartbeat, Reaper, ReapReport, WriterConfig, settle_pending

__all__ = [
    "AccessPolicy",
    "AgentCredential",
    "DiagramNotFound",
    "Forbidden",
    "Guard",
    "Person",
    "PersonalMigration",
    "ProposalError",
    "ProposalService",
    "RequirementChatService",
    "RequirementService",
    "TokenError",
    "TokenService",
    "UserError",
    "UserService",
    "WorkspaceError",
    "WorkspaceNotFound",
    "WorkspaceService",
    "WorkspaceSummary",
    "migrate_to_personal_workspaces",
    "reassign_workspace",
    "DEFAULT_PROJECT_ID",
    "DiagramService",
    "DiagramState",
    "ElementChange",
    "ElementRemoval",
    "RelationChange",
    "RelationRemoval",
    "RelationUsage",
    "RelationView",
    "ElementView",
    "LiveService",
    "LiveWrite",
    "UnknownSession",
    "ModelService",
    "ProjectExchangeService",
    "ProjectAccess",
    "ProjectDiagram",
    "ProjectModel",
    "ProjectNotEmpty",
    "ProjectNotFound",
    "ProjectService",
    "ProjectStateError",
    "ProjectSummary",
    "ProjectView",
    "ReconcileReport",
    "ServiceRoot",
    "Services",
    "SyncReport",
    "Usage",
    "Heartbeat",
    "ReapReport",
    "Reaper",
    "WriterConfig",
    "settle_pending",
]
