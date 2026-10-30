# Permalinks

- Status: Implemented
- ADR: [0212 Permalinks are hash deep links that grant nothing](../adr/0212-permalinks-are-hash-deep-links.md)
- Builds on: [App shell](app-shell.md) (hash routes), [Personal workspaces](personal-workspaces.md) (sharing)

## Summary

A project, one of its diagrams or one of its views can be shared as a link. The link only **names** the thing: it
carries no credential, so it opens for people who may already see the project (owner or share) and answers like a
missing project for everybody else. Inviting people stays the job of **Freigeben…**.

## Goals

- A link per project and per diagram, copyable from the UI and the command palette.
- Opening a link in a fresh tab shows that project and diagram, or says why it cannot.
- No server change and no new data: authorization stays in the services.

## Non-goals

- Invitation links that add the opener to a project (a separate design: tokens, expiry, revocation).
- Keeping the address bar equal to the permalink while working: the in-app hash stays as in the app shell.

## Design

Routes (pure, `static/routes.js`):

| Hash | Meaning |
|---|---|
| `#/p/<project>` | the project, its remembered diagram |
| `#/p/<project>/d/<diagram>` | the diagram of the project |
| `#/p/<project>/requirements` | the requirements view of the project |
| `#/p/<project>/architecture/<tab>` | a tab of the Architektur page |

`parseRoute` adds `project` (and `diagram`) to the route; ids are `encodeURIComponent`-escaped and an id that does not
decode, or an empty one, makes the hash an ordinary route; a bad diagram id falls back to the project (`#/p/pr/d/` opens project `pr`). `permalink(route)` builds the hash.

A link is also not followed while the Architektur page holds unsaved edits that the person declines to discard; a refused or failed link restores the address of the view on screen (`syncRoute`). While a chat turn or a proposal locks navigation (`state.busy`) a permalink is not followed: the address is cleared and the
status line says so. Otherwise `shell.apply` resolves a permalink through `navigator.openLocation(project, diagram)`: it reads the project
(`GET /api/projects/{id}`); a failure (404 for a missing project and for one the caller has no role in alike) sets the
status line and leaves the shown project, otherwise the project list is reloaded (a project shared after page load is
there) and `loadDiagramList(diagram, project)` opens it. A diagram the project no longer holds opens the remembered one
and says so. After the link was followed the address is replaced by the in-app form (`replaceState`), so the usual
hash routes, back button and e2e expectations are unchanged. The first project shown at start-up applies a permalink
in the address like any deep link.

Copying: **Link kopieren** in the **⋯** menu of the top bar (diagram) and in the project menu (project), both also in
the palette (`diagram-copy-link`, `project-copy-link`); `navigator.currentLink(scope)` builds the absolute URL from
`location`, `dom.copyText` copies it (moved from `export.js`; fallback for non-secure origins).

## Security

The ids in a link are not secrets and the link grants nothing: `GET /api/projects/{id}` and every diagram route go
through the guard, so a caller without a role gets the same 404 as for a missing id (no existence oracle,
see the access matrix in [Personal workspaces](personal-workspaces.md)). Reverse proxies or the login redirect keep the fragment client-side; the
fragment is never sent to the server, so it appears in no access log.

## Testing

`tests/js/routes.test.mjs` (parse/format round trip, escaping, malformed links); e2e: the copied link is the
permalink of the open diagram, opening it settles to the in-app address and shows the diagram, a link to an unknown
project changes nothing and says so.

## Decisions

- Hash links, no server route: maintainer, 2026-10-30 (answer to "deep link, access via existing sharing").
