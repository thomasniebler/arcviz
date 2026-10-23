"""Operations of sequence diagrams (UML 2.5.1 Interactions), registered in `operations.OPERATIONS`.

Spec: docs/architecture/specs/sequence-diagrams.md. Only `operations.apply` calls these (CLAUDE.md rule 2);
it rolls the diagram back when one raises `OperationError`, which the checks that depend on the final
order (execution specifications after a move) rely on. Every other check runs before the first change.
This module is imported at the end of `operations.py`, after the names it uses are defined.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .model import Diagram, single_line, slugify
from .operations import OperationError, _touch, clean_description
from .sequence import (
    INTERACTION_OPERATORS,
    MESSAGE_KINDS,
    MESSAGE_SET_OPERATORS,
    MESSAGE_SORTS,
    MULTI_OPERAND_OPERATORS,
    CombinedFragment,
    DestructionOccurrenceSpecification,
    ExecutionSpecification,
    FragmentNode,
    Interaction,
    InteractionOperand,
    InteractionUse,
    Lifeline,
    Message,
    canonical,
    element_ids,
    expected_kind,
    find,
    integrity_errors,
    involves,
    message_order,
    messages,
    walk,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _interaction(diagram: Diagram) -> Interaction:
    if diagram.interaction is None:  # only reachable if TYPE_OPERATIONS were bypassed
        raise OperationError(f"{diagram.type} diagrams have no interaction.")
    return diagram.interaction


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationError(f"{what} must be a non-empty string.")
    return value.strip()


def _optional_text(value: Any, what: str) -> str | None:
    """None stays None (unchanged); an empty string means "remove"."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise OperationError(f"{what} must be a string.")
    return value.strip()


def _enum(value: Any, allowed: tuple[str, ...], what: str) -> str:
    match = canonical(value, allowed) if isinstance(value, str) else None
    if match is None:
        raise OperationError(f"Unknown {what} '{value}'. Allowed: {', '.join(allowed)}")
    return match


def _lifeline(interaction: Interaction, ref: Any, what: str = "lifeline") -> Lifeline:
    if isinstance(ref, str) and ref.strip():
        wanted = ref.strip()
        for lifeline in interaction.lifelines:
            if lifeline.id == wanted:
                return lifeline
        lowered, slug = wanted.lower(), slugify(wanted)
        for lifeline in interaction.lifelines:
            if lifeline.name.lower() == lowered or lifeline.id.lower() == lowered or lifeline.id == slug:
                return lifeline
    known = ", ".join(lifeline.id for lifeline in interaction.lifelines) or "(none)"
    raise OperationError(f"Unknown {what} '{ref}'. Known lifelines: {known}")


def _covered(interaction: Interaction, covered: Any) -> list[str]:
    """Resolve a covered list; None means every lifeline (stored explicitly)."""
    if covered is None:
        ids = [lifeline.id for lifeline in interaction.lifelines]
        if not ids:
            raise OperationError("Add lifelines first: a fragment must cover at least one lifeline.")
        return ids
    if not isinstance(covered, list) or not covered:
        raise OperationError("covered must be a non-empty list of lifeline ids.")
    out: list[str] = []
    for ref in covered:
        lifeline = _lifeline(interaction, ref)
        if lifeline.id not in out:
            out.append(lifeline.id)
    return [lifeline.id for lifeline in interaction.lifelines if lifeline.id in out]  # left-to-right order


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise OperationError("messages must be a list of message names.")
    return [v.strip() for v in value]


def _new_id(interaction: Interaction, given: Any, prefix: str) -> str:
    taken = set(element_ids(interaction))
    if given is not None and isinstance(given, str) and given.strip():
        given = given.strip()
        if given in taken:
            raise OperationError(f"An element with id '{given}' already exists.")
        return given
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _operand(interaction: Interaction, ref: Any) -> InteractionOperand:
    element = find(interaction, ref) if isinstance(ref, str) else None
    if not isinstance(element, InteractionOperand):
        known = [
            op.id for f, _, _ in walk(interaction.fragments) if isinstance(f, CombinedFragment) for op in f.operands
        ]
        raise OperationError(f"Unknown interaction operand '{ref}'. Known operands: {', '.join(known) or '(none)'}")
    return element


