"""Write the v0.4.0 compatibility fixtures (milestone M25, spec docs/architecture/specs/model-relations.md).

Run with the **v0.4.0 release code** first on the path, never with the current code:

    git worktree add /tmp/arcviz-v040 v0.4.0
    PYTHONPATH=/tmp/arcviz-v040 .venv/bin/python tests/fixtures/v0_4_0/generate.py tests/fixtures/v0_4_0

It builds, with readable ids (`<base>`, like the test suite pins them), ada's project `webshop` shared with ed
(editor): two sections, a shared model of four elements (one inside another), a section standing for an element, a
deployment diagram whose linked nodes are connected by edges (with protocol and port), a sequence diagram whose linked
lifelines exchange messages, an activity diagram, notes, versions with a manual snapshot and an undo, transcripts; and
ada's default project with one diagram. Everything goes into a file store, a sqlite store and the Cosmos fake (dumped
as its items), plus `expected.json`: what the v0.4.0 code answered for that data (dumps, fingerprints, exports, the
model, the project bundle). `tests/test_compat_v040.py` checks that the current code (M25: model relations) reads the
same data and answers the same.
"""

from __future__ import annotations

import gc
import importlib
import json
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import arcviz
from arcviz import exchange, ports
from arcviz.model import fingerprint
from arcviz.services import ModelService, ProjectExchangeService, ServiceRoot
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.templates import create_from_template
from arcviz.workspaces import Identity
from tests.cosmos_fake import FakeContainer

ADMIN = Identity("admin", "Administrator")
ADA = Identity("ada", "Ada", "ada@example.org")
ED = Identity("ed", "Ed", "ed@example.org")


def readable_ids(base: str) -> Iterator[str]:
    yield base
    n = 2
    while True:
        yield f"{base}-{n}"
        n += 1


def build(store) -> None:
    root = ServiceRoot(store, admin=ADMIN)
    for person in (ADA, ED):
        root.users.seen(person)
        root.signed_in(person)
    ada = root.for_actor(ADA)
    projects, diagrams = ada.projects, ada.diagrams
    models = ModelService(projects, diagrams)
    projects.create("Webshop", "webshop", "Online-Shop mit Bestellung und Zahlung")
    projects.share("webshop", "ed", "editor")
    projects.apply("webshop", "add_section", {"title": "Bestellung", "id": "bestellung"})
    projects.apply("webshop", "add_section", {"title": "Zahlung", "id": "zahlung", "parent": "bestellung"})

    diagrams.create("Plattform", "plattform", create_from_template("empty", "plattform", "Plattform"), "webshop")
    diagrams.create(
        "Bestellprozess",
        "bestellprozess",
        create_from_template("order-process", "bestellprozess", "Bestellprozess"),
        "webshop",
        "bestellung",
    )
    diagrams.create(
        "Checkout", "checkout", create_from_template("checkout", "checkout", "Checkout"), "webshop", "zahlung"
    )
    diagrams.create("Notizen", "notizen", create_from_template("demo", "notizen", "Notizen"))  # ada's default project

    def op(diagram_id: str, name: str, args: dict, label: str) -> None:
        diagrams.apply_operation(diagram_id, name, args, label)

    op("plattform", "add_node", {"name": "Cluster", "kind": "cluster", "id": "cluster"}, "Cluster")
    op("plattform", "add_node", {"name": "Shop", "kind": "service", "id": "shop", "parent": "cluster"}, "Shop")
    op("plattform", "add_node", {"name": "Order Service", "kind": "service", "id": "orders", "parent": "cluster"}, "OS")
    op(
        "plattform",
        "add_node",
        {"name": "Orders DB", "kind": "database", "id": "db", "description": "PostgreSQL"},
        "DB",
    )
    op(
        "plattform",
        "add_edge",
        {"source": "shop", "target": "orders", "label": "Bestellung", "properties": {"protocol": "HTTPS", "port": 443}},
        "E1",
    )
    op("plattform", "add_edge", {"source": "orders", "target": "db", "kind": "connection", "direction": "both"}, "E2")
    diagrams.snapshot("plattform", "Vor dem Umbau")
    op("plattform", "add_node", {"name": "Cache", "kind": "database", "id": "cache"}, "Cache")
    diagrams.undo("plattform")  # leaves a redo target
    op("checkout", "add_lifeline", {"name": "Order Service", "id": "orders"}, "Lifeline")

    # the shared model (M18): elements shown in two diagrams, one inside another, a section standing for one
    models.add_element("webshop", "Shop", "system", "Der Webshop", element_id="shop")
    models.add_element("webshop", "Order Service", "component", "Nimmt Bestellungen an", "shop", "order-service")
    models.add_element("webshop", "Orders DB", "data", element_id="orders-db")
    models.add_element("webshop", "Cluster", "node", element_id="cluster")
    models.link("plattform", "cluster", "cluster")
    models.link("plattform", "orders", "order-service")
    models.link("plattform", "db", "orders-db")
    models.link("checkout", "orders", "order-service")
    models.link_section("webshop", "zahlung", "order-service")

    diagrams.append_chat("plattform", [{"role": "user", "content": "Füge einen Order Service hinzu."}])
    diagrams.append_chat("plattform", [{"role": "assistant", "content": "Erledigt.", "operations": []}])
    projects.append_chat(
        "webshop",
        [
            {"role": "user", "content": "Welche Diagramme gibt es?"},
            {"role": "assistant", "content": "Plattform, Bestellprozess und Checkout.", "operations": []},
        ],
    )


