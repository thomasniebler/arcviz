// Markdown of the assistant's replies as a small syntax tree (spec: docs/architecture/specs/chat-markdown.md,
// ADR-0162). Pure module: no DOM; `chatlog.js` turns the tree into elements with DOM APIs only, so no reply text is
// ever parsed as HTML. The subset is what chat replies use: headings, paragraphs (a single line break stays a
// break), bullet and numbered lists (nested by indentation), block quotes, fenced code, tables, rules; inline code,
// strong, emphasis, strikethrough, links and bare URLs. Anything else stays text. An unclosed fence or delimiter
// (a reply still streaming in) never fails: the fence runs to the end, the delimiter stays a literal character.

/** Link targets that may become links; everything else stays text. */
export function safeHref(url) {
  const href = String(url || "").trim();
  return /^(https?:\/\/|mailto:)/i.test(href) ? href : null;
}

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

const indentOf = (line) => line.match(/^\s*/)[0].replace(/\t/g, "    ").length;
const blank = (line) => !line.trim();

function cells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function startsBlock(lines, i) {
  const line = lines[i];
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line)
    || (line.includes("|") && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1]) && lines[i + 1].includes("-"));
}

/** Remove `n` columns of indentation (tabs count four). */
function dedent(line, n) {
  let col = 0;
  let k = 0;
  while (k < line.length && col < n && (line[k] === " " || line[k] === "\t")) { col += line[k] === "\t" ? 4 : 1; k++; }
  return line.slice(k);
}

function parseList(lines, start) {
  const first = lines[start].match(ITEM);
  const base = indentOf(first[1]);
  const ordered = /\d/.test(first[2]);
  const list = { type: "list", ordered, start: ordered ? Number.parseInt(first[2], 10) : 1, items: [] };
  let i = start;
  while (i < lines.length) {
    const m = lines[i].match(ITEM);
    if (!m || indentOf(m[1]) !== base || /\d/.test(m[2]) !== ordered) break;
    const contentIndent = m[1].length + m[2].length + 1;
    const body = [m[3]];
    i++;
    // the item's own lines: anything indented beyond the marker, blank lines between them, and lazy continuations
    while (i < lines.length) {
      const line = lines[i];
      if (blank(line)) {
        const next = lines.slice(i + 1).find((l) => !blank(l));
        if (next !== undefined && indentOf(next) > base) { body.push(""); i++; continue; }
        break;
      }
      if (indentOf(line) > base) { body.push(dedent(line, Math.min(contentIndent, indentOf(line)))); i++; continue; }
      if (!startsBlock(lines, i) && !blank(lines[i - 1])) { body.push(line.trim()); i++; continue; } // lazy line
      break;
    }
    list.items.push(parseBlocks(body));
    // one blank line between items of the same list keeps the list going
    if (i < lines.length && blank(lines[i])) {
      const k = lines.findIndex((l, j) => j > i && !blank(l));
      const next = k === -1 ? null : lines[k].match(ITEM);
      if (next && indentOf(next[1]) === base && /\d/.test(next[2]) === ordered) i = k;
    }
  }
  return [list, i];
}

