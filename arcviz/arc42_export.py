"""Writers of the arc42 section tree: Markdown, AsciiDoc, the zip of both, docx (spec: docs/architecture/specs/arc42-export.md,
ADR-0207, ADR-0208).

Every text reaches a writer as plain text and is escaped by it; no writer passes markup through. A Mermaid figure is
written as its source (a fenced block), an SVG figure as a file in `figures/`. docx needs the `docx` extra and is
imported lazily.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from typing import Any

from . import render
from .arc42 import Bullets, Document, Figure, Heading, Paragraph, Section, Table
from .i18n import _
from .model import Diagram

MISSING_DOCX = 'The docx export needs the python-docx package: pip install "arcviz-agent[docx]".'

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|#~])")
_ADOC_SPECIAL = re.compile(r"([\\*_`#+\[\]<>|{}~^&])")


def md(text: str) -> str:
    """`text` with Markdown's special characters escaped (also safe inside a table cell)."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def adoc(text: str) -> str:
    """`text` with AsciiDoc's special characters written as character references (safe in text and table cells)."""
    return _ADOC_SPECIAL.sub(lambda m: f"&#{ord(m.group(1))};", " ".join(text.split()))


def _depth(item: str) -> int:
    return (len(item) - len(item.lstrip(" "))) // 2


def figure_of(diagram: Diagram) -> tuple[str, str]:
    """`("mermaid", source)` or `("svg", markup)` of a diagram's rendering."""
    payload = render.payload(diagram)
    kind = str(payload["renderer"])
    return kind, str(payload[kind])


def _fence(source: str, char: str, least: int) -> str:
    """A fence of `char` longer than any run of it in `source`."""
    longest = max((len(m.group(0)) for m in re.finditer(re.escape(char) + "+", source)), default=0)
    return char * max(least, longest + 1)


def _numbered(section: Section) -> str:
    return f"{int(section.id)} {section.title}"


# ---- Markdown ------------------------------------------------------------------------------------------------------


def markdown_section(section: Section, doc: Document, figure_link: Callable[[str], str]) -> str:
    """One section as Markdown (`## n Title` and its blocks)."""
    out = [f"## {md(_numbered(section))}", ""]
    for block in section.blocks:
        out += _md_block(block, doc, figure_link)
    out += [f"*{md(g)}*" + "\n" for g in section.gaps]
    return "\n".join(out).rstrip() + "\n"


def to_markdown(doc: Document, figure_link: Callable[[str], str] = lambda d: f"figures/{d}.svg") -> str:
    """The document as Markdown: `# title`, `## n Section`; SVG figures link to `figure_link(diagram id)`."""
    parts = [f"# {md(doc.title)}\n", *(markdown_section(s, doc, figure_link) for s in doc.sections)]
    return "\n".join(parts).rstrip() + "\n"


def _md_block(block: Any, doc: Document, figure_link: Callable[[str], str]) -> list[str]:
    if isinstance(block, Paragraph):
        return [md(block.text), ""]
    if isinstance(block, Heading):
        return [f"{'#' * min(6, block.level)} {md(block.text)}", ""]
    if isinstance(block, Bullets):
        return [f"{'  ' * _depth(i)}- {md(i)}" for i in block.items] + [""]
    if isinstance(block, Table):
        head = "| " + " | ".join(md(h) for h in block.headers) + " |"
        rule = "|" + "|".join(" --- " for _h in block.headers) + "|"
        return [head, rule, *("| " + " | ".join(md(c) for c in row) + " |" for row in block.rows), ""]
    if isinstance(block, Figure):
        diagram = doc.diagrams[block.diagram]
        kind, source = figure_of(diagram)
        title = f"**{md(block.title)}**"
        if kind == "mermaid":
            fence = _fence(source, "`", 3)
            return [title, "", f"{fence}mermaid", source, fence, ""]
        return [title, "", f"![{md(block.title)}]({figure_link(block.diagram)})", ""]
    raise TypeError(f"unknown block {type(block).__name__}")


# ---- AsciiDoc ------------------------------------------------------------------------------------------------------


def to_asciidoc(doc: Document) -> str:
    """The document as AsciiDoc: `= title`, `== n Section`; SVG figures are `image::figures/<id>.svg[]`."""
    out = [f"= {adoc(doc.title)}", ":toc:", ":sectnums!:", ""]
    for section in doc.sections:
        out += [f"== {adoc(_numbered(section))}", ""]
        for block in section.blocks:
            out += _adoc_block(block, doc)
        out += [f"_{adoc(g)}_" + "\n" for g in section.gaps]
    return "\n".join(out).rstrip() + "\n"


