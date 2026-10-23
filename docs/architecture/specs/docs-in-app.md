# Documentation served by the app

- Status: Implemented (milestone M14); amended in M9 by [ADR-0116](../adr/0116-documentation-only-in-the-project.md)
  (#188): there is no GitHub Pages copy any more, and every image, wheel and sdist contains the site
- Issue: #111, #188
- ADRs: [0042 Documentation site built into the package](../adr/0042-docs-site-built-into-the-package.md),
  [0043 Serving the documentation under /docs](../adr/0043-serving-the-documentation-under-docs.md),
  [0116 The documentation only in the project](../adr/0116-documentation-only-in-the-project.md)
- Depends on: [HTTP server](../../spec/http-server.md), [Build, CI and release](../../spec/build-and-release.md)

## Summary

The documentation (user guide, reference, technical specification, architecture documentation) was
published on GitHub Pages, which needs network access and shows `latest` or `dev`. That is not
necessarily the version that is running. With this change, the app serves the documentation site of its own build under `/docs/`,
offline. The web UI's top bar links to the user guide and the arc42 architecture documentation. The
site is built from the same `docs/` sources by `scripts/build_docs_bundle.py` into
`arcviz/docs_site/`. The Docker image builds it in a separate stage, and the release wheel builds it before
`uv build`. In a development checkout without a built site, `/docs` answers 404 with instructions
instead of failing.

## Goals

- `/docs/` serves the documentation that matches the running version, without network access
  (no CDN for Mermaid, no Google Fonts).
- The image and the release wheel contain the site; the Dockerfile still copies nothing but
  `arcviz/` (plus the docs build stage).
- No path traversal out of the site directory, also not through symlinks.
- A development checkout without a build works and explains how to build the docs.
- The UI links to the user guide and to the architecture documentation.

## Non-goals

- Versioned documentation inside the app (the app serves exactly its own version).
- Search across versions, or rebuilding the docs at runtime.
- Making the documentation a runtime dependency: building it needs Zensical, which stays a
  documentation-only tool (the `docs` dependency group, ADR-0109), not a dependency of the package.

Since M9 (ADR-0116) the bundled site is the copy of a version: the Pages site with its mike version selector is
gone, and a wheel or sdist build without the site fails (`hatch_build.py`). Since ADR-0201 the current `main` is
also published on GitHub Pages as one unversioned site.

## Design

### Building the site (`scripts/build_docs_bundle.py`, `make docs-bundle`)

The script reads `zensical.toml`, derives an offline configuration from `[project]` and runs
`zensical build --clean --strict -f <derived>`. Differences from the published configuration:

| Setting | Published (`make docs`) | Bundle |
|---|---|---|
| `site_dir` | `site` | `arcviz/docs_site` (or `--out`, which must be inside the repository) |
| `site_url` | `https://thomasniebler.github.io/arcviz/` (ADR-0201) | removed (the bundle is served under any prefix) |
| `extra.version` | none since M9 (it was the mike version selector) | removed (one version) |
| `repo_url`, `repo_name`, `edit_uri` | GitHub repository, edit links | removed (the theme would query `api.github.com` on every page; edit links make no sense in a deployment) |
| `theme.font` | Google Fonts (Inter, JetBrains Mono) | `false` (system fonts, no network) |
| `extra_javascript` | – | `../static/vendor/mermaid.min.js` first, so the theme finds `window.mermaid` and does not load it from unpkg |

Zensical requires `site_dir` inside the directory of its configuration file, so the derived
configuration is written as `.zensical-bundle.yml` (JSON, which is YAML) to the repository root and
removed afterwards. Both files are git-ignored.

### Packaging (ADR-0042)

- **Docker:** a first stage `docs` (the pinned Python image) installs the `docs` dependency group from
  `uv.lock`, copies `zensical.toml`, `CHANGELOG.md`, `CONTRIBUTING.md`, `docs/` and the
  build script, and runs it. The runtime stage copies `/src/arcviz/docs_site` to `./arcviz/docs_site`
  before installing the project. `.dockerignore` no longer excludes `docs/` but excludes a local
  `arcviz/docs_site`, `site` and `.cache`, so the image always contains a freshly built site.
- **Wheel and sdist:** `arcviz/docs_site/` is a hatch `artifact`. It is git-ignored but included
  when it exists. The release workflow (`publish-wheel`) builds it before `uv build`. A wheel built
  without it is valid and serves the 404 explanation.
- **Size:** about 7 MB of content (103 files, 9 MB on disk), about 1.5 MB compressed. The wheel
  grows from about 1.2 MB to about 2.7 MB. The image grows by about 18 MB, because it holds the site twice,
  like `arcviz/static/`: the source copy in `/app/arcviz` and the installed package. Mermaid is not
  duplicated: the site uses the app's vendored copy.

### Serving (`server.py`, ADR-0043)

- `DOCS_DIR = arcviz/docs_site`. `create_app(store=None, provider=None, docs_dir=None)` takes
  `docs_dir` for tests. There is no environment variable.
- If `docs_dir/index.html` exists, `/docs` is a mount of `DocsFiles`, a `StaticFiles` subclass
  with `html=True`:
  - directory URLs serve their `index.html`, and `/docs` and `/docs/guide` redirect to the
    trailing slash;
  - an unknown path serves the site's `404.html` with status 404;
  - every response carries `Cache-Control: no-cache` (revalidation with the `ETag`/`Last-Modified` that
    `StaticFiles` sets).
- Otherwise `GET /docs` and `GET /docs/{path}` answer **404**, `text/plain`, with the text "The
  documentation is not part of this build" plus how to build it (`make docs-bundle`) and where the Markdown
  sources are (the GitHub Pages URL until M9). `make run` builds the site first when it is missing. The decision is made once in `create_app`: building the site needs a restart.
- **Path traversal:** `StaticFiles` resolves the requested path with `os.path.realpath` and refuses
  anything outside the directory (also symlinks pointing out, `follow_symlink=False`). Encoded
  dot segments (`%2e%2e`, `%2f`, `%5c`) never leave the site. Tests cover these cases plus a symlink
  to a file next to the site.
- FastAPI's interactive API documentation, which used `/docs` by default, moves to `/api/docs`
  (Swagger UI) and `/api/redoc`. `/openapi.json` stays. The docs routes are not part of the
  OpenAPI schema.

### Frontend

`index.html` gets a menu **Hilfe** (`<details id="help-menu" class="menu">`) at the end of the top
bar with three links, each opening a new tab (`target="_blank" rel="noopener"`), so the editing state is
kept:
- Benutzerhandbuch → `/docs/guide/`;
- Architekturdokumentation (arc42) → `/docs/architecture/arc42/`;
- Gesamte Dokumentation → `/docs/`.

This is static HTML, so no JavaScript module changes and the layering is unaffected.

### CI

- `scripts/ci_scope.py`: the `docker` scope also runs for `zensical.toml`, `pyproject.toml`/`uv.lock`
  (the docs group), `scripts/build_docs_bundle.py`, `scripts/check_wheel_docs.py` and `hatch_build.py`, the
  inputs of the image's docs stage and of the CI wheel check (ADR-0116). Changes to the pages
  under `docs/` do not trigger it, because the strict docs build already checks them.
- The image smoke test also fetches `/docs/` and `/docs/architecture/arc42/`.

## Testing

- `tests/test_docs_serving.py`:
  - pages and directory index served, redirects to the trailing slash, `no-cache`, and the site's 404 page;
  - path traversal (raw and encoded dot segments, absolute paths, backslashes), and a symlink pointing out of the site;
  - without a site: 404 with build instructions for `/docs`, `/docs/` and deeper paths;
  - Swagger UI at `/api/docs`;
  - the UI links.
- `tests/test_ci_scope.py`: the docs build inputs run the docker scope; pages do not.
- Manual: `make docs-bundle && make run`, then open `/docs/` with the network disabled (Mermaid
  diagrams render from `/static/vendor/`). `docker build` followed by `curl /docs/`.

## Migration/compatibility

- Swagger UI moves from `/docs` to `/api/docs` (ReDoc from `/redoc` to `/api/redoc`). Nothing in
  the repository or the documentation used the old paths.
- Existing deployments behind a reverse proxy that maps only `/api` and `/static` need `/docs` as
  well. The Azure guide uses one ingress for everything, so nothing changes there.

## Open questions

None open. The choices the maintainer should review are marked *Proposed* in the ADRs.

## Decisions

- Build script with a derived offline configuration; a multi-stage Docker build; the wheel
  includes the site as a hatch artifact, built by the release workflow, with no hatch build hook:
  [ADR-0042](../adr/0042-docs-site-built-into-the-package.md) (Proposed).
- `/docs` served by `StaticFiles` with `no-cache`, a 404 with instructions when not built, Swagger UI
  moved to `/api/docs`, no environment variable:
  [ADR-0043](../adr/0043-serving-the-documentation-under-docs.md) (Proposed).
