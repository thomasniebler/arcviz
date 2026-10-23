"""Server-side internationalization: German source texts, English catalogs (ADR-0199).

Spec: docs/architecture/specs/internationalization.md. No package imports. The language of a request comes from its
`Accept-Language` header (`LanguageMiddleware`); without one it is German, which is also what every text in the code is
written in. `_("Deutscher Text {x}", x=1)` returns the English entry of the German text under `en`, else the text.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

LANGUAGES = ("de", "en")
DEFAULT_LANGUAGE = "de"
LANGUAGE_NAMES = {"de": "German", "en": "English"}
CATALOG_DIR = Path(__file__).parent / "locales" / "en"

_current: ContextVar[str] = ContextVar("arcviz_language", default=DEFAULT_LANGUAGE)
_catalog: dict[str, str] | None = None


def load_catalog(directory: Path = CATALOG_DIR) -> dict[str, str]:
    """Every `*.json` file of the directory merged (German text -> English text)."""
    merged: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def catalog() -> dict[str, str]:
    global _catalog
    if _catalog is None:
        _catalog = load_catalog()
    return _catalog


def language() -> str:
    return _current.get()


def language_name() -> str:
    """The current language as a word for prompts ("German" or "English")."""
    return LANGUAGE_NAMES[_current.get()]


@contextmanager
def use(lang: str) -> Iterator[None]:
    """Run a block in a language (tests, background work that outlives its request)."""
    token = _current.set(lang if lang in LANGUAGES else DEFAULT_LANGUAGE)
    try:
        yield
    finally:
        _current.reset(token)


def _(text: str, **params: Any) -> str:
    """The text in the current language, `params` applied with `str.format` (none: the text is returned as is)."""
    out = catalog().get(text, text) if _current.get() == "en" else text
    return out.format_map(params) if params else out


def N_(text: str) -> str:
    """Marks a German text for the catalog without translating it (module-level tables): `_()` it when it is shown."""
    return text


def parse_accept_language(header: str | None) -> str:
    """The best supported language of an `Accept-Language` value (q-values honoured), German without a match."""
    if not header:
        return DEFAULT_LANGUAGE
    ranked: list[tuple[float, int, str]] = []
    for position, part in enumerate(header.split(",")):
        tag, _sep, params = part.strip().partition(";")
        quality = 1.0
        for param in params.split(";"):
            name, _eq, value = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        lang = tag.strip().lower().split("-")[0]
        if lang in LANGUAGES and quality > 0:
            ranked.append((-quality, position, lang))
    return min(ranked)[2] if ranked else DEFAULT_LANGUAGE


class LanguageMiddleware:
    """Pure ASGI: sets the language of the request from `Accept-Language` for everything below it."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        header = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"accept-language"), None)
        token = _current.set(parse_accept_language(header))
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
