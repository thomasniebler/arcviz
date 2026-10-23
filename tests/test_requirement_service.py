"""The requirement service over the file and sqlite stores (spec: docs/architecture/specs/requirements.md)."""

from __future__ import annotations

from typing import Any

import pytest

from arcviz import requirement_model as rqm
from arcviz.projects import ProjectError
from arcviz.requirement_model import RequirementError
from arcviz.services import Forbidden, ServiceRoot
from arcviz.services.access import ProjectNotFound
from arcviz.workspaces import Identity

PEOPLE = {name: Identity(name, name.capitalize(), f"{name}@example.org") for name in ("ada", "ed", "vic", "out")}


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in PEOPLE.values():
        root.users.seen(identity)
    ada = root.for_actor(PEOPLE["ada"])
    ada.projects.create("Shop", "shop")
    ada.projects.share("shop", "ed", "editor")
    ada.projects.share("shop", "vic", "viewer")
    ada.projects.apply("shop", "add_stakeholder", {"name": "Betrieb"})
    ada.projects.apply("shop", "add_driver", {"title": "Verfügbarkeit"})
    return root


def as_(root: ServiceRoot, name: str):
    return root.for_actor(PEOPLE[name])


def add(root: ServiceRoot, raw: str = "Das System soll schnell sein", who: str = "ed", **args: Any) -> str:
    out = as_(root, who).requirements.apply("shop", "add_requirement", {"raw": raw, **args})
    return out["result"]["id"]


def refine(root: ServiceRoot, rid: str, statement: str = "Das System antwortet in 200 ms.") -> None:
    svc = as_(root, "ed").requirements
    svc.apply("shop", "update_requirement", {"requirement": rid, "statement": statement})
    svc.apply("shop", "set_status", {"requirement": rid, "status": "refined"})


# ---- access ----------------------------------------------------------------------------------------------------


def test_editors_write_viewers_read_strangers_get_not_found(root):
    rid = add(root)
    assert as_(root, "vic").requirements.page("shop")["total"] == 1
    with pytest.raises(Forbidden):
        as_(root, "vic").requirements.apply("shop", "add_requirement", {"raw": "x"})
    with pytest.raises(ProjectNotFound):
        as_(root, "out").requirements.page("shop")
    with pytest.raises(ProjectNotFound):
        as_(root, "out").requirements.apply("shop", "add_requirement", {"raw": "x"})
    assert as_(root, "vic").requirements.get("shop", rid)["raw"] == "Das System soll schnell sein"


def test_a_write_records_author_and_client(root):
    out = as_(root, "ed").requirements.apply("shop", "add_requirement", {"raw": "x y z"}, client="mcp")
    assert (out["result"]["author"], out["result"]["author_name"], out["result"]["client"]) == ("ed", "Ed", "mcp")


def test_agents_may_not_decide(root):
    rid = add(root)
    svc = as_(root, "ed").requirements
    svc.apply("shop", "update_requirement", {"requirement": rid, "theme": "Performance"}, agent=True)
    for op, args in (
        ("set_status", {"requirement": rid, "status": "rejected"}),
        ("remove_requirement", {"requirement": rid}),
        ("update_requirement", {"requirement": rid, "statement": "x"}),
    ):
        with pytest.raises(rqm.Rejected):
            svc.apply("shop", op, args, agent=True)
    assert as_(root, "ed").requirements.get("shop", rid)["theme"] == "Performance"


def test_references_to_stakeholders_are_checked(root):
    add(root, stakeholders=["s1"])
    with pytest.raises(RequirementError, match="Unknown stakeholder 's9'"):
        add(root, "another one", stakeholders=["s9"])


# ---- reads -----------------------------------------------------------------------------------------------------


def test_page_filters_digest_and_coverage(root):
    a, b, c = add(root, "alpha alpha"), add(root, "beta beta", theme="Daten"), add(root, "gamma gamma")
    refine(root, a, "Alpha.")
    refine(root, b, "Beta.")
    ada = as_(root, "ada")
    ada.projects.apply("shop", "update_driver", {"driver": "d1", "requirements": [a]})
    svc = as_(root, "vic").requirements
    everything = svc.page("shop")
    assert everything["total"] == 3 and [r["id"] for r in everything["requirements"]] == [a, b, c]
    assert everything["digest"]["steps"] == {
        "raw": 1,
        "suggested": 0,
        "refined": 1,
        "derived": 1,
        "rejected": 0,
        "duplicate": 0,
    }
    assert [r["id"] for r in svc.page("shop", coverage="driver")["requirements"]] == [a]
    assert [r["id"] for r in svc.page("shop", coverage="uncovered")["requirements"]] == [b]
    assert [r["id"] for r in svc.page("shop", coverage="none")["requirements"]] == [c]
    assert [r["id"] for r in svc.page("shop", status="raw")["requirements"]] == [c]
    assert [r["id"] for r in svc.page("shop", theme="daten")["requirements"]] == [b]
    assert [r["id"] for r in svc.page("shop", q="GAMMA")["requirements"]] == [c]
    assert svc.page("shop", limit=1, offset=1)["requirements"][0]["id"] == b
    assert svc.page("shop", limit=1, offset=1)["total"] == 3
    with pytest.raises(RequirementError, match="Unknown status 'weird'"):
        svc.page("shop", status="weird")
    with pytest.raises(RequirementError, match="limit is 1 to 200"):
        svc.page("shop", limit=0)
    assert svc.get("shop", a)["trace"] == {"drivers": ["d1"], "scenarios": [], "decisions": []}
    assert svc.get("shop", a)["coverage"] == "driver" and svc.get("shop", a)["step"] == "derived"


