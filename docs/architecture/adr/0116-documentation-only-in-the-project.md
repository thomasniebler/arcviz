# ADR-0116: The documentation is built into every artifact; no GitHub Pages

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/operations.md` §6 (#188); supersedes the publishing part of
  [ADR-0042](0042-docs-site-built-into-the-package.md) and [ADR-0043](0043-serving-the-documentation-under-docs.md)
  (versioned GitHub Pages next to the bundled site)

## Context

The documentation was built twice: as a versioned site on GitHub Pages (mike on `gh-pages`, `docs.yml`, a release
job) and as an offline bundle that the app serves under `/docs` (ADR-0042, ADR-0043). The bundle was optional for
the wheel: a build without it served a 404. The owner wants the documentation always built inside the project and
Pages switched off for now (2026-10-08).

## Options

1. **Keep both.** Two pipelines, a Git dependency (mike), a Pages setup step, and documentation that a reader can
   find in two places for different versions.
2. **Only the bundle, required in every artifact.** The image already builds it; the wheel and sdist fail to build
   without it (a hatch build hook), CI and the release check the wheel; `make run` builds it when missing.
3. **Only Pages.** The app would link out; offline installations lose their documentation.

## Decision

Option 2. `docs.yml`, the release job *Publish versioned docs*, the `docs-publish` dependency group (mike) and the
version selector go; `zensical.toml` has no `site_url`. The `gh-pages` branch is left for the owner to delete.

## Consequences

Every running Arcviz shows the documentation of its own version; there is no public documentation URL until Pages
(or another host) is switched on again by a new ADR. Building a wheel or sdist needs the docs toolchain first
(`make docs-bundle`), which the release workflow and the CI wheel check do. Editable installs stay unaffected.
