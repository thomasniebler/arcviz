"""Hypothesis strategies for diagrams and operations (spec: docs/architecture/specs/property-tests.md).

Operations are drawn against the *current* diagram with ``st.data()``, so references hit existing ids and
names often enough to exercise the success paths, while unknown references, unknown kinds and junk values
exercise the rollback paths.
"""

from __future__ import annotations

from typing import Any

from hypothesis import strategies as st

from arcviz import sequence
from arcviz.model import DIRECTIONS, EDGE_DIRECTIONS, MAX_DESCRIPTION, Diagram, kinds_for
from arcviz.project_model import DRIVER_KINDS, DRIVER_LEVELS, ELEMENT_KINDS, RELATION_KINDS

# Strings that have broken (or could break) Mermaid, PlantUML or HTML: markup, quotes, entities, keywords,
# arrows, brackets, padding, control characters.
HOSTILE = [
    "<b>x</b>",
    '"quoted"',
    "&amp;",
    "a & b",
    "end",
    "subgraph",
    "class",
    "#lt;",
    "-->",
    "a|b",
    "[x]",
    "{y}",
    "(z)",
    "%%",
    ";",
    "  padded  ",
    "Ünïcödé ß",
    "flowchart TB",
    "~~tilde~~",
    "back\\slash",
    "two\nlines",
    "tab\there",
    "cr\r\nlf",
    "nul\x00bell\x07",
    "c1\x85\x9b",
    "sep\u2028par\u2029",
]

# Control characters and line breaks included: the operations make names one line (#103, ADR-0160). Lone surrogates
# are left out: they cannot be encoded as UTF-8, so no JSON request can carry them.
_CHARS = st.characters(exclude_categories=("Cs",))

texts = st.one_of(st.sampled_from(HOSTILE), st.text(_CHARS, max_size=10))
names = st.one_of(st.sampled_from(HOSTILE), st.text(_CHARS, min_size=1, max_size=10))
# notes (spec: collaboration.md): text, "" (removes it), blanks, and now and then one over the limit
notes = st.one_of(texts, st.sampled_from(["", "  ", "x" * (MAX_DESCRIPTION + 1)]))
properties = st.dictionaries(texts, texts, max_size=3)

JUNK_KINDS = ["", "  ", "nope", "SERVICE ", "Database"]

# Values of the wrong JSON type for any argument (issue #66): validated before the first change.
ILL_TYPED = [42, -1.5, True, None, [], ["x", 7], {}, {"k": {"nested": 1}}, "not-a-list"]


def weighted(*choices: tuple[int, st.SearchStrategy[Any]]) -> st.SearchStrategy[Any]:
    """Pick a strategy with roughly the given weights. `one_of`, `sampled_from` and `integers` are not
    uniform: Hypothesis favours the first and last entries, which would make junk as common as valid input.
    So the heaviest choice sits at both ends of the table (it is also where shrinking goes)."""
    ordered = sorted(choices, key=lambda c: -c[0])
    table = [strategy for weight, strategy in ordered for _ in range(weight)] + [ordered[0][1]]
    return st.integers(0, len(table) - 1).flatmap(lambda i: table[i])


def mostly(valid: st.SearchStrategy[Any], junk: list[Any]) -> st.SearchStrategy[Any]:
    """`valid` seven times out of eight, otherwise one of the `junk` values."""
    return weighted((7, valid), (1, st.sampled_from(junk)))


def _node_kinds(diagram: Diagram) -> list[str]:
    return sorted(kinds_for(diagram.type).all_kinds)


def node_ref(data: st.DataObject, diagram: Diagram, *, containers_only: bool = False) -> str:
    """An existing id, an existing name in other case, or (rarely) something unknown."""
    nodes = [n for n in diagram.nodes.values() if not containers_only or n.is_container]
    if not nodes:
        return data.draw(names)
    node = st.sampled_from(sorted(nodes, key=lambda n: n.id))
    return data.draw(weighted((1, names), (1, node.map(lambda n: n.name.upper())), (8, node.map(lambda n: n.id))))


