"""Element names and descriptions are bounded (#212, ADR-0153; spec: docs/architecture/specs/robustness.md).

New and changed elements and imported bundles respect `MAX_ELEMENT_NAME` / `MAX_ELEMENT_DESCRIPTION`, so the project
item stays below Cosmos DB's 2 MB limit (arc42 R22). The bound is never an integrity rule: a project stored before it
keeps loading, and every write that does not lengthen an oversize field still succeeds."""

from __future__ import annotations

import pytest

from arcviz import exchange, projects
from arcviz.project_model import (
    MAX_ELEMENT_DESCRIPTION,
    MAX_ELEMENT_NAME,
    MAX_ELEMENTS,
    MAX_RELATION_DESCRIPTION,
    MAX_RELATIONS,
    Element,
    Relation,
)
from arcviz.projects import (
    MAX_MEMBERSHIP_BYTES,
    MAX_PROJECT_BYTES,
    Project,
    ProjectError,
    check_document_size,
    document_size,
)
from arcviz.services.projects import DEFAULT_PROJECT_ID, ProjectService

LONG_NAME = "N" * (MAX_ELEMENT_NAME + 1)
LONG_DESCRIPTION = "d" * (MAX_ELEMENT_DESCRIPTION + 1)


def project() -> Project:
    p = Project(id="shop", title="Shop")
    projects.apply(p, "add_element", {"name": "Backend", "id": "backend"})
    return p


def test_new_elements_are_bounded():
    p = project()
    with pytest.raises(ProjectError, match=f"at most {MAX_ELEMENT_NAME}"):
        projects.apply(p, "add_element", {"name": LONG_NAME})
    with pytest.raises(ProjectError, match=f"at most {MAX_ELEMENT_DESCRIPTION}"):
        projects.apply(p, "add_element", {"name": "Kasse", "description": LONG_DESCRIPTION})
    at_bounds = {"name": "N" * MAX_ELEMENT_NAME, "description": "d" * MAX_ELEMENT_DESCRIPTION}
    assert projects.apply(p, "add_element", at_bounds)["result"]["name"] == at_bounds["name"]
    assert [e.id for e in p.elements][0] == "backend" and len(p.elements) == 2


def test_changed_elements_are_bounded_and_nothing_changes_on_a_refusal():
    p = project()
    before = p.model_dump()
    with pytest.raises(ProjectError, match=f"at most {MAX_ELEMENT_NAME}"):
        projects.apply(p, "update_element", {"element": "backend", "name": LONG_NAME, "description": "kurz"})
    with pytest.raises(ProjectError, match=f"at most {MAX_ELEMENT_DESCRIPTION}"):
        projects.apply(p, "update_element", {"element": "backend", "description": LONG_DESCRIPTION})
    assert p.model_dump() == before
    # surrounding whitespace is not counted (it is stripped)
    projects.apply(
        p, "update_element", {"element": "backend", "description": " " + "d" * MAX_ELEMENT_DESCRIPTION + " "}
    )


def test_a_stored_oversize_project_stays_readable_and_writable(store):
    """Never an integrity rule: a project written before the bound loads and takes every other write, including
    changes of the oversize element that do not lengthen its fields."""
    p = project()
    p.elements.append(Element(id="old", name="O" * 500, description="x" * 50_000))
    assert p.integrity_errors() == []
    store.create_project(p)
    service = ProjectService(store, store)
    service.apply("shop", "add_section", {"title": "Neu"})
    service.apply("shop", "update_element", {"element": "old", "kind": "system"}, internal=True)
    service.apply("shop", "add_element", {"name": "Kasse"}, internal=True)
    stored = service.get("shop")
    assert {e.id for e in stored.elements} == {"backend", "old", "kasse"}
    assert stored.find_element("old").description == "x" * 50_000  # kept as it was
    service.apply("shop", "update_element", {"element": "old", "description": "kurz", "name": "Alt"}, internal=True)
    assert service.get("shop").find_element("old").name == "Alt"


