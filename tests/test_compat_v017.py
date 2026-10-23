"""Data written by release v0.1.7 loads and works unchanged (issue #144).

Spec: docs/architecture/specs/shared-project-model.md (*Migration and compatibility*), ADR-0070, ADR-0078.
The fixtures in tests/fixtures/v0_1_7/ were written by the v0.1.7 code (`generate.py` there says how):
a file store, a sqlite store and the items of the Cosmos fake, plus `expected.json` with what v0.1.7
answered for them. Every test copies the fixture first, so the fixtures are never changed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from arcviz import exchange, render
from arcviz.model import fingerprint
from arcviz.services import DiagramService, ProjectService
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from tests.cosmos_fake import FakeContainer

FIXTURES = Path(__file__).parent / "fixtures" / "v0_1_7"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(params=["file", "sqlite", "cosmos"])
def legacy(request, tmp_path):
    if request.param == "file":
        shutil.copytree(FIXTURES / "file", tmp_path / "file")
        return FileStore(tmp_path / "file")
    if request.param == "sqlite":
        shutil.copy(FIXTURES / "arcviz.sqlite3", tmp_path / "arcviz.sqlite3")
        return SqliteStore(tmp_path / "arcviz.sqlite3")
    container = FakeContainer()
    for item in json.loads((FIXTURES / "cosmos_items.json").read_text(encoding="utf-8")):
        container.items[(item["diagram_id"], item["id"])] = item
    return CosmosStore(container)


def without_times(project: dict[str, Any]) -> dict[str, Any]:
    """A project dump without the timestamps that differ between the three fixture stores."""
    return {**project, "diagrams": [{k: v for k, v in m.items() if k != "added_at"} for m in project["diagrams"]]}


def services(store) -> tuple[ProjectService, DiagramService]:
    projects = ProjectService(store, store)
    return projects, DiagramService(store, projects)


def keys(value: Any) -> set[str]:
    """Every dict key anywhere inside `value`."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys(v)}
    return set()


def test_fixtures_were_written_by_v017():
    assert EXPECTED["version"] == "0.1.7"
    assert set(EXPECTED["diagrams"]) == {"altsystem", "bestellprozess", "checkout", "plattform"}


@pytest.mark.parametrize("diagram_id", sorted(EXPECTED["diagrams"]))
def test_diagrams_load_with_the_same_content(legacy, diagram_id):
    want = EXPECTED["diagrams"][diagram_id]
    diagram = legacy.load(diagram_id)
    assert fingerprint(diagram) == want["fingerprint"]
    exported = exchange.export_document(diagram)
    assert {**exported, "exported_at": None} == {**want["export"], "exported_at": None}  # same document
    assert diagram.summary() == want["summary"]
    assert "ref" not in keys(diagram.model_dump(mode="json"))  # nothing new appears in old data
    render.payload(diagram)  # still renders


@pytest.mark.parametrize("diagram_id", sorted(EXPECTED["diagrams"]))
def test_versions_and_their_fingerprints_are_unchanged(legacy, diagram_id):
    want = EXPECTED["diagrams"][diagram_id]
    versions = legacy.list_versions(diagram_id)
    assert [v.model_dump() for v in versions] == want["versions"]
    recomputed = [fingerprint(legacy.load_version(diagram_id, v.version)[1]) for v in versions]
    assert recomputed == want["version_fingerprints"]
    # The stored fingerprint of every version matches the one computed now: undo/redo and the
    # "did the turn change anything" check compare them.
    assert recomputed == [v.fingerprint for v in versions]
    assert legacy.undo_target(diagram_id) == want["undo_target"]
    assert legacy.redo_target(diagram_id) == want["redo_target"]


def test_transcripts_are_unchanged(legacy):
    for diagram_id, want in EXPECTED["diagrams"].items():
        chat = [{k: e[k] for k in ("role", "content", "seq")} for e in legacy.load_chat(diagram_id)]
        assert chat == want["chat"]


def test_projects_load_and_the_repair_changes_nothing(legacy):
    projects, _ = services(legacy)
    assert not projects.reconcile().changed
    for project_id, want in EXPECTED["projects"].items():
        got = projects.get(project_id).model_dump(mode="json", exclude={"created_at", "updated_at"})
        assert without_times({k: got[k] for k in want}) == without_times(want)  # new fields only add to the old ones
    assert {p.id for p in projects.list_projects()} == {"default", "webshop"}


def test_old_data_keeps_working(legacy):
    projects, diagrams = services(legacy)
    # redo what v0.1.7 undid, then undo again: the history written by v0.1.7 drives both
    assert diagrams.redo("plattform") is not None
    assert legacy.load("plattform").find_node("cache") is not None
    assert diagrams.undo("plattform") is not None
    assert legacy.load("plattform").find_node("cache") is None
    diagrams.apply_operation("checkout", "add_lifeline", {"name": "Payment", "id": "payment"}, "Payment")
    diagrams.apply_operation("bestellprozess", "update_node", {"id": "kunde", "name": "Kundin"}, "Partition")
    projects.apply("webshop", "add_section", {"title": "Lager"})
    assert projects.locate("altsystem") == ("default", None)
    assert projects.locate("checkout") == ("webshop", "zahlung")
