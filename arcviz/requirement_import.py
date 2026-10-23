"""Pour a list of requirements in: plain lines, Markdown lists and CSV, parsed without a model.

Spec: docs/architecture/specs/requirements-workshop.md (*Import*), ADR-0192. Pure: no store, no services, no LLM. The
result is a list of items `{raw, external_id?, source?, theme?, priority?, stakeholder_names?}` that the requirement service
adds (idempotently) and the problems of the rows that could not be read. Nothing here changes a requirement.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from .model import single_line
from .requirement_model import MAX_EXTERNAL_ID, MAX_RAW, MAX_SOURCE, MAX_THEME

MAX_ITEMS = 500
MAX_TEXT_CHARS = 1_000_000
FORMATS = ("auto", "lines", "markdown", "csv")

_BULLET = re.compile(r"^\s*(?:[-*+•–—]|\d{1,4}[.)]|\(\d{1,4}\))\s+")
_ID_PREFIX = re.compile(r"^\s*([A-Za-z]{1,8}[-_ ]?\d{1,6})\s*[:.)\-–]\s+")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_CONTINUATION = re.compile(r"^(?:\t|\s{2,})\S")

TEXT_HEADERS = ("text", "requirement", "requirements", "anforderung", "beschreibung", "description", "raw", "aussage")
COLUMNS = {
    "external_id": ("id", "key", "external_id", "nr", "nummer", "ticket", "req"),
    "source": ("source", "quelle"),
    "theme": ("theme", "thema", "kategorie", "category", "gruppe", "group"),
    "priority": ("priority", "priorität", "prioritaet", "prio"),
    "stakeholder_names": ("stakeholder", "stakeholders"),
}
PRIORITIES = {
    "must": "must",
    "muss": "must",
    "m": "must",
    "high": "must",
    "hoch": "must",
    "should": "should",
    "soll": "should",
    "s": "should",
    "medium": "should",
    "mittel": "should",
    "could": "could",
    "kann": "could",
    "c": "could",
    "low": "could",
    "niedrig": "could",
    "wont": "wont",
    "won't": "wont",
    "will not": "wont",
    "w": "wont",
    "entfällt": "wont",
}


@dataclass
class ParseResult:
    format: str
    items: list[dict[str, Any]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)  # {row, reason}


def parse(text: str, fmt: str = "auto", *, source: str = "", max_items: int = MAX_ITEMS) -> ParseResult:
    """The requirements in `text`. `fmt`: `lines`, `markdown`, `csv` or `auto`. Never raises for a bad row: it becomes a
    problem with its row number (a line number, or the data row of a CSV, counting from 1)."""
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format '{fmt}'. Available: {', '.join(FORMATS)}.")
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    chosen = detect(text) if fmt == "auto" else fmt
    parsers = {"lines": _lines, "markdown": _markdown, "csv": _csv}
    result = parsers[chosen](text)
    result.format = chosen
    return _finish(result, source, max_items)


def detect(text: str) -> str:
    """`csv` when the first line is a header with a text column, `markdown` when the text has a heading (it becomes the
    theme), else `lines` (bullets and numbers are stripped there)."""
    first = next((line for line in text.split("\n") if line.strip()), "")
    delimiter = _delimiter(first)
    if delimiter and _text_column([c.strip().casefold() for c in first.split(delimiter)]) is not None:
        return "csv"
    return "markdown" if any(_HEADING.match(line) for line in text.split("\n")) else "lines"


# ---- the formats ---------------------------------------------------------------------------------------------------


def _clean(line: str) -> str:
    return " ".join(single_line(line).split())


def _lines(text: str) -> ParseResult:
    result = ParseResult("lines")
    current: dict[str, Any] | None = None
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            current = None
            continue
        if current is not None and _CONTINUATION.match(line) and not _BULLET.match(line):
            current["raw"] += " " + _clean(line)
            continue
        item = _entry(line)
        item["row"] = number
        if not item["raw"]:
            result.problems.append({"row": number, "reason": "empty after removing the bullet"})
            current = None
            continue
        result.items.append(item)
        current = item
    return result


def _entry(line: str) -> dict[str, Any]:
    """One line as an item: a bullet, a number or an id prefix such as `REQ-12:` removed (the prefix is kept as the id)."""
    external = ""
    body = line
    if match := _ID_PREFIX.match(body):
        external = match.group(1).replace(" ", "-")
        body = body[match.end() :]
    else:
        body = _BULLET.sub("", body, count=1)
        if match := _ID_PREFIX.match(body):
            external = match.group(1).replace(" ", "-")
            body = body[match.end() :]
    item: dict[str, Any] = {"raw": _clean(body)}
    if external:
        item["external_id"] = external
    return item


def _markdown(text: str) -> ParseResult:
    result = ParseResult("markdown")
    lines = text.split("\n")
    has_list = any(re.match(r"^\s*(?:[-*+]|\d{1,4}[.)])\s+\S", ln) for ln in lines)
    theme = ""
    current: dict[str, Any] | None = None
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            current = None
            continue
        if heading := _HEADING.match(line):
            theme = _clean(heading.group(2))
            current = None
            continue
        is_item = bool(re.match(r"^\s*(?:[-*+]|\d{1,4}[.)])\s+\S", line))
        if current is not None and not is_item and _CONTINUATION.match(line):
            current["raw"] += " " + _clean(line)
            continue
        if has_list and not is_item:
            current = None  # prose between lists is not a requirement
            continue
        item = _entry(line)
        item["row"] = number
        if theme:
            item["theme"] = theme
        if not item["raw"]:
            result.problems.append({"row": number, "reason": "empty after removing the bullet"})
            current = None
            continue
        result.items.append(item)
        current = item
    return result


def _delimiter(line: str) -> str | None:
    counts = {d: line.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] >= 1 else None


def _text_column(headers: list[str]) -> int | None:
    for name in TEXT_HEADERS:
        if name in headers:
            return headers.index(name)
    return None


def _csv(text: str) -> ParseResult:
    result = ParseResult("csv")
    first = next((line for line in text.split("\n") if line.strip()), "")
    delimiter = _delimiter(first) or ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return result
    headers = [c.strip().casefold() for c in rows[0]]
    text_at = _text_column(headers)
    if text_at is None:
        result.problems.append(
            {"row": 1, "reason": f"no text column: the header needs one of {', '.join(TEXT_HEADERS[:5])}"}
        )
        return result
    columns = {key: next((headers.index(h) for h in names if h in headers), None) for key, names in COLUMNS.items()}
    for number, row in enumerate(rows[1:], start=2):
        cell = row[text_at].strip() if text_at < len(row) else ""
        if not cell:
            result.problems.append({"row": number, "reason": "empty text"})
            continue
        item: dict[str, Any] = {"raw": _clean(cell), "row": number}
        for key, at in columns.items():
            value = row[at].strip() if at is not None and at < len(row) else ""
            if not value:
                continue
            if key == "priority":
                mapped = PRIORITIES.get(value.casefold())
                if mapped is None:
                    result.problems.append({"row": number, "reason": f"unknown priority '{value}' (left out)"})
                else:
                    item["priority"] = mapped
            elif key == "stakeholder_names":
                item[key] = [n.strip() for n in re.split(r"[;,/|]", value) if n.strip()]
            else:
                item[key] = _clean(value)
        result.items.append(item)
    return result


@dataclass
class Prepared:
    """Items ready for `RequirementService.import_items`: `rows[i]` is the source row of `items[i]`."""

    items: list[dict[str, Any]]
    rows: list[int]
    problems: list[dict[str, Any]]


def prepare(items: list[dict[str, Any]], stakeholders: dict[str, str]) -> Prepared:
    """Turn parsed items into `add_requirement` arguments: stakeholder names become ids (`stakeholders` maps a casefolded
    name to its id; an unknown name is a problem and left out, never created), `row` is kept aside."""
    out: list[dict[str, Any]] = []
    rows: list[int] = []
    problems: list[dict[str, Any]] = []
    for item in items:
        args = {k: v for k, v in item.items() if k not in ("row", "stakeholder_names")}
        names = item.get("stakeholder_names") or []
        ids = [stakeholders[n.casefold()] for n in names if n.casefold() in stakeholders]
        for name in names:
            if name.casefold() not in stakeholders:
                problems.append({"row": item.get("row", 0), "reason": f"unknown stakeholder '{name}' (left out)"})
        if ids:
            args["stakeholders"] = list(dict.fromkeys(ids))
        out.append(args)
        rows.append(item.get("row", 0))
    return Prepared(out, rows, problems)


# ---- bounds --------------------------------------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _finish(result: ParseResult, source: str, max_items: int) -> ParseResult:
    """Apply the bounds of a requirement: long texts are shortened (and reported), more than `max_items` are left out."""
    if len(result.items) > max_items:
        result.problems.append(
            {
                "row": result.items[max_items].get("row", 0),
                "reason": f"more than {max_items} requirements: the rest is left out",
            }
        )
        result.items = result.items[:max_items]
    for item in result.items:
        if len(item["raw"]) > MAX_RAW:
            result.problems.append(
                {"row": item.get("row", 0), "reason": f"longer than {MAX_RAW} characters: shortened"}
            )
            item["raw"] = _cut(item["raw"], MAX_RAW)
        for key, limit in (("external_id", MAX_EXTERNAL_ID), ("source", MAX_SOURCE), ("theme", MAX_THEME)):
            if key in item:
                item[key] = _cut(item[key], limit)
        if source and not item.get("source"):
            item["source"] = _cut(source, MAX_SOURCE)
    return result
