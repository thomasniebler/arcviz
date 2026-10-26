"""The diagram agent: turns chat messages into validated model operations and persists the result."""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from . import agent_types, operations, prompts, proposal, render, telemetry
from .agent_model import (
    PROJECT_LEVEL_TOOLS,
    READ_MODEL_TOOLS,
    ArchitectureContext,
    ModelTools,
    changed_views,
    model_tools_for,
    propagated,
)
from .llm import ChatMessage, LLMProvider, ToolCall, ToolResult, ToolSpec
from .model import (
    ALL_KINDS,
    BUILTIN_KINDS,
    CONTAINER_KINDS,
    DIRECTIONS,
    EDGE_DIRECTIONS,
    EDGE_KINDS,
    Diagram,
    KindCatalog,
    fingerprint,
    kinds_for,
    now_iso,
)
from .ports import Revision, StoreError, VersionInfo
from .project_model import RELATION_KINDS
from .projects import ProjectError
from .services import DiagramService, Forbidden, ModelService
from .services.model import not_taken_back as problem_not_taken_back

_CONTAINER_LIST = ", ".join(sorted(CONTAINER_KINDS))
_EDGE_KIND_LIST = ", ".join(sorted(EDGE_KINDS))
_DIRECTION_SCHEMA = {
    "type": "string",
    "enum": [*EDGE_DIRECTIONS, "default"],
    "description": "Arrow heads: forward (source to target), both, none, or default (as the kind draws it).",
}

# Completeness of a diagram (spec: model-relations.md, ADR-0137): deployment and sequence diagrams.
SET_COMPLETENESS_DESCRIPTION = (
    "Declare that this diagram shows ALL relations of the project model of the given kinds between the elements it "
    "shows (communicates, deployed_on, data_flow; a sequence diagram only communicates), or with [] that it makes no "
    "such claim (the default: a diagram that leaves something out says nothing about it). Only for a complete "
    "diagram does the project review report a relation it does not draw. Set it only when the user says so."
)

