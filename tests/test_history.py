"""Version history read once, paged and optionally pruned (#114, spec: docs/architecture/specs/hardening.md,
ADR-0129, ADR-0130). Backend-neutral behaviour is in tests/store_contract.py."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from arcviz import operations as ops
from arcviz.envconfig import ConfigError
from arcviz.history import HistoryConfig, prunable, redo_target_of, undo_target_of
from arcviz.server import create_app
from arcviz.services import DiagramService
from arcviz.store import Change
from arcviz.store.cosmos import Q_VERSION_META, CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.templates import demo_diagram
from tests.conftest import ScriptedProvider
from tests.cosmos_fake import FakeContainer

# ---- configuration -------------------------------------------------------------------------------------------------


def test_the_defaults_keep_everything():
    config = HistoryConfig.from_env({})
    assert config == HistoryConfig(page=50, keep=0, slack=20, prune_manual=False)


def test_the_variables_are_read_and_checked():
    config = HistoryConfig.from_env(
        {
            "ARCVIZ_VERSIONS_PAGE": "20",
            "ARCVIZ_VERSIONS_KEEP": "200",
            "ARCVIZ_VERSIONS_PRUNE_SLACK": "5",
            "ARCVIZ_VERSIONS_PRUNE_MANUAL": "1",
        }
    )
    assert config == HistoryConfig(page=20, keep=200, slack=5, prune_manual=True)
    with pytest.raises(ConfigError) as error:
        HistoryConfig.from_env({"ARCVIZ_VERSIONS_KEEP": "5", "ARCVIZ_VERSIONS_PAGE": "0"})
    assert "ARCVIZ_VERSIONS_KEEP" in str(error.value) and "ARCVIZ_VERSIONS_PAGE" in str(error.value)


# ---- pruning never changes what undo and redo do ---------------------------------------------------------------------

STEPS = st.lists(st.sampled_from(["change", "change", "change", "undo", "redo", "manual", "noop"]), max_size=60)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(steps=STEPS, keep=st.integers(min_value=10, max_value=15))
def test_pruning_keeps_the_undo_and_redo_targets(tmp_path_factory, steps, keep):
    store = SqliteStore(tmp_path_factory.mktemp("history") / "db.sqlite3")
    try:
        store.create("Demo", "demo", demo_diagram("demo", "Demo"))
        for i, step in enumerate(steps):
            if step == "change":
                store.update("demo", lambda d, i=i: Change(_add(d, f"n{i}"), None, label=f"c{i}"))
            elif step == "undo":
                store.undo("demo")
            elif step == "redo":
                store.redo("demo")
            elif step == "manual":
                store.snapshot_latest("demo", "manual")
            else:
                store.update("demo", lambda d: Change(d, None, label="no-op"))
        versions = store.list_versions("demo")
        doomed = set(prunable(versions, HistoryConfig(keep=keep, slack=0, prune_manual=True)))
        kept = [v for v in versions if v.version not in doomed]
        assert undo_target_of(kept) == undo_target_of(versions)
        assert redo_target_of(kept) == redo_target_of(versions)
        assert versions[-1] in kept and versions[0] in kept
    finally:
        store.close()


def _add(diagram, node_id: str):
    ops.apply(diagram, "add_node", {"name": node_id, "kind": "service", "id": node_id})
    return diagram


# ---- no snapshot bodies for a listing --------------------------------------------------------------------------------


def test_the_file_store_lists_from_sidecars_and_migrates_old_versions_once(tmp_path, monkeypatch):
    store = FileStore(tmp_path / "data")
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    for i in range(3):
        store.snapshot_latest("demo", f"s{i}")
    versions_dir = tmp_path / "data" / "diagrams" / "demo" / "versions"
    assert sorted(p.name for p in versions_dir.glob("*.meta.json")) == [f"000{n}.meta.json" for n in range(1, 5)]
    for sidecar in versions_dir.glob("*.meta.json"):  # a store written before M23
        sidecar.unlink()
    reads: list[str] = []
    real = FileStore._read_version_meta

    def counting(path):
        reads.append(path.name)
        return real(path)

    monkeypatch.setattr(FileStore, "_read_version_meta", staticmethod(counting))
    first = store.list_versions("demo")
    assert len(reads) == 4 and [v.version for v in first] == [1, 2, 3, 4]
    reads.clear()
    assert DiagramService(store).state("demo").versions == first
    assert reads == []  # sidecars written by the first listing; no snapshot read any more
    assert json.loads((versions_dir / "0002.meta.json").read_text())["label"] == "s0"


def test_the_cosmos_store_lists_versions_by_a_projection_without_snapshots():
    container = FakeContainer()
    store = CosmosStore(container)
    store.create("Demo", "demo", demo_diagram("demo", "Demo"))
    store.snapshot_latest("demo", "s")
    container.queries.clear()
    DiagramService(store).state("demo")
    version_queries = [q for q in container.queries if "version" in q]
    assert version_queries == [Q_VERSION_META] and "snapshot" not in Q_VERSION_META
    assert not container.cross_partition


# ---- HTTP --------------------------------------------------------------------------------------------------------------


def test_responses_carry_a_page_and_the_versions_endpoint_pages(store, monkeypatch):
    monkeypatch.setenv("ARCVIZ_VERSIONS_PAGE", "5")
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    diagram_id = client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"}).json()[
        "diagram"
    ]["id"]
    for i in range(7):
        assert client.post(f"/api/diagrams/{diagram_id}/versions", json={"label": f"s{i}"}).status_code == 201
    detail = client.get(f"/api/diagrams/{diagram_id}").json()
    assert [v["version"] for v in detail["versions"]] == [4, 5, 6, 7, 8]
    assert detail["versions_total"] == 8 and detail["versions_more"] is True
    page = client.get(f"/api/diagrams/{diagram_id}/versions", params={"before": 4}).json()
    assert [v["version"] for v in page] == [1, 2, 3]
    page = client.get(f"/api/diagrams/{diagram_id}/versions", params={"before": 4, "limit": 2}).json()
    assert [v["version"] for v in page] == [2, 3]
    assert len(client.get(f"/api/diagrams/{diagram_id}/versions").json()) == 8  # unchanged without parameters
    assert client.get(f"/api/diagrams/{diagram_id}/versions", params={"limit": 501}).status_code == 422


def test_a_configured_retention_reaches_the_store(tmp_path, monkeypatch):
    from arcviz.store import create_store

    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCVIZ_VERSIONS_KEEP", "10")
    store = create_store()
    assert store.retention == HistoryConfig(keep=10)


# ---- review of #194: grace, cheap gating and 409 for a pruned target (ADR-0130) ---------------------------------------


def test_a_prune_is_due_every_slack_versions_from_the_number_alone():
    from arcviz.history import prune_due

    config = HistoryConfig(keep=10, slack=5)
    assert [n for n in range(1, 31) if prune_due(n, config)] == [20, 25, 30]
    assert [n for n in range(1, 14) if prune_due(n, HistoryConfig(keep=10, slack=0))] == [11, 12, 13]
    assert not prune_due(100, HistoryConfig()) and not prune_due(100, None)


def test_commits_list_the_history_only_when_a_prune_is_due(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite3")
    try:
        store.create("Demo", "demo", demo_diagram("demo", "Demo"))
        store.retention = HistoryConfig(keep=10, slack=5)
        listings: list[int] = []
        real = store.list_versions

        def counting(diagram_id):
            listings.append(1)
            return real(diagram_id)

        store.list_versions = counting  # type: ignore[method-assign]
        for i in range(24):  # versions 2..25
            store.update("demo", lambda d, i=i: Change(_add(d, f"n{i}"), None, label=f"c{i}"))
        assert len(listings) == 2  # at versions 20 and 25, not on every commit
    finally:
        store.close()


def test_the_grace_keeps_the_newest_keep_plus_slack_versions(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite3")
    try:
        store.create("Demo", "demo", demo_diagram("demo", "Demo"))
        store.retention = HistoryConfig(keep=10, slack=5)
        for i in range(24):
            store.update("demo", lambda d, i=i: Change(_add(d, f"n{i}"), None, label=f"c{i}"))
        numbers = [v.version for v in store.list_versions("demo")]
        assert numbers[0] == 1 and numbers[-15:] == list(range(11, 26))  # the newest keep + slack all kept
    finally:
        store.close()


def test_a_pruned_target_answers_409_reload_and_an_unknown_one_404(store):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    diagram_id = client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"}).json()[
        "diagram"
    ]["id"]
    for i in range(4):
        assert (
            client.post(
                f"/api/diagrams/{diagram_id}/ops", json={"op": "add_node", "args": {"name": f"n{i}", "kind": "service"}}
            ).status_code
            == 200
        )
    target = client.get(f"/api/diagrams/{diagram_id}").json()["undo_target"]
    store.delete_versions(diagram_id, [2, target])  # pruned by another replica meanwhile
    for method, path in (
        ("GET", f"/api/diagrams/{diagram_id}/versions/2"),
        ("POST", f"/api/diagrams/{diagram_id}/versions/2/restore"),
        ("GET", f"/api/diagrams/{diagram_id}/compare?base=2"),
    ):
        response = client.request(method, path)
        assert response.status_code == 409 and "reload" in response.json()["detail"], (path, response.text)
    assert client.get(f"/api/diagrams/{diagram_id}/versions/99").status_code == 404


def test_an_undo_whose_target_is_pruned_while_it_runs_answers_409(store, monkeypatch):
    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    diagram_id = client.post("/api/diagrams", json={"title": "Demo", "id": "demo", "template": "demo"}).json()[
        "diagram"
    ]["id"]
    for i in range(3):
        client.post(
            f"/api/diagrams/{diagram_id}/ops", json={"op": "add_node", "args": {"name": f"n{i}", "kind": "service"}}
        )
    real = type(store).undo_target

    def pruned_right_after(self, diagram):
        target = real(self, diagram)
        self.delete_versions(diagram, [target])  # another replica prunes it between the choice and the load
        return target

    monkeypatch.setattr(type(store), "undo_target", pruned_right_after)
    response = client.post(f"/api/diagrams/{diagram_id}/undo")
    assert response.status_code == 409 and "reload" in response.json()["detail"], response.text
