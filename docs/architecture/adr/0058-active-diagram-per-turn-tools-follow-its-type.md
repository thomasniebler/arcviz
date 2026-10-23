# ADR-0058: The active diagram is per-turn state seeded by the client; the tools follow its type

- Status: Proposed
- Date: 2026-10-07
- Spec: `docs/architecture/specs/project-chat.md`

## Context

A project turn may edit several diagrams of different types. The per-type tools (ADR-0031) clash by name
and differ in their kind enums (deployment and activity both have `add_node`), and `operations.apply`
rejects other types' operations. `LLMProvider.run_turn` received one fixed tool list per turn. The UI
always shows one diagram.

## Options

1. **Every type's tools at once, prefixed per type** (`deployment__add_node`, …). Stable tool list, but
   about 45 tools, duplicated descriptions, and an id parameter on every call.
2. **One active diagram per turn**: project tools plus the active type's tools, re-evaluated before every
   model call (`run_turn` accepts a callable); `open_diagram`/`create_diagram` switch. The active diagram is
   seeded from the request (the diagram the UI shows) and not stored on the server.
3. **Active diagram stored in the project document.** Survives reloads, but every switch would be a
   project write that conflicts with structure edits, and two browser tabs would fight over it.

## Decision

Option 2 (conservative, Proposed): the model sees exactly the vocabulary of the diagram it edits, and the
UI stays the single source of the shown diagram between turns.

## Consequences

- The tool list can change between rounds of one turn; earlier `tool_use` blocks may name tools that are
  no longer offered. Providers accept that (tools are per request); prompt caching of the tool block is
  lost after a switch.
- The model must call `open_diagram` before editing another diagram; editing without an active diagram is
  a tool error that says so.
- The per-type system prompt of a newly opened diagram arrives as the `open_diagram` result.