def _location(interaction: Interaction, fragment_id: str) -> tuple[list[FragmentNode], int, InteractionOperand | None]:
    for fragment, container, owner in walk(interaction.fragments):
        if fragment.id == fragment_id:
            return container, container.index(fragment), owner
    raise OperationError(f"Unknown fragment '{fragment_id}'.")


def _position(interaction: Interaction, operand: Any, before: Any) -> tuple[list[FragmentNode], int]:
    """The container list and index where a new fragment goes."""
    target = _operand(interaction, operand) if operand not in (None, "") else None
    container: list[FragmentNode] = target.fragments if target is not None else interaction.fragments
    if before in (None, ""):
        return container, len(container)
    before_container, index, owner = _location(interaction, str(before))
    if target is not None and owner is not target:
        raise OperationError(f"Fragment '{before}' is not in operand '{target.id}'.")
    if target is None and operand in (None, "") and before_container is not container:
        return before_container, index  # `before` alone decides the container
    return container, index


_ORDER_ERRORS = ("after its destruction", "is destroyed twice")


def _check_order(interaction: Interaction) -> None:
    """Raise if the time order no longer fits: an execution specification finishing before it starts, or a
    lifeline used after (or destroyed again after) its destruction. Runs after the change; `apply` rolls back."""
    for error in integrity_errors(interaction):
        if error.startswith("execution ") or any(marker in error for marker in _ORDER_ERRORS):
            raise OperationError(f"Not applied: {error}.")


def _result(element: Any, touched: list[str], **extra: Any) -> dict[str, Any]:
    return {"element": element.model_dump(mode="json"), "touched": touched, **extra}


# ---------------------------------------------------------------------------
# Lifelines
# ---------------------------------------------------------------------------


