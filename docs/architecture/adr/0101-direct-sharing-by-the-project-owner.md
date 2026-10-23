# ADR-0101: Projects are shared directly with users by their owner; owner → share → none

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/personal-workspaces.md` (#178); supersedes [ADR-0083](0083-project-roles-and-default-viewer.md)
  and [ADR-0085](0085-role-requirements-per-action.md) (their rules for deleting projects and diagrams and for
  moving diagrams between projects stay)

## Context

Without team membership (ADR-0100), the only way somebody else gets access to a project is that its owner grants
it. M20's `Project.roles` already maps user ids to roles; its default role for members (ADR-0083) and the "only
members can be given a role" rule no longer apply. Recipients must be identified without a search (ADR-0091).

## Options

1. **Shares in the project document** (`roles`), granted by the project owner to any known user, viewer or editor.
   One document read decides access; no new document kind; revoking is one conditional write.
2. **Share documents per (project, user)**, queried per request. Allows listing "shared with me" with one query,
   but every access check becomes a query and the project and its shares can disagree.
3. **Editors may re-share.** Convenient, but access spreads without the owner noticing.

## Decision

Option 1, owner-only. The effective role is: the owner of the project's (personal) workspace → `owner`; an explicit
share → that role; otherwise none (404). Through the API an owner grants `viewer` or `editor` to a known user by id
or by trusted, unambiguous e-mail (ADR-0091) and revokes it. A project has exactly one owner: an `owner` entry in
the shares (as M20 wrote them) grants nothing. The full share list is shown to owners; others see the owner and their own role.

## Consequences

Sharing must not become a way to learn about other users (M21 review): project responses never carry the shares;
recipients see the owner by id and display name only (the e-mail address is the owner's own to see); share rows show id and display name, never an e-mail address; the answer to a share names only what the owner typed
and the display name; every reference that does not identify exactly one known user gets the same message; sharing
has its own rate class, on by default (`ARCVIZ_RATE_LIMIT_SHARE`, 30 per minute and client). **Residual risk:** an
owner can still find out that a user id exists, and its display name, by sharing their own project with it and
revoking again; the rate limit bounds how fast, and the attempts are ordinary authenticated writes.

Listing "shared with me" reads the recipient's listing document (re-validated on read; a revoke never removes the
entry, which could hide a re-share made meanwhile, and a stale entry is skipped). A revoked recipient
loses access with the next request; their live streams end with `gone` (same replica at once, others within
`1.5 × poll`). Editors cannot share, revoke, delete the project or take diagrams out of it.
