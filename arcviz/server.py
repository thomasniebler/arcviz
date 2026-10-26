"""FastAPI application: REST + server-sent events for the chat, static frontend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, closing, contextmanager, nullcontext, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, TypeVar
from urllib.parse import parse_qs

import anyio
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from starlette.routing import Route

from . import (
    __version__,
    agentauth,
    auth,
    diff,
    exchange,
    i18n,
    limits,
    live,
    mcp_server,
    mermaid,
    observability,
    operations,
    project_review,
    proposal,
    quality_suggestion,
    render,
    requirement_derive,
    requirement_import,
    requirement_refine,
    requirement_workshop,
    review,
    rules,
    scenario_analysis,
    sequence,
    telemetry,
    tokens,
    whatsnew,
)
from .agent import DEFAULT_CONTEXT_BUDGET, DiagramAgent
from .agentauth import AgentAuthConfig, AgentGate
from .decision_model import adr_label, decision_lines, decision_markdown
from .envconfig import ConfigError, Env
from .graph_types import edge_may_be_undirected, profile_of
from .history import HistoryConfig
from .i18n import _
from .kinds import icon_packs, kind_pack_path, load_kind_catalog
from .llm import LLMProvider, ProviderConfig, check_provider, create_provider
from .mcp_server import McpEndpoint
from .model import BUILTIN_KINDS, DIAGRAM_TYPES, DIRECTIONS, UML_REFERENCES, active_kinds, kinds_for, now_iso, use_kinds
from .oidc import OidcValidator
from .ports import MAX_VERSIONS_PAGE, ConflictError, RepositoryPort, RevisionMismatch, StoreError, check_id
from .project_agent import ProjectAgent
from .project_model import (
    DRIVER_KINDS,
    DRIVER_LEVELS,
    ELEMENT_KINDS,
    MAX_DRIVER_DESCRIPTION,
    MAX_DRIVER_TITLE,
    MAX_DRIVERS,
    MAX_ELEMENTS,
    MAX_RELATIONS,
    PROPERTY_VALUES,
    RELATION_KINDS,
    RELATION_RULES,
    linkable_table,
    ranked_drivers,
    relation_table,
    shown_relation_kinds,
)
from .proposals import ProposalConfig
from .quality_model import driver_qualities
from .requirements_agent import RequirementsAgent
from .services import (
    Forbidden,
    Person,
    PersonalMigration,
    ProjectAccess,
    ProjectNotEmpty,
    ProjectNotFound,
    ProjectStateError,
    ProjectView,
    ServiceRoot,
    Services,
    TokenError,
    UnknownSession,
    WorkspaceNotFound,
    WriterConfig,
)
from .services.diagrams import UNTITLED, untitled
from .store import create_store
from .templates import TEMPLATE_TYPES, TEMPLATES, create_from_template, templates_of
from .workspaces import SHARE_ROLES, Identity, personal_workspace_id

logger = logging.getLogger(__name__)
T = TypeVar("T")
STATIC_DIR = Path(__file__).parent / "static"
# Built documentation site (scripts/build_docs_bundle.py); absent in a development checkout until built.
DOCS_DIR = Path(__file__).parent / "docs_site"
DOCS_MISSING = (
    "The documentation is not part of this build.\n\n"
    "Build it with `make docs-bundle` (scripts/build_docs_bundle.py writes arcviz/docs_site/) and restart, "
    "or read the Markdown sources under docs/ in the repository (https://github.com/thomasniebler/arcviz).\n"
)

# Type-specific enumerations the UI needs (for example message sorts), merged into the type's config.
TYPE_EXTRAS: dict[str, dict[str, Any]] = {
    "sequence": {
        "message_sorts": list(sequence.MESSAGE_SORTS),
        "message_kinds": list(sequence.MESSAGE_KINDS),
        "interaction_operators": list(sequence.INTERACTION_OPERATORS),
        "multi_operand_operators": sorted(sequence.MULTI_OPERAND_OPERATORS),
    }
}


def _field_table(diagram_type: str, *, nodes: bool) -> dict[str, list[dict[str, Any]]]:
    """The typed fields of a graph type by kind, for the UI and the tool descriptions (ADR-0177); empty without any."""
    profile = profile_of(diagram_type)
    if profile is None:
        return {}
    table = profile.node_fields if nodes else profile.edge_fields
    return {
        kind: [
            {"name": f.name, "list": f.is_list, "flag": f.flag, "max_items": f.max_items, "max_length": f.max_length}
            for f in fields
        ]
        for kind, fields in sorted(table.items())
    }


# Query parameter shared by every endpoint that renders: focus the view on one node's subtree.
Focus = Annotated[str | None, Query(description="Node id to focus on (its subtree plus crossing edges).")]


class CreateDiagramRequest(BaseModel):
    title: str
    id: str | None = None
    template: str = "empty"
    type: str | None = None  # default: the template's type, else deployment
    project: str | None = None  # default: the default project (spec: projects.md)
    section: str | None = None


class CreateProjectRequest(BaseModel):
    title: str  # the project goes into the caller's personal workspace (spec: personal-workspaces.md)
    id: str | None = None
    description: str = ""


class ShareRequest(BaseModel):
    user: str  # user id or (trusted) e-mail address of a user who has signed in once
    role: str  # viewer | editor


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    description: str | None = None


class ProjectOperationRequest(BaseModel):
    op: str
    args: dict[str, Any] = Field(default_factory=dict)


class VisionRequest(BaseModel):
    statement: str = ""
    goals: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)


class CreateSectionRequest(BaseModel):
    title: str
    parent: str | None = None
    position: int | None = None
    id: str | None = None


class UpdateSectionRequest(BaseModel):
    title: str | None = None
    parent: str | None = None  # present (also null) or `position` present: move
    position: int | None = None
    element: str | None = None  # present: the model element the section stands for (null: none)


class CreateElementRequest(BaseModel):
    name: str
    kind: str = "component"
    description: str = ""
    parent: str | None = None
    id: str | None = None


class CodeRefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    repo: str = ""
    symbol: str = ""
    note: str = ""


class CreateTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    scope: str = "read"
    expires_in_days: int = tokens.DEFAULT_DAYS


class CreateDriverRequest(BaseModel):
    title: str
    kind: str = "quality_goal"
    importance: str = "medium"
    risk: str = "medium"
    description: str = ""
    elements: list[str] = Field(default_factory=list)
    position: int | None = None
    requirements: list[str] = Field(default_factory=list)


class UpdateDriverRequest(BaseModel):
    title: str | None = None
    kind: str | None = None
    importance: str | None = None
    risk: str | None = None
    description: str | None = None
    elements: list[str] | None = None
    position: int | None = None  # present: move the driver to this place of the list
    requirements: list[str] | None = None
    status: str | None = None  # active | retired (ADR-0198)


class RequirementsChatRequest(BaseModel):
    message: str = Field(min_length=1)


class RefineRequirementsRequest(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=requirement_refine.MAX_CHUNK)
    limit: int = Field(default=requirement_refine.MAX_CHUNK, ge=1, le=requirement_refine.MAX_CHUNK)


class DeriveRequirementsRequest(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=requirement_derive.MAX_CHUNK)
    theme: str | None = None
    limit: int = Field(default=requirement_derive.MAX_CHUNK, ge=1, le=requirement_derive.MAX_CHUNK)


class ImportRequirementsRequest(BaseModel):
    text: str = Field(max_length=requirement_import.MAX_TEXT_CHARS)
    format: str = "auto"
    source: str = ""
    dry_run: bool = False


class ProposalDecisionRequest(BaseModel):
    """The items of a project proposal to apply (`accept`) or to discard (`keys`); absent: all that are pending."""

    accept: list[str] | None = Field(default=None, max_length=60)
    keys: list[str] | None = Field(default=None, max_length=60)


class AcceptItem(BaseModel):
    id: str
    expected: str  # the `rev` of the suggestion the user saw
    fields: list[str] | None = None


class AcceptSuggestionsRequest(BaseModel):
    items: list[AcceptItem] = Field(max_length=500)


class SetRequirementStatusRequest(BaseModel):
    ids: list[str] = Field(max_length=500)
    status: str
    duplicate_of: str | None = None
    reason: str = ""


class DeleteRequirementsRequest(BaseModel):
    ids: list[str] = Field(max_length=500)  # like the bulk status route


class ResolveIssueRequest(BaseModel):
    resolved: bool = True


class UpdateElementRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    kind: str | None = None
    parent: str | None = None  # present (also null): move the element


class CreateRelationRequest(BaseModel):
    kind: str
    source: str  # element id (or name)
    target: str
    properties: dict[str, str | int | float | bool] | None = None
    description: str = ""
    id: str | None = None


class UpdateRelationRequest(BaseModel):
    kind: str | None = None
    source: str | None = None
    target: str | None = None
    properties: dict[str, str | int | float | bool] | None = None
    remove_properties: list[str] | None = None
    description: str | None = None


class LinkRequest(BaseModel):
    view: str  # node or lifeline id (or name)
    element: str  # element id (or name) of the diagram's project


class UnlinkRequest(BaseModel):
    view: str


class HideRelationRequest(BaseModel):
    relation: str
    hidden: bool = True


class LinkRelationRequest(BaseModel):
    view: str  # edge or message id
    relation: str  # relation id of the diagram's project


class PromoteRequest(BaseModel):
    view: str
    kind: str | None = None  # default: the view element's default element kind
    description: str = ""


class ImportProjectRequest(BaseModel):
    bundle: Any
    title: str | None = None


class MoveDiagramRequest(BaseModel):
    project: str | None = None  # default: the project in the path
    section: str | None = None  # default within the same project: the current section
    position: int | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    focus: str | None = None  # container the UI is zoomed into; live renders use the same view
    mode: Literal["apply", "propose"] = "apply"  # propose = dry run, confirmed via /proposal/apply


class ProjectChatRequest(BaseModel):
    message: str = Field(min_length=1)
    active: str | None = None  # the diagram the UI shows: the turn's first active diagram (spec: project-chat.md)
    focus: str | None = None
    mode: Literal["apply", "propose"] = "apply"


class ProjectProposalRequest(BaseModel):
    diagram: str
    operations: list[dict[str, Any]]
    base: str
    result: str
    label: str | None = None


class ProjectDiscardRequest(BaseModel):
    diagram: str | None = None


class ProposalRequest(BaseModel):
    operations: list[dict[str, Any]]
    base: str
    result: str
    label: str | None = None


class SnapshotRequest(BaseModel):
    label: str = "Manual snapshot"


class ImportRequest(BaseModel):
    document: Any
    id: str | None = None
    title: str | None = None
    project: str | None = None
    section: str | None = None


class OperationRequest(BaseModel):
    op: str
    args: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class PresenceRequest(BaseModel):
    session: str
    selection: str | None = None


class LiveResponse(StreamingResponse):
    """A live stream that runs `on_close` however the response ends, also when it never started (spec:
    collaboration.md); `LiveHub.unsubscribe` is idempotent."""

    def __init__(self, content: Any, on_close: Callable[[], None], **kwargs: Any):
        super().__init__(content, **kwargs)
        self.on_close = on_close

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.on_close()


class TurnResponse(StreamingResponse):
    """A chat turn as server-sent events (spec: hardening.md, ADR-0126). However the response ends (completed, the
    client went away and the stream was cancelled, a send failed), the turn's generator is closed on a worker thread,
    shielded from cancellation, before the request is over: the agent's `finally` writes the version and the
    transcript at once, never on the event loop and never only when the garbage collector finds the generator.

    anyio does not abandon a running `next()` when the stream task is cancelled, so the generator is never closed
    while it runs; closing an exhausted or already closed generator does nothing."""

    def __init__(self, turn: Iterator[str]):
        self.turn = turn
        super().__init__(
            iterate_in_threadpool(turn),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.turn, "close", None)
            if close is not None:
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(close)


class DocsFiles(StaticFiles):
    """The built documentation: static files that browsers revalidate on every use (ETag/Last-Modified).

    Page URLs are not content-hashed, and the site changes with every release, so `no-cache` keeps a
    browser from showing the documentation of the previous version after an upgrade.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        # Also covers the site's 404.html (built without file_response) and the trailing-slash redirects.
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def caller(request: Request) -> Identity:
    """The identity the authentication middleware found; 401 without one (never on a protected route)."""
    identity = getattr(request.state, "arcviz_identity", None)
    if identity is None:
        raise HTTPException(401, "Authentication required.")
    return identity


