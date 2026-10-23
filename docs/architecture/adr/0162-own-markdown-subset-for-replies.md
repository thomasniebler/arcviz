# ADR-0162: Assistant replies are rendered by an own Markdown subset, built with DOM APIs

- Status: Accepted
- Date: 2026-10-12
- Spec: `docs/architecture/specs/chat-markdown.md`

## Context

Assistant replies are Markdown more often than not and were shown as raw text. The frontend has no build step,
vendors every third-party file, and admits no HTML sink for server- or model-derived text (`tests/js/sinks.test.mjs`,
#119, #197): model output can carry anything a prompt injection put into a diagram or a document.

## Options

1. **Vendor a Markdown library plus an HTML sanitizer** (e.g. marked + DOMPurify). Full GFM, about 60 kB of vendored
   code to keep current, and the output is an HTML string, so a reviewed `innerHTML` sink and a second sanitizer
   configuration next to `svgsafe.js`.
2. **Own subset that produces a syntax tree, rendered with DOM APIs.** About 250 lines, only what chat replies use; no
   HTML is ever parsed, so there is nothing to sanitize and no sink to review.
3. **Keep plain text.** Safe, but the replies stay hard to read.

## Decision

Option 2: the subset covers what the models write in a chat, and building elements from a tree keeps the
no-sink rule without exceptions.

## Consequences

- Constructs outside the subset (images, HTML, reference links) render as text; adding one is a change to
  `markdown.js` and its tests.
- Speech output reads `markdown.plainText`, so it no longer reads asterisks aloud.