def _edge_ref(data: st.DataObject, diagram: Diagram) -> str:
    ids = [e.id for e in diagram.edges]
    return data.draw(weighted((1, texts), (7, st.sampled_from(ids))) if ids else texts)


def _maybe(data: st.DataObject, strategy: st.SearchStrategy[Any]) -> Any:
    return data.draw(weighted((1, st.none()), (1, strategy)))


def _edge_direction(data: st.DataObject) -> Any:
    return _maybe(data, mostly(st.sampled_from([*EDGE_DIRECTIONS, "default", "Both "]), ["sideways", ""]))


def _edge_kind(data: st.DataObject, diagram: Diagram) -> str:
    return data.draw(mostly(st.sampled_from(sorted(kinds_for(diagram.type).edge_kinds)), JUNK_KINDS))


def _node_kind(data: st.DataObject, diagram: Diagram) -> str:
    return data.draw(mostly(st.sampled_from(_node_kinds(diagram)), JUNK_KINDS))


def ill_typed(data: st.DataObject, args: Any) -> Any:
    """`args` with one argument replaced by a value of the wrong type, dropped, or added; rarely not an object."""
    choice = data.draw(
        weighted((6, st.just("replace")), (2, st.just("drop")), (1, st.just("add")), (1, st.just("args")))
    )
    if choice == "args" or not isinstance(args, dict):
        return data.draw(st.sampled_from(["x", 1, ["name"], None]))
    args = dict(args)
    if choice == "add" or not args:
        args[data.draw(st.sampled_from(["bogus", "Name", "node_id"]))] = data.draw(st.sampled_from(ILL_TYPED))
    elif choice == "drop":
        del args[data.draw(st.sampled_from(sorted(args)))]
    else:
        args[data.draw(st.sampled_from(sorted(args)))] = data.draw(st.sampled_from(ILL_TYPED))
    return args


def operation(data: st.DataObject, diagram: Diagram, *, batch: bool = True) -> tuple[str, Any]:
    """One `(op, args)` against `diagram`; about a third of them are invalid by construction, one in ten
    because of an argument of the wrong type (or a missing or unknown one)."""
    op, args = _well_typed_operation(data, diagram, batch=batch)
    if data.draw(st.integers(0, 9)) == 0:
        args = ill_typed(data, args)
    return op, args


# A synthetic element catalogue for the model-link operations (spec: shared-project-model.md): the diagram
# operations take the element's id, name and kind from the model service, so any values exercise them.
ELEMENT_IDS = ["orders", "payments", "shop", "db"]


def model_link_operation(data: st.DataObject, op: str, view: str) -> dict[str, Any]:
    """Arguments of `link_element`, `unlink_element`, `sync_element` or `clear_element` for the view element `view`."""
    element = data.draw(mostly(st.sampled_from(ELEMENT_IDS), ["", "Not A Slug"]))
    if op == "link_element":
        kind = data.draw(mostly(st.sampled_from(ELEMENT_KINDS), ["robot", ""]))
        return {"id": view, "element": element, "name": data.draw(mostly(names, ["", "  "])), "element_kind": kind}
    if op == "unlink_element":
        return {"id": view}
    if op == "sync_element":
        return {"element": element, "name": data.draw(mostly(names, ["", "  "]))}
    return {"element": element}


# Relations (spec: model-relations.md): the diagram operations take the relation's kind and ends from the model
# service, so any values exercise them; the ends are drawn from ELEMENT_IDS, which the links of views also use.
RELATION_IDS = ["r1", "r2", "r3"]
RELATION_OPS = ("link_relation", "unlink_relation", "sync_relation", "clear_relation", "set_relation_hidden")
COMPLETENESS = st.lists(st.sampled_from(["all", *RELATION_KINDS, "contains"]), max_size=3)


