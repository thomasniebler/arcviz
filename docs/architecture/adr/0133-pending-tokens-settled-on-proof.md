# ADR-0133: Pending locator tokens are settled once their writer provably stopped or died

- Status: Accepted
- Date: 2026-10-09
- Spec: `docs/architecture/specs/follow-ups.md` (#189)
- Refines: [ADR-0120](0120-diagram-locator-as-a-re-validated-hint.md)

## Context

Since M22 a placement lists its target in the diagram's locator entry with a pending token before it writes the
membership and clears the token afterwards; `remove_location` and `vacate` never drop a pending project. That keeps
the invariant (the entry lists every project holding the id) without clocks, but a writer that crashes in between
leaves its token for good: the project stays listed (an extra point read), the entry is never dropped, and a create of
that id fails closed. Replicas share no clock; Cosmos DB has no transaction across the locator entry and the project
document.

## Options

1. **Operator command only** (`arcviz-migrate --settle-pending`): clear every token while no writer runs. Simple, but
   needs a maintenance window and trusts the operator that nothing runs.
2. **Writer incarnations with a heartbeat document, settled on proof** (plus the operator command). Every token names
   its writer incarnation (new per process start); each process renews a `writer` document by conditional writes. A
   writer is provably done when it wrote `stopped` on a clean shutdown with nothing in flight, or when another process
   saw its document unchanged for a whole lease (10 heartbeats) on *its own* monotonic clock and then won a write
   conditional on that very revision (`dead`). That write fences the writer: its next renewal fails and it issues no
   tokens any more; a writer that could not renew for half a lease stops issuing tokens by itself. Only durations on
   one clock are compared, never timestamps of two replicas.
3. **Server-side expiry** (Cosmos DB TTL on the writer document): uses the database's clock, but works only on Cosmos
   DB and gives no fence the writer could notice.
4. **Clock-based expiry of tokens** (a timestamp in the token): needs comparable clocks across replicas, which M22
   ruled out.

## Decision

Option 2, with option 1 as the operator path (`--settle-pending`, and `--all` in a maintenance window, which refuses
when any heartbeat moves; the only way to clear M22 tokens, which name no writer). It clears the tokens of crashed
writers automatically, on every backend, without comparing clocks, and fails closed: a failed read or write settles
nothing, a live writer is never settled, tokens of unknown writers are left alone.

## Consequences

- One small keyed document per process start (deleted a lease after its tokens are all settled) and one conditional write per
  heartbeat (default 30 s) per replica; each heartbeat also lists the writer documents (a background cross-partition
  query, never on a request path).
- Settling keeps the project listed when it holds the diagram; a writer checks its fence again inside the membership
  write (not fenced, renewed within half a lease) and lists its target again in its settle
  (`settle_location(relist=True)`). Residual: the membership write landing more than half a lease (2.5 minutes by
  default) after that check, while another process fenced the writer and dropped the target from the entry, leaves a
  membership unlisted until the writer's settle lists it again. It needs a stall of the write itself (not of the
  process before it), and is reported in the spec.
- A reaper judges others only while its own heartbeat is fresh, and starts every timer over after its own outage or a
  gap of more than half a lease, so a store outage that stops all heartbeats fences nobody. It runs on its own
  thread (a slow scan never delays a beat) and lists the locator entries twice per stopped or dead writer: once to
  settle, once a lease later before the writer document is dropped.
- A fenced process places no diagrams and fails `/readyz` and `/healthz` (liveness), so the orchestrator restarts it;
  every lifespan is a new writer incarnation (a new boot id).
- Tokens written before M24 are only cleared by the operator command with `--all`.
