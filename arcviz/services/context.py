"""The services of one caller, and the root that hands them out (spec: docs/architecture/specs/personal-workspaces.md).

`ServiceRoot` is built once per application from the repository; `for_actor(identity)` returns the services bound to
one caller (one request, one chat turn), so every read and write is checked by the access policy (ADR-0084). The
system context (unguarded services) is only for the composition roots' maintenance: the start-up migration here,
the CLI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..live import LiveHub
from ..ports import RepositoryPort
from ..proposals import ProposalConfig
from ..workspaces import Identity, personal_workspace_id
from .access import AccessPolicy, Guard, ensure_personal_workspace
from .bundles import ProjectExchangeService
from .diagrams import DiagramService
from .live import LiveService
from .model import ModelService
from .projects import ProjectService
from .proposals import ProposalService
from .requirement_chat import RequirementChatService
from .requirements import RequirementService
from .tokens import AgentCredential, TokenService
from .users import UserService
from .workspaces import PersonalMigration, WorkspaceService, migrate_to_personal_workspaces
from .writers import Heartbeat, WriterConfig

logger = logging.getLogger(__name__)


@dataclass
class Services:
    """Every service, bound to `actor`."""

    actor: Identity
    guard: Guard
    diagrams: DiagramService
    projects: ProjectService
    models: ModelService
    proposals: ProposalService
    requirements: RequirementService
    requirement_chat: RequirementChatService
    tokens: TokenService
    bundles: ProjectExchangeService
    workspaces: WorkspaceService
    users: UserService
    live: LiveService


class ServiceRoot:
    def __init__(
        self,
        repository: RepositoryPort,
        hub: LiveHub | None = None,
        admin: Identity | None = None,
        proposal_config: ProposalConfig | None = None,
    ):
        self._repo = repository
        self._proposal_config = proposal_config or ProposalConfig()
        self.admin = admin  # owns the default project (and the data from before workspaces)
        self._default_workspace = personal_workspace_id(admin.id) if admin is not None else None
        self._personal: set[str] = set()  # users whose personal workspace this process has seen (ADR-0100)
        self.hub = hub  # live updates and presence (spec: collaboration.md); None: nothing is published
        if hub is not None:
            hub.share_presence(repository.presence_channel())  # Cosmos: presence across replicas (ADR-0104)
        self.users = UserService(repository)  # the directory: shared, its cache is per process
        self.policy = AccessPolicy(repository, repository, repository)
        self._tokens = TokenService(repository)  # the middleware's lookup, bound to nobody
        self._probe = DiagramService(repository)  # storage name, Git status and readiness only

    def for_actor(self, actor: Identity, per_request: bool = False) -> Services:
        """The services bound to `actor`. `per_request`: for one ordinary HTTP request, whose guard reuses the project
        documents its viewer checks read (#166, ADR-0121); never for chat turns, live streams or anything long-lived."""
        guard = self.policy.guard(actor, cache_projects=per_request)
        projects = ProjectService(self._repo, self._repo, guard, self.hub, self.users, self._default_workspace)
        diagrams = DiagramService(self._repo, projects, guard, self.hub)
        models = ModelService(projects, diagrams)
        requirements = RequirementService(self._repo, projects, actor)
        return Services(
            actor=actor,
            guard=guard,
            diagrams=diagrams,
            projects=projects,
            models=models,
            proposals=ProposalService(
                self._repo, projects, diagrams, models, actor, self._proposal_config.retention_days, requirements
            ),
            requirements=requirements,
            requirement_chat=RequirementChatService(self._repo, projects),
            tokens=TokenService(self._repo, actor),
            bundles=ProjectExchangeService(diagrams, projects, requirements),
            workspaces=WorkspaceService(self._repo, self.users, guard),
            users=self.users,
            live=LiveService(self.hub, self._repo, projects, guard),
        )

    def authenticate_token(self, token: str) -> AgentCredential | None:
        """The credential of a personal access token (system context: the middleware's lookup, spec:
        agent-authentication.md), or None for any failure."""
        return self._tokens.authenticate(token)

    def signed_in(self, identity: Identity) -> None:
        """A request of `identity` arrived: make sure its personal workspace and default project exist (once per user
        and process; ADR-0100). Never raises: a failure is logged and repeated by the next request, and the first write that needs
        the workspace creates it anyway."""
        if identity.id in self._personal:
            return
        try:
            ensure_personal_workspace(self._repo, identity)
            self.for_actor(identity).projects.ensure_own_default()
        except Exception:
            logger.warning("Could not create the personal workspace of '%s'", identity.id, exc_info=True)
            return
        self._personal.add(identity.id)

    def heartbeat(self, config: WriterConfig) -> Heartbeat:
        """This process's writer heartbeat for the store (#189, ADR-0133); the app's lifespan starts and closes it."""
        return Heartbeat(self._repo, self._repo, config)

    def migrate(self, admin: Identity | None) -> PersonalMigration:
        """The migration to personal workspaces (ADR-0102), in the system context."""
        return migrate_to_personal_workspaces(self._repo, admin)

    # ---- facts about the store, no data -----------------------------------------------------------

    @property
    def storage_name(self) -> str:
        return self._probe.storage_name

    def git_status(self) -> dict[str, Any]:
        return self._probe.git_status()

    def start_mirror(self) -> None:
        """Start-up: the Git mirror pushes what a previous process left unpushed (#113)."""
        self._probe.start_mirror()

    def drain_mirror(self, timeout: float | None = None) -> bool:
        """Shutdown: the Git mirror's pending push, waited for at most `timeout` seconds (default: the mirror's bound,
        #113)."""
        return self._probe.drain_mirror(timeout)

    def ping(self) -> None:
        self._probe.ping()