def services_of(request: Request) -> Services:
    """The services bound to the caller of this request (spec: workspaces-and-sharing.md, ADR-0084): every route
    that reads or writes data gets them through this dependency, so the access policy sees every call. Their guard
    reads each project once per request (#166, ADR-0121)."""
    root: ServiceRoot = request.app.state.services
    return root.for_actor(caller(request), per_request=True)


def turn_services_of(request: Request) -> Services:
    """As `services_of` for the long-lived routes (chat turns, live streams): no project cache, so every step of a
    turn and every re-check sees a role change at once (ADR-0090, ADR-0096, ADR-0121)."""
    root: ServiceRoot = request.app.state.services
    return root.for_actor(caller(request))


Svc = Annotated[Services, Depends(services_of)]
TurnSvc = Annotated[Services, Depends(turn_services_of)]


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


WEAK = "W/"  # a weak entity tag: kept with its prefix, so it never equals a revision (strong comparison)


def if_match(request: Request) -> tuple[str, ...] | None:
    """The revisions of an `If-Match` header (spec: collaboration.md, ADR-0094; RFC 9110): any listed strong entity
    tag (`"rev"` or a bare token) may match; a weak one (`W/"rev"`) never does (If-Match compares strongly); `*`
    anywhere, an empty value or no header mean no condition (the diagram only has to exist, which every route checks
    anyway)."""
    tags = []
    for part in request.headers.get("if-match", "").split(","):
        tag = part.strip()
        if tag.startswith(WEAK):
            tags.append(WEAK + tag[2:].strip().strip('"'))
            continue
        tag = tag.strip('"').strip()
        if tag == "*":
            return None
        if tag:
            tags.append(tag)
    return tuple(tags) or None


IfMatch = Annotated[tuple[str, ...] | None, Depends(if_match)]


def etag_header(revision: str | None) -> dict[str, str]:
    """The diagram's latest revision as a strong ETag header (none without a revision)."""
    return {"ETag": f'"{revision}"'} if revision is not None else {}


def etag(response: Response, revision: str | None) -> None:
    response.headers.update(etag_header(revision))


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def close_on_shutdown_signals(close: Callable[[], None]) -> Callable[[], None]:
    """Run `close` first whenever SIGINT/SIGTERM arrive, then the handler the server installed (ADR-0098).

    Called from the app's lifespan start-up, which uvicorn runs after installing its own signal handlers and long
    before it drains open connections on shutdown, so the live streams end before that drain whoever serves the app
    (`arcviz`, `uvicorn --factory`, a uvicorn worker). Only in the main thread (signals cannot be set elsewhere);
    a handler that is not a Python callable (default, ignored) is left alone. Returns the undo."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    installed: dict[int, tuple[Any, Any]] = {}
    for sig in SHUTDOWN_SIGNALS:
        previous = signal.getsignal(sig)
        if not callable(previous):
            continue

        def handler(signum: int, frame: Any, previous: Any = previous) -> None:
            close()
            previous(signum, frame)

        signal.signal(sig, handler)
        installed[sig] = (previous, handler)

    def undo() -> None:
        for sig, (previous, handler) in installed.items():
            if signal.getsignal(sig) is handler:
                signal.signal(sig, previous)

    return undo


def error_type(error: str) -> str:
    """The exception type of a recorded `Type: message` (the message may carry content; ADR-0112)."""
    return error.split(":", 1)[0].strip() or "error"


@dataclass(frozen=True)
class AgentSettings:
    """The chat agents' limits from the environment (spec: configuration reference, *LLM provider*)."""

    max_rounds: int = 12
    context_budget: int | None = DEFAULT_CONTEXT_BUDGET  # None: no collapsing
    history_turns: int = 30
    compact_after: int = 60
    keep: int = 30
    token_budget: int = 0  # 0: no cap

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AgentSettings:
        env = Env(environ)
        settings = cls(
            max_rounds=env.integer("ARCVIZ_MAX_ROUNDS", 12, minimum=1),
            context_budget=env.integer("ARCVIZ_CONTEXT_BUDGET", DEFAULT_CONTEXT_BUDGET) or None,
            history_turns=env.integer("ARCVIZ_HISTORY_TURNS", 30),
            compact_after=env.integer("ARCVIZ_CHAT_COMPACT_AFTER", 60),
            keep=env.integer("ARCVIZ_CHAT_KEEP", 30, minimum=1),
            token_budget=env.integer("ARCVIZ_TURN_TOKEN_BUDGET", 0),
        )
        env.check()
        return settings


class StartupConfig:
    """Collects the `ConfigError`s of every subsystem so that start-up reports them together (ADR-0106)."""

    def __init__(self) -> None:
        self.errors: list[ConfigError] = []

    def read(self, from_env: Callable[[], T], fallback: T) -> T:
        """`from_env()`, or `fallback` after recording its problems (a ConfigError's, or another ValueError's)."""
        try:
            return from_env()
        except ConfigError as exc:
            self.errors.append(exc)
        except ValueError as exc:  # a subsystem that stops at its first problem (authentication)
            self.errors.append(ConfigError([str(exc)]))
        return fallback

    def add(self, problems: list[str]) -> None:
        if problems:
            self.errors.append(ConfigError(problems))

    def check(self) -> None:
        if self.errors:
            raise ConfigError.merge(self.errors)


