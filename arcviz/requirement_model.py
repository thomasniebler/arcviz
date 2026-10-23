"""Requirements of a project: raw wording, refinement, links, coverage.

Spec: docs/architecture/specs/requirements.md (ADR-0188..0190). A requirement keeps what was said (`raw`, never changed)
next to the sentence a person accepted (`statement`); an assistant's refinement is only a `suggestion` until a person
accepts it. The requirements of a project live in their own keyed document (`RequirementBox`, one per project), not in
the project document; this module is pure (no store, no services) and holds the types, their bounds, the operations on
a box, the integrity rules, coverage and the duplicate candidates.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .i18n import N_
from .model import now_iso, single_line
from .operations import OperationError, validate_arguments

# ---- bounds ----------------------------------------------------------------------------------------------------------

DOCUMENT = "requirements"  # kind of the keyed document, one per project (key = the project id)
MAX_REQUIREMENTS = 600
MAX_REQUIREMENTS_BYTES = 1_900_000  # of the serialised box: the hard limit, below Cosmos DB's 2 MB item limit
MAX_RAW = 1500
MAX_STATEMENT = 400
MAX_MEASURE = 300
MAX_THEME = 80
MAX_SOURCE = 150
MAX_EXTERNAL_ID = 80
MAX_ISSUES = 4
MAX_ISSUE_TEXT = 250
MAX_REASON = 250
MAX_STAKEHOLDERS = 10
MAX_ELEMENTS = 20
MAX_LINKED = 20  # requirements a driver, scenario or decision names

REQUIREMENT_KINDS = ("functional", "quality", "constraint", "business", "unclear")
REQUIREMENT_STATUSES = ("raw", "refined", "rejected", "duplicate")
PRIORITIES = ("", "must", "should", "could", "wont")
ISSUE_KINDS = ("ambiguous", "not_measurable", "incomplete", "duplicate", "conflict", "out_of_scope")
CLIENTS = ("ui", "mcp", "chat", "import", "derive")
SUGGESTION_FIELDS = ("statement", "kind", "priority", "measure", "theme", "stakeholders", "issues", "duplicate_of")
DUPLICATE_THRESHOLD = 0.7
MAX_CANDIDATES = 200
COVERAGE_LEVELS = ("uncovered", "driver", "scenario", "decision")  # in rising order

KIND_TEXT = {
    "functional": N_("Funktional"),
    "quality": N_("Qualität"),
    "constraint": N_("Randbedingung"),
    "business": N_("Fachlich"),
    "unclear": N_("Unklar"),
}
STATUS_TEXT = {"raw": N_("Roh"), "refined": N_("Verfeinert"), "rejected": N_("Verworfen"), "duplicate": N_("Duplikat")}


class RequirementError(OperationError):
    """Invalid input for a requirement operation; the box is unchanged."""


class StaleSuggestion(RequirementError):
    """The suggestion was replaced or is gone since the caller saw it (ADR-0190): nothing was accepted."""


# ---- types -----------------------------------------------------------------------------------------------------------


class Issue(BaseModel):
    kind: str  # ISSUE_KINDS
    text: str = ""
    other: str | None = None  # a requirement id: the duplicate, or the one it conflicts with
    resolved: bool = False


class Suggestion(BaseModel):
    """What an assistant proposes. Nothing of it is in force until a person accepts it (ADR-0190)."""

    statement: str
    kind: str = "unclear"
    priority: str = ""
    measure: str = ""
    theme: str = ""
    stakeholders: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    duplicate_of: str | None = None
    rev: str = ""  # fingerprint of the suggested content (not of `accepted`): a new suggestion has a new `rev`
    accepted: list[str] = Field(default_factory=list)  # fields already moved into force
    by: str = ""  # identity id of the caller whose request produced it
    by_name: str = ""
    client: str = "ui"
    at: str = ""


class Requirement(BaseModel):
    id: str  # req<n> from RequirementBox.seq, never reused
    raw: str  # the original wording, immutable after creation
    statement: str = ""  # the normalised sentence; "" while raw
    kind: str = "unclear"
    status: str = "raw"
    priority: str = ""
    measure: str = ""
    theme: str = ""
    source: str = ""
    external_id: str = ""
    stakeholders: list[str] = Field(default_factory=list)
    elements: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    duplicate_of: str | None = None
    reason: str = ""
    suggestion: Suggestion | None = None
    author: str = ""
    author_name: str = ""
    client: str = "ui"
    created_at: str
    updated_at: str


class RequirementBox(BaseModel):
    """The keyed document `requirements` of one project (key = project id)."""

    incarnation: str = ""  # of the project (ADR-0127): a project created again under a deleted id starts empty
    seq: int = 0
    requirements: list[Requirement] = Field(default_factory=list)


@dataclass(frozen=True)
class Known:
    """The ids a requirement's references are checked against (the project's stakeholders and elements)."""

    stakeholders: frozenset[str] = frozenset()
    elements: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Meta:
    """Who is asking and through which door; recorded on what the operation creates."""

    author: str = ""
    author_name: str = ""
    client: str = "ui"


# ---- small helpers ---------------------------------------------------------------------------------------------------


def number_of(requirement_id: str) -> int | None:
    rest = requirement_id[3:] if requirement_id.startswith("req") else ""
    return int(rest) if rest.isdigit() else None


def normalised_raw(text: str) -> str:
    """How two raw texts are compared for identity (an import twice adds nothing): one line, casefolded."""
    return " ".join(single_line(text).split()).casefold()


def find_requirement(box: RequirementBox, ref: str | None) -> Requirement | None:
    """A requirement by id, `external_id`, or a unique case-insensitive prefix of its statement or raw text."""
    if not ref or not ref.strip():
        return None
    ref = ref.strip()
    for r in box.requirements:
        if r.id == ref:
            return r
    by_external = [r for r in box.requirements if r.external_id and r.external_id == ref]
    if len(by_external) == 1:
        return by_external[0]
    prefix = normalised_raw(ref)
    hits = [r for r in box.requirements if normalised_raw(r.statement or r.raw).startswith(prefix)]
    return hits[0] if len(hits) == 1 else None


def _get(box: RequirementBox, ref: str) -> Requirement:
    found = find_requirement(box, ref)
    if found is None:
        known = ", ".join(r.id for r in box.requirements[:40]) or "none"
        more = " …" if len(box.requirements) > 40 else ""
        raise RequirementError(f"Unknown requirement '{ref}'. Requirements: {known}{more}.")
    return found


def _choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise RequirementError(f"Unknown {what} '{value}'. Available: {', '.join(a for a in allowed if a) or 'none'}.")
    return value


def _text(value: str, limit: int, what: str, one_line: bool = True) -> str:
    text = " ".join(single_line(value).split()) if one_line else single_line(value).strip()
    if len(text) > limit:
        raise RequirementError(f"{what} is at most {limit} characters long.")
    return text


def _unique(items: list[str], limit: int, what: str) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    if len(out) > limit:
        raise RequirementError(f"A requirement names at most {limit} {what}.")
    return out


def _issues(raw: list[dict[str, Any]]) -> list[Issue]:
    if len(raw) > MAX_ISSUES:
        raise RequirementError(f"A requirement carries at most {MAX_ISSUES} issues.")
    out: list[Issue] = []
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise RequirementError(f"Issue {n} must be an object with kind and text.")
        kind = _choice(str(item.get("kind") or ""), ISSUE_KINDS, "issue kind")
        other = item.get("other")
        out.append(
            Issue(
                kind=kind,
                text=_text(str(item.get("text") or ""), MAX_ISSUE_TEXT, f"The text of issue {n}"),
                other=str(other) if other else None,
                resolved=bool(item.get("resolved", False)),
            )
        )
    return out


def suggestion_rev(s: Suggestion) -> str:
    """The fingerprint of what a suggestion proposes (not of who made it or what was accepted already)."""
    body = s.model_dump(mode="json", include=set(SUGGESTION_FIELDS))
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


# ---- operations ------------------------------------------------------------------------------------------------------


def add_requirement(
    box: RequirementBox,
    raw: str,
    source: str = "",
    external_id: str = "",
    theme: str = "",
    priority: str = "",
    stakeholders: list[str] | None = None,
    elements: list[str] | None = None,
) -> Requirement:
    if len(box.requirements) >= MAX_REQUIREMENTS:
        raise RequirementError(f"A project holds at most {MAX_REQUIREMENTS} requirements.")
    text = _text(raw, MAX_RAW, "The requirement text")
    if not text:
        raise RequirementError("A requirement needs a text.")
    source = _text(source, MAX_SOURCE, "The source")
    external_id = _text(external_id, MAX_EXTERNAL_ID, "The external id")
    if external_id and any(r.external_id == external_id and r.source == source for r in box.requirements):
        raise RequirementError(f"A requirement with external id '{external_id}' from this source exists already.")
    numbers = [n for n in (number_of(r.id) for r in box.requirements) if n is not None]
    box.seq = max([box.seq, *numbers]) + 1
    now = now_iso()
    requirement = Requirement(
        id=f"req{box.seq}",
        raw=text,
        source=source,
        external_id=external_id,
        theme=_text(theme, MAX_THEME, "The theme"),
        priority=_choice(priority, PRIORITIES, "priority"),
        stakeholders=_unique(stakeholders or [], MAX_STAKEHOLDERS, "stakeholders"),
        elements=_unique(elements or [], MAX_ELEMENTS, "elements"),
        created_at=now,
        updated_at=now,
    )
    box.requirements.append(requirement)
    return requirement


def update_requirement(
    box: RequirementBox,
    requirement: str,
    statement: str | None = None,
    kind: str | None = None,
    priority: str | None = None,
    measure: str | None = None,
    theme: str | None = None,
    source: str | None = None,
    stakeholders: list[str] | None = None,
    elements: list[str] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> Requirement:
    """Edit the fields in force. Never `raw`; a statement on a raw requirement leaves it raw (`set_status` refines)."""
    target = _get(box, requirement)
    if statement is not None:
        target.statement = _text(statement, MAX_STATEMENT, "The statement")
        if not target.statement and target.status == "refined":
            raise RequirementError("A refined requirement needs a statement; set it back to raw first.")
    if kind is not None:
        target.kind = _choice(kind, REQUIREMENT_KINDS, "requirement kind")
    if priority is not None:
        target.priority = _choice(priority, PRIORITIES, "priority")
    if measure is not None:
        target.measure = _text(measure, MAX_MEASURE, "The measure")
    if theme is not None:
        target.theme = _text(theme, MAX_THEME, "The theme")
    if source is not None:
        target.source = _text(source, MAX_SOURCE, "The source")
    if stakeholders is not None:
        target.stakeholders = _unique(stakeholders, MAX_STAKEHOLDERS, "stakeholders")
    if elements is not None:
        target.elements = _unique(elements, MAX_ELEMENTS, "elements")
    if issues is not None:
        target.issues = _issues(issues)
    return target


def set_status(
    box: RequirementBox, requirement: str, status: str, duplicate_of: str | None = None, reason: str = ""
) -> Requirement:
    target = _get(box, requirement)
    status = _choice(status, REQUIREMENT_STATUSES, "status")
    if status == "refined" and not target.statement:
        raise RequirementError("A refined requirement needs a statement: write or accept one first.")
    if status == "duplicate":
        if not duplicate_of:
            raise RequirementError("A duplicate names the requirement it duplicates (duplicate_of).")
        original = _get(box, duplicate_of)
        if original.id == target.id:
            raise RequirementError("A requirement cannot be a duplicate of itself.")
        target.duplicate_of = original.id
    else:
        target.duplicate_of = None
    target.status = status
    target.reason = _text(reason, MAX_REASON, "The reason") if status == "rejected" else ""
    return target


def _suggested(box: RequirementBox, fields: dict[str, Any]) -> Suggestion:
    unknown = [k for k in fields if k not in SUGGESTION_FIELDS]
    if unknown:
        raise RequirementError(f"Unknown suggestion field {', '.join(repr(u) for u in unknown)}.")
    statement = _text(str(fields.get("statement") or ""), MAX_STATEMENT, "The suggested statement")
    if not statement:
        raise RequirementError("A suggestion needs a statement.")
    duplicate_of = fields.get("duplicate_of")
    if duplicate_of and find_requirement(box, str(duplicate_of)) is None:
        raise RequirementError(f"Unknown requirement '{duplicate_of}' in duplicate_of.")
    raw_issues = fields.get("issues") or []
    if not isinstance(raw_issues, list):
        raise RequirementError("The suggested issues must be a list.")
    for item in raw_issues:
        other = item.get("other") if isinstance(item, dict) else None
        if other and find_requirement(box, str(other)) is None:
            raise RequirementError(f"Unknown requirement '{other}' in an issue.")
    stakeholders = fields.get("stakeholders") or []
    if not isinstance(stakeholders, list) or not all(isinstance(s, str) for s in stakeholders):
        raise RequirementError("The suggested stakeholders must be a list of ids.")
    return Suggestion(
        statement=statement,
        kind=_choice(str(fields.get("kind") or "unclear"), REQUIREMENT_KINDS, "requirement kind"),
        priority=_choice(str(fields.get("priority") or ""), PRIORITIES, "priority"),
        measure=_text(str(fields.get("measure") or ""), MAX_MEASURE, "The suggested measure"),
        theme=_text(str(fields.get("theme") or ""), MAX_THEME, "The suggested theme"),
        stakeholders=_unique(stakeholders, MAX_STAKEHOLDERS, "stakeholders"),
        issues=_issues(raw_issues),
        duplicate_of=find_requirement(box, str(duplicate_of)).id if duplicate_of else None,  # type: ignore[union-attr]
    )


def set_suggestion(box: RequirementBox, requirement: str, suggestion: dict[str, Any]) -> Requirement:
    """Store or replace the suggestion. The fields in force are never touched (ADR-0190)."""
    target = _get(box, requirement)
    new = _suggested(box, suggestion)
    if new.duplicate_of == target.id:
        raise RequirementError("A requirement cannot be a duplicate of itself.")
    new.rev = suggestion_rev(new)
    new.at = now_iso()
    target.suggestion = new
    return target


def accept_suggestion(
    box: RequirementBox, requirement: str, expected: str, fields: list[str] | None = None
) -> Requirement:
    """Move the named fields (default: all not accepted yet) of the suggestion into force. Refused when `expected` is not
    the suggestion's current `rev`, so a suggestion replaced meanwhile is never accepted unseen."""
    target = _get(box, requirement)
    suggestion = target.suggestion
    if suggestion is None:
        raise StaleSuggestion(f"Requirement '{target.id}' has no suggestion.")
    if suggestion.rev != expected:
        raise StaleSuggestion(f"The suggestion of '{target.id}' changed since it was shown; look at it again.")
    wanted = [f for f in (fields if fields is not None else SUGGESTION_FIELDS) if f not in suggestion.accepted]
    for name in wanted:
        _choice(name, SUGGESTION_FIELDS, "suggestion field")
    for name in wanted:
        value = getattr(suggestion, name)
        if name == "issues":
            target.issues = [i.model_copy() for i in value]
        elif name == "duplicate_of":
            target.duplicate_of = value
            if value:
                target.status = "duplicate"
        elif name == "stakeholders":
            target.stakeholders = list(value)
        else:
            setattr(target, name, value)
        suggestion.accepted.append(name)
    if "statement" in wanted and target.status == "raw":
        target.status = "refined"
    if all(f in suggestion.accepted for f in SUGGESTION_FIELDS):
        target.suggestion = None
    return target


