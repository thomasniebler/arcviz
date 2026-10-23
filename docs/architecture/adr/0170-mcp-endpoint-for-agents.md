# ADR-0170: Agents reach Arcviz through an MCP endpoint

- Status: Accepted
- Date: 2026-10-19
- Spec: `docs/architecture/specs/agent-access.md`

## Context

Coding agents need structured, bounded reads of the architecture model (components, responsibilities, relations, code
links) and a few small writes. Arcviz has a REST API for its own UI and an LLM-facing tool layer for its chat agents,
but nothing a third-party agent can attach to. The roadmap lists read-only MCP context as a later item.

## Options

1. **MCP over Streamable HTTP, mounted in the app.** Reuses the app's authentication, limits and services; coding
   agents attach MCP servers natively; needs the `mcp` SDK as an optional extra.
2. **MCP over stdio** (`arcviz-mcp`, a client of the REST API). Easy for a local agent, but a second process to ship
   and configure, and it needs credentials anyway.
3. **Agent2Agent (A2A).** Peer protocol for delegating tasks to an opaque agent. It suits "design this for me", not a
   deterministic lookup, would put an LLM into every query, and has little support in coding agents.
4. **REST only**, with an OpenAPI description. Least code, but every agent needs glue.

## Decision

Option 1, stateless, opt-in (`ARCVIZ_MCP`), tools only, served by the official SDK: its 2026 protocol revision (a per-request envelope without the initialize handshake) coexists with the handshake revisions, which a hand-written endpoint would have to track. The tools call the service layer, so an A2A facade over the
project chat agent or a stdio launcher can be added later without touching the core.

## Consequences

One optional dependency (extra `mcp`, in the image). A new authenticated mount to test in the access matrix style.
The protocol is young: the extra is pinned by `uv.lock` and the tool set is small to keep the surface stable.
