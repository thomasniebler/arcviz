# Authentication

- Status: Implemented; identities, workspaces and roles extended in M20
- Issue: #82 (identities: #158)
- ADR: [0047](../adr/0047-authentication-off-by-default-shared-token-or-trusted-proxy.md),
  [0080](../adr/0080-identities-from-the-authenticating-proxy.md) (identities from the proxy)
- Extended by: [Users, workspaces and project sharing](workspaces-and-sharing.md),
  [Personal workspaces and direct sharing](personal-workspaces.md) (M21)
- Depends on: [HTTP server](../../spec/http-server.md), [Request limits](request-limits.md) (keys rate limits on the identity)

## Summary

Arcviz had no authentication: anyone who reached the port could read, change and delete every diagram
and spend the LLM budget (arc42 R1). The documented answer was an identity-aware proxy in front
(Azure Container Apps Easy Auth), which does not help a `docker compose up` on a shared network, and
the app could not tell whether such a proxy was there. This change adds an **optional** authentication
middleware with two modes besides `off` (the default, so local use and the e2e test keep working):

- **`token`**: one shared secret (`ARCVIZ_AUTH_TOKEN`). API clients send it as `Authorization: Bearer
  <token>`; the browser UI shows a login page once and then holds an HttpOnly session cookie, so the
  token never appears in a URL, in JavaScript or in logs.