def reject_suggestion(box: RequirementBox, requirement: str) -> Requirement:
    target = _get(box, requirement)
    target.suggestion = None
    return target


def resolve_issue(box: RequirementBox, requirement: str, index: int, resolved: bool = True) -> Requirement:
    target = _get(box, requirement)
    if not 0 <= index < len(target.issues):
        raise RequirementError(
            f"Requirement '{target.id}' has {len(target.issues)} issue(s); index {index} is invalid."
        )
    target.issues[index].resolved = resolved
    return target


def remove_requirement(box: RequirementBox, requirement: str) -> dict[str, Any]:
    """Remove it. The references to it (drivers, scenarios, decisions of the project) are the service's cascade."""
    target = _get(box, requirement)
    box.requirements.remove(target)
    for other in box.requirements:  # a duplicate of the removed one is a plain requirement again
        if other.duplicate_of == target.id:
            other.duplicate_of, other.status = None, "raw" if not other.statement else "refined"
        for issue in other.issues:
            if issue.other == target.id:
                issue.other = None
        if other.suggestion is not None:
            if other.suggestion.duplicate_of == target.id:
                other.suggestion.duplicate_of = None
            for issue in other.suggestion.issues:
                if issue.other == target.id:
                    issue.other = None
    return {"requirement": target.model_dump(mode="json")}


