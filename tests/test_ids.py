"""New diagram and project ids reveal nothing about other tenants (#167, spec: docs/architecture/specs/hardening.md,
ADR-0131). Every create path answers the same whether or not another tenant holds the requested slug: the id is
`<requested slug>-<10 random base32 characters>` either way. These tests use the production id generator
(`real_ids`); the rest of the suite pins readable ids (tests/conftest.py)."""

from __future__ import annotations

import re
from typing import Any

import pytest

from arcviz import ports
from arcviz.exchange import export_document
from arcviz.llm.base import ModelReply
from arcviz.ports import ID_RE, ID_SUFFIX_LENGTH, StoreError, id_base, id_suffix, new_ids
from arcviz.services.projects import ProjectService
from arcviz.templates import demo_diagram
from arcviz.workspaces import personal_project_id
from tests.conftest import ScriptedProvider, call, proxy_users

pytestmark = pytest.mark.real_ids
SUFFIX = r"-[a-z2-7]{10}"


def fresh(requested: str, actual: str) -> bool:
    return re.fullmatch(re.escape(requested) + SUFFIX, actual) is not None


# ---- the generator ------------------------------------------------------------------------------------------------


def test_new_ids_are_the_base_plus_a_random_suffix():
    ids = new_ids("orders")
    first, second = next(ids), next(ids)
    assert fresh("orders", first) and fresh("orders", second) and first != second
    assert ID_RE.match(first)


def test_a_long_base_is_cut_to_fit_and_an_empty_one_is_just_the_suffix():
    long = next(new_ids("a" * 64))
    assert len(long) == 64 and ID_RE.match(long) and fresh("a" * 53, long)
    cut = next(new_ids("a" * 52 + "--x"))
    assert fresh("a" * 52, cut)  # no dangling separator before the suffix
    assert re.fullmatch(r"[a-z2-7]{10}", next(new_ids("")))


def test_a_suffix_always_holds_a_digit():
    """#200: so that a generated suffix can be told from a word of ten letters (`processing`, `deployment`)."""
    suffixes = [id_suffix() for _ in range(500)]
    assert all(len(s) == ID_SUFFIX_LENGTH and re.search(r"[2-7]", s) for s in suffixes)
    assert all(re.fullmatch(r"[a-z2-7]{10}", s) for s in suffixes) and len(set(suffixes)) == 500


@pytest.mark.parametrize(
    ("requested", "base"),
    [
        ("orders-k3jdhs7a2b", "orders"),  # a generated suffix: replaced
        ("my-orders-x2aaaaaaaa", "my-orders"),
        ("k3jdhs7a2b", ""),  # an id that is only a suffix (an empty base)
        ("orders-abcdefghij", "orders-abcdefghij"),  # ten letters, no digit: a word, or a suffix from before M26
        ("order-processing", "order-processing"),
        ("monitoring", "monitoring"),  # ten letters, alone
        ("repository", "repository"),
        ("code-repository", "code-repository"),
        ("k8s-deployment", "k8s-deployment"),
        ("orders-k3jdhs7a2", "orders-k3jdhs7a2"),  # nine characters
        ("orders-k3jdhs7a2bb", "orders-k3jdhs7a2bb"),  # eleven
        ("orders-k3jdhs7a1b", "orders-k3jdhs7a1b"),  # 1 is not base32
        ("orders_k3jdhs7a2b", "orders_k3jdhs7a2b"),  # only a dash separates a suffix
        ("orders", "orders"),
    ],
)
def test_id_base_strips_one_generated_suffix(requested, base):
    assert id_base(requested) == base


def test_new_ids_replace_a_generated_suffix_of_the_base():
    """#200: a re-imported or requested `<slug>-<suffix>` gets a new suffix instead of a second one."""
    first = next(new_ids("orders"))
    again = next(new_ids(first))
    assert fresh("orders", again) and again != first
    assert fresh("order-processing", next(new_ids("order-processing")))


@pytest.mark.parametrize("word", ["order-processing", "monitoring", "repository"])
def test_ten_letter_words_keep_their_words(word):
    """#200: a word of ten base32 letters is never taken for a generated suffix."""
    assert fresh(word, next(new_ids(word)))