def _adoc_block(block: Any, doc: Document) -> list[str]:
    if isinstance(block, Paragraph):
        return [adoc(block.text), ""]
    if isinstance(block, Heading):
        return [f"{'=' * (block.level)} {adoc(block.text)}", ""]
    if isinstance(block, Bullets):
        return [f"{'*' * (_depth(i) + 1)} {adoc(i)}" for i in block.items] + [""]
    if isinstance(block, Table):
        cols = ",".join("1" for _h in block.headers)
        rows = [f"| {adoc(c)}" for row in block.rows for c in row]
        head = [f"| {adoc(h)}" for h in block.headers]
        return [f'[cols="{cols}",options="header"]', "|===", *head, "", *rows, "|===", ""]
    if isinstance(block, Figure):
        diagram = doc.diagrams[block.diagram]
        kind, source = figure_of(diagram)
        if kind == "mermaid":
            fence = _fence(source, "-", 4)
            return [f".{adoc(block.title)}", "[source,mermaid]", fence, source, fence, ""]
        return [f".{adoc(block.title)}", f"image::figures/{block.diagram}.svg[{adoc(block.title)}]", ""]
    raise TypeError(f"unknown block {type(block).__name__}")


# ---- files and the zip ---------------------------------------------------------------------------------------------


def svg_figures(doc: Document) -> dict[str, str]:
    """`figures/<id>.svg` -> markup, for every SVG-rendered figure of the document."""
    files: dict[str, str] = {}
    for block in (b for s in doc.sections for b in s.blocks if isinstance(b, Figure)):
        kind, source = figure_of(doc.diagrams[block.diagram])
        if kind == "svg":
            files[f"figures/{block.diagram}.svg"] = source
    return files


def to_zip(doc: Document, fmt: str) -> bytes:
    """The zip of the document (`fmt`: `asciidoc` or `markdown`): the index file, `figures/` for SVG figures."""
    if fmt not in ("asciidoc", "markdown"):
        raise ValueError(f"Unknown arc42 format '{fmt}'. Use asciidoc or markdown.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if fmt == "asciidoc":
            archive.writestr("arc42/index.adoc", to_asciidoc(doc))
        else:
            archive.writestr("arc42/index.md", to_markdown(doc))
        for name, svg in svg_figures(doc).items():
            archive.writestr(f"arc42/{name}", svg)
    return buffer.getvalue()


# ---- docx ----------------------------------------------------------------------------------------------------------


def to_docx(doc: Document) -> bytes:
    """The document as a Word file; Mermaid figures appear as their source in a monospaced paragraph."""
    try:
        from docx import Document as WordDocument
        from docx.shared import Pt
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] != "docx":
            raise
        raise RuntimeError(MISSING_DOCX) from exc
    word = WordDocument()
    word.add_heading(doc.title, 0)
    for section in doc.sections:
        word.add_heading(_numbered(section), 1)
        for block in section.blocks:
            if isinstance(block, Paragraph):
                word.add_paragraph(block.text)
            elif isinstance(block, Heading):
                word.add_heading(block.text, min(4, max(2, block.level - 1)))
            elif isinstance(block, Bullets):
                for item in block.items:
                    word.add_paragraph(item.strip(), style="List Bullet")
            elif isinstance(block, Table):
                table = word.add_table(rows=1, cols=len(block.headers))
                table.style = "Table Grid"
                for cell, text in zip(table.rows[0].cells, block.headers, strict=True):
                    cell.text = text
                for row in block.rows:
                    for cell, text in zip(table.add_row().cells, row, strict=True):
                        cell.text = text
                word.add_paragraph()
            elif isinstance(block, Figure):
                kind, source = figure_of(doc.diagrams[block.diagram])
                word.add_paragraph(block.title).runs[0].bold = True
                note = _("Diagrammquelle ({kind})", kind="Mermaid" if kind == "mermaid" else "SVG")
                word.add_paragraph(note)
                code = word.add_paragraph()
                run = code.add_run(source if kind == "mermaid" else _("(als SVG-Datei im Zip enthalten)"))
                run.font.name = "Courier New"
                run.font.size = Pt(8)
        for gap in section.gaps:
            word.add_paragraph(gap).runs[0].italic = True
    buffer = io.BytesIO()
    word.save(buffer)
    return buffer.getvalue()


def to_json(doc: Document, figure_link: Callable[[str], str]) -> dict[str, Any]:
    """The document for the JSON route: per section its Markdown and gaps, and the figures' diagram ids."""
    return {
        "project": doc.project,
        "title": doc.title,
        "sections": [
            {
                "id": s.id,
                "title": s.title,
                "markdown": markdown_section(s, doc, figure_link),
                "gaps": s.gaps,
                "figures": [b.diagram for b in s.blocks if isinstance(b, Figure)],
            }
            for s in doc.sections
        ],
    }
