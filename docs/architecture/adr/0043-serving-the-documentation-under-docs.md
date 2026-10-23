# ADR-0043: Serving the documentation under /docs with revalidation, a 404 when not built, and Swagger UI under /api

- Status: Proposed; amended by [ADR-0116](0116-documentation-only-in-the-project.md) (no GitHub Pages, the site in every artifact)
- Date: 2026-10-06
- Spec: `docs/architecture/specs/docs-in-app.md`

## Context

The built site (ADR-0042) has to be reachable from the UI. Several questions need an answer:
- the URL;
- what to do when no site exists (a development checkout);
- caching, since page URLs are not content-hashed and the site changes with every release;
- protection against path traversal, which matters because the app has no authentication (#82).

FastAPI serves its Swagger UI at `/docs` and ReDoc at `/redoc` by default. Nothing in the repository
or its documentation refers to those paths.

## Options

1. **`/docs` for the site, Swagger UI moved to `/api/docs`.** The obvious URL for users, which #111
   also names. Moving the Swagger UI changes an undocumented default path.
2. **Another prefix (`/help`, `/manual`)** that keeps FastAPI's defaults. No compatibility change, but
   a less obvious URL, and the interactive API docs would keep the more prominent `/docs` path.
3. **Serve the site through a custom route** with our own path checks instead of `StaticFiles`.
   This gives full control, but means our own code for a security-relevant check that Starlette already
   implements and tests (realpath plus a common-path check, symlinks not followed by default).

For a missing site, the choices are: no route at all (FastAPI's JSON 404), a 404 with a
plain-text explanation, a 503, or failing the start. For caching: no header (heuristic browser
caching, so a stale version can be shown after an upgrade), `no-cache` everywhere (revalidate with
ETag/Last-Modified, which `StaticFiles` sends), or long caching of hashed assets plus `no-cache` for
pages.

## Decision

Option 1 with `StaticFiles(html=True)` (subclass `DocsFiles`):
- **Caching:** `Cache-Control: no-cache` on every docs response. It is always correct after an
  upgrade, and revalidation of a local static file is cheap.
- **No site:** `/docs` and `/docs/{path}` answer 404 `text/plain` with build instructions and the
  GitHub Pages URL. A 404 says "not here" without claiming the server is unhealthy, which a 503 would.
  Failing the start would make the docs a runtime requirement.
- **Swagger UI and ReDoc** move to `/api/docs` and `/api/redoc`; `/openapi.json` stays.
- **Configuration:** the site directory is a `create_app` parameter for tests, not an environment
  variable. No deployment needs a different directory yet.
- **Links:** the UI links with absolute paths (`/docs/guide/`), like its existing `/static/` links.

## Consequences

- Anyone who used the undocumented Swagger UI at `/docs` finds it at `/api/docs`.
- Hashed theme assets are revalidated too (one conditional request each). This is acceptable for
  a local, low-traffic app. Long-lived caching for `assets/` can be added later without changing the URL scheme.
- The site's pages load Mermaid from `../static/vendor/mermaid.min.js`, relative to `/docs/`. The
  bundle is therefore tied to being served next to `/static/` by this app. This is also why the
  bundle's configuration has no `site_url`.
- The app, like the rest of its API, serves the docs without authentication (#82). The docs contain
  nothing that is not public on GitHub Pages.
- **To review:** the maintainer should confirm moving the Swagger UI to `/api/docs` and the 404
  behaviour (status stays *Proposed* until then).