def test_unknown_requirement_names_the_known_ones(root):
    add(root)
    with pytest.raises(RequirementError, match="Unknown requirement 'nope' in project 'shop'. Requirements: req1"):
        as_(root, "ed").requirements.get("shop", "nope")


def test_traceability_matrix(root):
    a = add(root, "alpha alpha")
    refine(root, a, "Alpha.")
    as_(root, "ada").projects.apply(
        "shop", "link_requirements", {"target_kind": "driver", "target": "d1", "requirements": [a]}
    )
    matrix = as_(root, "vic").requirements.traceability("shop")
    assert matrix["rows"] == [
        {"id": a, "statement": "Alpha.", "coverage": "driver", "drivers": ["d1"], "scenarios": [], "decisions": []}
    ]
    assert matrix["objects"]["drivers"] == {"d1": "Verfügbarkeit"}


# ---- import ----------------------------------------------------------------------------------------------------


def test_import_is_idempotent_and_reports_rejections(root):
    items = [
        {"raw": "Erste Anforderung", "external_id": "R-1"},
        {"raw": "Zweite Anforderung", "external_id": "R-2", "priority": "must"},
        {"raw": "Dritte Anforderung", "priority": "urgent"},  # rejected: not a MoSCoW priority
        {"raw": "ERSTE   anforderung"},  # the same text as the first
        {"raw": "Vierte", "external_id": "R-2"},  # the same external id as the second
    ]
    svc = as_(root, "ed").requirements
    out = svc.import_items("shop", items, source="csv", client="import")
    assert out["added"] == ["req1", "req2"]
    assert [x["row"] for x in out["rejected"]] == [3] and "Unknown priority" in out["rejected"][0]["reason"]
    assert [x["row"] for x in out["skipped_duplicates"]] == [4, 5]
    again = svc.import_items("shop", items[:2], source="csv")
    assert again["added"] == [] and len(again["skipped_duplicates"]) == 2
    page = as_(root, "vic").requirements.page("shop")
    assert (
        page["total"] == 2
        and page["requirements"][0]["source"] == "csv"
        and page["requirements"][0]["client"] == "import"
    )


def test_import_is_bounded(root):
    with pytest.raises(RequirementError, match="At most 500"):
        as_(root, "ed").requirements.import_items("shop", [{"raw": f"r {n}"} for n in range(501)])


def test_import_a_few_hundred_in_one_write(root):
    items = [{"raw": f"Anforderung {n} an Modul {n % 40}", "external_id": f"R-{n}"} for n in range(450)]
    out = as_(root, "ed").requirements.import_items("shop", items, source="jira")
    assert len(out["added"]) == 450 and out["rejected"] == []
    assert as_(root, "vic").requirements.page("shop", limit=200)["total"] == 450


# ---- suggestions and bulk accept -------------------------------------------------------------------------------


def test_suggest_many_and_accept_with_the_revision(root):
    a, b = add(root, "alpha alpha"), add(root, "beta beta")
    svc = as_(root, "ed").requirements
    out = svc.suggest_many(
        "shop",
        [
            {"requirement": a, "statement": "Alpha."},
            {"requirement": b, "statement": "Beta.", "kind": "quality", "stakeholders": ["s1"]},
            {"requirement": "nope", "statement": "X."},
        ],
        client="mcp",
    )
    assert out["stored"] == [a, b] and out["rejected"][0]["requirement"] == "nope"
    seen = {r["id"]: r["suggestion"]["rev"] for r in svc.page("shop")["requirements"]}
    assert svc.get("shop", a)["suggestion"]["client"] == "mcp" and svc.get("shop", a)["statement"] == ""
    svc.suggest_many("shop", [{"requirement": b, "statement": "Beta (neu)."}])  # replaced after the user looked
    result = svc.accept("shop", [{"id": a, "expected": seen[a]}, {"id": b, "expected": seen[b]}])
    assert result == {"accepted": [a], "stale": [b], "skipped": []}
    assert svc.get("shop", a)["status"] == "refined" and svc.get("shop", b)["status"] == "raw"
    again = svc.accept("shop", [{"id": a, "expected": seen[a]}])  # nothing left to accept
    assert again["stale"] == [a]


