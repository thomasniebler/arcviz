# ADR-0097: Presence lives in process memory, per instance

- Status: Superseded by ADR-0104
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md`

## Context

Presence (who views, what they selected, who chats) changes several times a second and is worthless after a few
seconds. It must not be persisted and needs no history. With several replicas the viewers of one diagram may be
connected to different processes.

## Options

1. **In process memory.** Free and instant; viewers on other replicas are invisible.
2. **Keyed documents in the store with a TTL.** Works across replicas through the existing poll, but writes a
   document per selection change (Cosmos RU, file/sqlite are single-instance anyway) and adds a new document kind
   and expiry to all three backends.
3. **A shared pub/sub service.** Correct everywhere; new infrastructure.

## Decision

Option 1. Entries belong to open streams; an ended stream's entry lingers 5 s (reconnects do not flicker), and
entries not refreshed for `max(3 × poll, 15 s)` are dropped. The limitation is documented in the guide and the
operations docs.

## Consequences

On a single instance (the default deployment) presence is complete. With replicas, changes still arrive everywhere
but presence shows only the people on the same replica. A follow-up can add option 3 behind the same hub interface.
