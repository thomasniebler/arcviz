# Agent access: MCP, tokens, stored proposals

As implemented. Design and decisions: [agent access](../architecture/specs/agent-access.md) (ADR-0170..0172),
[agent authentication](../architecture/specs/agent-authentication.md) (ADR-0173); user guide: [Coding agents](../guide/agents.md).

## Modules

| Module | Role | Imports |
|---|---|---|
| `mcp_server.py` | The endpoint: `build_mcp(root, path, version)` registers the 14 tools on the SDK's `MCPServer`, serves **POST only** (405 `Allow: POST` otherwise) statelessly with JSON answers (`stateless_http`, `json_response`, DNS-rebinding check off: credentials are bearer tokens), wraps every call in a span `arcviz.mcp.tool` and the histogram `arcviz.mcp.calls.duration`. The SDK (`mcp`, extra `mcp`) is imported inside `build_mcp`; `sdk_missing()` is the start-up check. No `from __future__ import annotations` (the SDK reads the annotations at registration) | `agent_tools`, `services`, `telemetry` |
| `agent_tools.py` | `AgentTools(svc, scope)`: the tools as plain methods returning bounded dicts; `ToolFailure` carries the message for the agent (the store's own text: unknown ids list the known ones). Scope `read` refuses `add_code_ref`, `remove_code_ref`, `propose_change` | `services`, `project_review`, `agent`, `project_model` |
| `agentauth.py` | `AgentAuthConfig.from_env` (the one reader of the MCP variables) and `AgentGate`, which `auth.AuthMiddleware` hands the MCP path and the metadata paths to | `auth`, `oidc`, `limits`, `services.tokens` |
| `oidc.py` | `OidcValidator` (RS256/ES256 only, `iss`, `aud`, `exp` required, 60 s leeway, optional required scope; keys by discovery, cached 1 h, refreshed on an unknown `kid` at most once a minute; a discovery document of another issuer is refused), `protected_resource_metadata` | PyJWT (lazy) |
| `tokens.py`, `services/tokens.py` | Token format, `TokenBox` document, `TokenService` | `workspaces` |
| `proposals.py`, `services/proposals.py` | Stored proposals: shape, bounds, retention, `diagram_diff`, `ProposalService`, `apply_proposal`/`discard_proposal` (moved here from `proposal.py`, which re-exports them) | `operations`, `sequence`, `model` |

## Tools

`list_projects`, `get_architecture_context`, `list_components`, `get_component`, `find_component_by_path`, `list_findings`,
`list_diagrams`, `get_diagram`, `list_operations` (argument schemas of the chat tools of a diagram type), `list_proposals`
(no diffs), `get_proposal` (operations and diff), `add_code_ref`, `remove_code_ref`, `propose_change(project, diagram,
operations, rationale?)`. Lists are bounded (`limit` 1..200, default 50, `total` and `truncated`). Every call runs on
`ServiceRoot.for_actor(identity, per_request=True)`, so the access policy decides; an unknown or foreign project is "not
found". There is no tool that applies, discards, accepts, deletes or decides anything (`tests/test_agent_tools.py`,
`tests/test_architecture_tools.py` assert it).

**Architecture objects** (spec [architecture objects over MCP](../architecture/specs/architecture-objects-over-mcp.md), ADR-0186,
ADR-0187). Read: `list_stakeholders`, `list_drivers`, `get_driver`, `list_scenarios`, `get_scenario`, `list_decisions`,
`get_decision`, `list_requirements` (paged, `next`), `get_requirement`, `get_traceability`; `get_architecture_context` also
answers the requirement counts per pipeline step. Write (scope `write`, editor role): staging data directly, **never delete,
accept or decide**: `add_requirements` (≤ 50 items, idempotent, rejected rows reported), `update_requirement` (only `source`,
`theme`, `priority`, `stakeholders`, `elements`; `requirement_model.check_agent`), `suggest_refinement` (≤ 25 suggestions,
stored as suggestions: nothing is in force until a person accepts), `add_stakeholder`, `update_stakeholder`. Binding objects
only as a proposal: `propose_architecture_items(project, items, rationale?)`. Requirement and object texts come from people; the
tool descriptions say they are data.

## Authentication on the MCP path

