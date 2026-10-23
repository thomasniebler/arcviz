"""Command-line entry points other than the server (`arcviz-migrate`).

A composition root like `server.py`: it builds repositories with the store factory and hands them to the
storage-level copy (`store.migrate`) and to the application services (the project repair, spec
docs/architecture/specs/projects.md, ADR-0053; the migration to personal workspaces and the recovery of stranded
projects, spec docs/architecture/specs/personal-workspaces.md, ADR-0102). Everything here runs in the system context.
"""

from __future__ import annotations

import argparse
import sys

from .envconfig import ConfigError
from .ports import RepositoryPort, StoreError
from .projects import ProjectError
from .services import (
    DEFAULT_PROJECT_ID,
    PersonalMigration,
    ProjectService,
    ReapReport,
    ReconcileReport,
    RequirementService,
    WorkspaceError,
    WriterConfig,
    migrate_to_personal_workspaces,
    reassign_workspace,
    settle_pending,
)
from .store import store_from_spec
from .store.migrate import migrate
from .workspaces import Identity


def _print_repair(report: ReconcileReport) -> None:
    for diagram_id in report.adopted:
        print(f"adopted {diagram_id} -> {report.into.get(diagram_id, DEFAULT_PROJECT_ID)}")
    for diagram_id in report.quarantined:
        print(
            f"quarantined {diagram_id}: the project it was created for is gone, so it is in no project and nobody "
            f"sees it; place it with --adopt {diagram_id}=PROJECT"
        )
    for diagram_id in report.dropped:
        print(f"dropped membership of deleted diagram {diagram_id}")
    for diagram_id in report.deduplicated:
        print(f"deduplicated {diagram_id} (kept its newest placement)")
    if report.located:
        print(f"diagram locator entries repaired: {report.located}")
    for diagram_id in report.skipped:
        print(f"skipped {diagram_id} for now (a store error, logged); the next run retries it")
    if not (report.changed or report.located or report.skipped):
        print("projects: nothing to repair")


def _print_workspaces(report: PersonalMigration) -> None:
    for user in report.created:
        print(f"created the personal workspace of {user}")
    for project_id, user in sorted(report.moved.items()):
        print(f"moved project {project_id} -> the personal workspace of {user}")
    for project_id, (owner, former) in sorted(report.reowned.items()):
        print(
            f"note: project {project_id} moved to its explicit owner {owner}; the former workspace owner {former} "
            "keeps it as an editor (no longer its owner)"
        )
    for old, new in sorted(report.renamed.items()):
        print(f"renamed project {old} -> {new} (its id is reserved for personal projects)")
    for project_id, error in sorted(report.failed.items()):
        print(f"error: project {project_id} could not be moved ({error}); run again to retry it")
    if report.indexed:
        print(f"listing entries repaired: {report.indexed}")
    if report.pruned:
        print(f"stale listing entries removed: {report.pruned}")
    if report.moved:
        print(f"shares written: {report.shares}")
    _print_repair(report.repair)
    if report.waiting_for_admin:
        print(
            f"note: {report.hidden} project(s) from before workspaces have no owner yet and stay hidden: run again "
            "with --admin USER (or start the server with ARCVIZ_ADMIN_USER)"
        )
    for workspace_id, project_ids in sorted(report.stranded.items()):
        print(
            f"note: workspace {workspace_id} does not exist, but project(s) {', '.join(project_ids)} name it, so nobody "
            f"sees them; use --reassign {workspace_id}=USER to move them into the personal workspace of USER"
        )
    if report.repair_error is not None:
        print(f"note: the project repair failed ({report.repair_error}); run --projects STORE to repeat it")
    if not (report.changed or report.waiting_for_admin or report.stranded or report.repair_error or report.failed):
        print("workspaces: nothing to migrate")


