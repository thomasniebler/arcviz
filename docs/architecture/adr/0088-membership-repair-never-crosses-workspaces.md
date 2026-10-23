# ADR-0088: The membership repair never moves a diagram across workspaces

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`, `docs/architecture/specs/projects.md`
- Amends: ADR-0052 (ordered writes and repair), ADR-0053 (default project lazily and by CLI)

## Context

ADR-0052 keeps "every diagram in exactly one project" with ordered writes (create diagram → place it) and a repair,
`reconcile()`, which `GET /api/projects` ran on every listing. The repair put every orphan, a diagram in no project,
into the default project. With workspaces (ADR-0081) that leaks: a team's diagram orphaned by a crash between create
and place became readable by every member of the default workspace, and any caller's read wrote into other
workspaces. The default project itself could also be moved into a team workspace, so new diagrams created without a
project (and adopted orphans) would land there.

## Options

1. **Quarantine every orphan** (never adopt automatically): safe, but legacy data written before projects existed
   would need an admin, against the maintainer's decision that existing data goes to the default workspace.
2. **Place before creating** (membership first, diagram second): a crash leaves a dangling membership, but a repair
   racing an in-flight create drops that membership and the create then orphans the diagram anyway.
3. **Record the target before creating** (a small `placement` document keyed by the diagram id, written before the
   create, removed after the placement) and adopt an orphan only into its recorded project. An orphan without a
   record is legacy data (before projects) and goes into the default project, as before; one whose recorded project
   is gone is quarantined.

## Decision

Option 3, plus:

- The repair runs **only in the system context**: at start-up (the workspace migration) and with
  `arcviz-migrate --projects`. A caller's read repairs nothing; `ProjectService.reconcile` refuses a guarded call.
- A quarantined orphan stays in no project, so nobody sees it; the CLI lists it and an admin places it with
  `arcviz-migrate --projects STORE --adopt DIAGRAM=PROJECT`.
- A placement record also claims the id: a create of the same id moves on to the next free one (ADR-0089).
  Records left over (diagram placed, or no diagram after one hour) are dropped by the repair.
- The default project never leaves the default workspace (`move_to_workspace` refuses it), and no caller can create a
  project with its id `default` (the next free id is used).

## Consequences

- Every diagram create costs two more small writes (the record and its removal).
- A crash between create and place leaves the diagram invisible until the next start or `arcviz-migrate --projects`
  (previously: until the next project listing). Duplicates and dangling memberships likewise wait for that repair.
- The `placement` kind is a keyed document like `workspace` and `user` (ADR-0082): no new store primitive, no
  migration; older versions ignore it.
