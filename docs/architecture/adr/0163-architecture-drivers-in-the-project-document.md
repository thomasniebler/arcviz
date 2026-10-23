# ADR-0163: Architecture drivers in the project document, prioritised like an ATAM utility tree

- Status: Accepted
- Date: 2026-10-12
- Spec: `docs/architecture/specs/architecture-drivers.md`

## Context

The assistants should weigh their proposals against what matters for a project: quality goals, constraints,
requirements and risks, with priorities. The project document already holds the shared model (elements, relations)
and is read once per turn for the prompt. A driver is small (a title, a short text, a few ids) and is edited rarely.

## Options

1. **A field in the project document.** One read per turn together with the elements, conditional writes and
   events for free, bundles carry it; the document grows by at most ~130 kB (100 drivers at their bounds).
2. **A keyed document of its own per project.** Keeps the project document small, but needs new store primitives,
   a second read per turn and its own events, access checks and bundle handling.
3. **A free text "background" field.** Cheapest, but no priorities and nothing to build what-if analysis on.

Priority scales considered: three levels; a strict rank 1..n; ATAM's two axes (business importance × technical
risk, each H/M/L).

## Decision

Option 1 with the ATAM scale: drivers are part of the project document, each rated for importance and risk; the
prompt orders them (H,H) first.

## Consequences

- Drivers have no history of their own (like elements).
- The size guard of the project document covers them; `MAX_DRIVERS` keeps them bounded.
- What-if analysis (#226) can rate scenarios against drivers without another document.