def test_bulk_status(root):
    a, b = add(root, "alpha alpha"), add(root, "beta beta")
    out = as_(root, "ed").requirements.set_status_many("shop", [a, b, "nope"], "rejected", reason="außer Scope")
    assert out["changed"] == [a, b] and out["skipped"][0]["id"] == "nope"
    assert as_(root, "ed").requirements.get("shop", a)["reason"] == "außer Scope"
    dup = as_(root, "ed").requirements.set_status_many("shop", [b], "duplicate", duplicate_of=a)
    assert dup["changed"] == [b]


# ---- links and cascades ----------------------------------------------------------------------------------------


def test_drivers_may_only_name_requirements_that_exist(root):
    a = add(root)
    ada = as_(root, "ada")
    ada.projects.apply("shop", "update_driver", {"driver": "d1", "requirements": [a]})
    with pytest.raises(ProjectError, match="Unknown requirement 'req9' in project 'shop'. Requirements: req1"):
        ada.projects.apply("shop", "update_driver", {"driver": "d1", "requirements": ["req9"]})
    with pytest.raises(ProjectError, match="Unknown requirement 'req9'"):
        ada.projects.apply(
            "shop", "link_requirements", {"target_kind": "driver", "target": "d1", "requirements": ["req9"]}
        )
    assert ada.projects.get("shop").drivers[0].requirements == [a]


def test_removing_a_requirement_strips_it_from_the_project(root):
    a, b = add(root, "alpha alpha"), add(root, "beta beta")
    ada = as_(root, "ada")
    ada.projects.apply("shop", "update_driver", {"driver": "d1", "requirements": [a, b]})
    ada.projects.apply("shop", "add_scenario", {"title": "Ausfall", "requirements": [a]})
    as_(root, "ed").requirements.remove("shop", a)
    project = ada.projects.get("shop")
    assert project.drivers[0].requirements == [b] and project.scenarios[0].requirements == []
    assert as_(root, "ada").requirements.ids("shop") == {b}
    assert (
        as_(root, "ed").requirements.apply("shop", "add_requirement", {"raw": "gamma gamma"})["result"]["id"] == "req3"
    )


def test_removing_a_stakeholder_or_element_cleans_the_requirements(root):
    ada = as_(root, "ada")
    ada.models.add_element("shop", "API", "component")
    rid = add(root, stakeholders=["s1"], elements=["api"])
    ada.projects.apply("shop", "remove_stakeholder", {"stakeholder": "s1"})
    assert as_(root, "vic").requirements.get("shop", rid)["stakeholders"] == []
    ada.models.remove_element("shop", "api")
    assert as_(root, "vic").requirements.get("shop", rid)["elements"] == []
    assert as_(root, "vic").requirements.page("shop")["dangling"] == {"stakeholders": 0, "elements": 0}


def test_sync_repairs_dangling_references_on_both_sides(root):
    a = add(root, stakeholders=["s1"])
    ada = as_(root, "ada")
    ada.projects.apply("shop", "update_driver", {"driver": "d1", "requirements": [a]})
    # damage both sides behind the service's back: a removed stakeholder and a removed requirement left references
    project = root._repo.read_project("shop")[0]
    project.stakeholders.clear()
    project.drivers[0].requirements.append("req77")
    root._repo.commit_project(project, root._repo.read_project("shop")[1])
    view = as_(root, "vic").requirements.page("shop")
    assert view["requirements"][0]["stakeholders"] == [] and view["dangling"]["stakeholders"] == 1
    counts = ada.requirements.sync("shop")
    assert counts == {"requirements": 1, "project": 1}
    assert ada.projects.get("shop").drivers[0].requirements == [a]
    assert ada.requirements.sync("shop") == {"requirements": 0, "project": 0}


def test_a_project_created_again_starts_without_the_old_requirements(root):
    add(root)
    ada = as_(root, "ada")
    ada.projects.delete("shop")
    ada.projects.create("Shop", "shop")
    assert ada.requirements.page("shop")["total"] == 0


def test_the_document_is_bounded(root, monkeypatch):
    monkeypatch.setattr(rqm, "MAX_REQUIREMENTS_BYTES", 2000)
    svc = as_(root, "ed").requirements
    svc.apply("shop", "add_requirement", {"raw": "a" * 900})
    with pytest.raises(RequirementError, match="too large"):
        svc.apply("shop", "add_requirement", {"raw": "b" * 1400})
    assert svc.page("shop")["total"] == 1
