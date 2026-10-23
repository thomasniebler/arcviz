# ADR-0125: No derived markup in the frontend; trusted sinks are listed in a test

- Status: Accepted
- Date: 2026-10-08
- Spec: `docs/architecture/specs/hardening.md`

## Context

Two error placeholders (`app.js` start failure, `canvas.js` render failure) interpolated `err.message` into
`innerHTML` (#119). Mermaid parse errors quote the diagram source and server errors echo request data, so error text
can contain markup. Labels are entity-escaped today, so it was not exploitable, but nothing prevented the next sink.
Every other dynamic insertion already used DOM APIs or `textContent`; the Mermaid SVG itself must be inserted as
markup (Mermaid returns a string; it runs with `securityLevel: "strict"`).

## Options

1. **Fix the two places only.** Cheapest; the next `innerHTML` with a message reintroduces the defect unnoticed.
2. **Fix them and add a static rule** (a `node --test` check over `arcviz/static/`): non-constant `innerHTML`, and
   always `insertAdjacentHTML`/`outerHTML`/`document.write`/`DOMParser("text/html")`, fail the tests unless the line
   is marked `// trusted-html: <reason>`, and the number of marked sinks per file is pinned. No new tooling (no ESLint;
   the frontend has no build step).
3. **Trusted Types with a Content-Security-Policy.** The strongest guarantee, enforced by the browser, but Mermaid's
   own DOM writes would need a policy, and the CSP touches every page and the docs site; a larger change.

## Decision

Option 2: error text goes through `dom.placeholder(text)` (`textContent`), and `tests/js/sinks.test.mjs` enforces the
rule; the two Mermaid SVG insertions are the only trusted sinks.

## Consequences

- A new markup sink is a visible, reviewed change to the test's list.
- The rule is line-based: it is a guard against accidents, not a parser; a CSP/Trusted Types rollout stays a possible
  follow-up (option 3).
