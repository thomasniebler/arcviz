# ADR-0090: A chat turn whose caller loses the editor role keeps what it wrote

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

A chat turn checks the editor role when it starts and writes every successful tool call at once (`write_through`);
its automatic version and transcript entry follow in the agent's `finally` (`commit_turn`, `append_chat`). A project
owner may lower the caller's role, or delete or move the diagram, while the turn runs. Before this decision a later
tool call raised `Forbidden`/`DiagramNotFound` out of the tool executor (the SSE stream ended without its `final`
event), and `Forbidden` escaped from the `finally` (no version for the changes already written).

## Decision

- A tool call after the change is a tool error, never an exception: `write_through` answers `deleted` (the diagram
  is gone or out of the caller's sight) or `forbidden` (the role dropped below editor), and nothing more is changed
  in that turn.
- The changes the turn already wrote **keep their automatic version, attributed to the caller**: `commit_turn`
  allows it for exactly the revision that `write_through` of the same request last wrote, so it adds no content.
  It never raises for access.
- **No transcript entry** is written once the caller is below editor (a viewer never writes chat); the version's
  label and details still name the turn.
- The `final` event reports it: "Your role on this project was lowered during this turn: the changes made before were
  kept and versioned, nothing after that."
- A caller who can no longer see the diagram at all (removed from the workspace, diagram moved away or deleted) gets
  neither version nor transcript entry, as for a deleted diagram.

- The guard of a turn reads the workspace again for every check above viewer (every write, `commit_turn`,
  `append_chat`), so a member removed, or an owner who handed the workspace over, during the turn is caught at the
  next write; reads keep the turn's cached workspace.
- A real store failure while writing the version (not a conflict, not a deleted diagram) is logged and reported in
  `final.error` ("…their version could not be written: …"); the changes themselves are already saved.

## Consequences

The record of who changed what stays complete: every write the turn made while allowed is in a version with its
author. A turn never fails without its `final` event because of an access change.
