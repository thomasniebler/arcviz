# ADR-0180: Use cases: the subject is a container, the specification is the note

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/use-case-diagrams.md`

## Context

A use case diagram draws a system boundary (the subject) around use cases, and actors outside it. A use case has a written
specification (goal, preconditions, flows) that requirements engineers maintain. Nodes already carry a note
(`description`, at most 2 000 characters, M10) that the UI, the summary, the diff and the exports know.

## Options

1. **Subject as a container kind; the specification in the note.**
2. **Subject as a diagram-level property** (one system per diagram) and use cases always inside it. Simpler picture, but
   diagrams of several systems and external actors become impossible and the boundary is not an element.
3. **Structured specification fields** (`precondition`, `postcondition`, `main_flow`, `alternatives`) as typed fields. Searchable
   and checkable, but a template that every team fills differently, in a diagram that should stay an overview.

## Decision

Option 1. The subject is the container `subject`; use cases are its children or top level; actors are top level. The note holds
the specification, and the prompt offers a template for it.

## Consequences

- No machine-readable specification yet; a follow-up can add fields and read the note as a fallback.
- The specification length is capped at the note's 2 000 characters.
- Actors outside and use cases inside are a rule of the profile, so an actor cannot be placed inside a subject by mistake.
