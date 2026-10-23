# Markdown in assistant replies

- Status: Implemented
- Issue: #223
- ADR: [0162 Assistant replies are rendered by an own Markdown subset, built with DOM APIs](../adr/0162-own-markdown-subset-for-replies.md)
- Related: [Reply streaming](reply-streaming.md), [Hardening](hardening.md) (no unsafe HTML sinks), [Frontend modules](frontend-modules.md)

## Summary

The models answer in Markdown: lists of what changed, bold names, inline code for ids, sometimes a table. The chat
showed that text verbatim (`white-space: pre-wrap`), with asterisks, hashes and pipes. Assistant replies and chat
summaries are now rendered as a Markdown subset. A pure module parses the text into a small syntax tree; `chatlog.js`
builds the elements from the tree with DOM APIs, so no reply text is ever interpreted as HTML.

## Goals

- The constructs chat replies use render: headings, paragraphs, line breaks, bullet and numbered lists (nested),
  block quotes, fenced code, tables, horizontal rules; inline code, strong, emphasis, strikethrough, links, bare URLs.
- Safe by construction: no new HTML sink (`tests/js/sinks.test.mjs` stays as it is), links only to `http:`, `https:`
  and `mailto:`, raw HTML stays text.
- Streaming keeps working: a half-written reply (an unclosed `**` or code fence) renders without errors and settles
  when the rest arrives.
- No runtime dependency and no build step.

## Non-goals

- Full CommonMark/GFM conformance (reference links, HTML blocks, images, footnotes, task lists, setext headings).
- Markdown in user messages, tool lines or error bubbles: they stay plain text.
- Syntax highlighting of code blocks.

## Design

### `static/markdown.js` (pure)

- `parseBlocks(text | lines)` → blocks: `paragraph {children}`, `heading {level, children}`, `rule`,
  `quote {children: blocks}`, `code {lang, text}`, `list {ordered, start, items: blocks[]}`,
  `table {align[], header: inline[][], rows: inline[][][]}` (rows padded or cut to the header's width).
- `parseInline(text)` → inline nodes: `text`, `br` (every single line break inside a paragraph; chat replies mean it),
  `code`, `strong`, `em`, `del`, `link {href, children}`. Backslash escapes; `_` never counts inside a word
  (`snake_case_ids` stay intact); an unmatched delimiter stays a literal character.
- `safeHref(url)` → the URL for `http(s):`/`mailto:`, else `null`; a link with another target keeps only its text.
- `plainText(text)` → the reply without marks (code blocks left out); speech output reads that.
- List items nest by indentation (deeper than the marker); one blank line between items keeps one list; a line that is
  not indented and starts no block continues the item (lazy continuation).
- An unclosed fence runs to the end of the text.

### `chatlog.js` (view)

- `setRichText(div, text)` replaces the bubble's content with `<div class="md">` built from the tree:
  `p`, `h3`…`h6` (`#` is `h3`: a bubble is no page), `hr`, `blockquote`, `pre > code[data-lang]`, `ul`/`ol[start]`,
  `div.md-table > table`, `code`, `strong`, `em`, `del`, `a[target=_blank][rel="noopener noreferrer"]`. Text only via
  text nodes. A tight list item shows its first paragraph without a `<p>`.
- `addMessage("assistant", …)` and the summaries of `addSummary` use it; `chat.js` uses it for `text_delta`, `text`
  and the `final` reply (re-rendered on every delta; a reply is a few kB).

### Styles

`.md` in `style.css`: compact margins, code and tables on `--bg`, quotes with a left border, links in `--accent`; no
skin-specific rules (skins restyle through the tokens).

### Prompt

`prompts/shared/reply.txt` allows Markdown (short lists, bold, inline code for ids, small tables) and forbids HTML.

## Testing

- `tests/js/markdown.test.mjs`: every construct, escapes and identifiers, unsafe links, HTML as text, streaming
  (unclosed marks and fences), tables with alignment and short rows, plain text for speech.
- `tests/js/modules.test.mjs`: `markdown.js` is pure; `sinks.test.mjs` unchanged.
- The e2e smoke test still finds the scripted reply text in the bubble (`textContent` of a plain reply is unchanged).

## Migration and compatibility

Rendering only: stored transcripts are unchanged and render as Markdown when shown again.

## Decisions

- Own subset instead of a vendored library (ADR-0162).
- Single line breaks are breaks (chat style), not spaces (CommonMark).