def relation_link_operation(data: st.DataObject, op: str, connection: str) -> dict[str, Any]:
    """Arguments of `link_relation`, `unlink_relation`, `sync_relation` or `clear_relation`."""
    relation = data.draw(mostly(st.sampled_from(RELATION_IDS), ["", "Not A Slug"]))
    if op == "unlink_relation":
        return {"id": connection}
    if op == "clear_relation":
        return {"relation": relation}
    if op == "set_relation_hidden":
        return {"relation": relation, "hidden": data.draw(st.booleans())}
    args = {
        "relation": relation,
        "kind": data.draw(mostly(st.sampled_from(RELATION_KINDS), ["contains"])),
        "source": data.draw(st.sampled_from(ELEMENT_IDS)),
        "target": data.draw(st.sampled_from(ELEMENT_IDS)),
    }
    return {"id": connection, **args} if op == "link_relation" else args


def _well_typed_operation(data: st.DataObject, diagram: Diagram, *, batch: bool) -> tuple[str, dict[str, Any]]:
    if diagram.type == "sequence":
        return sequence_operation(data, diagram, batch=batch)
    weights = {"unknown_kwarg": 1, "rename_diagram": 1, "set_direction": 1, "remove_node": 2, "remove_edge": 2}
    weights |= {"link_element": 3, "unlink_element": 1, "sync_element": 1, "clear_element": 1}
    weights |= {"link_relation": 3, "unlink_relation": 1, "sync_relation": 1, "clear_relation": 1}
    weights |= {"set_relation_hidden": 1, "set_completeness": 1}
    weights |= {"update_node": 3, "update_edge": 3, "move_node": 4, "add_edge": 7, "add_node": 8}
    if batch:
        weights["apply_batch"] = 2
    op = data.draw(weighted(*((w, st.just(name)) for name, w in weights.items())))
    args: dict[str, Any]
    if op == "add_node":
        args = {"name": data.draw(mostly(names, ["", "   "])), "kind": _node_kind(data, diagram)}
        if data.draw(st.integers(0, 3)):
            args["parent"] = node_ref(data, diagram, containers_only=data.draw(mostly(st.just(True), [False])))
        if data.draw(st.integers(0, 4)) == 0:
            args["id"] = data.draw(weighted((3, texts), (1, st.sampled_from(["", " ", "   "]))))
        if data.draw(st.booleans()):
            args["properties"] = data.draw(properties)
        if data.draw(st.integers(0, 3)) == 0:
            args["description"] = data.draw(notes)
    elif op == "update_node":
        args = {"id": node_ref(data, diagram)}
        if data.draw(st.booleans()):
            args["name"] = data.draw(texts)
        if data.draw(st.booleans()):
            args["kind"] = _node_kind(data, diagram)
        if data.draw(st.booleans()):
            args["properties"] = data.draw(properties)
        if data.draw(st.booleans()):
            args["remove_properties"] = data.draw(st.lists(texts, max_size=2))
        if data.draw(st.integers(0, 2)) == 0:
            args["description"] = data.draw(notes)
    elif op == "move_node":
        parent = (
            None
            if data.draw(st.integers(0, 3)) == 0
            else node_ref(data, diagram, containers_only=data.draw(mostly(st.just(True), [False])))
        )
        args = {"id": node_ref(data, diagram), "parent": parent}
    elif op == "remove_node":
        args = {"id": node_ref(data, diagram), "cascade": data.draw(st.booleans())}
    elif op == "add_edge":
        args = {"source": node_ref(data, diagram), "target": node_ref(data, diagram), "kind": _edge_kind(data, diagram)}
        args["label"] = _maybe(data, texts)
        args["direction"] = _edge_direction(data)
        args["properties"] = _maybe(data, properties)
        if data.draw(st.integers(0, 2)) == 0:  # a guard is valid in activity diagrams only
            args["guard"] = data.draw(texts)
    elif op == "update_edge":
        args = {"id": _edge_ref(data, diagram), "label": _maybe(data, texts), "direction": _edge_direction(data)}
        if data.draw(st.booleans()):
            args["kind"] = _edge_kind(data, diagram)
        if data.draw(st.booleans()):
            args["properties"] = data.draw(properties)
            args["remove_properties"] = data.draw(st.lists(texts, max_size=2))
        if data.draw(st.integers(0, 2)) == 0:
            args["guard"] = data.draw(texts)
    elif op == "remove_edge":
        args = (
            {"id": _edge_ref(data, diagram)}
            if data.draw(st.booleans())
            else {"source": node_ref(data, diagram), "target": node_ref(data, diagram)}
        )
    elif op == "rename_diagram":
        args = {"title": data.draw(st.one_of(names, st.just("  ")))}
    elif op == "set_direction":
        args = {"direction": data.draw(mostly(st.sampled_from([*DIRECTIONS, "td", " lr "]), ["XY", ""]))}
    elif op in ("link_element", "unlink_element", "sync_element", "clear_element"):
        args = model_link_operation(data, op, node_ref(data, diagram))
    elif op in RELATION_OPS:
        args = relation_link_operation(data, op, _edge_ref(data, diagram))
    elif op == "set_completeness":
        args = {"kinds": data.draw(COMPLETENESS)}
    elif op == "unknown_kwarg":  # an unknown argument: must become an OperationError
        op, args = "add_node", {"name": "x", "bogus": 1}
    else:
        steps = [operation(data, diagram, batch=False) for _ in range(data.draw(st.integers(1, 4)))]
        args = {"operations": [{"op": o, "args": a} for o, a in steps]}
    return op, args


