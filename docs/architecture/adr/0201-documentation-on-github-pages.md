# ADR-0201: The documentation is also published on GitHub Pages

- Status: Accepted
- Date: 2026-10-23
- Spec: `docs/spec/build-and-release.md` ("Documentation on GitHub Pages"); supersedes the "no GitHub Pages" part of
  [ADR-0116](0116-documentation-only-in-the-project.md)

## Context

ADR-0116 removed the Pages site and said it could come back through a new ADR. The project now has a public
repository whose documentation should be reachable without installing the app, for example from a blog post. The
bundled copy under `/docs` stays the documentation of a running version.

## Options

1. Keep the bundle only.
2. Publish the current `main` once more on GitHub Pages, as one version, without mike.
3. Versioned Pages with a version selector (the pre-M9 setup).

## Decision

Option 2. The workflow `docs-pages.yml` builds the site with `zensical build --strict` and deploys it with the
official Pages actions, on pushes to `main` that change the documentation sources and by hand. `zensical.toml` gets a
`site_url`; the app's bundle ignores it (`scripts/build_docs_bundle.py` removes it). No `gh-pages` branch, no mike.

## Consequences

- A public URL, `https://thomasniebler.github.io/arcviz/`, shows the documentation of `main`, which can be ahead of
  the last release. A running Arcviz still serves exactly its own version.
- Pages must be set to "GitHub Actions" as its source in the repository settings once.
- One more workflow, one job, only on documentation changes (about one billed minute per run).
