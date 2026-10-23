"""Data written by release v0.4.0 loads and answers unchanged after M25 (model relations).

Spec: docs/architecture/specs/model-relations.md (*Migration and compatibility*). The fixtures in
tests/fixtures/v0_4_0/ were written by the v0.4.0 code (`generate.py` there says how): a file store, a sqlite store
and the items of the Cosmos fake, plus `expected.json` with what v0.4.0 answered for them. The new fields (project
relations, edge and message refs, completeness, hidden relations) are omitted while unset, so dumps, fingerprints,
exports, the model answer and the project bundle stay as they were. Every test copies the fixture first.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from arcviz import exchange
from arcviz.model import fingerprint
from arcviz.services import ModelService, ProjectExchangeService, ServiceRoot
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.workspaces import Identity
from tests.cosmos_fake import FakeContainer

FIXTURES = Path(__file__).parent / "fixtures" / "v0_4_0"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
ADMIN = Identity("admin", "Administrator")
ADA = Identity("ada", "Ada", "ada@example.org")


@pytest.fixture(params=["file", "sqlite", "cosmos"])
def stored(request, tmp_path):
    if request.param == "file":
        shutil.copytree(FIXTURES / "file", tmp_path / "file")
        yield FileStore(tmp_path / "file")
        return
    if request.param == "sqlite":
        shutil.copy(FIXTURES / "arcviz.sqlite3", tmp_path / "arcviz.sqlite3")
        store = SqliteStore(tmp_path / "arcviz.sqlite3")
        yield store
        store.close()
        return
    container = FakeContainer()
    for item in json.loads((FIXTURES / "cosmos_items.json").read_text(encoding="utf-8")):
        container.items[(item["diagram_id"], item["id"])] = item
    yield CosmosStore(container)


def untimed(document: dict[str, Any]) -> dict[str, Any]:
    """An export without the timestamps that differ between the three fixture stores."""
    inner = {k: v for k, v in document["diagram"].items() if k not in ("created_at", "updated_at")}
    return {**{k: v for k, v in document.items() if k != "exported_at"}, "diagram": inner}


def without_times(project: dict[str, Any]) -> dict[str, Any]:
    """A project dump without what differs between the three fixture stores (timestamps, the random incarnation)."""
    project = {k: v for k, v in project.items() if k != "incarnation"}
    return {**project, "diagrams": [{k: v for k, v in m.items() if k != "added_at"} for m in project["diagrams"]]}


def test_fixtures_were_written_by_v040():
    assert EXPECTED["version"] == "0.4.0"
    assert "relations" not in EXPECTED["projects"]["webshop"]
    assert EXPECTED["bundles"]["webshop"]["schema_version"] == 2  # a model, no relations


@pytest.mark.parametrize("diagram_id", sorted(EXPECTED["diagrams"]))
def test_diagrams_dump_fingerprint_and_export_unchanged(stored, diagram_id):
    expected = EXPECTED["diagrams"][diagram_id]
    diagram = stored.load(diagram_id)
    assert diagram.model_dump(mode="json", exclude={"created_at", "updated_at"}) == expected["dump"]
    assert fingerprint(diagram) == expected["fingerprint"]
    assert diagram.summary() == expected["summary"]
    assert untimed(exchange.export_document(diagram)) == untimed(expected["export"])
    versions = stored.list_versions(diagram_id)
    assert [fingerprint(stored.load_version(diagram_id, v.version)[1]) for v in versions] == expected[
        "version_fingerprints"
    ]
    assert [v.model_dump(exclude={"created_at"}) for v in versions] == expected["versions"]


@pytest.mark.parametrize("project_id", sorted(EXPECTED["projects"]))
def test_projects_dump_unchanged(stored, project_id):
    project = stored.read_project(project_id)[0]
    dumped = project.model_dump(mode="json", exclude={"created_at", "updated_at"})
    assert without_times(dumped) == without_times(EXPECTED["projects"][project_id])
    assert project.relations == []


def test_model_and_bundle_answer_unchanged(stored):
    ada = ServiceRoot(stored, admin=ADMIN).for_actor(ADA)
    model = ModelService(ada.projects, ada.diagrams).model("webshop").to_dict()
    assert model["relations"] == []
    assert {k: v for k, v in model.items() if k in EXPECTED["models"]["webshop"]} == EXPECTED["models"]["webshop"]
    bundle = ProjectExchangeService(ada.diagrams, ada.projects).export("webshop")
    expected = EXPECTED["bundles"]["webshop"]
    bundle.pop("exported_at")
    assert [untimed(d) for d in bundle.pop("diagrams")] == [untimed(d) for d in expected["diagrams"]]
    assert bundle == {k: v for k, v in expected.items() if k != "diagrams"}


def test_old_data_accepts_relations(stored):
    """The v0.4.0 project takes relations; an old store needs no migration for them."""
    ada = ServiceRoot(stored, admin=ADMIN).for_actor(ADA)
    models = ModelService(ada.projects, ada.diagrams)
    relation = models.add_relation("webshop", "deployed_on", "order-service", "cluster")
    assert relation.id == "r1"
    assert [r.id for r in stored.read_project("webshop")[0].relations] == ["r1"]