def test_a_round_trip_of_a_new_id_is_stable():
    """#200: re-using a new id as the base again and again keeps its base and its length."""
    current = next(new_ids("orders"))
    for _ in range(20):
        following = next(new_ids(current))
        assert fresh("orders", following) and len(following) == len(current) and following != current
        current = following


def test_production_is_wired_to_random_ids():
    assert ports.new_ids is new_ids


# ---- every create path, with and without another tenant holding the slug ----------------------------------------


@pytest.fixture
def world(store, monkeypatch):
    """ada holds the project slug "shop" and the diagram slug "orders" (in her own workspace, nothing shared). Ids
    created before M23 are exact, so ada's data is written with legacy ids through the store."""
    provider = ScriptedProvider([])
    users = proxy_users(store, monkeypatch, provider=provider)
    assert users("ada", "GET", "/api/me").status_code == 200
    assert users("eve", "GET", "/api/me").status_code == 200
    from arcviz import projects

    project = projects.new_project("Shop", "shop", "", "user_" + "0" * 32)
    store.create_project(project)
    store.create("Orders", "orders", demo_diagram("orders", "Orders"))
    users.provider = provider
    return users


def creates(users, slug: str) -> dict[str, tuple[int, str]]:
    """(status, id) of every create path when eve asks for `slug`."""
    out: dict[str, tuple[int, str]] = {}

    def diagram_id(response: Any) -> tuple[int, str]:
        return response.status_code, response.json()["diagram"]["id"]

    out["diagram id"] = diagram_id(users("eve", "POST", "/api/diagrams", json={"title": "X", "id": slug}))
    out["diagram title"] = diagram_id(users("eve", "POST", "/api/diagrams", json={"title": slug}))
    out["template"] = diagram_id(
        users("eve", "POST", "/api/diagrams", json={"title": "Demo", "id": slug, "template": "demo"})
    )
    document = export_document(demo_diagram(slug, "Imported"))
    imported = users("eve", "POST", "/api/diagrams/import", json={"document": document})
    out["import"] = (imported.status_code, imported.json()["diagram"]["id"])
    imported = users("eve", "POST", "/api/diagrams/import", json={"document": document, "id": slug})
    out["import id"] = (imported.status_code, imported.json()["diagram"]["id"])
    project = users("eve", "POST", "/api/projects", json={"title": "Y", "id": slug})
    out["project id"] = (project.status_code, project.json()["project"]["id"])
    project = users("eve", "POST", "/api/projects", json={"title": slug})
    out["project title"] = (project.status_code, project.json()["project"]["id"])
    return out


def test_creates_answer_the_same_whether_or_not_another_tenant_holds_the_slug(world):
    taken = creates(world, "orders") | {k: v for k, v in creates(world, "shop").items() if k.startswith("project")}
    free = creates(world, "nothing-here")
    for path in taken:
        status, actual = taken[path]
        free_status, free_actual = free[path]
        assert status == free_status == 201, path
        requested_taken = "shop" if path.startswith("project") else "orders"
        assert fresh(requested_taken, actual), (path, actual)
        assert fresh("nothing-here", free_actual), (path, free_actual)


def test_a_project_bundle_import_gets_fresh_ids_for_the_project_and_its_diagrams(world):
    created = world("eve", "POST", "/api/projects", json={"title": "Mine"}).json()["project"]["id"]
    world(
        "eve",
        "POST",
        "/api/diagrams",
        json={"title": "Orders", "id": "orders", "template": "demo", "project": created},
    )
    bundle = world("eve", "GET", f"/api/projects/{created}/export").json()
    bundle["project"]["title"] = "shop"
    view = world("eve", "POST", "/api/projects/import", json={"bundle": bundle})
    assert view.status_code == 201
    body = view.json()
    assert fresh("shop", body["project"]["id"])
    diagram_ids = [d["id"] for d in body["diagrams"]]
    # the exported id's suffix is replaced, not extended (#200)
    assert diagram_ids and all(fresh("orders", d) for d in diagram_ids), diagram_ids


