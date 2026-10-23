"""Project bundles through the services (spec: docs/architecture/specs/project-exchange.md)."""

from __future__ import annotations

import pytest

from arcviz import exchange
from arcviz.services import DiagramService, ProjectExchangeService, ProjectService
from arcviz.templates import demo_diagram


@pytest.fixture
def services(store):
    projects = ProjectService(store, store)
    diagrams = DiagramService(store, projects)
    return diagrams, projects, ProjectExchangeService(diagrams, projects)


def _shop(diagrams, projects):
    projects.create("Shop", description="Alles")
    projects.apply("shop", "add_section", {"title": "Backend"})
    diagrams.create("Demo", "demo", demo_diagram("demo", "Demo"), "shop", "backend")
    diagrams.create("Second", "second", demo_diagram("second", "Second"), "shop")


def test_import_creates_a_new_project_with_unique_ids(services):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    created = bundles.import_bundle(bundle)
    assert created.id == "shop-2" and created.title == "Shop" and created.description == "Alles"
    assert [s.id for s in created.sections] == ["backend"]
    assert [(m.diagram, m.section) for m in created.diagrams] == [("demo-2", "backend"), ("second-2", None)]
    assert diagrams.get("demo-2").nodes.keys() == diagrams.get("demo").nodes.keys()
    assert [(m.diagram, m.section) for m in projects.get("shop").diagrams] == [("demo", "backend"), ("second", None)]
    again = bundles.import_bundle(bundle, title="Kopie")
    assert again.id == "kopie" and again.title == "Kopie"


def test_import_into_an_empty_installation_keeps_the_ids(services, tmp_path):
    from arcviz.store.file import FileStore

    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    other = FileStore(tmp_path / "other")
    other_projects = ProjectService(other, other)
    target = ProjectExchangeService(DiagramService(other, other_projects), other_projects)
    created = target.import_bundle(bundle)
    assert created.id == "shop" and [m.diagram for m in created.diagrams] == ["demo", "second"]


def test_invalid_bundle_writes_nothing(services, store):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    bundle["diagrams"][1]["diagram"]["nodes"]["service-a"]["kind"] = "nope"
    before = ([d.id for d in store.list_diagrams()], [p.id for p, _ in store.list_projects()])
    with pytest.raises(exchange.ExchangeError):
        bundles.import_bundle(bundle)
    assert ([d.id for d in store.list_diagrams()], [p.id for p, _ in store.list_projects()]) == before


def test_a_failing_write_is_rolled_back(services, store, monkeypatch):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    real = diagrams.import_new
    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(diagrams, "import_new", failing)
    with pytest.raises(RuntimeError):
        bundles.import_bundle(bundle)
    assert sorted(d.id for d in store.list_diagrams()) == ["demo", "second"]
    assert [p.id for p, _ in store.list_projects()] == ["shop"]


def test_export_leaves_out_deleted_members(services, store):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    store.delete("second")  # behind the service's back: a dangling membership
    bundle = bundles.export("shop")
    assert [d["diagram"]["id"] for d in bundle["diagrams"]] == ["demo"]
    assert bundle["project"]["diagrams"] == [{"diagram": "demo", "section": "backend"}]


def test_export_fails_on_a_storage_error_instead_of_leaving_a_diagram_out(services, store, monkeypatch):
    from arcviz.ports import StoreError

    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    real = store.load

    def flaky(diagram_id):
        if diagram_id == "second":
            raise StoreError("Cosmos DB read failed: 408 timeout")
        return real(diagram_id)

    monkeypatch.setattr(store, "load", flaky)
    with pytest.raises(StoreError, match="408"):
        bundles.export("shop")


def test_deleting_a_diagram_succeeds_when_the_membership_cleanup_fails(services, store, monkeypatch):
    from arcviz.ports import ConflictError

    diagrams, projects, bundles = services
    _shop(diagrams, projects)

    def busy(diagram_id):
        raise ConflictError("busy")

    monkeypatch.setattr(projects, "unplace", busy)
    diagrams.delete("second")
    assert not store.exists("second")
    monkeypatch.undo()
    projects.reconcile()
    assert projects.locate("second") is None


# ---- the shared model travels with the bundle (spec: shared-project-model.md, ADR-0077) -----------------------


def test_a_bundle_without_model_is_written_as_before(services):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    assert bundle["schema_version"] == 1
    assert "elements" not in bundle["project"]
    assert all("element" not in s for s in bundle["project"]["sections"])


