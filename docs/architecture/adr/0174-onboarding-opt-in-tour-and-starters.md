# ADR-0174: The tour is opt-in and the landing is the empty project

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/onboarding.md`

## Context

New users need an entry point. Options: a separate landing page, a welcome modal on the first visit, a tour that starts
by itself, or guidance inside the existing empty state.

## Options

1. **Separate landing page** before the app. A second page to maintain and to serve behind authentication; users still
   arrive in an empty project afterwards.
2. **Welcome modal or auto-starting tour on first visit.** Needs a per-user "seen" flag (browser storage is per device,
   a server flag is a new document kind and migration), covers the page for scripted clients and e2e runs and annoys
   returning users on a second device.
3. **Guidance in the empty state plus an opt-in tour** from the help menu.

## Decision

Option 3. The empty default project is where a new user lands anyway, so it carries the starters and offers the tour;
the tour is repeatable from **Hilfe**. No stored state, no server change.

## Consequences

- A user who never has an empty project (an admin on a fresh installation gets the demo diagram) sees no starters; the
  tour stays available in **Hilfe**.
- A "seen" flag can be added later without changing the design.
