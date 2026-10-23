"""Icons per kind and the Azure/AWS/GCP kind packs (spec: docs/architecture/specs/cloud-icons.md)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from arcviz import kinds, mermaid, operations
from arcviz.model import Diagram, use_kinds
from arcviz.server import create_app
from arcviz.store import FileStore

PACKS = ("aws", "azure", "gcp")


def write(tmp_path, data, name="kinds.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_vendored_packs_are_iconify_sets_within_budget():
    total = 0
    for pack in PACKS:
        path = kinds.ICON_DIR / f"{pack}.json"
        data = json.loads(path.read_text())
        assert data["prefix"] == pack and data["icons"]
        assert all("<svg" not in icon["body"] and "<script" not in icon["body"] for icon in data["icons"].values())
        total += path.stat().st_size
    assert total < 100_000
    readme = (kinds.ICON_DIR / "README.md").read_text()
    assert "CC0-1.0" in readme and "trademarks" in readme


def test_icon_names_are_listed():
    assert "lambda" in kinds.icon_names("aws")
    assert kinds.icon_packs() == list(PACKS)


def test_kinds_file_takes_an_icon(tmp_path):
    catalog = kinds.load_kind_catalog(write(tmp_path, {"node_kinds": {"fn": {"icon": "aws:lambda"}}}))
    assert catalog.node_icons == {"fn": "aws:lambda"}
    assert "fn" in catalog.leaf_kinds


@pytest.mark.parametrize(
    "spec,message",
    [
        ({"icon": "nope:lambda"}, "unknown icon pack 'nope'"),
        ({"icon": "aws:nope"}, "unknown icon 'aws:nope'"),
        ({"icon": "lambda"}, "'<pack>:<icon>'"),
        ({"icon": 3}, "'<pack>:<icon>'"),
        ({"icon": "aws:lambda", "container": True}, "leaf kinds only"),
    ],
)
def test_invalid_icons_stop_startup(tmp_path, spec, message):
    with pytest.raises(kinds.KindConfigError, match=message):
        kinds.load_kind_catalog(write(tmp_path, {"node_kinds": {"fn": spec}}))


def test_every_kind_pack_loads_and_its_icons_exist():
    for pack in PACKS:
        catalog = kinds.load_kind_catalog(kinds.kind_pack_path(pack))
        configured = catalog.all_kinds - kinds.BUILTIN_KINDS.all_kinds
        assert configured and all(k.startswith(f"{pack}_") for k in configured)
        for kind, icon in catalog.node_icons.items():
            assert icon.split(":")[1] in kinds.icon_names(icon.split(":")[0]), kind
            assert kind in catalog.leaf_kinds


def test_packs_combine_and_duplicates_fail(tmp_path):
    combined = kinds.load_kind_catalog(*(kinds.kind_pack_path(p) for p in PACKS))
    assert {"aws_lambda", "azure_functions", "gcp_cloud_run"} <= combined.all_kinds
    clash = write(tmp_path, {"node_kinds": {"aws_lambda": {}}})
    with pytest.raises(kinds.KindConfigError, match="aws_lambda.*also defined"):
        kinds.load_kind_catalog(kinds.kind_pack_path("aws"), clash)
    with pytest.raises(kinds.KindConfigError, match="Available: aws, azure, gcp"):
        kinds.kind_pack_path("oracle")


def test_server_activates_kind_packs(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_KIND_PACKS", "aws, gcp")
    client = TestClient(create_app(store=FileStore(tmp_path)))
    cfg = client.get("/api/config").json()
    assert cfg["icon_packs"] == list(PACKS)
    dep = cfg["diagram_types"]["deployment"]
    assert "aws_lambda" in dep["node_kinds"] and "gcp_gke" in dep["node_kinds"] and "azure_vm" not in dep["node_kinds"]
    assert dep["node_icons"]["aws_lambda"] == "aws:lambda"
    client.post("/api/diagrams", json={"title": "Cloud", "id": "cloud"})
    created = client.post(
        "/api/diagrams/cloud/ops", json={"op": "add_node", "args": {"name": "fn", "kind": "aws_lambda"}}
    )
    assert created.status_code == 200 and 'icon: "aws:lambda"' in created.json()["mermaid"]
    monkeypatch.setenv("ARCVIZ_KIND_PACKS", "oracle")
    with pytest.raises(kinds.KindConfigError):
        create_app(store=FileStore(tmp_path))


def test_mermaid_draws_icon_kinds_with_escaped_labels():
    use_kinds(kinds.load_kind_catalog(kinds.kind_pack_path("aws")))
    d = Diagram(id="x", title="X")
    operations.apply(d, "add_node", {"name": "VPC", "kind": "aws_vpc"})
    operations.apply(d, "add_node", {"name": 'fn "a\\b" <i>', "kind": "aws_lambda", "id": "fn", "parent": "vpc"})
    operations.apply(d, "add_node", {"name": "db", "kind": "aws_dynamodb"})
    operations.apply(d, "add_edge", {"source": "fn", "target": "db"})
    src = mermaid.render(d)
    ids = mermaid.id_map(d)
    assert (
        f'{ids["fn"]}@{{ icon: "aws:lambda", form: "rounded", '
        'label: "«aws_lambda»<br/>fn #quot;a#92;b#quot; #lt;i#gt;", pos: "b", h: 46 }' in src
    )
    assert f"subgraph {ids['vpc']}" in src  # containers stay subgraphs
    assert "classDef aws_lambda" in src
    focused = mermaid.render(d, focus="vpc")
    assert f'{ids["db"]}@{{ icon: "aws:dynamodb"' in focused and "stub" in focused
