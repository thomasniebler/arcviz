"""The curated highlights of a release, shown as a banner in the UI (spec: docs/architecture/specs/whats-new.md).

`whatsnew.json` lists releases newest first; each has a `version` and `items` of `{emoji, text}` (German) with an optional `text_en`. No package imports.
"""

from __future__ import annotations

import json
import logging
import re
from importlib import resources
from typing import Any

log = logging.getLogger(__name__)

MAX_ITEMS = 6
MAX_TEXT = 200
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def problems(data: Any) -> list[str]:
    """Everything wrong with the document, empty when it is well formed and sorted newest first."""
    releases = data.get("releases") if isinstance(data, dict) else None
    if not isinstance(releases, list):
        return ["'releases' must be a list"]
    found: list[str] = []
    seen: list[tuple[int, ...]] = []
    for i, release in enumerate(releases):
        where = f"releases[{i}]"
        version = release.get("version") if isinstance(release, dict) else None
        if not isinstance(version, str) or not _VERSION.match(version):
            found.append(f"{where}: 'version' must look like 1.2.3")
            continue
        seen.append(version_key(version))
        items = release.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            found.append(f"{where}: 'items' must hold 1 to {MAX_ITEMS} entries")
            continue
        for j, item in enumerate(items):
            ok = isinstance(item, dict) and all(
                isinstance(item.get(k), str) and item[k].strip() for k in ("emoji", "text")
            )
            if not ok or len(item["text"]) > MAX_TEXT:
                found.append(f"{where}.items[{j}]: 'emoji' and 'text' (at most {MAX_TEXT} characters) are required")
                continue
            english = item.get("text_en")
            if "text_en" in item and (not isinstance(english, str) or not english.strip() or len(english) > MAX_TEXT):
                found.append(f"{where}.items[{j}]: 'text_en' must be a text of at most {MAX_TEXT} characters")
    if seen != sorted(seen, reverse=True) or len(set(seen)) != len(seen):
        found.append("releases must be unique and sorted newest first")
    return found


def load() -> dict[str, Any]:
    """The shipped document; an empty one when it is missing or malformed (never stops the start)."""
    try:
        data = json.loads(resources.files("arcviz").joinpath("whatsnew.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("whatsnew.json could not be read")
        return {"releases": []}
    if problems(data):
        log.warning("whatsnew.json is malformed")
        return {"releases": []}
    return data


def localized(release: dict[str, Any], language: str = "de") -> dict[str, Any]:
    """The release with plain `{emoji, text}` items; `text_en` replaces `text` for English when the item has one."""
    items = [
        {
            "emoji": item["emoji"],
            "text": item.get("text_en") if language == "en" and item.get("text_en") else item["text"],
        }
        for item in release["items"]
    ]
    return {"version": release["version"], "items": items}


def current(version: str, data: dict[str, Any] | None = None, language: str = "de") -> dict[str, Any] | None:
    """The newest release that is not newer than `version` (a patch release without highlights keeps the last ones),
    in `language` (`de` or `en`)."""
    try:
        wanted = version_key(version)
    except ValueError:
        return None
    for release in (data if data is not None else load())["releases"]:
        if version_key(release["version"]) <= wanted:
            return localized(release, language)
    return None
