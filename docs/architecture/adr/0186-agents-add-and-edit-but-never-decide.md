# ADR-0186: Agents add and edit staging data directly; binding architecture objects only through proposals

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/architecture-objects-over-mcp.md`

## Context

ADR-0172 lets an MCP agent change diagram structure only through stored proposals; code links are the one direct write.
Stakeholders, drivers, scenarios and decisions have no MCP write path yet. They differ in weight: stakeholders and
requirements (see [requirements](../specs/requirements.md)) are inputs that bind nobody, while drivers, core scenarios
and **accepted** decisions are returned by `get_architecture_context` as binding for every later agent and enter the
prompts of both chat assistants (ADR-0168). An agent that writes those directly can change the constraints it is
supposed to obey.

## Options

1. **Everything direct.** Simplest and fastest; an unattended agent edits the binding context.
2. **Everything as proposals.** Safest; a list of 300 requirements or a stakeholder register would need a decision per
   item before anything can happen, which defeats the point of bulk input.
3. **Split by weight**: stakeholders and requirements are written directly (add and edit, never delete, never decide);
   drivers, scenarios and decisions are proposed and applied by a person. A decision proposed by an agent is a `draft`;
   `accepted` and `superseded` are only set by a person.

## Decision

Option 3. The line is "does it bind anyone else?": staging data is cheap to correct, binding data is not. Agents never
delete, never accept, never apply.

## Consequences

- New write tools (`add_requirements`, `update_requirement`, `suggest_refinement`, `add_stakeholder`,
  `update_stakeholder`) and one proposal tool (`propose_architecture_items`); no delete, accept or apply tool exists
  (`tests/test_mcp_server.py` lists them, like `test_agent_tools.py` does for apply).
- Direct writes still go through the services and the access policy (editor role); they are attributed to the caller
  (`author`) and visible in the UI as agent-made.
- The "agents propose, people decide" convention needs the PAT/OIDC separation of ADR-0173 to be enforced; in `token`
  mode it stays a convention of the tool set, as before.