OPERATIONS: dict[str, Callable[..., Any]] = {
    "add_requirement": add_requirement,
    "update_requirement": update_requirement,
    "set_status": set_status,
    "set_suggestion": set_suggestion,
    "accept_suggestion": accept_suggestion,
    "reject_suggestion": reject_suggestion,
    "resolve_issue": resolve_issue,
    "remove_requirement": remove_requirement,
}
# What an agent (MCP credential or chat assistant) may do: staging data and suggestions, nothing that decides
# (spec: architecture-objects-over-mcp.md, ADR-0186). `update_requirement` is further limited to AGENT_UPDATE_FIELDS.
AGENT_OPERATIONS = ("add_requirement", "update_requirement", "set_suggestion")
AGENT_UPDATE_FIELDS = ("source", "theme", "priority", "stakeholders", "elements")


class Rejected(RequirementError):
    """A caller may not do this (an agent deciding); the message names what it may do instead."""


def check_agent(op: str, args: dict[str, Any]) -> None:
    """Rejected unless an agent may call `op` with these arguments."""
    if op not in AGENT_OPERATIONS:
        raise Rejected(
            f"An agent cannot use '{op}': people decide. It can add requirements, edit "
            f"{', '.join(AGENT_UPDATE_FIELDS)}, and suggest a refinement (set_suggestion)."
        )
    if op == "update_requirement":
        forbidden = [k for k in args if k != "requirement" and k not in AGENT_UPDATE_FIELDS]
        if forbidden:
            raise Rejected(
                f"An agent cannot change {', '.join(forbidden)} of a requirement; suggest a refinement instead. "
                f"Allowed: {', '.join(AGENT_UPDATE_FIELDS)}."
            )