# ---- sequence diagrams (spec: docs/architecture/specs/sequence-diagrams.md) --------------------------------


def _lifeline_ref(data: st.DataObject, diagram: Diagram) -> str:
    lifelines = diagram.interaction.lifelines if diagram.interaction else []
    if not lifelines:
        return data.draw(names)
    lifeline = st.sampled_from([lf.id for lf in lifelines])
    by_name = st.sampled_from([lf.name.upper() for lf in lifelines])
    return data.draw(weighted((1, names), (1, by_name), (8, lifeline)))


def _ids(diagram: Diagram, wanted: tuple[type, ...] | None = None) -> list[str]:
    interaction = diagram.interaction
    if interaction is None:
        return []
    out = []
    for element_id in sequence.element_ids(interaction):
        element = sequence.find(interaction, element_id)
        if wanted is None or isinstance(element, wanted):
            out.append(element_id)
    return out


def _ref(data: st.DataObject, ids: list[str]) -> str:
    return data.draw(weighted((1, texts), (7, st.sampled_from(ids))) if ids else texts)


def _covered(data: st.DataObject, diagram: Diagram) -> Any:
    lifelines = [lf.id for lf in diagram.interaction.lifelines] if diagram.interaction else []
    if not lifelines or data.draw(st.integers(0, 5)) == 0:
        return data.draw(weighted((4, st.none()), (1, st.sampled_from([[], ["ghost"]]))))
    return data.draw(st.lists(st.sampled_from(lifelines), min_size=1, max_size=3))


def _place(data: st.DataObject, diagram: Diagram, args: dict[str, Any]) -> None:
    """Mostly a valid position (often inside an operand when there is one), sometimes an unknown one."""
    operands = _ids(diagram, (sequence.InteractionOperand,))
    siblings = _ids(diagram, (sequence.Message, sequence.CombinedFragment, sequence.InteractionUse))
    choice = data.draw(
        weighted((4, st.just("end")), (3, st.just("operand")), (2, st.just("before")), (1, st.just("junk")))
    )
    if choice == "operand" and operands:
        args["operand"] = data.draw(st.sampled_from(operands))
    elif choice == "before" and siblings:
        args["before"] = data.draw(st.sampled_from(siblings))
    elif choice == "junk":
        args[data.draw(st.sampled_from(["operand", "before"]))] = data.draw(texts)


