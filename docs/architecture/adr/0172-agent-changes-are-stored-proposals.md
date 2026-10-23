# ADR-0172: Agents change structure only through stored proposals

- Status: Accepted
- Date: 2026-10-19
- Spec: `docs/architecture/specs/agent-access.md`

## Context

Propose mode (ADR-0015) is client-held: the browser keeps the operations and fingerprints and sends them back to
confirm. An agent calling over MCP has no such client and may be gone before a person looks. Chat agents change
diagrams only on request of the user who is present; an external agent has no such request.

## Options

1. **No structural writes for agents.** Safe and simple; agents cannot contribute what they learn from the code.
2. **Direct writes** through the operations like the chat agents. Fast; an unattended agent edits shared diagrams.
3. **Stored proposals**: the agent submits operations, Arcviz validates them by dry run and stores them with the diff;
   people apply or discard in the UI.

## Decision

Option 3. Apply and discard exist only as REST routes used by the UI; the MCP tool set has no apply. Code links
(a small, reversible, structure-free write) are the one direct write.

## Consequences

A new keyed document kind (`proposal`) in all stores through the existing document primitives, a service, REST routes
and a UI list. A proposal can go stale; it is then listed as such. In `token` mode the agent holds the admin
identity and could call the apply route directly, so the separation is a convention of the tool set until per-agent
credentials exist (follow-up decision).
