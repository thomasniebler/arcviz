# ADR-0212: Permalinks are hash deep links that grant nothing

- Status: Accepted
- Date: 2026-10-30
- Spec: `docs/architecture/specs/permalinks.md`

## Context

Collaborators should be able to send each other a link to a project or a diagram. Sharing is decided by the project
owner (`Project.roles`); the app shell already routes through the location hash (ADR-0194).

## Options

1. **Hash deep link, access by existing sharing.** `#/p/<project>/d/<diagram>`; no server change, a stranger gets the
   usual 404.
2. **Server route `/p/<project>/…`.** Cleaner URLs and server-side redirects, but a new route class that must join the
   access matrix and the login redirect flow, for no extra capability.
3. **Invitation links** that add whoever opens them. A different feature: tokens, expiry and revocation in the
   sharing model.

## Decision

Option 1: the link names, the sharing decides. Invitation links stay a possible later feature.

## Consequences

Nothing to migrate or secure server-side. The fragment never reaches the server or its logs. The address bar is not
kept equal to the permalink while working, so people copy it with **Link kopieren**.