def _print_settled(report: ReapReport, everything: bool) -> None:
    if report.refused:
        print(
            f"refused: the heartbeat of writer(s) {', '.join(report.refused)} moved while watching, so a replica is "
            "running; stop every replica and run again (nothing was settled)"
        )
        return
    for boot in report.stopped:
        print(f"writer {boot} stopped cleanly: its tokens are settled")
    for boot in report.dead:
        print(f"writer {boot} stood still for the whole wait: declared dead, its tokens are settled")
    for boot in report.alive:
        print(f"writer {boot} is alive (or was not watched for a whole lease): its tokens stay")
    if report.legacy:
        what = "settled" if everything else "left (use --all in a maintenance window)"
        print(f"{report.legacy} token(s) from before M24 or of an unknown writer: {what}")
    print(f"settled {report.settled} pending token(s)")


def _sync_requirements(store: RepositoryPort) -> int:
    """Drop the dangling references between requirements and the project, in every project (system context)."""
    requirements = RequirementService(store, ProjectService(store, store))
    repaired = 0
    for project, _ in store.list_projects():
        counts = requirements.sync(project.id)
        if counts["requirements"] or counts["project"]:
            repaired += 1
            print(
                f"project {project.id}: {counts['requirements']} dangling reference(s) in the requirements, "
                f"{counts['project']} in the project removed"
            )
    print(f"requirements: {repaired} project(s) repaired" if repaired else "requirements: nothing to repair")
    return 0