def sequence_operation(data: st.DataObject, diagram: Diagram, *, batch: bool = True) -> tuple[str, dict[str, Any]]:
    """One `(op, args)` against a sequence diagram; a good share of them are invalid by construction."""
    weights = {"add_lifeline": 5, "update_lifeline": 2, "move_lifeline": 1, "remove_lifeline": 1, "add_message": 9}
    weights |= {"update_message": 3, "add_combined_fragment": 3, "update_combined_fragment": 1}
    weights |= {"add_interaction_operand": 2, "update_interaction_operand": 1, "add_interaction_use": 1}
    weights |= {"update_interaction_use": 1, "add_execution_specification": 2, "move_fragment": 3}
    weights |= {"add_destruction_occurrence_specification": 1, "remove_fragment": 2, "rename_diagram": 1, "foreign": 1}
    weights |= {"link_element": 2, "unlink_element": 1, "sync_element": 1, "clear_element": 1}
    weights |= {"link_relation": 2, "unlink_relation": 1, "sync_relation": 1, "clear_relation": 1}
    weights |= {"set_relation_hidden": 1, "set_completeness": 1}
    if batch:
        weights["apply_batch"] = 1
    op = data.draw(weighted(*((w, st.just(name)) for name, w in weights.items())))
    messages = _ids(diagram, (sequence.Message,))
    sorts = mostly(st.sampled_from([*sequence.MESSAGE_SORTS, "synch_call", "REPLY"]), ["call", ""])
    args: dict[str, Any]
    if op == "add_lifeline":
        args = {
            "name": data.draw(mostly(names, ["", " "])),
            "type": _maybe(data, texts),
            "actor": data.draw(st.booleans()),
        }
        if data.draw(st.integers(0, 3)) == 0:
            args["before"] = _lifeline_ref(data, diagram)
        if data.draw(st.integers(0, 3)) == 0:
            args["description"] = data.draw(notes)
    elif op == "update_lifeline":
        args = {"id": _lifeline_ref(data, diagram), "name": _maybe(data, names), "type": _maybe(data, texts)}
        if data.draw(st.integers(0, 2)) == 0:
            args["description"] = data.draw(notes)
    elif op in ("move_lifeline", "remove_lifeline"):
        args = {"id": _lifeline_ref(data, diagram)}
        if op == "move_lifeline" and data.draw(st.booleans()):
            args["before"] = _lifeline_ref(data, diagram)
    elif op == "add_message":
        args = {"name": data.draw(mostly(names, [""])), "sort": data.draw(sorts)}
        ends = data.draw(
            weighted((8, st.just("both")), (1, st.just("found")), (1, st.just("lost")), (1, st.just("none")))
        )
        if ends in ("both", "lost"):
            args["sender"] = _lifeline_ref(data, diagram)
        if ends in ("both", "found"):
            args["receiver"] = _lifeline_ref(data, diagram)
        if data.draw(st.integers(0, 4)) == 0:
            args["kind"] = data.draw(st.sampled_from([*sequence.MESSAGE_KINDS, "unknown"]))
        _place(data, diagram, args)
    elif op == "update_message":
        args = {"id": _ref(data, messages), "name": _maybe(data, names)}
        if data.draw(st.booleans()):
            args["sort"] = data.draw(sorts)
        if data.draw(st.integers(0, 3)) == 0:
            args["sender"] = "" if data.draw(st.booleans()) else _lifeline_ref(data, diagram)
    elif op == "add_combined_fragment":
        operators = [*sequence.INTERACTION_OPERATORS, *sorted(sequence.MULTI_OPERAND_OPERATORS) * 2]
        args = {"operator": data.draw(mostly(st.sampled_from(operators), ["maybe"]))}
        args["guard"] = _maybe(data, texts)
        args["covered"] = _covered(data, diagram)
        if data.draw(st.integers(0, 2)) == 0:
            siblings = _ids(diagram, (sequence.Message, sequence.CombinedFragment, sequence.InteractionUse))
            args["contents"] = (
                data.draw(st.lists(st.sampled_from(siblings), min_size=1, max_size=3)) if siblings else ["m99"]
            )
        elif data.draw(st.booleans()):
            _place(data, diagram, args)
        if data.draw(st.integers(0, 5)) == 0:
            args["messages"] = data.draw(st.lists(names, max_size=2))
    elif op == "update_combined_fragment":
        args = {"id": _ref(data, _ids(diagram, (sequence.CombinedFragment,)))}
        if data.draw(st.booleans()):
            args["operator"] = data.draw(st.sampled_from(sequence.INTERACTION_OPERATORS))
        if data.draw(st.booleans()):
            args["covered"] = _covered(data, diagram)
    elif op == "add_interaction_operand":
        args = {"fragment": _ref(data, _ids(diagram, (sequence.CombinedFragment,))), "guard": _maybe(data, texts)}
    elif op == "update_interaction_operand":
        args = {"id": _ref(data, _ids(diagram, (sequence.InteractionOperand,))), "guard": data.draw(texts)}
    elif op == "add_interaction_use":
        args = {"refers_to": data.draw(mostly(names, [""])), "covered": _covered(data, diagram)}
        _place(data, diagram, args)
    elif op == "update_interaction_use":
        args = {"id": _ref(data, _ids(diagram, (sequence.InteractionUse,))), "refers_to": _maybe(data, names)}
    elif op == "add_execution_specification":
        args = {"lifeline": _lifeline_ref(data, diagram), "start": _ref(data, messages)}
        if data.draw(st.booleans()):
            args["finish"] = _ref(data, messages)
    elif op == "add_destruction_occurrence_specification":
        args = {"lifeline": _lifeline_ref(data, diagram)}
        _place(data, diagram, args)
    elif op == "move_fragment":
        args = {"id": _ref(data, _ids(diagram, (sequence.Message, sequence.CombinedFragment, sequence.InteractionUse)))}
        _place(data, diagram, args)
    elif op == "remove_fragment":
        args = {"id": _ref(data, _ids(diagram))}
    elif op == "rename_diagram":
        args = {"title": data.draw(st.one_of(names, st.just("  ")))}
    elif op in ("link_element", "unlink_element", "sync_element", "clear_element"):
        args = model_link_operation(data, op, _lifeline_ref(data, diagram))
    elif op in RELATION_OPS:
        args = relation_link_operation(data, op, _ref(data, messages))
    elif op == "set_completeness":
        args = {"kinds": data.draw(COMPLETENESS)}
    elif op == "foreign":  # an operation of another diagram type
        op, args = "add_node", {"name": "x"}
    else:
        steps = [operation(data, diagram, batch=False) for _ in range(data.draw(st.integers(1, 4)))]
        args = {"operations": [{"op": o, "args": a} for o, a in steps]}
    return op, args


