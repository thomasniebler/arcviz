"""The project agent: one chat over the diagrams of a project.

Spec: docs/architecture/specs/project-chat.md (ADR-0057..0061). The model sees the project structure and
edits one *active* diagram at a time with the tools of its type; `open_diagram`/`create_diagram` switch it.
Every diagram the turn edits follows the semantics of a diagram turn (`DiagramAgent.chat`): write-through
after each call, conditional on that diagram's revision, and one automatic version per changed diagram in
the `finally` block. The transcript is the project's.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from . import prompts, proposal, render, sequence
from .agent import (
    DEFAULT_CONTEXT_BUDGET,
    ROLE_LOST,
    ROLE_LOST_TURN,
    VERSION_FAILED,
    DiagramAgent,
    _is_tool_error,
    apply_tool_call,
    build_tools,
    get_diagram_result,
    store_span,
    take_back_invalid_link,
    tool_error,
)
from .agent_model import (
    MODEL_TOOLS,
    PROJECT_LEVEL_TOOLS,
    READ_MODEL_TOOLS,
    ModelTools,
    architecture_context,
    changed_views,
    model_tools_for,
    propagated,
)
from .llm import LLMProvider, ToolCall, ToolResult, ToolSpec
from .model import DIAGRAM_TYPES, Diagram, fingerprint, kinds_for, slugify
from .operations import OperationError
from .ports import StoreError, VersionInfo
from .projects import Membership, Project, incarnation_of
from .services import DiagramService, Forbidden, ModelService, ProjectService

logger = logging.getLogger(__name__)

PROJECT_CONTEXT_BUDGET = 8_000  # characters of project structure in the system prompt
BRIEF_MIN, BRIEF_MAX = 60, 400  # characters per diagram brief in the structure

_DIAGRAM_REF = {"type": "string", "description": "Id of the diagram (or its exact title)."}

PROJECT_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "list_diagrams",
        "Return the project's current structure as JSON: its sections and every diagram with id, title, type, "
        "section and element counts, plus the id of the active diagram.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "read_diagram",
        "Return the content of any diagram of the project as text (elements with ids, edges) without opening it. "
        "Use it to look at a diagram before deciding what to change.",
        {
            "type": "object",
            "properties": {"diagram": _DIAGRAM_REF},
            "required": ["diagram"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "open_diagram",
        "Make a diagram of the project the active one. Its editing tools (of its diagram type) and its instructions "
        "come with the result and can be used from your next step on. Only the active diagram can be edited.",
        {
            "type": "object",
            "properties": {"diagram": _DIAGRAM_REF},
            "required": ["diagram"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "create_diagram",
        "Create a new, empty diagram in the project and make it the active one. Not available in preview mode.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title of the new diagram, e.g. 'Checkout'."},
                "type": {"type": "string", "enum": list(DIAGRAM_TYPES), "description": "UML diagram type."},
                "section": {
                    "type": "string",
                    "description": "Id (or title) of an existing section; omit for the top level of the project.",
                },
            },
            "required": ["title", "type"],
            "additionalProperties": False,
        },
    ),
]
# never change the active diagram's rendering
READ_TOOLS = ("list_diagrams", "read_diagram", "get_diagram", *READ_MODEL_TOOLS)
_MODEL_PROJECT_TOOLS = [t for t in MODEL_TOOLS if t.name in PROJECT_LEVEL_TOOLS]  # also without an active diagram

# The project chat's prompt (spec: project-chat.md, assistant-prompts.md): the shared blocks and the project part
# from arcviz/prompts (a stable prefix for prompt caching), then this project, then the active diagram.
PROJECT_CONTEXT_TEMPLATE = """Project: {title}{description}

