# ADR-0124: Listing entries are stamped and pruned after a grace period, conditionally

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/scale-and-robustness.md` (#183)

## Context

The per-user listing (M21) is only ever added to: removing an entry after the revoke or delete that made it stale
can hide a project that is shared again or created again under the same id meanwhile (the add happens before the
share, and is a no-op when the id is still listed). Stale entries cost a point read on every listing.

## Options

1. **Never remove** (M21). Grows without bound.
2. **Remove on read, conditional on the listing revision.** Not enough: a re-share whose add found the id still
   listed did not change the revision, so the prune wins and the share is hidden.
3. **Stamp every add and remove only entries older than a grace period, conditional on the listing revision.** An add
   always changes the revision (it rewrites the stamp); an add before the prune's read has a fresh stamp and is kept
   until its share or create has long completed.
4. **Only a system-context compaction.** Same race as 2 against requests on other replicas.

## Decision

Option 3, on read (`Guard.listed`, best effort) and in the start-up repair. Stale means: the project does not exist
(a definite "not found", never a failed read) or is foreign (not in the user's personal workspace, no share for the
user). Grace: one hour. An entry without a stamp (before M22, or rewritten by an older replica during a rolling
upgrade, which drops the stamps) is stamped by the prune's conditional write instead of removed, so its grace runs
from then.

## Consequences

Listings shrink to what the user sees (plus the last hour's changes). A share whose project write is delayed by more
than the grace period after its listing write could lose its entry; the start-up repair re-adds it.
