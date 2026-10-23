# ADR-0100: One personal workspace per user, created on first sign-in

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/personal-workspaces.md` (#177); supersedes [ADR-0081](0081-workspaces-are-teams-with-one-owner.md)

## Context

M20 made workspaces teams: one owner, members who see every project of the team at a default role, ownership
transfer (ADR-0081). The target deployment (owner decision, 2026-10-08) is one service on one Cosmos DB with many
users who must be isolated from each other; team membership is exactly the kind of implicit access that leaks
across users. Several replicas may see a new user's first request at the same time.

## Options

1. **Keep teams, add personal workspaces next to them.** Two kinds of containers and two access paths to test and
   explain; membership stays an implicit grant.
2. **One personal workspace per user, nothing else.** The workspace is just the owner's container; access to
   anybody else's project only comes from an explicit share (ADR-0101).
3. **No workspace at all, an owner field on the project.** Simplest model, but loses the existing keyed document,
   the place for per-user settings, and changes every project query.

## Decision

Option 2. Each user also gets an own default project (*Standardprojekt*, id `home_` + the same hash), where
diagrams created without a project go (owner, 2026-10-08); the reserved project `default` stays the admin's. Ids of
the forms `home_`/`user_` + 32 hex digits are reserved for these; should one be taken anyway (an M20 project not
renamed yet), a free variant is recorded on the workspace (`default_project`); a recorded id that no longer points
into the caller's own workspace (deleted, then taken by somebody else) is replaced by a new variant. A user's project list comes from a
per-user listing document, re-validated on read, never from a scan of every tenant's projects. The
workspace id is derived from the user id (`user_` + 32 hex digits of its SHA-256; `_` never occurs in an M20
workspace id, which were slugs), the document is created with create-if-absent on the first request (cached per
process, repeated before a project is created), so replicas racing for it both end up with the same one. A document
under that id that is not the user's personal workspace is never used. The workspace API shrinks to
`GET /api/workspace`.

## Consequences

Isolation by construction: a project is either in your workspace or shared with you. Creating, renaming, deleting
workspaces, members and transfers disappear from the API and the UI (breaking). A per-process cache keeps the
check to one point read per user and process.