Project structure:
{structure}
"""

NO_ACTIVE = "No diagram is open. Call open_diagram or create_diagram before editing a diagram."
NO_ACTIVE_ERROR = "No diagram is open; call open_diagram or create_diagram first, then edit it."

PROJECT_SUMMARY_SYSTEM_PROMPT = (
    "You summarise the earlier part of a conversation between a user and an assistant that edits the diagrams "
    "of a project through tool calls. Write a compact summary covering: what the user asked for, which "
    "diagrams were created or changed and how (diagram ids, element names and kinds), naming conventions and "
    "decisions, and open requests or errors. Plain text, no headings, at most about 250 words, in the language "
    "of the conversation. The assistant knows the current project structure separately, so do not describe it."
)


# ---- prompt helpers -------------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def diagram_brief(diagram: Diagram, limit: int = BRIEF_MAX) -> str:
    """A one-line summary of a diagram: its size and its top-level names, at most `limit` characters."""
    if diagram.interaction is not None:
        lifelines = [lf.name for lf in diagram.interaction.lifelines]
        messages = sum(
            1 for fragment, _, _ in sequence.walk(diagram.interaction.fragments) if fragment.element == "message"
        )
        text = f"{len(lifelines)} lifelines, {messages} messages"
        names = lifelines
    else:
        text = f"{len(diagram.nodes)} elements, {len(diagram.edges)} edges"
        names = [n.name for n in diagram.children(None)]
    if names:
        text += ": " + ", ".join(names)
    return _cut(text, limit)


def project_structure(
    project: Project, members: list[tuple[Membership, Diagram]], budget: int = PROJECT_CONTEXT_BUDGET
) -> str:
    """The section tree with every member diagram as `[id] «type» title — brief`, within `budget` characters
    (brief length clamped to BRIEF_MIN..BRIEF_MAX; without briefs when even BRIEF_MIN does not fit)."""
    share = budget // max(1, len(members) + 1)
    limit = min(BRIEF_MAX, share) if share >= BRIEF_MIN else 0
    by_section: dict[str | None, list[Diagram]] = {}
    for member, diagram in members:
        by_section.setdefault(member.section, []).append(diagram)
    lines: list[str] = []

    def diagram_line(diagram: Diagram, depth: int) -> str:
        line = f"{'  ' * depth}- [{diagram.id}] «{diagram.type}» {diagram.title}"
        return line + (f" — {diagram_brief(diagram, limit)}" if limit else "")

    def level(section_id: str | None, depth: int, seen: frozenset[str]) -> None:
        for section in project.children(section_id):
            if section.id in seen:
                continue
            lines.append(f"{'  ' * depth}- Section: {section.title} [{section.id}]")
            level(section.id, depth + 1, seen | {section.id})
        lines.extend(diagram_line(d, depth) for d in by_section.get(section_id, []))

    level(None, 0, frozenset())
    if not members:
        lines.append("(no diagrams yet)")
    if members and not limit:
        lines.append("(The project is too large for summaries here; use read_diagram for a diagram's content.)")
    return "\n".join(lines)


def build_project_prompt(
    project: Project,
    members: list[tuple[Membership, Diagram]],
    active: Diagram | None,
    context_budget: int | None = DEFAULT_CONTEXT_BUDGET,
    model: str | None = None,
) -> str:
    """The project chat's prompt: the stable prefix, this project's structure, its model elements (`model`) and its
    architecture drivers (from the project document; spec architecture-drivers.md), then the active diagram."""
    description = f"\nDescription: {project.description}" if project.description else ""
    context = PROJECT_CONTEXT_TEMPLATE.format(
        title=project.title, description=description, structure=project_structure(project, members)
    )
    prompt = f"{prompts.project_prompt_prefix()}\n\n{context}"
    if model:  # the project's shared model elements (spec: shared-project-model.md)
        prompt += f"\n{prompts.model_part(model)}\n"
    # what the architecture has to serve and what was decided (specs: architecture-drivers.md, decision-support.md)
    arch = architecture_context(project)
    block = prompts.architecture_part(arch.drivers, arch.stakeholders, arch.decisions, arch.vision)
    if block:
        prompt += f"\n{block}\n"
    if active is None:
        return prompt + "\n" + NO_ACTIVE + "\n"
    head = (
        f"\nActive diagram: [{active.id}] «{active.type}» {active.title}. Its modelling guidance and current state:\n\n"
    )
    return prompt + head + active_diagram_part(active, context_budget)


def active_diagram_part(diagram: Diagram, context_budget: int | None = DEFAULT_CONTEXT_BUDGET) -> str:
    """The active diagram's type guidance and state (the project prompt already has the shared blocks)."""
    kinds = ", ".join(sorted(kinds_for(diagram.type).all_kinds))
    return prompts.diagram_part(diagram.type, kinds, diagram.summary(budget=context_budget))


# ---- the agent --------------------------------------------------------------------------------------


