# ADR-0096: A stream re-checks access on every event and every poll; it ends with an unexplained `gone`

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`
- Builds on: ADR-0084 (access in the service layer, 404 for invisible resources)

## Context

A stream lives for minutes. Meanwhile the caller can be removed from the workspace, the project can move to
another workspace, the diagram can be deleted or moved to a project the caller cannot see. A check at open time
only would keep sending events (and presence) to someone who lost access.

## Options

1. **Check at open only; rely on the stream's lifetime.** Leaks until it expires.
2. **Re-check on every delivered event and every poll tick, with a fresh workspace read.** Three point reads per
   check; a removed member is cut off within one poll interval, instantly on the same instance (the workspace and
   project services publish access events).
3. **Push revocations from the services to the streams only.** Misses changes on other replicas.

## Decision

Option 2. A failed check sends `{"type": "gone"}` and ends the stream; it does not say why, so it leaks no more
than a 404.

## Consequences

The re-checks of all streams share their reads per change (`LiveHub.shared`, invalidated by the events): on the
same instance an access change applies at once, from another replica within about 1.5 poll intervals. The presence
of a viewer whose access ended disappears with their stream. The client treats `gone` like a 404:
it stops and reloads its lists.