def test_imports_shorten_oversize_element_texts_and_say_so():
    """Coordinator review #2: a project stored with longer texts must survive its own export and re-import (a backup):
    over-long names and descriptions are cut with "…" (after stripping, round 2 #5), and the bundle says how many were
    cut."""
    p = project()
    p.elements.append(Element(id="long", name=LONG_NAME, description=LONG_DESCRIPTION))
    p.elements.append(Element(id="wide", name=" " + "W" * 150 + " ", description="w" * 1000))
    projects.apply(p, "add_section", {"title": "Lang"})
    p.sections[0].element = "long"
    bundle = exchange.parse_project(exchange.export_project(p, []))
    imported = {e.id: e for e in bundle.elements}
    for element in ("long", "wide"):
        assert len(imported[element].name) == MAX_ELEMENT_NAME and imported[element].name.endswith("…")
        assert len(imported[element].description) == MAX_ELEMENT_DESCRIPTION
        assert imported[element].description.endswith("…")
    assert imported["backend"].name == "Backend"
    assert bundle.sections[0].title == imported["long"].name  # a section standing for it follows
    assert bundle.warnings == [
        f"2 element names longer than {MAX_ELEMENT_NAME} and 2 element descriptions longer than "
        f"{MAX_ELEMENT_DESCRIPTION} characters were shortened."
    ]
    assert exchange.parse_project(exchange.export_project(project(), [])).warnings == []


def test_the_import_route_reports_shortened_texts_and_refuses_an_oversize_bundle(store, monkeypatch):
    from fastapi.testclient import TestClient

    from arcviz.server import create_app
    from tests.conftest import ScriptedProvider

    client = TestClient(create_app(store=store, provider=ScriptedProvider([])))
    p = project()
    p.elements.append(Element(id="long", name=LONG_NAME))
    imported = client.post("/api/projects/import", json={"bundle": exchange.export_project(p, [])})
    assert imported.status_code == 201, imported.text
    assert imported.json()["warnings"] == [f"1 element name longer than {MAX_ELEMENT_NAME} characters was shortened."]
    worst = at_bounds("€")
    worst.diagrams = []
    refused = client.post("/api/projects/import", json={"bundle": exchange.export_project(worst, [])})
    assert refused.status_code == 400 and "too large" in refused.json()["detail"]


# ---- the size guard of the project document (#212) ----------------------------------------------------------------


def at_bounds(char: str) -> Project:
    """Every element and relation at its bounds (free text of `char`) and 1 000 memberships: the budget's worst case."""
    p = Project(id="p" * 50, title="Projekt", workspace="user_" + "0" * 32)
    p.elements = [
        Element(
            id=f"{'e' * 58}{i:05d}",
            name=f"{i:05d}" + char * (MAX_ELEMENT_NAME - 5),
            kind="subsystem",
            description=char * MAX_ELEMENT_DESCRIPTION,
            parent=f"{'e' * 58}{i - 1:05d}" if i else None,
        )
        for i in range(MAX_ELEMENTS)
    ]
    p.relations = [
        Relation(
            id=f"{'r' * 58}{i:05d}",
            kind="communicates",
            source=p.elements[i].id,
            target=p.elements[(i + 1) % MAX_ELEMENTS].id,
            properties={"protocol": char * 64, "port": char * 64, "direction": "forward", "mode": "async"},
            description=char * MAX_RELATION_DESCRIPTION,
        )
        for i in range(MAX_RELATIONS)
    ]
    p.diagrams = [
        projects.Membership(diagram=f"{'d' * 53}-{i:010d}", added_at="2026-10-11T00:00:00.000000+00:00")
        for i in range(1000)
    ]
    assert p.integrity_errors() == []
    return p