# ---------------------------------------------------------------------------
# Project operations (spec: docs/architecture/specs/projects.md)
# ---------------------------------------------------------------------------

DIAGRAM_IDS = ["overview", "orders", "payments", "web", "billing"]


def _section_ref(data: st.DataObject, project: Any) -> Any:
    ids = [s.id for s in project.sections]
    known = st.sampled_from(ids) if ids else st.just("nope")
    by_title = st.sampled_from([s.title.upper() for s in project.sections]) if ids else st.just("NOPE")
    return data.draw(weighted((6, known), (1, by_title), (1, st.sampled_from([None, "", "nope", "Nope"])), (1, texts)))


def _element_ref(data: st.DataObject, project: Any) -> Any:
    ids = [e.id for e in project.elements]
    known = st.sampled_from(ids) if ids else st.just("nope")
    by_name = st.sampled_from([e.name.upper() for e in project.elements]) if ids else st.just("NOPE")
    return data.draw(weighted((6, known), (1, by_name), (1, st.sampled_from([None, "", "nope"]))))


def _driver_ref(data: st.DataObject, project: Any) -> Any:
    ids = [d.id for d in project.drivers]
    known = st.sampled_from(ids) if ids else st.just("d99")
    by_title = st.sampled_from([d.title.upper() for d in project.drivers]) if ids else st.just("NOPE")
    return data.draw(weighted((6, known), (1, by_title), (1, st.sampled_from([None, "", "nope"]))))


