# ADR-0047: Authentication off by default, a shared token with a session cookie, or a trusted proxy header

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/authentication.md`

## Context

The app had no authentication (arc42 R1, #82). It runs locally (`arcviz`, `docker compose up`), in the
e2e test, and behind Azure Container Apps, where Easy Auth can authenticate at the ingress. Users are
one person or a small team; there are no roles. The browser UI is plain ES modules without a build
step, uses `fetch` for JSON and for the SSE chat stream, and links to the documentation served under
`/docs`. No new runtime dependency without a reason (CLAUDE.md rule 5).

## Options

1. **Proxy only (documentation).** Nothing in the app; local Docker deployments on shared networks stay
   open, and the app cannot enforce that a proxy is there.
2. **Built-in OIDC.** Real users and single sign-on, but needs a library (`authlib`) or a careful own
   implementation of the code flow, token validation and key rotation, plus per-provider configuration.
   Easy Auth and oauth2-proxy already do this in front of the app.
3. **A shared bearer token, with a login page that sets a session cookie for the browser.** Small,
   dependency-free, works everywhere; one secret for everyone, no user identity.
4. **Token in the URL or in `localStorage` for the browser.** Simpler UI wiring, but leaks the secret
   into history, logs, `Referer` headers or to any script on the page.

## Decision

Authentication stays **off by default** (local use and the e2e test unchanged). `ARCVIZ_AUTH=token`
enables option 3: `Authorization: Bearer` for API clients and an HttpOnly, `SameSite=Strict` session
cookie (an HMAC of the issue time keyed with the token, valid 7 days) set by a login form for the
browser, never option 4. `ARCVIZ_AUTH=proxy` trusts a configured header set by an authenticating
reverse proxy, documented as safe only when nothing else can reach the app. Health probes are always
public; `/docs` is protected unless `ARCVIZ_AUTH_DOCS=public`. OIDC in the app is a follow-up.

## Consequences

- A deployment can be protected without a proxy, and with a proxy the app can refuse requests that did
  not pass it.
- Everyone with the token is the same identity; rate limits then key on the client address. Proxy mode
  provides a user name, which the rate limiter uses.
- The UI handles 401 by redirecting to the login page, and offers a logout in token mode.
- Changing the token is the only way to end all sessions; there is no server-side session store.