`AuthMiddleware(app, config, agents)`: a request whose path is the MCP path (`ARCVIZ_MCP_PATH`, default `/mcp`; exact, no
trailing slash) or, with OIDC, `/.well-known/oauth-protected-resource[<path>]` goes to `AgentGate.handle`, in every mode and
before the mode's own rules. The bearer is classified by shape: `avp_<32 hex>_<8 hex>_<40 hex>` is a **personal access token**
(`ServiceRoot.authenticate_token`, run in a worker thread), a three-part JWT is an **OIDC access token** when an issuer is
configured, and in `token` mode the shared secret is accepted (admin identity, scope `write`). In `off` mode no credential is
needed (admin, `write`), but a credential that is presented must be valid. Proxy identity headers and the session cookie
are **ignored** on this path. The result is `scope["state"]`: `arcviz_identity`, `arcviz_agent_scope` (`read`/`write`; OIDC
and the shared secret are `write`), `arcviz_agent_kind` (`pat`, `oidc`, `shared`, `open`), and `arcviz_user` for the first
two. Failure: `401` `{"detail": "Authentication required."}` with `WWW-Authenticate: Bearer realm="arcviz"`, plus
`resource_metadata="<base>/.well-known/oauth-protected-resource<path>"` when OIDC is configured and `error="invalid_token"`
when a credential was presented. `<base>` is `ARCVIZ_PUBLIC_URL`, else scheme and host of the request (`X-Forwarded-Proto` and
`-Host` honoured, host charset checked). Everywhere else these credentials mean nothing: REST keeps its one rule per mode.
`tests/test_agent_gate.py` is the matrix (mode × credential).

### Personal access tokens

