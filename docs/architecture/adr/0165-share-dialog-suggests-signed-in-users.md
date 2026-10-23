# ADR-0165: The share dialog suggests signed-in users while typing

- Status: Accepted
- Date: 2026-10-13
- Spec: `docs/architecture/specs/personal-workspaces.md` (sharing)
- Supersedes: decision 5 of [ADR-0085](0085-role-requirements-per-action.md) ("no search endpoint"), kept by ADR-0091 and ADR-0101

## Context

A project owner shares by exact user id or trusted e-mail address. Ids from an identity provider (an Entra object id)
are unknown to people, and with Easy Auth there is no trusted e-mail address, so sharing needs the id from somewhere
else. ADR-0085 refused a directory search while the maintainer was unavailable, because a search lets every user
find every other user. The maintainer has now asked for suggestions while typing.

## Options

1. **Keep exact ids and addresses only.** Private, but hard to use.
2. **Suggest only people one already works with** (shares in either direction). Reveals nothing new, but never helps
   with the first share, which is the hard one.
3. **Search every signed-in user by name, id or trusted e-mail address** from two typed characters, at most 10 results,
   choosing a suggestion fills in the user id.

## Decision

Option 3, at the maintainer's request: `GET /api/users?q=` for any signed-in user; untrusted addresses are neither
searched nor shown (they are claims anybody can make, ADR-0091), the caller is left out, and a suggestion shares by
id, so the e-mail rule of ADR-0091 stays as it is.

## Consequences

- Every signed-in user can learn the names (and trusted addresses) of other users who signed in before, a few at a
  time. Deployments that must not allow this need a follow-up switch.
- The directory is listed at most once a minute per process (a cross-partition query with Cosmos DB), so a new user
  may take up to a minute to be suggested.