def test_ascii_and_typical_german_projects_at_their_bounds_fit_the_guard_with_margin():
    assert document_size(at_bounds("x")) < 0.75 * MAX_PROJECT_BYTES
    german = at_bounds("x")
    for element in german.elements:  # about one character in twenty is an umlaut or ß (six bytes when escaped)
        element.description = ("Ablaufsteuerung für Zahlungen " * 10)[:MAX_ELEMENT_DESCRIPTION]
        element.name = element.name[:5] + ("Größenprüfung " * 8)[: MAX_ELEMENT_NAME - 5]
    assert document_size(german) < 0.9 * MAX_PROJECT_BYTES


def test_the_three_byte_worst_case_is_refused_cleanly(store):
    """Above the budget as the Cosmos SDK sends it (`€` is six bytes escaped): a ProjectError (HTTP 400), never a
    failed store write or an integrity error."""
    worst = at_bounds("€")
    assert document_size(worst) > MAX_PROJECT_BYTES and worst.integrity_errors() == []
    with pytest.raises(ProjectError, match="too large"):
        check_document_size(worst, None)
    service = ProjectService(store, store)
    with pytest.raises(ProjectError, match="too large"):  # a bundle import of it creates nothing
        service.create_with_sections("Groß", "", [], worst.elements, worst.relations)
    assert [p.id for p, _ in store.list_projects()] == []


def test_an_oversize_stored_project_is_never_locked(store):
    """Only a write that makes a document above the budget larger is refused: shrinking or keeping it passes."""
    worst = at_bounds("€")
    worst.diagrams = []
    store.create_project(worst)
    service = ProjectService(store, store)
    with pytest.raises(ProjectError, match="too large"):
        service.apply(worst.id, "add_section", {"title": "Noch ein Abschnitt"})
    first = worst.elements[0]
    same_size = {"element": first.id, "description": "€" * (MAX_ELEMENT_DESCRIPTION - 1) + "x"}
    service.apply(worst.id, "update_element", same_size, internal=True)  # the same size: passes
    service.apply(worst.id, "remove_relation", {"relation": worst.relations[0].id}, internal=True)  # smaller
    assert len(service.get(worst.id).relations) == MAX_RELATIONS - 1


def sized(below: int) -> Project:
    """The default project at the three-byte worst case, cut down (relations removed) to just below `below` bytes."""
    p = at_bounds("€")
    p.id, p.diagrams = DEFAULT_PROJECT_ID, []
    while document_size(p) >= below:
        p.relations.pop()
    return p


def test_membership_writes_may_overshoot_the_budget_a_little(store):
    """Coordinator review #1: placing or adopting a diagram only adds a membership; it may take a document above
    MAX_PROJECT_BYTES up to MAX_MEMBERSHIP_BYTES, so a create or the repair never fails on a full project."""
    full = sized(MAX_PROJECT_BYTES + 20_000)
    assert MAX_PROJECT_BYTES < document_size(full) < MAX_MEMBERSHIP_BYTES
    store.create_project(full)
    service = ProjectService(store, store)
    with pytest.raises(ProjectError, match="too large"):
        service.apply(full.id, "add_section", {"title": "Mehr Inhalt"})
    store.create("Waise", "waise")  # an orphan: the repair adopts it into the default project
    report = service.reconcile()
    assert report.adopted == ["waise"] and service.locate("waise") == (DEFAULT_PROJECT_ID, None)
    store.create("Neu", "neu")
    assert service.place("neu", DEFAULT_PROJECT_ID, new=True)[0] == DEFAULT_PROJECT_ID


def test_the_repair_never_aborts_on_a_project_over_every_budget(store):
    """Coordinator review #1: above MAX_MEMBERSHIP_BYTES the adoption is skipped (logged, reported, retried by the next
    run), and the rest of the repair goes on."""
    over = at_bounds("€")
    over.id, over.diagrams = DEFAULT_PROJECT_ID, []
    assert document_size(over) > MAX_MEMBERSHIP_BYTES
    store.create_project(over)
    store.create("Waise", "waise")
    report = ProjectService(store, store).reconcile()
    assert report.adopted == [] and report.skipped == ["waise"]


