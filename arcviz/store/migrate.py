"""Copy all data from one repository to another (the storage part of `arcviz-migrate`, see `arcviz/cli.py`)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import DOCUMENT_KINDS, DiagramRepository, StoreError


@dataclass
class MigrationReport:
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    projects_copied: list[str] = field(default_factory=list)
    projects_skipped: list[str] = field(default_factory=list)
    documents_copied: list[str] = field(default_factory=list)  # "<kind>:<key>" of workspaces and users
    documents_skipped: list[str] = field(default_factory=list)


def migrate(
    source: DiagramRepository,
    target: DiagramRepository,
    diagram_ids: list[str] | None = None,
    overwrite: bool = False,
) -> MigrationReport:
    """Copies latest, every version (original numbers and timestamps) and the chat (with seq), and every
    project document (spec: docs/architecture/specs/projects.md).

    Existing diagrams and projects on the target are skipped unless `overwrite` is set, in which case
    they are replaced. A diagram that has versions but no latest on the target (an interrupted run) is an error
    without `overwrite` and replaced with it: two histories are never mixed. Versions are stored under their own numbers (`put_version`), so numbers stay identical, also
    across the gaps a retention prune leaves.
    Projects are copied verbatim (with new revisions), also when `diagram_ids` selects diagrams; so are the keyed
    documents (workspaces and the user directory, spec: docs/architecture/specs/workspaces-and-sharing.md).
    """
    report = MigrationReport()
    ids = diagram_ids or [d.id for d in source.list_diagrams()]
    for diagram_id in ids:
        if target.exists(diagram_id):
            if not overwrite:
                report.skipped.append(diagram_id)
                continue
            target.delete(diagram_id)
        leftovers = target.list_versions(diagram_id)
        if leftovers:  # versions without a latest: an interrupted earlier run; never mix two histories
            if not overwrite:
                raise StoreError(
                    f"Diagram '{diagram_id}' has versions but no current state on the target (an interrupted "
                    "migration?); run again with --overwrite to replace them."
                )
            target.delete_versions(diagram_id, [v.version for v in leftovers])
        for info in source.list_versions(diagram_id):
            _, snapshot = source.load_version(diagram_id, info.version)
            # under its own number: a history pruned by the retention policy has gaps (ADR-0130), and
            # `restored_from` must keep naming the same versions
            target.put_version(diagram_id, snapshot, info)
        target.write_latest(source.load(diagram_id))
        chat = source.load_chat(diagram_id)
        if chat:
            target.import_chat(diagram_id, chat)
        report.copied.append(diagram_id)
    for project, _ in source.list_projects():
        try:
            _, revision = target.read_project(project.id)
        except StoreError:
            revision = None
        if revision is not None:
            if not overwrite:
                report.projects_skipped.append(project.id)
                continue
            target.delete_project(project.id, revision)
        target.create_project(project)
        report.projects_copied.append(project.id)
    for kind in DOCUMENT_KINDS:
        for key, body, _ in source.list_documents(kind):
            try:
                _, revision = target.read_document(kind, key)
            except StoreError:
                revision = None
            if revision is not None:
                if not overwrite:
                    report.documents_skipped.append(f"{kind}:{key}")
                    continue
                target.delete_document(kind, key, revision)
            target.create_document(kind, key, body)
            report.documents_copied.append(f"{kind}:{key}")
    return report