- **`proxy`**: a reverse proxy authenticates and passes the user in a header
  (`ARCVIZ_AUTH_PROXY_HEADER`, e.g. Easy Auth's `X-MS-CLIENT-PRINCIPAL-NAME`); requests without it are
  refused. For deployments where only the proxy can reach the app.

Health probes stay unauthenticated; the served documentation is protected unless configured public.

## Goals

- Off by default; when on, every route except `/healthz`, `/readyz` and the login/logout routes needs
  an identity: the API (incl. the SSE chat stream), the UI and its static files, and `/docs` (unless
  `ARCVIZ_AUTH_DOCS=public`).
- The browser UI keeps working in token mode without the token in URLs, `localStorage` or script
  reach; an expired session leads back to the login page and then to where the user was.
- Constant-time comparison of secrets; no secret in logs, in `/api/config` or in error messages.
- Configuration errors (a mode without its secret, a short token) stop the start-up.
- No new dependency.

## Non-goals

- OIDC/OAuth in the app (follow-up; the proxy mode covers it through Easy Auth, oauth2-proxy and the
  like).
- Users, roles and permissions: added in M20 ([Users, workspaces and project sharing](workspaces-and-sharing.md)).
  Since then every request carries an `Identity` (proxy headers, or the admin identity in `off`/`token` mode) and
  the services decide per project what it may do (own projects and shares, M21).
- CSRF tokens: the session cookie is `SameSite=Strict` and mutations need a JSON content type (see
  *Security*).

## Design

### Configuration (`auth.AuthConfig.from_env()`)

| Variable | Default | Meaning |
|---|---|---|
| `ARCVIZ_AUTH` | `off` | `off`, `token` or `proxy`; anything else stops the start-up. |
| `ARCVIZ_AUTH_TOKEN` | unset | `token` mode: the shared secret, at least 16 characters (start-up error otherwise). Generate it, e.g. `openssl rand -hex 32`. |
| `ARCVIZ_AUTH_PROXY_HEADER` | unset | `proxy` mode: name of the header that carries the authenticated user (required). |
| `ARCVIZ_AUTH_DOCS` | `protected` | `public` serves `/docs` without authentication (it holds no diagram data). |
| `ARCVIZ_COOKIE_SECURE` | `auto` | `auto`: the session cookie gets `Secure` when the request came over HTTPS (as uvicorn sees it, see `FORWARDED_ALLOW_IPS`); `true`/`false` force it. |

### Middleware (`auth.AuthMiddleware`, outermost)

For every HTTP request: public paths pass (`/healthz`, `/readyz`, `/login`, `/logout`, and `/docs`,
`/docs/…` when the docs are public). Otherwise the identity is determined:

- `token`: `Authorization: Bearer <token>` compared with `hmac.compare_digest`, or a valid session
  cookie → identity `token`.
- `proxy`: the configured header, stripped, non-empty → its value is the identity.

With an identity the request continues and the identity is stored as `scope["state"]["arcviz_identity"]`
(M20: an `Identity` with id, name and e-mail; in `token` and `off` mode the admin identity), in proxy mode also
its id as `scope["state"]["arcviz_user"]` (the rate limiter keys on it). Proxy mode reads the id, name and e-mail
headers configured directly or by a preset (`easyauth`, `oauth2-proxy`; see
[workspaces and sharing](workspaces-and-sharing.md)), and refuses
a request without a valid id header with `401` — browser navigations included, since there is no login page to
send them to. In token mode, without an identity:

- a browser navigation (`GET`/`HEAD`, `Accept` contains `text/html`, path outside `/api/`) gets
  `303 See Other` to `/login?next=<path and query>`;
- everything else gets `401 {"detail": "Authentication required."}` with
  `WWW-Authenticate: Bearer realm="arcviz"` (token mode).

The middleware runs before the limits middleware, so an unauthenticated request is rejected before its
body is read and does not use anyone's rate budget.

### Session cookie (token mode)

`arcviz_session=<issued>.<mac>`, where `issued` is the Unix time of the login and `mac =
HMAC-SHA256(key=token, msg="arcviz-session:<issued>")` in hex. Valid while `issued` is at most 7 days
old (`SESSION_MAX_AGE`) and not in the future; compared in constant time. Attributes: `HttpOnly`,
`SameSite=Strict`, `Path=/`, `Max-Age` 7 days, `Secure` as configured. The server keeps no session
state: changing the token invalidates every session, logout deletes the cookie in the browser.

### Login and logout

- `GET /login?next=/path`: in token mode a small German login page (self-contained HTML, no script)
  with a password field `token` and the `next` value in a hidden field; in the other modes a redirect
  to `next`.
- `POST /login` (form-encoded or JSON `{"token", "next"}`): a correct token sets the cookie and
  redirects (303) to `next`; a wrong one answers 401 with the login page and the message *Ungültiges
  Token.* `next` is used only when it is a local path (`/…`, not `//…` and without a backslash), else
  `/`, so the login cannot redirect off-site.
- `POST /logout`: deletes the cookie and redirects to `/login` (token mode) or `/`.
- `GET /api/config` reports `auth: {"mode": "off"|"token"|"proxy", "user": <proxy identity or null>}`.

### Browser UI

All requests to the server go through `api.js` (`apiFetch`, used by `api()`, the chat stream and the
export download). A `401` sends the browser to `/login?next=<current path and query>`; after the
login the user is back where they were. The header shows **Abmelden** (a form posting to `/logout`)
in token mode. The page and the documentation are reached by navigation, so the middleware's redirect
covers them.

### Security

- The token travels only in the login form's POST body and the `Authorization` header; the cookie
  carries a MAC derived from it, not the token. uvicorn's access log shows paths, never bodies or
  headers.
- CSRF: a cross-site request does not carry a `SameSite=Strict` cookie; mutating endpoints accept only
  JSON bodies, which a foreign page cannot send without a CORS preflight (`tests/test_cross_origin.py`).
  The login form itself is the only form endpoint; logging someone in requires the token.
- Brute force: the token is at least 16 random characters; guessing it online is not feasible. The
  login is not rate-limited.
- Proxy mode trusts the identity headers blindly. It is safe only if nothing but the proxy can reach the app (an
  internal ingress, a private network, a firewall), and the proxy overwrites or strips the identity headers on
  every request (Easy Auth does; oauth2-proxy sets them). Since M20 the headers decide whose projects a request
  sees, so anyone who can reach the app directly can act as any user. The deployment guide and the configuration
  reference say so.

## Testing

`tests/test_auth.py`: off by default (everything open); configuration errors; in token mode the API
answers 401 with `WWW-Authenticate`, a browser navigation is redirected to the login page with `next`,
a bearer token or a session cookie passes, a wrong token does not; the probes and the login page stay
public; `/docs` protected or public as configured; login sets an HttpOnly, SameSite=Strict cookie and
redirects only to local paths; a wrong token answers 401 with the page; an expired, future or forged
session is refused; logout deletes the cookie; the SSE chat stream works with the cookie; proxy mode
accepts the header and refuses its absence, and the identity keys the rate limiter; `/api/config`
reports the mode and never the token. `tests/js/api.test.mjs`: a 401 redirects to the login page with
the current path. `tests/e2e/smoke.mjs`: a second server in token mode: redirect to the login page, a
wrong token is refused, the right one opens the UI, a chat turn streams and changes the diagram,
**Abmelden** logs out.

## Migration / compatibility

None: the default is `off`, which behaves exactly as before.

## Decisions

- Off by default; a shared token with a cookie session for the browser; a trusted-header proxy mode
  only when explicitly configured; no OIDC in the app for now (ADR-0047, *Proposed*).
- The documentation is protected by default, because it is served by the same app; it contains no
  diagram data, so `ARCVIZ_AUTH_DOCS=public` is offered.
- Sessions last 7 days and are stateless (no server-side revocation other than changing the token).

## Open questions

None. Follow-ups: built-in OIDC (if a deployment cannot use a proxy), configurable session lifetime.