def expected(store) -> dict:
    root = ServiceRoot(store, admin=ADMIN)
    ada = root.for_actor(ADA)
    models = ModelService(ada.projects, ada.diagrams)
    out: dict = {"version": arcviz.__version__, "diagrams": {}, "projects": {}, "models": {}, "bundles": {}}
    for info in store.list_diagrams():
        diagram = store.load(info.id)
        out["diagrams"][info.id] = {
            "dump": diagram.model_dump(mode="json", exclude={"created_at", "updated_at"}),
            "fingerprint": fingerprint(diagram),
            "versions": [v.model_dump(exclude={"created_at"}) for v in store.list_versions(info.id)],
            "version_fingerprints": [
                fingerprint(store.load_version(info.id, v.version)[1]) for v in store.list_versions(info.id)
            ],
            "export": {k: v for k, v in exchange.export_document(diagram).items() if k != "exported_at"},
            "summary": diagram.summary(),
        }
    for project, _ in store.list_projects():
        out["projects"][project.id] = project.model_dump(mode="json", exclude={"created_at", "updated_at"})
        if project.workspace and project.workspace.startswith("user_") and project.id == "webshop":
            out["models"][project.id] = models.model(project.id).to_dict()
            bundle = ProjectExchangeService(ada.diagrams, ada.projects).export(project.id)
            bundle.pop("exported_at", None)
            for document in bundle["diagrams"]:
                document.pop("exported_at", None)
            out["bundles"][project.id] = bundle
    return out


def main(target: Path) -> None:
    ports.new_ids = readable_ids
    target.mkdir(parents=True, exist_ok=True)
    for name in ("file", "arcviz.sqlite3", "cosmos_items.json", "expected.json"):
        path = target / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    file_store = FileStore(target / "file")
    build(file_store)
    sqlite_store = SqliteStore(target / "arcviz.sqlite3")
    build(sqlite_store)
    container = FakeContainer()
    build(CosmosStore(container))

    sqlite_store.close()
    del sqlite_store
    gc.collect()
    with sqlite3.connect(target / "arcviz.sqlite3") as conn:  # fold the WAL into the database file
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    items = sorted(container.items.values(), key=lambda i: (i["diagram_id"], i["id"]))
    (target / "cosmos_items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (target / "expected.json").write_text(
        json.dumps(expected(file_store), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    # refuse code that knows model relations (M25)
    if arcviz.__version__ != "0.4.0" or hasattr(importlib.import_module("arcviz.project_model"), "Relation"):
        sys.exit(f"Run this with the v0.4.0 release code on PYTHONPATH (found arcviz {arcviz.__version__}).")
    main(Path(sys.argv[1]))
