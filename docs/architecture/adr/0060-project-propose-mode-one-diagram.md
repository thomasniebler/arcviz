# ADR-0060: Propose mode in a project turn covers one existing diagram

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-chat.md`

## Context

Propose mode (ADR-0015) is a dry run: the client holds the operations with base and result fingerprints of
one diagram and confirms them through a replay. A project turn could change several diagrams and create
new ones; creating a diagram is a persistent write.

## Options

1. **Multi-diagram proposals**: a list of per-diagram proposals confirmed together. No atomic commit
   across diagrams exists, so a partial confirmation would need its own conflict handling and UI.
2. **One diagram per proposal**: the first diagram edited in a propose turn is the proposal's; edits to a
   second diagram and `create_diagram` are refused with tool errors that say why; confirmation reuses the
   diagram replay (`proposal.apply_proposal`) through project routes that note the outcome in the project
   transcript.
3. **No propose mode for project turns.** Simplest, but the review's "Als Vorschlag umsetzen" and the
   Vorschau checkbox would silently apply in the project chat.

## Decision

Option 2 (conservative, Proposed): proposals stay exactly as safe as today and the limitation is
reported to the model (tool error) and visible to the user (the reply explains it).

## Consequences

- A multi-diagram change in preview mode takes several turns.
- `proposal.apply_proposal`/`discard_proposal` take a `note` callable for the transcript to write to.
- Lifting the limit later needs ADR-level work on multi-document confirmation.
