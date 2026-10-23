# ADR-0084: One access policy in the service layer; services bound to the caller; 404 for invisible, 403 for too low a role

- Status: Accepted
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

All business logic goes through the services over the ports (ADR-0050). Authorization could be checked in the
routes, in a middleware, or in the services. The diagram and project agents, proposals and the model propagation
call the services directly, not through routes.

## Options

1. **Checks in the routes** (dependencies per route). Agents and internal flows bypass them; every new route must
   remember the check.
2. **Checks in the services with an explicit actor argument** on every method. Correct, but every call site and
   test changes and internal calls must pass the actor through.
3. **Checks in the services, which are bound to the caller.** A `Guard` (the policy plus the actor) is given to
   the services when they are built for a request or a chat turn; `None` is the system context (tests, CLI,
   migration, repairs). The server gets bound services from one dependency and never constructs services.

## Decision

Option 3 (the maintainer asked for one policy in the service layer): `AccessPolicy` in `services/access.py`,
bound per request through `ServiceRoot.for_actor`; agents work on bound copies. Invisible resources raise the
not-found error the store would raise (404), visible ones with too low a role `Forbidden` (403).

## Consequences

- `tests/test_layering.py` checks that `server.py` builds no service and never uses the system context; a matrix
  test covers every endpoint and role.
- A guard caches lookups for its lifetime: a role change applies from the next request or chat turn.
- Ids stay global: creating an id that exists elsewhere answers 409, revealing only that the id is taken.