def check_known(args: dict[str, Any], known: Known) -> None:
    """RequirementError when `args` name a stakeholder or element the project does not have."""
    for key, ids, what in (
        ("stakeholders", known.stakeholders, "stakeholder"),
        ("elements", known.elements, "element"),
    ):
        values = args.get(key)
        if isinstance(values, list):
            missing = [v for v in values if v not in ids]
            if missing:
                raise RequirementError(
                    f"Unknown {what} {', '.join(repr(m) for m in missing)}. Known: {', '.join(sorted(ids)) or 'none'}."
                )
    suggestion = args.get("suggestion")
    if isinstance(suggestion, dict):
        check_known({"stakeholders": suggestion.get("stakeholders")}, known)


def _execute(box: RequirementBox, op: str, args: dict[str, Any], known: Known | None, meta: Meta | None) -> Any:
    """Run one operation on the box (no rollback: `apply` and `apply_each` own that); returns the operation's result."""
    func = OPERATIONS.get(op)
    if func is None:
        raise RequirementError(f"Unknown requirement operation '{op}'. Available: {', '.join(OPERATIONS)}")
    args = validate_arguments(func, op, args, RequirementError)
    if known is not None:
        check_known(args, known)
    result = func(box, **args)
    if isinstance(result, Requirement):
        result.updated_at = now_iso()
        if op == "add_requirement" and meta is not None:
            result.author, result.author_name, result.client = meta.author, meta.author_name, meta.client
        if op == "set_suggestion" and meta is not None and result.suggestion is not None:
            s = result.suggestion
            s.by, s.by_name, s.client = meta.author, meta.author_name, meta.client
    return result


