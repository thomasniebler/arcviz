# Agent authentication: personal access tokens and OIDC bearer tokens for `/mcp`

- Status: Implemented
- Issue: #238
- ADR: [0173 Agents authenticate with a PAT or an OIDC token, only on the MCP endpoint](../adr/0173-agent-credentials-pat-and-oidc-on-mcp-only.md)
- Builds on: [Authentication](authentication.md), [Personal workspaces](personal-workspaces.md),
  [Agent access](agent-access.md)

## Summary

The MCP endpoint of [agent access](agent-access.md) needs credentials that a coding agent can present without a browser:

- **Personal access tokens (PAT)**, like GitHub's: a signed-in user creates one in the UI, copies it once into the agent's
  MCP configuration, and can revoke it. Works everywhere, including local runs.
- **OIDC bearer tokens** for deployments behind an identity provider (Azure Easy Auth with Entra ID): Arcviz is an OAuth
  *protected resource*; it advertises its authorization server (RFC 9728 metadata), MCP clients with OAuth support run the
  sign-in themselves, and Arcviz validates the access token. The token a user obtains by signing in to the page for the same
  app registration is accepted too.

Both credentials are accepted **only on the MCP endpoint**, never on the REST API, and the proxy headers are never trusted
there.

## Goals

- `claude mcp add --transport http arcviz https://host/mcp --header "Authorization: Bearer <PAT>"` works in every auth mode.
- With Easy Auth and Entra ID configured, an OAuth-capable MCP client signs the user in and the agent acts as that user:
  same identity id as the browser session, hence the same personal workspace and shares.
- Credentials are per user, revocable, optionally expiring, and have a scope: `read` or `write` (code links and proposals).
- Nothing an agent holds can reach `…/proposals/{id}/apply`, sharing, token management or any other REST route.

## Non-goals

- Dynamic client registration or running an authorization server. Arcviz only validates tokens of an existing issuer; the
  operator pre-registers a public client (PKCE) at the identity provider.
- Refresh or exchange of tokens; the client does that with the issuer.
- A "last used" timestamp (it would turn every read into a write; a follow-up if wanted).
- Fine-grained per-project token scopes.

## Design

### Where credentials are read (`auth.py`)

`AuthMiddleware` keeps its behaviour for every path except the MCP mount (`ARCVIZ_MCP_PATH`, default `/mcp`) and the
metadata path. On the MCP path, in every mode:

1. `Authorization: Bearer <credential>` is classified by its shape:
   - `avp_<32 hex>_<8 hex>_<40 hex>` is a **PAT**; it is looked up and verified (below).
   - a three-part JWT is an **OIDC token** when `ARCVIZ_MCP_OAUTH_ISSUER` is set; validated (below).
   - in `token` mode the shared `ARCVIZ_AUTH_TOKEN` is accepted as before (admin identity, scope `write`).
2. Proxy identity headers are **ignored** on this path (so excluding `/mcp` from Easy Auth cannot be used to forge them),
   and the session cookie is not honoured (a browser has no business there; CSRF surface zero).
3. `off` mode: no credential needed, the admin identity, scope `write`, as for REST.
4. Anything else: `401` with `WWW-Authenticate: Bearer realm="arcviz"` plus, when OIDC is configured,
   `resource_metadata="<base>/.well-known/oauth-protected-resource/mcp"` (and `error="invalid_token"` when a credential was
   presented and failed). The body never says why a token failed beyond that.

Identity is put in `scope["state"]["arcviz_identity"]` as usual; the credential's scope in
`scope["state"]["arcviz_agent_scope"]` (`read|write`). A PAT or OIDC token presented on any other path gets `401` (REST
stays cookie / shared token / proxy headers only).

### PATs (`services/tokens.py`, store document kind `token`)

Format `avp_<user key>_<id>_<secret>`: `user key` = the user's 32-hex personal-workspace hash (`workspaces`, ADR-0084),
`id` = 8 random hex, `secret` = 40 random hex (160 bits). Only the SHA-256 of the secret is stored.

One keyed document per user (kind `token`, key = user key; file `tokens/`, sqlite `documents`, Cosmos partition
`token:<key>`; no new store primitive), so a lookup is one read and listing is one read:

```json
{"user": {"id": "...", "name": "...", "email": "...", "email_trusted": false},
 "tokens": [{"id": "ab12cd34", "label": "laptop", "scope": "read|write", "hash": "<sha256 hex>",
             "created_at": "...", "expires_at": "..."}]}
```

`TokenService` (guarded; an actor manages only their own tokens): `create(label, scope, expires_in_days)` (label ≤ 64,
`expires_in_days` 1..365, default 90; ≤ 20 tokens per user; the identity is snapshotted into the document; conditional
commit), `list()` (never the hash), `revoke(id)`. `authenticate(token)` is the middleware's system-context method:
well-formed, user document exists, id present, `hmac.compare_digest` of the hashes, not expired; every failure looks the
same. Expired tokens are dropped on the next write. Deleting a user's document revokes everything.

