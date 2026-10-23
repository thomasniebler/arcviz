# ADR-0132: Mermaid's output goes through the SVG allow-list, parsed in an inert template

- Status: Accepted
- Date: 2026-10-09
- Spec: `docs/architecture/specs/follow-ups.md` (#197)
- Refines: [ADR-0125](0125-no-derived-markup-in-the-frontend.md) (the two Mermaid sinks were its only trusted sinks)

## Context

Since M23 every SVG the own renderers produce goes through the allow-list sanitizer `svgsafe.js`. Mermaid's output
was inserted with `innerHTML` as a reviewed trusted sink (`canvas.js`, and a detached `div` in `export.js`), relying on
`securityLevel: "strict"` and on the server escaping every label (`escape_text`). With `htmlLabels: true` Mermaid
writes labels as HTML in `foreignObject` (`div` > `span.nodeLabel|edgeLabel` > `p`, `br`), its stylesheet uses
`@keyframes`, and its output is not well-formed XML (`<br>` inside `<p>`), so `DOMParser("image/svg+xml")` rejects it.
A probe over every template, an activity diagram, icon kinds and hostile names (Mermaid 11.17.2, Chromium) found that
the allow-list, extended for labels, removes only an unused `filter`/`feDropShadow` definition and no attribute.

## Options

1. **Own label sub-sanitizer, inert template parse.** Parse with the HTML parser into a `<template>` (inert: no script
   runs, nothing loads, no handler fires), run `sanitizeSvg(root, {htmlLabels: true})`, which additionally keeps an SVG
   `foreignObject` holding only `div`/`span`/`p`/`br` in the XHTML namespace with `class`, a safe `style` and
   `xmlns` (XHTML), and adopt the sanitized `<svg>` (`importNode`). The DOM is never re-serialized and re-parsed, so
   there is no mutation-XSS round trip. `@keyframes` joins `@media` as an allowed at-rule. Cost: about 1.5–2 ms per
   render (median 2.5–2.9 ms for parse + sanitize + insert, against 0.9–1.1 ms for `innerHTML`), next to 60–160 ms of
   Mermaid layout. No dependency.
2. **Vendor DOMPurify** (SVG + HTML profiles). A maintained, broader sanitizer, but a new vendored dependency, larger
   than our need, and its defaults keep far more than Mermaid's label markup.
3. **`htmlLabels: false`** on the canvas (plain SVG `text`, as the export already does), so the existing allow-list
   suffices. Changes the look of every diagram (wrapping, line height, icon labels) and the e2e expectations.
4. **Keep the trusted sink.** No cost, but the only server-derived markup outside the allow-list stays.

## Decision

Option 1: defence in depth for the last unsanitized markup without a dependency or a visual change, at a measured
cost of about 2 ms per render.

## Consequences

- `tests/js/sinks.test.mjs`: no `trusted-html` sink is left; an inert parse (`template.innerHTML = …` marked
  `// inert-html:`) is allowed only in the listed file (`canvas.js`, 1) and only when `sanitizeSvg(` follows before
  the function returns. The export uses the same path (`renderOffscreen` returns the sanitized element).
- A Mermaid upgrade that writes other label markup (for example Markdown strings, which the server never emits) shows
  up as missing text or styling; the probe in the spec and the e2e check (label tags, no `filter`) catch it.
- The server-side escaping stays the first line of defence; the e2e hostile-name checks are unchanged.
