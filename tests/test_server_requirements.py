"""The requirement routes (spec: docs/architecture/specs/requirements.md): CRUD, bulk accept and status, traceability."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import requirement_model as rqm
from arcviz.server import create_app
from arcviz.services import ServiceRoot
from arcviz.workspaces import Identity


@pytest.fixture
def client(store):
    c = TestClient(create_app(store=store))
    assert c.post("/api/projects", json={"title": "Shop", "id": "shop"}).status_code == 201
    assert c.post("/api/projects/shop/drivers", json={"title": "Verfügbarkeit"}).status_code == 201
    return c


def create(client: TestClient, raw: str = "Das System soll schnell sein", **body: Any) -> dict[str, Any]:
    response = client.post("/api/projects/shop/requirements", json={"raw": raw, **body})
    assert response.status_code == 201, response.text
    return response.json()["requirement"]


def test_create_read_update_delete(client):
    r = create(client, "Antwortzeit unter 200 ms", source="Workshop", external_id="R-1")
    assert r["id"] == "req1" and r["status"] == "raw" and r["step"] == "raw" and r["coverage"] is None
    got = client.get("/api/projects/shop/requirements/req1").json()["requirement"]
    assert got["raw"] == "Antwortzeit unter 200 ms" and got["trace"] == {
        "drivers": [],
        "scenarios": [],
        "decisions": [],
    }
    patched = client.patch("/api/projects/shop/requirements/req1", json={"theme": "Performance", "priority": "must"})
    assert patched.status_code == 200 and patched.json()["requirement"]["theme"] == "Performance"
    deleted = client.delete("/api/projects/shop/requirements/req1")
    assert deleted.status_code == 200 and deleted.json()["requirement"]["id"] == "req1"
    assert client.get("/api/projects/shop/requirements").json()["total"] == 0


def test_invalid_input_is_a_400_with_a_message_for_the_caller(client):
    r = client.post("/api/projects/shop/requirements", json={"raw": "x", "priority": "urgent"})
    assert r.status_code == 400 and "Unknown priority 'urgent'" in r.json()["detail"]
    r = client.post("/api/projects/shop/requirements", json={"raw": "x", "bogus": 1})
    assert r.status_code == 400 and "unknown argument 'bogus'" in r.json()["detail"]
    r = client.patch("/api/projects/shop/requirements/req9", json={"theme": "x"})
    assert r.status_code == 400 and "Unknown requirement 'req9'" in r.json()["detail"]
    assert client.patch("/api/projects/shop/requirements/req9", json={"raw": "x"}).status_code == 400
    assert client.get("/api/projects/nope/requirements").status_code == 404


def test_listing_filters_and_paging(client):
    for n in range(5):
        create(client, f"Anforderung Nummer {n}", theme="A" if n % 2 else "B")
    page = client.get("/api/projects/shop/requirements", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 5 and [r["id"] for r in page["requirements"]] == ["req2", "req3"]
    assert page["digest"]["steps"]["raw"] == 5 and page["digest"]["themes"] == {"B": 3, "A": 2}
    by_theme = client.get("/api/projects/shop/requirements", params={"theme": "a"}).json()
    assert by_theme["total"] == 2
    assert client.get("/api/projects/shop/requirements", params={"status": "weird"}).status_code == 400
    assert client.get("/api/projects/shop/requirements", params={"limit": 500}).status_code == 400


def test_accept_with_the_revision_and_bulk_status(client, store):
    create(client, "alpha alpha")
    create(client, "beta beta")
    # an assistant's suggestions arrive through the service (the routes for them are the workshop's)
    system(store).suggest_many(
        "shop", [{"requirement": "req1", "statement": "Alpha."}, {"requirement": "req2", "statement": "Beta."}]
    )
    listed = client.get("/api/projects/shop/requirements", params={"suggested": True}).json()["requirements"]
    assert [r["step"] for r in listed] == ["suggested", "suggested"]
    revs = {r["id"]: r["suggestion"]["rev"] for r in listed}
    accepted = client.post(
        "/api/projects/shop/requirements/accept",
        json={"items": [{"id": "req1", "expected": revs["req1"]}, {"id": "req2", "expected": "stale"}]},
    )
    assert accepted.status_code == 200 and accepted.json() == {"accepted": ["req1"], "stale": ["req2"], "skipped": []}
    assert client.get("/api/projects/shop/requirements/req1").json()["requirement"]["step"] == "refined"
    status = client.post(
        "/api/projects/shop/requirements/status", json={"ids": ["req2"], "status": "rejected", "reason": "x"}
    )
    assert status.json()["changed"] == ["req2"]
    assert (
        client.post("/api/projects/shop/requirements/req2/reject-suggestion").json()["requirement"]["suggestion"]
        is None
    )
    assert client.post("/api/projects/shop/requirements/accept", json={"items": [{"id": "req1"}]}).status_code == 422


def system(store):
    """The requirement service of the admin (the identity of an app without authentication)."""
    return ServiceRoot(store).for_actor(Identity("admin", "Admin", None)).requirements  # type: ignore[arg-type]


def test_traceability_and_drivers_naming_requirements(client):
    create(client, "alpha alpha")
    client.patch("/api/projects/shop/requirements/req1", json={"statement": "Alpha."})
    assert client.post("/api/projects/shop/requirements/status", json={"ids": ["req1"], "status": "refined"}).json()[
        "changed"
    ] == ["req1"]
    linked = client.patch("/api/projects/shop/drivers/d1", json={"requirements": ["req1"]})
    assert linked.status_code == 200 and linked.json()["driver"]["requirements"] == ["req1"]
    unknown = client.patch("/api/projects/shop/drivers/d1", json={"requirements": ["req7"]})
    assert unknown.status_code == 400 and "Unknown requirement 'req7'" in unknown.json()["detail"]
    matrix = client.get("/api/projects/shop/traceability").json()
    assert matrix["rows"][0]["coverage"] == "driver" and matrix["rows"][0]["drivers"] == ["d1"]
    assert client.get("/api/projects/shop/requirements/req1").json()["requirement"]["step"] == "derived"
    assert rqm.COVERAGE_LEVELS[1] == "driver"


def test_import_a_list_and_do_it_again(client):
    client.post("/api/projects/shop/stakeholders", json={"name": "Betrieb"})
    text = "text,thema,stakeholder,prio\nAntwortzeit unter 200 ms,Performance,Betrieb,Muss\nDaten verschlüsselt,,Unbekannt,\n,,Betrieb,\n"
    dry = client.post("/api/projects/shop/requirements/import", json={"text": text, "dry_run": True})
    assert dry.status_code == 200
    body = dry.json()
    assert body["format"] == "csv" and body["dry_run"] is True and body["added"] == ["req1", "req2"]
    assert client.get("/api/projects/shop/requirements").json()["total"] == 0  # nothing written
    done = client.post("/api/projects/shop/requirements/import", json={"text": text, "source": "workshop.csv"}).json()
    assert done["added"] == ["req1", "req2"] and done["parsed"] == 2
    assert [p["row"] for p in done["problems"]] == [3, 4]
    assert (
        "unknown stakeholder 'Unbekannt'" in done["problems"][0]["reason"]
        and done["problems"][1]["reason"] == "empty text"
    )
    first = client.get("/api/projects/shop/requirements/req1").json()["requirement"]
    assert (first["theme"], first["priority"], first["stakeholders"], first["source"], first["client"]) == (
        "Performance",
        "must",
        ["s1"],
        "workshop.csv",
        "import",
    )
    again = client.post("/api/projects/shop/requirements/import", json={"text": text, "source": "workshop.csv"}).json()
    assert again["added"] == [] and [x["row"] for x in again["skipped_duplicates"]] == [2, 3]  # the rows of the file


def test_import_lines_from_a_markdown_file(client):
    text = "# Daten\n- Alle Daten sind verschlüsselt\n- Ein Backup pro Nacht\n"
    done = client.post("/api/projects/shop/requirements/import", json={"text": text}).json()
    assert done["format"] == "markdown" and done["added"] == ["req1", "req2"]
    assert client.get("/api/projects/shop/requirements/req2").json()["requirement"]["theme"] == "Daten"


def test_import_errors(client):
    assert client.post("/api/projects/shop/requirements/import", json={"text": "x", "format": "xml"}).status_code == 400
    assert client.post("/api/projects/shop/requirements/import", json={"text": "x" * 1_000_001}).status_code == 422
    assert client.post("/api/projects/nope/requirements/import", json={"text": "x"}).status_code == 404