REST (session identity only, because PATs do not authenticate there): `GET /api/tokens`, `POST /api/tokens` (the secret is
in this response only), `DELETE /api/tokens/{id}`; rate class `ops`; added to `tests/test_access_matrix.py`.
UI: a **Zugangstokens** dialog (top bar overflow menu): list, create with label/scope/expiry, show-once secret with a copy
button and a ready-to-paste `claude mcp add` line, revoke.

### OIDC bearer tokens (`arcviz/oidc.py`)

Enabled by `ARCVIZ_MCP_OAUTH_ISSUER` (needs `ARCVIZ_MCP_OAUTH_AUDIENCE`). `OidcValidator`:

- discovers `jwks_uri` from `<issuer>/.well-known/openid-configuration` (the `issuer` in the document must equal the
  configured one) and caches the key set (`PyJWKClient`, lifespan 1 h, refresh on an unknown `kid`, at most once per minute);
  network I/O runs in a worker thread, never on the loop;
- accepts only `RS256`/`ES256`, requires `exp`, `iss`, `aud` (configured audience), verifies `nbf`/`exp` with 60 s leeway;
  optional `ARCVIZ_MCP_OAUTH_REQUIRED_SCOPE` must be in `scp` or `scope`;
- identity: id = `oid` claim, else `sub` (for Entra `oid` equals Easy Auth's `X-MS-CLIENT-PRINCIPAL-ID`, so the browser
  user and the agent are the same user); name = `name`; e-mail = `email`, else `preferred_username`, **never trusted** for
  sharing (ADR-0091); scope `write`;
- failures raise a single `InvalidToken`; nothing from the token is logged.

`GET /.well-known/oauth-protected-resource` and `…/oauth-protected-resource/mcp` (public paths when OIDC is configured)
return `{"resource": "<base>/mcp", "authorization_servers": ["<issuer>"], "bearer_methods_supported": ["header"],
"scopes_supported": [...]}`. `<base>` is `ARCVIZ_PUBLIC_URL` if set, else scheme and host of the request (honouring
`X-Forwarded-Proto/Host`; it only affects the advertised URL).

### Deployment notes (documented in `docs/guide/agents.md` and `docs/deploy-azure.md`)

- **Easy Auth + Entra ID**: register a public client with redirect URIs of the MCP client, expose an API scope on the app
  registration, set `ARCVIZ_MCP_OAUTH_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0`,
  `ARCVIZ_MCP_OAUTH_AUDIENCE=<client id of the app registration>`; add `/mcp` and `/.well-known/oauth-protected-resource*`
  to the Easy Auth *excluded paths* so the agent reaches Arcviz without the browser redirect (Arcviz validates the token
  itself). Entra does not offer dynamic client registration, so the MCP client must be told its client id.
  A token copied from the page (`/.auth/me`) for the same audience also works as a bearer.
- **Local or any other deployment**: use a PAT.

## Testing

- `tests/test_tokens.py`: format and parsing, create/list/revoke, caps, expiry, constant-time compare path, hash only stored,
  another user's tokens invisible, documents over all stores (contract for the `token` kind), identity snapshot.
- `tests/test_oidc.py`: validator with a locally generated RSA key and a fake JWKS (no network): good token, wrong issuer,
  audience, signature, expired, not-yet-valid, `alg=none`, HS256 with the public key as secret, missing `oid` falls back to
  `sub`, scope check; metadata documents.
- `tests/test_auth_mcp.py`: per mode (off, token, proxy) × credential (none, PAT read/write, OIDC, shared token, forged proxy
  header, session cookie): status, identity, scope, `WWW-Authenticate`; PAT and OIDC refused on REST; PAT management routes
  refuse a PAT.
- e2e: create a PAT in the UI and call `/mcp` with it.

## Migration and compatibility

None. Without `ARCVIZ_MCP=1` nothing changes; `/api/tokens` exists regardless (so tokens can be created before enabling MCP).

## Deviations from the design

- Without the `oauth` extra of the draft: the `mcp` extra carries PyJWT (`pyjwt[crypto]`) too.
- `ARCVIZ_PUBLIC_URL`, `ARCVIZ_MCP_OAUTH_REQUIRED_SCOPE` and `ARCVIZ_MCP_OAUTH_SCOPES` are read by `AgentAuthConfig.from_env`
  (`agentauth.py`); `AgentGate` is what `AuthMiddleware` calls, so `auth.py` stays free of the MCP details. The OIDC validator is
  `oidc.py`; the token format and shape `tokens.py`, the service `services/tokens.py`.
- In `off` mode a presented credential must be valid (a bad token is a 401, no token is the admin).
- The dialog that creates tokens is reached from the project menu (**Agenten-Zugang…**); the one line about the shared admin
  identity in `token` mode is shown there.
- Not verified against a real Entra ID tenant (the validator is tested with a local key and a fake issuer, the MCP client with
  the official SDK against the real app); the deployment steps are in [Deploy to Azure](../../deploy-azure.md#agents-and-mcp-behind-easy-auth).

## Open questions

Answered as proposed: the dialog says so in `token` mode.


- Should PAT creation require the `token`-mode admin to be explicit (a notice in the dialog that every token acts as the
  admin there)? Proposed: yes, one line in the dialog.

## Decisions

ADR-0173, accepted.
