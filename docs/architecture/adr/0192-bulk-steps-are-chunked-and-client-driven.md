# ADR-0192: Import is deterministic; refinement and derivation run in chunks driven by the client

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/requirements-workshop.md`

## Context

The steps that touch hundreds of requirements are import, refinement (LLM) and derivation (LLM). One LLM call over 500
requirements exceeds the output limits and the request time limits, and a tool call with 500 items is not reliable.
Arcviz has no background job runner; the file and sqlite stores are single-writer and replicas share nothing but the
store.

## Options

1. **A server-side job** with progress polling. Convenient; a new subsystem (queue, state, crash recovery, replicas)
   for one feature.
2. **One big call per step.** Simple; fails on volume.
3. **Deterministic import (no LLM) and chunked LLM steps, one request per chunk, driven by the client** (or by an agent
   calling the tool repeatedly). Each chunk is an ordinary, bounded, rate-limited request that stores its own result.

## Decision

Option 3. Import parses lists, CSV and Markdown on the server without an LLM (up to 500 items per request, 100 per MCP
call). Refinement takes at most 25 requirements per call, derivation at most 30 per call.

## Consequences

- Progress, cancel and resume are client concerns (the table shows how many are still `raw` without a suggestion);
  closing the browser stops the run without losing finished chunks.
- Cross-chunk quality (duplicates, conflicts) relies on deterministic candidate detection that is passed to the LLM as
  hints (spec); a global consistency pass per theme is a follow-up, not part of this change.
- A pasted list in the chat is split by the assistant into `add_requirements` calls of at most 50; for long lists the
  assistant points to the import button instead (cost and reliability).