def _restore(box: RequirementBox, snapshot: RequirementBox) -> None:
    for name in type(box).model_fields:
        setattr(box, name, getattr(snapshot, name))


def apply(
    box: RequirementBox, op: str, args: dict[str, Any], known: Known | None = None, meta: Meta | None = None
) -> dict[str, Any]:
    """Apply a named operation to the box; `{"ok": True, "op", "result"}`. RequirementError on invalid input; on that or
    any other exception the box is left unchanged. With `known` the references are checked against it."""
    snapshot = box.model_copy(deep=True)
    try:
        result = _execute(box, op, args, known, meta)
        errors = integrity_errors(box)
        if errors:  # a defect of an operation, never bad input
            raise AssertionError(f"Requirement operation {op} broke integrity: {'; '.join(errors)}")
    except BaseException:
        _restore(box, snapshot)
        raise
    payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    return {"ok": True, "op": op, "result": payload}


@dataclass
class StepResult:
    ok: bool
    result: Any = None
    error: str | None = None
    stale: bool = False  # an accept whose suggestion was replaced or is gone (StaleSuggestion)


def apply_each(
    box: RequirementBox,
    steps: list[tuple[str, dict[str, Any]]],
    known: Known | None = None,
    meta: Meta | None = None,
) -> list[StepResult]:
    """Apply many operations (an import, a bulk accept) with one snapshot of the box, not one per step: a step that fails
    with a RequirementError is undone and reported, the others stay. Any other exception undoes everything and propagates.
    Integrity is asserted once at the end."""
    snapshot = box.model_copy(deep=True)
    results: list[StepResult] = []
    try:
        for op, args in steps:
            ref = args.get("requirement") if isinstance(args, dict) else None
            target = find_requirement(box, ref) if isinstance(ref, str) else None
            before = target.model_copy(deep=True) if target is not None else None
            size, seq = len(box.requirements), box.seq
            try:
                results.append(StepResult(True, _execute(box, op, args, known, meta)))
            except RequirementError as exc:
                if before is not None:  # put the one requirement back as it was, undo an append
                    box.requirements[box.requirements.index(target)] = before  # type: ignore[arg-type]
                del box.requirements[size:]
                box.seq = seq
                results.append(StepResult(False, error=str(exc), stale=isinstance(exc, StaleSuggestion)))
        errors = integrity_errors(box)
        if errors:
            raise AssertionError(f"Requirement operations broke integrity: {'; '.join(errors)}")
    except BaseException:
        _restore(box, snapshot)
        raise
    return results


