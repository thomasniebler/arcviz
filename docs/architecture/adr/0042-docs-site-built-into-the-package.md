# ADR-0042: Documentation site built into the package by a script, a Docker stage and the release job

- Status: Proposed; amended by [ADR-0116](0116-documentation-only-in-the-project.md) (no GitHub Pages, the site in every artifact)
- Date: 2026-10-06
- Spec: `docs/architecture/specs/docs-in-app.md`

## Context

#111 asks the app to serve its own documentation. The Dockerfile copies only `arcviz/`,
`pyproject.toml`, `README.md` and `LICENSE`, and `.dockerignore` excluded `docs/`. So the built site has
to end up under `arcviz/` when the image or the wheel is built. Building needs Zensical
(`requirements-docs.txt`, together with a mike fork from a Git URL), which is not a dependency of the
package. The published site loads Mermaid from unpkg and fonts from Google, so it does not work
offline. The built site is about 7 MB (103 files), and the release wheel is built with `uv build`
(sdist, then wheel) in `release-please.yml`.

## Options

1. **Hatch build hook** that runs Zensical whenever a wheel is built. It is automatic, but every
   `pip install -e .`, every `uv build` and every CI install would need Zensical (`build-system.requires`),
   network access and `docs/` (which the sdist and the Docker context would then have to contain).
   Editable installs in CI and in development would get slower, and a docs problem would break
   installing the code.
2. **Commit the built site** under `arcviz/`. Nothing to build, but 7 MB of generated files in
   every docs change, merge conflicts in `search.json` and `sitemap.xml`, and the copy goes stale whenever
   someone forgets to rebuild it.
3. **A build script, run by a Docker stage and the release job; the wheel includes the output when it
   exists.** `scripts/build_docs_bundle.py` derives an offline configuration from
   `zensical.toml`. The Dockerfile gets a `docs` stage and copies its output into
   `arcviz/docs_site`. Hatch ships the git-ignored directory as an `artifact`, and the release job builds it
   before `uv build`. Development installs are unchanged. A wheel or checkout without the site still
   works, and `/docs` explains how to build it.

## Decision

Option 3. Building the documentation is kept out of installing the code, while both delivery
channels (image, release wheel) always contain a site built from the same commit.

## Consequences

- The site is about 7 MB of content (9 MB on disk, about 1.5 MB compressed). The wheel grows from
  about 1.2 MB to 2.7 MB. The image grows by about 18 MB uncompressed: like `arcviz/static/`, the site
  exists twice, in the source tree `/app/arcviz` and in the installed package, which is the one that is
  served. Removing the source copy after installing would save that for both directories. It is a
  separate image-size change and is not done here. Mermaid is not duplicated, because the site
  references the app's vendored copy.
- Docker builds need network access to PyPI for Zensical (they already need it for the
  dependencies), and the `docs` stage is cached until `docs/` or the configuration changes.
- `.dockerignore` stops excluding `docs/`, and excludes a local `arcviz/docs_site` so a stale local
  build never ends up in an image.
- A wheel built by hand without `make docs-bundle` has no site. This is acceptable, and the 404 says so.
- In a development checkout, `arcviz/docs_site/` can be older than `docs/`. It is rebuilt with
  `make docs-bundle`, and nothing checks for staleness.
- CI: the docker scope also runs when the docs build setup changes (`zensical.toml`,
  `requirements-docs.txt`, the script), and the smoke test checks `/docs/`.
- **To review:** the maintainer should confirm option 3 over a hatch build hook, and that the size
  impact is acceptable (status stays *Proposed* until then).
