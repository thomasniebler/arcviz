"""ProposalService: the stored proposals of agents (spec: docs/architecture/specs/agent-access.md, ADR-0172).

One keyed document `proposal` per project holds its proposals (so listing is one read, also with Cosmos DB), written
conditionally on its revision like every keyed document. Creating dry-runs the operations on a copy of the diagram and
stores them with the preview; nothing is written to a diagram before a person applies the proposal, which replays it
through `proposal.apply_proposal` (the same base check, preview check and adoption as a confirmed chat proposal).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import ValidationError

from .. import operations
from .. import projects as project_ops
from ..model import Diagram, fingerprint, now_iso, single_line
from ..operations import OperationError
from ..ports import (
    MUTATION_ATTEMPTS,
    Change,
    ConflictError,
    DocumentRepositoryPort,
    StoreError,
    VersionInfo,
    is_definitely_missing,
)
from ..projects import incarnation_of
from ..proposals import (
    AGENT_REFUSED_FIELDS,
    APPLIED_NOTE,
    CLAIM_TTL_SECONDS,
    DEFAULT_RETENTION_DAYS,
    DISCARDED_NOTE,
    DOCUMENT,
    KEY_RE,
    LIVE,
    MAX_BYTES,
    MAX_ITEMS,
    MAX_OPERATIONS,
    MAX_PENDING,
    MAX_PENDING_PROJECT,
    MAX_PROJECT_PROPOSAL_BYTES,
    MAX_RATIONALE,
    MAX_TITLE,
    PROPOSABLE_OPERATIONS,
    STATUSES,
    ProposalBox,
    ProposalConflict,
    ProposalItem,
    StoredProposal,
    diagram_diff,
    references,
    resolve,
    sweep,
)
from ..workspaces import Identity
from .adoption import adopting
from .diagrams import DiagramService
from .model import ModelService
from .projects import ProjectService
from .requirements import RequirementService

T = TypeVar("T")


class ProposalError(OperationError):
    """Invalid input for a proposal, or a proposal in a state that does not allow the call (HTTP 400); nothing was
    written."""


class ProposalService:
    def __init__(
        self,
        documents: DocumentRepositoryPort,
        projects: ProjectService,
        diagrams: DiagramService,
        models: ModelService,
        actor: Identity | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        requirements: RequirementService | None = None,
    ):
        self._requirements = requirements  # to check the requirement ids an item names; None: not checked
        self._docs = documents
        self._projects = projects
        self._diagrams = diagrams
        self._models = models
        self._actor = actor
        self._retention = retention_days

    # ---- reads (viewer) --------------------------------------------------------------------------------------

    def list(self, project_id: str, status: str | None = None) -> list[StoredProposal]:
        """The project's proposals, newest first; decided ones past the retention are left out (they are dropped from the
        document by the next write, never by a read)."""
        self._projects.require(project_id, "viewer")
        if status is not None and status not in STATUSES:
            raise ProposalError(f"Unknown status '{status}'. Available: {', '.join(STATUSES)}.")
        box, _ = self._load(project_id)
        sweep(box, self._retention)
        found = [p for p in box.proposals if status is None or p.status == status]
        return sorted(found, key=lambda p: int(p.id[1:]), reverse=True)

    def get(self, project_id: str, proposal_id: str) -> StoredProposal:
        self._projects.require(project_id, "viewer")
        box, _ = self._load(project_id)
        return self._find(box, project_id, proposal_id)

    # ---- writes (editor) -------------------------------------------------------------------------------------

    def create(
        self, project_id: str, diagram_id: str, operation_list: Any, rationale: str = "", client: str = "mcp"
    ) -> StoredProposal:
        """Dry-run `operation_list` ([{"op", "arguments"}]) on a copy of the diagram and store it as a pending proposal.
        ProposalError for invalid input, a diagram outside the project, too many pending proposals; nothing is stored
        then, and no diagram is ever written."""
        self._projects.require(project_id, "editor")
        project = self._projects.get(project_id)
        if project.member(diagram_id) is None:
            raise ProposalError(f"Diagram '{diagram_id}' is not in project '{project_id}'.")
        ops = _normalised(operation_list)
        if len(rationale) > MAX_RATIONALE:
            raise ProposalError(f"The rationale is too long ({len(rationale)} characters, at most {MAX_RATIONALE}).")
        before = self._diagrams.get(diagram_id)
        after = before.model_copy(deep=True)
        for number, op in enumerate(ops, start=1):
            try:
                operations.apply(after, op["op"], op["arguments"])
            except OperationError as exc:
                raise ProposalError(f"Operation {number} ({op['op']}) cannot be applied: {exc}") from exc
        diff = diagram_diff(before, after)
        size = len(json.dumps({"operations": ops, "diff": diff}, ensure_ascii=False))
        if size > MAX_BYTES:
            raise ProposalError(
                f"The proposal is too large ({size // 1024} KB, at most {MAX_BYTES // 1024} KB); split it into several."
            )
        actor = self._actor

        def add(box: ProposalBox) -> StoredProposal:
            if sum(p.status in LIVE and p.kind == "diagram" for p in box.proposals) >= MAX_PENDING:
                raise ProposalError(
                    f"The project already has {MAX_PENDING} pending proposals; wait until people decided some."
                )
            box.seq += 1
            stored = StoredProposal(
                id=f"p{box.seq}",
                project=project_id,
                diagram=diagram_id,
                author=actor.id if actor else "system",
                author_name=actor.name if actor else "",
                client=client,
                created_at=now_iso(),
                rationale=rationale.strip(),
                operations=ops,
                base=fingerprint(before),
                result=fingerprint(after),
                diff=diff,
            )
            box.proposals.append(stored)
            return stored

        return self._update(project_id, add)

    def apply(self, project_id: str, proposal_id: str, accept: Sequence[str] | None = None) -> StoredProposal:
        """Replay the proposal on its diagram as one version and mark it applied. The proposal is claimed (`applying`)
        before the diagram is written, so a concurrent discard or apply is refused instead of contradicting the
        diagram; `ProposalConflict` (and the proposal `stale`) when the diagram changed since or is gone; ProposalError for
        a decided or claimed proposal."""
        self._projects.require(project_id, "editor")
        if self._find(self._load(project_id)[0], project_id, proposal_id).kind == "project":
            return self._apply_project(project_id, proposal_id, accept)
        if accept is not None:
            raise ProposalError("`accept` names the items of a project proposal; a diagram proposal is applied whole.")
        item = self._claim(project_id, proposal_id)
        label = f"Proposal {item.id}" + (f" by {item.author_name}" if item.author_name else "")
        try:
            _, version, _ = apply_proposal(
                self._diagrams,
                item.diagram,
                item.operations,
                item.base,
                item.result,
                label=label,
                models=self._models,
            )
        except ProposalConflict:
            written = self._written_version(item, label)
            if written is not None:  # an earlier attempt wrote the diagram and was interrupted before the mark
                return self._decide(project_id, proposal_id, "applied", written)
            self._settle(project_id, proposal_id, "stale")
            raise
        except StoreError as exc:
            if not is_definitely_missing(exc):
                self._settle(project_id, proposal_id, "pending")
                raise
            self._settle(project_id, proposal_id, "stale")
            raise ProposalConflict(f"The diagram '{item.diagram}' no longer exists.") from exc
        except BaseException:
            self._settle(project_id, proposal_id, "pending")  # nothing is known to be written: it can be tried again
            raise
        return self._decide(project_id, proposal_id, "applied", version.version)

    def discard(self, project_id: str, proposal_id: str, keys: Sequence[str] | None = None) -> StoredProposal:
        self._projects.require(project_id, "editor")
        if self._open(project_id, proposal_id).kind == "project":
            return self._discard_items(project_id, proposal_id, keys)
        if keys is not None:
            raise ProposalError("`keys` name the items of a project proposal; a diagram proposal is discarded whole.")
        return self._decide(project_id, proposal_id, "discarded", None)

    # ---- project proposals (ADR-0187) -------------------------------------------------------------------------

    def create_project(self, project_id: str, items: Any, rationale: str = "", client: str = "mcp") -> StoredProposal:
        """Store a project proposal: `items` ([{key, op, arguments, derived_from?}]) are project operations on drivers,
        scenarios and decisions, checked in order on a copy of the project (later items may name earlier ones as
        `@key`). ProposalError for invalid input, naming the item; nothing is stored then and the project is never
        written. A person applies the items, all or in part."""
        self._projects.require(project_id, "editor")
        if len(rationale) > MAX_RATIONALE:
            raise ProposalError(f"The rationale is too long ({len(rationale)} characters, at most {MAX_RATIONALE}).")
        parsed = _items(items)
        known = self._requirements.ids(project_id) if self._requirements is not None else None
        copy = self._projects.get(project_id).model_copy(deep=True)
        ids: dict[str, str] = {}
        for item in parsed:
            later = references(item.arguments) - set(ids)
            if later:
                raise ProposalError(
                    f"Item '{item.key}' refers to {', '.join('@' + k for k in sorted(later))}, which is not an earlier item."
                )
            named = [*item.derived_from, *(item.arguments.get("requirements") or [])]
            if known is not None and any(isinstance(r, str) and r not in known for r in named):
                missing = sorted({str(r) for r in named if r not in known})
                raise ProposalError(
                    f"Item '{item.key}' names unknown requirement(s) {', '.join(missing)}. Requirements: "
                    f"{', '.join(sorted(known)[:20]) or 'none'}."
                )
            args = resolve(item.arguments, ids)
            if item.op.startswith("update_") and not references(item.arguments):
                target = project_ops.target_of(copy, item.op, args)
                item.base = project_ops.object_fingerprint(target) if target is not None else None
            try:
                out = project_ops.apply(copy, item.op, args)
                if item.derived_from and item.op.startswith("update_"):
                    target = project_ops.target_of(copy, item.op, args)
                    if target is not None:
                        project_ops.apply(
                            copy,
                            "link_requirements",
                            {
                                "target_kind": item.op.split("_", 1)[1],
                                "target": getattr(target, "id", ""),
                                "requirements": item.derived_from,
                            },
                        )
            except OperationError as exc:
                raise ProposalError(f"Item '{item.key}' ({item.op}) cannot be applied: {exc}") from exc
            if item.op.startswith("add_"):
                ids[item.key] = out["result"]["id"]
        size = len(json.dumps([i.model_dump() for i in parsed], ensure_ascii=False))
        if size > MAX_PROJECT_PROPOSAL_BYTES:
            raise ProposalError(
                f"The proposal is too large ({size // 1024} KB, at most {MAX_PROJECT_PROPOSAL_BYTES // 1024} KB); "
                "split it into several."
            )
        actor = self._actor

        def add(box: ProposalBox) -> StoredProposal:
            if sum(p.status in LIVE and p.kind == "project" for p in box.proposals) >= MAX_PENDING_PROJECT:
                raise ProposalError(
                    f"The project already has {MAX_PENDING_PROJECT} pending project proposals; wait until people "
                    "decided some."
                )
            box.seq += 1
            stored = StoredProposal(
                id=f"p{box.seq}",
                project=project_id,
                diagram="",
                kind="project",
                items=parsed,
                author=actor.id if actor else "system",
                author_name=actor.name if actor else "",
                client=client,
                created_at=now_iso(),
                rationale=rationale.strip(),
                operations=[],
                base="",
                result="",
            )
            box.proposals.append(stored)
            return stored

        return self._update(project_id, add)

    def _apply_project(self, project_id: str, proposal_id: str, accept: Sequence[str] | None) -> StoredProposal:
        """Apply the named items (default: every pending one) in order. The proposal is claimed first, like a diagram
        proposal; items are independent project writes; an `add_*` carries `origin` so that a repeat after a crash finds
        the object instead of creating a second one."""
        claimed = self._claim(project_id, proposal_id)
        try:
            selected = _selection(claimed, accept)
            states = self._run_items(project_id, claimed, selected)
        except BaseException:
            self._settle(project_id, proposal_id, "pending")
            raise
        return self._finish_project(project_id, proposal_id, states)

    def _run_items(self, project_id: str, proposal: StoredProposal, selected: set[str]) -> dict[str, ProposalItem]:
        done: dict[str, ProposalItem] = {i.key: i.model_copy(deep=True) for i in proposal.items}
        created = {k: i.created for k, i in done.items() if i.created}
        for item in proposal.items:
            if item.key not in selected:
                continue
            out = done[item.key]
            missing = sorted(references(item.arguments) - set(created))
            if missing:
                out.state, out.error = (
                    "failed",
                    f"depends on {', '.join('@' + k for k in missing)}, which was not applied",
                )
                continue
            args = resolve(item.arguments, created)
            project = self._projects.fresh(project_id)
            origin = f"{proposal.id}/{item.key}"
            if item.op.startswith("add_"):
                existing = _with_origin(project, item.op, origin)
                if existing is not None:  # an earlier, interrupted application created it
                    out.state, out.created = "applied", existing
                    created[item.key] = existing
                    continue
            target = project_ops.target_of(project, item.op, args)
            if item.base is not None and (target is None or project_ops.object_fingerprint(target) != item.base):
                out.state = "stale"
                out.error = "the target no longer exists" if target is None else "the target changed since the proposal"
                continue
            steps: list[tuple[str, dict[str, Any]]]
            if item.op.startswith("add_"):
                linked = list(dict.fromkeys([*(args.get("requirements") or []), *item.derived_from]))
                steps = [(item.op, {**args, "origin": origin, **({"requirements": linked} if linked else {})})]
            else:
                steps = [(item.op, args)]
                if item.derived_from and item.op.startswith("update_") and target is not None:
                    steps.append(
                        (
                            "link_requirements",
                            {
                                "target_kind": item.op.split("_", 1)[1],
                                "target": getattr(target, "id", ""),
                                "requirements": item.derived_from,
                            },
                        )
                    )
            try:
                results = self._projects.apply_all(project_id, steps)
            except (OperationError, StoreError) as exc:
                out.state, out.error = "failed", str(exc)[:300]
                continue
            out.state = "applied"
            if item.op.startswith("add_"):
                out.created = results[0]["result"]["id"]
                created[item.key] = out.created
        return done

    def _finish_project(self, project_id: str, proposal_id: str, states: dict[str, ProposalItem]) -> StoredProposal:
        actor = self._actor

        def finish(box: ProposalBox) -> StoredProposal:
            stored = self._find(box, project_id, proposal_id)
            for item in stored.items:
                new = states[item.key]
                item.state, item.created, item.error = new.state, new.created, new.error
            _settle_project(stored, actor.id if actor else "system")
            return stored

        return self._update(project_id, finish)

    def _discard_items(self, project_id: str, proposal_id: str, keys: Sequence[str] | None) -> StoredProposal:
        actor = self._actor

        def discard(box: ProposalBox) -> StoredProposal:
            stored = self._find(box, project_id, proposal_id)
            _decidable(stored)
            wanted = {i.key for i in stored.pending_items()} if keys is None else set(keys)
            unknown = sorted(wanted - {i.key for i in stored.pending_items()})
            if unknown:
                raise ProposalError(
                    f"No pending item {', '.join(repr(k) for k in unknown)} in proposal '{proposal_id}'."
                )
            for item in stored.items:  # the items that depend on a discarded one cannot be applied either
                if item.state == "pending" and (item.key in wanted or references(item.arguments) & wanted):
                    item.state = "discarded"
                    wanted.add(item.key)
            _settle_project(stored, actor.id if actor else "system")
            return stored

        return self._update(project_id, discard)

    # ---- internals -------------------------------------------------------------------------------------------

    def _open(self, project_id: str, proposal_id: str) -> StoredProposal:
        """The proposal if it can still be decided (pending, stale, or claimed by a process that is gone)."""
        item = self._find(self._load(project_id)[0], project_id, proposal_id)
        _decidable(item)
        return item

    def _claim(self, project_id: str, proposal_id: str) -> StoredProposal:
        """Mark the proposal `applying` for the caller, conditionally on the revision read: of two concurrent applies one
        wins, the other is refused."""
        actor = self._actor

        def claim(box: ProposalBox) -> StoredProposal:
            item = self._find(box, project_id, proposal_id)
            _decidable(item)
            item.status, item.decided_by, item.decided_at = "applying", (actor.id if actor else "system"), now_iso()
            return item

        return self._update(project_id, claim)

    def _decide(self, project_id: str, proposal_id: str, status: str, version: int | None) -> StoredProposal:
        actor = self._actor

        def decide(box: ProposalBox) -> StoredProposal:
            item = self._find(box, project_id, proposal_id)
            if item.status in ("applied", "discarded"):  # decided meanwhile by somebody else
                raise ProposalError(f"Proposal '{proposal_id}' is already {item.status}.")
            if status == "discarded":
                _decidable(item)  # a live claim of an apply in progress is not overridden
            item.status, item.version = status, version
            item.decided_by, item.decided_at = (actor.id if actor else "system"), now_iso()
            return item

        return self._update(project_id, decide)

    def _settle(self, project_id: str, proposal_id: str, status: str) -> None:
        """Give an `applying` claim up: back to `pending`, or `stale`. A proposal somebody else decided meanwhile stays."""

        def settle(box: ProposalBox) -> None:
            item = self._find(box, project_id, proposal_id)
            if item.status in ("pending", "applying", "stale"):
                item.status = status
                if status == "pending":
                    item.decided_by = item.decided_at = None

        self._update(project_id, settle)

    def _written_version(self, item: StoredProposal, label: str) -> int | None:
        """The version an earlier, interrupted application of this proposal wrote (its label, made since the claim), if
        there is one. The fingerprint of the result cannot tell: the application also links the new views to elements."""
        since = item.decided_at or ""
        try:
            versions = self._diagrams.versions(item.diagram)
        except StoreError:
            return None
        found = [v.version for v in versions if v.label == label and v.created_at >= since]
        return max(found) if found else None

    @staticmethod
    def _find(box: ProposalBox, project_id: str, proposal_id: str) -> StoredProposal:
        found = next((p for p in box.proposals if p.id == proposal_id), None)
        if found is None:
            known = ", ".join(p.id for p in box.proposals) or "none"
            raise ProposalError(f"Unknown proposal '{proposal_id}' in project '{project_id}'. Proposals: {known}.")
        return found

    def _load(self, project_id: str) -> tuple[ProposalBox, str | None]:
        """The project's box and its revision (None while the document does not exist). An unreadable document is an
        error, never an empty box: writing over it would lose the proposals."""
        incarnation = incarnation_of(self._projects.get(project_id))
        try:
            body, revision = self._docs.read_document(DOCUMENT, project_id)
        except ConflictError:
            raise
        except StoreError as exc:
            if is_definitely_missing(exc):
                return ProposalBox(incarnation=incarnation), None
            raise
        try:
            box = ProposalBox.model_validate(body)
        except ValidationError as exc:
            raise StoreError(f"The proposals of project '{project_id}' are damaged and cannot be read.") from exc
        if box.incarnation != incarnation:  # the project was deleted and created again: these belong to the old one
            return ProposalBox(incarnation=incarnation), revision
        return box, revision

    def _update(self, project_id: str, change: Callable[[ProposalBox], T]) -> T:
        """`change` on the project's box, written conditionally on the revision read (retried on a concurrent write); the
        write also drops the decided proposals past the retention."""
        for attempt in range(MUTATION_ATTEMPTS):
            box, revision = self._load(project_id)
            result = change(box)
            sweep(box, self._retention, datetime.now(UTC))
            body = box.model_dump(mode="json")
            try:
                if revision is None:
                    self._docs.create_document(DOCUMENT, project_id, body)
                else:
                    self._docs.commit_document(DOCUMENT, project_id, body, revision)
            except ConflictError:
                if attempt == MUTATION_ATTEMPTS - 1:
                    raise
                continue
            except StoreError:
                if revision is None and attempt < MUTATION_ATTEMPTS - 1:
                    continue  # created concurrently: read it and add to it
                raise
            return result
        raise ConflictError(f"The proposals of project '{project_id}' kept changing; try again.")


def _items(raw: Any) -> list[ProposalItem]:
    """The items in their stored shape, or ProposalError saying what is wrong with which item."""
    if not isinstance(raw, list) or not raw:
        raise ProposalError("A project proposal needs at least one item: a list of {key, op, arguments}.")
    if len(raw) > MAX_ITEMS:
        raise ProposalError(f"A project proposal has at most {MAX_ITEMS} items ({len(raw)} given); split it.")
    out: list[ProposalItem] = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ProposalError(f"Item {number} must be an object {{key, op, arguments}}.")
        key, op = item.get("key"), item.get("op")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ProposalError(f"Item {number} needs a key of 1 to 24 characters a-z, 0-9 and _ (got {key!r}).")
        if any(i.key == key for i in out):
            raise ProposalError(f"Item key '{key}' is used twice.")
        if op not in PROPOSABLE_OPERATIONS:
            raise ProposalError(
                f"Item '{key}': '{op}' cannot be proposed. Available: {', '.join(PROPOSABLE_OPERATIONS)}."
            )
        arguments = item.get("arguments", item.get("args", {}))
        if not isinstance(arguments, dict):
            raise ProposalError(f"The arguments of item '{key}' must be an object.")
        derived = item.get("derived_from", [])
        if not isinstance(derived, list) or not all(isinstance(r, str) for r in derived):
            raise ProposalError(f"derived_from of item '{key}' must be a list of requirement ids.")
        if "origin" in arguments:
            raise ProposalError(f"Item '{key}': origin is set by Arcviz when the item is applied.")
        refused = [f for f in AGENT_REFUSED_FIELDS.get(op, ()) if f in arguments]
        if refused:
            raise ProposalError(
                f"Item '{key}': a proposal cannot set {', '.join(refused)}; a decision is a draft until a person accepts it."
            )
        if op.startswith("update_") and "requirements" in arguments:
            raise ProposalError(
                f"Item '{key}': use link_requirements to add requirements; update_* would replace the existing trace."
            )
        title = arguments.get("title") or arguments.get("name") or arguments.get("target") or ""
        if op == "link_requirements":
            title = f"{arguments.get('target', '')} ← {', '.join(arguments.get('requirements') or [])}"
        out.append(
            ProposalItem(
                key=key,
                op=op,
                arguments=arguments,
                title=" ".join(single_line(str(title)).split())[:MAX_TITLE],
                derived_from=list(dict.fromkeys(derived)),
            )
        )
    return out


def _selection(proposal: StoredProposal, accept: Sequence[str] | None) -> set[str]:
    """The keys to apply: `accept`, or every pending item. ProposalError for an unknown or decided key, or when an item
    depends on one that is neither selected nor applied."""
    pending = {i.key for i in proposal.items if i.state == "pending"}
    wanted = set(pending) if accept is None else set(accept)
    unknown = sorted(wanted - pending)
    if unknown:
        raise ProposalError(f"No pending item {', '.join(repr(k) for k in unknown)} in proposal '{proposal.id}'.")
    if not wanted:
        raise ProposalError(f"Proposal '{proposal.id}' has no pending item to apply.")
    applied = {i.key for i in proposal.items if i.state == "applied"}
    for item in proposal.items:
        if item.key in wanted:
            missing = sorted(references(item.arguments) - wanted - applied)
            if missing:
                raise ProposalError(
                    f"Item '{item.key}' needs {', '.join(repr(k) for k in missing)}: apply it with it or before it."
                )
    return wanted


def _with_origin(project: Any, op: str, origin: str) -> str | None:
    """The id of the object an earlier application of the item created (its `origin`), if there is one."""
    items = {"add_driver": project.drivers, "add_scenario": project.scenarios, "add_decision": project.decisions}[op]
    return next((i.id for i in items if i.origin == origin), None)


def _settle_project(stored: StoredProposal, who: str) -> None:
    """After an apply or a discard: back to pending while items wait, else decided (and the arguments dropped, so a
    decided proposal stays small)."""
    if stored.pending_items():
        stored.status, stored.decided_by, stored.decided_at = "pending", None, None
        return
    states = {i.state for i in stored.items}
    stored.status = "applied" if "applied" in states else ("stale" if states & {"stale", "failed"} else "discarded")
    stored.decided_by, stored.decided_at = who, now_iso()
    for item in stored.items:
        item.arguments = {}


def _decidable(item: StoredProposal) -> None:
    """ProposalError unless the proposal can be applied or discarded now."""
    if item.status in ("applied", "discarded"):
        raise ProposalError(f"Proposal '{item.id}' is already {item.status}.")
    if item.status == "applying" and not _claim_expired(item):
        raise ProposalError(f"Proposal '{item.id}' is being applied right now; wait for the outcome.")


def _claim_expired(item: StoredProposal) -> bool:
    try:
        since = datetime.fromisoformat(item.decided_at) if item.decided_at else None
    except ValueError:
        since = None
    return since is None or (datetime.now(UTC) - since.astimezone(UTC)).total_seconds() > CLAIM_TTL_SECONDS


def _normalised(raw: Any) -> list[dict[str, Any]]:
    """The operations in their stored shape, or ProposalError saying what is wrong with which operation."""
    if not isinstance(raw, list) or not raw:
        raise ProposalError("A proposal needs at least one operation: a list of {op, arguments}.")
    if len(raw) > MAX_OPERATIONS:
        raise ProposalError(f"A proposal has at most {MAX_OPERATIONS} operations ({len(raw)} given); split it.")
    out: list[dict[str, Any]] = []
    for number, op in enumerate(raw, start=1):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str) or not op["op"]:
            raise ProposalError(f"Operation {number} needs an 'op' name: {{op, arguments}}.")
        arguments = op.get("arguments", op.get("args", {}))
        if not isinstance(arguments, dict):
            raise ProposalError(f"The arguments of operation {number} ({op['op']}) must be an object.")
        if op["op"] in operations.INTERNAL_OPERATIONS:
            raise ProposalError(
                f"A proposal cannot contain '{op['op']}': links to project elements are made by the project model."
            )
        out.append({"op": op["op"], "arguments": arguments})
    return out


# ---- replaying a confirmed proposal (also the chat's confirm route; arcviz/proposal.py re-exports these) -----------


def apply_proposal(
    diagrams: DiagramService,
    diagram_id: str,
    ops: list[dict[str, Any]],
    base: str,
    result: str,
    label: str | None = None,
    note: Callable[[list[dict[str, Any]]], bool] | None = None,
    models: ModelService | None = None,
) -> tuple[Diagram, VersionInfo, list[str]]:
    """Replay a proposal on the stored diagram and save it as one version.

    Raises `ProposalConflict` (and writes nothing) if the diagram changed since the proposal was made,
    an operation fails, or the replay does not reproduce the previewed result.
    Returns the new diagram, the version and the touched node ids.

    The base check and the write are atomic: the write is conditional on the revision the replay started
    from (#65). A concurrent write in between makes the replay run again on the new state, where the base
    check turns a real change into a `ProposalConflict`.

    `note` appends the outcome to a transcript and returns False when its owner is gone; default: the
    diagram's transcript (a project chat records it in the project's, spec project-chat.md).

    With `models` the structural views the proposal creates become project elements in the same version (spec:
    elements-by-default.md, ADR-0164), after the result was checked against the preview; links whose element was
    removed or re-kinded meanwhile are taken back (`ModelService.settle_adoption`).
    """
    details = "\n".join(f"- {op['op']} {json.dumps(op.get('arguments') or {}, ensure_ascii=False)}" for op in ops)

    def operations_of(diagram: Diagram) -> set[str]:
        if fingerprint(diagram) != base:
            raise ProposalConflict("The diagram changed since the proposal was made; ask again to get a new proposal.")
        touched: set[str] = set()
        for op in ops:
            name, args = op.get("op"), op.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(args, dict):
                raise ProposalConflict(f"Invalid operation in the proposal: {json.dumps(op, default=str)}")
            if name in operations.INTERNAL_OPERATIONS:  # links are made by the project model (ADR-0071)
                raise ProposalConflict(f"A proposal cannot contain '{name}'; link elements through the project model.")
            try:
                outcome = operations.apply(diagram, name, args)
            except operations.OperationError as exc:
                raise ProposalConflict(f"The proposal no longer applies: {exc}") from exc
            touched.update(outcome.get("touched", []))
        if fingerprint(diagram) != result:
            raise ProposalConflict("Replaying the proposal did not reproduce the preview; ask again.")
        return touched

    adopter = models.adopter(diagram_id) if models is not None else None
    stored = diagrams.get(diagram_id) if adopter is not None else None
    replayed, adopted = adopting(adopter, stored, operations_of, lambda _: False) if stored else (operations_of, None)

    def replay(diagram: Diagram) -> Change[set[str]]:
        return Change(diagram, replayed(diagram), label=label or "Proposal applied", details=details)

    try:
        updated = diagrams.update(diagram_id, replay)
    finally:
        adoption = adopted() if adopted is not None else None

    assert updated is not None and updated.version is not None
    diagram, version, touched = updated.diagram, updated.version, updated.result
    if adoption and models is not None and models.settle_adoption(diagram_id, adoption.to_dict()) != adoption.to_dict():
        diagram = diagrams.get(diagram_id)  # links were taken back: the stored state
    _note(
        diagrams,
        diagram_id,
        note,
        [
            {
                "role": "assistant",
                "content": APPLIED_NOTE.format(version=version.version),
                "proposal_outcome": "applied",
                "version": version.version,
            }
        ],
    )
    return diagram, version, sorted(t for t in touched if diagram.has_element(t))


def discard_proposal(
    diagrams: DiagramService, diagram_id: str, note: Callable[[list[dict[str, Any]]], bool] | None = None
) -> None:
    """Record that the user discarded the pending proposal (the diagram is untouched)."""
    diagrams.get(diagram_id)  # 404 for unknown diagrams
    entry = {"role": "assistant", "content": DISCARDED_NOTE, "proposal_outcome": "discarded"}
    _note(diagrams, diagram_id, note, [entry])


def _note(
    diagrams: DiagramService,
    diagram_id: str,
    note: Callable[[list[dict[str, Any]]], bool] | None,
    entries: list[dict[str, Any]],
) -> None:
    """Append the outcome to the transcript; StoreError when its owner was deleted meanwhile."""
    written = note(entries) if note is not None else diagrams.append_chat(diagram_id, entries)
    if not written:
        raise StoreError(f"Diagram '{diagram_id}' not found." if note is None else "The transcript's owner is gone.")