def _driver_fields(data: st.DataObject, project: Any) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for key, value in (
        ("kind", st.sampled_from([*DRIVER_KINDS, "wish"])),
        ("importance", st.sampled_from([*DRIVER_LEVELS, "urgent"])),
        ("risk", st.sampled_from([*DRIVER_LEVELS, "none"])),
        ("description", texts),
    ):
        if data.draw(st.booleans()):
            args[key] = data.draw(value)
    if data.draw(st.booleans()):
        args["elements"] = [_element_ref(data, project) or "nope" for _ in range(data.draw(st.integers(0, 2)))]
    return args


def _position(data: st.DataObject) -> Any:
    return data.draw(weighted((3, st.none()), (4, st.integers(0, 4)), (1, st.just(-1))))


def project_operation(data: st.DataObject, project: Any) -> tuple[str, Any]:
    """One project operation (internal membership operations included) against the current project; ill-typed
    arguments now and then."""
    from arcviz import projects

    op = data.draw(st.sampled_from(sorted(projects.OPERATIONS)))
    members = [m.diagram for m in project.diagrams]
    member = st.sampled_from(members) if members else st.sampled_from(DIAGRAM_IDS)
    args: dict[str, Any]
    if op == "rename_project":
        args = {"title": data.draw(names)}
    elif op == "set_description":
        args = {"description": data.draw(texts)}
    elif op == "add_section":
        args = {"title": data.draw(names), "parent": _section_ref(data, project), "position": _position(data)}
    elif op == "rename_section":
        args = {"section": _section_ref(data, project), "title": data.draw(names)}
    elif op == "move_section":
        args = {
            "section": _section_ref(data, project),
            "parent": _section_ref(data, project),
            "position": _position(data),
        }
    elif op == "remove_section":
        args = {"section": _section_ref(data, project)}
    elif op == "add_element":
        args = {
            "name": data.draw(mostly(names, ["", "  "])),
            "kind": data.draw(mostly(st.sampled_from(ELEMENT_KINDS), ["robot"])),
        }
        if data.draw(st.booleans()):
            args["parent"] = _element_ref(data, project)
        if data.draw(st.integers(0, 3)) == 0:
            args["id"] = data.draw(weighted((3, st.sampled_from(ELEMENT_IDS)), (1, texts)))
        if data.draw(st.booleans()):
            args["description"] = data.draw(texts)
    elif op == "update_element":
        args = {"element": _element_ref(data, project)}
        for key, value in (
            ("name", mostly(names, ["", " "])),
            ("description", texts),
            ("kind", st.sampled_from([*ELEMENT_KINDS, "robot"])),
        ):
            if data.draw(st.booleans()):
                args[key] = data.draw(value)
    elif op == "move_element":
        args = {"element": _element_ref(data, project), "parent": _element_ref(data, project)}
    elif op == "remove_element":
        args = {"element": _element_ref(data, project)}
    elif op == "link_section":
        args = {"section": _section_ref(data, project), "element": _element_ref(data, project)}
    elif op == "add_driver":
        args = {"title": data.draw(mostly(names, ["", " "])), **_driver_fields(data, project)}
        if data.draw(st.booleans()):
            args["position"] = _position(data)
    elif op == "update_driver":
        args = {"driver": _driver_ref(data, project), **_driver_fields(data, project)}
        if data.draw(st.booleans()):
            args["title"] = data.draw(mostly(names, [""]))
        if data.draw(st.booleans()):
            args["status"] = data.draw(st.sampled_from(["active", "retired", "archived"]))  # ADR-0198
    elif op == "move_driver":
        args = {"driver": _driver_ref(data, project), "position": data.draw(st.integers(-1, 4))}
    elif op == "remove_driver":
        args = {"driver": _driver_ref(data, project)}
    elif op in ("move_diagram", "add_diagram"):
        diagram = data.draw(member if op == "move_diagram" else st.sampled_from(DIAGRAM_IDS))
        args = {"diagram": diagram, "section": _section_ref(data, project), "position": _position(data)}
    else:  # remove_diagram
        args = {"diagram": data.draw(member)}
    if data.draw(st.integers(0, 9)) == 0:
        args = ill_typed(data, args)
    return op, args