def test_the_model_round_trips_with_its_links(services):
    from arcviz.services import ModelService
    from arcviz.templates import create_from_template

    diagrams, projects, bundles = services
    models = ModelService(projects, diagrams)
    _shop(diagrams, projects)
    diagrams.create("Checkout", "checkout", create_from_template("checkout", "checkout", "Checkout"), "shop", "backend")
    models.add_element("shop", "Shop", "system", "Der Webshop")
    models.add_element("shop", "Backend", "subsystem", parent="shop")
    node = next(iter(diagrams.get("demo").nodes.values()))
    models.link("demo", node.id, "backend")
    lifeline = diagrams.get("checkout").interaction.lifelines[1]
    models.link("checkout", lifeline.id, "shop")
    models.link_section("shop", "backend", "backend")

    bundle = bundles.export("shop")
    assert bundle["schema_version"] == 2
    assert [e["id"] for e in bundle["project"]["elements"]] == ["shop", "backend"]
    assert bundle["project"]["sections"][0]["element"] == "backend"
    created = bundles.import_bundle(bundle)
    assert [e.model_dump() for e in created.elements] == [e.model_dump() for e in projects.get("shop").elements]
    assert created.sections[0].element == "backend"
    imported = {m.diagram: diagrams.get(m.diagram) for m in created.diagrams}
    assert imported["demo-2"].nodes[node.id].ref == "backend"
    assert imported["checkout-2"].interaction.lifelines[1].ref == "shop"
    usages = {e.id: sorted(u.diagram for u in e.usages) for e in models.model(created.id).elements}
    assert usages == {"shop": ["checkout-2"], "backend": ["demo-2"]}


def test_bundles_with_an_invalid_model_are_refused(services):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    bundle["schema_version"] = 2
    bundle["project"]["elements"] = [{"id": "a", "name": "A", "kind": "robot", "parent": "ghost"}]
    bundle["project"]["sections"][0]["element"] = "nope"
    with pytest.raises(exchange.ExchangeError) as info:
        bundles.import_bundle(bundle)
    text = "\n".join(info.value.errors)
    assert "unknown kind 'robot'" in text and "unknown parent 'ghost'" in text and "unknown element 'nope'" in text
    bundle["schema_version"] = exchange.PROJECT_SCHEMA_VERSION + 1
    with pytest.raises(exchange.ExchangeError, match="newer Arcviz"):
        bundles.import_bundle(bundle)


def test_dangling_refs_are_kept_on_import(services):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    node = next(iter(bundle["diagrams"][0]["diagram"]["nodes"]))
    bundle["diagrams"][0]["diagram"]["nodes"][node]["ref"] = "gone"
    created = bundles.import_bundle(bundle)
    assert diagrams.get(created.diagrams[0].diagram).nodes[node].ref == "gone"


# ---- relations (spec: model-relations.md, ADR-0135) ------------------------------------------------------------


def test_relations_round_trip_with_schema_version_3(services):
    from arcviz.services import ModelService

    diagrams, projects, bundles = services
    models = ModelService(projects, diagrams)
    _shop(diagrams, projects)
    models.add_element("shop", "Web", "component")
    models.add_element("shop", "DB", "data")
    bundle = bundles.export("shop")
    assert bundle["schema_version"] == 2 and "relations" not in bundle["project"]  # a model without relations
    models.add_relation("shop", "data_flow", "web", "db", {"protocol": "SQL"}, "liest")
    bundle = bundles.export("shop")
    assert bundle["schema_version"] == 3
    assert bundle["project"]["relations"] == [
        {
            "id": "r1",
            "kind": "data_flow",
            "source": "web",
            "target": "db",
            "properties": {"protocol": "SQL"},
            "description": "liest",
        }
    ]
    created = bundles.import_bundle(bundle)
    assert [r.model_dump() for r in created.relations] == [r.model_dump() for r in projects.get("shop").relations]


def test_bundles_with_invalid_relations_are_refused(services):
    diagrams, projects, bundles = services
    _shop(diagrams, projects)
    bundle = bundles.export("shop")
    bundle["schema_version"] = 3
    bundle["project"]["elements"] = [{"id": "a", "name": "A", "kind": "component"}]
    bundle["project"]["relations"] = [{"id": "r1", "kind": "deployed_on", "source": "a", "target": "ghost"}]
    with pytest.raises(exchange.ExchangeError) as info:
        bundles.import_bundle(bundle)
    assert "unknown target 'ghost'" in "\n".join(info.value.errors)
