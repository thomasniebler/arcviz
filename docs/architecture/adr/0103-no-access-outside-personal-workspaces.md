# ADR-0103: Projects outside a personal workspace are visible to nobody

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/personal-workspaces.md` (#178, #179)

## Context

The start-up migration is best effort (ADR-0102): if it fails (a busy Cosmos DB), projects may still name an M20
team workspace, or none, until the next start or the CLI completes it. M20 roles are only meaningful together with
the team's membership: a role of a user who left the team is inert in M20.

## Options

1. **Nobody sees such projects until they are migrated.** No path can give access the new rule does not grant.
2. **Fall back to the M20 rule for them.** Nobody loses access while the migration is pending, but the policy keeps
   two rules, and team membership stays a live access path.
3. **Apply the new rule to the M20 roles as they are.** Inert roles of former members would become access: a gain.

## Decision

Option 1: `project_role` answers None unless the project's workspace is the personal workspace of its owner. The
start-up logs every project left behind; `arcviz-migrate --workspaces` repeats the migration.

## Consequences

A failed migration hides some projects temporarily (as M20 hid data waiting for an admin), never shows anything to
somebody who should not see it. The M20 documents can stay in the store without any effect on access.
