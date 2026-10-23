# ADR-0173: Agents authenticate with a PAT or an OIDC token, only on the MCP endpoint

- Status: Accepted
- Date: 2026-10-19
- Spec: `docs/architecture/specs/agent-authentication.md`

## Context

Arcviz authenticates browsers: a shared token with a session cookie, or identity headers from a trusting proxy (Easy Auth,
oauth2-proxy). An agent has no browser. In token mode the only credential is the admin secret; in proxy mode the proxy
stands between the agent and the app. The owner wants a GitHub-style PAT for simple setups and an OAuth flow when the app
sits behind Entra ID.

## Options

1. **Reuse today's credentials.** Admin secret in token mode, nothing in proxy mode. Every agent is the admin; no agent
   works behind Easy Auth.
2. **PATs on all routes.** Simple, but an agent could then call every REST route (sharing, applying its own proposals).
3. **PATs and OIDC bearer tokens, accepted only on `/mcp`**, validated by Arcviz itself; proxy headers ignored on that path.
4. **Arcviz as OAuth authorization server** (DCR, authorize, token endpoints). Self-contained but duplicates the identity
   provider and is a large security surface.

## Decision

Option 3. It gives per-user, revocable, scoped credentials, keeps the "agents propose, people decide" boundary enforceable
(the MCP tool set has no apply, REST refuses these credentials), and works with an unmodified identity provider because
Arcviz is only an RFC 9728 protected resource.

## Consequences

Easy Auth deployments must exclude `/mcp` and the metadata path from Easy Auth, so Arcviz's own JWT validation is the gate
there; a mistake in that code is an authentication bypass for the MCP surface, hence the test matrix. One optional
dependency (PyJWT with crypto, part of the `mcp` extra). A new `token` document kind. No "last used" tracking.
