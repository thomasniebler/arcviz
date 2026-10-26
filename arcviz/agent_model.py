"""Tools for the shared project model in the diagram chat and the project chat.

Spec: docs/architecture/specs/shared-project-model.md (ADR-0076). The model's elements live in the project
document; `ModelService` reads and writes them and carries renames into the diagrams. `link_element` and
`unlink_element` edit the active diagram (the former through the internal operation with the element's name
and kind from the model, the latter as an ordinary operation); `create_element` and `update_element` write the
project and are refused in propose mode, like `link_element` (a proposal is one diagram's operations).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import decision_model as dm
from . import operations
from .llm import ToolCall, ToolResult, ToolSpec
from .model import Diagram
from .ports import StoreError
from .project_model import (
    DRIVER_KINDS,
    DRIVER_LEVELS,
    ELEMENT_KINDS,
    MAX_DRIVER_DESCRIPTION,
    MAX_DRIVER_TITLE,
    MAX_ELEMENT_DESCRIPTION,
    MAX_ELEMENT_NAME,
    PROPERTY_VALUES,
    RELATION_KINDS,
    RELATION_PROPERTIES,
    Driver,
    drivers_summary,
    find_view,
    model_summary,
    references,
    shown_relation_kinds,
    vision_lines,
)
from .projects import Project, ProjectError
from .quality_model import MAX_QUALITIES, QUALITY_CHARACTERISTICS
from .services import ElementChange, Forbidden, ModelService, RelationChange
from .services.adoption import Adoption, adopted_views, adopting, unlink_change, without_taken_back

logger = logging.getLogger(__name__)

_ELEMENT_REF = {"type": "string", "description": "Id (or exact name) of the project element."}
_VIEW_REF = {"type": "string", "description": "Id (or exact name) of the node or lifeline in the active diagram."}

MODEL_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "list_elements",
        "Return the project's shared model elements as JSON: id, name, kind, description, parent, and where each is "
        "shown (diagram ids and the ids of the nodes or lifelines that represent it).",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "create_element",
        "Create a shared element in the project model (an actor, system, subsystem, component, node or data) so "
        "several diagrams can show it; then link nodes or lifelines to it with link_element. Returns the element "
        "with its id. Not available in preview mode.",
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": f"Name, e.g. 'Order Service'. Unique in the project, at most {MAX_ELEMENT_NAME} "
                    "characters.",
                },
                "kind": {"type": "string", "enum": list(ELEMENT_KINDS)},
                "description": {
                    "type": "string",
                    "description": f"One or two sentences on what it is or does, at most {MAX_ELEMENT_DESCRIPTION} "
                    "characters.",
                },
                "parent": {"type": "string", "description": "Id of the element it is part of (e.g. a subsystem)."},
            },
            "required": ["name", "kind"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "update_element",
        "Rename, describe or re-kind a shared element. A new name reaches every diagram that shows the element. "
        "Not available in preview mode.",
        {
            "type": "object",
            "properties": {
                "element": _ELEMENT_REF,
                "name": {"type": "string", "description": f"At most {MAX_ELEMENT_NAME} characters."},
                "description": {"type": "string", "description": f"At most {MAX_ELEMENT_DESCRIPTION} characters."},
                "kind": {"type": "string", "enum": list(ELEMENT_KINDS)},
            },
            "required": ["element"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "link_element",
        "Make a node or lifeline of the active diagram a view of a shared element: it takes the element's name. "
        "Only deployment nodes, activity partitions and data nodes, and lifelines can be linked. Not available in "
        "preview mode.",
        {
            "type": "object",
            "properties": {"id": _VIEW_REF, "element": _ELEMENT_REF},
            "required": ["id", "element"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "unlink_element",
        "End the link of a node or lifeline of the active diagram to its shared element; it keeps its name.",
        {"type": "object", "properties": {"id": _VIEW_REF}, "required": ["id"], "additionalProperties": False},
    ),
]
_RELATION_REF = {"type": "string", "description": "Id of the project relation (e.g. r1)."}
_CONNECTION_REF = {"type": "string", "description": "Id of the edge or message in the active diagram."}
_RELATION_PROPERTIES = {
    "type": "object",
    "description": "Optional: protocol, port, direction (forward|both|none), mode (sync|async); deployed_on has none, "
    "data_flow no direction.",
    "properties": {
        k: ({"type": "string", "enum": list(PROPERTY_VALUES[k])} if k in PROPERTY_VALUES else {"type": "string"})
        for k in RELATION_PROPERTIES
    },
    "additionalProperties": False,
}

# Relations (spec: docs/architecture/specs/model-relations.md, ADR-0140).
RELATION_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "create_relation",
        "Create a relation between two shared elements in the project model: communicates (one calls or talks to the "
        "other), deployed_on (source runs on the node target) or data_flow (data flows from source to target). Only "
        "when the user asked for it or agreed: when a drawn edge or message has no matching relation, say in your "
        "reply which relation you would add instead of creating it. Not available in preview mode.",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(RELATION_KINDS)},
                "source": _ELEMENT_REF,
                "target": _ELEMENT_REF,
                "properties": _RELATION_PROPERTIES,
                "description": {"type": "string", "description": "One sentence, at most 300 characters."},
            },
            "required": ["kind", "source", "target"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "update_relation",
        "Change a relation of the project model: its kind, ends, properties or description. Changing the kind or "
        "an end unlinks the edges and messages that no longer fit it. Not available in preview mode.",
        {
            "type": "object",
            "properties": {
                "relation": _RELATION_REF,
                "kind": {"type": "string", "enum": list(RELATION_KINDS)},
                "source": _ELEMENT_REF,
                "target": _ELEMENT_REF,
                "properties": _RELATION_PROPERTIES,
                "remove_properties": {"type": "array", "items": {"type": "string", "enum": list(RELATION_PROPERTIES)}},
                "description": {"type": "string"},
            },
            "required": ["relation"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "link_relation",
        "Make an edge (deployment or component diagram) or message (sequence diagram) of the active diagram show a relation of the "
        "project model. Its two ends must be linked to the relation's elements. Use it right after drawing a "
        "connection between two linked elements when a matching relation exists. Not available in preview mode.",
        {
            "type": "object",
            "properties": {"id": _CONNECTION_REF, "relation": _RELATION_REF},
            "required": ["id", "relation"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "unlink_relation",
        "End the link of an edge or message of the active diagram to its project relation; the connection stays.",
        {"type": "object", "properties": {"id": _CONNECTION_REF}, "required": ["id"], "additionalProperties": False},
    ),
]
RELATION_TOOL_NAMES = frozenset(t.name for t in RELATION_TOOLS)

# Architecture drivers (spec: docs/architecture/specs/architecture-drivers.md, ADR-0163).
_DRIVER_REF = {"type": "string", "description": "Id (e.g. d3) or exact title of the driver."}
_DRIVER_FIELDS: dict[str, Any] = {
    "title": {
        "type": "string",
        "description": f"Short title, unique in the project, at most {MAX_DRIVER_TITLE} characters.",
    },
    "kind": {"type": "string", "enum": list(DRIVER_KINDS)},
    "importance": {"type": "string", "enum": list(DRIVER_LEVELS), "description": "Business importance."},
    "risk": {"type": "string", "enum": list(DRIVER_LEVELS), "description": "Technical risk or difficulty."},
    "description": {
        "type": "string",
        "description": f"What exactly is required, measurable where possible; at most {MAX_DRIVER_DESCRIPTION} characters.",
    },
    "elements": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Ids (or names) of the project elements it concerns; replaces the list.",
    },
}
DRIVER_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "list_drivers",
        "Return the project's architecture drivers as JSON, most important first, with their full descriptions.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "create_driver",
        "Record an architecture driver of the project (a quality goal, constraint, requirement or risk) with its "
        "business importance and technical risk. Only when the user asks for it. Not available in preview mode.",
        {
            "type": "object",
            "properties": _DRIVER_FIELDS,
            "required": ["title", "kind", "importance", "risk"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "update_driver",
        "Change an architecture driver: title, kind, importance, risk, description or the elements it concerns. Only "
        "when the user asks for it. Not available in preview mode.",
        {
            "type": "object",
            "properties": {"driver": _DRIVER_REF, **_DRIVER_FIELDS},
            "required": ["driver"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "remove_driver",
        "Remove an architecture driver. Only when the user asks for it, and only a driver the user has retired already "
        "(otherwise the call is refused). Not available in preview mode.",
        {
            "type": "object",
            "properties": {"driver": _DRIVER_REF},
            "required": ["driver"],
            "additionalProperties": False,
        },
    ),
]
_PART_TEXTS = {
    "source": "Who or what causes the stimulus (a user group, a system, an attacker, the operator).",
    "stimulus": "The event: what happens ('Was, wenn …').",
    "environment": "The conditions (normal operation, peak load, during a deployment).",
    "artifact": "What is stimulated (the whole system or a component).",
    "response": "How the system should respond.",
    "response_measure": "How the response is measured (e.g. '< 5 min Ausfall', '99,9 % der Anfragen < 300 ms').",
}
_CODE_REF_FIELDS: dict[str, Any] = {
    "element": {"type": "string", "description": "Id or name of the project element."},
    "path": {"type": "string", "description": "Repository-relative file or directory, e.g. src/orders (not absolute)."},
    "repo": {"type": "string", "description": "Optional repository label when the system has several."},
    "symbol": {"type": "string", "description": "Optional class, function or module inside the path."},
}
CODE_REF_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "add_code_ref",
        "Link a project element to the code that implements it (a path in the repository, optionally a symbol). Only "
        "when the user asks for it or names the code. Not available in preview mode.",
        {
            "type": "object",
            "properties": {**_CODE_REF_FIELDS, "note": {"type": "string", "description": "Optional short remark."}},
            "required": ["element", "path"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "remove_code_ref",
        "Remove a code link from a project element (same path, repo and symbol as when it was added). Only when the "
        "user asks for it. Not available in preview mode.",
        {
            "type": "object",
            "properties": _CODE_REF_FIELDS,
            "required": ["element", "path"],
            "additionalProperties": False,
        },
    ),
]
# Decision support (spec: docs/architecture/specs/decision-support.md): reading, and scenarios on request.
SUPPORT_TOOLS: list[ToolSpec] = [
    ToolSpec(
        "list_scenarios",
        "Return the project's quality scenarios (six ATAM parts, the driver each serves, whether it is a core scenario).",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "create_scenario",
        "Record a quality scenario ('what if …') in the six parts of ATAM, serving at most one driver. Only when the user "
        "asks for it; the user analyses it in the Architektur dialog. Not available in preview mode.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": f"Short title, at most {dm.MAX_TITLE} characters."},
                "kind": {"type": "string", "enum": list(dm.SCENARIO_KINDS)},
                **{k: {"type": "string", "description": text} for k, text in _PART_TEXTS.items()},
                "driver": {"type": "string", "description": "Id of the one driver it serves (e.g. d1)."},
                "core": {"type": "boolean", "description": "A core scenario: one of the driver's core goals."},
                "qualities": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(QUALITY_CHARACTERISTICS)},
                    "maxItems": MAX_QUALITIES,
                    "description": "The one, at most two ISO/IEC 25010 quality characteristics the scenario is about.",
                },
                "elements": {"type": "array", "items": {"type": "string"}, "description": "Element ids it concerns."},
            },
            "required": ["title", "kind", "stimulus"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        "list_decisions",
        "Return the project's architecture decisions (ADRs) with status, options, decision and consequences.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        "list_stakeholders",
        "Return the project's stakeholders with role, concerns, influence and the drivers they care about.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
]
DRIVER_TOOL_NAMES = frozenset(t.name for t in [*DRIVER_TOOLS, *SUPPORT_TOOLS])
MODEL_TOOLS = [*MODEL_TOOLS, *RELATION_TOOLS, *DRIVER_TOOLS, *SUPPORT_TOOLS, *CODE_REF_TOOLS]
CODE_REF_TOOL_NAMES = frozenset(t.name for t in CODE_REF_TOOLS)
MODEL_TOOL_NAMES = frozenset(t.name for t in MODEL_TOOLS)
# no active diagram needed
PROJECT_LEVEL_TOOLS = frozenset(
    {
        "list_elements",
        "create_element",
        "update_element",
        "create_relation",
        "update_relation",
        *DRIVER_TOOL_NAMES,
        *CODE_REF_TOOL_NAMES,
    }
)
_CONNECTION_TOOLS = frozenset({"link_relation", "unlink_relation"})


def model_tools_for(diagram_type: str) -> list[ToolSpec]:
    """The model tools of a diagram of this type: the relation link tools only where connections can show relations
    (not activity diagrams, ADR-0139)."""
    if shown_relation_kinds(diagram_type):
        return MODEL_TOOLS
    return [t for t in MODEL_TOOLS if t.name not in _CONNECTION_TOOLS]


def propagated(change: ElementChange | RelationChange | None) -> list[str]:
    """The diagrams a model change wrote into (renamed views, or links that no longer fitted)."""
    if isinstance(change, ElementChange):
        return change.propagated
    if isinstance(change, RelationChange):
        return change.unlinked
    return []


def changed_views(diagram: Diagram, change: ElementChange | RelationChange | None) -> list[str]:
    """The view elements to highlight after a model change reached the diagram."""
    if isinstance(change, ElementChange):
        return [v.id for v in references(diagram, change.element.id)]
    return []


READ_MODEL_TOOLS = frozenset({"list_elements", "list_drivers", "list_scenarios", "list_decisions", "list_stakeholders"})
DRIVER_PREVIEW_REFUSAL = (
    "{name} changes the project's architecture drivers or scenarios, which preview mode does not do; ask the user to "
    "turn the preview off."
)
PREVIEW_REFUSAL = (
    "{name} changes the shared project model, which preview mode does not do; ask the user to turn the preview "
    "off, or propose the diagram changes only."
)


NO_ELEMENTS = "(none yet: every structural node or lifeline you create becomes one)"


@dataclass(frozen=True)
class ArchitectureContext:
    """The texts of the architecture block (spec: decision-support.md): each None when the project has none."""

    drivers: str | None = None
    stakeholders: str | None = None
    decisions: str | None = None
    vision: str | None = None


def architecture_context(project: Project) -> ArchitectureContext:
    """Drivers most important first with their core scenarios, stakeholders, accepted decisions (ADR-0168)."""
    return ArchitectureContext(
        dm.drivers_with_core_scenarios(project.drivers, project.scenarios),
        dm.stakeholder_lines(project.stakeholders, drivers=project.drivers),
        dm.decision_lines(project.decisions),
        vision_lines(project.vision),
    )


def drivers_prompt(drivers: list[Driver]) -> str | None:
    """The drivers alone for a system prompt, most important first; None when the project has none."""
    return drivers_summary(drivers) or None


def _error(call: ToolCall, message: str) -> ToolResult:
    return ToolResult(call.id, json.dumps({"ok": False, "error": message}, ensure_ascii=False), is_error=True)


def _ok(call: ToolCall, payload: dict[str, Any]) -> ToolResult:
    return ToolResult(call.id, json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str))


def _text(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) else None


class ModelTools:
    """The element tools of one turn on a diagram (or a project) that belongs to project `project_id`."""

    def __init__(self, models: ModelService, project_id: str, propose: bool = False):
        self.models = models
        self.project_id = project_id
        self.propose = propose

    def prompt(self) -> str | None:
        """The project's elements and relations for the system prompt (`NO_ELEMENTS` while it has none); None when the
        project cannot be read."""
        return self.context()[0]

    def adopting(
        self, diagram: Diagram, apply_call: Callable[[Diagram], Any], name: str
    ) -> tuple[Callable[[Diagram], Any], Callable[[], Adoption | None]]:
        """An editing call that creates structural views also makes them project elements, in the same write (spec:
        elements-by-default.md, ADR-0164); the tool result then carries `adoption`. Not in propose mode (a proposal is
        one diagram's operations; confirming it adopts) and not for the model's own tools. Call the second function
        once after the write."""
        if self.propose or name in MODEL_TOOL_NAMES:
            return apply_call, lambda: None
        adopter = self.models.project_adopter(self.project_id)
        return adopting(adopter, diagram, apply_call, lambda r: isinstance(r, ToolResult))

    def settle(
        self, diagram: Diagram, result: dict[str, Any]
    ) -> tuple[dict[str, str], Callable[[Diagram], list[str]]] | None:
        """After an adopting call was written: None while every adopted link can stand, else the problems (view id ->
        why) and the change that takes those links back, to run through the turn's write-through (#155, ADR-0128)."""
        adoption = result.get("adoption")
        if not adopted_views(adoption):
            return None
        problems = self.models.adoption_problems(self.project_id, diagram, adoption)
        if not problems:
            return None
        return problems, unlink_change(problems, dict(adopted_views(adoption)))

    @staticmethod
    def settled(result: dict[str, Any], problems: dict[str, str], undone: list[str]) -> dict[str, Any]:
        """The tool result after the take-back: the links taken back move to `adoption.skipped`."""
        reasons = {v: problems[v] for v in undone}
        return {**result, "adoption": without_taken_back(result["adoption"], reasons)} if reasons else result

    def context(self) -> tuple[str | None, ArchitectureContext]:
        """The project's elements and relations, and its architecture context (drivers with their core scenarios,
        stakeholders, accepted decisions), for the system prompt (one project read); None for what it has none of."""
        try:
            project = self.models.project(self.project_id)
        except StoreError:
            return None, ArchitectureContext()
        # the model block is there also without elements: its reuse rule matters from the first node on (ADR-0164)
        model = model_summary(project.elements, relations=project.relations) if project.elements else NO_ELEMENTS
        return model, architecture_context(project)

    def execute(self, call: ToolCall) -> tuple[ToolResult, ElementChange | RelationChange | None]:
        """A project-level call (`PROJECT_LEVEL_TOOLS`): the result, and for `update_element` what it changed."""
        args = call.arguments if isinstance(call.arguments, dict) else {}
        if call.name not in READ_MODEL_TOOLS and self.propose:
            refusal = DRIVER_PREVIEW_REFUSAL if call.name in DRIVER_TOOL_NAMES else PREVIEW_REFUSAL
            return _error(call, refusal.format(name=call.name)), None
        try:
            if call.name in DRIVER_TOOL_NAMES:
                return self._driver(call, args), None
            if call.name in CODE_REF_TOOL_NAMES:
                op = "add_code_ref" if call.name == "add_code_ref" else "remove_code_ref"
                fields = ("element", "path", "repo", "symbol", *(("note",) if op == "add_code_ref" else ()))
                given = {k: v for k in fields if isinstance(v := args.get(k), str)}
                return _ok(call, self.models.code_ref_operation(self.project_id, op, given)), None
            if call.name == "list_elements":
                return _ok(call, self.models.model(self.project_id).to_dict()), None
            if call.name in ("create_relation", "update_relation"):
                return self._relation(call, args)
            if call.name == "create_element":
                name, kind = _text(args, "name"), _text(args, "kind") or "component"
                if not name:
                    return _error(call, "create_element needs a name."), None
                element = self.models.add_element(
                    self.project_id, name, kind, _text(args, "description") or "", _text(args, "parent") or None
                )
                return _ok(call, {"element": element.model_dump()}), None
            element_ref = _text(args, "element")
            if not element_ref:
                return _error(call, "update_element needs the element's id."), None
            change = self.models.update_element(
                self.project_id, element_ref, _text(args, "name"), _text(args, "description"), _text(args, "kind")
            )
            payload = {"element": change.element.model_dump(), "renamed_in": change.propagated}
            if change.failed:
                payload["not_yet_renamed_in"] = change.failed
            return _ok(call, payload), change
        except (ProjectError, operations.OperationError) as exc:
            return _error(call, str(exc)), None
        except Forbidden:  # the caller's role dropped during the turn
            return _error(call, "Your role on this project no longer allows changes; nothing was changed."), None
        except StoreError as exc:
            return _error(call, f"The project model could not be changed: {exc}"), None

    def _driver(self, call: ToolCall, args: dict[str, Any]) -> ToolResult:
        """The driver and decision support tools (errors propagate to `execute`): project reads and operations, no
        diagram is touched."""
        listed = {"list_scenarios": "scenarios", "list_decisions": "decisions", "list_stakeholders": "stakeholders"}
        if call.name in listed:
            items = getattr(self.models.project(self.project_id), listed[call.name])
            return _ok(call, {listed[call.name]: [i.model_dump() for i in items]})
        if call.name == "create_scenario":
            result = self.models.driver_operation(self.project_id, "add_scenario", dict(args))
            return _ok(call, {"scenario": result})
        if call.name == "list_drivers":
            return _ok(call, {"drivers": [d.model_dump() for d in self.models.ranked_drivers(self.project_id)]})
        op = {"create_driver": "add_driver", "update_driver": "update_driver", "remove_driver": "remove_driver"}[
            call.name
        ]
        if "status" in args:  # retiring and reactivating are a person's acts, like deleting (ADR-0198)
            raise ProjectError("Retiring or reactivating a driver is up to the user; the status cannot be set here.")
        result = self.models.driver_operation(self.project_id, op, dict(args))
        return _ok(call, {"driver": result.get("driver", result)})

    def _relation(self, call: ToolCall, args: dict[str, Any]) -> tuple[ToolResult, RelationChange | None]:
        """`create_relation` / `update_relation` (errors propagate to `execute`)."""
        properties = args.get("properties") if isinstance(args.get("properties"), dict) else None
        if call.name == "create_relation":
            kind, source, target = _text(args, "kind"), _text(args, "source"), _text(args, "target")
            if not kind or not source or not target:
                return _error(call, "create_relation needs kind, source and target."), None
            relation = self.models.add_relation(
                self.project_id, kind, source, target, properties, _text(args, "description") or ""
            )
            return _ok(call, {"relation": relation.model_dump()}), None
        relation_ref = _text(args, "relation")
        if not relation_ref:
            return _error(call, "update_relation needs the relation's id."), None
        removed = args.get("remove_properties")
        change = self.models.update_relation(
            self.project_id,
            relation_ref,
            _text(args, "kind"),
            _text(args, "source"),
            _text(args, "target"),
            properties,
            [str(k) for k in removed] if isinstance(removed, list) else None,
            _text(args, "description"),
        )
        payload: dict[str, Any] = {"relation": change.relation.model_dump(), "unlinked_in": change.unlinked}
        if change.failed:
            payload["not_yet_checked_in"] = change.failed
        return _ok(call, payload), change

    def link_relation(self, call: ToolCall) -> Callable[[Diagram], dict[str, Any] | ToolResult] | ToolResult:
        """`link_relation` on the active diagram: the change to run (and re-run after a conflict), or the error."""
        if self.propose:
            return _error(call, PREVIEW_REFUSAL.format(name="link_relation"))
        args = call.arguments if isinstance(call.arguments, dict) else {}
        view, relation_ref = _text(args, "id"), _text(args, "relation")
        if not view or not relation_ref:
            return _error(call, "link_relation needs the edge or message (id) and the relation.")
        try:
            relation = self.models.relation(self.project_id, relation_ref)
        except ProjectError as exc:
            return _error(call, str(exc))
        except Forbidden:
            return _error(call, "Your role on this project no longer allows changes; nothing was changed.")
        except StoreError as exc:
            return _error(call, f"The project model could not be read: {exc}")
        link_args = {
            "id": view,
            "relation": relation.id,
            "kind": relation.kind,
            "source": relation.source,
            "target": relation.target,
        }

        def change(target: Diagram) -> dict[str, Any] | ToolResult:
            try:
                return operations.apply(target, "link_relation", link_args)
            except operations.OperationError as exc:
                return _error(call, str(exc))
            except Exception as exc:  # a defect: `apply` restored the diagram; keep the turn alive
                logger.exception("link_relation failed unexpectedly on '%s'", target.id)
                return _error(call, f"Internal error while linking ({type(exc).__name__}); nothing was changed.")

        return change

    def verify_link(
        self, diagram: Diagram, call: ToolCall
    ) -> tuple[str, Callable[[Diagram], dict[str, Any] | ToolResult]] | None:
        """After a `link_element` call was written: None while the element still suits the view, else the problem and
        the change that takes the link back (only while the view still stands for that element). The element is
        read again, so a kind change that landed between the call's read and its write is seen (#155, ADR-0128); a
        failed read takes the link back too (fail closed)."""
        args = call.arguments if isinstance(call.arguments, dict) else {}
        view = find_view(diagram, _text(args, "id") or "")
        if view is None or view.ref is None:
            return None
        view_id, element_id = view.id, view.ref
        try:
            problem = self.models.link_problem(self.project_id, element_id, diagram.type, view.kind)
        except StoreError as exc:
            problem = f"The project model could not be read to confirm the link ({exc}); the link was taken back."
        if problem is None:
            return None

        def take_back(target: Diagram) -> dict[str, Any] | ToolResult:
            found = find_view(target, view_id)
            if found is None or found.ref != element_id:
                return _error(call, "nothing to take back")
            return operations.apply(target, "unlink_element", {"id": view_id})

        return problem, take_back

    def link(self, call: ToolCall) -> Callable[[Diagram], dict[str, Any] | ToolResult] | ToolResult:
        """`link_element` on the active diagram: the change to run (and re-run after a conflict), or the error."""
        if self.propose:
            return _error(call, PREVIEW_REFUSAL.format(name="link_element"))
        args = call.arguments if isinstance(call.arguments, dict) else {}
        view, element = _text(args, "id"), _text(args, "element")
        if not view or not element:
            return _error(call, "link_element needs the node or lifeline (id) and the element.")
        try:
            link_args = self.models.link_args(self.project_id, view, element)
        except ProjectError as exc:
            return _error(call, str(exc))
        except Forbidden:
            return _error(call, "Your role on this project no longer allows changes; nothing was changed.")
        except StoreError as exc:
            return _error(call, f"The project model could not be read: {exc}")

        def change(target: Diagram) -> dict[str, Any] | ToolResult:
            try:
                return operations.apply(target, "link_element", link_args)
            except operations.OperationError as exc:
                return _error(call, str(exc))
            except Exception as exc:  # a defect: `apply` restored the diagram; keep the turn alive
                logger.exception("link_element failed unexpectedly on '%s'", target.id)
                return _error(call, f"Internal error while linking ({type(exc).__name__}); nothing was changed.")

        return change
