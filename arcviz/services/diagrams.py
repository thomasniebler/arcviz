"""DiagramService: diagrams, their versions and their transcripts for the entry points.

Spec: docs/architecture/specs/application-services.md. Every write is conditional on the revision it
read (#65, ADR-0044); conflicts are retried here, never in the callers. With a `Guard` every method checks the
caller's role on the diagram's project first (spec: workspaces-and-sharing.md, ADR-0084).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from .. import operations, ports
from ..live import EventSink
from ..model import RELATION_DIAGRAM_TYPES, Diagram, single_line, slugify
from ..ports import (
    DEFAULT_VERSIONS_PAGE,
    MUTATION_ATTEMPTS,
    Change,
    ConflictError,
    DiagramInfo,
    DiagramRepositoryPort,
    Expected,
    Revision,
    RevisionMismatch,
    StoreError,
    Updated,
    VersionInfo,
    check_id,
    page_of,
)
from ..project_model import (
    Element,
    Relation,
    fits,
    linkable_kinds,
    model_refs,
    referenced_model,
    view_connections,
    view_elements,
)
from ..projects import CarriedModel, Project
from ..workspaces import Identity
from .access import DiagramNotFound, Forbidden, Guard, ProjectNotFound, authored_entries
from .projects import ProjectService

T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass
class DiagramState:
    """Everything the UI shows of one diagram besides its rendering."""

    diagram: Diagram
    versions: list[VersionInfo]  # the newest page (ascending; spec: hardening.md, #114)
    undo_target: int | None
    redo_target: int | None
    chat: list[dict[str, Any]]
    revision: Revision | None = None  # of `diagram`: the ETag (spec: collaboration.md)
    versions_total: int = 0  # every stored version
    versions_more: bool = False  # older versions exist beyond the page


@dataclass
class LiveWrite(Generic[T]):
    """Outcome of `write_through`: `written`; `rejected` (the change refused, nothing written); `deleted`
    (the diagram vanished, or the caller cannot see it any more); `forbidden` (the caller's role dropped below editor
    during the turn, nothing written); `busy` (it kept changing). `diagram`/`revision` are the state to continue on."""

    outcome: Literal["written", "rejected", "deleted", "forbidden", "busy"]
    diagram: Diagram
    revision: Revision
    result: T | None = None


class DiagramService:
    def __init__(
        self,
        repository: DiagramRepositoryPort,
        projects: ProjectService | None = None,
        guard: Guard | None = None,
        events: EventSink | None = None,
    ):
        self._repo = repository
        self._projects = projects  # places new diagrams and unplaces deleted ones (spec: projects.md)
        self.guard = guard  # None: the system context
        self.events = events  # live updates (spec: collaboration.md, ADR-0095); None: nobody listens
        self._written: dict[str, Revision] = {}  # diagram id -> the revision write_through last wrote (this turn)
        if guard is not None and (projects is None or projects.guard is not guard):
            raise ValueError("A guarded DiagramService needs the ProjectService bound to the same guard.")

    @property
    def _author(self) -> Identity | None:
        """Who the versions and transcript entries written here are recorded for (ADR-0086); None: system."""
        return self.guard.actor if self.guard is not None else None

    def _publish(self, event: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(event)

    def _changed(self, diagram_id: str, origin: str = "user") -> None:
        """Announce a write of the diagram's latest state to the live streams (a hint: they re-read it)."""
        author = self._author
        self._publish(
            {
                "type": "changed",
                "diagram": diagram_id,
                "author": author.id if author is not None else None,
                "author_name": author.name if author is not None else None,
                "origin": origin,
            }
        )

    def _update(
        self, diagram_id: str, change: Callable[[Diagram], Change[T] | None], expected: Expected | None = None
    ) -> Updated[T] | None:
        """The store's read-modify-write, with the caller as the author of the version it records. With `expected`
        (HTTP `If-Match`) one attempt on that revision, else RevisionMismatch (ADR-0094)."""

        def authored(diagram: Diagram) -> Change[T] | None:
            outcome = change(diagram)
            if outcome is not None and outcome.author is None:
                outcome.author = self._author
            return outcome

        updated = self._repo.update(diagram_id, authored, expected=expected)
        if updated is not None:
            self._changed(diagram_id)
        return updated

    def require(self, diagram_id: str, need: str = "viewer") -> None:
        """DiagramNotFound when the caller may not see the diagram (or it does not exist in a project), Forbidden
        below `need`; nothing in the system context."""
        if self.guard is not None:
            self.guard.diagram(diagram_id, need)

    def _holder_of(self, diagram_id: str, need: str) -> Project | None:
        """The diagram's project (as just read) after checking `need` (the guard's answer; located in the system
        context)."""
        if self.guard is not None:
            return self.guard.diagram(diagram_id, need)
        located = self._projects.holder(diagram_id) if self._projects is not None else None
        return located.project if located is not None else None

    def access(self, diagram_id: str) -> tuple[str | None, str | None, str | None]:
        """(the caller's role, the project, the workspace) of a diagram the caller may see; the role is None in the
        system context."""
        if self.guard is None:
            located = self._projects.locate(diagram_id) if self._projects is not None else None
            if located is None:
                return None, None, None
            return None, located[0], self._projects.get(located[0]).workspace if self._projects else None
        project = self.guard.diagram(diagram_id)
        return self.guard.role_of(project), project.id, project.workspace

    # ---- storage ---------------------------------------------------------------

    @property
    def storage_name(self) -> str:
        return self._repo.name

    def git_status(self) -> dict[str, Any]:
        return self._repo.git_status()

    def start_mirror(self) -> None:
        self._repo.start_mirror()

    def drain_mirror(self, timeout: float | None = None) -> bool:
        return self._repo.drain_mirror(timeout)

    def ping(self) -> None:
        self._repo.ping()

    # ---- queries ---------------------------------------------------------------

    def list_diagrams(self) -> list[DiagramInfo]:
        """Every diagram the caller may see."""
        if self.guard is None:
            return self._repo.list_diagrams()
        assert self._projects is not None
        # the diagrams of the caller's listed projects, described one by one: never a listing of the store (#181)
        return self._repo.diagram_infos(sorted(self._projects.visible_diagrams()))

    def exists(self, diagram_id: str) -> bool:
        """Whether the diagram exists (and the caller may see it)."""
        if self.guard is not None:
            try:
                self.guard.diagram(diagram_id)
            except DiagramNotFound:
                return False
        return self._repo.exists(diagram_id)

    def get(self, diagram_id: str) -> Diagram:
        """The current state; StoreError if the diagram does not exist (or the caller may not see it)."""
        self.require(diagram_id)
        return self._repo.load(diagram_id)

    def read(self, diagram_id: str) -> tuple[Diagram, Revision]:
        """The current state and the opaque revision a conditional write of a change to it must expect."""
        self.require(diagram_id)
        return self._repo.load_with_revision(diagram_id)

    def state(self, diagram_id: str, page: int = DEFAULT_VERSIONS_PAGE) -> DiagramState:
        """The diagram with one listing of its version metadata (no snapshot is read, #114): the newest `page`
        versions and the undo/redo targets of the whole history."""
        self.require(diagram_id)
        diagram, revision = self._repo.load_with_revision(diagram_id)
        history = self._repo.history(diagram_id)
        shown = page_of(history.versions, page)
        return DiagramState(
            diagram=diagram,
            versions=shown,
            undo_target=history.undo_target,
            redo_target=history.redo_target,
            chat=self._repo.load_chat(diagram_id),
            revision=revision,
            versions_total=len(history.versions),
            versions_more=len(shown) < len(history.versions),
        )

    def versions(self, diagram_id: str, before: int | None = None, limit: int | None = None) -> list[VersionInfo]:
        """Every version, or with `before`/`limit` the newest `limit` versions older than `before` (ascending)."""
        self.require(diagram_id)
        versions = self._repo.list_versions(diagram_id)
        if before is None and limit is None:
            return versions
        return page_of(versions, limit if limit is not None else DEFAULT_VERSIONS_PAGE, before)

    def version(self, diagram_id: str, version: int) -> tuple[VersionInfo, Diagram]:
        self.require(diagram_id)
        return self._repo.load_kept_version(diagram_id, version)  # a pruned one is VersionGone (409)

    # ---- diagram lifecycle -------------------------------------------------------

    def create(
        self, title: str, diagram_id: str, diagram: Diagram, project: str | None = None, section: str | None = None
    ) -> Diagram:
        """Store a new diagram (initial version) under a new id `<diagram_id>-<random suffix>` (ADR-0131): the
        requested id is only its prefix, so the answer is the same whether or not anybody holds `diagram_id`
        (#167); StoreError if `diagram_id` is invalid. The returned diagram carries the id.

        With a project service the target project (default: the default project) and section are checked
        first (StoreError / ProjectError, nothing created) and the diagram is placed there afterwards."""
        if not single_line(title).strip():  # like rename_diagram (#103); imports name an untitled diagram instead
            raise operations.OperationError("The diagram title must not be empty.")
        target = self._projects.check_target(project, section) if self._projects is not None else None
        return self._create_free(title, check_id(diagram_id), diagram, target)

    def _create_free(
        self,
        title: str,
        base: str,
        diagram: Diagram,
        target: tuple[str, str | None] | None,
        incarnation: str | None = None,
    ) -> Diagram:
        """Create under a new id `<base>-<random suffix>` (`ports.id_attempts`, ADR-0131) and place it. An id is free when
        no diagram has it and no placement record claims it (a create in flight, ADR-0088); a taken one (practically
        never) or one whose leftovers cannot be cleared now moves on to a fresh suffix, so the answer never depends on
        other tenants' ids (#167)."""
        title = single_line(title).strip()  # a title is one line (#103); `create` refuses an empty one, imports name it
        for candidate in ports.id_attempts(base, "diagram"):
            record = self._intend(candidate, target) if not self._repo.exists(candidate) else None
            if record is not None:
                try:
                    if self._projects is not None:  # the id is ours (no diagram, placement claimed): clear leftovers
                        self._projects.vacate(candidate)
                except StoreError:  # a leftover membership could not be cleared now: the id counts as taken
                    self._settle(candidate, record)
                    _blocked(candidate)
                    continue
                except BaseException:
                    self._settle(candidate, record)  # given up: the id is not held by a placement record any more
                    raise
                try:
                    created = self._repo.create(title, candidate, diagram, author=self._author)
                except StoreError:
                    self._settle(candidate, record)
                    if not self._repo.exists(candidate):
                        raise  # not a lost race for the id (an invalid id, a failed write)
                else:
                    self._place(created.id, target, record, incarnation)
                    return created
        raise AssertionError("unreachable")  # pragma: no cover

    def _intend(self, diagram_id: str, target: tuple[str, str | None] | None) -> str | None:
        """Record the target project before the create (ADR-0088): its revision, "" when there is nothing to record,
        None when the id is claimed by another create."""
        if self._projects is None or target is None:
            return ""
        return self._projects.intend(diagram_id, target[0])

    def _settle(self, diagram_id: str, record: str) -> None:
        if record and self._projects is not None:
            self._projects.settle(diagram_id, record)

    def _place(
        self, diagram_id: str, target: tuple[str, str | None] | None, record: str, incarnation: str | None = None
    ) -> None:
        """Place a just-created diagram, then drop its placement record. If placing fails (the project or section
        vanished meanwhile, conflicts), the diagram is deleted again so a failed request leaves nothing behind; should
        that fail too, the record stays and the repair puts the diagram into the project it was created for."""
        if self._projects is None or target is None:
            return
        try:
            self._projects.place(diagram_id, *target, new=True, incarnation=incarnation)
        except BaseException:
            # Through the repository: the diagram is in no project yet, so no caller could pass the delete check.
            try:
                self._repo.delete(diagram_id)
            except Exception:
                logger.warning("New diagram '%s' could not be placed nor removed; left to the repair", diagram_id)
            else:
                self._settle(diagram_id, record)
                try:  # a membership placed before the failure must not wait for the repair (a re-create would show)
                    self._projects.withdraw(diagram_id, target[0])
                except Exception:
                    logger.warning("Membership of unplaced diagram '%s' left for the repair", diagram_id, exc_info=True)
            raise
        self._settle(diagram_id, record)

    def import_new(
        self,
        diagram: Diagram,
        title: str,
        diagram_id: str | None = None,
        project: str | None = None,
        section: str | None = None,
        keep_refs: bool = False,
        incarnation: str | None = None,
    ) -> str:
        """Create an imported diagram under a new id prefixed by `diagram_id` (StoreError if invalid) or else by the
        document's id, like `create` (ADR-0131). Placed like `create`.

        Needs editor on the target project. Links to project elements are removed unless `keep_refs` (a project
        bundle brings its elements along):
        element ids are only unique within a project, so a ref from elsewhere could name an unrelated element
        here (spec: shared-project-model.md, #153).

        With `incarnation` (`projects.incarnation_of` of the target project, as a project turn read it) the diagram is placed only in
        that incarnation of the project; one deleted and created again meanwhile is ProjectNotFound (ADR-0127)."""
        target = self._projects.check_target(project, section, incarnation) if self._projects is not None else None
        if untitled(title):  # the caller's title wins; else the document's; else UNTITLED (#103 review)
            title = diagram.title if not untitled(diagram.title) else UNTITLED
        if not keep_refs:
            _unlink_all(diagram)
        if diagram_id:
            base = check_id(diagram_id)
        else:
            # the generated suffix goes first (ports.id_base, #200), then the cut: a 61-64 character id keeps its slug
            named = any(c.isalnum() for c in diagram.id)
            base = ports.id_base(slugify(diagram.id))[:60].strip("-") if named else "diagram"
        return self._create_free(title, base, diagram, target, incarnation).id

    def import_carrying(
        self,
        diagram: Diagram,
        model: tuple[list[Element], list[Relation], list[str]] | None,
        title: str,
        diagram_id: str | None = None,
        project: str | None = None,
        section: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """`import_new` of a single diagram document that may carry its model (`exchange.parse_document_model`, #153,
        ADR-0161): the target project gets the referenced elements and relations first (`ProjectService.carry`, the
        planner of `move`), then the diagram is created linked to them, then its links are checked against the target
        once more (a copy removed meanwhile is unlinked). If the create fails, the copies are removed again. Without a
        model its links are dropped (ADR-0079) and reported as such. Returns the new id and the report (None when the diagram links nothing)."""
        element_ids, relation_ids = model_refs(diagram)
        if not (element_ids or relation_ids) or self._projects is None:
            return self.import_new(diagram, title, diagram_id, project, section), None
        target, _ = self._projects.check_target(project, section)  # before anything is written
        if model is None:
            carried = CarriedModel(
                dropped_elements=[(e, NO_MODEL) for e in sorted(element_ids)],
                dropped_relations=[(r, NO_MODEL) for r in sorted(relation_ids)],
            )
            return self.import_new(diagram, title, diagram_id, project, section), carried.report()
        source = Project(id="import", title="import", elements=model[0], relations=model[1])
        carried = self._projects.carry(target, source, element_ids, relation_ids)
        relink = _Relink()
        relink.plan(diagram, carried)
        relink.unlink(diagram)
        relink.link(diagram, self._projects.get(target), carried)
        try:
            created = self.import_new(diagram, title, diagram_id, project, section, keep_refs=True)
        except BaseException:
            self._uncarry(target, carried)  # nothing was created: the copies go again (#153 review)
            raise
        projects = self._projects
        self._update(created, _checked(relink, lambda: projects.fresh(target), carried))  # a copy removed meanwhile?
        return created, carried.report()

    def model_for_export(
        self, diagram_id: str, diagram: Diagram, version: bool = False
    ) -> tuple[list[Element], list[Relation]] | None:
        """What a single-diagram export of `diagram` carries of its project's model (`project_model.referenced_model`,
        #153); None outside a project. Needs viewer, like the export. For a `version` (written perhaps while the diagram
        was in another project) only elements whose current name and kind still match every view of it in that version
        come along, with the relations between them (#153 review): the refs of the others stay in the document and an
        import reports them as dropped, so a view is never paired with an element it may not have meant."""
        self.require(diagram_id)
        holder = self._projects.holder(diagram_id) if self._projects is not None else None
        if holder is None:
            return None
        elements, relations = holder.project.elements, holder.project.relations
        if version:
            diagram = _matching_refs(diagram, elements, relations)
        return referenced_model(diagram, elements, relations)

    def move(
        self, diagram_id: str, project_id: str, section: str | None = None, position: int | None = None
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        """Place the diagram in `project_id` (within its project, or into another one); returns (project, section,
        report). A move into another project carries the linked elements and relations along (#153, ADR-0161): the
        target project gets them first (`ProjectService.carry`: mapped, copied, or dropped when they do not fit), then
        one version unlinks every view whose element or relation has another id (or none) in the target, then the
        membership moves (ADR-0052), then one version links those views to their new ids and drops every link whose
        element or relation is gone from the target by then (another editor removed a copy; read after the membership,
        so a later removal reaches the diagram through the model service). If the unlink version or the membership
        fails, the views get their links back and the copies are removed again (`ProjectService.uncarry`); nothing is
        lost. Every state in between links nothing wrongly (ADR-0079): a crash leaves views unlinked at worst. The
        report (`CarriedModel.report`) says what was copied, mapped and dropped; None within the project.
        StoreError/ProjectError for an unknown target, nothing changed.

        Within its project an editor may move it; taking it out of its project needs the owner of that project (it
        removes the diagram from everyone who sees only the source, like a deletion; ADR-0085), and editor on the
        target."""
        assert self._projects is not None
        self.require(diagram_id, "editor")
        target, section_id = self._projects.check_target(project_id, section)
        source = self._projects.holder(diagram_id)
        if source is None or source.project.id == target:
            placed = self._projects.place(diagram_id, target, section_id, position, source=source)
            self._publish({"type": "access", "diagram": diagram_id})
            return (*placed, None)
        self.require(diagram_id, "owner")
        element_ids, relation_ids = model_refs(self._repo.load(diagram_id))
        carried = (
            self._projects.carry(target, source.project, element_ids, relation_ids)
            if element_ids or relation_ids
            else CarriedModel()
        )
        relink = _Relink()

        def adjust(diagram: Diagram) -> Change[None] | None:
            relink.plan(diagram, carried)
            if not relink.unlink(diagram):
                return None
            return Change(diagram, None, label="Verknüpfungen angepasst (anderes Projekt)")

        adjusted = False
        try:
            adjusted = self._update(diagram_id, adjust) is not None
            placed = self._projects.place(diagram_id, target, section_id, position, source=source)
        except Exception as exc:
            # An orphan copy is harmless, a lost or wrong link is not (#153 review): look at the target first.
            try:  # a fresh read: the request's cached copy predates the write that may have landed
                written = self._projects.fresh(target).member(diagram_id)
            except (ProjectNotFound, Forbidden):  # a definite answer: the target is gone for us, nothing moved
                written = None
            except Exception:  # cannot tell whether it moved: leave the views unlinked and the copies in place
                raise exc from None
            if written is None:  # nothing moved: the views get their links back, untouched copies go again
                if adjusted:
                    self._restore_links(diagram_id, relink, source.project.id)
                self._uncarry(target, carried)
                raise
            # the membership is written (a later step of `place` failed): the move is done. Finish it against the
            # target, then report the failure; the repair (reconcile) drops a leftover membership in the source.
            logger.warning("A move failed after its membership was written (%s); finished", type(exc).__name__)
            self._publish({"type": "access", "diagram": diagram_id})
            projects = self._projects
            self._update(diagram_id, _checked(relink, lambda: projects.fresh(target), carried))
            try:  # the source membership goes too, so the model service of the source no longer touches the diagram
                projects.withdraw(diagram_id, source.project.id, source.member.added_at)
            except Exception:
                logger.warning("The source membership of a finished move is left to the repair", exc_info=True)
            raise
        self._publish({"type": "access", "diagram": diagram_id})  # its streams re-check where it is now
        # read after the membership: a copy removed before it is caught here, one removed later by the model service
        projects = self._projects
        self._update(diagram_id, _checked(relink, lambda: projects.fresh(target), carried))
        return (*placed, carried.report())

    def _restore_links(self, diagram_id: str, relink: _Relink, source_id: str) -> None:
        """Best effort after a failed move: link the views and connections again as before the move, against the source
        project as read now (only to elements and relations that still exist and fit; the others stay unlinked). A
        failure is logged; the diagram then has unlinked views at worst (ADR-0079)."""
        assert self._projects is not None
        try:
            source = self._projects.fresh(source_id)  # as it is now, not as this request read it before the move
        except Exception:
            logger.warning("The source of a failed move could not be read; its views stay unlinked", exc_info=True)
            return

        def restore(diagram: Diagram) -> Change[None] | None:
            if not relink.restore(diagram, source):
                return None
            return Change(diagram, None, label="Verknüpfungen wiederhergestellt (Verschieben fehlgeschlagen)")

        try:
            self._update(diagram_id, restore)
        except Exception:
            logger.warning("The links of a diagram whose move failed could not be restored", exc_info=True)

    def _uncarry(self, project_id: str, carried: CarriedModel) -> None:
        if not (carried.copied_elements or carried.copied_relations) or self._projects is None:
            return
        try:
            left = self._projects.uncarry(project_id, carried)
        except Exception:
            logger.warning("The copies of a failed move or import could not be removed", exc_info=True)
            return
        if left:  # changed or used meanwhile: harmless where they are, never removed blindly
            logger.warning("%d copies of a failed move or import were left in the target project", len(left))

    def delete(self, diagram_id: str, expected: Expected | None = None) -> None:
        """Delete the diagram (project owner, ADR-0085), then every membership of it (the projects its locator entry
        lists, ADR-0120). Once the diagram is gone the request has succeeded: a failing membership cleanup is logged and
        left to the project repair, which drops dangling memberships; until then a create of the same id clears them
        first (`ProjectService.vacate`). With `expected` (HTTP `If-Match`, #175) one conditional attempt: RevisionMismatch
        when the latest revision is another one or changes before the delete."""
        holder = self._holder_of(diagram_id, "owner")
        # every membership of the id, read before the delete: removed after it (a create of the same id later is safe)
        observed = self._projects.memberships(diagram_id, holder) if self._projects is not None else []
        if expected is None:
            self._repo.delete(diagram_id)
        else:
            accepted = (expected,) if isinstance(expected, str) else expected
            current = self._repo.read_revision(diagram_id)
            if current not in accepted:
                raise RevisionMismatch(
                    f"Diagram '{diagram_id}' has changed since revision {', '.join(accepted)}.", current
                )
            try:
                self._repo.delete(diagram_id, expected=current)
            except ConflictError:
                raise RevisionMismatch(
                    f"Diagram '{diagram_id}' was changed by another request while it was being deleted."
                ) from None
        self._publish({"type": "access", "diagram": diagram_id})  # its streams end with `gone`
        if self._projects is not None:
            try:
                self._projects.unplace(diagram_id, observed)
            except Exception:
                logger.warning("Membership of deleted diagram '%s' left for the repair", diagram_id, exc_info=True)

    # ---- mutations -----------------------------------------------------------------

    def update(
        self, diagram_id: str, change: Callable[[Diagram], Change[T] | None], expected: Expected | None = None
    ) -> Updated[T] | None:
        """Read-modify-write: `change` runs on a fresh copy, again after every conflict (store `update`); with
        `expected` once, on that revision (RevisionMismatch otherwise)."""
        self.require(diagram_id, "editor")
        return self._update(diagram_id, change, expected)

    def apply_operation(
        self, diagram_id: str, op: str, args: dict[str, Any], label: str, expected: Expected | None = None
    ) -> Updated[dict[str, Any]]:
        """One operation through `operations.apply`, saved as a version; re-applied on the newer state when
        another request wrote in between, unless `expected` names the revision it must apply to (HTTP `If-Match`:
        RevisionMismatch when latest is another one). OperationError for invalid input (nothing written)."""

        self.require(diagram_id, "editor")

        def change(diagram: Diagram) -> Change[dict[str, Any]]:
            return Change(diagram, operations.apply(diagram, op, args), label=label)

        updated = self._update(diagram_id, change, expected)
        assert updated is not None and updated.version is not None
        return updated

    def apply_if_changed(
        self, diagram_id: str, steps: list[tuple[str, dict[str, Any]]], label: str
    ) -> Updated[list[dict[str, Any]]] | None:
        """Operations applied to the diagram as one versioned write, like `apply_operation`; nothing is written
        (None) when they leave the content as it was: the model service's propagation and repair (spec:
        shared-project-model.md). All or nothing: `operations.apply` restores the diagram, the write is skipped."""
        self.require(diagram_id, "editor")

        def change(diagram: Diagram) -> Change[list[dict[str, Any]]] | None:
            before = diagram.model_dump(mode="json", exclude={"updated_at"})
            results = [operations.apply(diagram, op, args) for op, args in steps]
            if diagram.model_dump(mode="json", exclude={"updated_at"}) == before:
                return None
            return Change(diagram, results, label=label)

        return self._update(diagram_id, change)

    def snapshot(self, diagram_id: str, label: str, expected: Expected | None = None) -> VersionInfo:
        self.require(diagram_id, "editor")
        version = self._repo.snapshot_latest(diagram_id, label, author=self._author, expected=expected)
        self._changed(diagram_id)
        return version

    def restore(self, diagram_id: str, version: int, expected: Expected | None = None) -> VersionInfo:
        self.require(diagram_id, "editor")
        info = self._repo.restore(diagram_id, version, author=self._author, expected=expected)[1]
        self._changed(diagram_id)
        return info

    def undo(self, diagram_id: str, expected: Expected | None = None) -> VersionInfo | None:
        self.require(diagram_id, "editor")
        outcome = self._repo.undo(diagram_id, author=self._author, expected=expected)
        if outcome:
            self._changed(diagram_id)
        return outcome[1] if outcome else None

    def redo(self, diagram_id: str, expected: Expected | None = None) -> VersionInfo | None:
        self.require(diagram_id, "editor")
        outcome = self._repo.redo(diagram_id, author=self._author, expected=expected)
        if outcome:
            self._changed(diagram_id)
        return outcome[1] if outcome else None

    def write_through(
        self,
        diagram_id: str,
        diagram: Diagram,
        revision: Revision,
        change: Callable[[Diagram], T],
        failed: Callable[[T], bool],
    ) -> LiveWrite[T]:
        """Apply `change` to `diagram` (read at `revision`) and write it as latest without a version.

        On a conflict latest is re-loaded (it holds the earlier writes and the concurrent change) and
        `change` runs again on it, up to MUTATION_ATTEMPTS times. When `failed(result)` is true the change
        refused itself: nothing is written and the state it ran on is returned.
        """
        try:  # a diagram deleted, moved away or a role lowered mid-turn is an outcome for the tool, not an exception
            self.require(diagram_id, "editor")
        except DiagramNotFound:
            return LiveWrite("deleted", diagram, revision)
        except Forbidden:
            return LiveWrite("forbidden", diagram, revision)
        target, target_revision = diagram, revision
        for _ in range(MUTATION_ATTEMPTS):
            result = change(target)
            if failed(result):
                return LiveWrite("rejected", target, target_revision, result)
            try:
                new_revision = self._repo.save_latest(target, expected=target_revision)
            except ConflictError:
                try:
                    target, target_revision = self._repo.load_with_revision(diagram_id)
                except StoreError:
                    return LiveWrite("deleted", target, target_revision, result)
                continue
            self._written[diagram_id] = new_revision
            self._changed(diagram_id, "assistant")
            return LiveWrite("written", target, new_revision, result)
        return LiveWrite("busy", target, target_revision)

    def commit_turn(self, diagram: Diagram, revision: Revision, label: str, details: str) -> VersionInfo | None:
        """Record `diagram` as latest and as an automatic version, conditional on `revision`; None when
        latest moved on meanwhile (the newer state already holds the change) or the diagram is gone or out of the
        caller's sight. Never raises for access (it runs in the agents' `finally`); a real store failure is logged and
        raised as StoreError, which the agents report.

        A caller whose role dropped below editor during the turn still gets the version of exactly the state this
        service wrote in the turn (`revision` is the one `write_through` last wrote): it records changes made while
        they were allowed and adds no content (spec: workspaces-and-sharing.md)."""
        try:
            self.require(diagram.id, "editor")
        except DiagramNotFound:
            return None
        except Forbidden:
            if self._written.get(diagram.id) != revision:
                return None
        try:
            _, version = self._repo.commit(
                diagram, revision, label=label, source="auto", details=details, author=self._author
            )
        except ConflictError:  # latest moved on: the newer state already holds the change
            return None
        except StoreError:
            if not self._repo.exists(diagram.id):
                return None  # deleted meanwhile
            logger.exception("The version of a chat turn on '%s' could not be written", diagram.id)
            raise  # a real store failure: the agent reports it in `final.error`
        self._changed(diagram.id, "assistant")
        return version

    # ---- chat transcript -----------------------------------------------------------------

    def chat(self, diagram_id: str) -> list[dict[str, Any]]:
        self.require(diagram_id)
        return self._repo.load_chat(diagram_id)

    def append_chat(self, diagram_id: str, entries: list[dict[str, Any]], generation: str | None = None) -> bool:
        """Append transcript entries (editor); False when the diagram no longer exists (nothing is recreated) or, with
        `generation`, when the transcript has another one (cleared, imported, deleted and created again; ADR-0127)."""
        try:
            self.require(diagram_id, "editor")
        except DiagramNotFound:
            return False
        try:
            self._repo.append_chat(diagram_id, authored_entries(entries, self._author), generation)
        except ConflictError:
            if generation is None:
                raise
            return False
        except StoreError:
            if self._repo.exists(diagram_id):
                raise
            return False
        self._publish({"type": "chat", "diagram": diagram_id})
        return True

    def chat_generation(self, diagram_id: str) -> str:
        try:
            self.require(diagram_id)
        except DiagramNotFound:
            return ""  # like an unknown diagram
        return self._repo.chat_generation(diagram_id)

    def compact_chat(self, diagram_id: str, upto_seq: int, summary: dict[str, Any], generation: str) -> bool:
        self.require(diagram_id, "editor")
        compacted = self._repo.compact_chat(diagram_id, upto_seq, summary, generation)
        if compacted:
            self._publish({"type": "chat", "diagram": diagram_id})
        return compacted

    def clear_chat(self, diagram_id: str) -> None:
        """Clear the transcript of the diagram incarnation the access check approved (ADR-0127): the generation is
        read between two checks, and the clear is conditional on it."""
        for _ in range(MUTATION_ATTEMPTS):
            self.require(diagram_id, "editor")
            generation = self._repo.chat_generation(diagram_id)
            self.require(diagram_id, "editor")
            try:
                self._repo.clear_chat(diagram_id, generation)
            except ConflictError:
                continue  # cleared, imported or re-created meanwhile: check again
            self._publish({"type": "chat", "diagram": diagram_id})
            return
        raise ConflictError(f"The transcript of diagram '{diagram_id}' kept changing; try again.")


_BLOCKED_LOGGED: set[str] = set()  # ids whose leftover memberships blocked a create (logged once per process)


def _blocked(diagram_id: str) -> None:
    if diagram_id not in _BLOCKED_LOGGED:
        _BLOCKED_LOGGED.add(diagram_id)
        logger.warning(
            "Diagram id '%s' is held by a leftover membership that could not be cleared now; creates take the next "
            "free id (the start-up repair or `arcviz-migrate --projects` clears it)",
            diagram_id,
            exc_info=True,
        )


NO_MODEL = "the document carries no model"
UNTITLED = "Unbenanntes Diagramm"  # the title of an imported diagram that has none (#103 review)


def untitled(title: str) -> bool:
    """Whether `title` is empty once one line (#103): a create refuses it, an import names the diagram UNTITLED."""
    return not single_line(title).strip()


def _matching_refs(diagram: Diagram, elements: list[Element], relations: list[Relation]) -> Diagram:
    """A copy of `diagram` (a version) keeping only the links that still match the project's model: a view whose name
    or kind does not fit its element's current one is unlinked, and so is every connection or hidden marker of a
    relation that touches such an element (through the operations, on the copy; nothing is stored)."""
    copy = diagram.model_copy(deep=True)
    by_id = {e.id: e for e in elements}
    stale: set[str] = set()
    for view in view_elements(copy):
        element = by_id.get(view.ref) if view.ref is not None else None
        if view.ref is not None and (
            element is None
            or single_line(view.name).strip().casefold() != single_line(element.name).strip().casefold()
            or element.kind not in linkable_kinds(copy.type, view.kind)
        ):
            stale.add(view.ref)
    if not stale:
        return copy
    for view in view_elements(copy):
        if view.ref in stale:
            operations.apply(copy, "unlink_element", {"id": view.id})
    if copy.type in RELATION_DIAGRAM_TYPES:
        touching = {r.id for r in relations if r.source in stale or r.target in stale}
        for connection in view_connections(copy):
            if connection.ref in touching:
                operations.apply(copy, "unlink_relation", {"id": connection.id})
        for relation in [r for r in copy.hidden_relations if r in touching]:
            operations.apply(copy, "set_relation_hidden", {"relation": relation, "hidden": False})
    return copy


class _Relink:
    """The diagram side of carrying a model into another project (#153): which links are dropped before the membership
    moves (their target id differs, or nothing came along) and which are made again afterwards, by view id."""

    def __init__(self) -> None:
        self.kept_elements: set[str] = set()  # ids that name the same element in both projects
        self.kept_relations: set[str] = set()
        self.views: dict[str, str] = {}  # view -> element id in the target
        self.connections: dict[str, str] = {}  # connection -> relation id in the target
        self.hidden: list[str] = []  # relation ids in the target to hide again
        self.renamed: list[str] = []  # kept ids whose views may show another name than the target's element

    def plan(self, diagram: Diagram, carried: CarriedModel) -> None:
        self.original_views = {v.id: v.ref for v in view_elements(diagram) if v.ref is not None}  # for `restore`
        self.original_connections = {c.id: c.ref for c in view_connections(diagram) if c.ref is not None}
        self.original_hidden = list(diagram.hidden_relations)
        self.kept_elements = {s for s, t in carried.elements.items() if s == t}
        self.kept_relations = {s for s, t in carried.relations.items() if s == t}
        self.views = {
            v.id: carried.elements[v.ref]
            for v in view_elements(diagram)
            if v.ref is not None and v.ref in carried.elements and v.ref not in self.kept_elements
        }
        self.connections, self.hidden = {}, []
        if diagram.type in RELATION_DIAGRAM_TYPES:
            # every carried connection: one that keeps its id loses its link too when an end view is unlinked
            self.connections = {
                c.id: carried.relations[c.ref]
                for c in view_connections(diagram)
                if c.ref is not None and c.ref in carried.relations
            }
            self.hidden = [
                carried.relations[r]
                for r in diagram.hidden_relations
                if r in carried.relations and r not in self.kept_relations
            ]
        self.renamed = sorted(self.kept_elements)  # mapped, or copied shortened (#212): the views take the name

    def unlink(self, diagram: Diagram) -> bool:
        """Drop every link whose id does not name the same thing in the target; True if anything changed."""
        changed = False
        for view in view_elements(diagram):
            if view.ref is not None and view.ref not in self.kept_elements:
                operations.apply(diagram, "unlink_element", {"id": view.id})
                changed = True
        if diagram.type in RELATION_DIAGRAM_TYPES:
            for connection in view_connections(diagram):  # unlinking an end may have cleared some already
                if connection.ref is not None and connection.ref not in self.kept_relations:
                    operations.apply(diagram, "unlink_relation", {"id": connection.id})
                    changed = True
            for relation in list(diagram.hidden_relations):
                if relation not in self.kept_relations:
                    operations.apply(diagram, "set_relation_hidden", {"relation": relation, "hidden": False})
                    changed = True
        return changed

    def pending(self) -> bool:
        """Whether `link` may have anything to do; it writes nothing when every link survived."""
        return bool(self.views or self.connections or self.hidden or self.renamed)

    def link(self, diagram: Diagram, project: Project, carried: CarriedModel) -> bool:
        """Link the views again to their new ids in `project` (as read after the move), one operation each; one that
        is refused (the element or relation was removed or changed meanwhile) is reported as dropped. Then every link
        that kept its id is checked against `project` too: one whose element or relation is gone is unlinked and
        reported, so no later element with that id can re-attach to it (#153 review). True if anything changed."""
        changed = self._drop_vanished(diagram, project, carried)
        elements = {e.id: e for e in project.elements}
        element_source = {t: s for s, t in carried.elements.items()}
        for view_id, element_id in self.views.items():
            element = elements.get(element_id)
            args = {"id": view_id, "element": element_id, "name": element.name if element else "-", "element_kind": ""}
            if element is not None:
                args["element_kind"] = element.kind
            if element is not None and _applied(diagram, "link_element", args):
                changed = True
            else:
                carried.drop_element(element_source.get(element_id, element_id), "changed in the target meanwhile")
        for element_id in self.renamed:  # the views show the target element's name (spelling, shortening)
            element = elements.get(element_id)
            if element is not None:
                touched = operations.apply(diagram, "sync_element", {"element": element_id, "name": element.name})
                changed = changed or bool(touched["touched"])
        relations = {r.id: r for r in project.relations}
        relation_source = {t: s for s, t in carried.relations.items()}
        current = {c.id: c.ref for c in view_connections(diagram)}
        for connection_id, relation_id in self.connections.items():
            if current.get(connection_id) == relation_id:
                continue  # kept its link
            relation = relations.get(relation_id)
            if relation is not None and _applied(
                diagram,
                "link_relation",
                {
                    "id": connection_id,
                    "relation": relation_id,
                    "kind": relation.kind,
                    "source": relation.source,
                    "target": relation.target,
                },
            ):
                changed = True
            else:
                carried.drop_relation(relation_source.get(relation_id, relation_id), "changed in the target meanwhile")
        for relation_id in self.hidden:
            if relation_id in relations:
                operations.apply(diagram, "set_relation_hidden", {"relation": relation_id, "hidden": True})
                changed = True
        return changed

    def _drop_vanished(self, diagram: Diagram, project: Project, carried: CarriedModel) -> bool:
        """Unlink what names an element or relation `project` does not have (any more), or one it cannot stand for:
        an element of a kind the view cannot show (changed meanwhile), a relation the connection does not fit (an old
        version's edge, a relation changed meanwhile). Kept ids are checked like relinked ones (#153, Codex review).
        True if anything changed."""
        elements = {e.id: e for e in project.elements}
        relations = {r.id: r for r in project.relations}
        element_source = {t: s for s, t in carried.elements.items()}
        relation_source = {t: s for s, t in carried.relations.items()}
        changed = False
        for view in view_elements(diagram):
            if view.ref is None:
                continue
            element = elements.get(view.ref)
            if element is None or element.kind not in linkable_kinds(diagram.type, view.kind):
                ref = view.ref
                operations.apply(diagram, "unlink_element", {"id": view.id})
                carried.drop_element(element_source.get(ref, ref), "removed or changed in the target meanwhile")
                changed = True
        if diagram.type in RELATION_DIAGRAM_TYPES:
            for connection in view_connections(diagram):
                if connection.ref is None:
                    continue
                relation = relations.get(connection.ref)
                if relation is None or not fits(diagram.type, connection, relation):
                    ref = connection.ref
                    operations.apply(diagram, "unlink_relation", {"id": connection.id})
                    carried.drop_relation(relation_source.get(ref, ref), "removed from the target or does not fit")
                    changed = True
            for relation_id in list(diagram.hidden_relations):
                if relation_id not in relations:
                    operations.apply(diagram, "set_relation_hidden", {"relation": relation_id, "hidden": False})
                    carried.drop_relation(relation_source.get(relation_id, relation_id), "removed from the target")
                    changed = True
        return changed

    def restore(self, diagram: Diagram, source: Project) -> bool:
        """The links of the plan's original refs again (a move that failed after `unlink`), against the `source` project
        they never left; one that no longer fits stays unlinked. True if anything changed."""
        elements = {e.id: e for e in source.elements}
        relations = {r.id: r for r in source.relations}
        current = {v.id: v.ref for v in view_elements(diagram)}
        changed = False
        for view_id, ref in self.original_views.items():
            element = elements.get(ref)
            if element is not None and view_id in current and current[view_id] != ref:
                args = {"id": view_id, "element": ref, "name": element.name, "element_kind": element.kind}
                changed = _applied(diagram, "link_element", args) or changed
        connections = {c.id: c.ref for c in view_connections(diagram)}
        for connection_id, ref in self.original_connections.items():
            relation = relations.get(ref)
            if relation is not None and connection_id in connections and connections[connection_id] != ref:
                args = {
                    "id": connection_id,
                    "relation": ref,
                    "kind": relation.kind,
                    "source": relation.source,
                    "target": relation.target,
                }
                changed = _applied(diagram, "link_relation", args) or changed
        for relation_id in self.original_hidden:
            if relation_id in relations and relation_id not in diagram.hidden_relations:
                changed = _applied(diagram, "set_relation_hidden", {"relation": relation_id, "hidden": True}) or changed
        return changed


def _checked(
    relink: _Relink, target: Callable[[], Project], carried: CarriedModel
) -> Callable[[Diagram], Change[None] | None]:
    """The change of the last step of a move or import: relink against the target as read now, and drop the links
    whose element or relation is gone; a version only when the diagram changed."""

    def change(diagram: Diagram) -> Change[None] | None:
        before = diagram.model_dump(mode="json")
        relink.link(diagram, target(), carried)
        if diagram.model_dump(mode="json") == before:
            return None
        return Change(diagram, None, label="Verknüpfungen übernommen (anderes Projekt)")

    return change


def _applied(diagram: Diagram, op: str, args: dict[str, Any]) -> bool:
    try:
        operations.apply(diagram, op, args)
    except operations.OperationError:
        return False
    return True


def _unlink_all(diagram: Diagram) -> bool:
    """Unlink every node or lifeline that stands for a project element (through the operations); True if any."""
    linked = [v.id for v in view_elements(diagram) if v.ref is not None]
    for view in linked:
        operations.apply(diagram, "unlink_element", {"id": view})
    if diagram.type not in RELATION_DIAGRAM_TYPES:  # no relation refs to drop (ADR-0139)
        return bool(linked)
    connections = [c.id for c in view_connections(diagram) if c.ref is not None]  # relations (ADR-0139)
    for connection in connections:
        operations.apply(diagram, "unlink_relation", {"id": connection})
    hidden = list(diagram.hidden_relations)  # relation ids are only unique within a project, too
    for relation in hidden:
        operations.apply(diagram, "set_relation_hidden", {"relation": relation, "hidden": False})
    return bool(linked or connections or hidden)
