"""RequirementService: the requirements of a project (spec: docs/architecture/specs/requirements.md, ADR-0188..0190).

One keyed document `requirements` per project holds them, written conditionally on its revision like every keyed
document, so a bulk import or an agent writing never contends on the project revision. References to the project's
stakeholders and elements are checked on write and tolerated when dangling on read; the cascades of a removed stakeholder
or element, and of a removed requirement, are second, ordered writes that `sync` repairs. Access is the project's: viewer
reads, editor writes; what an agent may do is `requirement_model.check_agent` (ADR-0186).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import ValidationError

from .. import requirement_model as rqm
from ..ports import MUTATION_ATTEMPTS, ConflictError, DocumentRepositoryPort, StoreError, is_definitely_missing
from ..projects import Project, incarnation_of
from ..requirement_model import DOCUMENT, Known, Meta, Requirement, RequirementBox, RequirementError
from ..workspaces import Identity
from .projects import ProjectService

T = TypeVar("T")
logger = logging.getLogger(__name__)

MAX_IMPORT_BATCH = 500
DEFAULT_PAGE = 50
MAX_PAGE = 200


class RequirementService:
    def __init__(self, documents: DocumentRepositoryPort, projects: ProjectService, actor: Identity | None = None):
        self._docs = documents
        self._projects = projects
        self._actor = actor
        projects.attach_requirements(self)

    # ---- what the project service needs (ADR-0189) ---------------------------------------------------------

    def ids(self, project_id: str) -> set[str]:
        """The ids of the requirements that exist (no access check: the caller of the project service has one)."""
        return {r.id for r in self._load(project_id)[0].requirements}

    def removed(self, project_id: str, stakeholder: str | None = None, element: str | None = None) -> None:
        """A stakeholder or element was removed from the project: drop it from the requirements."""
        self._update(project_id, lambda box: rqm.drop_ids(box, stakeholder=stakeholder, element=element), quiet=True)

    # ---- reads (viewer) --------------------------------------------------------------------------------------

    def page(
        self,
        project_id: str,
        status: str | None = None,
        kind: str | None = None,
        theme: str | None = None,
        stakeholder: str | None = None,
        coverage: str | None = None,
        step: str | None = None,
        q: str | None = None,
        suggested: bool | None = None,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """A page of the requirements, filtered, in id order, with the digest of the whole box and the number of dangling
        references. `coverage` is a level or `none` (not refined); `step` one of `requirement_model.STEPS`."""
        self._projects.require(project_id, "viewer")
        if not 1 <= limit <= MAX_PAGE or offset < 0:
            raise RequirementError(f"limit is 1 to {MAX_PAGE} and offset at least 0.")
        for value, allowed, what in (
            (status, rqm.REQUIREMENT_STATUSES, "status"),
            (kind, rqm.REQUIREMENT_KINDS, "kind"),
            (step, rqm.STEPS, "step"),
            (coverage, (*rqm.COVERAGE_LEVELS, "none"), "coverage"),
        ):
            if value is not None and value not in allowed:
                raise RequirementError(f"Unknown {what} '{value}'. Available: {', '.join(allowed)}.")
        box, project, known, cover = self._context(project_id)
        wanted = (q or "").strip().casefold()
        rows = [
            r
            for r in sorted(box.requirements, key=lambda r: rqm.number_of(r.id) or 0)
            if (status is None or r.status == status)
            and (kind is None or r.kind == kind)
            and (theme is None or r.theme.casefold() == theme.casefold())
            and (stakeholder is None or stakeholder in r.stakeholders)
            and (coverage is None or (cover.get(r.id) or "none") == coverage)
            and (step is None or rqm.step_of(r, cover.get(r.id)) == step)
            and (suggested is None or (r.suggestion is not None) == suggested)
            and (not wanted or wanted in f"{r.raw} {r.statement} {r.external_id} {r.id}".casefold())
        ]
        return {
            "requirements": [self._view(r, known, cover) for r in rows[offset : offset + limit]],
            "total": len(rows),
            "digest": rqm.digest(box, cover),
            "dangling": rqm.dangling(box, known),
        }

    def get(self, project_id: str, ref: str) -> dict[str, Any]:
        """One requirement with its inverse trace (the drivers, scenarios and decisions that name it)."""
        self._projects.require(project_id, "viewer")
        box, project, known, cover = self._context(project_id)
        found = rqm.find_requirement(box, ref)
        if found is None:
            raise self._unknown(box, project_id, ref)
        view = self._view(found, known, cover)
        trace = rqm.traces(project).get(found.id, rqm.Trace())
        view["trace"] = {"drivers": trace.drivers, "scenarios": trace.scenarios, "decisions": trace.decisions}
        return view

    def traceability(self, project_id: str) -> dict[str, Any]:
        """The matrix: every refined requirement with the objects that name it, and those objects' titles."""
        self._projects.require(project_id, "viewer")
        box, project, _, cover = self._context(project_id)
        traced = rqm.traces(project)
        rows = [
            {
                "id": r.id,
                "statement": r.statement or r.raw,
                "coverage": cover.get(r.id),
                "drivers": traced.get(r.id, rqm.Trace()).drivers,
                "scenarios": traced.get(r.id, rqm.Trace()).scenarios,
                "decisions": traced.get(r.id, rqm.Trace()).decisions,
            }
            for r in box.requirements
            if r.status == "refined"
        ]
        objects = {
            "drivers": {d.id: d.title for d in project.drivers},
            "scenarios": {s.id: s.title for s in project.scenarios},
            "decisions": {d.id: d.title for d in project.decisions},
        }
        return {"rows": rows, "objects": objects}

    def box(self, project_id: str) -> RequirementBox:
        """The whole box (viewer): for the project review and the architecture context, which read it once per request."""
        self._projects.require(project_id, "viewer")
        return self._load(project_id)[0]

    # ---- writes (editor) -------------------------------------------------------------------------------------

    def apply(
        self, project_id: str, op: str, args: dict[str, Any], agent: bool = False, client: str = "ui"
    ) -> dict[str, Any]:
        """One operation. `agent`: the caller is an agent (MCP credential or chat assistant): only what
        `requirement_model.check_agent` allows. RequirementError for invalid input; nothing is written then."""
        self._projects.require(project_id, "editor")
        if agent:
            rqm.check_agent(op, args if isinstance(args, dict) else {})
        known = self._known(self._projects.get(project_id))
        meta = self._meta(client)
        return self._update(project_id, lambda box: rqm.apply(box, op, args, known=known, meta=meta))

    def import_items(
        self,
        project_id: str,
        items: list[dict[str, Any]],
        source: str = "",
        client: str = "import",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add requirements in one write. Idempotent: an item whose `(source, external_id)` or normalised text exists
        (or came earlier in the batch) is skipped. A rejected item (too long, unknown priority, …) is reported with its row
        and does not stop the others. `{added: [ids], skipped_duplicates: [{row, reason}], rejected: [{row, reason}]}`.
        `dry_run`: the same answer for what would happen (`added` are then the ids it would get), nothing is written."""
        self._projects.require(project_id, "editor")
        if len(items) > MAX_IMPORT_BATCH:
            raise RequirementError(f"At most {MAX_IMPORT_BATCH} requirements per import ({len(items)} given).")
        known = self._known(self._projects.get(project_id))
        meta = self._meta(client)

        def add(box: RequirementBox) -> dict[str, Any]:
            seen_text = {rqm.normalised_raw(r.raw) for r in box.requirements}
            seen_external = {(r.source, r.external_id) for r in box.requirements if r.external_id}
            steps: list[tuple[str, dict[str, Any]]] = []
            rows: list[int] = []
            skipped: list[dict[str, Any]] = []
            for row, item in enumerate(items, start=1):
                args = {k: v for k, v in item.items() if v not in (None, "", [])}
                if source:
                    args.setdefault("source", source)
                raw_text = str(args.get("raw") or "")
                key = (str(args.get("source") or ""), str(args.get("external_id") or ""))
                if key[1] and key in seen_external:
                    skipped.append({"row": row, "reason": f"external id '{key[1]}' exists already"})
                    continue
                if raw_text and rqm.normalised_raw(raw_text) in seen_text:
                    skipped.append({"row": row, "reason": "the same text exists already"})
                    continue
                seen_text.add(rqm.normalised_raw(raw_text))
                if key[1]:
                    seen_external.add(key)
                steps.append(("add_requirement", args))
                rows.append(row)
            outcomes = rqm.apply_each(box, steps, known=known, meta=meta)
            added = [o.result.id for o in outcomes if o.ok]
            rejected = [{"row": row, "reason": o.error} for row, o in zip(rows, outcomes, strict=True) if not o.ok]
            return {"added": added, "skipped_duplicates": skipped, "rejected": rejected}

        if dry_run:
            return add(self._load(project_id)[0])
        return self._update(project_id, add)

    def suggest_many(
        self, project_id: str, suggestions: list[dict[str, Any]], client: str = "ui"
    ) -> dict[str, list[Any]]:
        """Store suggestions (`{requirement, ...fields}` each) in one write: `{stored: [ids], rejected: [{requirement,
        reason}]}`. A suggestion replaces an unaccepted one; the fields in force are never touched (ADR-0190)."""
        self._projects.require(project_id, "editor")
        known = self._known(self._projects.get(project_id))
        meta = self._meta(client)

        def store(box: RequirementBox) -> dict[str, list[Any]]:
            steps = [
                ("set_suggestion", {"requirement": s.get("requirement", ""), "suggestion": _fields(s)})
                for s in suggestions
            ]
            outcomes = rqm.apply_each(box, steps, known=known, meta=meta)
            stored = [o.result.id for o in outcomes if o.ok]
            rejected = [
                {"requirement": str(s.get("requirement", "")), "reason": o.error}
                for s, o in zip(suggestions, outcomes, strict=True)
                if not o.ok
            ]
            return {"stored": stored, "rejected": rejected}

        return self._update(project_id, store)

    def accept(self, project_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Accept suggestions in bulk (`{id, expected, fields?}` each; `expected` is the `rev` the user saw): `{accepted,
        stale, skipped}`. A suggestion replaced meanwhile is `stale` and not accepted; on a conditional-write retry the
        check runs again against the state read then (ADR-0190)."""
        self._projects.require(project_id, "editor")

        def accept(box: RequirementBox) -> dict[str, Any]:
            steps = [
                (
                    "accept_suggestion",
                    {
                        "requirement": str(i.get("id", "")),
                        "expected": str(i.get("expected", "")),
                        **({"fields": i["fields"]} if i.get("fields") is not None else {}),
                    },
                )
                for i in items
            ]
            outcomes = rqm.apply_each(box, steps)
            out: dict[str, list[Any]] = {"accepted": [], "stale": [], "skipped": []}
            for item, outcome in zip(items, outcomes, strict=True):
                rid = str(item.get("id", ""))
                if outcome.ok:
                    out["accepted"].append(rid)
                elif outcome.stale:
                    out["stale"].append(rid)
                else:
                    out["skipped"].append({"id": rid, "reason": outcome.error})
            return out

        return self._update(project_id, accept)

    def set_status_many(
        self, project_id: str, ids: list[str], status: str, duplicate_of: str | None = None, reason: str = ""
    ) -> dict[str, Any]:
        """Set a status on several requirements in one write: `{changed: [ids], skipped: [{id, reason}]}`."""
        self._projects.require(project_id, "editor")

        def change(box: RequirementBox) -> dict[str, Any]:
            args: dict[str, Any] = {"status": status, "reason": reason}
            if duplicate_of:
                args["duplicate_of"] = duplicate_of
            outcomes = rqm.apply_each(box, [("set_status", {"requirement": i, **args}) for i in ids])
            return {
                "changed": [i for i, o in zip(ids, outcomes, strict=True) if o.ok],
                "skipped": [{"id": i, "reason": o.error} for i, o in zip(ids, outcomes, strict=True) if not o.ok],
            }

        return self._update(project_id, change)

    def remove(self, project_id: str, ref: str) -> dict[str, Any]:
        """Remove a requirement (a person's act: agents never delete). The requirement document is written first, then the
        references are stripped from the project in one conditional write; a failure between the two leaves ids that reads
        ignore and `sync` removes (ADR-0189)."""
        self._projects.require(project_id, "editor")
        out = self._update(project_id, lambda box: rqm.apply(box, "remove_requirement", {"requirement": ref}))
        removed_id = out["result"]["requirement"]["id"]
        try:
            self._projects.strip_requirement_refs(project_id, {removed_id})
        except Exception as exc:
            logger.warning(
                "References to a removed requirement of project '%s' not cleaned: %s", project_id, type(exc).__name__
            )
        return out

    def remove_many(self, project_id: str, ids: list[str]) -> dict[str, Any]:
        """Remove several requirements in one write (a person's act, editor): `{removed: [ids], skipped: [{id, reason}]}`.
        The references are stripped from the project once, like `remove` (ADR-0198)."""
        self._projects.require(project_id, "editor")
        wanted = list(dict.fromkeys(ids))

        def change(box: RequirementBox) -> dict[str, Any]:
            outcomes = rqm.apply_each(box, [("remove_requirement", {"requirement": i}) for i in wanted])
            return {
                "removed": [i for i, o in zip(wanted, outcomes, strict=True) if o.ok],
                "skipped": [{"id": i, "reason": o.error} for i, o in zip(wanted, outcomes, strict=True) if not o.ok],
            }

        out = self._update(project_id, change)
        if out["removed"]:
            try:
                self._projects.strip_requirement_refs(project_id, set(out["removed"]))
            except Exception as exc:
                logger.warning(
                    "References to removed requirements of project '%s' not cleaned: %s",
                    project_id,
                    type(exc).__name__,
                )
        return out

    def restore(self, project_id: str, box: RequirementBox) -> int:
        """Store a parsed bundle's requirements as the project's first (a bundle import into a new project; editor). The
        box was validated by `exchange.parse_project`; this only writes it, under the project's incarnation."""
        self._projects.require(project_id, "editor")
        incarnation = incarnation_of(self._projects.get(project_id))

        def put(current: RequirementBox) -> int:
            if current.requirements:
                raise RequirementError(f"Project '{project_id}' has requirements already.")
            current.requirements = [r.model_copy(deep=True) for r in box.requirements]
            current.seq = box.seq
            current.incarnation = incarnation
            return len(current.requirements)

        return self._update(project_id, put)

    def sync(self, project_id: str) -> dict[str, int]:
        """Drop dangling references on both sides (editor): in the requirements (removed stakeholders and elements) and in
        the project (removed requirements). One write each, only when something changed."""
        self._projects.require(project_id, "editor")
        project = self._projects.get(project_id)
        known = self._known(project)
        counts = {"requirements": 0, "project": 0}

        def drop(box: RequirementBox) -> bool:
            counts["requirements"] = sum(rqm.dangling(box, known).values())
            return rqm.drop_dangling(box, known)

        self._update(project_id, drop, quiet=True)
        existing = self.ids(project_id)
        carriers: list[Any] = [*project.drivers, *project.scenarios, *project.decisions]
        stale = {rid for item in carriers for rid in item.requirements if rid not in existing}
        counts["project"] = len(stale)
        if stale:
            self._projects.strip_requirement_refs(project_id, stale)
        return counts

    # ---- internals -------------------------------------------------------------------------------------------

    def _context(self, project_id: str) -> tuple[RequirementBox, Project, Known, dict[str, str | None]]:
        project = self._projects.get(project_id)
        box, _ = self._load(project_id, project)
        return box, project, self._known(project), rqm.coverage_map(box, project)

    @staticmethod
    def _known(project: Project) -> Known:
        return Known(
            stakeholders=frozenset(s.id for s in project.stakeholders),
            elements=frozenset(e.id for e in project.elements),
        )

    def _meta(self, client: str) -> Meta:
        actor = self._actor
        return Meta(actor.id if actor else "", actor.name if actor else "", client)

    @staticmethod
    def _view(r: Requirement, known: Known, cover: dict[str, str | None]) -> dict[str, Any]:
        """The requirement as the API shows it: references to what no longer exists are left out (ADR-0188)."""
        view = r.model_dump(mode="json")
        view["stakeholders"] = [s for s in r.stakeholders if s in known.stakeholders]
        view["elements"] = [e for e in r.elements if e in known.elements]
        if view.get("suggestion"):
            view["suggestion"]["stakeholders"] = [s for s in r.suggestion.stakeholders if s in known.stakeholders]  # type: ignore[union-attr]
        view["coverage"] = cover.get(r.id)
        view["step"] = rqm.step_of(r, cover.get(r.id))
        return view

    @staticmethod
    def _unknown(box: RequirementBox, project_id: str, ref: str) -> RequirementError:
        known = ", ".join(r.id for r in box.requirements[:40]) or "none"
        return RequirementError(f"Unknown requirement '{ref}' in project '{project_id}'. Requirements: {known}.")

    def _load(self, project_id: str, project: Project | None = None) -> tuple[RequirementBox, str | None]:
        """The project's box and its revision (None while the document does not exist). An unreadable document is an
        error, never an empty box: writing over it would lose the requirements."""
        incarnation = incarnation_of(project or self._projects.get(project_id))
        try:
            body, revision = self._docs.read_document(DOCUMENT, project_id)
        except ConflictError:
            raise
        except StoreError as exc:
            if is_definitely_missing(exc):
                return RequirementBox(incarnation=incarnation), None
            raise
        try:
            box = RequirementBox.model_validate(body)
        except ValidationError as exc:
            raise StoreError(f"The requirements of project '{project_id}' are damaged and cannot be read.") from exc
        if box.incarnation != incarnation:  # the project was deleted and created again: these belong to the old one
            return RequirementBox(incarnation=incarnation), revision
        return box, revision

    def _update(self, project_id: str, change: Callable[[RequirementBox], T], quiet: bool = False) -> T:
        """`change` on the project's box, written conditionally on the revision read (again on a concurrent write); a box
        that does not change is not written. `quiet`: no live hint (the cascades)."""
        for attempt in range(MUTATION_ATTEMPTS):
            box, revision = self._load(project_id)
            before = box.model_dump(mode="json")
            result = change(box)
            body = box.model_dump(mode="json")
            if body == before:
                return result
            size = len(json.dumps(body, ensure_ascii=False))
            if size > rqm.MAX_REQUIREMENTS_BYTES:
                raise RequirementError(
                    f"The requirements would be too large ({size // 1024} KB, at most {rqm.MAX_REQUIREMENTS_BYTES // 1024} KB)."
                )
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
            if not quiet:
                self._projects.notify(project_id)
            return result
        raise ConflictError(f"The requirements of project '{project_id}' kept changing; try again.")


def _fields(suggestion: dict[str, Any]) -> dict[str, Any]:
    """The suggestion's own fields: everything but the requirement it is for."""
    return {k: v for k, v in suggestion.items() if k != "requirement"}
