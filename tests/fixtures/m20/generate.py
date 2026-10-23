"""Write the M20-era compatibility fixtures (issue #179, spec docs/architecture/specs/personal-workspaces.md).

Run with the **M20/M10 code** (main at 242b97f, before M21) first on the path, never with the current code:

    git worktree add /tmp/arcviz-m20 242b97f
    PYTHONPATH=/tmp/arcviz-m20 .venv/bin/python tests/fixtures/m20/generate.py tests/fixtures/m20

It builds the team workspaces of M20 in a file store, a sqlite store and the Cosmos fake (dumped as its items):

- `default` (the admin's, migrated from v0.1.8 data): project `webshop` (ed editor, vic default viewer) and the
  default project with diagram `altsystem`;
- `team` (olga's): members ed, vic, zed; project `plattform` created by ed (explicit owner) with vic as editor and
  zed at the default role; project `labor` created by olga with an inert role of `gone`, a member who left;
- `handover` created by ed and transferred to olga (ed stays a member): project `uebergabe` created by ed;
- `leer` (zed's, empty).

`expected.json` records what the M20 code answered: every person's effective role on every project (None: no
access), the projects, and the diagrams' fingerprints and versions. `tests/test_compat_m20.py` checks that the
migration to personal workspaces keeps every effective role (nobody loses or gains access).
"""

from __future__ import annotations

import gc
import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from arcviz.model import fingerprint
from arcviz.services import ServiceRoot, migrate_to_workspaces
from arcviz.store.cosmos import CosmosStore
from arcviz.store.file import FileStore
from arcviz.store.sqlite import SqliteStore
from arcviz.templates import create_from_template
from arcviz.workspaces import Identity
from tests.cosmos_fake import FakeContainer

PEOPLE = {
    "admin": Identity("admin", "Administrator", "admin@example.org", email_trusted=True),
    "olga": Identity("olga", "Olga Owner", "olga@example.org", email_trusted=True),
    "ed": Identity("ed", "Ed Editor", "ed@example.org", email_trusted=True),
    "vic": Identity("vic", "Vic Viewer", "vic@example.org", email_trusted=True),
    "zed": Identity("zed", "Zed", "zed@example.org", email_trusted=True),
    "gone": Identity("gone", "Gone", "gone@example.org", email_trusted=True),
    "out": Identity("out", "Outsider", "out@example.org", email_trusted=True),
}


def build(store) -> None:
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    migrate_to_workspaces(store, PEOPLE["admin"])  # creates the default workspace, owned by the admin
    admin, olga, ed, zed = (root.for_actor(PEOPLE[n]) for n in ("admin", "olga", "ed", "zed"))

    # the default workspace: the admin's data from before workspaces, shared with two members
    admin.workspaces.add_member("default", "ed")
    admin.workspaces.add_member("default", "vic")
    admin.projects.create("Webshop", "webshop", "Online-Shop", workspace="default")
    admin.projects.set_role("webshop", "ed", "editor")
    admin.diagrams.create("Altsystem", "altsystem", create_from_template("demo", "altsystem", "Altsystem"))
    admin.diagrams.create(
        "Bestellprozess",
        "bestellprozess",
        create_from_template("order-process", "bestellprozess", "Bestellprozess"),
        "webshop",
    )
    admin.diagrams.apply_operation("bestellprozess", "rename_diagram", {"title": "Bestellprozess 2"}, "Umbenannt")

    # a team: a project created by a member (explicit owner), one by the owner with an inert role
    olga.workspaces.create("Team")
    for name in ("ed", "vic", "zed", "gone"):
        olga.workspaces.add_member("team", name)
    ed.projects.create("Plattform", "plattform", workspace="team")
    ed.projects.set_role("plattform", "vic", "editor")
    ed.diagrams.create("Netz", "netz", create_from_template("aks-database", "netz", "Netz"), "plattform")
    ed.diagrams.append_chat("netz", [{"role": "user", "content": "Hallo"}])
    olga.projects.create("Labor", "labor", workspace="team")
    olga.projects.set_role("labor", "gone", "editor")
    olga.workspaces.remove_member("team", "gone")  # leaves an inert role behind only if the clean-up failed:
    labor, revision = store.read_project("labor")
    labor.roles = {**labor.roles, "gone": "editor"}  # so put one back by hand (data from a crash in between)
    store.commit_project(labor, revision)

    # a workspace handed over: its creator stays a member and keeps owning the project they created
    ed.workspaces.create("Handover")
    ed.workspaces.add_member("handover", "olga")
    ed.projects.create("Übergabe", "uebergabe", workspace="handover")
    ed.workspaces.transfer("handover", "olga")

    zed.workspaces.create("Leer")


def roles(store) -> dict[str, dict[str, str | None]]:
    """Every person's effective role on every project, as the M20 code computes it."""
    root = ServiceRoot(store)
    out: dict[str, dict[str, str | None]] = {}
    for project, _ in store.list_projects():
        out[project.id] = {}
        for name, identity in PEOPLE.items():
            visible = {p.id: p.role for p in root.for_actor(identity).projects.list_projects()}
            out[project.id][name] = visible.get(project.id)
    return out


def expected(store) -> dict:
    out: dict = {"era": "M20", "roles": roles(store), "projects": {}, "diagrams": {}, "workspaces": {}}
    for project, _ in store.list_projects():
        out["projects"][project.id] = project.model_dump(mode="json", exclude={"created_at", "updated_at"})
    for info in store.list_diagrams():
        out["diagrams"][info.id] = {
            "fingerprint": fingerprint(store.load(info.id)),
            "versions": [
                {k: v for k, v in i.model_dump().items() if k != "created_at"} for i in store.list_versions(info.id)
            ],
        }
    for key, body, _ in store.list_documents("workspace"):
        out["workspaces"][key] = {"owner": body["owner"], "members": sorted(m["user"] for m in body["members"])}
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
    build(CosmosStore(container))
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
    # refuse code without team workspaces (v0.1.8) and code with personal workspaces (M21)
    from arcviz.services import workspaces as module

    if importlib.util.find_spec("arcviz.workspaces") is None or not hasattr(module, "set_owner"):
        sys.exit("Run this with the M20 code (242b97f) on PYTHONPATH.")
    main(Path(sys.argv[1]))