`avp_<user key>_<id>_<secret>`: the 32-hex part of `workspaces.user_key(user id)`, 8 random hex, 40 random hex (160 bits).
The keyed document `token` (key `user_key`; file `tokens/`, SQLite `documents`, Cosmos `token:<key>`) holds
`{user: {id, name, email, email_trusted}, tokens: [{id, label, scope, hash, created_at, expires_at}]}` with the SHA-256 of
the secret. `TokenService`: `create(label, scope, expires_in_days)` (label ≤ 64 one line, scope `read|write`, 1..365 days,
default 90, at most 20; the identity is snapshotted at creation; the value is returned once), `list()` (own, unexpired, no
hash), `revoke(id)`, `authenticate(token)` (every failure `None`; the hashes are compared in constant time also for an unknown
id; a damaged or unreadable document authenticates nobody and is never overwritten). Writes are conditional on the revision and
retried (`MUTATION_ATTEMPTS`); expired tokens are dropped by the next write. REST: `GET /api/tokens`, `POST /api/tokens`,
`DELETE /api/tokens/{id}` ([API](../reference/api.md#agent-access-tokens-proposals-mcp)). UI: `tokenpanel.js` (**Agenten-Zugang…**),
pure `agentaccess.js` (snippets, expiry text).

## Stored proposals

One keyed document `proposal` per project (key = project id): `{seq, proposals: [...]}`, a proposal being `{id p<n> (never
reused), project, diagram, author, author_name, client, created_at, rationale, operations [{op, arguments}], base, result,
diff, status pending|applying|applied|discarded|stale, decided_by, decided_at, version}`; the document also holds the project's `incarnation` (`projects.incarnation_of`): a box of another incarnation, left by a deleted project of the same id, is ignored and replaced. `ProposalService.create` (editor) requires the
diagram to be a member, refuses internal operations (`operations.INTERNAL_OPERATIONS`), at most `MAX_OPERATIONS` 50 operations,
a rationale ≤ 2000, ≤ `MAX_BYTES` 16 KB of operations and diff, at most `MAX_PENDING` 30 pending per project, and dry-runs
every operation on a copy (`Operation n (op) cannot be applied: …`); it writes no diagram. `apply` (editor, REST only) first
**claims** the proposal (`applying`, conditional on the revision, so of two concurrent applies one is refused and a discard during
the replay is refused), then replays through `apply_proposal` (the base and preview fingerprint checks, adoption of the new
views, one version labelled `Proposal p<n> by <name>`) and marks it `applied`; a conflict or a deleted diagram marks it `stale`
and answers 409, any other failure releases the claim (`pending`). A claim older than `CLAIM_TTL_SECONDS` (300) belongs to a
process that is gone and is taken over; if the version with the proposal's label exists since the claim, the interrupted
application is recognised and the proposal marked `applied` with that version. Claimed proposals count as pending for the cap
and are never swept, `discard` (editor). Reads (viewer) never
write; every write drops decided proposals older than `ARCVIZ_PROPOSAL_RETENTION_DAYS` (default 30) and all but the newest
`KEEP_DECIDED` 20. UI: `proposalpanel.js` (**Vorschläge**, count of pending proposals read when a project is shown, every 30 s
and after a decision; diffs by `proposal.js`).

### Project proposals (`kind: "project"`, ADR-0187)

A stored proposal is a **diagram** proposal (above) or a **project** proposal: `kind` (omitted from dumps for diagram proposals, so
stored data is unchanged), `items`, `diagram = ""` and no `operations`, `base`, `result` or `diff`. An item is `{key, op, arguments,
title, derived_from, base, state pending|applied|discarded|stale|failed, created, error}`; `op` is one of `PROPOSABLE_OPERATIONS`
(`add_`/`update_` of driver, scenario, decision, the additive `link_requirements`, `set_vision`, `set_arc42_note`, and the shared model's `add_element`, `update_element`, `add_relation`, `update_relation`, `remove_relation`, ADR-0206); later items name earlier ones as `"@key"`. The steward ([design](../architecture/specs/project-steward.md)) also sets `finding`, `group` (`fix` | `next_step`), `rationale` (≤ 600 characters) and `cites` (≤ 12 ids) on an item; each is omitted from dumps while empty. An item of the shared model applies through `ModelService` (views follow a rename, links of a changed relation are cleared) instead of `ProjectService.apply_all`; `add_element` and `add_relation` carry no `origin`, an interrupted application is recognised by the element's name or the relation's kind and ends.

`ProposalService.create_project` (editor) checks at most `MAX_ITEMS` 60 items and `MAX_PROJECT_PROPOSAL_BYTES` 64 KB, keys of 1 to
24 characters `a-z0-9_`, at most `MAX_PENDING_PROJECT` 5 pending project proposals (diagram proposals count separately against
`MAX_PENDING`), refuses `origin` in arguments, `status` and `superseded_by` of a decision (`AGENT_REFUSED_FIELDS`: a proposed
decision is always a draft) and `requirements` on an `update_*` (it would replace the trace: use `link_requirements`), checks that
every named requirement exists, then applies the items **in order to a copy of the project** through `projects.apply`, resolving
`@key` to the id the earlier item gets; an error names the item (`Item 'q' (add_scenario) cannot be applied: …`) and nothing is
stored. An `update_*` item whose arguments name no key records `base`, the fingerprint of its target.

`apply(project, proposal, accept?)` (editor, REST only) claims the proposal like a diagram proposal, takes `accept` (item keys; default
every pending item), refuses a selection whose item needs a key that is neither selected nor applied, then applies the items in
order through `ProjectService.apply_all`, each a project write: `derived_from` of an `add_*` is merged into its `requirements`, of
an `update_*` written as `link_requirements` in the same write; an `update_*` whose target changed (`base`) is `stale`; a failing item
is `failed` with the error and the others go on. **Crash safety:** an applied `add_*` sets `origin = "<proposal>/<key>"` on the object in
the same write, and a repeat finds an object with that origin instead of creating another. The proposal is `pending` while items wait,
else `applied` (some applied), `stale` (only stale/failed) or `discarded`; a decided project proposal drops its arguments (small
documents). `discard(project, proposal, keys?)` discards the named pending items and those that need them.

## Configuration

`ARCVIZ_MCP`, `ARCVIZ_MCP_PATH`, `ARCVIZ_PUBLIC_URL`, `ARCVIZ_MCP_OAUTH_ISSUER`, `ARCVIZ_MCP_OAUTH_AUDIENCE`,
`ARCVIZ_MCP_OAUTH_REQUIRED_SCOPE`, `ARCVIZ_MCP_OAUTH_SCOPES`, `ARCVIZ_PROPOSAL_RETENTION_DAYS`, `ARCVIZ_RATE_LIMIT_AGENT`
([reference](../reference/configuration.md#agent-access-mcp)). A malformed value, a missing SDK (`mcp`) or missing PyJWT stops the
start with a message naming the variable or the extra. `GET /api/config` has `mcp: {enabled, path, oauth}`.