def renumber(box: RequirementBox) -> dict[str, str]:
    """Give the requirements the ids `req1`, `req2`, … in their order (a bundle import, where ids are local) and follow the
    references inside the box; returns old id -> new id. `seq` becomes the highest number."""
    mapping = {r.id: f"req{n}" for n, r in enumerate(box.requirements, start=1)}
    for r in box.requirements:
        r.id = mapping[r.id]
    for r in box.requirements:
        r.duplicate_of = mapping.get(r.duplicate_of) if r.duplicate_of else None
        suggestions = [r.suggestion] if r.suggestion is not None else []
        for issues in (r.issues, *(s.issues for s in suggestions)):
            for issue in issues:
                issue.other = mapping.get(issue.other) if issue.other else None
        for s in suggestions:
            s.duplicate_of = mapping.get(s.duplicate_of) if s.duplicate_of else None
    box.seq = len(box.requirements)
    return mapping


# ---- integrity -------------------------------------------------------------------------------------------------------


def integrity_errors(box: RequirementBox) -> list[str]:
    """Problems of the box itself (references to the project are checked on write by `check_known`, and tolerated when
    dangling on read, ADR-0188). Empty for a consistent box."""
    errors: list[str] = []
    ids: set[str] = set()
    externals: set[tuple[str, str]] = set()
    for r in box.requirements:
        if r.id in ids:
            errors.append(f"duplicate id {r.id}")
        ids.add(r.id)
        if r.status not in REQUIREMENT_STATUSES or r.kind not in REQUIREMENT_KINDS or r.priority not in PRIORITIES:
            errors.append(f"{r.id}: value outside its closed set")
        if not r.raw.strip():
            errors.append(f"{r.id}: empty raw text")
        if r.status == "refined" and not r.statement:
            errors.append(f"{r.id}: refined without a statement")
        if r.external_id:
            key = (r.source, r.external_id)
            if key in externals:
                errors.append(f"{r.id}: external id {r.external_id} is used twice")
            externals.add(key)
    for r in box.requirements:
        if (r.status == "duplicate") != (r.duplicate_of is not None):
            errors.append(f"{r.id}: duplicate_of is set exactly for status duplicate")
        if r.duplicate_of is not None and (r.duplicate_of not in ids or r.duplicate_of == r.id):
            errors.append(f"{r.id}: duplicate_of names no other requirement")
    for r in box.requirements:  # no cycle of duplicates
        seen, cursor = {r.id}, r.duplicate_of
        by_id = {x.id: x for x in box.requirements}
        while cursor is not None and cursor in by_id:
            if cursor in seen:
                errors.append(f"{r.id}: duplicate_of forms a cycle")
                break
            seen.add(cursor)
            cursor = by_id[cursor].duplicate_of
    return errors


