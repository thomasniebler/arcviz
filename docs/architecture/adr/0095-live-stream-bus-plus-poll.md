# ADR-0095: One live stream per tab: in-process bus plus a revision poll; events are refetch hints

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`

## Context

Other clients must see changes without reloading. The app runs as one process (file, sqlite) or as several
replicas over Cosmos DB; no message broker exists and no runtime dependency may be added without need. Browsers
allow six HTTP/1.1 connections per origin. Every client may have its own focus (zoomed container).

## Options

1. **WebSockets.** Bidirectional, but needs an extra protocol path through the proxies and no gain: clients send
   little (a selection), which a POST covers.
2. **SSE fed only by polling.** Simple, works across replicas, but every change waits for the poll.
3. **SSE fed by an in-process bus plus a poll of the latest revision.** Instant on one instance, correct across
   replicas after one interval (`ARCVIZ_LIVE_POLL_SECONDS`).
4. **A broker (Redis, Azure Web PubSub).** Instant everywhere; new infrastructure and a dependency.

For the event content: the render payload (only right for clients without focus, rendered per publish) versus a
hint with revision and author, after which each client refetches its own detail.

## Decision

Option 3, one stream per tab (the diagram's, or the project's when no diagram is open; a diagram stream also
carries its project's events), with hints only. The services publish after their writes; the bus never carries
diagram content.

## Consequences

One GET per change and client. Changes on other replicas arrive with up to one poll interval delay and without an
author. Each open stream costs three point reads per interval. Transcripts are pushed only within one instance.