TOOLS: list[ToolSpec] = [
    ToolSpec(
        "add_node",
        "Add a new element to the diagram. Container kinds (" + _CONTAINER_LIST + ") can hold other elements "
        "via `parent`. Leaf kinds (service, database, ...) cannot. Returns the created node including its id.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Display name, e.g. 'prod-vnet' or 'Orders DB'."},
                "kind": {
                    "type": "string",
                    "enum": sorted(ALL_KINDS),
                    "description": "UML-ish stereotype of the element.",
                },
                "id": {
                    "type": "string",
                    "description": "Optional stable id (lowercase slug). Derived from the name if omitted.",
                },
                "parent": {
                    "type": "string",
                    "description": "Id (or exact name) of the container to place the element in. Omit for top level.",
                },
                "properties": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Free-form key/value attributes such as region, cidr, runtime, sku.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional note: what the element is for, decisions, open points (max 2000 "
                    "characters). On update an empty string removes it.",
                },
            },
            "required": ["name", "kind"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "update_node",
        "Rename an element, change its kind, set/remove properties, or set its note (description).",
        {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": sorted(ALL_KINDS)},
                "properties": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Properties to set or overwrite.",
                },
                "remove_properties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Property keys to delete.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional note: what the element is for, decisions, open points (max 2000 "
                    "characters). On update an empty string removes it.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "move_node",
        "Move an existing element into a container (or to the top level with parent=null). "
        "Use this to put existing services into a new vnet/subnet/deployment; it keeps all edges intact.",
        {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Id (or exact name) of the element to move."},
                "parent": {"type": ["string", "null"], "description": "Target container id, or null for top level."},
            },
            "required": ["id", "parent"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "remove_node",
        "Delete an element and all edges touching it. By default its children move up to its parent; "
        "with cascade=true the children are deleted too.",
        {
            "type": "object",
            "properties": {"id": {"type": "string"}, "cascade": {"type": "boolean", "default": False}},
            "required": ["id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "add_edge",
        "Connect two elements. Kinds: " + _EDGE_KIND_LIST + ". Idempotent for identical edges. `direction` "
        "overrides the arrow heads of the kind (forward = source to target, both, none); omit it for the "
        "kind's default. Put details such as protocol and port into `properties`; they are shown on the edge.",
        {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Id (or exact name) of the source element."},
                "target": {"type": "string", "description": "Id (or exact name) of the target element."},
                "label": {
                    "type": "string",
                    "description": "Optional relationship label such as 'orders' or 'replicates'.",
                },
                "kind": {"type": "string", "enum": sorted(EDGE_KINDS), "default": "connection"},
                "direction": _DIRECTION_SCHEMA,
                "properties": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Free-form key/value attributes, e.g. protocol=https, port=443.",
                },
            },
            "required": ["source", "target"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "update_edge",
        "Change the label, kind, direction or properties of an existing edge (direction=default restores the "
        "kind's own arrow).",
        {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "kind": {"type": "string", "enum": sorted(EDGE_KINDS)},
                "direction": _DIRECTION_SCHEMA,
                "properties": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Properties to set or overwrite.",
                },
                "remove_properties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Property keys to delete.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "remove_edge",
        "Remove an edge, either by id or by source and target.",
        {
            "type": "object",
            "properties": {"id": {"type": "string"}, "source": {"type": "string"}, "target": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "rename_diagram",
        "Change the diagram title.",
        {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "set_direction",
        "Set the layout direction of the rendered diagram: TB (top to bottom, default), BT, LR (left to right) "
        "or RL. Wide diagrams with many elements side by side usually read better LR.",
        {
            "type": "object",
            "properties": {"direction": {"type": "string", "enum": list(DIRECTIONS)}},
            "required": ["direction"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "set_completeness",
        SET_COMPLETENESS_DESCRIPTION,
        {
            "type": "object",
            "properties": {
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["all", *RELATION_KINDS]},
                    "description": "Relation kinds this diagram shows completely; ['all'] for every kind, [] for none.",
                }
            },
            "required": ["kinds"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "apply_batch",
        "Apply several operations in one call, all or nothing: if any step fails, none is applied and the error "
        "names the failing step. Use it to build related elements at once (a container with its contents and "
        "edges). Steps run in order; later steps may refer to elements created by earlier ones by id or name.",
        {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": operations.MAX_BATCH,
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": operations.batch_operations("deployment"),
                            },
                            "args": {
                                "type": "object",
                                "description": "Arguments exactly as for the tool of the same name.",
                            },
                        },
                        "required": ["op", "args"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["operations"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "get_diagram",
        "Return the current diagram as JSON (nodes with parents and properties, edges). Without arguments the "
        "whole diagram; with `focus` only that container's subtree; with `ids` only those elements (plus their "
        "ancestors). Always use it for containers marked as collapsed in the summary, or when the summary is not "
        "detailed enough. Edges touching the selected elements are included.",
        {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "description": "Id of a container whose subtree to return."},
                "ids": {"type": "array", "items": {"type": "string"}, "description": "Ids (or names) of elements."},
            },
            "additionalProperties": False,
        },
    ),
]


def build_tools(catalog: KindCatalog, diagram_type: str = "deployment") -> list[ToolSpec]:
    """The tools of a diagram type, with the kind enums and kind lists taken from `catalog`.

    For deployment diagrams that is `TOOLS`, which describes the built-in kinds; configured kinds
    (ARCVIZ_KINDS_FILE) are added here. Other types get their tools from `agent_types`.
    """
    if diagram_type != "deployment":
        return agent_types.build_tools(diagram_type, catalog, TOOLS)
    if catalog is BUILTIN_KINDS:
        return TOOLS
    node_kinds, edge_kinds = sorted(catalog.all_kinds), sorted(catalog.edge_kinds)
    tools = copy.deepcopy(TOOLS)
    for tool in tools:
        kind = tool.parameters.get("properties", {}).get("kind")
        if kind is not None and "enum" in kind:
            kind["enum"] = edge_kinds if tool.name.endswith("_edge") else node_kinds
        tool.description = tool.description.replace(
            _CONTAINER_LIST, ", ".join(sorted(catalog.container_kinds))
        ).replace(_EDGE_KIND_LIST, ", ".join(edge_kinds))
    return tools


DEFAULT_CONTEXT_BUDGET = 12_000  # characters of model summary in the system prompt before collapsing


def build_system_prompt(
    diagram: Diagram,
    context_budget: int | None = DEFAULT_CONTEXT_BUDGET,
    model: str | None = None,
    architecture: ArchitectureContext | None = None,
) -> str:
    """The diagram type's prompt (arcviz/prompts/) with the kind list, the project's model elements (`model`) and its
    architecture context (drivers with core scenarios, stakeholders, accepted decisions; spec: decision-support.md),
    each if the project has any, and the current summary at the end."""
    kinds = ", ".join(sorted(kinds_for(diagram.type).all_kinds))
    context = architecture or ArchitectureContext()
    block = prompts.architecture_part(context.drivers, context.stakeholders, context.decisions, context.vision)
    return prompts.system_prompt(diagram.type, kinds, diagram.summary(budget=context_budget), model, block)


logger = logging.getLogger(__name__)

# A caller whose role drops below editor during a turn (spec: workspaces-and-sharing.md): the tool call, then the turn.
ROLE_LOST = (
    "Your role on this project was lowered during this turn: you may only view it now; the change was not applied."
)
ROLE_LOST_TURN = (
    "Your role on this project was lowered during this turn: the changes made before were kept and versioned, "
    "nothing after that."
)
VERSION_FAILED = "The changes of this turn are saved, but their version could not be written: {error}"


def store_span(operation: str) -> AbstractContextManager[telemetry.Span]:
    """A span around one of the turn's store writes (#187): its operation and outcome, nothing else."""
    return telemetry.span("arcviz.store.write", {"arcviz.store.operation": operation})


@dataclass
class LinkTakeBack:
    """A written `link_element` whose element changed kind meanwhile, after the attempt to unlink it again (#155,
    ADR-0128), as both agents handle it (#195). `error` is the tool's answer; `diagram`/`revision` are the state the
    turn continues on. `kept`: the take-back was not written (busy, deleted, forbidden), so the link stays stored and
    the call is recorded as applied (the project review reports it). `deleted`/`lost`: the take-back found the diagram
    gone or the caller's role dropped below editor; the turn changes nothing more."""

    error: str
    diagram: Diagram
    revision: Revision
    kept: bool
    deleted: bool
    lost: bool


def take_back_invalid_link(
    diagrams: DiagramService,
    model_tools: ModelTools,
    diagram_id: str,
    diagram: Diagram,
    revision: Revision,
    call: ToolCall,
) -> LinkTakeBack | None:
    """Verify a just-written `link_element` against the element as it is now; None when the link is valid. Otherwise
    the link is unlinked again through the turn's write-through, and the outcome says what the turn must do."""
    verdict = model_tools.verify_link(diagram, call)
    if verdict is None:
        return None
    problem, take_back = verdict
    with store_span("write_through") as span:
        back = diagrams.write_through(diagram_id, diagram, revision, take_back, _is_tool_error)
        span.set("arcviz.store.outcome", back.outcome)
    if back.outcome in ("written", "rejected"):  # unlinked again, or nothing left to unlink
        return LinkTakeBack(problem, back.diagram, back.revision, kept=False, deleted=False, lost=False)
    return LinkTakeBack(
        problem_not_taken_back(problem),
        back.diagram,
        back.revision,
        kept=True,
        deleted=back.outcome == "deleted",
        lost=back.outcome == "forbidden",
    )


SUMMARY_SYSTEM_PROMPT = (
    "You summarise the earlier part of a conversation between a user and an assistant that edits a "
    "UML diagram through tool calls. Write a compact summary covering: what the user asked for, what "
    "was built or changed (element names and kinds), naming conventions and decisions, and open requests or "
    "errors. Plain text, no headings, at most about 250 words, in the language of the conversation. The "
    "assistant knows the current diagram separately, so do not describe the whole diagram."
)
SUMMARY_INPUT_LIMIT = 60_000  # characters of folded transcript handed to the model
SUMMARY_HISTORY_PREFIX = "Summary of the earlier conversation:\n"
SUMMARY_ACK = "Understood."


class TranscriptService(Protocol):
    """What the transcript machinery needs: `DiagramService` (per diagram) or `ProjectService` (per project)."""

    def chat(self, owner_id: str) -> list[dict[str, Any]]: ...
    def chat_generation(self, owner_id: str) -> str: ...
    def compact_chat(self, owner_id: str, upto_seq: int, summary: dict[str, Any], generation: str) -> bool: ...


class _Compaction:
    """The background compaction of every copy of an agent (`bound`): one single-worker executor, its futures."""

    def __init__(self) -> None:
        self.executor: ThreadPoolExecutor | None = None
        self.pending: list[Future[None]] = []


class DiagramAgent:
    summary_prompt = SUMMARY_SYSTEM_PROMPT  # the instruction of the compaction call

    def __init__(
        self,
        provider: LLMProvider,
        diagrams: DiagramService,
        max_rounds: int = 12,
        history_turns: int = 30,
        context_budget: int | None = DEFAULT_CONTEXT_BUDGET,
        compact_after: int = 60,
        keep: int = 30,
        background: bool = True,
        token_budget: int | None = None,
        models: ModelService | None = None,
    ):
        self.provider = provider
        self.diagrams = diagrams
        self.models = models  # the shared project model (spec: shared-project-model.md); None: no element tools
        self.transcripts: TranscriptService = diagrams  # whose transcripts `_history`/`compact` work on
        self.max_rounds = max_rounds
        self.history_turns = history_turns
        self.context_budget = context_budget  # None = never collapse
        self.compact_after = compact_after  # 0 disables automatic compaction
        self.keep = max(1, keep)
        self.background = background
        self.token_budget = token_budget or None  # tokens per turn; None/0 = no cap
        self._compaction = _Compaction()  # shared with the copies `bound` makes

    def bound(self, diagrams: DiagramService, models: ModelService | None = None) -> DiagramAgent:
        """A copy of this agent working on services bound to one caller (spec: workspaces-and-sharing.md); it shares
        the provider, the configuration and the background compaction."""
        agent = copy.copy(self)
        agent.diagrams = diagrams
        agent.transcripts = diagrams
        agent.models = models if self.models is not None else None
        return agent

    def _model_tools(self, diagram_id: str, propose: bool) -> ModelTools | None:
        """The element tools of a turn on a diagram that belongs to a project (spec: shared-project-model.md)."""
        if self.models is None:
            return None
        try:
            return ModelTools(self.models, self.models.project_of(diagram_id), propose)
        except (ProjectError, StoreError):
            return None

    def _history(self, diagram_id: str) -> list[ChatMessage]:
        entries = self.transcripts.chat(diagram_id)
        head: list[ChatMessage] = []
        if entries and entries[0].get("role") == "summary":
            head = [
                ChatMessage(role="user", content=SUMMARY_HISTORY_PREFIX + entries[0]["content"]),
                ChatMessage(role="assistant", content=SUMMARY_ACK),
            ]
        msgs = [
            ChatMessage(role=e["role"], content=e["content"])
            for e in entries
            if e.get("role") in ("user", "assistant") and e.get("content")
        ]
        return head + msgs[-self.history_turns :]

    # ---- chat compaction ----------------------------------------------------

    def compact(self, diagram_id: str) -> bool:
        """Fold everything but the newest `keep` entries into one cumulative summary entry.

        Returns False when there is nothing to fold. Runs inline; raises on provider/store errors.
        """
        # The generation first: a clear (or delete) after this read makes compact_chat refuse the summary,
        # even when the fresh transcript reuses the same seq numbers (#67).
        generation = self.transcripts.chat_generation(diagram_id)
        entries = self.transcripts.chat(diagram_id)
        cut = self._fold_boundary(entries)
        if cut is None:
            return False
        prompt = self._summary_input(entries[:cut])
        while len(prompt) > SUMMARY_INPUT_LIMIT:
            # Too much to summarise at once: shorten the fold from its newer end rather than dropping
            # anything from the prompt (what is not summarised must stay in the transcript).
            cut = self._fold_boundary(entries[:cut], keep=1)
            if cut is None:
                return False
            prompt = self._summary_input(entries[:cut])
        folded = entries[:cut]
        previous = folded[0] if folded[0].get("role") == "summary" else None
        text = self.provider.complete_text(self.summary_prompt, prompt)
        turns = sum(1 for e in folded if e.get("role") == "user")
        summary = {
            "role": "summary",
            "content": text,
            "covers": {
                "from_seq": previous["covers"]["from_seq"] if previous else folded[0]["seq"],
                "to_seq": folded[-1]["seq"],
                "turns": turns + (previous["covers"]["turns"] if previous else 0),
            },
            "created_at": now_iso(),
        }
        # False if the transcript was cleared, imported or deleted while the summary was being written.
        return self.transcripts.compact_chat(diagram_id, folded[-1]["seq"], summary, generation)

    def _fold_boundary(self, entries: list[dict[str, Any]], keep: int | None = None) -> int | None:
        """Index of the first kept entry: `keep` from the end, moved back to a user entry; None = nothing to fold."""
        start = len(entries) - (self.keep if keep is None else keep)
        while start > 0 and entries[start].get("role") != "user":
            start -= 1
        if start <= 0:
            return None
        roles = {e.get("role") for e in entries[:start]}
        if not {"user", "assistant"} <= roles:
            return None  # the fold must hold at least one complete turn
        return start

    @staticmethod
    def _summary_input(folded: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        previous: str | None = None
        for e in folded:
            role = e.get("role")
            if role == "summary":
                previous = e["content"]
                continue
            if role not in ("user", "assistant"):
                continue
            line = f"{role}: {e.get('content', '')}"
            ops = [_describe_op(op) for op in e.get("operations") or []]
            if ops:
                line += f" [{', '.join(ops)}]"
            lines.append(line)
        head = f"Previous summary:\n{previous}\n\n" if previous else ""
        return head + "\n".join(lines)

    def _maybe_compact(self, diagram_id: str) -> None:
        if self.compact_after <= 0:
            return
        try:
            if len(self.transcripts.chat(diagram_id)) <= self.compact_after:
                return
        except Exception as exc:  # never let bookkeeping break a turn
            # the type in the text, the exception as exc_info: log records never carry its message (ADR-0112)
            logger.warning("Chat compaction check for '%s' failed (%s)", diagram_id, type(exc).__name__, exc_info=True)
            return
        if self.background:
            compaction = self._compaction
            if compaction.executor is None:
                compaction.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="arcviz-compact")
            compaction.pending = [f for f in compaction.pending if not f.done()]
            compaction.pending.append(compaction.executor.submit(self._compact_safely, diagram_id))
        else:
            self._compact_safely(diagram_id)

    def _compact_safely(self, diagram_id: str) -> None:
        try:
            self.compact(diagram_id)
        except Exception as exc:
            logger.warning(
                "Chat compaction for '%s' failed (%s), will retry after the next turn",
                diagram_id,
                type(exc).__name__,
                exc_info=True,
            )

    def drain(self) -> None:
        """Wait for background compactions to finish (tests, shutdown)."""
        for f in self._compaction.pending:
            f.result()
        self._compaction.pending = []

    def chat(
        self, diagram_id: str, user_message: str, focus: str | None = None, propose: bool = False
    ) -> Generator[dict[str, Any], None, None]:
        """Run one chat turn and yield JSON-able events.

        With `propose` the turn is a dry run: operations change only the in-memory diagram, nothing but
        the transcript is persisted, and the `final` event carries the proposal (operations, base and
        result fingerprints, diff) for the client to confirm or discard (see `arcviz/proposal.py`).

        `focus` is the container the UI is currently zoomed into; live renders use the same view and
        fall back to the whole diagram if the focused node disappears during the turn.

        Every successful mutation is written to `latest.json` immediately, and the version snapshot
        plus the transcript entry are written in a `finally` block, so a client that disconnects
        mid-stream (browser refresh, lost connection) never leaves displayed changes unpersisted.
        """
        self.diagrams.require(diagram_id, "editor")  # viewers never start a turn (also not a proposal), ADR-0084
        # The transcript generation of this incarnation: a chat cleared or imported, or a diagram deleted and created
        # again during the turn, does not receive its entry (ADR-0127).
        generation = self.diagrams.chat_generation(diagram_id)
        diagram, revision = self.diagrams.read(diagram_id)
        deleted = False  # the diagram was deleted during the turn: nothing is written any more
        lost = False  # the caller's role dropped below editor during the turn: nothing is changed any more
        before = diagram.model_dump(mode="json", exclude={"updated_at"})
        original = diagram.model_copy(deep=True) if propose else None
        changed: set[str] = set()
        applied: list[dict[str, Any]] = []

        def rendering() -> dict[str, Any]:
            current_focus = focus if focus in diagram.nodes else None
            return render.payload(diagram, highlight=changed, focus=current_focus)

        def failed(call: ToolCall, error: str) -> ToolResult:
            return tool_error(call, error)

        model_tools = self._model_tools(diagram_id, propose)

        def applier(call: ToolCall) -> Callable[[Diagram], dict[str, Any] | ToolResult] | ToolResult:
            if call.name in ("link_element", "link_relation"):
                if model_tools is None:
                    return failed(call, "This diagram belongs to no project, so it has no shared model.")
                return model_tools.link(call) if call.name == "link_element" else model_tools.link_relation(call)
            return lambda target: apply_tool_call(target, call)

        def model_call(call: ToolCall) -> ToolResult:
            """A call on the project model (list, create, update): when a rename was carried into this diagram (as a
            version of its own, which also holds this turn's earlier calls), the diagram is read again. A change that
            wrote nothing here (a description, a kind, an element this diagram does not show, a failed write) leaves
            the turn's state alone, so its earlier edits still get the turn's version."""
            nonlocal diagram, revision, deleted, before
            assert model_tools is not None
            result, change = model_tools.execute(call)
            if result.is_error or call.name in READ_MODEL_TOOLS:
                return result
            applied.append({"op": call.name, "arguments": call.arguments, "result": json.loads(result.content)})
            if change is not None and not deleted and not propose and diagram_id in propagated(change):
                try:
                    diagram, revision = self.diagrams.read(diagram_id)
                except StoreError:
                    deleted = True
                    return result
                before = diagram.model_dump(mode="json", exclude={"updated_at"})
                changed.update(changed_views(diagram, change))
            return result

        def execute(call: ToolCall) -> ToolResult:
            nonlocal diagram, revision, deleted, lost
            if call.name == "get_diagram":
                return get_diagram_result(diagram, call)
            if model_tools is not None and call.name in PROJECT_LEVEL_TOOLS:
                return model_call(call)
            if deleted:
                return failed(call, "The diagram was deleted meanwhile; nothing can be changed any more.")
            if lost:
                return failed(call, ROLE_LOST)
            apply_call = applier(call)
            if isinstance(apply_call, ToolResult):
                return apply_call
            if propose:  # a dry run on the in-memory copy
                result = apply_call(diagram)
                if isinstance(result, ToolResult):
                    return result
            else:
                # Commit at once, conditional on the revision this state was read at (#65). On a conflict the
                # service re-loads latest (which holds this turn's earlier calls and the concurrent change)
                # and applies the call again; the turn continues on that state.
                adopt, adopted = (
                    model_tools.adopting(diagram, apply_call, call.name)
                    if model_tools is not None
                    else (apply_call, lambda: None)
                )
                try:
                    with store_span("write_through") as span:
                        write = self.diagrams.write_through(diagram_id, diagram, revision, adopt, _is_tool_error)
                        span.set("arcviz.store.outcome", write.outcome)
                finally:
                    adopted()  # elements created for views that were not written are taken back
                if write.outcome == "deleted":
                    deleted = True
                    return failed(call, "The diagram was deleted meanwhile; the change was not applied.")
                if write.outcome == "forbidden":
                    lost = True
                    return failed(call, ROLE_LOST)
                diagram, revision = write.diagram, write.revision
                if write.outcome == "busy":
                    return failed(call, "The diagram keeps being changed by other requests; not applied, try again.")
                if isinstance(write.result, ToolResult):  # rejected: the operation failed, nothing written
                    return write.result
                assert write.result is not None
                result = write.result
                verdict = model_tools.settle(diagram, result) if model_tools is not None else None
                if verdict is not None:  # an adopted element was removed or re-kinded meanwhile: take the link back
                    problems, take_back = verdict
                    with store_span("write_through") as span:
                        undo = self.diagrams.write_through(diagram_id, diagram, revision, take_back, lambda _: False)
                        span.set("arcviz.store.outcome", undo.outcome)
                    diagram, revision = undo.diagram, undo.revision
                    undone = undo.result if undo.outcome == "written" and isinstance(undo.result, list) else []
                    result = ModelTools.settled(result, problems, undone)
                if call.name == "link_element" and model_tools is not None:
                    back = take_back_invalid_link(self.diagrams, model_tools, diagram_id, diagram, revision, call)
                    if back is not None:
                        diagram, revision = back.diagram, back.revision
                        deleted, lost = deleted or back.deleted, lost or back.lost
                        if back.kept:  # the link stays stored: record it like any applied call
                            changed.update(result.get("touched", []))
                            applied.append(
                                {"op": call.name, "arguments": call.arguments, "result": result.get("result")}
                            )
                        return failed(call, back.error)
            changed.update(result.get("touched", []))
            applied.append({"op": call.name, "arguments": call.arguments, "result": result.get("result")})
            return ToolResult(call.id, json.dumps(result, ensure_ascii=False, default=str))

        model, architecture = model_tools.context() if model_tools is not None else (None, None)
        system = build_system_prompt(diagram, self.context_budget, model, architecture)
        history = self._history(diagram_id)
        final_text = ""
        usage: dict[str, int] = {}
        error: str | None = None
        completed = False
        version = None

        try:
            tools = build_tools(kinds_for(diagram.type), diagram.type)
            if model_tools is not None:
                tools = [*tools, *model_tools_for(diagram.type)]
            for event in self.provider.run_turn(
                system, history, user_message, tools, execute, self.max_rounds, token_budget=self.token_budget
            ):
                payload = {"type": event.type, **event.data}
                if event.type == "tool_result" and not event.data.get("is_error"):
                    # Live update: ship the intermediate state after every successful mutation.
                    payload["diagram"] = diagram.model_dump(mode="json")
                    payload.update(rendering())
                    payload["focus"] = focus if focus in diagram.nodes else None
                if event.type == "done":
                    final_text = event.data.get("text", "")
                if event.type in ("done", "error"):  # errors carry the usage of the rounds that did complete
                    usage = event.data.get("usage") or usage
                if event.type == "error":
                    error = event.data.get("message")
                yield payload
            completed = True
        finally:
            if not completed and error is None:
                error = "Turn interrupted before the model finished (client disconnected)."
            version, problem = self._finalize(
                diagram_id,
                diagram,
                None if deleted else revision,
                before,
                user_message,
                final_text,
                error,
                applied,
                usage,
                propose=propose,
                generation=generation,
            )
            if lost and problem is None:
                problem = ROLE_LOST_TURN
            if problem is not None and error is None:
                error = problem
            if not propose:
                # The final event shows the stored state: after a skipped finalization (a newer edit or
                # restore) that is not the turn's own copy any more (#65).
                try:
                    diagram = self.diagrams.get(diagram_id)
                except StoreError:
                    deleted = True
                if deleted and error is None:
                    error = "The diagram was deleted during this turn."
            self._maybe_compact(diagram_id)

        yield {
            "type": "final",
            "reply": final_text,
            "error": error,
            "changed": sorted(changed),
            "operations": applied,
            "version": version.model_dump() if version else None,
            "diagram": diagram.model_dump(mode="json"),
            **rendering(),
            "focus": focus if focus in diagram.nodes else None,
            "usage": usage,
            "proposal": (
                {
                    "base": fingerprint(original),
                    "result": fingerprint(diagram),
                    "operations": [{"op": op["op"], "arguments": op["arguments"]} for op in applied],
                    "diff": proposal.diagram_diff(original, diagram),
                }
                if original is not None and applied
                else None
            ),
        }

    def _finalize(
        self,
        diagram_id: str,
        diagram: Diagram,
        revision: str | None,
        before: dict[str, Any],
        user_message: str,
        final_text: str,
        error: str | None,
        applied: list[dict[str, Any]],
        usage: dict[str, int] | None = None,
        propose: bool = False,
        generation: str | None = None,
    ) -> tuple[VersionInfo | None, str | None]:
        """Persist the outcome of a turn: an automatic version of the committed state, the transcript. Returns the
        version and a problem for `final.error`, if any: the version could not be written (a store failure), or the
        caller's role dropped during the turn (the version of what the turn wrote is kept, no transcript entry).

        The version is written together with latest, conditional on `revision`, the revision of the turn's
        last committed call. If latest moved on since (a newer edit, restore or undo) or the diagram was
        deleted (`revision` None), no version is written: the newer state already holds the turn's
        operations, and writing would overwrite it (#65). A propose turn persists only the transcript;
        its assistant entry is marked `proposed`."""
        version = None
        problem: str | None = None
        if (
            not propose
            and revision is not None
            and applied
            and diagram.model_dump(mode="json", exclude={"updated_at"}) != before
        ):
            label = user_message.strip().splitlines()[0][:100] or "Chat change"
            details = "\n".join(
                f"- {op['op']} {json.dumps(op['arguments'], ensure_ascii=False, default=str)}" for op in applied
            )
            try:
                with store_span("commit_turn") as span:
                    version = self.diagrams.commit_turn(diagram, revision, label=label, details=details)
                    span.set("arcviz.store.outcome", "ok" if version is not None else "skipped")
            except StoreError as exc:
                problem = VERSION_FAILED.format(error=exc)
                error = error or problem
            if version is None and problem is None:
                logger.info("Turn on '%s' not versioned: the diagram changed after its last operation", diagram_id)

        transcript: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        assistant_text = final_text or (f"(error: {error})" if error else "")
        if assistant_text:
            transcript.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "operations": applied,
                    "version": version.version if version else None,
                    "error": error,
                    "usage": usage or {},
                    **({"proposed": True} if propose and applied else {}),
                }
            )
        try:
            with store_span("append_chat") as span:
                written = self.diagrams.append_chat(diagram_id, transcript, generation)
                span.set("arcviz.store.outcome", "ok" if written else "skipped")
            if not written:
                logger.info(
                    "Transcript of '%s' not written: the diagram was deleted, or its chat cleared, during the turn",
                    diagram_id,
                )
        except Forbidden:
            logger.info("Transcript of '%s' not written: the caller may only view it now", diagram_id)
            return version, problem or ROLE_LOST_TURN
        return version, problem


def tool_error(call: ToolCall, error: str) -> ToolResult:
    """The failed result of a tool call, as the model sees it: nothing was changed."""
    return ToolResult(call.id, json.dumps({"ok": False, "error": error}), is_error=True)


def apply_tool_call(target: Diagram, call: ToolCall) -> dict[str, Any] | ToolResult:
    """Run an editing call through `operations.apply`; a ToolResult when it failed (target unchanged)."""
    if call.name in operations.INTERNAL_OPERATIONS:  # only the model service passes an element's name and kind
        return tool_error(call, f"Unknown tool '{call.name}'; use link_element with the view element and the element.")
    try:
        return operations.apply(target, call.name, call.arguments)
    except operations.OperationError as exc:
        return tool_error(call, str(exc))
    except Exception as exc:  # a defect, not bad input: `apply` restored the diagram; keep the turn alive
        logger.exception("Operation %s failed unexpectedly on '%s'", call.name, target.id)
        return tool_error(
            call, f"Internal error while applying {call.name} ({type(exc).__name__}); nothing was changed."
        )


def get_diagram_result(diagram: Diagram, call: ToolCall) -> ToolResult:
    """The read-only `get_diagram` tool: the whole diagram, a container's subtree (`focus`) or elements (`ids`)."""
    args = call.arguments or {}
    focus_arg = args.get("focus") or None
    if focus_arg is not None and diagram.find_node(str(focus_arg)) is None:
        return tool_error(call, f"Unknown focus '{focus_arg}'.")
    if focus_arg is not None:
        focus_arg = diagram.find_node(str(focus_arg)).id  # type: ignore[union-attr]
    ids_arg = args.get("ids") or None
    payload = diagram.subset(focus=focus_arg, ids=[str(i) for i in ids_arg] if ids_arg else None)
    return ToolResult(call.id, json.dumps(payload, ensure_ascii=False))


def _is_tool_error(result: dict[str, Any] | ToolResult) -> bool:
    return isinstance(result, ToolResult)


def _describe_op(op: dict[str, Any]) -> str:
    args = op.get("arguments") or {}
    if op.get("op") == "apply_batch":
        return f"apply_batch ({len(args.get('operations') or [])} steps)"
    target = args.get("name") or args.get("id") or args.get("source") or ""
    text = f"{op.get('op', '?')} {target}".strip()
    if args.get("parent") and op.get("op") in ("move_node", "add_node"):
        text += f" → {args['parent']}"
    if args.get("target") and op.get("op", "").endswith("edge"):
        text += f" → {args['target']}"
    if op.get("diagram"):  # an operation of a project turn names its diagram
        text = f"[{op['diagram']}] {text}"
    return text