def dangling(box: RequirementBox, known: Known) -> dict[str, int]:
    """How many stakeholder and element references point at nothing (counted, never an error: ADR-0188)."""
    count = {"stakeholders": 0, "elements": 0}
    for r in box.requirements:
        count["stakeholders"] += sum(s not in known.stakeholders for s in r.stakeholders)
        count["elements"] += sum(e not in known.elements for e in r.elements)
    return count


def drop_dangling(box: RequirementBox, known: Known) -> bool:
    """Remove references to stakeholders and elements that no longer exist; True when something changed."""
    changed = False
    for r in box.requirements:
        stakeholders = [s for s in r.stakeholders if s in known.stakeholders]
        elements = [e for e in r.elements if e in known.elements]
        if stakeholders != r.stakeholders or elements != r.elements:
            r.stakeholders, r.elements, changed = stakeholders, elements, True
        s = r.suggestion
        if s is not None:
            kept = [x for x in s.stakeholders if x in known.stakeholders]
            if kept != s.stakeholders:
                s.stakeholders, changed = kept, True
    return changed


def drop_ids(box: RequirementBox, stakeholder: str | None = None, element: str | None = None) -> bool:
    """The cascade of a removed stakeholder or element: drop the id from every requirement; True when changed."""
    changed = False
    for r in box.requirements:
        if stakeholder is not None and stakeholder in r.stakeholders:
            r.stakeholders, changed = [s for s in r.stakeholders if s != stakeholder], True
        if element is not None and element in r.elements:
            r.elements, changed = [e for e in r.elements if e != element], True
        s = r.suggestion
        if s is not None and stakeholder is not None and stakeholder in s.stakeholders:
            s.stakeholders, changed = [x for x in s.stakeholders if x != stakeholder], True
    return changed


# ---- coverage and traceability ---------------------------------------------------------------------------------------


class _Linked(Protocol):
    id: str
    requirements: list[str]


class _Traced(Protocol):
    drivers: list[Any]
    scenarios: list[Any]
    decisions: list[Any]


@dataclass
class Trace:
    drivers: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        if self.decisions:
            return "decision"
        if self.scenarios:
            return "scenario"
        if self.drivers:
            return "driver"
        return "uncovered"


def traces(project: _Traced) -> dict[str, Trace]:
    """The inverse index: requirement id -> the drivers, scenarios and decisions that name it (stored on those, ADR-0189)."""
    out: dict[str, Trace] = {}
    for items, attr in (
        (project.drivers, "drivers"),
        (project.scenarios, "scenarios"),
        (project.decisions, "decisions"),
    ):
        for item in items:
            if getattr(item, "status", "") == "retired":  # a retired driver or scenario covers nothing (ADR-0198)
                continue
            for rid in getattr(item, "requirements", []):
                getattr(out.setdefault(rid, Trace()), attr).append(item.id)
    return out


