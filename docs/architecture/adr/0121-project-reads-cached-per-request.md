# ADR-0121: Project documents are cached per HTTP request, never in chat turns or live streams

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/scale-and-robustness.md` (#166)

## Context

The access guard re-reads a diagram's project for every service call, so a diagram detail request read the same
project document four or five times. ADR-0090 requires that a chat turn sees a role change at its next step, and
live streams re-check access on every event (ADR-0096).

## Options

1. **A project cache in every guard.** Simple, but a chat turn and a live stream would keep using a project read
   minutes ago.
2. **A cache only in guards of ordinary requests; viewer checks only.** Ordinary requests last milliseconds; checks
   for more than viewer (writes) and `fresh` checks still read the project, and writes of the request invalidate
   the cached copy. Chat and live routes get services without the cache.
3. **One service call per route that returns everything.** Every route would need a bespoke service method.

## Decision

Option 2: `Guard(cache_projects=True)` through `ServiceRoot.for_actor(actor, per_request=True)` in `services_of`;
`turn_services_of` (chat streams, live streams) and every non-HTTP use (tests, CLI) build guards without it.

## Consequences

A detail request reads the project once. The workspace cache already worked this way for viewer checks; both now
share the rule "viewer reads may reuse, everything else reads again". A new long-running route must use
`TurnSvc`.