def test_the_project_chats_create_diagram_gets_a_fresh_id(world):
    project_id = world("eve", "POST", "/api/projects", json={"title": "Mine"}).json()["project"]["id"]
    world.provider.replies[:] = [
        ModelReply(
            text="",
            tool_calls=[call("create_diagram", "c1", title="Orders", type="deployment")],
            stop_reason="tool_use",
        ),
        ModelReply(text="fertig", tool_calls=[], stop_reason="end_turn"),
    ]
    response = world("eve", "POST", f"/api/projects/{project_id}/chat", json={"message": "neues Diagramm"})
    assert response.status_code == 200
    view = world("eve", "GET", f"/api/projects/{project_id}").json()
    assert [d["id"] for d in view["diagrams"] if fresh("orders", d["id"])], view["diagrams"]


def test_a_squatted_default_project_falls_back_to_a_fresh_id(store, monkeypatch):
    from arcviz import projects

    users = proxy_users(store, monkeypatch)  # the start-up migration ran already; this squat comes later
    squatter = projects.new_project("Not yours", personal_project_id("eve"), "", "user_" + "1" * 32)
    store.create_project(squatter)
    created = users("eve", "POST", "/api/diagrams", json={"title": "Erstes"})
    assert created.status_code == 201
    assert fresh(personal_project_id("eve"), created.json()["access"]["project"])


def test_a_create_whose_leftovers_cannot_be_cleared_takes_a_fresh_random_id(world, monkeypatch):
    real_vacate = ProjectService.vacate
    failed: list[str] = []

    def vacate_once(self, diagram_id):
        if not failed:
            failed.append(diagram_id)
            raise StoreError("throttled")
        return real_vacate(self, diagram_id)

    monkeypatch.setattr(ProjectService, "vacate", vacate_once)
    created = world("eve", "POST", "/api/diagrams", json={"title": "X", "id": "orders"})
    assert created.status_code == 201
    actual = created.json()["diagram"]["id"]
    assert failed and fresh("orders", failed[0]) and fresh("orders", actual) and actual != failed[0]


def test_round_trips_keep_one_suffix(world):
    """#200: each export and re-import of a diagram or a project replaces the suffix; ids never grow."""
    project_id = world("eve", "POST", "/api/projects", json={"title": "Shop"}).json()["project"]["id"]
    diagram = {"title": "Orders", "template": "demo", "project": project_id}
    diagram_id = world("eve", "POST", "/api/diagrams", json=diagram).json()["diagram"]["id"]
    for _ in range(3):
        document = world("eve", "GET", f"/api/diagrams/{diagram_id}/export").json()
        imported = world("eve", "POST", "/api/diagrams/import", json={"document": document})
        assert imported.status_code == 201
        assert fresh("orders", imported.json()["diagram"]["id"]), imported.json()["diagram"]["id"]
        bundle = world("eve", "GET", f"/api/projects/{project_id}/export").json()
        imported = world("eve", "POST", "/api/projects/import", json={"bundle": bundle})
        assert imported.status_code == 201, imported.text
        project_id = imported.json()["project"]["id"]
        diagram_ids = [d["id"] for d in imported.json()["diagrams"]]
        assert fresh("shop", project_id) and len(diagram_ids) == 1 and fresh("orders", diagram_ids[0]), diagram_ids
        diagram_id = diagram_ids[0]


def test_an_invalid_requested_id_is_still_400(world):
    assert world("eve", "POST", "/api/diagrams", json={"title": "X", "id": "Bad Id"}).status_code == 400
    assert world("eve", "POST", "/api/projects", json={"title": "X", "id": "Bad Id"}).status_code == 400


@pytest.mark.parametrize("slug_length", [50, 53])
def test_a_long_exported_id_keeps_its_slug_on_import(world, slug_length):
    """Coordinator review #6: ids of 61 to 64 characters lost part of their suffix to the 60-character cut before the
    suffix was stripped, and kept the rest of it in the new id."""
    slug = "o" * slug_length
    exported = f"{slug}-k3jdhs7a2b"
    assert 61 <= len(exported) <= 64
    document = export_document(demo_diagram(exported, "Lang"))
    imported = world("eve", "POST", "/api/diagrams/import", json={"document": document})
    assert imported.status_code == 201
    assert fresh(slug, imported.json()["diagram"]["id"]), imported.json()["diagram"]["id"]
