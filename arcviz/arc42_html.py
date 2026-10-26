"""The live arc42 page: the section tree as HTML (spec: docs/architecture/specs/arc42-export.md, ADR-0207).

Every text goes through `html.escape`; the page holds no inline script. A Mermaid figure is a `<pre class="mermaid">` with
the escaped source (the vendored Mermaid in `/static/vendor` draws it, `/static/arc42/page.js` starts it), an SVG figure an
`<img>` of the figure route, so a markup defect in a rendering can never run in the page.
"""

from __future__ import annotations

from html import escape
from typing import Any

from .arc42 import Bullets, Document, Figure, Heading, Paragraph, Table
from .arc42_export import figure_of
from .i18n import _

# The page may load only what the app serves itself; Mermaid sets inline styles on its SVG.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _depth(item: str) -> int:
    return min(6, (len(item) - len(item.lstrip(" "))) // 2)


def _block(block: Any, doc: Document, figure_url: str, lang: str) -> str:
    if isinstance(block, Paragraph):
        return f"<p>{escape(block.text)}</p>"
    if isinstance(block, Heading):
        level = min(6, max(3, block.level))
        return f"<h{level}>{escape(block.text)}</h{level}>"
    if isinstance(block, Bullets):
        items = "".join(f'<li class="d{_depth(i)}">{escape(i.strip())}</li>' for i in block.items)
        return f"<ul>{items}</ul>"
    if isinstance(block, Table):
        head = "".join(f"<th>{escape(h)}</th>" for h in block.headers)
        rows = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in block.rows)
        return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
    if isinstance(block, Figure):
        kind, source = figure_of(doc.diagrams[block.diagram])
        title = f"<figcaption>{escape(block.title)}</figcaption>"
        if kind == "mermaid":
            return f'<figure><pre class="mermaid">{escape(source)}</pre>{title}</figure>'
        url = f"{figure_url}/{escape(block.diagram)}.svg"
        return f'<figure><img src="{url}" alt="{escape(block.title)}">{title}</figure>'
    raise TypeError(f"unknown block {type(block).__name__}")


def page(
    doc: Document,
    *,
    figure_url: str,
    lang: str = "de",
    downloads: dict[str, str] | None = None,
    only: str | None = None,
) -> str:
    """The whole page, or only section `only`. `figure_url` is the base of the SVG figure route, `downloads` maps a
    label to a URL."""
    chosen = [s for s in doc.sections if only in (None, s.id)]
    nav = "".join(f'<li><a href="#s{s.id}">{int(s.id)} {escape(s.title)}</a></li>' for s in doc.sections)
    links = "".join(f'<a href="{escape(url)}">{escape(label)}</a>' for label, url in (downloads or {}).items())
    sections = []
    for s in chosen:
        body = "".join(_block(b, doc, figure_url, lang) for b in s.blocks)
        gaps = "".join(f'<p class="gap">{escape(g)}</p>' for g in s.gaps)
        sections.append(f'<section id="s{s.id}"><h2>{int(s.id)} {escape(s.title)}</h2>{body}{gaps}</section>')
    title = escape(doc.title)
    return (
        f'<!doctype html><html lang="{escape(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>arc42: {title}</title>"
        '<link rel="stylesheet" href="/static/arc42/page.css"></head><body>'
        f'<header><h1>{title}</h1><p class="sub">{escape(_("arc42-Dokumentation, live aus dem Projektmodell"))}</p>'
        f'<nav class="downloads">{links}</nav></header>'
        f'<div class="layout"><nav class="toc"><ol>{nav}</ol></nav><main>{"".join(sections)}</main></div>'
        '<script src="/static/vendor/mermaid.min.js"></script><script src="/static/arc42/page.js"></script>'
        "</body></html>"
    )
