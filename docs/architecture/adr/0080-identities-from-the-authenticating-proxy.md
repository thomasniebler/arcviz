# ADR-0080: Identities from the authenticating proxy; off and token mode act as the admin identity

- Status: Accepted
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

ADR-0047 added `ARCVIZ_AUTH=off|token|proxy`; proxy mode passed one header value through as a user name and
every authenticated client could do everything. Workspaces and sharing (M20) need a stable user id per request,
a display name and an e-mail address. Deployments use Azure Container Apps Easy Auth (Entra ID) or oauth2-proxy.

## Options

1. **OIDC in the app.** The app runs the login flow and validates tokens itself. A new dependency, secrets in the
   app, session handling; duplicates what the proxies already do well.
2. **Identity headers from the proxy.** The proxy authenticates; the app reads id, name and e-mail from
   configurable headers, with presets for Easy Auth and oauth2-proxy. Requires that only the proxy reaches the app.

## Decision

Option 2 (maintainer decision): identities from configurable proxy headers with presets `easyauth` and
`oauth2-proxy`; requests without a valid id header are refused with 401 (no redirect). `off` and `token` mode
map every request to one admin identity configured with `ARCVIZ_ADMIN_USER` (default `admin`).

## Consequences

- The trust requirement is sharper than before: anyone who reaches the app directly can claim any identity. The
  deployment guide and the configuration reference state it next to the variables.
- Local use (`off`) and shared-token use (`token`) stay single-user: everything belongs to the admin identity.
- A user directory is filled on first sight; it is not searchable through the API.