/** Block tree of `text` (or of the lines of a quote or list item). */
export function parseBlocks(input) {
  const lines = Array.isArray(input) ? input : String(input ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (blank(line)) { i++; continue; }
    let m;
    if ((m = line.match(FENCE))) {
      const fence = m[1];
      const body = [];
      i++;
      while (i < lines.length && !(lines[i].trim().startsWith(fence[0].repeat(fence.length)) && /^[`~]+$/.test(lines[i].trim()))) body.push(lines[i++]);
      i++; // the closing fence (or past the end while it is still streaming)
      blocks.push({ type: "code", lang: m[2] || "", text: body.join("\n") });
      continue;
    }
    if ((m = line.match(HEADING))) { blocks.push({ type: "heading", level: m[1].length, children: parseInline(m[2]) }); i++; continue; }
    if (RULE.test(line)) { blocks.push({ type: "rule" }); i++; continue; }
    if (QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && !blank(lines[i]) && (QUOTE.test(lines[i]) || !startsBlock(lines, i))) {
        const q = lines[i].match(QUOTE);
        body.push(q ? q[1] : lines[i]);
        i++;
      }
      blocks.push({ type: "quote", children: parseBlocks(body) });
      continue;
    }
    if (ITEM.test(line)) { const [list, next] = parseList(lines, i); blocks.push(list); i = next; continue; }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const header = cells(line);
      const align = cells(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : ""));
      const rows = [];
      i += 2;
      while (i < lines.length && !blank(lines[i]) && lines[i].includes("|")) rows.push(cells(lines[i++]));
      const width = header.length;
      const fit = (row) => Array.from({ length: width }, (_, k) => parseInline(row[k] ?? ""));
      blocks.push({ type: "table", align: align.slice(0, width), header: fit(header), rows: rows.map(fit) });
      continue;
    }
    const body = [line.trim()];
    i++;
    while (i < lines.length && !blank(lines[i]) && !startsBlock(lines, i)) body.push(lines[i++].trim());
    blocks.push({ type: "paragraph", children: parseInline(body.join("\n")) });
  }
  return blocks;
}

const URL_RE = /https?:\/\/[^\s<>"]*[^\s<>".,:;!?'")\]]/y;
const ESCAPABLE = "\\`*_{}[]()#+-.!|~>";

/** Inline nodes: text, br, code, strong, em, del, link. */
export function parseInline(text) {
  const out = [];
  let buf = "";
  const flush = () => { if (buf) { out.push({ type: "text", text: buf }); buf = ""; } };
  const s = String(text ?? "");
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c === "\\" && i + 1 < s.length && ESCAPABLE.includes(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
    if (c === "\n") { flush(); out.push({ type: "br" }); i++; continue; }
    if (c === "`") {
      const run = s.slice(i).match(/^`+/)[0];
      const end = s.indexOf(run, i + run.length);
      if (end !== -1) {
        flush();
        out.push({ type: "code", text: s.slice(i + run.length, end).replace(/\n/g, " ").replace(/^ (.*) $/, "$1") });
        i = end + run.length;
        continue;
      }
      buf += run; i += run.length; continue;
    }
    if (c === "[") {
      const close = matchingBracket(s, i);
      if (close !== -1 && s[close + 1] === "(") {
        const paren = closingParen(s, close + 1);
        if (paren !== -1) {
          const [url] = s.slice(close + 2, paren).trim().split(/\s+/);
          const href = safeHref(url.replace(/^<(.*)>$/, "$1"));
          flush();
          const children = parseInline(s.slice(i + 1, close));
          if (href) out.push({ type: "link", href, children });
          else out.push(...children); // an unsafe target: the text stays, the link goes
          i = paren + 1;
          continue;
        }
      }
    }
    if (c === "h" && (i === 0 || /[\s(]/.test(s[i - 1]))) {
      URL_RE.lastIndex = i;
      const m = URL_RE.exec(s);
      if (m) { flush(); out.push({ type: "link", href: m[0], children: [{ type: "text", text: m[0] }] }); i += m[0].length; continue; }
    }
    const span = delimited(s, i);
    if (span) { flush(); out.push({ type: span.type, children: parseInline(span.inner) }); i = span.end; continue; }
    buf += c;
    i++;
  }
  flush();
  return out;
}

/** The `)` that closes the `(` at `open`, counting nested parentheses (`[x](https://a.b/f(1))`). */
function closingParen(s, open) {
  let depth = 0;
  for (let k = open; k < s.length; k++) {
    if (s[k] === "\\") { k++; continue; }
    if (s[k] === "(") depth++;
    else if (s[k] === ")" && --depth === 0) return k;
    else if (s[k] === "\n") return -1;
  }
  return -1;
}

function matchingBracket(s, open) {
  let depth = 0;
  for (let k = open; k < s.length; k++) {
    if (s[k] === "\\") { k++; continue; }
    if (s[k] === "[") depth++;
    else if (s[k] === "]" && --depth === 0) return k;
  }
  return -1;
}

const word = (ch) => ch !== undefined && /[\p{L}\p{N}]/u.test(ch);

/** A strong/em/del span opening at `i`: `**x**`, `__x__`, `*x*`, `_x_`, `~~x~~`. Intraword `_` never counts. */
function delimited(s, i) {
  for (const [mark, type] of [["**", "strong"], ["__", "strong"], ["~~", "del"], ["*", "em"], ["_", "em"]]) {
    if (!s.startsWith(mark, i)) continue;
    const after = s[i + mark.length];
    if (after === undefined || /\s/.test(after)) continue;
    if (mark[0] === "_" && word(s[i - 1])) continue;
    let from = i + mark.length + 1;
    while (from <= s.length) {
      const end = s.indexOf(mark, from);
      if (end === -1) break;
      const ok = !/\s/.test(s[end - 1]) && s[end - 1] !== "\\" && !(mark[0] === "_" && word(s[end + mark.length]))
        && !(mark.length === 1 && s[end + 1] === mark); // `*a**` is not an em closed by the first star of `**`
      if (ok) return { type, inner: s.slice(i + mark.length, end), end: end + mark.length };
      from = end + 1;
    }
  }
  return null;
}

function inlineText(nodes) {
  return nodes.map((n) => (n.type === "br" ? "\n" : n.type === "text" || n.type === "code" ? n.text : inlineText(n.children || []))).join("");
}

/** The reply as plain text without Markdown marks (what speech output reads); code blocks are left out. */
export function plainText(text) {
  const walk = (blocks) => blocks.map((b) => {
    if (b.type === "paragraph" || b.type === "heading") return inlineText(b.children);
    if (b.type === "quote") return walk(b.children);
    if (b.type === "list") return b.items.map((item) => walk(item)).join("\n");
    if (b.type === "table") return [b.header, ...b.rows].map((r) => r.map(inlineText).join(", ")).join("\n");
    return "";
  }).filter(Boolean).join("\n");
  return walk(parseBlocks(text));
}
