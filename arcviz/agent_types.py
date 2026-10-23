"""Tools of the diagram types other than deployment (those live in `agent.py`; prompts in `arcviz/prompts/`).

Spec: docs/architecture/specs/diagram-types.md. Tool and operation names, descriptions and enumerations
use the UML 2.5.1 terms of the type (ADR-0030). Builders receive the deployment `TOOLS` so a type can
reuse a definition without importing the agent (which imports this module).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping

from . import operations, sequence
from .graph_types import Field, profile_of
from .llm import ToolSpec
from .model import RELATION_DIAGRAM_TYPES, KindCatalog
from .project_model import shown_relation_kinds

# diagram type -> builder(catalog, deployment_tools) -> tools
TOOL_BUILDERS: dict[str, Callable[[KindCatalog, list[ToolSpec]], list[ToolSpec]]] = {}


def build_tools(diagram_type: str, catalog: KindCatalog, deployment_tools: list[ToolSpec]) -> list[ToolSpec]:
    return TOOL_BUILDERS[diagram_type](catalog, deployment_tools)


# ---- activity diagrams (spec: docs/architecture/specs/activity-diagrams.md) ------------------------------

_ACTIVITY_DESCRIPTIONS = {
    "add_node": (
        "Add an ActivityNode (UML 2.5.1 Activities) or an ActivityPartition (swimlane). Kinds: opaque_action "
        "(an action, named by what it does, e.g. 'Bestellung prüfen'), initial_node (start), activity_final_node "
        "(ends the whole activity), flow_final_node (ends one flow only), decision_node / merge_node (diamond; "
        "the guards go on the outgoing edges of a decision), fork_node / join_node (bars that split into and "
        "join parallel flows), central_buffer_node and data_store_node (object nodes). activity_partition is a "
        "container: place nodes into it with `parent`. Returns the created node including its id."
    ),
    "update_node": "Rename an ActivityNode or ActivityPartition, change its kind, set/remove properties, or set its "
    "note (description).",
    "move_node": (
        "Move an ActivityNode into an ActivityPartition, a partition into another partition, or either to the "
        "top level with parent=null. Keeps all edges."
    ),
    "remove_node": (
        "Delete an ActivityNode or ActivityPartition and all edges touching it. By default the contents of a "
        "partition move up one level; with cascade=true they are deleted too."
    ),
    "add_edge": (
        "Add an ActivityEdge from source to target (always directed): control_flow (order of actions and "
        "control nodes) or object_flow (objects/data, usually into or out of object nodes). `guard` is the UML "
        "guard, drawn as [guard]; put guards on the outgoing edges of a decision_node ('ja', 'nein', 'else'). "
        "`label` is the edge's optional name. Rules: no edge may start or end at an activity_partition, an "
        "initial_node has no incoming edges, final nodes have no outgoing edges. Idempotent for identical edges."
    ),
    "update_edge": (
        "Change the name (label), kind, guard (an empty string removes it) or properties of an ActivityEdge."
    ),
}

_GUARD_SCHEMA = {
    "type": "string",
    "description": "UML guard of the edge, shown as [guard], e.g. 'ja', 'nein', 'else', 'betrag > 100'.",
}


def _activity_tools(catalog: KindCatalog, deployment_tools: list[ToolSpec]) -> list[ToolSpec]:
    # activity diagrams show no model relations, so they declare no completeness (ADR-0139)
    tools = copy.deepcopy([t for t in deployment_tools if t.name != "set_completeness"])
    node_kinds, edge_kinds = sorted(catalog.all_kinds), sorted(catalog.edge_kinds)
    for tool in tools:
        props = tool.parameters.get("properties", {})
        tool.description = _ACTIVITY_DESCRIPTIONS.get(tool.name, tool.description)
        if "kind" in props and "enum" in props["kind"]:
            props["kind"]["enum"] = edge_kinds if tool.name.endswith("_edge") else node_kinds
            props["kind"].pop("default", None)
            props["kind"]["description"] = (
                "ActivityEdge kind." if tool.name.endswith("_edge") else "UML metaclass of the element."
            )
        if tool.name in ("add_edge", "update_edge"):
            props.pop("direction", None)
            props["guard"] = _GUARD_SCHEMA
        if tool.name == "add_node":
            props["parent"]["description"] = "Id (or exact name) of the activity_partition to place it in."
        if tool.name == "apply_batch":
            step = props["operations"]["items"]["properties"]["op"]
            step["enum"] = operations.batch_operations("activity")
        if tool.name == "get_diagram":
            props["focus"]["description"] = "Id of an activity_partition whose contents to return."
    return tools


TOOL_BUILDERS["activity"] = _activity_tools


# ---- graph types with a profile (spec: docs/architecture/specs/uml-graph-types.md) ----------------------------------


def _fields_schema(table: Mapping[str, tuple[Field, ...]]) -> dict | None:
    """The `fields` argument of a node or edge tool: every field of the type, with the kinds it belongs to. The
    operation checks the kind; JSON schema cannot say it here without `oneOf` per kind."""
    by_name: dict[str, tuple[Field, list[str]]] = {}
    for kind, fields in sorted(table.items()):
        for spec in fields:
            by_name.setdefault(spec.name, (spec, []))[1].append(kind)
    if not by_name:
        return None
    properties: dict[str, dict] = {}
    for name, (spec, kinds) in by_name.items():
        text = f"{spec.help} Only for {', '.join(kinds)}. At most {spec.max_length} characters"
        if spec.flag:
            properties[name] = {"type": "boolean", "description": f"{spec.help} Only for {', '.join(kinds)}."}
        elif spec.is_list:
            properties[name] = {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": spec.max_items,
                "description": f"{text} per line, at most {spec.max_items} lines; replaces the list, [] removes it.",
            }
        else:
            properties[name] = {"type": "string", "description": f"{text}; an empty string removes it."}
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "description": "Typed values of the element (not free properties).",
    }


def _graph_builder(
    diagram_type: str,
    descriptions: dict[str, str],
    *,
    node_kind_help: str,
    edge_kind_help: str,
    parent_help: str,
    focus_help: str,
) -> Callable[[KindCatalog, list[ToolSpec]], list[ToolSpec]]:
    """The tool builder of a graph type: the deployment tools with the type's descriptions, kind enums, guard, typed
    fields and operation list, and without the direction (when edges are always directed) and the completeness tool."""

    def build(catalog: KindCatalog, deployment_tools: list[ToolSpec]) -> list[ToolSpec]:
        profile = profile_of(diagram_type)
        assert profile is not None
        completeness = diagram_type in RELATION_DIAGRAM_TYPES  # connectors show relations (ADR-0181)
        tools = copy.deepcopy([t for t in deployment_tools if completeness or t.name != "set_completeness"])
        node_kinds, edge_kinds = sorted(catalog.all_kinds), sorted(catalog.edge_kinds)
        for tool in tools:
            props = tool.parameters.get("properties", {})
            edge_tool = tool.name.endswith("_edge")
            if tool.name == "set_completeness":
                props["kinds"]["items"]["enum"] = ["all", *shown_relation_kinds(diagram_type)]
                continue
            tool.description = descriptions.get(tool.name, tool.description)
            if "kind" in props and "enum" in props["kind"]:
                props["kind"]["enum"] = edge_kinds if edge_tool else node_kinds
                props["kind"].pop("default", None)
                props["kind"]["description"] = edge_kind_help if edge_tool else node_kind_help
            if tool.name in ("add_edge", "update_edge"):
                if profile.directed_noun:
                    props.pop("direction", None)
                if profile.guard:
                    props["guard"] = _GUARD_SCHEMA
            if tool.name in ("add_node", "update_node", "add_edge", "update_edge"):
                fields = _fields_schema(profile.edge_fields if edge_tool else profile.node_fields)
                if fields is not None:
                    props["fields"] = fields
            if tool.name == "add_node":
                props["parent"]["description"] = parent_help
            if tool.name == "apply_batch":
                props["operations"]["items"]["properties"]["op"]["enum"] = operations.batch_operations(diagram_type)
            if tool.name == "get_diagram":
                props["focus"]["description"] = focus_help
        return tools

    return build


# ---- state machine diagrams (spec: docs/architecture/specs/state-machine-diagrams.md) -----------------------------

_STATE_MACHINE_DESCRIPTIONS = {
    "add_node": (
        "Add a vertex of the state machine (UML 2.5.1 State Machines). Kinds: state (a simple state, named by a noun "
        "or adjective; optional behaviours in `fields`: entry, do, exit), composite_state (contains states, or "
        "state_regions), state_region (one parallel part of a composite_state; only inside one, which then holds "
        "regions only), initial_pseudostate (where a scope starts; exactly one outgoing transition), final_state, "
        "terminate_pseudostate, choice_pseudostate (decision with guards on its outgoing transitions), "
        "junction_pseudostate, fork_pseudostate / join_pseudostate (split into and merge parallel regions), "
        "shallow_history_pseudostate (H) and deep_history_pseudostate (H*). Containers take children through "
        "`parent`. Returns the created node including its id."
    ),
    "update_node": (
        "Rename a state or pseudostate, change its kind (a state becomes composite with kind=composite_state), set "
        "its fields (entry, do, exit; an empty string removes one), properties or note (description)."
    ),
    "move_node": (
        "Move a vertex into a composite_state or state_region, a region into another composite_state, or to the "
        "top level with parent=null. Keeps all transitions."
    ),
    "remove_node": (
        "Delete a state, pseudostate or container and all transitions touching it. By default the contents of a "
        "composite_state move up one level; with cascade=true they are deleted too."
    ),
    "add_edge": (
        "Add a transition (always directed) from source to target. `label` is the trigger (the event that fires "
        "it, e.g. 'bezahlen'), `guard` the condition shown as [guard], `fields.effect` what happens on the way; "
        "drawn as `trigger [guard] / effect`. Rules: no transition at a state_region, none into an "
        "initial_pseudostate, none out of a final_state or terminate_pseudostate, and a transition out of an "
        "initial_pseudostate or fork_pseudostate (or into a join_pseudostate) has no trigger and no guard. "
        "Idempotent for identical transitions."
    ),
    "update_edge": (
        "Change the trigger (label), guard (an empty string removes it), `fields.effect` or properties of a transition."
    ),
}

TOOL_BUILDERS["state_machine"] = _graph_builder(
    "state_machine",
    _STATE_MACHINE_DESCRIPTIONS,
    node_kind_help="UML metaclass of the vertex (state, pseudostate, final state, composite state or region).",
    edge_kind_help="Transition kind.",
    parent_help="Id (or exact name) of the composite_state or state_region to place it in.",
    focus_help="Id of a composite_state or state_region whose contents to return.",
)


# ---- class diagrams (spec: docs/architecture/specs/class-diagrams.md) --------------------------------------------------

_CLASS_DESCRIPTIONS = {
    "add_node": (
        "Add a classifier or a package to the class diagram (UML 2.5.1 Classification). Kinds: class (a noun of the "
        "domain or design), interface (operations only), enumeration (named values), data_type (a value without "
        "identity), package (a container for classifiers and packages, placed with `parent`). Members go into `fields`: "
        "attributes and operations as UML text lines ('- id: UUID', '+ place(): Order'), literals for an enumeration, "
        "abstract=true for an abstract class. Returns the created node including its id."
    ),
    "update_node": (
        "Rename a classifier or package, change its kind, replace its members (`fields`: attributes, operations, "
        "literals, abstract; an empty list removes one), set properties or its note (description)."
    ),
    "move_node": "Move a classifier or package into a package, or to the top level with parent=null.",
    "remove_node": (
        "Delete a classifier or package and all relationships touching it. By default the contents of a package move up "
        "one level; with cascade=true they are deleted too."
    ),
    "add_edge": (
        "Add a relationship. Every edge points from the element that refers to the other to the element it refers to: "
        "association (plain line; direction=forward makes it navigable to the target; `label` is its name; "
        "`fields` source_multiplicity, target_multiplicity, source_role, target_role), aggregation and composition "
        "(from the PART to the WHOLE: Position -> Bestellung; classes only), generalization (from the specific to the "
        "general classifier, same kind on both ends, no cycles), realization (from a class to an interface), "
        "dependency (client to supplier; the only edge a package may take part in). Idempotent for identical edges."
    ),
    "update_edge": (
        "Change the name (label), kind, direction (association only), `fields` (multiplicities, roles; an empty "
        "string removes one) or properties of a relationship."
    ),
}

TOOL_BUILDERS["class"] = _graph_builder(
    "class",
    _CLASS_DESCRIPTIONS,
    node_kind_help="UML metaclass of the element (class, interface, enumeration, data_type or package).",
    edge_kind_help=(
        "Relationship kind; aggregation and composition point from the part to the whole, generalization from the "
        "specific to the general classifier."
    ),
    parent_help="Id (or exact name) of the package to place it in.",
    focus_help="Id of a package whose contents to return.",
)


# ---- use case diagrams (spec: docs/architecture/specs/use-case-diagrams.md) --------------------------------------------

_USE_CASE_DESCRIPTIONS = {
    "add_node": (
        "Add an element of the use case diagram (UML 2.5.1 Use Cases). Kinds: actor (a role outside the system: a person, "
        "organisation or other system), use_case (a goal of an actor that the system supports, named as a verb phrase; "
        "its written specification goes into `description`), subject (the system boundary: a container; place the use "
        "cases of the system into it with `parent`). Actors and subjects stand at the top level. Returns the created "
        "node including its id."
    ),
    "update_node": (
        "Rename an actor, use case or subject, change its kind, set properties, or set its note (description): for a use "
        "case the specification (goal, preconditions, flow, alternatives)."
    ),
    "move_node": "Move a use case into a subject, or to the top level with parent=null. Actors and subjects stay at the top level.",
    "remove_node": (
        "Delete an element and all edges touching it. By default the use cases of a subject move up one level; with "
        "cascade=true they are deleted too."
    ),
    "add_edge": (
        "Add a relationship. Edges point from the element that refers to the other to the element it refers to: "
        "association (an actor takes part in a use case, either way round; direction=forward shows who starts it), "
        "include (from the base use case to the one it always uses), extend (from the extending use case to the extended "
        "one; `fields` condition and extension_point), generalization (from the specific to the general actor or use "
        "case). Includes and generalizations never form a cycle. A subject has no edges. Idempotent for identical edges."
    ),
    "update_edge": (
        "Change the name (label), kind, direction (association only), `fields` (the condition and extension point of an "
        "extend; an empty string removes one) or properties of a relationship."
    ),
}

TOOL_BUILDERS["use_case"] = _graph_builder(
    "use_case",
    _USE_CASE_DESCRIPTIONS,
    node_kind_help="UML metaclass of the element (actor, use_case or subject).",
    edge_kind_help="Relationship kind; include and extend are drawn with their keyword, do not repeat it in the label.",
    parent_help="Id (or exact name) of the subject to place the use case in.",
    focus_help="Id of a subject whose contents to return.",
)


# ---- component diagrams (spec: docs/architecture/specs/component-diagrams.md) ------------------------------------------

_COMPONENT_DESCRIPTIONS = {
    "add_node": (
        "Add an element of the component diagram (UML 2.5.1 Components). Kinds: component (a building block with one "
        "responsibility; its ports are `fields.ports`, one line each, e.g. 'orders-in: OrderApi'), subsystem (contains "
        "components, interfaces and subsystems, placed with `parent`), interface (a contract: provided by a realization, "
        "required by a usage), package (groups building blocks; only dependencies). Returns the created node "
        "including its id."
    ),
    "update_node": (
        "Rename an element, change its kind, replace its ports (`fields.ports`; an empty list removes them; a port that "
        "an edge names cannot be removed), set properties or its note (description)."
    ),
    "move_node": "Move an element into a subsystem or package, or to the top level with parent=null.",
    "remove_node": (
        "Delete an element and all edges touching it. By default the contents of a subsystem or package move up one "
        "level; with cascade=true they are deleted too."
    ),
    "add_edge": (
        "Add an edge. Edges point from the element that refers to the other to the element it refers to: connection "
        "(from a component or subsystem to another one, requirer to provider; `fields` source_port and target_port name a "
        "port of the ends), realization (a component PROVIDES an interface: component -> interface; `fields.source_port`), "
        "usage (a component REQUIRES an interface: component -> interface), dependency (a looser 'uses' between any two "
        "elements). No edge joins a node with its own subsystem or package; a package takes part only in dependencies. "
        "Idempotent for identical edges."
    ),
    "update_edge": (
        "Change the name (label), kind, direction, `fields` (the ports of the ends; an empty string removes one) or "
        "properties (e.g. protocol=https) of an edge."
    ),
}

TOOL_BUILDERS["component"] = _graph_builder(
    "component",
    _COMPONENT_DESCRIPTIONS,
    node_kind_help="UML metaclass of the element (component, subsystem, interface or package).",
    edge_kind_help=(
        "Edge kind; realization and usage go from a component to an interface, connection from a component to another one."
    ),
    parent_help="Id (or exact name) of the subsystem or package to place it in.",
    focus_help="Id of a subsystem or package whose contents to return.",
)


# ---- sequence diagrams (spec: docs/architecture/specs/sequence-diagrams.md) ------------------------------

_ID = {"type": "string", "description": "Optional stable id; generated if omitted."}
_NOTE = {
    "type": "string",
    "description": "Optional note about the participant (max 2000 characters); on update an empty string removes it.",
}
_LIFELINE_REF = {"type": "string", "description": "Id (or exact name) of a Lifeline."}
_COVERED = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Ids of the Lifelines the fragment covers. Omit to cover all lifelines.",
}
_OPERAND = {
    "type": "string",
    "description": "Id of the InteractionOperand to place it in (inside a CombinedFragment). Omit for the top level.",
}
_BEFORE = {"type": "string", "description": "Id of a fragment to insert it before. Omit to append at the end."}


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _sequence_tools(catalog: KindCatalog, deployment_tools: list[ToolSpec]) -> list[ToolSpec]:
    sorts, kinds, operators = (
        list(sequence.MESSAGE_SORTS),
        list(sequence.MESSAGE_KINDS),
        list(sequence.INTERACTION_OPERATORS),
    )
    shared = {
        t.name: copy.deepcopy(t)
        for t in deployment_tools
        if t.name in ("rename_diagram", "apply_batch", "set_completeness")
    }
    batch = shared["apply_batch"]
    batch.parameters["properties"]["operations"]["items"]["properties"]["op"]["enum"] = operations.batch_operations(
        "sequence"
    )
    multi = ", ".join(sorted(sequence.MULTI_OPERAND_OPERATORS))
    return [
        ToolSpec(
            "add_lifeline",
            "Add a Lifeline (a participant of the Interaction), placed rightmost or before another lifeline. "
            "`type` is the classifier of the represented participant, shown as 'name : type'; actor=true draws it "
            "as an Actor (stick figure).",
            _object(
                {
                    "name": {"type": "string"},
                    "type": {"type": "string", "description": "Classifier name, e.g. 'OrderService'."},
                    "actor": {"type": "boolean"},
                    "id": _ID,
                    "before": {"type": "string", "description": "Id of the lifeline to insert it before."},
                    "description": _NOTE,
                },
                ["name"],
            ),
        ),
        ToolSpec(
            "update_lifeline",
            "Rename a Lifeline, change its type (empty string removes it), whether it is an actor, or its note.",
            _object(
                {
                    "id": _LIFELINE_REF,
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "actor": {"type": "boolean"},
                    "description": _NOTE,
                },
                ["id"],
            ),
        ),
        ToolSpec(
            "move_lifeline",
            "Reorder a Lifeline: move it before another lifeline, or rightmost without `before`.",
            _object({"id": _LIFELINE_REF, "before": {"type": "string"}}, ["id"]),
        ),
        ToolSpec(
            "remove_lifeline",
            "Remove a Lifeline together with its Messages, ExecutionSpecifications and destruction; it is dropped "
            "from the covered lifelines of fragments.",
            _object({"id": _LIFELINE_REF}, ["id"]),
        ),
        ToolSpec(
            "add_message",
            "Add a Message from sender to receiver at a position in time (appended at the end unless `before` or "
            "`operand` say otherwise). messageSort: synchCall (call, waits), asynchCall, asynchSignal, reply (return "
            "of a call), createMessage (creates the receiver, whose lifeline starts here), deleteMessage (destroys the "
            "receiver). messageKind follows from the ends: omit `sender` for a found message, `receiver` for a lost "
            "one. A sender equal to the receiver is a self message. `name` is the signature or signal, e.g. "
            "'placeOrder(id)', or the return value of a reply.",
            _object(
                {
                    "name": {"type": "string"},
                    "sender": _LIFELINE_REF,
                    "receiver": _LIFELINE_REF,
                    "sort": {"type": "string", "enum": sorts, "default": "synchCall"},
                    "kind": {"type": "string", "enum": kinds, "description": "Optional; derived from the ends."},
                    "operand": _OPERAND,
                    "before": _BEFORE,
                    "id": _ID,
                },
                ["name"],
            ),
        ),
        ToolSpec(
            "update_message",
            "Change a Message's name, messageSort, sender or receiver ('' removes an end: found/lost).",
            _object(
                {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "sort": {"type": "string", "enum": sorts},
                    "kind": {"type": "string", "enum": kinds},
                    "sender": {"type": "string"},
                    "receiver": {"type": "string"},
                },
                ["id"],
            ),
        ),
        ToolSpec(
            "add_combined_fragment",
            "Add a CombinedFragment with its first InteractionOperand (guard = InteractionConstraint, e.g. 'x > 0', "
            "'else'). interactionOperator: alt (alternatives), opt (optional), loop, par (parallel), break, critical, "
            "neg (invalid traces), assert, strict, seq, ignore, consider (the last two list message names in "
            f"`messages`). Only {multi} may have several operands; add more with add_interaction_operand. "
            "`contents` moves existing sibling fragments (e.g. messages) into the first operand; the fragment takes "
            "the place of the first of them. Returns the fragment and the operand id.",
            _object(
                {
                    "operator": {"type": "string", "enum": operators},
                    "covered": _COVERED,
                    "guard": {"type": "string"},
                    "contents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ids of fragments to wrap.",
                    },
                    "messages": {"type": "array", "items": {"type": "string"}, "description": "ignore/consider only."},
                    "operand": _OPERAND,
                    "before": _BEFORE,
                    "id": _ID,
                },
                ["operator"],
            ),
        ),
        ToolSpec(
            "update_combined_fragment",
            "Change a CombinedFragment's interactionOperator, covered lifelines or (ignore/consider) messages.",
            _object(
                {
                    "id": {"type": "string"},
                    "operator": {"type": "string", "enum": operators},
                    "covered": _COVERED,
                    "messages": {"type": "array", "items": {"type": "string"}},
                },
                ["id"],
            ),
        ),
        ToolSpec(
            "add_interaction_operand",
            f"Add an InteractionOperand (with an optional guard) to a CombinedFragment of kind {multi}.",
            _object(
                {
                    "fragment": {"type": "string", "description": "Id of the CombinedFragment."},
                    "guard": {"type": "string"},
                    "before": {"type": "string", "description": "Id of an operand to insert it before."},
                },
                ["fragment"],
            ),
        ),
        ToolSpec(
            "update_interaction_operand",
            "Change the guard of an InteractionOperand (empty string removes it).",
            _object({"id": {"type": "string"}, "guard": {"type": "string"}}, ["id", "guard"]),
        ),
        ToolSpec(
            "add_interaction_use",
            "Add an InteractionUse ('ref' frame) that refers to another interaction by name, covering lifelines.",
            _object(
                {
                    "refers_to": {"type": "string"},
                    "covered": _COVERED,
                    "operand": _OPERAND,
                    "before": _BEFORE,
                    "id": _ID,
                },
                ["refers_to"],
            ),
        ),
        ToolSpec(
            "update_interaction_use",
            "Change what an InteractionUse refers to or the lifelines it covers.",
            _object({"id": {"type": "string"}, "refers_to": {"type": "string"}, "covered": _COVERED}, ["id"]),
        ),
        ToolSpec(
            "add_execution_specification",
            "Add an ExecutionSpecification (activation bar) on a lifeline from the start message to the finish "
            "message (ids; both must involve the lifeline, finish not before start). Without finish it lasts until "
            "the lifeline's last message. Typical: start = the call received, finish = the reply sent.",
            _object(
                {"lifeline": _LIFELINE_REF, "start": {"type": "string"}, "finish": {"type": "string"}, "id": _ID},
                ["lifeline", "start"],
            ),
        ),
        ToolSpec(
            "add_destruction_occurrence_specification",
            "Add a DestructionOccurrenceSpecification (X): the lifeline ends here. A deleteMessage destroys its "
            "receiver by itself; use this for a lifeline that terminates on its own.",
            _object({"lifeline": _LIFELINE_REF, "operand": _OPERAND, "before": _BEFORE, "id": _ID}, ["lifeline"]),
        ),
        ToolSpec(
            "move_fragment",
            "Move a fragment (message, combined fragment, interaction use, destruction) to another position: into an "
            "operand, before another fragment, or to the end of the top level when both are omitted.",
            _object({"id": {"type": "string"}, "operand": _OPERAND, "before": _BEFORE}, ["id"]),
        ),
        ToolSpec(
            "remove_fragment",
            "Remove a fragment with everything inside it, an InteractionOperand (not the last one of its fragment) "
            "or an ExecutionSpecification. Removing a message also removes activations that start or end at it.",
            _object({"id": {"type": "string"}}, ["id"]),
        ),
        shared["rename_diagram"],
        _sequence_completeness(shared["set_completeness"]),
        batch,
        ToolSpec(
            "get_diagram",
            "Return the whole interaction as JSON: lifelines left to right, fragments top to bottom (nested in "
            "operands), execution specifications.",
            _object({}, []),
        ),
    ]


def _sequence_completeness(tool: ToolSpec) -> ToolSpec:
    tool.parameters["properties"]["kinds"]["items"]["enum"] = ["all", "communicates"]
    return tool


TOOL_BUILDERS["sequence"] = _sequence_tools
