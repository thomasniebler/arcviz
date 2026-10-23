# ADR-0081: Workspaces are teams with exactly one owner who controls access

- Status: Superseded by ADR-0100
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

Arcviz is used by several teams of one organisation. Projects (M16) were the top-level container and visible to
everybody. The maintainer asked for workspaces as teams, above projects, for internal use (not a multi-tenant
SaaS).

## Options

1. **Workspaces administered by global admins.** Central administration; a bottleneck and a role nobody asked for.
2. **Workspaces with members of equal rights.** Anyone in the team can add anyone; nobody is accountable.
3. **Workspaces with exactly one owner.** The owner adds and removes members and can hand the workspace over.

## Decision

Option 3 (maintainer decision): every workspace has exactly one owner, who alone manages its membership and can
transfer ownership; users see only the workspaces they own or are a member of.

## Consequences

- A workspace whose owner left the organisation needs an operator: `arcviz-migrate --set-owner`.
- Members can leave a workspace themselves; the owner cannot (transfer first).
