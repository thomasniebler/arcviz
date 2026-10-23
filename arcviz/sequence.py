"""Content model of sequence diagrams: a UML 2.5.1 Interaction (chapter 17) and pure queries over it.

Spec: docs/architecture/specs/sequence-diagrams.md, ADR-0035. Lifelines are ordered left to right,
fragments form an ordered tree top to bottom (combined fragments hold operands, operands hold fragments),
execution specifications refer to messages. Mutations live in `sequence_ops.py` (called only through
`operations.apply`); this module imports nothing from the package.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

# Enumerations, spelled as the UML literals.
MESSAGE_SORTS = ("synchCall", "asynchCall", "asynchSignal", "reply", "createMessage", "deleteMessage")
MESSAGE_KINDS = ("complete", "found", "lost")
INTERACTION_OPERATORS = (
    "alt",
    "opt",
    "par",
    "loop",
    "critical",
    "neg",
    "assert",
    "strict",
    "seq",
    "ignore",
    "consider",
    "break",
)
MULTI_OPERAND_OPERATORS = frozenset({"alt", "par", "strict", "seq"})  # all others have exactly one operand
MESSAGE_SET_OPERATORS = frozenset({"ignore", "consider"})  # carry the names of the messages they list


def canonical(value: str, allowed: tuple[str, ...]) -> str | None:
    """`value` matched case-insensitively and with or without underscores against `allowed`, or None."""
    wanted = value.replace("_", "").replace("-", "").replace(" ", "").lower()
    return next((a for a in allowed if a.lower() == wanted), None)


# Notes on nodes and lifelines (spec: docs/architecture/specs/collaboration.md, ADR-0092/0093): the longest note,
# and how much of one the assistant's summary shows (full, shortened over budget).
MAX_DESCRIPTION = 2000
NOTE_CHARS = 200
SHORT_NOTE_CHARS = 40


def note_suffix(description: str | None, limit: int = NOTE_CHARS) -> str:
    """A note for one summary line: on one line, cut at `limit` characters; `limit` 0 gives only a hint."""
    if not description:
        return ""
    if limit <= 0:
        return " — note (call get_diagram)"
    text = " ".join(description.split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return f' — note: "{text}"'


class Lifeline(BaseModel):
    id: str
    name: str
    type: str | None = None  # the classifier of the represented participant, shown as "name : type"
    actor: bool = False  # drawn as a stick figure (the represented participant is an Actor)
    ref: str | None = None  # the project model element it represents (spec: shared-project-model.md)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)  # a note (spec: collaboration.md)

    @model_serializer(mode="wrap")
    def _omit_unset_ref(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in ("ref", "description"):
            if data.get(key, "") is None:
                del data[key]  # documents written before M18 / M10 dump, fingerprint and export unchanged
        return data


class Message(BaseModel):
    element: Literal["message"] = "message"
    id: str
    name: str
    sort: str = "synchCall"
    kind: str = "complete"
    sender: str | None = None  # lifeline id; None for a found message
    receiver: str | None = None  # lifeline id; None for a lost message
    ref: str | None = None  # the model relation it shows (spec: model-relations.md)

    @model_serializer(mode="wrap")
    def _omit_unset_ref(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if data.get("ref", "") is None:
            del data["ref"]  # documents written before M25 dump, fingerprint and export unchanged
        return data


class InteractionOperand(BaseModel):
    id: str
    guard: str | None = None
    fragments: list[Fragment] = Field(default_factory=list)


class CombinedFragment(BaseModel):
    element: Literal["combined_fragment"] = "combined_fragment"
    id: str
    operator: str
    covered: list[str] = Field(default_factory=list)
    operands: list[InteractionOperand] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)  # ignore / consider only


class InteractionUse(BaseModel):
    element: Literal["interaction_use"] = "interaction_use"
    id: str
    refers_to: str
    covered: list[str] = Field(default_factory=list)


class DestructionOccurrenceSpecification(BaseModel):
    element: Literal["destruction_occurrence_specification"] = "destruction_occurrence_specification"
    id: str
    lifeline: str


Fragment = Annotated[
    Message | CombinedFragment | InteractionUse | DestructionOccurrenceSpecification,
    Field(discriminator="element"),
]


class ExecutionSpecification(BaseModel):
    id: str
    lifeline: str
    start: str  # message id
    finish: str | None = None  # message id; None = until the lifeline's last message


class Interaction(BaseModel):
    lifelines: list[Lifeline] = Field(default_factory=list)
    fragments: list[Fragment] = Field(default_factory=list)
    executions: list[ExecutionSpecification] = Field(default_factory=list)


InteractionOperand.model_rebuild()
CombinedFragment.model_rebuild()

FragmentNode = Message | CombinedFragment | InteractionUse | DestructionOccurrenceSpecification
Element = Lifeline | FragmentNode | InteractionOperand | ExecutionSpecification


# ---- queries ---------------------------------------------------------------------------------------


def walk(fragments: list[FragmentNode], operand: InteractionOperand | None = None) -> Iterator[tuple]:
    """Depth-first, in time order: `(fragment, container list, owning operand or None)`."""
    for fragment in fragments:
        yield fragment, fragments, operand
        if isinstance(fragment, CombinedFragment):
            for op in fragment.operands:
                yield from walk(op.fragments, op)


def messages(interaction: Interaction) -> list[Message]:
    """All messages in time order."""
    return [f for f, _, _ in walk(interaction.fragments) if isinstance(f, Message)]


def operands(interaction: Interaction) -> list[InteractionOperand]:
    out: list[InteractionOperand] = []
    for fragment, _, _ in walk(interaction.fragments):
        if isinstance(fragment, CombinedFragment):
            out.extend(fragment.operands)
    return out


def element_ids(interaction: Interaction) -> list[str]:
    """Every id in the interaction (duplicates included, so callers can detect them)."""
    ids = [lifeline.id for lifeline in interaction.lifelines]
    ids += [f.id for f, _, _ in walk(interaction.fragments)]
    ids += [op.id for op in operands(interaction)]
    ids += [e.id for e in interaction.executions]
    return ids


def find(interaction: Interaction, element_id: str) -> Element | None:
    for lifeline in interaction.lifelines:
        if lifeline.id == element_id:
            return lifeline
    for fragment, _, _ in walk(interaction.fragments):
        if fragment.id == element_id:
            return fragment
    for op in operands(interaction):
        if op.id == element_id:
            return op
    return next((e for e in interaction.executions if e.id == element_id), None)


def element_kind(element: Element) -> str:
    """The UML-term kind of an element as used in `data-element` and the inspector."""
    if isinstance(element, Lifeline):
        return "lifeline"
    if isinstance(element, InteractionOperand):
        return "interaction_operand"
    if isinstance(element, ExecutionSpecification):
        return "execution_specification"
    return element.element


def message_order(interaction: Interaction) -> dict[str, int]:
    return {m.id: i for i, m in enumerate(messages(interaction))}


def involves(message: Message, lifeline_id: str) -> bool:
    return lifeline_id in (message.sender, message.receiver)


def expected_kind(sender: str | None, receiver: str | None) -> str:
    return "complete" if sender and receiver else ("found" if receiver else "lost")


def describe(interaction: Interaction, element_id: str) -> str:
    """One line describing an element (proposal diffs, summaries)."""
    element = find(interaction, element_id)
    if element is None:
        return element_id
    if isinstance(element, Lifeline):
        return f"Lifeline {element.name}" + (f" : {element.type}" if element.type else "")
    if isinstance(element, Message):
        return f"{element.sort} {element.sender or '●'} → {element.receiver or '●'}: {element.name}"
    if isinstance(element, CombinedFragment):
        return f"{element.operator} ({', '.join(element.covered)})"
    if isinstance(element, InteractionOperand):
        return f"operand [{element.guard or ''}]"
    if isinstance(element, InteractionUse):
        return f"ref {element.refers_to}"
    if isinstance(element, DestructionOccurrenceSpecification):
        return f"destruction of {element.lifeline}"
    return f"execution on {element.lifeline} {element.start} → {element.finish or 'end'}"


def integrity_errors(interaction: Interaction) -> list[str]:
    """Referential and structural rules every stored interaction satisfies (spec: sequence-diagrams.md)."""
    errors: list[str] = []
    seen: set[str] = set()
    for element_id in element_ids(interaction):
        if not element_id.strip():
            errors.append("an element of the interaction has an empty id")
        elif element_id in seen:
            errors.append(f"duplicate element id '{element_id}'")
        seen.add(element_id)
    lifelines = {lifeline.id for lifeline in interaction.lifelines}
    for lifeline in interaction.lifelines:
        if not lifeline.name.strip():
            errors.append(f"lifeline '{lifeline.id}' has an empty name")
    destroyed: set[str] = set()  # lifelines destroyed so far, in time order (as drawn)
    for fragment, _, _ in walk(interaction.fragments):
        if isinstance(fragment, Message):
            if fragment.sort not in MESSAGE_SORTS:
                errors.append(f"message '{fragment.id}' has unknown sort '{fragment.sort}'")
            if fragment.kind not in MESSAGE_KINDS:
                errors.append(f"message '{fragment.id}' has unknown kind '{fragment.kind}'")
            for end in (fragment.sender, fragment.receiver):
                if end is not None and end not in lifelines:
                    errors.append(f"message '{fragment.id}' refers to unknown lifeline '{end}'")
                if end is not None and end in destroyed:
                    errors.append(f"message '{fragment.id}' involves lifeline '{end}' after its destruction")
            if fragment.sort == "deleteMessage" and fragment.receiver is not None:
                destroyed.add(fragment.receiver)  # its receive event is the receiver's destruction
            if fragment.sender is None and fragment.receiver is None:
                errors.append(f"message '{fragment.id}' has neither sender nor receiver")
            elif fragment.kind in MESSAGE_KINDS and fragment.kind != expected_kind(fragment.sender, fragment.receiver):
                errors.append(
                    f"message '{fragment.id}' is {fragment.kind} but its ends make it {expected_kind(fragment.sender, fragment.receiver)}"
                )
        elif isinstance(fragment, CombinedFragment):
            if fragment.operator not in INTERACTION_OPERATORS:
                errors.append(f"combined fragment '{fragment.id}' has unknown operator '{fragment.operator}'")
            if not fragment.operands:
                errors.append(f"combined fragment '{fragment.id}' has no operand")
            elif len(fragment.operands) > 1 and fragment.operator not in MULTI_OPERAND_OPERATORS:
                errors.append(f"combined fragment '{fragment.id}' ({fragment.operator}) must have exactly one operand")
            if fragment.messages and fragment.operator not in MESSAGE_SET_OPERATORS:
                errors.append(f"combined fragment '{fragment.id}': only ignore and consider list messages")
            errors += _covered_errors(fragment.id, fragment.covered, lifelines)
        elif isinstance(fragment, InteractionUse):
            if not fragment.refers_to.strip():
                errors.append(f"interaction use '{fragment.id}' refers to nothing")
            errors += _covered_errors(fragment.id, fragment.covered, lifelines)
        else:
            if fragment.lifeline not in lifelines:
                errors.append(f"destruction '{fragment.id}' refers to unknown lifeline '{fragment.lifeline}'")
            if fragment.lifeline in destroyed:
                errors.append(f"lifeline '{fragment.lifeline}' is destroyed twice")
            destroyed.add(fragment.lifeline)
    order = message_order(interaction)
    by_id = {m.id: m for m in messages(interaction)}
    for execution in interaction.executions:
        if execution.lifeline not in lifelines:
            errors.append(f"execution '{execution.id}' refers to unknown lifeline '{execution.lifeline}'")
        for end in (execution.start, execution.finish):
            if end is None:
                continue
            message = by_id.get(end)
            if message is None:
                errors.append(f"execution '{execution.id}' refers to unknown message '{end}'")
            elif not involves(message, execution.lifeline):
                errors.append(f"execution '{execution.id}': message '{end}' does not involve '{execution.lifeline}'")
        if execution.finish in order and execution.start in order and order[execution.finish] < order[execution.start]:
            errors.append(
                f"execution '{execution.id}' finishes ({execution.finish}) before it starts ({execution.start})"
            )
    return errors


def _covered_errors(element_id: str, covered: list[str], lifelines: set[str]) -> list[str]:
    if not covered:
        return [f"'{element_id}' covers no lifeline"]
    return [f"'{element_id}' covers unknown lifeline '{c}'" for c in covered if c not in lifelines]


def summary_lines(interaction: Interaction, note_chars: int = NOTE_CHARS) -> list[str]:
    """Text for the system prompt: lifelines left to right, fragments top to bottom with ids; lifeline notes are
    cut at `note_chars` (0: only a hint, see `note_suffix`)."""
    lines = ["Lifelines (left to right):"]
    if not interaction.lifelines:
        lines.append("  (no lifelines yet)")
    for lifeline in interaction.lifelines:
        typed = f" : {lifeline.type}" if lifeline.type else ""
        actor = " «actor»" if lifeline.actor else ""
        linked = f" (= element {lifeline.ref})" if lifeline.ref else ""
        note = note_suffix(lifeline.description, note_chars)
        lines.append(f"  - [{lifeline.id}] {lifeline.name}{typed}{actor}{linked}{note}")
    lines.append("Fragments (top to bottom):")
    if not interaction.fragments:
        lines.append("  (no messages yet)")

    def emit(fragments: list[FragmentNode], depth: int) -> None:
        pad = "  " * (depth + 1)
        for fragment in fragments:
            if isinstance(fragment, Message):
                lines.append(
                    f"{pad}- [{fragment.id}] {fragment.sender or '(found)'} -> {fragment.receiver or '(lost)'}: "
                    f"{fragment.name} ({fragment.sort}{', ' + fragment.kind if fragment.kind != 'complete' else ''})"
                )
            elif isinstance(fragment, CombinedFragment):
                listed = f" {{{', '.join(fragment.messages)}}}" if fragment.messages else ""
                lines.append(
                    f"{pad}- [{fragment.id}] {fragment.operator}{listed} covering {', '.join(fragment.covered)}"
                )
                for op in fragment.operands:
                    lines.append(f"{pad}  operand [{op.id}]" + (f" [{op.guard}]" if op.guard else "") + ":")
                    emit(op.fragments, depth + 2)
            elif isinstance(fragment, InteractionUse):
                lines.append(f"{pad}- [{fragment.id}] ref {fragment.refers_to} covering {', '.join(fragment.covered)}")
            else:
                lines.append(f"{pad}- [{fragment.id}] destruction of {fragment.lifeline}")

    emit(interaction.fragments, 0)
    if interaction.executions:
        lines.append("Execution specifications:")
        for e in interaction.executions:
            lines.append(f"  - [{e.id}] on {e.lifeline} from {e.start} to {e.finish or '(last message)'}")
    return lines
