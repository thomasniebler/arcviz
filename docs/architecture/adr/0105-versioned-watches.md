# ADR-0105: A stream's watch is versioned; a stale re-check never overwrites a newer watch

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/collaboration.md` (#176)

## Context

A live stream's `Watch` (diagram, project, workspace, role, revisions) is written from two places: the stream's own
re-check (`run_stream`, in a worker thread, every event and poll) and a selection (`LiveService.select`), which
re-points the stream to the project a just-moved diagram is in now, before the selection is shown (ADR-0097's
"presence follows the diagram"). A re-check that started before the move read the old project; when it finished
after the selection had re-pointed the stream, it wrote the old project back. Until the next re-check (which the
move's events trigger at once) a selection in the moved diagram could be fanned out to viewers of the old project,
who may no longer be allowed to see it.

## Options

1. **Version the watch.** Every update bumps `Subscription.watch_version`; a re-check remembers the version it
   started from and its result is dropped when the version changed meanwhile, and the stream re-checks the newer
   watch at once. Cheap, local to the hub, no lock held across a store read.
2. **Hold a per-stream lock across the re-check.** Selections would wait for a store read (and for a hanging one up
   to the shared-read timeout); the presence POST would block.
3. **Re-check the project after every re-check.** One more read per stream and poll, and still racy.

## Decision

Option 1: `LiveHub.update_watch(sub, watch, based_on=version)` applies only when `sub.watch_version == based_on`
and returns whether it did. `run_stream` captures `(watch_version, watch)` before each re-check, re-checks that
watch, and on a refused update drops the result (no events), keeps the coalesced bus events for the next round and
re-checks at once. `select` updates with `based_on` too and looks again (up to `MUTATION_ATTEMPTS`) when a re-check
won the race.

## Consequences

A stream never points back to a project after it was re-pointed by something newer, so presence never fans a
selection out to the old project's viewers. A dropped re-check costs one extra re-check (shared reads, usually
cached). Ending a stream (`gone`) is never dropped: ending is always safe.