@dataclass
class _DiagramTurn:
    """What a project turn knows about one diagram it opened."""

    diagram: Diagram
    revision: str
    before: dict[str, Any]
    section: str | None = None
    original: Diagram | None = None  # propose mode: the state the proposal is based on
    applied: list[dict[str, Any]] = field(default_factory=list)
    changed: set[str] = field(default_factory=set)
    created: bool = False
    deleted: bool = False
    lost: bool = False  # a write to it was refused because the caller may only view it now: no more writes to it
    version: VersionInfo | None = None


class ProjectAgent(DiagramAgent):
    """Chat turns over a project (spec: project-chat.md). Shares the transcript machinery (history, compaction)
    with `DiagramAgent`, on the project's transcript."""

    summary_prompt = PROJECT_SUMMARY_SYSTEM_PROMPT

    def __init__(
        self,
        provider: LLMProvider,
        diagrams: DiagramService,
        projects: ProjectService,
        models: ModelService | None = None,
        **options: Any,
    ):
        super().__init__(provider, diagrams, models=models, **options)
        self.projects = projects
        self.transcripts = projects

    def bound(
        self, diagrams: DiagramService, models: ModelService | None = None, projects: ProjectService | None = None
    ) -> ProjectAgent:
        """A copy working on services bound to one caller (see `DiagramAgent.bound`)."""
        agent = copy.copy(self)
        agent.diagrams = diagrams
        agent.models = models if self.models is not None else None
        if projects is not None:
            agent.projects = projects
        agent.transcripts = agent.projects
        return agent

    def chat(  # type: ignore[override]
        self,
        project_id: str,
        user_message: str,
        active: str | None = None,
        focus: str | None = None,
        propose: bool = False,
    ) -> Generator[dict[str, Any], None, None]:
        """Run one project turn and yield JSON-able events (spec: project-chat.md). ProjectNotFound before the
        first event when the project does not exist."""
        self.projects.require(project_id, "editor")  # viewers never start a turn (also not a proposal), ADR-0084
        project, members = self.projects.load_members(project_id)
        # The transcript generation identifies this incarnation of the project's transcript: a project deleted and
        # created again under the same id (or a cleared chat) has another one, and the turn is not written there.
        generation = self.projects.chat_generation(project_id)
        known: dict[str, tuple[str, str, str | None]] = {d.id: (d.title, d.type, m.section) for m, d in members}
        turns: dict[str, _DiagramTurn] = {}
        order: list[str] = []  # diagrams in the order they were first changed or created
        applied: list[dict[str, Any]] = []
        current: str | None = None  # the active diagram
        proposal_of: str | None = None  # propose mode: the diagram the proposal is about
        project_changed = False
        # the caller may not create diagrams in the project any more (create_diagram refused); a refused write to one
        # diagram only stops writes to that diagram (`_DiagramTurn.lost`): another may still be editable (#199 review)
        lost = False
        model_tools = ModelTools(self.models, project_id, propose) if self.models is not None else None

        def resolve(ref: Any) -> str | None:
            text = str(ref or "").strip()
            if text in known:
                return text
            matches = [i for i, (title, _, _) in known.items() if title.strip().casefold() == text.casefold()]
            return matches[0] if len(matches) == 1 else None

        def unknown(call: ToolCall, ref: Any) -> ToolResult:
            ids = ", ".join(known) or "none"
            return tool_error(call, f"Unknown diagram '{ref}' in this project. Diagrams: {ids}.")

        def member_now(diagram_id: str) -> bool:
            """Whether the diagram still belongs to this project (it may have been moved or the project deleted
            since the turn started); a diagram that left is forgotten for the rest of the turn."""
            try:
                still = self.projects.get(project_id).member(diagram_id) is not None
            except StoreError:
                still = False
            if not still:
                known.pop(diagram_id, None)
            return still

        def left(call: ToolCall, diagram_id: str) -> ToolResult:
            if not self.diagrams.exists(diagram_id):
                return tool_error(call, "The diagram was deleted meanwhile; the change was not applied.")
            return tool_error(call, f"Diagram '{diagram_id}' is no longer in this project; nothing was changed.")

        def open_turn(diagram_id: str) -> _DiagramTurn | None:
            turn = turns.get(diagram_id)
            if turn is not None:
                return turn
            try:
                diagram, revision = self.diagrams.read(diagram_id)
            except StoreError:
                return None
            turn = _DiagramTurn(
                diagram,
                revision,
                diagram.model_dump(mode="json", exclude={"updated_at"}),
                section=known.get(diagram_id, ("", "", None))[2],
                original=diagram.model_copy(deep=True) if propose else None,
            )
            turns[diagram_id] = turn
            return turn

        def active_turn() -> _DiagramTurn | None:
            return turns.get(current) if current is not None else None

        def offered() -> list[ToolSpec]:
            turn = active_turn()
            if turn is None or turn.deleted:
                return PROJECT_TOOLS + (_MODEL_PROJECT_TOOLS if model_tools is not None else [])
            return (
                PROJECT_TOOLS
                + build_tools(kinds_for(turn.diagram.type), turn.diagram.type)
                + (model_tools_for(turn.diagram.type) if model_tools is not None else [])
            )

        def model_call(call: ToolCall) -> ToolResult:
            """A call on the project model; after a rename every diagram the turn holds that the rename was written into
            is read again (it reached them as versions of their own, which also hold this turn's earlier calls). The
            others keep their state, so their earlier edits still get the turn's version."""
            assert model_tools is not None
            result, change = model_tools.execute(call)
            if result.is_error or call.name in READ_MODEL_TOOLS:
                return result
            applied.append(
                {"diagram": None, "op": call.name, "arguments": call.arguments, "result": json.loads(result.content)}
            )
            if change is not None and not propose:
                for turn in turns.values():
                    if turn.deleted or turn.diagram.id not in propagated(change):
                        continue
                    try:
                        turn.diagram, turn.revision = self.diagrams.read(turn.diagram.id)
                    except StoreError:
                        turn.deleted = True
                        continue
                    turn.before = turn.diagram.model_dump(mode="json", exclude={"updated_at"})
                    turn.changed.update(changed_views(turn.diagram, change))
            return result

        def applier(call: ToolCall) -> Any:
            if call.name in ("link_element", "link_relation"):
                if model_tools is None:
                    return tool_error(call, f"This project has no shared model here; {call.name} is not available.")
                return model_tools.link(call) if call.name == "link_element" else model_tools.link_relation(call)
            return lambda target: apply_tool_call(target, call)

        def ok(call: ToolCall, payload: dict[str, Any]) -> ToolResult:
            return ToolResult(call.id, json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str))

        def structure() -> dict[str, Any]:
            latest = self.projects.get(project_id)
            diagrams = []
            for member in latest.diagrams:
                turn = turns.get(member.diagram)
                if turn is not None and not turn.deleted:
                    diagram: Diagram | None = turn.diagram
                else:
                    try:
                        diagram = self.diagrams.get(member.diagram)
                    except StoreError:
                        diagram = None
                if diagram is None:
                    continue
                count = len(diagram.interaction.lifelines) if diagram.interaction is not None else len(diagram.nodes)
                diagrams.append(
                    {
                        "id": diagram.id,
                        "title": diagram.title,
                        "type": diagram.type,
                        "section": member.section,
                        "elements": count,
                        "edges": len(diagram.edges),
                    }
                )
            sections = [{"id": s.id, "title": s.title, "parent": s.parent} for s in latest.sections]
            return {"sections": sections, "diagrams": diagrams, "active": current}

        def opened(call: ToolCall, turn: _DiagramTurn, **extra: Any) -> ToolResult:
            diagram = turn.diagram
            return ok(
                call,
                {
                    **extra,
                    "active": diagram.id,
                    "title": diagram.title,
                    "type": diagram.type,
                    "instructions": active_diagram_part(diagram, self.context_budget),
                },
            )

        def create(call: ToolCall) -> ToolResult:
            nonlocal current, project_changed, lost
            if propose:
                return tool_error(
                    call, "create_diagram is not available in preview mode; ask the user to turn the preview off."
                )
            if lost:
                return tool_error(call, ROLE_LOST)
            if self.projects.chat_generation(project_id) != generation:  # deleted (and maybe re-created) meanwhile
                return tool_error(call, "The project was deleted or its chat cleared meanwhile; nothing was created.")
            args = call.arguments or {}
            title, diagram_type, section = args.get("title"), args.get("type"), args.get("section")
            if not isinstance(title, str) or not title.strip():
                return tool_error(call, "create_diagram needs a non-empty title.")
            if diagram_type not in DIAGRAM_TYPES:
                return tool_error(
                    call, f"Unknown diagram type '{diagram_type}'. Available: {', '.join(DIAGRAM_TYPES)}."
                )
            if section is not None and not isinstance(section, str):
                return tool_error(call, "section must be a section id or title.")
            title = title.strip()
            base = slugify(title)[:60].strip("-") or "diagram"
            try:
                diagram_id = self.diagrams.import_new(
                    Diagram(id=base, title=title, type=diagram_type),
                    title,
                    None,
                    project_id,
                    section or None,
                    incarnation=incarnation_of(project),  # never into a project re-created meanwhile (ADR-0127)
                )
            except OperationError as exc:  # unknown section (ProjectError)
                return tool_error(call, str(exc))
            except Forbidden:
                lost = True
                return tool_error(call, ROLE_LOST)
            except StoreError as exc:
                return tool_error(call, f"The diagram could not be created: {exc}")
            located = self.projects.locate(diagram_id)
            known[diagram_id] = (title, diagram_type, located[1] if located else None)
            turn = open_turn(diagram_id)
            if turn is None:
                return tool_error(call, "The new diagram was deleted meanwhile.")
            turn.created = True
            order.append(diagram_id)
            current, project_changed = diagram_id, True
            return opened(call, turn, diagram=diagram_id, section=turn.section)

        def edit(call: ToolCall) -> ToolResult:
            nonlocal proposal_of
            turn = active_turn()
            if turn is None:
                return tool_error(call, NO_ACTIVE_ERROR)
            if call.name == "get_diagram":
                return get_diagram_result(turn.diagram, call)
            diagram_id = turn.diagram.id
            if turn.deleted:
                return tool_error(call, "The diagram was deleted meanwhile; nothing can be changed any more.")
            if turn.lost:
                return tool_error(call, ROLE_LOST)
            if not member_now(diagram_id):  # moved to another project meanwhile: not this project's to change
                turn.deleted = True
                return left(call, diagram_id)
            if propose:
                if proposal_of is not None and proposal_of != diagram_id:
                    return tool_error(
                        call,
                        f"In preview mode a turn can change only one diagram; this proposal already changes "
                        f"'{proposal_of}'. Ask the user to confirm it first, then change '{diagram_id}'.",
                    )
                apply_call = applier(call)
                if isinstance(apply_call, ToolResult):
                    return apply_call
                result = apply_call(turn.diagram)
                if isinstance(result, ToolResult):
                    return result
                proposal_of = diagram_id
            else:
                apply_call = applier(call)
                if isinstance(apply_call, ToolResult):
                    return apply_call
                adopt, adopted = (
                    model_tools.adopting(turn.diagram, apply_call, call.name)
                    if model_tools is not None
                    else (apply_call, lambda: None)
                )
                try:
                    with store_span("write_through") as span:
                        write = self.diagrams.write_through(
                            diagram_id, turn.diagram, turn.revision, adopt, _is_tool_error
                        )
                        span.set("arcviz.store.outcome", write.outcome)
                finally:
                    adopted()  # elements created for views that were not written are taken back
                if write.outcome == "deleted":
                    turn.deleted = True
                    return tool_error(call, "The diagram was deleted meanwhile; the change was not applied.")
                if write.outcome == "forbidden":  # the turn's earlier writes keep their version (commit_turn)
                    turn.lost = True
                    return tool_error(call, ROLE_LOST)
                turn.diagram, turn.revision = write.diagram, write.revision
                if write.outcome == "busy":
                    return tool_error(
                        call, "The diagram keeps being changed by other requests; not applied, try again."
                    )
                if isinstance(write.result, ToolResult):
                    return write.result
                assert write.result is not None
                result = write.result
                verdict = model_tools.settle(turn.diagram, result) if model_tools is not None else None
                if verdict is not None:  # an adopted element was removed or re-kinded meanwhile: take the link back
                    problems, take_back = verdict
                    with store_span("write_through") as span:
                        undo = self.diagrams.write_through(
                            diagram_id, turn.diagram, turn.revision, take_back, lambda _: False
                        )
                        span.set("arcviz.store.outcome", undo.outcome)
                    turn.diagram, turn.revision = undo.diagram, undo.revision
                    undone = undo.result if undo.outcome == "written" and isinstance(undo.result, list) else []
                    result = ModelTools.settled(result, problems, undone)
                if call.name == "link_element" and model_tools is not None:
                    back = take_back_invalid_link(
                        self.diagrams, model_tools, diagram_id, turn.diagram, turn.revision, call
                    )
                    if back is not None:
                        turn.diagram, turn.revision = back.diagram, back.revision
                        turn.deleted, turn.lost = turn.deleted or back.deleted, turn.lost or back.lost
                        if back.kept:  # the link stays stored: record it like any applied call
                            record(turn, call, result)
                        return tool_error(call, back.error)
            record(turn, call, result)
            return ToolResult(call.id, json.dumps(result, ensure_ascii=False, default=str))

        def record(turn: _DiagramTurn, call: ToolCall, result: dict[str, Any]) -> None:
            turn.changed.update(result.get("touched", []))
            op = {"op": call.name, "arguments": call.arguments, "result": result.get("result")}
            turn.applied.append(op)
            applied.append({"diagram": turn.diagram.id, **op})
            if turn.diagram.id not in order:
                order.append(turn.diagram.id)

        def execute(call: ToolCall) -> ToolResult:
            nonlocal current
            name, args = call.name, call.arguments or {}
            if name == "list_diagrams":
                try:
                    return ok(call, structure())
                except StoreError:  # the project was deleted meanwhile
                    return tool_error(call, "The project was deleted meanwhile; nothing can be listed or changed.")
            if name in ("read_diagram", "open_diagram"):
                diagram_id = resolve(args.get("diagram"))
                if diagram_id is None:
                    return unknown(call, args.get("diagram"))
                if not member_now(diagram_id):
                    return left(call, diagram_id)
                turn = open_turn(diagram_id)
                if turn is None or turn.deleted:
                    return tool_error(call, f"Diagram '{diagram_id}' was deleted meanwhile.")
                if name == "open_diagram":
                    current = diagram_id
                    return opened(call, turn)
                diagram = turn.diagram
                summary = diagram.summary(budget=self.context_budget)
                return ok(
                    call,
                    {
                        "diagram": diagram.id,
                        "title": diagram.title,
                        "type": diagram.type,
                        "section": turn.section,
                        "summary": summary,
                    },
                )
            if name == "create_diagram":
                return create(call)
            if model_tools is not None and name in PROJECT_LEVEL_TOOLS:
                return model_call(call)
            return edit(call)

        def rendering(turn: _DiagramTurn) -> dict[str, Any]:
            current_focus = focus if focus in turn.diagram.nodes else None
            return {
                "diagram_id": turn.diagram.id,
                "diagram": turn.diagram.model_dump(mode="json"),
                **render.payload(turn.diagram, highlight=turn.changed, focus=current_focus),
                "focus": current_focus,
            }

        if active in known and open_turn(active) is not None:
            current = active
        start = active_turn()
        system = build_project_prompt(
            project,
            members,
            start.diagram if start else None,
            self.context_budget,
            model_tools.prompt() if model_tools is not None else None,
        )
        history = self._history(project_id)
        final_text = ""
        usage: dict[str, int] = {}
        error: str | None = None
        completed = False

        try:
            for event in self.provider.run_turn(
                system, history, user_message, offered, execute, self.max_rounds, token_budget=self.token_budget
            ):
                payload = {"type": event.type, **event.data}
                if event.type == "tool_result" and not event.data.get("is_error"):
                    turn = active_turn()
                    if event.data.get("name") not in READ_TOOLS and turn is not None and not turn.deleted:
                        payload.update(rendering(turn))  # live update of the active diagram
                if event.type == "done":
                    final_text = event.data.get("text", "")
                if event.type in ("done", "error"):
                    usage = event.data.get("usage") or usage
                if event.type == "error":
                    error = event.data.get("message")
                yield payload
            completed = True
        finally:
            if not completed and error is None:
                error = "Turn interrupted before the model finished (client disconnected)."
            changed_diagrams, problem = self._finalize_project(
                project_id,
                generation,
                turns,
                order,
                applied,
                user_message,
                final_text,
                error,
                usage,
                current,
                propose,
                proposal_of,
            )
            if (lost or any(t.lost for t in turns.values())) and problem is None:
                problem = ROLE_LOST_TURN
            if problem is not None and error is None:
                error = problem
            final_turn = active_turn()
            if final_turn is not None and not propose:
                try:  # the final event shows the stored state (#65)
                    final_turn.diagram = self.diagrams.get(final_turn.diagram.id)
                except StoreError:
                    final_turn.deleted = True
            if final_turn is not None and final_turn.deleted:
                final_turn, current = None, None
            self._maybe_compact(project_id)

        proposal_turn = turns.get(proposal_of) if proposal_of is not None else None
        yield {
            "type": "final",
            "reply": final_text,
            "error": error,
            "active": current,
            "changed": sorted(final_turn.changed) if final_turn is not None else [],
            "operations": applied,
            "diagrams": changed_diagrams,
            "project_changed": project_changed,
            "version": final_turn.version.model_dump() if final_turn is not None and final_turn.version else None,
            "usage": usage,
            **(rendering(final_turn) if final_turn is not None else {"diagram_id": None, "diagram": None}),
            "proposal": (
                {
                    "diagram_id": proposal_of,
                    "base": fingerprint(proposal_turn.original),
                    "result": fingerprint(proposal_turn.diagram),
                    "operations": [{"op": op["op"], "arguments": op["arguments"]} for op in proposal_turn.applied],
                    "diff": proposal.diagram_diff(proposal_turn.original, proposal_turn.diagram),
                }
                if proposal_turn is not None and proposal_turn.original is not None and proposal_turn.applied
                else None
            ),
        }

    def _finalize_project(
        self,
        project_id: str,
        generation: str,
        turns: dict[str, _DiagramTurn],
        order: list[str],
        applied: list[dict[str, Any]],
        user_message: str,
        final_text: str,
        error: str | None,
        usage: dict[str, int],
        active: str | None,
        propose: bool,
        proposal_of: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One automatic version per changed diagram (ADR-0059), then the project transcript entry. Returns the
        `diagrams` list of the final event and a problem for `final.error`, if any: a version could not be written
        (a store failure), or the caller's role dropped during the turn (the versions of what the turn wrote are
        kept, no transcript entry is written)."""
        problem: str | None = None
        failed: list[str] = []  # "<diagram>: <error>" for every version that could not be written
        label = user_message.strip().splitlines()[0][:100] if user_message.strip() else "Chat change"
        changed: list[dict[str, Any]] = []
        for diagram_id in order:
            turn = turns[diagram_id]
            content = turn.diagram.model_dump(mode="json", exclude={"updated_at"})
            if not propose and not turn.deleted and turn.applied and content != turn.before:
                details = "\n".join(
                    f"- {op['op']} {json.dumps(op['arguments'], ensure_ascii=False, default=str)}"
                    for op in turn.applied
                )
                try:
                    with store_span("commit_turn") as span:
                        turn.version = self.diagrams.commit_turn(
                            turn.diagram, turn.revision, label=label or "Chat change", details=details
                        )
                        span.set("arcviz.store.outcome", "ok" if turn.version is not None else "skipped")
                except StoreError as exc:  # commit_turn logged it; keep going with the other diagrams
                    failed.append(f"{diagram_id}: {exc}")
                if turn.version is None and not failed:
                    logger.info("Project turn on '%s' not versioned: the diagram changed meanwhile", diagram_id)
            changed.append(
                {
                    "id": diagram_id,
                    "title": turn.diagram.title,
                    "type": turn.diagram.type,
                    "version": turn.version.version if turn.version else None,
                    "created": turn.created,
                    "changed": sorted(turn.changed),
                    "operations": len(turn.applied),
                }
            )

        if failed:  # the same text in the transcript and in final.error: every diagram whose version failed
            problem = VERSION_FAILED.format(error="; ".join(failed))
            error = error or problem
        transcript: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        assistant_text = final_text or (f"(error: {error})" if error else "")
        if assistant_text:
            transcript.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "operations": applied,
                    "diagrams": changed,
                    "active": active,
                    "error": error,
                    "usage": usage or {},
                    **({"proposed": True, "proposal_diagram": proposal_of} if propose and proposal_of else {}),
                }
            )
        try:
            # conditional on the generation the turn started with (ADR-0127): deleted, re-created or cleared meanwhile
            with store_span("append_chat") as span:
                written = self.projects.append_chat(project_id, transcript, generation)
                span.set("arcviz.store.outcome", "ok" if written else "skipped")
            if not written:
                logger.info(
                    "Transcript of project '%s' not written: it was deleted or cleared during the turn", project_id
                )
        except Forbidden:
            logger.info("Transcript of project '%s' not written: the caller may only view it now", project_id)
            return changed, problem or ROLE_LOST_TURN
        return changed, problem
