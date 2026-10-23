# ADR-0083: Project roles owner/editor/viewer; members default to viewer; the workspace owner owns every project

- Status: Superseded by ADR-0101
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

The maintainer decided on sharing per project with the roles owner, editor and viewer. Open were: the role a
workspace member has on a project nobody shared explicitly, whether a member can be excluded from a project, and
what the workspace owner may do on projects.

## Options

1. **Default role editor** for members: convenient for small teams, but anyone joining a workspace can change
   everything.
2. **Default role viewer**, raised per member by a project owner (the conservative default named in #160).
3. **No default access** (projects invisible until shared): a role "none" and a second visibility concept inside
   a workspace, which contradicts "workspace = team".
4. Workspace owner with **no implicit rights** on projects: a project whose owners left the workspace would be
   unmanageable.

## Decision

Option 2, and the workspace owner is owner of every project of the workspace. Roles are stored in the project
document (`roles: {user id: role}`); the effective role checks workspace membership first, so roles of removed
members are inert.

## Consequences

- Every member sees every project of the workspace; confidential work goes into its own workspace.
- A project owner can lower another owner, but never the workspace owner, so a project can always be managed.
- A per-project default role is a possible follow-up.
