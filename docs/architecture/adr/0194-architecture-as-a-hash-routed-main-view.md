# ADR-0194: The Architektur tool is a main view addressed by hash routes

- Status: Accepted
- Date: 2026-10-20
- Spec: `docs/architecture/specs/app-shell.md`

## Context

The Architektur tool (four tabs of forms and lists) is a 960 px modal dialog. The requirements elaboration
already is a main view ([ADR-0191](0191-elaboration-as-a-main-view-with-table-and-chat.md)) selected by
`state.mainView`, without a URL. The frontend has no build step and no router; the server serves the single page.

## Options

1. **Keep the dialog and enlarge it.** Least change; still modal, no deep links, blocks the rest of the UI.
2. **Main view in `state.mainView` only.** Consistent with the requirements view; no URL, no back button.
3. **Main view plus hash routes.** The hash is the source of truth for the view; a pure `routes.js` parses it;
   links and the back button work. No server change; a later view only adds a route.

## Decision

Option 3: a main view addressed by `#/architecture/<tab>` (and `#/requirements`), because deep links and the
back button are cheap with a hash and the server stays untouched.

## Consequences

The requirements view joins the router (its button sets the hash). Anything that opened the dialog now sets the
hash. A page-wide chat scope is not needed: the chat is hidden on the page. Later pages (Modell) can follow the
same pattern; real server routes would be a separate decision.
