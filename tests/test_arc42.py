"""The arc42 export (spec: docs/architecture/specs/arc42-export.md, ADR-0207, ADR-0208)."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcviz import arc42, arc42_export, arc42_html, projects
from arcviz.arc42 import Bullets, Figure, Paragraph, Table
from arcviz.model import Diagram
from arcviz.project_review import ProjectSnapshot, run_project_checks
from arcviz.projects import Membership, ProjectError
from arcviz.server import create_app

HOSTILE = "<img src=x onerror=alert(1)> **bold** | pipe `tick` {attr} [link](http://x)"


def op(p: projects.Project, operation: str, **args: Any) -> Any:
    return projects.apply(p, operation, args)["result"]


def seeded() -> ProjectSnapshot:
    p = projects.new_project("Shop", "shop", "Ein Shop.\n\nMit zwei Absätzen.")
    op(p, "set_vision", statement="Schnell einkaufen", goals=["Wenige Klicks"], non_goals=["Kein Marktplatz"])
    op(p, "add_driver", title="Verfügbarkeit", kind="quality_goal", importance="high", risk="high")
    op(p, "add_driver", title="Budget", kind="constraint")
    op(p, "add_driver", title="Ausfall des Zahlungsdienstes", kind="risk")
    op(p, "add_scenario", title="Ausfall", driver="d1", core=True, stimulus="Knoten fällt aus", response="Failover")
    op(p, "add_stakeholder", name="Betrieb", role="SRE", concerns="ruhige Nächte")
    d = op(p, "add_decision", title="Aktiv/Aktiv", status="accepted", decision="Zwei Regionen", context="Ausfall")
    assert d["id"] == "adr1"
    p.elements = []
    return ProjectSnapshot(p, [])


def diagram(diagram_id: str, title: str, type_: str = "deployment") -> Diagram:
    return Diagram(id=diagram_id, title=title, type=type_)


def test_twelve_sections_in_order_with_gaps_for_what_is_missing():
    doc = arc42.build(ProjectSnapshot(projects.new_project("Leer", "leer"), []))
    assert [s.id for s in doc.sections] == [f"{n:02d}" for n in range(1, 13)]
    assert all(s.gaps for s in doc.sections if s.id in ("03", "04", "05", "06", "07", "08", "09", "10", "12"))
    assert any("Vision" in g for g in doc.sections[0].gaps)


def test_the_model_fills_the_sections():
    snap = seeded()
    doc = arc42.build(snap, run_project_checks(snap))
    first, constraints, strategy = doc.sections[0], doc.sections[1], doc.sections[3]
    text = arc42_export.to_markdown(doc)
    assert "Schnell einkaufen" in text and "Wenige Klicks" in text and "Kein Marktplatz" in text
    assert any(isinstance(b, Table) and "Verfügbarkeit" in b.rows[0] for b in first.blocks)
    assert any(isinstance(b, Table) and "Budget" in b.rows[0] for b in constraints.blocks)
    assert strategy.blocks and "ADR-1" in strategy.blocks[0].items[0]  # type: ignore[union-attr]
    assert "Ausfall des Zahlungsdienstes" in text and "Betrieb" in text and "Failover" in text


def test_two_builds_are_equal():
    snap = seeded()
    assert arc42_export.to_markdown(arc42.build(snap)) == arc42_export.to_markdown(arc42.build(snap))


def test_retired_objects_and_unaccepted_decisions_stay_out():
    snap = seeded()
    op(snap.project, "update_driver", driver="d2", status="retired")
    op(snap.project, "add_decision", title="Entwurf XY", status="draft")
    text = arc42_export.to_markdown(arc42.build(snap))
    assert "Budget" not in text and "Entwurf XY" not in text and "1 Entwürfe" in text


def test_notes_lead_their_section_and_are_bounded():
    snap = seeded()
    op(snap.project, "set_arc42_note", section="8", text="Logging: strukturiert.\n\nFehler: einheitlich.")
    doc = arc42.build(snap)
    eight = doc.sections[7]
    assert [b.text for b in eight.blocks if isinstance(b, Paragraph)] == [
        "Logging: strukturiert.",
        "Fehler: einheitlich.",
    ]
    assert not eight.gaps
    assert snap.project.model_dump(mode="json")["arc42_notes"] == {
        "08": "Logging: strukturiert.\n\nFehler: einheitlich."
    }
    op(snap.project, "set_arc42_note", section="08", text="")
    assert "arc42_notes" not in snap.project.model_dump(mode="json")
    with pytest.raises(ProjectError):
        op(snap.project, "set_arc42_note", section="13", text="x")
    with pytest.raises(ProjectError):
        op(snap.project, "set_arc42_note", section="01", text="x" * 8001)


def test_note_is_proposable_but_not_an_agent_tool():
    from arcviz.proposals import PROPOSABLE_OPERATIONS

    assert "set_arc42_note" in PROPOSABLE_OPERATIONS


def test_figures_follow_the_diagram_type():
    snap = seeded()
    seq, dep, overview = (
        diagram("seq", "Bestellung", "sequence"),
        diagram("dep", "Verteilung"),
        diagram("ov", "Übersicht Kontext"),
    )
    snap.members.extend([(Membership(diagram=d.id, section=None, added_at=""), d) for d in (seq, dep, overview)])
    doc = arc42.build(snap)
    kinds = {s.id: [b.diagram for b in s.blocks if isinstance(b, Figure)] for s in doc.sections}
    assert kinds["06"] == ["seq"] and kinds["07"] == ["dep"] and kinds["03"] == ["ov"]
    md = arc42_export.to_markdown(doc)
    assert "![Bestellung](figures/seq.svg)" in md and "```mermaid" in md
    assert set(arc42_export.svg_figures(doc)) == {"figures/seq.svg"}


# ---- hostile text --------------------------------------------------------------------------------------------------


def hostile() -> arc42.Document:
    p = projects.new_project(HOSTILE, "x", HOSTILE)
    op(p, "add_driver", title=HOSTILE, kind="quality_goal", description=HOSTILE)
    op(p, "set_vision", statement=HOSTILE, goals=[HOSTILE])
    op(p, "set_arc42_note", section="08", text=HOSTILE)
    return arc42.build(ProjectSnapshot(p, []))


def test_the_page_escapes_everything():
    html = arc42_html.page(hostile(), figure_url="/f")
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<script" not in html.replace('<script src="/static/vendor/mermaid.min.js"></script>', "").replace(
        '<script src="/static/arc42/page.js"></script>', ""
    )


def test_markdown_and_asciidoc_escape_everything():
    doc = hostile()
    markdown = arc42_export.to_markdown(doc)
    assert (
        not re.search(r"(?<!\\)<img", markdown)
        and "\\<img" in markdown
        and "\\|" in markdown
        and "\\[link\\]" in markdown
    )
    asciidoc = arc42_export.to_asciidoc(doc)
    assert "<img" not in asciidoc and "&#60;img" in asciidoc and "&#124;" in asciidoc and "&#123;attr&#125;" in asciidoc


def test_a_fence_outgrows_the_source():
    assert arc42_export._fence("a\n````\nb", "`", 3) == "`````"
    assert arc42_export._fence("x", "-", 4) == "----"


# ---- the routes ----------------------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCVIZ_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def project_with_content(client: TestClient) -> str:
    pid = client.post("/api/projects", json={"title": "Shop"}).json()["project"]["id"]
    client.post(f"/api/projects/{pid}/drivers", json={"title": "Verfügbarkeit", "kind": "quality_goal"})
    client.put(f"/api/projects/{pid}/vision", json={"statement": "Schnell", "goals": ["A"]})
    client.post("/api/diagrams", json={"title": "Bestellung", "type": "sequence", "template": "empty", "project": pid})
    client.post("/api/diagrams", json={"title": "Kontext Übersicht", "template": "empty", "project": pid})
    return pid


def test_the_page_is_html_with_a_strict_policy(client: TestClient):
    pid = project_with_content(client)
    response = client.get(f"/project/{pid}/arc42")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "Einführung und Ziele" in body and "Schnell" in body and 'class="mermaid"' in body and "<img src=" in body
    one = client.get(f"/project/{pid}/arc42/09")
    assert 'id="s09"' in one.text and 'id="s02"' not in one.text
    assert client.get(f"/project/{pid}/arc42/13").status_code == 404
    assert client.get("/project/missing/arc42").status_code == 404


def test_english_headings_follow_accept_language(client: TestClient):
    pid = project_with_content(client)
    body = client.get(f"/project/{pid}/arc42", headers={"Accept-Language": "en"}).text
    assert "Introduction and goals" in body and "Einführung" not in body


def test_json_markdown_zip_docx_and_figures(client: TestClient):
    pid = project_with_content(client)
    data = client.get(f"/api/projects/{pid}/arc42").json()
    assert [s["id"] for s in data["sections"]][0] == "01" and len(data["sections"]) == 12
    md = client.get(f"/api/projects/{pid}/arc42.md")
    assert md.headers["content-disposition"].startswith("attachment") and md.text.startswith("# Shop")
    for fmt, name in (("asciidoc", "arc42/index.adoc"), ("markdown", "arc42/index.md")):
        archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{pid}/arc42.zip?format={fmt}").content))
        assert name in archive.namelist() and "arc42/figures/" in "".join(archive.namelist())
    assert client.get(f"/api/projects/{pid}/arc42.zip?format=pdf").status_code == 422
    docx = client.get(f"/api/projects/{pid}/arc42.docx")
    assert docx.status_code == 200 and zipfile.is_zipfile(io.BytesIO(docx.content))
    diagrams = client.get(f"/api/projects/{pid}/diagrams").json()
    by_title = {d["title"]: d["id"] for d in diagrams}
    svg = client.get(f"/api/projects/{pid}/arc42/figures/{by_title['Bestellung']}.svg")
    assert svg.status_code == 200 and svg.headers["content-type"] == "image/svg+xml"
    assert svg.headers["content-security-policy"].startswith("sandbox")
    assert client.get(f"/api/projects/{pid}/arc42/figures/{by_title['Kontext Übersicht']}.svg").status_code == 400
    assert client.get(f"/api/projects/{pid}/arc42/figures/nope.svg").status_code == 404


def test_docx_without_the_extra_answers_501(client: TestClient, monkeypatch):
    pid = project_with_content(client)
    monkeypatch.setitem(__import__("sys").modules, "docx", None)
    response = client.get(f"/api/projects/{pid}/arc42.docx")
    assert response.status_code == 501 and "arcviz-agent[docx]" in response.json()["detail"]


def test_the_note_route_is_the_project_operation(client: TestClient):
    pid = project_with_content(client)
    ok = client.post(
        f"/api/projects/{pid}/ops", json={"op": "set_arc42_note", "args": {"section": "08", "text": "Logging"}}
    )
    assert ok.status_code == 200
    assert "Logging" in client.get(f"/project/{pid}/arc42").text
    bad = client.post(f"/api/projects/{pid}/ops", json={"op": "set_arc42_note", "args": {"section": "99", "text": "x"}})
    assert bad.status_code == 400


def test_blocks_are_plain_data():
    assert Bullets(("a",)).items == ("a",) and Table(("h",), (("c",),)).rows == (("c",),)


def test_rule_arc42_section_empty_names_what_a_note_would_add():
    p = projects.new_project("Shop", "shop")
    assert not [f for f in run_project_checks(ProjectSnapshot(p, [])) if f.rule == "arc42-section-empty"]
    op(p, "add_driver", title="Kosten", kind="constraint")
    found = [f for f in run_project_checks(ProjectSnapshot(p, [])) if f.rule == "arc42-section-empty"]
    assert found and all(
        s in found[0].message for s in ("Kontextabgrenzung", "Lösungsstrategie", "Querschnittliche", "Glossar")
    )
    for section in ("03", "04", "08", "12"):
        op(p, "set_arc42_note", section=section, text="Text")
    assert not [f for f in run_project_checks(ProjectSnapshot(p, [])) if f.rule == "arc42-section-empty"]