# ---- coordinator review, round 2 ----------------------------------------------------------------------------------


def test_shortened_names_stay_unique_and_duplicates_are_refused():
    """Round 2 #1: two names equal in their first 99 characters stay apart after shortening (case-insensitive, like
    the operations); names that were equal already are refused."""
    p = project()
    common = "Abrechnungssystem " * 6
    p.elements += [
        Element(id="eins", name=common + "Eins"),
        Element(id="zwei", name=common + "Zwei"),
        Element(id="drei", name=common.upper() + "Drei"),
    ]
    names = [e.name for e in exchange.parse_project(exchange.export_project(p, [])).elements]
    assert len({n.casefold() for n in names}) == len(names) == 4
    assert all(len(n) <= MAX_ELEMENT_NAME for n in names)
    assert names[1].endswith("…") and names[2].endswith("… (2)") and names[3].endswith("… (3)")
    twins = project()
    twins.elements.append(Element(id="twin", name="backend"))
    with pytest.raises(exchange.ExchangeError, match="element 'twin' has the same name as element 'backend'"):
        exchange.parse_project(exchange.export_project(twins, []))


def test_views_linked_to_a_shortened_element_follow_its_name():
    """Round 2 #2: the bundle's nodes and lifelines that stand for a shortened element get its new name (through
    sync_element), so the imported views do not start out of sync."""
    from arcviz import operations
    from arcviz.model import Diagram

    p = project()
    p.elements.append(Element(id="long", name=LONG_NAME))
    view = Diagram(id="view", title="Sicht")
    operations.apply(view, "add_node", {"name": "Lang", "kind": "service", "id": "lang"})
    link = {"id": "lang", "element": "long", "name": LONG_NAME, "element_kind": "component"}
    operations.apply(view, "link_element", link)
    p.diagrams = [projects.Membership(diagram="view", added_at="2026-10-11T00:00:00.000000+00:00")]
    bundle = exchange.parse_project(exchange.export_project(p, [view]))
    shortened = next(e.name for e in bundle.elements if e.id == "long")
    assert bundle.diagrams["view"].nodes["lang"].name == shortened


def test_imports_bound_the_stripped_text_like_the_operations():
    """Round 2 #5: surrounding whitespace is stripped first (as add_element does), then bounded."""
    p = project()
    p.elements.append(Element(id="padded", name="  Kasse  " + " " * 200, description="\n kurz \n" + " " * 1000))
    bundle = exchange.parse_project(exchange.export_project(p, []))
    padded = next(e for e in bundle.elements if e.id == "padded")
    assert (padded.name, padded.description) == ("Kasse", "kurz") and bundle.warnings == []


def test_the_repair_adopts_one_by_one_when_the_batch_does_not_fit(store):
    """Round 2 #3: when all orphans of a target together exceed MAX_MEMBERSHIP_BYTES, those that fit are adopted one by
    one; only the rest waits (skipped, retried by the next run)."""
    full = sized(MAX_MEMBERSHIP_BYTES)
    full.description = "x" * (MAX_MEMBERSHIP_BYTES - 250 - document_size(full))  # room for about two memberships
    assert MAX_MEMBERSHIP_BYTES - 260 < document_size(full) <= MAX_MEMBERSHIP_BYTES - 250
    store.create_project(full)
    orphans = [f"waise-{i}" for i in range(5)]
    for orphan in orphans:
        store.create("Waise", orphan)
    report = ProjectService(store, store).reconcile()
    assert report.adopted and set(report.adopted) | set(report.skipped) == set(orphans)
    assert not set(report.adopted) & set(report.skipped)
    assert document_size(store.read_project(DEFAULT_PROJECT_ID)[0]) <= MAX_MEMBERSHIP_BYTES
