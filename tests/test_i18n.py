"""Server-side internationalization (spec: docs/architecture/specs/internationalization.md, ADR-0199)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from arcviz import i18n
from arcviz.i18n import N_, _

PACKAGE = Path(i18n.__file__).parent
PLACEHOLDER = re.compile(r"\{(\w*)\}")


def test_parse_accept_language():
    parse = i18n.parse_accept_language
    assert parse(None) == "de"
    assert parse("") == "de"
    assert parse("en-US,en;q=0.9,de;q=0.8") == "en"
    assert parse("de-DE,de;q=0.9,en;q=0.8") == "de"
    assert parse("fr;q=0.9, en;q=0.5") == "en"
    assert parse("fr, es") == "de"
    assert parse("en;q=0, de;q=0.1") == "de"
    assert parse("en;q=abc") == "de"
    assert parse("*") == "de"


def test_translation_falls_back_to_the_german_text(monkeypatch):
    monkeypatch.setattr(i18n, "_catalog", {"Hallo {name}": "Hello {name}"})
    assert _("Hallo {name}", name="Ada") == "Hallo Ada"
    with i18n.use("en"):
        assert _("Hallo {name}", name="Ada") == "Hello Ada"
        assert _("Ohne Eintrag {x}", x=1) == "Ohne Eintrag 1"
        assert _("Mit {Klammern}") == "Mit {Klammern}"  # no params: returned as is
    assert i18n.language() == "de"
    with i18n.use("xx"):
        assert i18n.language() == "de"


def test_language_name():
    with i18n.use("en"):
        assert i18n.language_name() == "English"
    assert i18n.language_name() == "German"


def test_middleware_sets_the_language_of_the_request():
    app = Starlette(routes=[Route("/", lambda request: PlainTextResponse(i18n.language()))])
    app.add_middleware(i18n.LanguageMiddleware)
    client = TestClient(app)
    assert client.get("/").text == "de"
    assert client.get("/", headers={"Accept-Language": "en-GB"}).text == "en"
    assert client.get("/").text == "de"


def test_english_reply_language_in_the_prompts():
    from arcviz import prompts

    assert "English" not in prompts.prompt_prefix("deployment", "x").split("Replies:")[-1]
    with i18n.use("en"):
        assert "answer in English" in prompts.prompt_prefix("deployment", "x")
        assert "answer in English" in prompts.project_prompt_prefix()
        assert "answer in English" in prompts.requirements_prompt_prefix()


def _literals() -> tuple[set[str], list[str]]:
    """(German texts of every `_("…")` / `N_("…")` call in arcviz, problems with calls that cannot be extracted)."""
    texts: set[str] = set()
    problems: list[str] = []
    for file in sorted(PACKAGE.rglob("*.py")):
        if file.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"_", "N_"}):
                continue
            where = f"{file.relative_to(PACKAGE)}:{node.lineno}"
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                texts.add(arg.value)
            elif isinstance(arg, (ast.JoinedStr, ast.BinOp)):
                problems.append(f"{where}: use placeholders, not an f-string or concatenation")
    return texts, problems


def test_every_call_is_extractable():
    assert _literals()[1] == []


def test_every_text_has_an_english_entry_and_every_entry_is_used():
    texts, _problems = _literals()
    entries = i18n.load_catalog()
    assert sorted(texts - entries.keys()) == [], "texts without an English entry (arcviz/locales/en/*.json)"
    assert sorted(entries.keys() - texts) == [], "catalog entries no `_()` / `N_()` call uses"


def test_placeholders_match_between_german_and_english():
    for german, english in i18n.load_catalog().items():
        assert sorted(PLACEHOLDER.findall(german)) == sorted(PLACEHOLDER.findall(english)), german
        assert english.strip(), german


def test_catalog_files_are_json_objects_of_strings():
    for path in (PACKAGE / "locales" / "en").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()), path


def test_n_marks_without_translating():
    with i18n.use("en"):
        assert N_("Text") == "Text"


def test_fragments_do_not_translate_the_same_text_differently():
    seen: dict[str, tuple[str, str]] = {}
    conflicts = []
    for path in sorted((PACKAGE / "locales" / "en").glob("*.json")):
        for german, english in json.loads(path.read_text(encoding="utf-8")).items():
            if german in seen and seen[german][1] != english:
                conflicts.append(f"{german}: {seen[german][0]} vs {path.name}")
            seen.setdefault(german, (path.name, english))
    assert conflicts == []
