# ADR-0104: Presence across replicas as expiring Cosmos DB items, one per project and replica

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md` (#173); supersedes [ADR-0097](0097-presence-in-process-memory.md)

## Context

ADR-0097 kept presence in the memory of one process: with several replicas on one Cosmos DB, a viewer saw only the
people connected to the same replica. The owner's target deployment is one service, scaled out to a few replicas,
on one Cosmos DB (M21). Presence changes several times a second per stream but is worthless after a few seconds;
Cosmos bills every read and write in request units (RU). No new runtime dependency is wanted (owner, 2026-10-08).

## Options

1. **One item per stream, written on every change.** Simple, but a write per selection change and per heartbeat of
   every stream, and a query per stream and poll to read them.
2. **One item per project and replica, written by one relay thread per process.** The relay writes the replica's
   item of a project when its entries changed (at most four times a second) and refreshes it with a heartbeat
   (TTL/3); it reads the other replicas' items of the projects it has open streams for once per poll interval
   through `LiveHub.shared`. Cost per replica: ≤ 1 query per project and half poll, writes only on change plus one
   per project and heartbeat, independent of the number of streams.
3. **A pub/sub service** (Azure Web PubSub, Redis). Instant and cheap per message, but new infrastructure and a new
   dependency.

## Decision

Option 2. Items live in partition `presence:<project>` (id: the replica, type `presence`), with the Cosmos `ttl`
set to the presence TTL (`max(3 × poll, 15 s)`) and an `expires` wall-clock time that readers check themselves
(so a container without TTL still works; readers delete items far past their TTL). The store offers it through
`presence_channel()` (None for file and sqlite: they serve one instance, presence stays in memory). A stream sees
remote entries exactly like local ones: the entries of its own project, for which it was access-checked; a session
seen locally is shown once, from local memory. On shutdown a replica deletes its items.

## Consequences

Presence is complete across replicas within about one poll interval (a selection made on another replica appears
after up to `poll/2` of cached read plus the relay cycle). Within that window an entry that left a project (a moved
diagram, a revoked share) may still be shown to that project's viewers on other replicas, like every other
cross-replica change (ADR-0096's `1.5 × poll`). A failing presence channel only costs the cross-replica part
(logged once per failure streak). New containers are created with TTL enabled (`default_ttl = -1`); for an existing one the
app turns it on at start-up when permitted, passing every other container setting back unchanged (a replace resets
what it does not carry; a container with unique keys or another setting a replace cannot carry is left alone), else the
operator enables TTL ("On (no default)") to have Cosmos remove leftovers, which readers otherwise clean up.
