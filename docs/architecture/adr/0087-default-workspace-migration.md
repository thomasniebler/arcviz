# ADR-0087: Existing data moves into a default workspace owned by the configured admin

- Status: Superseded by ADR-0102
- Date: 2026-10-07
- Spec: `docs/architecture/specs/workspaces-and-sharing.md`

## Context

Data written by v0.1.8 has projects without a workspace. The maintainer decided that it goes into a default
workspace owned by an admin identity configured via the environment. In proxy mode the app cannot know which
proxy user is the admin unless `ARCVIZ_ADMIN_USER` is set; existing proxy deployments do not set it.

## Options

1. **Refuse to start** in proxy mode without `ARCVIZ_ADMIN_USER`: clear, but an upgrade takes the deployment down.
2. **Default the admin id to `admin`** in proxy mode: a proxy user whose id happens to be `admin` would own all
   existing data.
3. **Migrate projects into the reserved workspace id `default`, and create that workspace only once an admin is
   configured.** Until then the data is hidden, not lost, and nobody can claim the id.

## Decision

Option 3. The migration runs automatically at start-up and with `arcviz-migrate --workspaces` and is idempotent.
`off`/`token` mode always know the admin (`ARCVIZ_ADMIN_USER`, default `admin`). An existing default workspace is
never changed, so a transfer by its owner sticks.

## Consequences

- Proxy deployments set `ARCVIZ_ADMIN_USER` when upgrading (documented operator action); the app logs a warning
  until they do.
- `arcviz-migrate --set-owner WORKSPACE=USER` recovers a workspace whose owner left.
