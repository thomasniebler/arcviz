"""Write the v0.1.7 compatibility fixtures (issue #144, spec docs/architecture/specs/shared-project-model.md).

Run with the **v0.1.7 release code** first on the path, never with the current code:

    git worktree add /tmp/arcviz-v017 v0.1.7
    PYTHONPATH=/tmp/arcviz-v017 .venv/bin/python tests/fixtures/v0_1_7/generate.py tests/fixtures/v0_1_7

It builds one project with sections, diagrams of all three types (with versions, a manual snapshot, an
undo, transcripts) and a diagram in the default project, in a file store, a sqlite store and the Cosmos
fake (dumped as its items), plus `expected.json`: what the v0.1.7 code answered for that data
(fingerprints, versions, undo/redo targets, exports, summaries). `tests/test_compat_v017.py` checks that
the current code reads the same data and answers the same.
"""

from __future__ import annotations

import gc
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import arcviz
from arcviz import exchange
from arcviz.model import fingerprint
from arcviz.services import DiagramService, ProjectService
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.templates import create_from_template
from tests.cosmos_fake import FakeContainer

FIXED_TIME = "2026-10-01T12:00:00+00:00"


def build(store) -> None:
    projects = ProjectService(store, store)
    diagrams = DiagramService(store, projects)
    projects.create("Webshop", "webshop", "Online-Shop mit Bestellung und Zahlung")
    projects.apply("webshop", "add_section", {"title": "Bestellung", "id": "bestellung"})
    projects.apply("webshop", "add_section", {"title": "Zahlung", "id": "zahlung", "parent": "bestellung"})

    diagrams.create("Plattform", "plattform", create_from_template("aks-database", "plattform", "Plattform"), "webshop")
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
    diagrams.create("Altsystem", "altsystem", create_from_template("demo", "altsystem", "Altsystem"))  # default project

    diagrams.apply_operation(
        "plattform", "add_node", {"name": "Order Service", "kind": "service", "id": "order-service"}, "Order Service"
    )
    diagrams.apply_operation(
        "plattform", "update_node", {"id": "order-service", "properties": {"runtime": "python"}}, "Runtime"
    )
    diagrams.snapshot("plattform", "Vor dem Umbau")
    diagrams.apply_operation("plattform", "add_node", {"name": "Cache", "kind": "database", "id": "cache"}, "Cache")
    diagrams.undo("plattform")  # leaves a redo target

    diagrams.apply_operation("bestellprozess", "update_node", {"id": "shop", "name": "Webshop"}, "Partition")
    diagrams.apply_operation("checkout", "add_lifeline", {"name": "Order Service", "id": "orders"}, "Lifeline")

    diagrams.append_chat("plattform", [{"role": "user", "content": "Füge einen Order Service hinzu."}])
    diagrams.append_chat("plattform", [{"role": "assistant", "content": "Erledigt.", "operations": []}])
    projects.list_projects()  # runs the membership repair, as the UI does on start


def expected(store) -> dict:
    out: dict = {"version": arcviz.__version__, "diagrams": {}, "projects": {}}
    for info in store.list_diagrams():
        diagram = store.load(info.id)
        out["diagrams"][info.id] = {
            "fingerprint": fingerprint(diagram),
            "versions": [v.model_dump() for v in store.list_versions(info.id)],
            "version_fingerprints": [
                fingerprint(store.load_version(info.id, v.version)[1]) for v in store.list_versions(info.id)
            ],
            "undo_target": store.undo_target(info.id),
            "redo_target": store.redo_target(info.id),
            "export": exchange.export_document(diagram),
            "summary": diagram.summary(),
            "chat": [{k: e[k] for k in ("role", "content", "seq")} for e in store.load_chat(info.id)],
        }
    for project, _ in store.list_projects():
        out["projects"][project.id] = project.model_dump(mode="json", exclude={"created_at", "updated_at"})
    return out


def main(target: Path) -> None:
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
    cosmos_store = CosmosStore(container)
    build(cosmos_store)

    del sqlite_store
    gc.collect()  # closes the store's connection
    with sqlite3.connect(target / "arcviz.sqlite3") as conn:  # fold the WAL into the database file
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    items = sorted(container.items.values(), key=lambda i: (i["diagram_id"], i["id"]))
    (target / "cosmos_items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (target / "expected.json").write_text(
        json.dumps(expected(file_store), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    if arcviz.__version__ != "0.1.7":
        sys.exit(f"Run this with the v0.1.7 code on PYTHONPATH (found arcviz {arcviz.__version__}).")
    main(Path(sys.argv[1]))