def migrate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arcviz-migrate",
        description="Copy all data between stores (--from/--to), put every diagram without a project into "
        "the default project (--projects), or move v0.1.8 and M20 data into personal workspaces (--workspaces).",
    )
    parser.add_argument("--from", dest="source", help="source store spec, e.g. file:data")
    parser.add_argument("--to", dest="target", help="target store spec, e.g. sqlite:data")
    parser.add_argument("--diagram", action="append", help="only this diagram id (repeatable)")
    parser.add_argument("--overwrite", action="store_true", help="replace diagrams and projects on the target")
    parser.add_argument("--projects", metavar="STORE", help="repair project membership in this store only")
    parser.add_argument(
        "--adopt",
        metavar="DIAGRAM=PROJECT",
        action="append",
        help="with --projects: put a diagram that is in no project (quarantined) into PROJECT (repeatable)",
    )
    parser.add_argument(
        "--requirements",
        metavar="STORE",
        help="repair the references between requirements and the project's stakeholders, elements, drivers, scenarios "
        "and decisions in every project of this store (spec: requirements.md)",
    )
    parser.add_argument(
        "--workspaces",
        metavar="STORE",
        help="move projects of M20 workspaces (and without one) into personal workspaces, keeping every access as "
        "a share (and repair the membership)",
    )
    parser.add_argument(
        "--admin", metavar="USER", help="with --workspaces: the owner of the data from before workspaces"
    )
    parser.add_argument(
        "--reassign",
        metavar="MISSING=USER",
        action="append",
        help="with --workspaces: move the projects of the workspace MISSING, which does not exist, into the personal "
        "workspace of USER (without shares)",
    )
    parser.add_argument(
        "--settle-pending",
        metavar="STORE",
        help="clear the pending locator tokens of writers that provably stopped or died (#189): watches every writer "
        "heartbeat for --wait seconds first",
    )
    parser.add_argument(
        "--wait",
        type=float,
        metavar="SECONDS",
        help="with --settle-pending: how long to watch the heartbeats (default and minimum: one lease, 10 x "
        "ARCVIZ_WRITER_HEARTBEAT_SECONDS; with --all at least two heartbeats)",
    )
    parser.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help="with --settle-pending, in a maintenance window with every replica stopped: also clear tokens from before "
        "M24 and of unknown writers; refuses if any heartbeat moves while watching",
    )
    args = parser.parse_args(argv)
    modes = [
        bool(args.projects),
        bool(args.workspaces),
        bool(args.source or args.target),
        bool(args.settle_pending),
        bool(args.requirements),
    ]
    if sum(modes) > 1:
        parser.error("use one of --from/--to, --projects, --workspaces, --requirements or --settle-pending")
    if not any(modes[:2]) and not args.settle_pending and not args.requirements and not (args.source and args.target):
        parser.error("use --from and --to, --projects, --workspaces, --requirements or --settle-pending")
    if (args.wait is not None or args.everything) and not args.settle_pending:
        parser.error("--wait and --all need --settle-pending")
    if (args.admin or args.reassign) and not args.workspaces:
        parser.error("--admin and --reassign need --workspaces")
    reassignments = [(m.strip(), sep, t.strip()) for m, sep, t in (pair.partition("=") for pair in args.reassign or [])]
    for missing, sep, target in reassignments:
        if not sep or not missing or not target:
            parser.error(f"--reassign needs MISSING=USER, got {missing + sep + target!r}")
    if args.adopt and not args.projects:
        parser.error("--adopt needs --projects")
    adoptions = [pair.partition("=") for pair in args.adopt or []]
    for diagram_id, sep, project_id in adoptions:
        if not sep or not diagram_id or not project_id:
            parser.error(f"--adopt needs DIAGRAM=PROJECT, got {diagram_id + sep + project_id!r}")
    try:
        if args.settle_pending:
            problems: tuple[str, ...] = ()
            try:
                config = WriterConfig.from_env()
            except ConfigError as exc:
                problems = exc.problems
            if problems:
                parser.error("; ".join(problems))
            wait = config.lease if args.wait is None else args.wait
            minimum = 2 * config.heartbeat if args.everything else config.lease
            if wait < minimum:
                parser.error(
                    f"--wait must be at least {minimum:g} seconds "
                    + ("(two heartbeats)" if args.everything else "(one lease; use --all in a maintenance window)")
                )
            store = store_from_spec(args.settle_pending)
            print(f"watching the writer heartbeats for {wait:g} seconds ...", flush=True)
            settled = settle_pending(store, store, wait, config.lease, everything=args.everything)
            _print_settled(settled, args.everything)
            return 1 if settled.refused else 0
        if args.requirements:
            return _sync_requirements(store_from_spec(args.requirements))
        if args.workspaces:
            store = store_from_spec(args.workspaces)
            admin = Identity.of(args.admin) if args.admin else None
            if args.admin and admin is None:
                parser.error(f"invalid user id {args.admin!r}")
            _print_workspaces(migrate_to_personal_workspaces(store, admin))
            for missing, _, target in reassignments:
                for project_id in reassign_workspace(store, missing, target):
                    print(f"reassigned project {project_id} -> the personal workspace of {target}")
            return 0
        if args.projects:
            store = store_from_spec(args.projects)
            service = ProjectService(store, store)
            for diagram_id, _, project_id in adoptions:
                service.adopt(diagram_id.strip(), project_id.strip())
                print(f"adopted {diagram_id.strip()} -> {project_id.strip()}")
            _print_repair(service.reconcile())
            return 0
        source = store_from_spec(args.source)
        target = store_from_spec(args.target)
        report = migrate(source, target, diagram_ids=args.diagram, overwrite=args.overwrite)
        repair = ProjectService(target, target).reconcile()
    except (StoreError, WorkspaceError, ProjectError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for diagram_id in report.copied:
        print(f"copied  {diagram_id}")
    for diagram_id in report.skipped:
        print(f"skipped {diagram_id} (exists; use --overwrite)")
    for project_id in report.projects_copied:
        print(f"copied  project {project_id}")
    for project_id in report.projects_skipped:
        print(f"skipped project {project_id} (exists; use --overwrite)")
    for key in report.documents_copied:
        print(f"copied  {key}")
    for key in report.documents_skipped:
        print(f"skipped {key} (exists; use --overwrite)")
    _print_repair(repair)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(migrate_main())
