# ADR-0155: Damaged version files are reported and skipped, not repaired

- Status: Accepted
- Date: 2026-10-11
- Spec: `docs/architecture/specs/robustness.md`

## Context

The file store claims a version number by creating an empty file (`O_EXCL`) and then writes it. A crash between the
two, or a power loss before M22 synced writes, leaves an empty or cut-off version file; `load_version` raised a
`JSONDecodeError` (HTTP 500) and `purge` could raise a bare `OSError` (#117, arc42 R13).

## Options

1. **Repair**: delete an empty newest claim, or reuse its number. Needs the diagram lock around every claim, and a
   claim by another process sharing the directory (which the store tolerates for numbering) could be taken over.
2. **Report**: `StoreError` from `load_version` ("incomplete" / "damaged", HTTP 404), the listing skips the file, the
   next version takes the next free number.

## Decision

Option 2. `purge` raises only `StoreError` and tolerates temporary files that writes outside the lock rename or create.

## Consequences

A damaged version is visible as an error on that version only; the rest of the history works. An operator can remove
the file by hand. No stored file is renamed or rewritten.
