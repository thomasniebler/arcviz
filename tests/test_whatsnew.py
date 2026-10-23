"""Release highlights (spec: docs/architecture/specs/whats-new.md)."""

import copy

from fastapi.testclient import TestClient

from arcviz import __version__, whatsnew
from arcviz.server import create_app
from arcviz.store import FileStore
from tests.conftest import ScriptedProvider

DOC = {
    "releases": [
        {"version": "0.5.0", "items": [{"emoji": "🚀", "text": "neu"}]},
        {"version": "0.4.2", "items": [{"emoji": "🔗", "text": "alt"}]},
    ]
}


def test_the_shipped_highlights_are_well_formed_and_not_from_the_future():
    data = whatsnew.load()
    assert data["releases"], "whatsnew.json is missing or malformed"
    assert whatsnew.problems(data) == []
    assert whatsnew.version_key(data["releases"][0]["version"]) <= whatsnew.version_key(__version__)


def test_current_is_the_newest_release_not_newer_than_the_version():
    assert whatsnew.current("0.5.0", DOC)["version"] == "0.5.0"
    assert whatsnew.current("0.5.3", DOC)["version"] == "0.5.0"  # a patch release keeps the highlights
    assert whatsnew.current("0.4.9", DOC)["version"] == "0.4.2"
    assert whatsnew.current("0.4.1", DOC) is None
    assert whatsnew.current("not-a-version", DOC) is None


def test_problems_names_what_is_wrong():
    assert whatsnew.problems({}) == ["'releases' must be a list"]
    bad = copy.deepcopy(DOC)
    bad["releases"][0]["version"] = "v5"
    bad["releases"][1]["items"] = []
    found = whatsnew.problems(bad)
    assert any("1.2.3" in p for p in found) and any("1 to" in p for p in found)
    unsorted = {"releases": list(reversed(DOC["releases"]))}
    assert whatsnew.problems(unsorted) == ["releases must be unique and sorted newest first"]
    long_text = copy.deepcopy(DOC)
    long_text["releases"][0]["items"][0]["text"] = "x" * (whatsnew.MAX_TEXT + 1)
    assert len(whatsnew.problems(long_text)) == 1


def test_english_text_is_optional_and_picked_by_language():
    doc = {
        "releases": [
            {
                "version": "0.5.0",
                "items": [{"emoji": "a", "text": "neu", "text_en": "new"}, {"emoji": "b", "text": "nur"}],
            }
        ]
    }
    assert whatsnew.problems(doc) == []
    assert [i["text"] for i in whatsnew.current("0.5.0", doc)["items"]] == ["neu", "nur"]
    assert [i["text"] for i in whatsnew.current("0.5.0", doc, "en")["items"]] == ["new", "nur"]
    assert all("text_en" not in i for i in whatsnew.current("0.5.0", doc, "en")["items"])
    doc["releases"][0]["items"][0]["text_en"] = " "
    assert any("text_en" in p for p in whatsnew.problems(doc))


def test_the_shipped_highlights_have_english_texts():
    assert all(i.get("text_en") for r in whatsnew.load()["releases"] for i in r["items"])


def test_config_answers_the_highlights_in_the_requested_language(tmp_path):
    client = TestClient(create_app(store=FileStore(tmp_path), provider=ScriptedProvider([])))
    en = client.get("/api/config", headers={"Accept-Language": "en"}).json()["whats_new"]
    assert en == whatsnew.current(__version__, language="en")
    assert en != whatsnew.current(__version__)


def test_config_reports_the_version_and_its_highlights(tmp_path):
    client = TestClient(create_app(store=FileStore(tmp_path), provider=ScriptedProvider([])))
    cfg = client.get("/api/config").json()
    assert cfg["version"] == __version__
    assert cfg["whats_new"] == whatsnew.current(__version__)
    assert cfg["whats_new"]["items"][0]["emoji"]