def add_lifeline(
    diagram: Diagram,
    name: str,
    type: str | None = None,
    actor: bool = False,
    id: str | None = None,
    before: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    name = _text(name, "A lifeline's name")
    note = clean_description(description) or None
    type_name = _optional_text(type, "type") or None
    if not isinstance(actor, bool):
        raise OperationError("actor must be true or false.")
    index = len(interaction.lifelines)
    if before not in (None, ""):
        index = interaction.lifelines.index(_lifeline(interaction, before))
    if id is not None and isinstance(id, str) and id.strip():
        lifeline_id = _new_id(interaction, id, "")
    else:
        base, taken, n = slugify(name), set(element_ids(interaction)), 2
        lifeline_id = base
        while lifeline_id in taken:
            lifeline_id, n = f"{base}-{n}", n + 1
    lifeline = Lifeline(id=lifeline_id, name=name, type=type_name, actor=actor, description=note)
    interaction.lifelines.insert(index, lifeline)
    _touch(diagram)
    return _result(lifeline, [lifeline.id])


def update_lifeline(
    diagram: Diagram,
    id: str,
    name: str | None = None,
    type: str | None = None,
    actor: bool | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    lifeline = _lifeline(interaction, id)
    note = clean_description(description)
    new_name = _text(name, "A lifeline's name") if name is not None else None
    new_type = _optional_text(type, "type")
    if actor is not None and not isinstance(actor, bool):
        raise OperationError("actor must be true or false.")
    if new_name is not None:
        if lifeline.ref is not None and new_name != single_line(lifeline.name).strip():  # stored names may predate #103
            raise OperationError(
                f"'{lifeline.id}' represents the project element '{lifeline.ref}', so its name comes from the project "
                f"model. Rename the element (update_element) and every diagram follows, or unlink '{lifeline.id}' first."
            )
        lifeline.name = new_name
    if new_type is not None:
        lifeline.type = new_type or None
    if actor is not None:
        lifeline.actor = actor
    if note is not None:
        lifeline.description = note or None
    _touch(diagram)
    return _result(lifeline, [lifeline.id])


def move_lifeline(diagram: Diagram, id: str, before: str | None = None) -> dict[str, Any]:
    interaction = _interaction(diagram)
    lifeline = _lifeline(interaction, id)
    anchor = _lifeline(interaction, before) if before not in (None, "") else None
    if anchor is lifeline:
        raise OperationError("A lifeline cannot be moved before itself.")
    interaction.lifelines.remove(lifeline)
    index = interaction.lifelines.index(anchor) if anchor is not None else len(interaction.lifelines)
    interaction.lifelines.insert(index, lifeline)
    for fragment, _, _ in walk(interaction.fragments):  # keep covered lists in left-to-right order
        if isinstance(fragment, CombinedFragment | InteractionUse):
            fragment.covered = [lf.id for lf in interaction.lifelines if lf.id in fragment.covered]
    _touch(diagram)
    return _result(lifeline, [lifeline.id])


def remove_lifeline(diagram: Diagram, id: str) -> dict[str, Any]:
    """Remove a lifeline with its messages, executions and destruction; drop it from covered lists."""
    interaction = _interaction(diagram)
    lifeline = _lifeline(interaction, id)
    removed = [lifeline.id]

    def prune(fragments: list[FragmentNode]) -> list[FragmentNode]:
        kept: list[FragmentNode] = []
        for fragment in fragments:
            if isinstance(fragment, Message) and involves(fragment, lifeline.id):
                removed.append(fragment.id)
                continue
            if isinstance(fragment, DestructionOccurrenceSpecification) and fragment.lifeline == lifeline.id:
                removed.append(fragment.id)
                continue
            if isinstance(fragment, CombinedFragment):
                for op in fragment.operands:
                    op.fragments = prune(op.fragments)
            if isinstance(fragment, CombinedFragment | InteractionUse) and lifeline.id in fragment.covered:
                fragment.covered = [c for c in fragment.covered if c != lifeline.id]
                if not fragment.covered:
                    fragment.covered = _used_lifelines(interaction, fragment, exclude=lifeline.id)
                if not fragment.covered:
                    removed.extend(f.id for f, _, _ in walk([fragment]))
                    removed.extend(
                        op.id for f, _, _ in walk([fragment]) if isinstance(f, CombinedFragment) for op in f.operands
                    )
                    continue
            kept.append(fragment)
        return kept

    interaction.fragments = prune(interaction.fragments)
    gone = set(removed)
    for execution in list(interaction.executions):
        if execution.lifeline == lifeline.id or execution.start in gone or execution.finish in gone:
            interaction.executions.remove(execution)
            removed.append(execution.id)
    interaction.lifelines.remove(lifeline)
    _touch(diagram)
    return {"removed": removed, "touched": []}


def _used_lifelines(interaction: Interaction, fragment: CombinedFragment | InteractionUse, exclude: str) -> list[str]:
    used: set[str] = set()
    if isinstance(fragment, CombinedFragment):
        for op in fragment.operands:
            for inner, _, _ in walk(op.fragments):
                if isinstance(inner, Message):
                    used.update(end for end in (inner.sender, inner.receiver) if end)
                elif isinstance(inner, CombinedFragment | InteractionUse):
                    used.update(inner.covered)
    used.discard(exclude)
    return [lf.id for lf in interaction.lifelines if lf.id in used]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def _message_ends(
    interaction: Interaction, sender: Any, receiver: Any, kind: Any
) -> tuple[str | None, str | None, str]:
    src = _lifeline(interaction, sender, "sender").id if sender not in (None, "") else None
    dst = _lifeline(interaction, receiver, "receiver").id if receiver not in (None, "") else None
    if src is None and dst is None:
        raise OperationError("A message needs a sender or a receiver (or both).")
    derived = expected_kind(src, dst)
    if kind is not None:
        kind = _enum(kind, MESSAGE_KINDS, "message kind")
        if kind != derived:
            raise OperationError(
                f"A {kind} message must have "
                + {
                    "complete": "a sender and a receiver",
                    "found": "a receiver and no sender",
                    "lost": "a sender and no receiver",
                }[kind]
                + f"; these ends make it {derived}."
            )
    return src, dst, derived


def add_message(
    diagram: Diagram,
    name: str,
    sender: str | None = None,
    receiver: str | None = None,
    sort: str = "synchCall",
    kind: str | None = None,
    operand: str | None = None,
    before: str | None = None,
    id: str | None = None,
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    name = _text(name, "A message's name")
    sort = _enum(sort, MESSAGE_SORTS, "message sort")
    src, dst, kind = _message_ends(interaction, sender, receiver, kind)
    container, index = _position(interaction, operand, before)
    message = Message(id=_new_id(interaction, id, "m"), name=name, sort=sort, kind=kind, sender=src, receiver=dst)
    container.insert(index, message)
    _check_order(interaction)
    _touch(diagram)
    return _result(message, [message.id])


def update_message(
    diagram: Diagram,
    id: str,
    name: str | None = None,
    sort: str | None = None,
    kind: str | None = None,
    sender: str | None = None,
    receiver: str | None = None,
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    message = find(interaction, id) if isinstance(id, str) else None
    if not isinstance(message, Message):
        known = ", ".join(m.id for m in messages(interaction)) or "(none)"
        raise OperationError(f"Unknown message '{id}'. Known messages: {known}")
    new_name = _text(name, "A message's name") if name is not None else None
    new_sort = _enum(sort, MESSAGE_SORTS, "message sort") if sort is not None else None
    src = message.sender if sender is None else sender
    dst = message.receiver if receiver is None else receiver
    src, dst, new_kind = _message_ends(interaction, src, dst, kind)
    for execution in interaction.executions:
        if message.id in (execution.start, execution.finish) and execution.lifeline not in (src, dst):
            raise OperationError(
                f"Execution specification '{execution.id}' on '{execution.lifeline}' starts or finishes at "
                f"'{message.id}'; remove it first or keep '{execution.lifeline}' as an end of the message."
            )
    if new_name is not None:
        message.name = new_name
    if new_sort is not None:
        message.sort = new_sort
    unlinked = None
    if message.ref is not None and (src, dst) != (message.sender, message.receiver):
        unlinked, message.ref = message.ref, None  # its ends no longer stand for the relation's (ADR-0139)
    message.sender, message.receiver, message.kind = src, dst, new_kind
    _check_order(interaction)
    _touch(diagram)
    return _result(message, [message.id], **({"unlinked_relation": unlinked} if unlinked else {}))


# ---------------------------------------------------------------------------
# Combined fragments and operands
# ---------------------------------------------------------------------------


def add_combined_fragment(
    diagram: Diagram,
    operator: str,
    covered: list[str] | None = None,
    guard: str | None = None,
    contents: list[str] | None = None,
    messages: list[str] | None = None,
    operand: str | None = None,
    before: str | None = None,
    id: str | None = None,
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    operator = _enum(operator, INTERACTION_OPERATORS, "interaction operator")
    names = _names(messages)
    if names and operator not in MESSAGE_SET_OPERATORS:
        raise OperationError("Only ignore and consider list messages.")
    guard_text = _optional_text(guard, "guard") or None
    moved: list[FragmentNode] = []
    if contents:
        if not isinstance(contents, list):
            raise OperationError("contents must be a list of fragment ids.")
        locations = [_location(interaction, str(c)) for c in contents]
        container = locations[0][0]
        if any(loc[0] is not container for loc in locations):
            raise OperationError("All contents must be siblings (in the same operand or all at the top level).")
        if operand not in (None, "") or before not in (None, ""):
            raise OperationError(
                "With contents the fragment takes the place of the first of them; omit operand and before."
            )
        indexes = sorted({loc[1] for loc in locations})
        moved = [container[i] for i in indexes]
        index = indexes[0]
    else:
        container, index = _position(interaction, operand, before)
    covered_ids = _covered(interaction, covered)
    fragment_id = _new_id(interaction, id, "cf")
    taken = set(element_ids(interaction)) | {fragment_id}
    n = 1
    while f"op{n}" in taken:
        n += 1
    first = InteractionOperand(id=f"op{n}", guard=guard_text, fragments=moved)
    fragment = CombinedFragment(
        id=fragment_id, operator=operator, covered=covered_ids, operands=[first], messages=names
    )
    for item in moved:
        container.remove(item)
    container.insert(index, fragment)
    _check_order(interaction)  # wrapping moves the contents to the place of the first of them
    _touch(diagram)
    return _result(fragment, [fragment.id, first.id], operand=first.id)


def _combined(interaction: Interaction, ref: Any) -> CombinedFragment:
    element = find(interaction, ref) if isinstance(ref, str) else None
    if not isinstance(element, CombinedFragment):
        known = [f.id for f, _, _ in walk(interaction.fragments) if isinstance(f, CombinedFragment)]
        raise OperationError(f"Unknown combined fragment '{ref}'. Known: {', '.join(known) or '(none)'}")
    return element


def update_combined_fragment(
    diagram: Diagram,
    id: str,
    operator: str | None = None,
    covered: list[str] | None = None,
    messages: list[str] | None = None,
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    fragment = _combined(interaction, id)
    new_operator = (
        _enum(operator, INTERACTION_OPERATORS, "interaction operator") if operator is not None else fragment.operator
    )
    if len(fragment.operands) > 1 and new_operator not in MULTI_OPERAND_OPERATORS:
        raise OperationError(
            f"{new_operator} has exactly one operand, but '{fragment.id}' has {len(fragment.operands)}; "
            "remove operands first."
        )
    names = _names(messages) if messages is not None else fragment.messages
    if names and new_operator not in MESSAGE_SET_OPERATORS:
        raise OperationError("Only ignore and consider list messages; pass messages=[] to clear them.")
    covered_ids = _covered(interaction, covered) if covered is not None else fragment.covered
    fragment.operator, fragment.messages, fragment.covered = new_operator, names, covered_ids
    _touch(diagram)
    return _result(fragment, [fragment.id])


def add_interaction_operand(
    diagram: Diagram, fragment: str, guard: str | None = None, before: str | None = None
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    combined = _combined(interaction, fragment)
    if combined.operator not in MULTI_OPERAND_OPERATORS:
        raise OperationError(
            f"{combined.operator} has exactly one operand; only {', '.join(sorted(MULTI_OPERAND_OPERATORS))} have several."
        )
    index = len(combined.operands)
    if before not in (None, ""):
        anchor = _operand(interaction, before)
        if anchor not in combined.operands:
            raise OperationError(f"Operand '{before}' is not part of '{combined.id}'.")
        index = combined.operands.index(anchor)
    op = InteractionOperand(id=_new_id(interaction, None, "op"), guard=_optional_text(guard, "guard") or None)
    combined.operands.insert(index, op)
    _touch(diagram)
    return _result(op, [combined.id, op.id])


def update_interaction_operand(diagram: Diagram, id: str, guard: str | None = None) -> dict[str, Any]:
    interaction = _interaction(diagram)
    op = _operand(interaction, id)
    new_guard = _optional_text(guard, "guard")
    if new_guard is not None:
        op.guard = new_guard or None
    _touch(diagram)
    return _result(op, [op.id])


# ---------------------------------------------------------------------------
# Interaction uses, executions, destructions
# ---------------------------------------------------------------------------


def add_interaction_use(
    diagram: Diagram,
    refers_to: str,
    covered: list[str] | None = None,
    operand: str | None = None,
    before: str | None = None,
    id: str | None = None,
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    name = _text(refers_to, "refers_to (the name of the referenced interaction)")
    covered_ids = _covered(interaction, covered)
    container, index = _position(interaction, operand, before)
    use = InteractionUse(id=_new_id(interaction, id, "ref"), refers_to=name, covered=covered_ids)
    container.insert(index, use)
    _touch(diagram)
    return _result(use, [use.id])


def update_interaction_use(
    diagram: Diagram, id: str, refers_to: str | None = None, covered: list[str] | None = None
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    use = find(interaction, id) if isinstance(id, str) else None
    if not isinstance(use, InteractionUse):
        raise OperationError(f"Unknown interaction use '{id}'.")
    name = _text(refers_to, "refers_to") if refers_to is not None else use.refers_to
    covered_ids = _covered(interaction, covered) if covered is not None else use.covered
    use.refers_to, use.covered = name, covered_ids
    _touch(diagram)
    return _result(use, [use.id])


def add_execution_specification(
    diagram: Diagram, lifeline: str, start: str, finish: str | None = None, id: str | None = None
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    owner = _lifeline(interaction, lifeline)
    by_id = {m.id: m for m in messages(interaction)}
    order = message_order(interaction)
    ends: list[str] = []
    for what, ref in (("start", start), ("finish", finish)):
        if ref in (None, "") and what == "finish":
            continue
        message = by_id.get(ref) if isinstance(ref, str) else None
        if message is None:
            raise OperationError(f"Unknown {what} message '{ref}'. Known messages: {', '.join(by_id) or '(none)'}")
        if not involves(message, owner.id):
            raise OperationError(f"Message '{message.id}' does not involve lifeline '{owner.id}'.")
        ends.append(message.id)
    if len(ends) == 2 and order[ends[1]] < order[ends[0]]:
        raise OperationError(f"The execution would finish ({ends[1]}) before it starts ({ends[0]}).")
    execution = ExecutionSpecification(
        id=_new_id(interaction, id, "ex"), lifeline=owner.id, start=ends[0], finish=ends[1] if len(ends) == 2 else None
    )
    interaction.executions.append(execution)
    _touch(diagram)
    return _result(execution, [execution.id])


def add_destruction_occurrence_specification(
    diagram: Diagram, lifeline: str, operand: str | None = None, before: str | None = None, id: str | None = None
) -> dict[str, Any]:
    interaction = _interaction(diagram)
    owner = _lifeline(interaction, lifeline)
    for fragment, _, _ in walk(interaction.fragments):
        if isinstance(fragment, DestructionOccurrenceSpecification) and fragment.lifeline == owner.id:
            raise OperationError(f"Lifeline '{owner.id}' is already destroyed by '{fragment.id}'.")
    container, index = _position(interaction, operand, before)
    destruction = DestructionOccurrenceSpecification(id=_new_id(interaction, id, "x"), lifeline=owner.id)
    container.insert(index, destruction)
    _check_order(interaction)
    _touch(diagram)
    return _result(destruction, [destruction.id, owner.id])


# ---------------------------------------------------------------------------
# Moving and removing
# ---------------------------------------------------------------------------


def move_fragment(diagram: Diagram, id: str, operand: str | None = None, before: str | None = None) -> dict[str, Any]:
    interaction = _interaction(diagram)
    container, index, _ = _location(interaction, str(id))
    fragment = container[index]
    if isinstance(fragment, CombinedFragment) and operand not in (None, ""):
        inner = {op.id for f, _, _ in walk([fragment]) if isinstance(f, CombinedFragment) for op in f.operands}
        if operand in inner:
            raise OperationError(f"Cannot move '{fragment.id}' inside itself.")
    if before == fragment.id:
        raise OperationError("A fragment cannot be moved before itself.")
    container.pop(index)
    try:
        target, position = _position(interaction, operand, before)
    except OperationError:
        container.insert(index, fragment)
        raise
    target.insert(position, fragment)
    _check_order(interaction)  # the order changed; apply() rolls back if this raises
    _touch(diagram)
    return _result(fragment, [fragment.id])


def remove_fragment(diagram: Diagram, id: str) -> dict[str, Any]:
    """Remove a fragment (with everything inside it), an operand (not the last) or an execution."""
    interaction = _interaction(diagram)
    element = find(interaction, id) if isinstance(id, str) else None
    removed: list[str] = []
    if isinstance(element, ExecutionSpecification):
        interaction.executions.remove(element)
        removed.append(element.id)
    elif isinstance(element, InteractionOperand):
        owner = next(
            f for f, _, _ in walk(interaction.fragments) if isinstance(f, CombinedFragment) and element in f.operands
        )
        if len(owner.operands) == 1:
            raise OperationError(
                f"'{element.id}' is the last operand of '{owner.id}'; remove the combined fragment instead."
            )
        owner.operands.remove(element)
        removed += [element.id, *(f.id for f, _, _ in walk(element.fragments))]
        removed += [
            op.id for f, _, _ in walk(element.fragments) if isinstance(f, CombinedFragment) for op in f.operands
        ]
    elif element is not None and not isinstance(element, Lifeline):
        container, index, _ = _location(interaction, element.id)
        fragment = container.pop(index)
        removed += [f.id for f, _, _ in walk([fragment])]
        removed += [op.id for f, _, _ in walk([fragment]) if isinstance(f, CombinedFragment) for op in f.operands]
    else:
        hint = " (use remove_lifeline for lifelines)" if element is not None else ""
        raise OperationError(
            f"Unknown fragment '{id}'{hint}. Known ids: {', '.join(element_ids(interaction)) or '(none)'}"
        )
    gone = set(removed)
    for execution in list(interaction.executions):
        if execution.id not in gone and (execution.start in gone or execution.finish in gone):
            interaction.executions.remove(execution)
            removed.append(execution.id)
    _touch(diagram)
    return {"removed": removed, "touched": []}


OPERATIONS: dict[str, Callable[..., Any]] = {
    "add_lifeline": add_lifeline,
    "update_lifeline": update_lifeline,
    "move_lifeline": move_lifeline,
    "remove_lifeline": remove_lifeline,
    "add_message": add_message,
    "update_message": update_message,
    "add_combined_fragment": add_combined_fragment,
    "update_combined_fragment": update_combined_fragment,
    "add_interaction_operand": add_interaction_operand,
    "update_interaction_operand": update_interaction_operand,
    "add_interaction_use": add_interaction_use,
    "update_interaction_use": update_interaction_use,
    "add_execution_specification": add_execution_specification,
    "add_destruction_occurrence_specification": add_destruction_occurrence_specification,
    "move_fragment": move_fragment,
    "remove_fragment": remove_fragment,
}
