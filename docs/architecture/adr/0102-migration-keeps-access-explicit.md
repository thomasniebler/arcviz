# ADR-0102: The migration to personal workspaces keeps access explicit

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/personal-workspaces.md` (#179); supersedes [ADR-0087](0087-default-workspace-migration.md)

## Context

M20 stores have the `default` workspace (the admin's, holding the v0.1.8 data) and team workspaces with members,
explicit project roles (members created projects as their explicit owners), a default `viewer` role and inert roles
of users who left. v0.1.8 stores have no workspaces at all. Owner decision: keep access explicit, nobody loses or
gains access. The migration runs best effort at every start (several replicas at once) and through the CLI.

## Options

1. **Each project into its workspace owner's personal workspace, every M20 effective role as a share, explicit
   owners kept as co-owner shares.** Nobody loses or gains anything, but projects get two owners, which the target
   model does not have.
2. **A project with an explicit M20 owner among the members (its creator) goes to that member; the former workspace
   owner becomes an editor; all others keep their effective role.** Exactly one owner per project; read and write
   access is preserved; only the team owner's administration of other people's projects ends.
3. **Like 1, but explicit owners become editors.** One owner, but the creators lose their own projects.

## Decision

Option 2 (owner decision, 2026-10-08). Projects without an explicit owner go to the workspace owner, and so do
projects whose explicit owners include the workspace owner (they created it themselves) and the reserved project
`default` (always the owner of the M20 workspace `default`). With several
explicit owners the first by user id gets the project (projects do not record their creator) and the others become
editors. The report lists every such project (`reowned`), and `arcviz-migrate` prints it. Per project one conditional
write sets the new workspace and the shares together. `default` (and data without a workspace) goes to the current
owner of `default`, which is the admin unless it was transferred; without an admin it waits, hidden, as before.
Inert roles of non-members are dropped; the M20 workspace documents are kept as read-only history, so a project added
to a team during a rolling upgrade is still migrated with its members.

## Consequences

The migration is idempotent and crash-safe per project; a project that fails is reported and retried by the next run
while the others go on. M20 projects whose ids have the reserved forms of M21 (`home_…`, `user_…`) are renamed first, crash-safe: the target is
recorded before anything else (keyed document of kind `rename`) so a restart resumes it, and the memberships pass
through that record, so two projects never hold the same diagram. Former team owners keep editing their team's projects but no
longer share or delete those a member created. Projects naming a workspace that does not exist are reported as
stranded and moved with `arcviz-migrate --workspaces STORE --reassign MISSING=USER`.