def coverage_map(box: RequirementBox, project: _Traced) -> dict[str, str | None]:
    """Coverage level per requirement (never stored). Refined ones get their own trace; a duplicate counts through the
    requirement it duplicates; raw and rejected ones have none (None)."""
    traced = traces(project)
    by_id = {r.id: r for r in box.requirements}
    out: dict[str, str | None] = {}
    for r in box.requirements:
        target = r
        seen = {r.id}
        while target.status == "duplicate" and target.duplicate_of in by_id and target.duplicate_of not in seen:
            seen.add(target.duplicate_of)
            target = by_id[target.duplicate_of]
        if target.status != "refined":
            out[r.id] = None
        else:
            out[r.id] = traced.get(target.id, Trace()).level
    return out


# ---- duplicates ------------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokens_of(text: str) -> frozenset[str]:
    """Language-neutral word set of a text: casefolded words of three or more characters (no stemmer, no stop words)."""
    return frozenset(t for t in _TOKEN.findall(text.casefold()) if len(t) >= 3)


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of the word sets of two texts, 0.0 to 1.0."""
    ta, tb = tokens_of(a), tokens_of(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def duplicate_candidates(
    box: RequirementBox,
    ids: set[str] | None = None,
    threshold: float = DUPLICATE_THRESHOLD,
    limit: int = MAX_CANDIDATES,
) -> list[tuple[str, str, float]]:
    """Pairs of requirements whose texts are similar, best first, at most `limit`. With `ids` only pairs that involve one
    of them. Uses an inverted index over the words, so it is linear in the words, not quadratic in the requirements."""
    texts = {r.id: tokens_of(r.statement or r.raw) for r in box.requirements if r.status != "duplicate"}
    index: dict[str, list[str]] = {}
    for rid, tokens in texts.items():
        for token in tokens:
            index.setdefault(token, []).append(rid)
    pairs: dict[tuple[str, str], float] = {}
    for rid, tokens in texts.items():
        if ids is not None and rid not in ids:
            continue
        shared: dict[str, int] = {}
        for token in tokens:
            for other in index.get(token, ()):
                if other != rid:
                    shared[other] = shared.get(other, 0) + 1
        for other, count in shared.items():
            if count < 2 and len(tokens) > 1:
                continue
            key = (rid, other) if rid < other else (other, rid)
            if key in pairs:
                continue
            score = len(tokens & texts[other]) / len(tokens | texts[other])
            if score >= threshold:
                pairs[key] = score
    ranked = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(a, b, round(score, 3)) for (a, b), score in ranked[:limit]]


# ---- pipeline steps ----------------------------------------------------------------------------------------------------

STEPS = ("raw", "suggested", "refined", "derived", "rejected", "duplicate")


def step_of(requirement: Requirement, coverage: str | None) -> str:
    """Where a requirement is in the pipeline (derived, never stored): `raw`, `suggested` (raw with a suggestion),
    `refined` (accepted, not covered), `derived` (covered by a driver, scenario or decision), `rejected`, `duplicate`."""
    if requirement.status == "raw":
        return "suggested" if requirement.suggestion is not None else "raw"
    if requirement.status == "refined":
        return "derived" if coverage not in (None, "uncovered") else "refined"
    return requirement.status


def digest(box: RequirementBox, coverage: dict[str, str | None]) -> dict[str, Any]:
    """Counts per pipeline step, kind and theme (the table header)."""
    steps = dict.fromkeys(STEPS, 0)
    kinds: dict[str, int] = {}
    themes: dict[str, int] = {}
    for r in box.requirements:
        steps[step_of(r, coverage.get(r.id))] += 1
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
        if r.theme:
            themes[r.theme] = themes.get(r.theme, 0) + 1
    top = dict(sorted(themes.items(), key=lambda kv: (-kv[1], kv[0]))[:30])
    return {"total": len(box.requirements), "steps": steps, "kinds": kinds, "themes": top}
