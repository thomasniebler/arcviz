# ADR-0219: Agents write the model directly or through proposals, per project

- Status: Proposed
- Date: 2026-11-10
- Spec: `docs/architecture/specs/model-first.md`
- Amends: ADR-0172

## Context

ADR-0172 made every MCP change a stored proposal except code links. With the model first, agents should be able to
build the model in the background. The owner decided on a switch per project, set by the project owner, off by
default.

## Options

1. **Always proposals** (ADR-0172 unchanged).
2. **Always direct** for editors.
3. **Per project**: `Project.agent_writes` = `propose` (default) | `direct`.

## Decision

Option 3: owners who trust their agents get background model building; everyone else keeps review before change.

## Consequences

- MCP gets model and view tools that write or propose depending on the setting; no MCP tool removes from the model in
  either mode; hiding in a view is allowed in both.
- Direct writes are change sets with the token's user and client `mcp`, so they are visible and undoable.
- `PUT /api/projects/{pid}/agent-writes` (owner only) and a project setting in the UI.