def create_app(
    store: RepositoryPort | None = None, provider: LLMProvider | None = None, docs_dir: Path | None = None
) -> FastAPI:
    kinds_file = os.environ.get("ARCVIZ_KINDS_FILE", "").strip()
    packs = [p.strip() for p in os.environ.get("ARCVIZ_KIND_PACKS", "").split(",") if p.strip()]
    sources: list[str | Path] = [kind_pack_path(p) for p in packs]
    if kinds_file:
        sources.append(kinds_file)
    use_kinds(load_kind_catalog(*sources) if sources else BUILTIN_KINDS)  # KindConfigError stops startup
    # Every subsystem reads its own variables; the problems of all of them stop the start together (ADR-0106).
    startup = StartupConfig()
    limit_config = startup.read(limits.LimitConfig.from_env, limits.LimitConfig())
    auth_config = startup.read(auth.AuthConfig.from_env, auth.AuthConfig())
    live_config = startup.read(live.LiveConfig.from_env, live.LiveConfig())
    agent_settings = startup.read(AgentSettings.from_env, AgentSettings())
    log_config = startup.read(observability.LogConfig.from_env, observability.LogConfig())
    history_config = startup.read(HistoryConfig.from_env, HistoryConfig())  # pages and retention (ADR-0129/0130)
    writer_config = startup.read(WriterConfig.from_env, WriterConfig())  # writer heartbeat (#189, ADR-0133)
    proposal_config = startup.read(ProposalConfig.from_env, ProposalConfig())  # stored proposals (ADR-0172)
    agent_config = startup.read(AgentAuthConfig.from_env, AgentAuthConfig())  # MCP endpoint and credentials (ADR-0173)
    if agent_config.enabled:
        startup.add([m for m in (mcp_server.sdk_missing(), agentauth.oauth_missing(agent_config)) if m])
    if provider is None:  # the provider is still created lazily; its configuration and SDK are checked now (ADR-0107)
        provider_config = startup.read(ProviderConfig.from_env, None)
        if provider_config is not None:
            startup.add(check_provider(provider_config))
    startup.check()
    observability.configure_logging(log_config)  # spec: operations.md §4
    observability.start_telemetry(log_config)
    hub = live.LiveHub(live_config, limit_config.max_streams, limit_config.max_streams_per_user)
    telemetry.observe_live_streams(hub)  # the `arcviz.live.streams` gauge (#187); nothing while telemetry is off
    store = store or create_store()
    root = ServiceRoot(store, hub, auth_config.admin, proposal_config)
    # Data of v0.1.8 and M20 moves into personal workspaces (ADR-0102); idempotent, every start. Best effort: a
    # failure (a busy Cosmos DB, a replica starting at the same time) is logged and never stops the start; the
    # migration is repeated at the next start and by `arcviz-migrate --workspaces`. Until then nobody sees the
    # projects it did not move (ADR-0103); every move is one conditional write, so nothing is half-moved.
    try:
        migration = root.migrate(auth_config.admin)
    except Exception:
        logger.exception(
            "The migration to personal workspaces failed and is left for the next start; run "
            "`arcviz-migrate --workspaces STORE` to repeat it now. Projects not moved yet stay hidden until then."
        )
        migration = PersonalMigration()
    if migration.repair_error is not None:
        logger.warning(  # the type only: the service logged the exception itself (ADR-0112)
            "The project membership repair failed (%s); run `arcviz-migrate --projects STORE` or restart.",
            error_type(migration.repair_error),
        )
    for missing, project_ids in sorted(migration.stranded.items()):
        logger.warning(
            "Workspace '%s' does not exist, but project(s) %s name it; nobody sees them. Run "
            "`arcviz-migrate --workspaces STORE --reassign %s=USER`.",
            missing,
            ", ".join(project_ids),
            missing,
        )
    for old, new in sorted(migration.renamed.items()):
        logger.warning("Project '%s' had an id reserved for personal projects and is now '%s'.", old, new)
    for project_id, error in sorted(migration.failed.items()):
        logger.error(
            "Project '%s' could not be moved into a personal workspace (%s); nobody sees it until the next start or "
            "`arcviz-migrate --workspaces STORE` moves it.",
            project_id,
            error_type(error),
        )
    if migration.hidden:
        logger.warning(
            "ARCVIZ_ADMIN_USER is not set: %d project(s) from before workspaces are visible to nobody until "
            "ARCVIZ_ADMIN_USER names their owner (spec: personal-workspaces.md).",
            migration.hidden,
        )

    agents: AgentGate | None = None
    mcp_endpoint: McpEndpoint | None = None
    if agent_config.enabled:  # the MCP endpoint (spec: agent-access.md); its credentials are checked by `agents`
        oidc = OidcValidator(agent_config.oidc) if agent_config.oidc is not None else None
        agents = AgentGate(agent_config, root.authenticate_token, oidc, auth_config.admin, auth_config)
        mcp_endpoint = mcp_server.build_mcp(root, agent_config.path, __version__)
    users = root.users
    heartbeat = root.heartbeat(writer_config)  # settles the locator tokens of writers that died (#189, ADR-0133)

    def record_caller(request: Request) -> None:
        """Every authenticated request keeps the user directory current and makes sure the caller has a personal
        workspace (spec: personal-workspaces.md); neither ever fails the request."""
        identity = getattr(request.state, "arcviz_identity", None)
        if identity is not None:
            users.seen(identity)
            root.signed_in(identity)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Shutdown signals end the live streams before the server waits for open connections (ADR-0098)."""
        hub.open()  # a new lifespan after an earlier shutdown
        undo = close_on_shutdown_signals(hub.close)
        await run_in_threadpool(heartbeat.start)  # store writes stay off the event loop
        await run_in_threadpool(root.start_mirror)  # pushes what a previous process left unpushed (#113)
        try:
            async with mcp_endpoint.lifespan() if mcp_endpoint is not None else nullcontext():
                yield
        finally:
            hub.close()
            undo()
            await run_in_threadpool(heartbeat.close)  # "stopped" once no placement is in flight
            await run_in_threadpool(root.drain_mirror)  # a pending Git push, bounded (#113)

    # /docs is the documentation site (ADR-0043); FastAPI's interactive API docs move under /api.
    app = FastAPI(
        title="Arcviz agent",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        dependencies=[Depends(record_caller)],
        lifespan=lifespan,
    )
    app.state.services = root
    app.state.hub = hub  # opened and closed by the lifespan; shutdown signals close it first (ADR-0098)
    app.state.heartbeat = heartbeat  # started and closed by the lifespan
    app.state.provider = provider
    app.state.limits = limit_config
    if mcp_endpoint is not None:
        app.router.routes.insert(0, Route(agent_config.path, mcp_endpoint))  # exactly this path, no redirect to a slash
    app.add_middleware(
        limits.LimitsMiddleware, config=limit_config, agent_path=agent_config.path if agent_config.enabled else None
    )  # spec: request-limits.md
    app.add_middleware(auth.AuthMiddleware, config=auth_config, agents=agents)  # spec: authentication.md
    app.add_middleware(i18n.LanguageMiddleware)  # spec: internationalization.md; outside auth and limits
    # Outermost: request id, trace id and the request record, also for the answers of the auth and limit middleware.
    app.add_middleware(observability.RequestContextMiddleware, log_requests=log_config.format == "json")
    app.state.log_config = log_config
    observability.instrument(app, log_config)  # request spans with ARCVIZ_TELEMETRY (ADR-0114)

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError) -> JSONResponse:
        """A mutation that kept conflicting with concurrent writes (spec: concurrent-mutations.md)."""
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RevisionMismatch)
    async def precondition_failed(request: Request, exc: RevisionMismatch) -> JSONResponse:
        """`If-Match` named a revision that is not the latest any more (spec: collaboration.md, ADR-0094)."""
        return JSONResponse({"detail": str(exc), "revision": exc.revision}, status_code=412)

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden) -> JSONResponse:
        """A visible resource, but the caller's role does not allow the action (spec: workspaces-and-sharing.md)."""
        return JSONResponse({"detail": str(exc)}, status_code=403)

    app.state.provider_error = None
    app.state.agent = None
    app.state.project_agent = None
    app.state.requirements_agent = None

    def get_provider() -> LLMProvider:
        """The injected or lazily created provider; HTTP 503 if it cannot be created (no network call)."""
        if app.state.provider is None:
            try:
                app.state.provider = create_provider()
            except Exception as exc:  # missing key, unknown provider, ...
                app.state.provider_error = str(exc)
                raise HTTPException(503, f"LLM provider not available: {exc}") from exc
        return app.state.provider

    def get_agent(svc: Services) -> DiagramAgent:
        """The diagram agent working on the caller's services (one shared agent, bound per request)."""
        if app.state.agent is None:
            app.state.agent = DiagramAgent(
                get_provider(),
                svc.diagrams,
                models=svc.models,
                **asdict(agent_settings),
            )
        return app.state.agent.bound(svc.diagrams, svc.models)

    def get_requirements_agent() -> RequirementsAgent:
        """The requirements chat agent (stateless: the services come with each call)."""
        if app.state.requirements_agent is None:
            app.state.requirements_agent = RequirementsAgent(
                get_provider(),
                max_rounds=agent_settings.max_rounds,
                history_turns=agent_settings.history_turns,
                token_budget=agent_settings.token_budget,
            )
        return app.state.requirements_agent

    def get_project_agent(svc: Services) -> ProjectAgent:
        """The project agent working on the caller's services (one shared agent, bound per request)."""
        if app.state.project_agent is None:
            app.state.project_agent = ProjectAgent(
                get_provider(),
                svc.diagrams,
                svc.projects,
                svc.models,
                **asdict(agent_settings),
            )
        return app.state.project_agent.bound(svc.diagrams, svc.models, svc.projects)

    def load(svc: Services, diagram_id: str):
        try:
            return svc.diagrams.get(diagram_id)
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc

    def gone(svc: Services, diagram_id: str, exc: StoreError) -> HTTPException:
        """A StoreError of a write: 404 when the diagram does not exist (any more), else re-raised."""
        if isinstance(exc, ConflictError) or svc.diagrams.exists(diagram_id):
            raise exc
        return HTTPException(404, str(exc))

    def check_focus(diagram, focus: str | None) -> str | None:
        """Validate a requested focus; a focus that no longer exists (or that the diagram's renderer cannot
        show) silently falls back to the whole diagram."""
        if focus and (focus not in diagram.nodes or not render.renderer_for(diagram).supports_focus):
            return None
        return focus or None

    def detail(
        svc: Services, diagram_id: str, highlight: list[str] | None = None, focus: str | None = None
    ) -> dict[str, Any]:
        try:
            state = svc.diagrams.state(diagram_id, history_config.page)
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        diagram = state.diagram
        focus = check_focus(diagram, focus)
        role, project_id, workspace_id = svc.diagrams.access(diagram_id)
        return {
            "diagram": diagram.model_dump(mode="json"),
            "revision": state.revision,  # the ETag; send it back as If-Match (spec: collaboration.md)
            "access": {"role": role, "project": project_id, "workspace": workspace_id},
            **render.payload(diagram, highlight=highlight or [], focus=focus),
            "focus": focus,
            "breadcrumb": mermaid.breadcrumb(diagram, focus),
            "versions": [v.model_dump() for v in state.versions],  # the newest page (#114)
            "versions_total": state.versions_total,
            "versions_more": state.versions_more,
            "undo_target": state.undo_target,
            "redo_target": state.redo_target,
            "chat": state.chat,
        }

    @contextmanager
    def placement_errors() -> Iterator[None]:
        """Errors of a placement target (spec: projects.md): unknown project 404, unknown section 400."""
        try:
            yield
        except operations.OperationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ProjectNotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    @contextmanager
    def project_errors() -> Iterator[None]:
        """Project routes: unknown project 404, invalid input 400, not empty 409, conflicts 409 (handler)."""
        try:
            yield
        except ProjectStateError as exc:  # a delete before the retirement (ADR-0198)
            raise HTTPException(409, str(exc)) from exc
        except operations.OperationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ProjectNotEmpty as exc:
            raise HTTPException(409, str(exc)) from exc
        except ConflictError:
            raise
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc

    def project_view(view: ProjectView) -> dict[str, Any]:
        return {
            # never the shares (`roles`): a recipient must not learn who else has access; owners use /access
            "project": view.project.model_dump(mode="json", exclude={"roles"}),
            "tree": view.tree,
            "diagrams": [asdict(d) for d in view.diagrams],
            "role": view.role,
            "revision": view.revision,  # the project's ETag; send it back as If-Match on DELETE (#175)
        }

    # ---- probes ------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Liveness: the process serves requests. No store, provider or network access. A process another replica
        fenced (declared dead, #189) is not live: it places no diagrams any more, and a restart makes it a new
        writer incarnation."""
        fenced = heartbeat.ready()
        if fenced is not None:
            return JSONResponse({"status": "fenced", "error": fenced}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness: the store answers a cheap probe and the LLM provider has credentials (no model call)."""
        checks: dict[str, dict[str, Any]] = {}
        try:
            root.ping()
            checks["store"] = {"ok": True}
        except Exception as exc:
            checks["store"] = {"ok": False, "error": str(exc)}
        try:
            error = get_provider().config_error()
        except HTTPException:
            error = app.state.provider_error
        checks["provider"] = {"ok": True} if error is None else {"ok": False, "error": error}
        fenced = heartbeat.ready()  # a process another replica declared dead must be restarted (#189)
        checks["writer"] = {"ok": True} if fenced is None else {"ok": False, "error": fenced}
        ready = all(check["ok"] for check in checks.values())
        return JSONResponse(
            {"status": "ready" if ready else "unavailable", "checks": checks}, status_code=200 if ready else 503
        )

    # ---- authentication (spec: docs/architecture/specs/authentication.md) ---

    @app.get("/login", include_in_schema=False, response_model=None)
    def login_form(next: str = "/") -> Response:
        if auth_config.mode != "token":  # nothing to log in to: off, or the proxy authenticated already
            return RedirectResponse(auth.safe_next(next), status_code=303)
        return HTMLResponse(auth.login_page(auth.safe_next(next)))

    @app.post("/login", include_in_schema=False, response_model=None)
    async def login(request: Request) -> Response:
        raw = await request.body()
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                fields = json.loads(raw or b"{}")
            except ValueError:
                fields = {}
            fields = fields if isinstance(fields, dict) else {}
        else:
            fields = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        target = auth.safe_next(fields.get("next"))
        if auth_config.mode != "token":
            return RedirectResponse(target, status_code=303)
        candidate = fields.get("token")
        if not isinstance(candidate, str) or not auth.token_matches(auth_config, candidate.strip()):
            return HTMLResponse(auth.login_page(target, error=_("Ungültiges Token.")), status_code=401)
        response = RedirectResponse(target, status_code=303)
        assert auth_config.token is not None
        secure = auth_config.cookie_secure if auth_config.cookie_secure is not None else request.url.scheme == "https"
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.session_value(auth_config.token, int(time.time())),
            max_age=auth.SESSION_MAX_AGE,
            path="/",
            httponly=True,
            samesite="strict",
            secure=secure,
        )
        return response

    @app.post("/logout", include_in_schema=False, response_model=None)
    def logout() -> Response:
        response = RedirectResponse("/login" if auth_config.mode == "token" else "/", status_code=303)
        response.delete_cookie(auth.SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        return response

    # ---- meta --------------------------------------------------------------

    @app.get("/api/me")
    def me(request: Request) -> dict[str, Any]:
        """The caller (spec: personal-workspaces.md): id, display name, e-mail, whether it is the admin, and the id of
        their personal workspace."""
        identity = caller(request)
        admin = auth_config.admin
        return {
            "id": identity.id,
            "name": identity.name,
            "email": identity.email,
            "admin": admin is not None and identity.id == admin.id,
            "auth_mode": auth_config.mode,
            "workspace": personal_workspace_id(identity.id),
        }

    @app.get("/api/config")
    def config(request: Request) -> dict[str, Any]:
        provider = app.state.provider
        user = getattr(request.state, "arcviz_user", None) if auth_config.mode == "proxy" else None
        return {
            "auth": {"mode": auth_config.mode, "user": user},
            "provider": getattr(provider, "name", os.environ.get("ARCVIZ_PROVIDER", "anthropic")),
            "model": getattr(provider, "model", os.environ.get("ARCVIZ_MODEL") or None),
            "provider_ready": provider is not None,
            "provider_error": app.state.provider_error,
            "version": __version__,
            "whats_new": whatsnew.current(__version__, language=i18n.language()),
            "store": root.storage_name,
            "templates": sorted(TEMPLATES),
            "git": root.git_status(),
            "node_kinds": sorted(active_kinds().all_kinds),
            "container_kinds": sorted(active_kinds().container_kinds),
            "edge_kinds": sorted(active_kinds().edge_kinds),
            "mcp": {"enabled": agent_config.enabled, "path": agent_config.path, "oauth": agent_config.oidc is not None},
            "node_roles": dict(active_kinds().node_roles),
            "node_traits": {k: dict(v) for k, v in active_kinds().node_traits.items()},
            "directions": list(DIRECTIONS),
            "diagram_types": {t: type_config(t) for t in DIAGRAM_TYPES},
            "icon_packs": icon_packs(),
            "project_model": {
                "element_kinds": list(ELEMENT_KINDS),
                "max_elements": MAX_ELEMENTS,
                "relation_kinds": list(RELATION_KINDS),
                "max_relations": MAX_RELATIONS,
                "relation_rules": {
                    k: {"source": list(s), "target": list(t)} for k, (s, t, _p) in RELATION_RULES.items()
                },
                "relation_properties": {k: list(p) for k, (_s, _t, p) in RELATION_RULES.items()},
                "property_values": {k: list(v) for k, v in PROPERTY_VALUES.items()},
                "driver_kinds": list(DRIVER_KINDS),
                "driver_levels": list(DRIVER_LEVELS),
                "max_drivers": MAX_DRIVERS,
                "max_driver_title": MAX_DRIVER_TITLE,
                "max_driver_description": MAX_DRIVER_DESCRIPTION,
            },
            "template_types": TEMPLATE_TYPES,
            "limits": {
                "max_body_bytes": limit_config.max_body_bytes,
                "max_import_bytes": limit_config.max_import_bytes,
                "max_chat_chars": limit_config.max_chat_chars,
                "max_proposal_operations": limits.MAX_PROPOSAL_OPERATIONS,
                "max_batch": operations.MAX_BATCH,
                "rate_limits": dict(limit_config.rates),
            },
        }

    def type_config(diagram_type: str) -> dict[str, Any]:
        """What the UI needs to know about a diagram type (spec: docs/architecture/specs/diagram-types.md)."""
        kinds = kinds_for(diagram_type)
        ops = operations.TYPE_OPERATIONS[diagram_type]
        renderer = render.RENDERERS[diagram_type]
        return {
            "uml": UML_REFERENCES[diagram_type],
            "renderer": renderer.name,
            "node_kinds": sorted(kinds.all_kinds),
            "container_kinds": sorted(kinds.container_kinds),
            "edge_kinds": sorted(kinds.edge_kinds),
            "operations": list(ops),
            "templates": templates_of(diagram_type),
            "features": {
                "focus": renderer.supports_focus,
                "direction": "set_direction" in ops,
                "compare": diagram_type in diff.SUPPORTED_TYPES,
                "plantuml": diagram_type in exchange.PLANTUML_TYPES,
                "edge_direction": edge_may_be_undirected(diagram_type),
            },
            "node_fields": _field_table(diagram_type, nodes=True),  # typed fields by node kind (ADR-0177)
            "edge_fields": _field_table(diagram_type, nodes=False),
            "linkable": linkable_table(diagram_type),  # view kinds that may represent model elements
            "relations": relation_table(diagram_type),  # view connections that may show model relations
            "completeness": list(shown_relation_kinds(diagram_type)),  # relation kinds it may declare complete
            **({"node_icons": dict(kinds.node_icons)} if kinds.node_icons else {}),
            **TYPE_EXTRAS.get(diagram_type, {}),
        }

    # ---- the personal workspace and sharing (spec: docs/architecture/specs/personal-workspaces.md) ------

    @app.get("/api/workspace")
    def get_workspace(svc: Svc) -> dict[str, Any]:
        """The caller's personal workspace with the numbers of own and shared projects."""
        return asdict(svc.workspaces.mine())

    def owner_view(owner: str, records: dict[str, Any], caller_owns: bool) -> dict[str, Any]:
        """A project's owner: in full for the owner, by id and display name only for a recipient (never the e-mail
        address, ADR-0101)."""
        person = Person.of(owner, records)
        return asdict(person) if caller_owns else {"id": person.id, "name": person.name}

    def access_view(access: ProjectAccess) -> dict[str, Any]:
        records = users.records(
            [access.owner, *(s.id for s in access.shares), *([access.added] if access.added else [])]
        )
        view = {
            "project": access.project,
            "owner": owner_view(access.owner, records, access.role == "owner"),
            "role": access.role,
            "roles": list(SHARE_ROLES),
            # id and display name only: an e-mail the owner did not type is never revealed (ADR-0101)
            "shares": [{"id": s.id, "name": Person.of(s.id, records).name, "role": s.role} for s in access.shares],
        }
        if access.added is not None:  # what the owner typed and the display name it resolved to (ADR-0091, ADR-0101)
            view["added"] = {"user": access.added_ref, "name": Person.of(access.added, records).name}
        return view

    @app.get("/api/users")
    def suggest_users(svc: Svc, q: str = "") -> dict[str, Any]:
        """Users who signed in before, matching `q` (at least two characters), for the share dialog (ADR-0165): id,
        name and the e-mail address only when it is trusted. Choosing one shares by id."""
        users = svc.users.suggest(q, exclude=svc.actor.id)
        return {"users": [{"id": u.id, "name": u.name, "email": u.email if u.email_trusted else None} for u in users]}

    @app.get("/api/projects/{project_id}/access")
    def get_project_access(svc: Svc, project_id: str) -> dict[str, Any]:
        """The owner, the caller's role and the shares (all of them for owners, the caller's own for others)."""
        with project_errors():
            return access_view(svc.projects.access(project_id))

    @app.post("/api/projects/{project_id}/access")
    def share_project(svc: Svc, project_id: str, req: ShareRequest) -> dict[str, Any]:
        """The project owner shares the project with a known user (id or e-mail) as viewer or editor."""
        with project_errors():
            return access_view(svc.projects.share(project_id, req.user, req.role))

    @app.delete("/api/projects/{project_id}/access/{user_id:path}")
    def revoke_project_share(svc: Svc, project_id: str, user_id: str) -> dict[str, Any]:
        """The project owner revokes a user's share."""
        with project_errors():
            return access_view(svc.projects.revoke(project_id, user_id))

    # ---- diagrams ----------------------------------------------------------

    @app.get("/api/diagrams")
    def list_diagrams(svc: Svc) -> list[dict[str, Any]]:
        return [d.model_dump() for d in svc.diagrams.list_diagrams()]

    @app.post("/api/diagrams", status_code=201)
    def create_diagram(svc: Svc, req: CreateDiagramRequest) -> dict[str, Any]:
        if req.template not in TEMPLATES:
            raise HTTPException(400, f"Unknown template '{req.template}'. Available: {', '.join(sorted(TEMPLATES))}")
        if req.type is not None and req.type not in DIAGRAM_TYPES:
            raise HTTPException(400, f"Unknown diagram type '{req.type}'. Available: {', '.join(DIAGRAM_TYPES)}")
        diagram_id = req.id or operations.slugify(req.title)
        try:
            check_id(diagram_id)
            diagram = create_from_template(req.template, diagram_id, req.title, req.type)
        except (StoreError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        with placement_errors():  # a new id `<id>-<random suffix>` (ADR-0131); the answer names it
            try:
                created = svc.diagrams.create(req.title, diagram_id, diagram, req.project, req.section)
            except ProjectNotFound:
                raise
            except StoreError as exc:
                raise HTTPException(409, str(exc)) from exc
        return detail(svc, created.id)

    @app.post("/api/diagrams/import", status_code=201)
    def import_diagram(svc: Svc, req: ImportRequest) -> dict[str, Any]:
        """Create a new diagram from an exported document (spec: docs/architecture/specs/diagram-exchange.md)."""
        try:
            diagram = exchange.parse_document(req.document)
            model = exchange.parse_document_model(req.document)  # the elements it refers to, if carried (#153)
        except exchange.ExchangeError as exc:
            raise HTTPException(400, f"Invalid document: {exc}") from exc
        title = (req.title or "").strip() or diagram.title
        diagram.updated_at = now_iso()
        if req.id:
            try:
                check_id(req.id)
            except StoreError as exc:
                raise HTTPException(400, str(exc)) from exc
        with placement_errors():  # the requested id or the document's, plus a random suffix (ADR-0131)
            diagram_id, carried = svc.diagrams.import_carrying(
                diagram, model, title, req.id or None, req.project, req.section
            )
        answer = detail(svc, diagram_id)
        if carried is not None:  # what came along, mapped or was dropped (ADR-0161)
            answer["carried"] = carried
        warnings = list(model.warnings) if model is not None else []  # shortened element texts (#212)
        if untitled(title):
            warnings.append(f"The document has no title; the diagram is called „{UNTITLED}“.")
        if warnings:
            answer["warnings"] = warnings
        return answer

    @app.get("/api/diagrams/{diagram_id}")
    def get_diagram(svc: Svc, diagram_id: str, response: Response, focus: Focus = None) -> dict[str, Any]:
        shown = detail(svc, diagram_id, focus=focus)
        etag(response, shown["revision"])
        return shown

    def read(svc: Services, diagram_id: str):
        """The current state for a GET and its revision (the ETag); 404 when the caller may not see it."""
        try:
            return svc.diagrams.read(diagram_id)
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete("/api/diagrams/{diagram_id}", status_code=204)
    def delete_diagram(svc: Svc, diagram_id: str, expected: IfMatch) -> None:
        """Delete the diagram (owner); `If-Match` makes it conditional on the latest revision (412, #175)."""
        try:
            svc.diagrams.delete(diagram_id, expected)
        except RevisionMismatch:
            raise
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/diagrams/{diagram_id}/mermaid")
    def get_mermaid(svc: Svc, diagram_id: str, response: Response, focus: Focus = None) -> dict[str, Any]:
        diagram, revision = read(svc, diagram_id)
        etag(response, revision)
        if render.renderer_for(diagram).name != "mermaid":
            raise HTTPException(400, f"{diagram.type} diagrams are not rendered with Mermaid; use /render.")
        if focus and focus not in diagram.nodes:
            raise HTTPException(404, f"Unknown focus node '{focus}'.")
        return {"mermaid": mermaid.render(diagram, focus=focus or None), "focus": focus or None}

    @app.get("/api/diagrams/{diagram_id}/render")
    def get_render(svc: Svc, diagram_id: str, response: Response, focus: Focus = None) -> dict[str, Any]:
        """A clean rendering (no highlight) in the format of the diagram's renderer."""
        diagram, revision = read(svc, diagram_id)
        etag(response, revision)
        if focus and focus not in diagram.nodes:
            raise HTTPException(404, f"Unknown focus node '{focus}'.")
        focus = check_focus(diagram, focus)
        return {**render.payload(diagram, focus=focus), "focus": focus}

    @app.get("/api/diagrams/{diagram_id}/export", response_model=None)
    def export_diagram(
        svc: Svc, diagram_id: str, format: str = "json", version: int | None = None
    ) -> JSONResponse | PlainTextResponse:
        diagram, revision = read(svc, diagram_id)
        headers = etag_header(revision) if version is None else {}  # the current state only, not a version
        if version is not None:
            try:
                _, diagram = svc.diagrams.version(diagram_id, version)
            except StoreError as exc:
                raise HTTPException(404, str(exc)) from exc
        stem = diagram_id if version is None else f"{diagram_id}-v{version}"
        if format == "json":
            headers["Content-Disposition"] = f'attachment; filename="{stem}.arcviz.json"'
            model = svc.diagrams.model_for_export(diagram_id, diagram, version is not None)  # what it refers to (#153)
            return JSONResponse(exchange.export_document(diagram, model), headers=headers)
        if format == "plantuml":
            headers["Content-Disposition"] = f'attachment; filename="{stem}.puml"'
            try:
                text = exchange.export_plantuml(diagram)
            except exchange.ExchangeError as exc:  # also too deep nesting (any RecursionError of the export)
                raise HTTPException(400, str(exc)) from exc
            return PlainTextResponse(text, headers=headers)
        raise HTTPException(400, f"Unknown export format '{format}'. Available: json, plantuml")

    # ---- projects (spec: docs/architecture/specs/projects.md) ---------------------

    @app.get("/api/projects")
    def list_projects(svc: Svc) -> list[dict[str, Any]]:
        """The caller's own projects and those shared with them (`shared`), each with the caller's role and the
        owner."""
        summaries = svc.projects.list_projects()
        records = users.records([p.owner for p in summaries if p.owner is not None])
        return [
            {**asdict(p), "owner": owner_view(p.owner, records, not p.shared) if p.owner is not None else None}
            for p in summaries
        ]

    @app.post("/api/projects", status_code=201)
    def create_project(svc: Svc, req: CreateProjectRequest) -> dict[str, Any]:
        if req.id:
            try:
                check_id(req.id)
            except StoreError as exc:
                raise HTTPException(400, str(exc)) from exc
        try:
            project = svc.projects.create(req.title, req.id, req.description)
        except operations.OperationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except WorkspaceNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except StoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        return project_view(svc.projects.view(project.id))

    @app.post("/api/projects/import", status_code=201)
    def import_project(svc: Svc, req: ImportProjectRequest) -> dict[str, Any]:
        """Create a new project from a bundle (spec: docs/architecture/specs/project-exchange.md)."""
        try:
            project, warnings = svc.bundles.import_with_warnings(req.bundle, req.title)
        except exchange.ExchangeError as exc:
            raise HTTPException(400, f"Invalid bundle: {exc}") from exc
        except operations.OperationError as exc:  # e.g. the project document would be too large (#212)
            raise HTTPException(400, f"Invalid bundle: {exc}") from exc
        except WorkspaceNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return {**project_view(svc.projects.view(project.id)), "warnings": warnings}

    @app.get("/api/projects/{project_id}/export")
    def export_project(svc: Svc, project_id: str) -> JSONResponse:
        with project_errors():
            bundle = svc.bundles.export(project_id)
        headers = {"Content-Disposition": f'attachment; filename="{project_id}.arcviz-project.json"'}
        return JSONResponse(bundle, headers=headers)

    @app.get("/api/projects/{project_id}")
    def get_project(svc: Svc, project_id: str, response: Response) -> dict[str, Any]:
        with project_errors():
            view = svc.projects.view(project_id)
        etag(response, view.revision)
        return project_view(view)

    @app.patch("/api/projects/{project_id}")
    def update_project(svc: Svc, project_id: str, req: UpdateProjectRequest) -> dict[str, Any]:
        with project_errors():
            if req.title is not None:
                svc.projects.apply(project_id, "rename_project", {"title": req.title})
            if req.description is not None:
                svc.projects.apply(project_id, "set_description", {"description": req.description})
            return project_view(svc.projects.view(project_id))

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(svc: Svc, project_id: str, expected: IfMatch) -> None:
        """Delete an empty project (owner); `If-Match` makes it conditional on the project revision (412, #175)."""
        with project_errors():
            svc.projects.delete(project_id, expected)

    @app.put("/api/projects/{project_id}/vision")
    def set_project_vision(svc: Svc, project_id: str, req: VisionRequest) -> dict[str, Any]:
        """Replace the product vision (editor or owner); all fields empty clears it (spec: project-vision.md)."""
        with project_errors():
            svc.projects.apply(project_id, "set_vision", req.model_dump())
            return project_view(svc.projects.view(project_id))

    @app.post("/api/projects/{project_id}/ops")
    def apply_project_operation(svc: Svc, project_id: str, req: ProjectOperationRequest) -> dict[str, Any]:
        with project_errors():
            result = svc.projects.apply(project_id, req.op, req.args)
            return {"result": result, **project_view(svc.projects.view(project_id))}

    @app.post("/api/projects/{project_id}/sections", status_code=201)
    def create_section(svc: Svc, project_id: str, req: CreateSectionRequest) -> dict[str, Any]:
        with project_errors():
            result = svc.projects.apply(project_id, "add_section", req.model_dump())
            return {"section": result["result"], **project_view(svc.projects.view(project_id))}

    @app.patch("/api/projects/{project_id}/sections/{section_id}")
    def update_section(svc: Svc, project_id: str, section_id: str, req: UpdateSectionRequest) -> dict[str, Any]:
        with project_errors():
            # One project write: a 400 for any part means nothing was changed.
            steps: list[tuple[str, dict[str, Any]]] = []
            if "element" in req.model_fields_set:  # first: a linked section's title follows its element
                steps.append(("link_section", {"section": section_id, "element": req.element}))
            if req.title is not None:
                steps.append(("rename_section", {"section": section_id, "title": req.title}))
            if {"parent", "position"} & req.model_fields_set:
                parent = req.parent
                if "parent" not in req.model_fields_set:  # only a new position: keep the parent
                    current = svc.projects.get(project_id).find_section(section_id)
                    parent = current.parent if current is not None else None
                steps.append(("move_section", {"section": section_id, "parent": parent, "position": req.position}))
            if steps:
                # link_section is the model's (internal); the other steps are public section operations
                svc.projects.apply_all(project_id, steps, internal=True)

            return project_view(svc.projects.view(project_id))

    @app.delete("/api/projects/{project_id}/sections/{section_id}")
    def delete_section(svc: Svc, project_id: str, section_id: str) -> dict[str, Any]:
        """Remove a section; its subsections and diagrams move into its parent (ADR-0054)."""
        with project_errors():
            svc.projects.apply(project_id, "remove_section", {"section": section_id})
            return project_view(svc.projects.view(project_id))

    # ---- the shared project model (spec: docs/architecture/specs/shared-project-model.md) ----------

    @app.get("/api/projects/{project_id}/model")
    def get_project_model(svc: Svc, project_id: str) -> dict[str, Any]:
        with project_errors():
            return svc.models.model(project_id).to_dict()

    @app.get("/api/projects/{project_id}/elements")
    def list_elements(svc: Svc, project_id: str) -> dict[str, Any]:
        """The elements only (the project document): what the inspector needs, without reading every diagram."""
        with project_errors():
            project = svc.projects.get(project_id)
            return {
                "project": project_id,
                "elements": [e.model_dump() for e in project.elements],
                "relations": [r.model_dump() for r in project.relations],
            }

    @app.post("/api/projects/{project_id}/elements", status_code=201)
    def create_element(svc: Svc, project_id: str, req: CreateElementRequest) -> dict[str, Any]:
        with project_errors():
            element = svc.models.add_element(project_id, req.name, req.kind, req.description, req.parent, req.id)
            return {"element": element.model_dump(), "model": svc.models.model(project_id).to_dict()}

    # ---- personal access tokens for agents (spec: docs/architecture/specs/agent-authentication.md) -----------------------

    @contextmanager
    def token_errors() -> Iterator[None]:
        try:
            yield
        except TokenError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/tokens")
    def list_tokens(svc: Svc) -> dict[str, Any]:
        return {
            "tokens": [t.public() for t in svc.tokens.list()],
            "scopes": list(tokens.SCOPES),
            "max_tokens": tokens.MAX_TOKENS,
            "default_days": tokens.DEFAULT_DAYS,
            "max_days": tokens.MAX_DAYS,
        }

    @app.post("/api/tokens", status_code=201)
    def create_token(svc: Svc, req: CreateTokenRequest) -> dict[str, Any]:
        """A new token for the caller; its value is in this answer only (only a hash is stored)."""
        with token_errors():
            created = svc.tokens.create(req.label, req.scope, req.expires_in_days)
        return {"token": created.token, "record": created.record.public()}

    @app.delete("/api/tokens/{token_id}")
    def revoke_token(svc: Svc, token_id: str) -> dict[str, Any]:
        with token_errors():
            svc.tokens.revoke(token_id)
        return {"revoked": token_id}

    # ---- code links (spec: docs/architecture/specs/agent-access.md) -------------------------------------------------

    @app.get("/api/projects/{project_id}/elements/{element_id}/code-refs")
    def list_code_refs(svc: Svc, project_id: str, element_id: str) -> dict[str, Any]:
        with project_errors():
            element = svc.models.element(project_id, element_id)
            return {"element": element.id, "code_refs": [r.model_dump() for r in element.code_refs]}

    @app.post("/api/projects/{project_id}/elements/{element_id}/code-refs", status_code=201)
    def add_code_ref(svc: Svc, project_id: str, element_id: str, req: CodeRefRequest) -> dict[str, Any]:
        with project_errors():
            return svc.models.code_ref_operation(
                project_id, "add_code_ref", {"element": element_id, **req.model_dump()}
            )

    @app.delete("/api/projects/{project_id}/elements/{element_id}/code-refs")
    def remove_code_ref(
        svc: Svc, project_id: str, element_id: str, path: str, repo: str = "", symbol: str = ""
    ) -> dict[str, Any]:
        with project_errors():
            args = {"element": element_id, "path": path, "repo": repo, "symbol": symbol}
            return svc.models.code_ref_operation(project_id, "remove_code_ref", args)

    @app.get("/api/projects/{project_id}/code-owners")
    def code_owners(svc: Svc, project_id: str, path: str, repo: str | None = None) -> dict[str, Any]:
        """The elements that own a repository path through their code links (longest matching link)."""
        with project_errors():
            owners = svc.models.code_owners(project_id, path, repo)
            return {
                "path": path,
                "owners": [{"element": e.model_dump(), "code_ref": r.model_dump()} for e, r in owners],
            }

    @app.patch("/api/projects/{project_id}/elements/{element_id}")
    def update_element(svc: Svc, project_id: str, element_id: str, req: UpdateElementRequest) -> dict[str, Any]:
        with project_errors():
            moved = {"parent": req.parent} if "parent" in req.model_fields_set else {}
            change = svc.models.update_element(project_id, element_id, req.name, req.description, req.kind, **moved)
            return {
                "element": change.element.model_dump(),
                "propagated": change.propagated,
                "failed": change.failed,
                "model": svc.models.model(project_id).to_dict(),
            }

    @app.delete("/api/projects/{project_id}/elements/{element_id}")
    def delete_element(svc: Svc, project_id: str, element_id: str) -> dict[str, Any]:
        with project_errors():
            removal = svc.models.remove_element(project_id, element_id)
            return {
                "element": removal.element,
                "unlinked": removal.unlinked,
                "failed": removal.failed,
                "model": svc.models.model(project_id).to_dict(),
            }

    @app.post("/api/projects/{project_id}/model/sync")
    def sync_project_model(svc: Svc, project_id: str) -> dict[str, Any]:
        with project_errors():
            report = svc.models.sync(project_id)
            return {"changed": report.changed, "failed": report.failed, "model": svc.models.model(project_id).to_dict()}

    # ---- project elements by default (spec: docs/architecture/specs/elements-by-default.md) --------------------

    def adoption_counts(diagrams: list[dict[str, Any]]) -> dict[str, int]:
        return {key: sum(len(d[key]) for d in diagrams) for key in ("linked", "created", "skipped")}

    @app.get("/api/projects/{project_id}/adoption")
    def get_adoption_plan(svc: Svc, project_id: str) -> dict[str, Any]:
        """What **Unverknüpfte übernehmen** would do (nothing is written)."""
        with project_errors():
            planned = svc.models.adoption_plan(project_id)
            return {"diagrams": planned, "counts": adoption_counts(planned)}

    @app.post("/api/projects/{project_id}/adoption")
    def adopt_unlinked_views(svc: Svc, project_id: str) -> dict[str, Any]:
        """Link the unlinked structural views of every diagram to their project elements (creating missing ones)."""
        with project_errors():
            done, failed = svc.models.adopt_all(project_id)
            return {
                "diagrams": done,
                "counts": adoption_counts(done),
                "failed": failed,
                "model": svc.models.model(project_id).to_dict(),
            }

    @app.post("/api/projects/{project_id}/relations", status_code=201)
    def create_relation(svc: Svc, project_id: str, req: CreateRelationRequest) -> dict[str, Any]:
        with project_errors():
            relation = svc.models.add_relation(
                project_id, req.kind, req.source, req.target, req.properties, req.description, req.id
            )
            return {"relation": relation.model_dump(), "model": svc.models.model(project_id).to_dict()}

    @app.patch("/api/projects/{project_id}/relations/{relation_id}")
    def update_relation(svc: Svc, project_id: str, relation_id: str, req: UpdateRelationRequest) -> dict[str, Any]:
        with project_errors():
            change = svc.models.update_relation(
                project_id,
                relation_id,
                req.kind,
                req.source,
                req.target,
                req.properties,
                req.remove_properties,
                req.description,
            )
            return {
                "relation": change.relation.model_dump(),
                "unlinked": change.unlinked,
                "failed": change.failed,
                "model": svc.models.model(project_id).to_dict(),
            }

    @app.delete("/api/projects/{project_id}/relations/{relation_id}")
    def delete_relation(svc: Svc, project_id: str, relation_id: str) -> dict[str, Any]:
        with project_errors():
            removal = svc.models.remove_relation(project_id, relation_id)
            return {
                "relation": removal.relation,
                "unlinked": removal.unlinked,
                "failed": removal.failed,
                "model": svc.models.model(project_id).to_dict(),
            }

    # ---- decision support: stakeholders, scenarios, decisions (spec: decision-support.md) ------------------------

    def support_routes(plural: str, singular: str) -> None:
        """GET/POST `/api/projects/{pid}/<plural>`, PATCH/DELETE `…/<plural>/{id}`: the project operations
        `add_/update_/remove_<singular>` with the body as their arguments (unknown fields answer 400)."""

        def listing(svc: Services, project_id: str) -> dict[str, Any]:
            items = getattr(svc.projects.get(project_id), plural)
            return {"project": project_id, plural: [i.model_dump() for i in items]}

        def create(svc: Svc, project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
            with project_errors():
                result = svc.projects.apply(project_id, f"add_{singular}", body)
                return {singular: result["result"], **listing(svc, project_id)}

        def read(svc: Svc, project_id: str) -> dict[str, Any]:
            with project_errors():
                return listing(svc, project_id)

        def change(svc: Svc, project_id: str, item_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
            with project_errors():
                result = svc.projects.apply(project_id, f"update_{singular}", {singular: item_id, **body})
                return {singular: result["result"], **listing(svc, project_id)}

        def delete(svc: Svc, project_id: str, item_id: str) -> dict[str, Any]:
            with project_errors():
                result = svc.projects.apply(project_id, f"remove_{singular}", {singular: item_id})
                return {singular: result["result"][singular], **listing(svc, project_id)}

        base = f"/api/projects/{{project_id}}/{plural}"
        app.add_api_route(base, read, methods=["GET"], name=f"list_{plural}")
        app.add_api_route(base, create, methods=["POST"], status_code=201, name=f"create_{singular}")
        app.add_api_route(f"{base}/{{item_id}}", change, methods=["PATCH"], name=f"update_{singular}")
        app.add_api_route(f"{base}/{{item_id}}", delete, methods=["DELETE"], name=f"delete_{singular}")

    for plural, singular in (("stakeholders", "stakeholder"), ("scenarios", "scenario"), ("decisions", "decision")):
        support_routes(plural, singular)

    @app.get("/api/projects/{project_id}/decisions/{decision_id}/markdown")
    def decision_as_markdown(svc: Svc, project_id: str, decision_id: str) -> Response:
        """The decision as a Markdown ADR (spec: decision-support.md)."""
        with project_errors():
            project = svc.projects.get(project_id)
            decision = next((d for d in project.decisions if d.id == decision_id), None)
            if decision is None:
                raise HTTPException(404, f"Unknown decision '{decision_id}'.")
            text = decision_markdown(decision, project.drivers, project.elements, project.scenarios)
        name = adr_label(decision.id).lower()
        return Response(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
        )

    # ---- architecture drivers (spec: docs/architecture/specs/architecture-drivers.md) --------------------------

    def drivers_view(svc: Services, project_id: str) -> dict[str, Any]:
        project = svc.projects.get(project_id)
        ranks = {d.id: n for n, d in enumerate(ranked_drivers(project.drivers), start=1)}
        return {
            "project": project_id,
            "drivers": [
                {**d.model_dump(), "rank": ranks[d.id], "qualities": driver_qualities(d.id, project.scenarios)}
                for d in project.drivers
            ],
        }

    @app.get("/api/projects/{project_id}/drivers")
    def list_drivers(svc: Svc, project_id: str) -> dict[str, Any]:
        with project_errors():
            return drivers_view(svc, project_id)

    @app.post("/api/projects/{project_id}/drivers", status_code=201)
    def create_driver(svc: Svc, project_id: str, req: CreateDriverRequest) -> dict[str, Any]:
        with project_errors():
            result = svc.projects.apply(project_id, "add_driver", req.model_dump())
            return {"driver": result["result"], **drivers_view(svc, project_id)}

    @app.patch("/api/projects/{project_id}/drivers/{driver_id}")
    def update_driver(svc: Svc, project_id: str, driver_id: str, req: UpdateDriverRequest) -> dict[str, Any]:
        with project_errors():
            # One project write: a 400 for any part means nothing was changed.
            fields = req.model_dump(exclude={"position"}, exclude_unset=True)
            steps: list[tuple[str, dict[str, Any]]] = [("update_driver", {"driver": driver_id, **fields})]
            if req.position is not None:
                steps.append(("move_driver", {"driver": driver_id, "position": req.position}))
            results = svc.projects.apply_all(project_id, steps)
            return {"driver": results[-1]["result"], **drivers_view(svc, project_id)}

    @app.delete("/api/projects/{project_id}/drivers/{driver_id}")
    def delete_driver(svc: Svc, project_id: str, driver_id: str) -> dict[str, Any]:
        with project_errors():
            result = svc.projects.apply(project_id, "remove_driver", {"driver": driver_id})
            return {"driver": result["result"]["driver"], **drivers_view(svc, project_id)}

    # ---- requirements (spec: docs/architecture/specs/requirements.md) -----------------------------------------

    @app.get("/api/projects/{project_id}/requirements")
    def list_requirements(
        svc: Svc,
        project_id: str,
        status: str | None = None,
        kind: str | None = None,
        theme: str | None = None,
        stakeholder: str | None = None,
        coverage: str | None = None,
        step: str | None = None,
        q: str | None = None,
        suggested: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with project_errors():
            return {
                "project": project_id,
                **svc.requirements.page(
                    project_id, status, kind, theme, stakeholder, coverage, step, q, suggested, limit, offset
                ),
            }

    @app.post("/api/projects/{project_id}/requirements", status_code=201)
    def create_requirement(svc: Svc, project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        with project_errors():
            created = svc.requirements.apply(project_id, "add_requirement", body)
            return {"requirement": svc.requirements.get(project_id, created["result"]["id"])}

    # the chat routes come before `/requirements/{requirement_id}`, which would take `chat` for an id
    @app.post("/api/projects/{project_id}/requirements/chat")
    async def requirements_chat(svc: TurnSvc, project_id: str, req: RequirementsChatRequest) -> StreamingResponse:
        """One turn of the requirements chat as server-sent events (spec: requirements-workshop.md)."""
        check_chat_length(req.message)

        def prepare() -> RequirementsAgent:  # store calls stay off the event loop (#112)
            with project_errors():
                svc.projects.require(project_id, "editor")  # a viewer gets a 403, not a stream (ADR-0084)
            return get_requirements_agent()

        agent = await run_in_threadpool(prepare)

        def events() -> Iterator[str]:
            turn = agent.chat(svc, project_id, req.message)
            # closing(): closing `events` closes the turn at once (its `finally` persists it, ADR-0126)
            with svc.live.chatting(diagram_id=None, project_id=project_id), closing(turn):
                for event in turn:
                    yield _sse(event)

        return TurnResponse(events())

    @app.get("/api/projects/{project_id}/requirements/chat")
    def get_requirements_chat(svc: Svc, project_id: str) -> dict[str, Any]:
        with project_errors():
            return {"chat": svc.requirement_chat.entries(project_id)}

    @app.delete("/api/projects/{project_id}/requirements/chat", status_code=204)
    def clear_requirements_chat(svc: Svc, project_id: str) -> None:
        with project_errors():
            svc.requirement_chat.clear(project_id)

    @app.get("/api/projects/{project_id}/requirements/{requirement_id}")
    def read_requirement(svc: Svc, project_id: str, requirement_id: str) -> dict[str, Any]:
        with project_errors():
            return {"requirement": svc.requirements.get(project_id, requirement_id)}

    @app.patch("/api/projects/{project_id}/requirements/{requirement_id}")
    def update_requirement(
        svc: Svc,
        project_id: str,
        requirement_id: str,
        body: dict[str, Any] = Body(...),  # noqa: B008
    ) -> dict[str, Any]:
        with project_errors():
            svc.requirements.apply(project_id, "update_requirement", {"requirement": requirement_id, **body})
            return {"requirement": svc.requirements.get(project_id, requirement_id)}

    @app.delete("/api/projects/{project_id}/requirements/{requirement_id}")
    def delete_requirement(svc: Svc, project_id: str, requirement_id: str) -> dict[str, Any]:
        with project_errors():
            return {"requirement": svc.requirements.remove(project_id, requirement_id)["result"]["requirement"]}

    @app.post("/api/projects/{project_id}/requirements/import")
    def import_requirements(svc: Svc, project_id: str, req: ImportRequirementsRequest) -> dict[str, Any]:
        """Pour a list of requirements in (lines, Markdown or CSV, parsed without a model); idempotent. `dry_run` answers
        what would happen and writes nothing (spec: requirements-workshop.md)."""
        with project_errors():
            try:
                parsed = requirement_import.parse(req.text, req.format, source=req.source)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            people = {s.name.casefold(): s.id for s in svc.projects.get(project_id).stakeholders}
            prepared = requirement_import.prepare(parsed.items, people)
            outcome = svc.requirements.import_items(project_id, prepared.items, client="import", dry_run=req.dry_run)

            def source_row(position: int) -> int:
                return prepared.rows[position - 1] if 0 < position <= len(prepared.rows) else position

            def rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [{**e, "row": source_row(e["row"])} for e in entries]

            return {
                "project": project_id,
                "format": parsed.format,
                "dry_run": req.dry_run,
                "parsed": len(parsed.items),
                "added": outcome["added"],
                "skipped_duplicates": rows(outcome["skipped_duplicates"]),
                "rejected": rows(outcome["rejected"]),
                "problems": sorted([*parsed.problems, *prepared.problems], key=lambda p: p["row"]),
            }

    @app.post("/api/projects/{project_id}/requirements/refine")
    def refine_requirements(svc: Svc, project_id: str, req: RefineRequirementsRequest) -> dict[str, Any]:
        """Refine one chunk (at most 25) of requirements with one model call and store the result as suggestions that a
        person accepts; `remaining` counts the raw ones without a suggestion, so the client loops until it is 0 (spec:
        requirements-workshop.md, ADR-0190, ADR-0192)."""
        with project_errors():
            return requirement_workshop.refine_chunk(svc, get_provider(), project_id, req.ids, req.limit)

    @app.post("/api/projects/{project_id}/requirements/derive")
    def derive_requirements(svc: Svc, project_id: str, req: DeriveRequirementsRequest) -> dict[str, Any]:
        """Derive drivers and scenarios from one chunk (at most 30) of refined requirements with one model call and store
        the result as a **project proposal** that a person applies in part; nothing is written to the project. `remaining`
        counts the refined requirements nothing covers and no pending proposal serves (spec: requirements-workshop.md)."""
        with project_errors():
            return requirement_workshop.derive_chunk(svc, get_provider(), project_id, req.ids, req.theme, req.limit)

    @app.post("/api/projects/{project_id}/requirements/accept")
    def accept_requirement_suggestions(svc: Svc, project_id: str, req: AcceptSuggestionsRequest) -> dict[str, Any]:
        with project_errors():
            return svc.requirements.accept(project_id, [i.model_dump(exclude_none=True) for i in req.items])

    @app.post("/api/projects/{project_id}/requirements/status")
    def set_requirement_status(svc: Svc, project_id: str, req: SetRequirementStatusRequest) -> dict[str, Any]:
        with project_errors():
            return svc.requirements.set_status_many(project_id, req.ids, req.status, req.duplicate_of, req.reason)

    @app.post("/api/projects/{project_id}/requirements/delete")
    def delete_requirements(svc: Svc, project_id: str, req: DeleteRequirementsRequest) -> dict[str, Any]:
        """Delete several requirements in one write (spec: graded-deletion.md, ADR-0198)."""
        with project_errors():
            return svc.requirements.remove_many(project_id, req.ids)

    @app.post("/api/projects/{project_id}/requirements/{requirement_id}/reject-suggestion")
    def reject_requirement_suggestion(svc: Svc, project_id: str, requirement_id: str) -> dict[str, Any]:
        with project_errors():
            svc.requirements.apply(project_id, "reject_suggestion", {"requirement": requirement_id})
            return {"requirement": svc.requirements.get(project_id, requirement_id)}

    @app.patch("/api/projects/{project_id}/requirements/{requirement_id}/issues/{index}")
    def resolve_requirement_issue(
        svc: Svc, project_id: str, requirement_id: str, index: int, req: ResolveIssueRequest
    ) -> dict[str, Any]:
        with project_errors():
            svc.requirements.apply(
                project_id,
                "resolve_issue",
                {"requirement": requirement_id, "index": index, "resolved": req.resolved},
            )
            return {"requirement": svc.requirements.get(project_id, requirement_id)}

    @app.get("/api/projects/{project_id}/traceability")
    def traceability(svc: Svc, project_id: str) -> dict[str, Any]:
        with project_errors():
            return {"project": project_id, **svc.requirements.traceability(project_id)}

    def linked_detail(svc: Services, diagram_id: str, updated: Any, focus: str | None) -> dict[str, Any]:
        return {
            **detail(svc, diagram_id, highlight=updated.result.get("touched", []), focus=focus),
            "result": updated.result,
        }

    @app.post("/api/diagrams/{diagram_id}/link")
    def link_view_element(
        svc: Svc, diagram_id: str, req: LinkRequest, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        load(svc, diagram_id)
        with project_errors():
            try:
                updated = svc.models.link(diagram_id, req.view, req.element, expected)
            except StoreError as exc:
                raise gone(svc, diagram_id, exc) from exc
        return linked_detail(svc, diagram_id, updated, focus)

    @app.post("/api/diagrams/{diagram_id}/unlink")
    def unlink_view_element(
        svc: Svc, diagram_id: str, req: UnlinkRequest, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        load(svc, diagram_id)
        with project_errors():
            try:
                updated = svc.models.unlink(diagram_id, req.view, expected)
            except StoreError as exc:
                raise gone(svc, diagram_id, exc) from exc
        return linked_detail(svc, diagram_id, updated, focus)

    @app.post("/api/diagrams/{diagram_id}/link-relation")
    def link_view_relation(
        svc: Svc, diagram_id: str, req: LinkRelationRequest, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        """Let an edge or message show a relation of the project model (spec: model-relations.md)."""
        load(svc, diagram_id)
        with project_errors():
            try:
                updated = svc.models.link_relation(diagram_id, req.view, req.relation, expected)
            except StoreError as exc:
                raise gone(svc, diagram_id, exc) from exc
        return linked_detail(svc, diagram_id, updated, focus)

    @app.post("/api/diagrams/{diagram_id}/hidden-relations")
    def hide_view_relation(
        svc: Svc, diagram_id: str, req: HideRelationRequest, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        """Mark a relation as intentionally left out of the diagram, or not any more (spec: model-relations.md)."""
        load(svc, diagram_id)
        with project_errors():
            try:
                updated = svc.models.hide_relation(diagram_id, req.relation, req.hidden, expected)
            except StoreError as exc:
                raise gone(svc, diagram_id, exc) from exc
        return linked_detail(svc, diagram_id, updated, focus)

    @app.post("/api/diagrams/{diagram_id}/unlink-relation")
    def unlink_view_relation(
        svc: Svc, diagram_id: str, req: UnlinkRequest, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        load(svc, diagram_id)
        with project_errors():
            try:
                updated = svc.models.unlink_relation(diagram_id, req.view, expected)
            except StoreError as exc:
                raise gone(svc, diagram_id, exc) from exc
        return linked_detail(svc, diagram_id, updated, focus)

    @app.post("/api/diagrams/{diagram_id}/promote", status_code=201)
    def promote_view_element(svc: Svc, diagram_id: str, req: PromoteRequest, focus: Focus = None) -> dict[str, Any]:
        load(svc, diagram_id)
        with project_errors():
            try:
                element, updated = svc.models.promote(diagram_id, req.view, req.kind, req.description)
            except StoreError as exc:
                raise gone(svc, diagram_id, exc) from exc
        return {"element": element.model_dump(), **linked_detail(svc, diagram_id, updated, focus)}

    @app.get("/api/projects/{project_id}/diagrams")
    def list_project_diagrams(svc: Svc, project_id: str) -> list[dict[str, Any]]:
        with project_errors():
            return [asdict(d) for d in svc.projects.view(project_id).diagrams]

    @app.post("/api/projects/{project_id}/diagrams/{diagram_id}/move")
    def move_diagram(svc: Svc, project_id: str, diagram_id: str, req: MoveDiagramRequest) -> dict[str, Any]:
        """Move a member diagram within its project or into another project (and section, position)."""
        with project_errors():
            member = svc.projects.get(project_id).member(diagram_id)
            if member is None:
                raise HTTPException(404, f"Diagram '{diagram_id}' is not in project '{project_id}'.")
            target = req.project or project_id
            section = req.section
            if target == project_id and "section" not in req.model_fields_set:
                section = member.section  # only a new position
            if req.position is not None and req.position < 0:
                raise HTTPException(400, f"Invalid position {req.position}: use 0 for the first place.")
            placed, _moved, carried = svc.diagrams.move(diagram_id, target, section, req.position)
            answer = project_view(svc.projects.view(placed))
            if carried is not None:  # into another project: what came along (#153, ADR-0161)
                answer["carried"] = carried
            return answer

    # ---- project chat (spec: docs/architecture/specs/project-chat.md) ------------------

    def check_chat_length(message: str) -> None:
        if limit_config.max_chat_chars and len(message) > limit_config.max_chat_chars:
            raise HTTPException(
                413, f"Chat message too long ({len(message)} characters, max {limit_config.max_chat_chars})."
            )

    def project_member(svc: Services, project_id: str, diagram_id: str) -> None:
        """404 unless the diagram is a member of the project."""
        with project_errors():
            if svc.projects.get(project_id).member(diagram_id) is None:
                raise HTTPException(404, f"Diagram '{diagram_id}' is not in project '{project_id}'.")

    @app.post("/api/projects/{project_id}/chat")
    async def project_chat(svc: TurnSvc, project_id: str, req: ProjectChatRequest) -> StreamingResponse:
        """One project turn as server-sent events: the model works across the project's diagrams."""
        check_chat_length(req.message)

        def prepare() -> ProjectAgent:  # store calls stay off the event loop (#112)
            with project_errors():
                svc.projects.require(project_id, "editor")  # a viewer gets a 403, not a stream (ADR-0084)
            return get_project_agent(svc)

        agent = await run_in_threadpool(prepare)

        def events() -> Iterator[str]:
            turn = agent.chat(
                project_id, req.message, active=req.active, focus=req.focus, propose=req.mode == "propose"
            )
            # closing(): closing `events` closes the turn at once (its `finally` persists it, ADR-0126)
            with svc.live.chatting(diagram_id=req.active, project_id=project_id), closing(turn):  # presence
                for event in turn:
                    yield _sse(event)

        return TurnResponse(events())

    @app.get("/api/projects/{project_id}/chat")
    def get_project_chat(svc: Svc, project_id: str) -> dict[str, Any]:
        with project_errors():
            svc.projects.get(project_id)
            return {"chat": svc.projects.chat(project_id)}

    @app.delete("/api/projects/{project_id}/chat", status_code=204)
    def clear_project_chat(svc: Svc, project_id: str) -> None:
        with project_errors():
            svc.projects.clear_chat(project_id)

    @app.post("/api/projects/{project_id}/chat/compact")
    def compact_project_chat(svc: Svc, project_id: str) -> dict[str, Any]:
        """Fold the older project transcript into a summary now (the agent also does this automatically)."""
        with project_errors():
            svc.projects.require(project_id, "editor")
        agent = get_project_agent(svc)
        try:
            compacted = agent.compact(project_id)
        except Exception as exc:
            raise HTTPException(502, f"Compaction failed: {exc}") from exc
        return {"compacted": compacted, "chat": svc.projects.chat(project_id)}

    # ---- stored proposals of agents (spec: docs/architecture/specs/agent-access.md, ADR-0172) ------------------------

    @app.get("/api/projects/{project_id}/proposals")
    def list_stored_proposals(svc: Svc, project_id: str, status: str | None = None) -> dict[str, Any]:
        with project_errors():
            found = svc.proposals.list(project_id, status)
            return {"project": project_id, "proposals": [p.model_dump(mode="json") for p in found]}

    @app.get("/api/projects/{project_id}/proposals/{proposal_id}")
    def get_stored_proposal(svc: Svc, project_id: str, proposal_id: str) -> dict[str, Any]:
        with project_errors():
            return {"proposal": svc.proposals.get(project_id, proposal_id).model_dump(mode="json")}

    @app.post("/api/projects/{project_id}/proposals/{proposal_id}/apply")
    def apply_stored_proposal(
        svc: Svc, project_id: str, proposal_id: str, req: ProposalDecisionRequest | None = None
    ) -> dict[str, Any]:
        """Replay an agent's proposal on its diagram as one version; 409 when the diagram changed since (the proposal
        is then `stale`). A project proposal applies its items (`accept`: their keys, default every pending one)."""
        with project_errors():
            try:
                applied = svc.proposals.apply(project_id, proposal_id, req.accept if req else None)
            except proposal.ProposalConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"proposal": applied.model_dump(mode="json")}

    @app.post("/api/projects/{project_id}/proposals/{proposal_id}/discard")
    def discard_stored_proposal(
        svc: Svc, project_id: str, proposal_id: str, req: ProposalDecisionRequest | None = None
    ) -> dict[str, Any]:
        with project_errors():
            discarded = svc.proposals.discard(project_id, proposal_id, req.keys if req else None)
            return {"proposal": discarded.model_dump(mode="json")}

    @app.post("/api/projects/{project_id}/proposal/apply")
    def apply_project_proposal(
        svc: Svc, project_id: str, req: ProjectProposalRequest, focus: Focus = None
    ) -> dict[str, Any]:
        """Confirm the proposal of a project turn (one diagram, ADR-0060); the outcome goes into the project
        transcript."""
        if len(req.operations) > limits.MAX_PROPOSAL_OPERATIONS:
            raise HTTPException(
                413,
                f"Too many operations in the proposal ({len(req.operations)}, max {limits.MAX_PROPOSAL_OPERATIONS}).",
            )
        # the transcript incarnation, read before the membership check: the note goes only there (ADR-0127)
        generation = svc.projects.chat_generation(project_id)
        project_member(svc, project_id, req.diagram)
        load(svc, req.diagram)

        def note(entries: list[dict[str, Any]]) -> bool:
            try:
                moved = not svc.projects.holds(project_id, req.diagram)  # moved to another project during the replay
            except (Forbidden, ProjectNotFound):
                moved = True  # the caller may only view the project now, or it is gone: no note there
            if moved:
                logger.info("Proposal outcome not noted: the diagram left the project meanwhile")
                return True  # the change itself is applied and versioned; its note does not belong here any more
            return svc.projects.append_chat(project_id, entries, generation)

        try:
            _, version, changed = proposal.apply_proposal(
                svc.diagrams,
                req.diagram,
                req.operations,
                req.base,
                req.result,
                label=req.label,
                note=note,
                models=svc.models,
            )
        except proposal.ProposalConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except StoreError as exc:
            raise gone(svc, req.diagram, exc) from exc
        return {
            "version": version.model_dump(),
            "changed": changed,
            **detail(svc, req.diagram, highlight=changed, focus=focus),
        }

    @app.post("/api/projects/{project_id}/proposal/discard")
    def discard_project_proposal(svc: Svc, project_id: str, req: ProjectDiscardRequest) -> dict[str, Any]:
        note = {"role": "assistant", "content": proposal.DISCARDED_NOTE, "proposal_outcome": "discarded"}
        if req.diagram is not None:
            project_member(svc, project_id, req.diagram)
            note["diagram"] = req.diagram
        with project_errors():
            if not svc.projects.append_chat(project_id, [note]):
                raise HTTPException(404, f"Project '{project_id}' not found.")
            return {"chat": svc.projects.chat(project_id)}

    # ---- architecture review ---------------------------------------------------

    def project_snapshot(svc: Services, project_id: str) -> project_review.ProjectSnapshot:
        with project_errors():
            project, members = svc.projects.load_members(project_id)
            box = svc.requirements.box(project_id)
        return project_review.ProjectSnapshot(project, members, box)

    @app.get("/api/projects/{project_id}/checks")
    def run_project_checks(svc: Svc, project_id: str) -> dict[str, Any]:
        """Project rules plus the rule checks of every member diagram (spec: project-review.md)."""
        return project_review.checks(project_snapshot(svc, project_id))

    @app.post("/api/projects/{project_id}/review")
    def run_project_review(svc: Svc, project_id: str) -> dict[str, Any]:
        """The project checks plus one LLM review of the whole project; a failed model call is reported in
        `llm` and still returns the rule findings; nothing is written (spec: project-review.md)."""
        snapshot = project_snapshot(svc, project_id)
        return project_review.review_project(get_provider(), snapshot)

    @app.post("/api/projects/{project_id}/scenarios/{scenario_id}/analysis")
    def analyse_scenario(svc: Svc, project_id: str, scenario_id: str) -> dict[str, Any]:
        """The what-if analysis of a scenario: one model call, nothing written (spec: decision-support.md, ADR-0167);
        a failed call answers `llm.ok = false` and no options."""
        snapshot = project_snapshot(svc, project_id)
        scenario = next((s for s in snapshot.project.scenarios if s.id == scenario_id), None)
        if scenario is None:
            raise HTTPException(404, f"Unknown scenario '{scenario_id}'.")
        return scenario_analysis.analyse(get_provider(), snapshot, scenario)

    @app.post("/api/projects/{project_id}/scenarios/qualities")
    def suggest_scenario_qualities(svc: Svc, project_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        """Suggest the ISO 25010 characteristics of the scenario text in the body (title, kind, the six parts): one model
        call, nothing written (spec: iso25010-qualities.md, ADR-0202); a failed or useless answer is `llm.ok = false`."""
        with project_errors():
            svc.projects.get(project_id)  # access and existence (404), as for every project route
        if not quality_suggestion.has_text(body):
            raise HTTPException(400, "Give the scenario a title or at least one of its parts.")
        return quality_suggestion.suggest(get_provider(), body)

    @app.get("/api/diagrams/{diagram_id}/checks")
    def run_checks(svc: Svc, diagram_id: str) -> dict[str, Any]:
        """Deterministic rule checks of the current diagram (spec: docs/architecture/specs/rule-checks.md)."""
        diagram = load(svc, diagram_id)
        kind = rules.diagram_type(diagram)
        return {
            "diagram_type": kind,
            "rules": [{"id": r.id, "title": r.title, "category": r.category} for r in rules.rules_for(kind)],
            "findings": [f.model_dump() for f in rules.run_checks(diagram)],
        }

    @app.post("/api/diagrams/{diagram_id}/review")
    def run_review(svc: Svc, diagram_id: str) -> dict[str, Any]:
        """Rule checks plus an LLM review of the current diagram (spec: docs/architecture/specs/llm-review.md).

        A failed model call is reported in `llm` and still returns the rule findings; nothing is written."""
        diagram = load(svc, diagram_id)
        decisions = None
        with suppress(
            operations.OperationError, StoreError
        ):  # the accepted decisions of its project (decision-support.md)
            decisions = decision_lines(svc.models.project(svc.models.project_of(diagram_id)).decisions)
        return review.review(get_provider(), diagram, decisions)

    # ---- direct operations (no LLM) ---------------------------------------

    @app.post("/api/diagrams/{diagram_id}/ops")
    def apply_operation(
        svc: Svc, diagram_id: str, req: OperationRequest, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        load(svc, diagram_id)
        if req.op in operations.INTERNAL_OPERATIONS:  # the model service passes the element (ADR-0071)
            route = "link-relation" if req.op in operations.RELATION_OPERATIONS else "link"
            route = "hidden-relations" if req.op == "set_relation_hidden" else route
            raise HTTPException(
                400, f"'{req.op}' is made through the project model: POST /api/diagrams/{diagram_id}/{route}."
            )
        label = req.label or f"{req.op} {json.dumps(req.args, ensure_ascii=False)}"

        try:  # re-applied on the newer state when another request wrote in between (#65); new structural views become
            # project elements in the same version (spec: elements-by-default.md, ADR-0164)
            updated = svc.models.apply_operation(diagram_id, req.op, req.args, label, expected)
        except operations.OperationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except StoreError as exc:
            raise gone(svc, diagram_id, exc) from exc
        assert updated.version is not None
        result = dict(updated.result)
        adoption = result.pop("adoption", None)
        return {
            "result": result,
            "version": updated.version.model_dump(),
            **({"adoption": adoption} if adoption else {}),
            **detail(svc, diagram_id, highlight=result.get("touched"), focus=focus),
        }

    # ---- versions ----------------------------------------------------------

    @app.get("/api/diagrams/{diagram_id}/versions")
    def list_versions(
        svc: Svc,
        diagram_id: str,
        before: Annotated[int | None, Query(ge=1, description="Only versions older than this one.")] = None,
        limit: Annotated[int | None, Query(ge=1, le=MAX_VERSIONS_PAGE, description="The newest this many.")] = None,
    ) -> list[dict[str, Any]]:
        """Every version (ascending); with `before` and/or `limit` one page of them (#114)."""
        load(svc, diagram_id)
        if before is not None and limit is None:
            limit = history_config.page
        return [v.model_dump() for v in svc.diagrams.versions(diagram_id, before, limit)]

    @app.post("/api/diagrams/{diagram_id}/versions", status_code=201)
    def create_snapshot(
        svc: Svc, diagram_id: str, req: SnapshotRequest, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        load(svc, diagram_id)
        try:
            version = svc.diagrams.snapshot(diagram_id, req.label, expected)
        except StoreError as exc:
            raise gone(svc, diagram_id, exc) from exc
        return {"version": version.model_dump(), **detail(svc, diagram_id, focus=focus)}

    @app.get("/api/diagrams/{diagram_id}/versions/{version}")
    def get_version(svc: Svc, diagram_id: str, version: int, focus: Focus = None) -> dict[str, Any]:
        load(svc, diagram_id)
        try:
            info, diagram = svc.diagrams.version(diagram_id, version)
        except ConflictError:
            raise  # pruned meanwhile: 409 (ADR-0130)
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        focus = check_focus(diagram, focus)
        return {
            "meta": info.model_dump(),
            "diagram": diagram.model_dump(mode="json"),
            **render.payload(diagram, focus=focus),
            "focus": focus,
        }

    @app.get("/api/diagrams/{diagram_id}/compare")
    def compare_versions(
        svc: Svc, diagram_id: str, base: int, target: int | None = None, focus: Focus = None
    ) -> dict[str, Any]:
        """Diff of version `base` against version `target` (default: the current state), as one marked render."""
        current = load(svc, diagram_id)
        try:
            base_info, old = svc.diagrams.version(diagram_id, base)
            target_info, new = svc.diagrams.version(diagram_id, target) if target is not None else (None, current)
        except ConflictError:
            raise  # pruned meanwhile: 409 (ADR-0130)
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        changes = diff.compare(old, new)
        view, marks, edge_marks = diff.merged(old, new, changes)
        focus = check_focus(view, focus)
        return {
            "base": base_info.model_dump(),
            "target": target_info.model_dump() if target_info is not None else None,
            "diff": changes.to_dict(),
            **render.payload(view, focus=focus, marks=marks, edge_marks=edge_marks),
            "focus": focus,
        }

    @app.post("/api/diagrams/{diagram_id}/versions/{version}/restore")
    def restore_version(
        svc: Svc, diagram_id: str, version: int, expected: IfMatch, focus: Focus = None
    ) -> dict[str, Any]:
        load(svc, diagram_id)
        try:
            info = svc.diagrams.restore(diagram_id, version, expected)
        except ConflictError:
            raise
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"version": info.model_dump(), **detail(svc, diagram_id, focus=focus)}

    @app.post("/api/diagrams/{diagram_id}/undo")
    def undo(svc: Svc, diagram_id: str, expected: IfMatch, focus: Focus = None) -> dict[str, Any]:
        load(svc, diagram_id)
        try:
            version = svc.diagrams.undo(diagram_id, expected)
        except StoreError as exc:
            raise gone(svc, diagram_id, exc) from exc
        if version is None:
            raise HTTPException(409, "Nothing to undo.")
        return {"version": version.model_dump(), **detail(svc, diagram_id, focus=focus)}

    @app.post("/api/diagrams/{diagram_id}/redo")
    def redo(svc: Svc, diagram_id: str, expected: IfMatch, focus: Focus = None) -> dict[str, Any]:
        load(svc, diagram_id)
        try:
            version = svc.diagrams.redo(diagram_id, expected)
        except StoreError as exc:
            raise gone(svc, diagram_id, exc) from exc
        if version is None:
            raise HTTPException(409, "Nothing to redo.")
        return {"version": version.model_dump(), **detail(svc, diagram_id, focus=focus)}

    # ---- chat --------------------------------------------------------------

    @app.post("/api/diagrams/{diagram_id}/chat")
    async def chat(svc: TurnSvc, diagram_id: str, req: ChatRequest) -> StreamingResponse:
        if limit_config.max_chat_chars and len(req.message) > limit_config.max_chat_chars:
            raise HTTPException(
                413, f"Chat message too long ({len(req.message)} characters, max {limit_config.max_chat_chars})."
            )

        def prepare() -> DiagramAgent:  # store calls stay off the event loop (#112)
            load(svc, diagram_id)
            svc.diagrams.require(diagram_id, "editor")  # a viewer gets a 403, not a stream (ADR-0084)
            return get_agent(svc)

        agent = await run_in_threadpool(prepare)

        def events() -> Iterator[str]:
            turn = agent.chat(diagram_id, req.message, focus=req.focus, propose=req.mode == "propose")
            # closing(): closing `events` closes the turn at once (its `finally` persists it, ADR-0126)
            with svc.live.chatting(diagram_id=diagram_id), closing(turn):  # presence (spec: collaboration.md)
                for event in turn:
                    yield _sse(event)

        return TurnResponse(events())

    @app.post("/api/diagrams/{diagram_id}/proposal/apply")
    def apply_proposal(svc: Svc, diagram_id: str, req: ProposalRequest, focus: Focus = None) -> dict[str, Any]:
        """Confirm a proposal from a propose-mode turn: replay its operations and save one version."""
        if len(req.operations) > limits.MAX_PROPOSAL_OPERATIONS:
            raise HTTPException(
                413,
                f"Too many operations in the proposal ({len(req.operations)}, max {limits.MAX_PROPOSAL_OPERATIONS}).",
            )
        load(svc, diagram_id)
        try:
            _, version, changed = proposal.apply_proposal(
                svc.diagrams,
                diagram_id,
                req.operations,
                req.base,
                req.result,
                label=req.label,
                models=svc.models,
            )
        except proposal.ProposalConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except StoreError as exc:
            raise gone(svc, diagram_id, exc) from exc
        return {
            "version": version.model_dump(),
            "changed": changed,
            **detail(svc, diagram_id, highlight=changed, focus=focus),
        }

    @app.post("/api/diagrams/{diagram_id}/proposal/discard")
    def discard_proposal(svc: Svc, diagram_id: str, focus: Focus = None) -> dict[str, Any]:
        load(svc, diagram_id)
        proposal.discard_proposal(svc.diagrams, diagram_id)
        return detail(svc, diagram_id, focus=focus)

    @app.delete("/api/diagrams/{diagram_id}/chat", status_code=204)
    def clear_chat(svc: Svc, diagram_id: str) -> None:
        load(svc, diagram_id)
        svc.diagrams.clear_chat(diagram_id)

    @app.post("/api/diagrams/{diagram_id}/chat/compact")
    def compact_chat(svc: Svc, diagram_id: str) -> dict[str, Any]:
        """Fold the older transcript into a summary now (the agent also does this automatically)."""
        load(svc, diagram_id)
        svc.diagrams.require(diagram_id, "editor")
        agent = get_agent(svc)
        try:
            compacted = agent.compact(diagram_id)
        except Exception as exc:
            raise HTTPException(502, f"Compaction failed: {exc}") from exc
        return {"compacted": compacted, "chat": svc.diagrams.chat(diagram_id)}

    # ---- live updates and presence (spec: docs/architecture/specs/collaboration.md) -------------

    async def live_stream(open_stream: Any, target: str, session: str | None, svc: Services) -> StreamingResponse:
        """Open the caller's stream through the service (it checks access and creates the watch itself)."""
        try:
            sub = await run_in_threadpool(open_stream, target, session, asyncio.get_running_loop())
        except live.StreamLimit as exc:
            status = 429 if exc.scope == "user" else 503
            raise HTTPException(status, str(exc), headers={"Retry-After": "30"}) from exc
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc

        async def body():
            async for event in live.run_stream(hub, sub, svc.live.recheck):
                yield ": keep-alive\n\n" if event is None else _sse(event)

        # The stream unsubscribes when its body ends; a client gone before the body ever started must not leave a
        # phantom viewer counting toward the caps.
        return LiveResponse(body(), lambda: hub.unsubscribe(sub), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/diagrams/{diagram_id}/live")
    async def diagram_live(svc: TurnSvc, diagram_id: str, session: str | None = None) -> StreamingResponse:
        """Server-sent events about the diagram, its project and who is there (viewers only; 404 otherwise)."""
        return await live_stream(svc.live.open_diagram, diagram_id, session, svc)

    @app.get("/api/projects/{project_id}/live")
    async def project_live(svc: TurnSvc, project_id: str, session: str | None = None) -> StreamingResponse:
        """Server-sent events about the project and who is there, while no diagram is open (viewers only)."""
        return await live_stream(svc.live.open_project, project_id, session, svc)

    @app.post("/api/diagrams/{diagram_id}/presence", status_code=204)
    def update_presence(svc: Svc, diagram_id: str, req: PresenceRequest) -> None:
        """The element the caller's live stream `session` on this diagram has selected (null: none)."""
        try:
            svc.live.select(diagram_id, req.session, req.selection)
        except StoreError as exc:  # the diagram is not visible to the caller
            raise HTTPException(404, str(exc)) from exc
        except UnknownSession as exc:
            raise HTTPException(409, f"{exc} Open the live stream first.") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ---- frontend -----------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ---- documentation ------------------------------------------------------

    docs_root = docs_dir or DOCS_DIR
    if (docs_root / "index.html").is_file():
        # StaticFiles resolves every path and refuses anything outside docs_root (also via symlinks).
        app.mount("/docs", DocsFiles(directory=docs_root, html=True), name="docs")
    else:

        @app.get("/docs", include_in_schema=False)
        @app.get("/docs/{path:path}", include_in_schema=False)
        def docs_missing(path: str = "") -> PlainTextResponse:
            return PlainTextResponse(DOCS_MISSING, status_code=404)

    return app


def main() -> None:
    import uvicorn

    env = Env()
    host = env.text("ARCVIZ_HOST", "127.0.0.1")
    port = env.integer("ARCVIZ_PORT", 8000, minimum=1, maximum=65535)
    try:
        app = create_app()
    except ConfigError as exc:  # one message with every problem, no traceback (ADR-0106)
        fail(ConfigError.merge([ConfigError(env.problems), exc]))
    if env.problems:
        fail(ConfigError(env.problems))
    options: dict[str, Any] = {}
    if app.state.log_config.format == "json":  # uvicorn's records through the app's JSON handler, no access log
        options = {"log_config": None, "access_log": False}
    uvicorn.run(app, host=host, port=port, **options)  # the app's lifespan ends live streams on shutdown


def fail(error: ConfigError) -> NoReturn:
    print(error, file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
